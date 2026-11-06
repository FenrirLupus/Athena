"""Channels — one resident agent each: its own session, queue and worker.

Turns serialise per channel (documentation/architecture/11-multiplexing.md): two inputs never run
concurrently inside one session, and one channel never blocks another.
"""
from __future__ import annotations

import queue
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from context import stack as stack_mod
from core import agentconfig
from core import context_window as _window_mod
from core import trace


def _effective_window(model_cfg: dict) -> int:
    """One window figure for every reader: the provider's if it is at least the floor, else the floor."""
    try:
        return _window_mod.resolve(model_cfg or {})["window"]
    except ValueError:
        return _window_mod.FLOOR
from core.permissions import PermissionManager
from core.security import Ledger
from core import identity
from core import plugininstall
from core import channelstate
from core import metrics as metrics_mod
from core import profile as profile_mod
from server.instructions import DEFAULT_INSTRUCTIONS, HIVE_INSTRUCTIONS   # re-exported
from server.admission import Admission
from server.lifecycle import ChannelAsleep, ChannelLifecycle   # re-exported: tests import it from here
from server.routing import MentionRouter

__all__ = ["Channel", "ChannelManager", "ChannelAsleep", "NO_TOOLS",
           "DEFAULT_INSTRUCTIONS", "HIVE_INSTRUCTIONS"]
from core.budget import IterationBudget
from core.loop import run_turn
from core.session import Session

ROW_DEFAULTS = {
    "id": "",
    "identity": "",
    "state": "available",
    "available": True,
    "resident": False,
    "busy": False,
    "queue_depth": 0,
    "awaiting": [],
    "inbound_from": "",
    "turns": 0,
    "session_messages": 0,
    "persona": "",
    "provider": "",
    "model": "",
    "window": 0,
    "last_author": "",
    "configured": False,
}


class _NoTools:
    """The registry an agent gets when tools are switched off: no schemas, and a refusal if called."""

    def schemas(self) -> list:
        return []

    def invoke(self, name: str, arguments: dict, timeout: float = 60.0) -> str:
        return "error: tools are disabled for this agent"

    def names(self) -> list:
        return []


NO_TOOLS = _NoTools()


_new_queue = queue.Queue      # aliased: a class-level name would otherwise shadow the queue module


@dataclass
class Channel:
    name: str
    root: Path
    session: Session
    # the factory is aliased because a class-level name shadows the module inside the class body
    queue: queue.Queue = field(default_factory=_new_queue)        # [Message] turns
    call_queue: queue.Queue = field(default_factory=_new_queue)   # [Voice] turns, drained first
    work_ready: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    last_result: dict | None = None
    last_author: str = ""
    last_end: float = 0.0
    state: str = channelstate.AVAILABLE
    awaiting: dict = field(default_factory=dict)   # peer -> number of unanswered asks
    inbound_from: str = ""                         # who sent the message this channel is handling
    last_seen: float = field(default_factory=time.time)   # last activity, for the idle-sleep sweep
    inbox: list = field(default_factory=list)      # notices addressed to the turn that was running
    current_turn: int = 0                          # which turn the notices belong to
    turns: int = 0
    agent_id: str = ""                             # the lane's minted id — what a metric or trace row carries
    busy: bool = False
    stopping: bool = False                         # profile is gone: the worker exits after the turn in hand
    _done: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)


class ChannelManager:
    """Owns every channel, the provider, the tools, and the worker threads."""

    def __init__(self, cfg: dict, provider, tools_registry, agents_dir: Path):
        self.cfg = cfg
        # The config is loaded once at boot, but a *plugin install* changes `plugins.enabled` while the server runs.
        # Without noticing, the new capability is on disk and invisible until a restart — which is exactly the
        # "installed but the agent says it has no skills" report.
        self._cfg_mtime = self._config_mtime()
        self.provider = provider
        self.tools = tools_registry
        # A small local model over-calls tools: hand it a two-word turn and it reaches for the cheapest
        # one it can see. `tools.enabled: false` is the honest switch for a conversation-only agent —
        # the schemas are never sent, so the choice cannot be made at all.
        self.tools_enabled = bool((cfg.get("tools") or {}).get("enabled", True))
        # `0` means "accept nothing that has to wait", so it must not be read as "unset": `or 32` made a
        # deliberate 0 fall back to the default and the admission test passed nothing
        _queue_cfg = (cfg.get("agents") or {}).get("max_queue")
        self.max_queue = int(32 if _queue_cfg is None else _queue_cfg)
        # one provider per agent, built on first use: a profile may name its own endpoint, key and model
        self._providers: dict[str, object] = {}
        # routing, lifecycle and admission are separate collaborators; the manager keeps the registry
        self.router = MentionRouter(self)
        self.lifecycle = ChannelLifecycle(self)
        self.admission = Admission(self)
        # One permissions manager for the whole multiplexer. An out-of-scope action pauses the turn and asks; an
        # install request appears in the same queue and is answered by the same click path. `self.approvals` stays as
        # the name the tools and the scope guard already use — it is the same object.
        athena_root = agents_dir.parent
        self._security_ledger = Ledger(athena_root / "security")
        self.permissions = PermissionManager(
            athena_root,
            timeout_s=float((cfg.get("tools") or {}).get("approval_timeout_s") or 300),
            ledger=self._security_ledger,
            profiles=agents_dir)
        self.permissions.register("path", blocking=True)
        self.permissions.attach("install",
                                lister=lambda: plugininstall.pending_installs(athena_root, athena_root / "plugins"),
                                resolver=lambda rid, decision: plugininstall.resolve_install(
                                    rid, decision, athena_root, athena_root / "plugins"))
        self.permissions.on_permanent = self._remember_workspace
        self.permissions.on_revoke = self._forget_workspace
        self.approvals = self.permissions
        if float((cfg.get("agents") or {}).get("idle_sleep_minutes") or 0) > 0:
            self.lifecycle.start_sweeper()
        self.agents_dir = agents_dir
        self.channels: dict[str, Channel] = {}
        self._lock = threading.Lock()

    # -- providers ---------------------------------------------------------
    def provider_for(self, name: str, root: Path | None = None):
        """The provider this agent should use: its own if it has an config.yaml model block, else global."""
        cached = self._providers.get(name)
        if cached is not None:
            return cached

        root = root or (self.agents_dir / name)
        agent_cfg = agentconfig.read(root)
        model_cfg = agent_cfg.get("model") or {}
        configured = bool(model_cfg.get("name") or model_cfg.get("base_url")
                          or agentconfig.local_model_name(model_cfg))
        if not configured:
            trace.event("provider.fallback", level="debug", channel=name, reason="no model block of its own",
                        inherited=str(getattr(self.provider, "name", "") or "global"))
            return self.provider                 # inherits the global provider; nothing cached

        from providers import base as provider_base
        cfg = agentconfig.as_cfg(agent_cfg, self.cfg)
        try:
            # the key name may sit at the top level (`api_key:`) or inside the model block (`key_name:`);
            # both mean "use this line from .secret", and the model block wins when both are present
            model_block = agent_cfg.get("model") or {}
            key_name = str(model_block.get("key_name") or agent_cfg.get("api_key") or "")
            provider = provider_base.build(cfg, key_name=key_name, root=root)
        except Exception as exc:                 # a broken config.yaml must not take the channel down
            # silent substitution is the bug: the channel answers from the global provider while its own file says
            # otherwise, so the ✗ names both the lane and the adapter it actually uses
            trace.failure("provider.fallback", exc, channel=name, reason="the model block would not build",
                          inherited=str(getattr(self.provider, "name", "") or "global"))
            return self.provider
        self._providers[name] = provider
        return provider

    def _remember_workspace(self, agent: str, path: str) -> None:
        """A `permanent` approval is written to that profile's workspace store, so it survives a restart."""
        from core import paths as paths_mod
        root = self.agents_dir / agent
        if not root.is_dir():
            return
        scope = paths_mod.for_profile(root)
        try:
            scope.grant(path)
        except ValueError as exc:
            raise exc                  # surfaced by the manager as mirror-failed: the path may simply not exist
        paths_mod.save_workspaces(root, [str(w) for w in scope.granted])

    def _forget_workspace(self, agent: str, path: str) -> None:
        """Take the mirrored folder back out of that profile's workspace list."""
        from core import paths as paths_mod

        root = self.agents_dir / agent
        if not root.is_dir():
            return
        scope = paths_mod.for_profile(root)
        if scope.revoke(path).get("removed"):
            paths_mod.save_workspaces(root, [str(w) for w in scope.granted])

    @staticmethod
    def _config_mtime():
        try:
            from config import loader

            return loader.USER_CONFIG.stat().st_mtime
        except Exception:                             # noqa: BLE001
            return None

    def refresh_config(self) -> bool:
        """Reload the configuration if the file changed. Returns True when it did."""
        stamp = self._config_mtime()
        if stamp is None or stamp == self._cfg_mtime:
            return False
        try:
            from config import loader

            fresh = loader.load()
        except Exception:                             # noqa: BLE001 — a broken edit must not take the server down
            return False
        self.cfg = fresh
        self._cfg_mtime = stamp
        return True

    def _tools_for(self, channel):
        """The toolset for one turn: the shared built-ins, rebound to this agent's scope and approval broker."""
        from tools.filesystem import Context

        if not hasattr(self.tools, "bind"):
            return self.tools
        ctx = Context(scope=self.scope_for(channel.name), approvals=self.approvals,
                      agent_id=str(getattr(channel, "agent_id", "") or ""))
        ctx.cfg = self.cfg
        ctx.profile_root = channel.root
        ctx.athena_root = self.agents_dir.parent
        # a worker runs on the same provider as the agent that spawned it
        ctx.provider = self.provider_for(channel.name, channel.root)
        ctx.is_subagent = False
        return self.tools.bind(ctx)

    def scope_for(self, name: str):
        """This agent's scope, with its grants — what every file tool must consult."""
        from core import paths as paths_mod
        return paths_mod.for_profile(self.agents_dir / name)

    def forget_provider(self, name: str) -> None:
        """Drop a cached provider so the next turn rebuilds it (used when a profile is re-saved)."""
        self._providers.pop(name, None)

    # -- state machine -----------------------------------------------------
    def set_state(self, channel: Channel, state: str, **extra) -> None:
        """Move a channel to `state`, refusing an impossible transition instead of recording nonsense."""
        before = channel.state
        if before == state:
            return
        if not channelstate.can_move(before, state):
            trace.event("state.refused", level="error", channel=channel.name, was=before, to=state)
            return
        channel.state = state
        channelstate.write(channel.root, state, **extra)
        trace.event("state.change", level="debug", channel=channel.name, was=before, to=state, **extra)

    # -- admission (server/admission.py) -----------------------------------
    def _notice_if_busy(self, channel: Channel, text: str, author: str = "") -> None:
        """Tell a running turn that new input arrived — see Admission.notice_if_busy."""
        self.admission.notice_if_busy(channel, text, author)

    def _drain(self, channel: Channel) -> list[str]:
        """Notices addressed to the running turn — see Admission.drain."""
        return self.admission.drain(channel)

    # -- lifecycle ---------------------------------------------------------
    def channels_snapshot(self) -> list:
        """The resident channels, copied — a sweep must not hold the registry lock while it works."""
        with self._lock:
            return list(self.channels.values())

    def channel(self, ref: str, *, ensure: bool = False) -> Channel:
        """The resident channel for a lane — by label, alias or `id:<uuid>`. Creates nothing.

        `ensure=True` is a **test seam**: it creates a free label on first touch, which is what the lifecycle,
        admission and routing suites need to build fixtures. No production path passes it — the wizard, the CLI and
        the routes all go through `create()`, so a typo or a stale mention can still only fail, not fabricate a lane.

Resolution only: a miss raises instead of scaffolding, so a typo, a stale mention or a poll against a renamed
lane cannot fabricate a profile. Creation is `create()`."""
        with self._lock:
            try:
                record = identity.resolve(self.agents_dir, ref)
            except identity.UnknownAgent:
                if not ensure or not identity.label_ok(str(ref or "").strip()):
                    raise
                # the seam: only for tests, inline because `create()` takes this same non-reentrant lock
                root = self.agents_dir / str(ref).strip()
                root.mkdir(parents=True, exist_ok=True)
                profile_mod.ensure(root)
                identity.mint(root, minted_by="create")
                record = identity.resolve(self.agents_dir, ref)
            name = record["label"]                            # one lane, one key — whatever the caller spelled
            channel = self.channels.get(name)
            if channel:
                return channel
            root = record["root"]
            adopted, recovered_from = channelstate.recover(root)
            # `available` means "on disk, not in memory" — the moment the channel exists, that stops being
            # true, so adoption promotes it to idle instead of leaving a contradiction in the row
            if adopted == channelstate.AVAILABLE:
                adopted = channelstate.IDLE
                channelstate.write(root, adopted)
            channel = Channel(name=name, root=root, session=Session(name, root), state=adopted,
                              agent_id=str(record.get("id") or ""))
            if recovered_from:
                trace.event("state.recover", channel=name, was=recovered_from, now=adopted,
                            note="the process that wrote it is gone")
            channel.thread = threading.Thread(target=self._worker, args=(channel,),
                                           name=f"channel-{name}", daemon=True)
            channel.thread.start()
            self.channels[name] = channel
            trace.event("worker.start", level="debug", channel=name, worker=f"channel-{name}",
                        state=adopted, agent_id=str(record.get("id") or ""))
            return channel

    def _worker(self, channel: Channel) -> None:
        """Drain this channel's queue forever — see Admission.worker."""
        self.admission.worker(channel)

    def do_turn(self, channel: Channel, job: dict) -> None:
        """Run one turn — the name Admission's protocol asks for."""
        self._do_turn(channel, job)

    # -- routing (server/routing.py) ---------------------------------------
    def roster(self, exclude: str) -> str:
        """Live roster of the other agents — see MentionRouter.roster."""
        return self.router.roster(exclude)

    def instructions_for(self, channel: Channel) -> str:
        """Shared instructions plus the hive roster — see MentionRouter.instructions_for."""
        return self.router.instructions_for(channel)

    def _do_turn(self, channel: Channel, job: dict) -> None:
        # one job is one lane: everything the turn, its routing and its metrics call inherits it, so a helper
        # that cannot be given a channel (a durable write, a config read) still lands in this channel's trail
        with trace.lane_scope(channel.name):
            return self._do_turn_inner(channel, job)

    def _do_turn_inner(self, channel: Channel, job: dict) -> None:
        self.refresh_config()                         # a plugin enabled since boot becomes visible here
        # a message from another agent is not the operator speaking — the prompt says who it is from
        author = job.get("author") or ""
        mode = job.get("mode") or "chat"
        queued_behind = channel.queue.qsize() + channel.call_queue.qsize()
        # admission: a turn starts only from idle/queued — anything else means something else is
        # already working this channel, and starting a second would corrupt its transcript
        if channel.state not in (channelstate.IDLE, channelstate.QUEUED):
            trace.event("admission.refused", level="error", channel=channel.name, state=channel.state,
                        author=author, mode=mode)
            return
        channel.busy = True
        # inbound: the message being handled came from someone else — the right-hand indicator names them
        channel.inbound_from = (job.get("author") or "") if job.get("author") != channel.name else ""
        channel.current_turn = channel.turns + 1
        with channel._lock:
            channel.inbox = [item for item in channel.inbox if item.get("turn") == channel.current_turn]
        trace.event("job.received", channel=channel.name, turn=channel.current_turn,
                    author=author or "operator", mode=mode, queue_depth=queued_behind,
                    wait_ms=round(max(0.0, time.time() - float(job.get("queued_at") or time.time())) * 1000))
        self.set_state(channel, channelstate.WORKING, turn=channel.current_turn)
        turn_started = time.time()
        # this agent's config.yaml first, the global one filling the blanks — the same rule the provider and the
        # keys follow. Without it, budget/tools overrides in an agent's file were documented and ignored.
        turn_cfg = agentconfig.as_cfg(agentconfig.read(channel.root), self.cfg)
        turn_budget = turn_cfg.get("budget") or {}
        try:
            budget = IterationBudget(
                max_turns=int(turn_budget.get("max_iterations") or 100),
                max_usd=float(turn_budget.get("max_turn_usd") or 0.50),
                grace_calls=int(turn_budget.get("grace_calls") or 1),
            )
            # both modes write into the same transcript; the marker is what tells them apart afterwards
            marker = "[Voice]" if mode == "call" else "[Message]"
            spoken = f"{marker} {job['text']}"
            user_text = f"[from @{author}] {spoken}" if author else spoken
            result = run_turn(
                channel=channel.name,
                channel_root=channel.root,
                session=channel.session,
                provider=self.provider_for(channel.name, channel.root),
                tools_registry=(self._tools_for(channel) if (turn_cfg.get("tools") or {}).get("enabled", self.tools_enabled)
                                else NO_TOOLS),
                # a callable, not a string: the roster must describe the hive at each model call
                instructions=lambda: self.instructions_for(channel),
                user_text=user_text,
                cfg=turn_cfg,
                budget=budget,
                mode=mode,
                interrupts=lambda: self._drain(channel),
            )
            with channel._lock:
                channel.last_result = dict(result)
                channel.turns += 1
                channel.last_end = time.time()
                channel.last_author = author
            self._route(channel, result, job)
        except Exception as exc:                       # noqa: BLE001 — a channel must not die
            trace.failure("channel.error", exc, channel=channel.name, turn=channel.current_turn)
            with channel._lock:
                channel.last_result = {"channel": channel.name, "status": "error_channel",
                                    "reply": f"channel error: {exc}",
                                    "trace": traceback.format_exc(limit=3)}
        finally:
            # measured here, not in the try: a turn that failed still spent tokens and seconds, and a
            # failed turn is exactly the one whose cost you want to see
            self._record_metrics(channel, job, turn_started)
            channel.busy = False
            channel.last_seen = time.time()
            channel.inbound_from = ""
            # the outbound half of the turn: what it answered, how long it took, and whether it failed
            last = channel.last_result or {}
            status = str(last.get("status") or "")
            trace.event("turn.closed", channel=channel.name, turn=channel.current_turn,
                        status=status or "unknown", seconds=round(time.time() - turn_started, 3),
                        reply_chars=len(str(last.get("reply") or "")),
                        calls=len(last.get("model_calls") or []))
            if status == "error_api":
                # loop.py caught the provider's exception and turned it into a status, so there is no
                # exception object here — the frame is the reply it built, still read as a trail entry
                trace.failure("channel.error_api", None, channel=channel.name, turn=channel.current_turn,
                              error=str(last.get("reply") or "")[:300])
            # more work waiting means queued, otherwise the channel is idle or in error
            pending = channel.queue.qsize()
            failed = str((channel.last_result or {}).get("status") or "").startswith("error")
            if pending:
                self.set_state(channel, channelstate.QUEUED, pending=pending)
            else:
                self.set_state(channel, channelstate.ERROR if failed else channelstate.IDLE)
            # wake only the caller that queued THIS job: a channel-wide event woke whoever happened to be
            # waiting, so two concurrent submits returned each other's turn (and the second caller saw
            # `working` while its own turn had not started yet)
            done = job.get("done")
            if done is not None:
                done.set()

    def _record_metrics(self, channel: Channel, job: dict, started: float) -> None:
        """Write one row per turn. Never raises: measuring must not break the thing being measured."""
        try:
            cfg_metrics = self.cfg.get("metrics") or {}
            if not cfg_metrics.get("enabled", True):
                return
            result = dict(channel.last_result or {})
            usage = result.get("usage") or {}
            prompt_tokens = int(usage.get("prompt_tokens") or 0)
            completion_tokens = int(usage.get("completion_tokens") or 0)
            total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
            seconds = round(time.time() - started, 3)
            # the model calls, not the wall clock: overhead must not be charged to the model's speed
            inference_seconds = round(sum(float(c.get("seconds") or 0)
                                          for c in (result.get("model_calls") or [])), 3)
            rate = float(cfg_metrics.get("usd_per_mtok") or 1.0)
            provider = self.provider_for(channel.name, channel.root)
            described = provider.describe() if hasattr(provider, "describe") else {}
            is_local = str(described.get("provider") or "") == "local"
            prompt_report = result.get("prompt") or {}
            metrics_mod.record(self.agents_dir, {
                "channel": channel.name,
                "agent_id": getattr(channel, "agent_id", "") or "",
                "turn": channel.turns,
                "provider": described.get("provider"),
                "model": described.get("model"),
                "status": result.get("status"),
                "seconds": seconds,
                # admission → first model call is the part the *operator* waits for, separate from inference
                "queue_wait_s": round(max(0.0, started - float(job.get("queued_at") or started)), 3),
                "inference_seconds": inference_seconds,
                "tokens_per_second": (round(completion_tokens / inference_seconds, 2)
                                      if inference_seconds and completion_tokens else None),
                "seconds_per_token": (round(inference_seconds / completion_tokens, 3)
                                      if completion_tokens and inference_seconds else None),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "model_calls": len(result.get("model_calls") or []),
                # why each call stopped: "length" means the output cap truncated an answer, which is the only
                # evidence that the cap is too low. Without it, C1 can only be argued, not measured.
                "finish_reasons": [c.get("finish_reason") or "" for c in (result.get("model_calls") or [])],
                "tool_rounds": int(result.get("tool_rounds") or 0),
                "tool_names": list(result.get("tool_names") or []),
                "cost_usd": round(total_tokens / 1_000_000 * rate, 6),
                "usd_per_mtok": rate,
                "local_resources": (metrics_mod.sample_resources(described.get("model") or "")
                                    if is_local and cfg_metrics.get("sample_resources", True) else None),
                "context": {"window": prompt_report.get("window"),
                            "prompt_tokens_est": prompt_report.get("prompt_tokens_est"),
                            "history_turns": prompt_report.get("history_turns"),
                            "dropped_turns": prompt_report.get("dropped_turns"),
                            # the calibration this turn was assembled with, and the raw estimate it came from:
                            # together they show whether the estimator is converging (00-baseline.md §3)
                            "calibration": prompt_report.get("calibration"),
                            "est_raw": prompt_report.get("est_raw"),
                            # per-block prompt cost: which part of the system block to attack, in tokens
                            "blocks": prompt_report.get("blocks"),
                            "blocks_share_pct": prompt_report.get("blocks_share_pct")},
                "error": None if not str(result.get("status") or "").startswith("error")
                         else (result.get("reply") or "")[:300],
            })
        except Exception as exc:                       # noqa: BLE001 — measurement must never break a turn
            trace.failure("metrics.record", exc, channel=channel.name, turn=channel.current_turn)

    # -- agent to agent ----------------------------------------------------
    def profiles(self) -> list[str]:
        """Every profile on disk — the set a mention can address."""
        try:
            return sorted(entry.name for entry in self.agents_dir.iterdir()
                          if entry.is_dir() and not entry.name.startswith("."))
        except OSError:
            return []

    def _route(self, channel: Channel, result: dict, job: dict) -> None:
        """Send answers home and dispatch mentions — see MentionRouter.route."""
        self.router.route(channel, result, job)

    def enqueue(self, name: str, text: str, *, author: str = "", hops: int = 0,
                reply_to: str | None = None) -> dict:
        """Queue a turn without waiting — see Admission.enqueue."""
        try:
            result = self.admission.enqueue(name, text, author=author, hops=hops, reply_to=reply_to)
        except Exception as exc:
            # a refusal (full queue, asleep lane) is a trail entry too: the message never became a turn
            trace.failure("job.rejected", exc, channel=name, author=author or "operator",
                          mode="chat", hops=hops)
            raise
        trace.event("job.queued", level="debug", channel=name, author=author or "operator",
                    mode="chat", hops=hops, queue_depth=result.get("queue_depth"), chars=len(text))
        return result

    def submit(self, name: str, text: str, wait_s: float = 120.0, mode: str = "chat") -> dict:
        """Queue a turn and wait for this job — see Admission.submit."""
        try:
            result = self.admission.submit(name, text, wait_s=wait_s, mode=mode)
        except Exception as exc:
            trace.failure("job.rejected", exc, channel=name, author="operator", mode=mode)
            raise
        trace.event("job.submitted", level="debug", channel=name, author="operator", mode=mode,
                    status=result.get("status"), wait_s=wait_s, chars=len(text))
        return result

    def ensure_awake(self, channel: Channel) -> None:
        """Delivery wakes a sleeping channel — see ChannelLifecycle.ensure_awake."""
        self.lifecycle.ensure_awake(channel)

    # -- lifecycle (server/lifecycle.py) -----------------------------------
    def sleep(self, name: str) -> dict:
        """Park a channel — see ChannelLifecycle.sleep."""
        return self.lifecycle.sleep(name)

    def wake(self, name: str) -> dict:
        """Bring a channel back and verify it — see ChannelLifecycle.wake."""
        return self.lifecycle.wake(name)

    def sweep_idle(self, now: float | None = None) -> list[str]:
        """Park channels idle past the window — see ChannelLifecycle.sweep_idle."""
        return self.lifecycle.sweep_idle(now)

    # -- profile lifecycle -------------------------------------------------
    def create(self, name: str) -> dict:
        """Register a profile without starting it: directory, identity, colour, state.

        Under `_lock`, because this is check-then-act: two concurrent creates for one free label both passed the check
        and both minted, and the loser raised out of a route that had already answered.
        """
        with self._lock:
            problem = identity.new_label_problem(self.agents_dir, name)
            if problem:
                raise identity.Conflict(problem)
            root = self.agents_dir / name
            profile_mod.ensure(root)
            record = identity.mint(root, minted_by="create")
        # every profile is born with a colour, so a new agent is identifiable before it is configured
        if not agentconfig.read(root).get("accent"):
            taken = [agentconfig.read(self.agents_dir / other).get("accent", "")
                     for other in self.profiles() if other != name]
            config = agentconfig.read(root)
            config["accent"] = agentconfig.random_accent(avoid=[c for c in taken if c])
            agentconfig.write(root, config)
        accent = agentconfig.accent_for(name, agentconfig.read(root))
        channelstate.write(root, channelstate.AVAILABLE)
        # outside the accent branch: a lane that already had a colour fell through to `return None`
        # `created` is always True because a second create for the same label is refused (409) before reaching here —
        # the field survives so the response shape does not change, not because it can be false
        return {"channel": name, "created": True, "available": True,
                "state": channelstate.AVAILABLE, "accent": accent, "id": record["id"]}

    def remove(self, ref: str) -> dict:
        """Delete a lane and stop listing it — by label, alias or `id:<uuid>`.

Resolution comes first: the ref is resolved to a label before any path is built, so a label, an alias and
`id:<uuid>` all address the same lane."""
        import shutil

        try:
            record = identity.resolve(self.agents_dir, ref)
            name, root = record["label"], record["root"]
        except identity.IdentityError:
            # A directory with no usable identity resolves to nothing, so it could not be deleted at all — and an
            # orphan is exactly the thing an operator needs to clear. Only a plain label qualifies: an `id:` ref
            # names no directory, and a valid identity would have resolved above.
            text = str(ref or "").strip()
            candidate = self.agents_dir / text
            if (text.startswith(identity.ID_PREFIX) or not identity.label_ok(text)
                    or not candidate.is_dir()):
                raise
            try:
                usable = identity.read(candidate) is not None
            except identity.IdentityError:
                usable = False                      # corrupt counts as an orphan, not as a lane
            if usable:
                raise
            name, root = text, candidate
        if name == "default":
            raise PermissionError("the default agent is the queen of the hive and cannot be removed")
        if Path(root).is_symlink():
            # `rmtree` refuses a symlink, and the DELETE route would surface that as a 500. Say what it is instead:
            # deleting the target of a link is not what "remove this lane" means.
            raise identity.Conflict(f"{root} is a symbolic link — refusing to delete through it; remove it by hand")
        with self._lock:
            channel = self.channels.pop(name, None)   # a resident channel must stop answering
        if channel is not None:
            self.set_state(channel, channelstate.REMOVED)
        # Before the rmtree: `forget_session` resolves the lane through its identity file, and after the delete there
        # is nothing to resolve — the dead lane's session grants would stay in memory forever.
        try:
            self.permissions.forget_session(name)
        except Exception:                          # noqa: BLE001 — a failed cleanup must not block the delete
            pass
        self._providers.pop(name, None)
        # peers that were waiting on this lane would otherwise show "waiting on scout" for the life of the process,
        # and `sweep_idle` refuses to park a lane with a non-empty awaiting
        for other in list(self.channels.values()):
            try:
                other.awaiting.pop(name, None)
            except Exception:                  # noqa: BLE001
                pass
        shutil.rmtree(root)
        return {"channel": name, "removed": True}

    def repair_layout(self) -> list[str]:
        """Give every profile the full layout. A profile missing `sandbox/` or `workspace/` cannot be worked in.

        Idempotent and quiet: it creates only what is absent, so it runs at startup and after a profile is
        registered without ever touching an existing file.
        """
        repaired = []
        try:
            entries = sorted(self.agents_dir.iterdir())
        except OSError:
            return repaired
        for entry in entries:
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            try:
                result = profile_mod.ensure(entry)
            except OSError:
                continue
            if result.get("created"):
                repaired.append(entry.name)
        return repaired

    # -- inspection --------------------------------------------------------
    def discover(self) -> list[dict]:
        """Every agent profile on disk, resident or not.

        A channel is instantiated when it is first used, but the *profile* is the agent: it exists from the
        moment it is created, with its identity, memories and transcript. Listing only resident channels
        hides every agent that has not been spoken to since the server started — so this reads the
        profiles directory and merges the live state over it.
        """
        found: dict[str, dict] = {}
        try:
            for entry in sorted(self.agents_dir.iterdir()):
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                messages, turns, last = 0, 0, ""
                latest = entry / "sessions" / "LATEST.jsonl"
                try:
                    with open(latest, encoding="utf-8") as fh:
                        for line in fh:
                            line = line.strip()
                            if not line:
                                continue
                            messages += 1
                            try:
                                row = __import__("json").loads(line)
                            except __import__("json").JSONDecodeError:
                                continue
                            if row.get("role") == "user":
                                turns += 1
                            last = row.get("ts") or last
                except OSError:
                    pass
                agent_cfg = agentconfig.read(entry)
                model_cfg = agent_cfg.get("model") or {}
                persona = agent_cfg.get("persona") or {}
                recorded = str(channelstate.read(entry).get("state") or channelstate.AVAILABLE)
                # a state file only describes a live process while that process runs: `working` on disk
                # with nothing resident means the writer died mid-turn, and saying "working" would be a
                # screenshot of a lie. It is reported as stale until a channel adopts and recovers it.
                display = "stale" if recorded in (channelstate.WORKING, channelstate.QUEUED) else recorded
                # A lane without a usable identity is listed, not hidden and not fatal: the listing is how an
                # operator finds the one that needs `identity --adopt`.
                try:
                    record_at = identity.read(entry)
                    identity_state = "ok" if record_at else "missing"
                except identity.IdentityInvalid:
                    record_at, identity_state = None, "invalid"
                found[entry.name] = {
                    "channel": entry.name, "resident": False, "available": True, "turns": turns,
                    "id": str((record_at or {}).get("id") or ""), "identity": identity_state,
                    "state": display, "last_state": recorded,
                    "accent": agentconfig.accent_for(entry.name, agent_cfg),
                    "persona": persona.get("name") or "",
                    "provider": model_cfg.get("provider") or "",
                    "model": model_cfg.get("name") or agentconfig.local_model_name(model_cfg) or "",
                    # each agent may declare its own window; the global figure is only the fallback
                    "window": _effective_window(model_cfg or (self.cfg.get("model") or {})),
                    "configured": bool(model_cfg.get("name") or model_cfg.get("base_url")
                                       or agentconfig.local_model_name(model_cfg)),
                                     "last_author": "",
                                     "busy": False, "queue_depth": 0,
                                     "session_messages": messages, "last_status": None,
                                     "last_calls": None, "last_tool_rounds": None,
                                     "last_seconds": None, "last_ts": last}
        except OSError:
            pass

                # A channel can be resident after its profile is deleted; the row is dropped rather than listed as a
        
        # dropped rather than listed as a ghost. Its worker thread is a daemon blocked on an empty queue
        # and exits with the process.
        with self._lock:
            for name in [n for n in self.channels if n not in found]:
                gone = self.channels.pop(name, None)
                if gone is not None:
                    self.admission.stop(gone)        # else the worker stays blocked on an empty queue for ever
            live_rows = list(self.channels.values())

        for channel in live_rows:
            row = self._row(channel)
            merged = found.get(row["channel"], {})
            merged.update(row)
            merged["resident"] = True
            # a channel can be resident with no directory (its profile was deleted while the server ran):
            # the file-derived fields are absent then, and a consumer that reads them would KeyError
            merged.setdefault("available", True)
            # a payload whose shape depends on whether a channel is warm is a client bug waiting to
            # happen: every row carries every field, resident or not
            merged.setdefault("state", channelstate.AVAILABLE)
            merged.setdefault("persona", "")
            merged.setdefault("provider", "")
            merged.setdefault("model", "")
            merged.setdefault("configured", False)
            merged.setdefault("id", getattr(channel, "agent_id", "") or "")
            merged.setdefault("identity", "ok")
            found[row["channel"]] = merged
        # One normalisation pass: a payload whose shape depends on whether a channel happens to be warm
        # is a client bug waiting to happen. Every row carries every field.
        for row in found.values():
            for key, default in ROW_DEFAULTS.items():
                if key not in row or (default is not None and row[key] is None):
                    row[key] = list(default) if isinstance(default, list) else default
        return [found[name] for name in sorted(found)]

    def _row(self, channel: Channel) -> dict:
        """One channel's live row — the shape both `snapshot()` and `discover()` return."""
        with channel._lock:
            last = channel.last_result or {}
            # a turn survives a restart because the session does; the counter in memory does not,
            # so the number the UI shows comes from the transcript
            persisted_turns = sum(1 for m in channel.session.history() if m.get("role") == "user")
            return {
                "channel": channel.name,
                "state": channel.state,
                "available": channel.root.is_dir(),
                "last_author": getattr(channel, "last_author", ""),
                # one entry per unanswered ask, so two pending questions read as "waiting on Scout +1"
                "awaiting": [name for name, count in sorted(channel.awaiting.items())
                             for _ in range(max(1, int(count)))],
                "inbound_from": channel.inbound_from,
                "turns": max(channel.turns, persisted_turns),
                "busy": channel.busy,
                "queue_depth": channel.queue.qsize(),
                "session_messages": channel.session.size(),
                "last_status": last.get("status"),
                "last_calls": last.get("calls"),
                "last_tool_rounds": last.get("tool_rounds"),
                "last_seconds": last.get("seconds"),
            }

    def snapshot(self) -> list[dict]:
        with self._lock:
            channels = list(self.channels.values())
        return [self._row(channel) for channel in channels]

    def _lane_model(self, name: str) -> dict:
        """The model block this lane's turns actually use — not the installation's."""
        try:
            from core import agentconfig

            lane = agentconfig.read(self.agents_dir / name)
            return (agentconfig.as_cfg(lane, self.cfg).get("model") or {})
        except Exception:                          # noqa: BLE001 — a broken lane file falls back to the global
            return self.cfg.get("model") or {}

    def resources(self, name: str) -> dict:
        channel = self.channel(name)
        last = dict(channel.last_result or {})
        prompt = last.get("prompt") or {}
        # before the first turn there is no report — size the blocks from the config so the
        # panel is useful from the moment the channel exists
        alloc = prompt.get("allocated") or stack_mod.allocate(
            _effective_window(self._lane_model(name)), self.cfg)
        # before the first turn there is no report, and a footer that says 0 tokens while the thread has
        # 26 messages is worse than no footer: estimate from the transcript instead of reporting nothing
        est = prompt.get("prompt_tokens_est")
        if not est:
            history = channel.session.history()
            est = sum(stack_mod.est_tokens(m.get("content") or "") + 8 for m in history)

        return {
            "channel": name,
            "window": _effective_window(self._lane_model(name)),
            "prompt_tokens_est": est,
            "budget": last.get("budget"),
            "history_tokens": prompt.get("history_tokens"),
            "history_turns": prompt.get("history_turns"),
            "dropped_turns": prompt.get("dropped_turns"),
            "allocated": {k: v for k, v in alloc.items() if isinstance(v, int)},
            # the last assembled system block, so an operator can see what the agent was told
            "system_prompt": prompt.get("system_prompt") or last.get("system_prompt") or "",
            "session_messages": channel.session.size(),
            "turns": max(channel.turns, sum(1 for m in channel.session.history() if m.get("role") == "user")),
        }
