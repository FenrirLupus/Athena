"""Lifecycle — sleep, wake, wake-on-delivery and the idle sweep.

Extracted from `ChannelManager` (optimization plan B2b). The manager keeps *admission* (the queue) and
*routing* (`server/routing.py`); this owns the state machine's transitions and the checks that make them honest.

Two rules drive everything here:

- **A state that cannot be seen is not a state.** `waking` exists so the checks have a visible duration, and
  every transition is written to disk, so an operator reads state instead of guessing.
- **Sleep is refused while work is in flight.** A channel that parks mid-answer loses the answer, and
  "asleep" becomes a lie about work that is still running.

The wake checks are three small functions returning `{"ok": bool, "detail": str}` rather than one 80-line body:
the same checks in the same order, but a failure now names itself.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Protocol

from core import agentconfig
from core import channelstate
from core import trace
from core.context_window import effective_window


class ChannelAsleep(ValueError):
    """Raised when a sleeping channel could not be woken for a delivery.

    Sending to a sleeping channel is *not* an error any more — the message wakes it. This is raised only when
    the wake verification fails, so the caller learns which check failed (a missing model, no key) instead of
    the work silently vanishing or the channel pretending to be awake.
    """


class LifecycleRegistry(Protocol):
    """What lifecycle needs from the thing that owns the channels."""

    cfg: dict

    def channel(self, name: str) -> object: ...

    def channels_snapshot(self) -> list: ...

    def set_state(self, channel, state: str, **extra) -> None: ...

    def provider_for(self, name: str, root: Path | None = None): ...


class ChannelLifecycle:
    def __init__(self, registry: LifecycleRegistry):
        self.registry = registry

    # -- sleep -------------------------------------------------------------
    def sleep(self, name: str) -> dict:
        """Park a channel: it keeps its state on disk but takes no work until woken.

        Refused while a turn is running or queued — a channel that sleeps mid-answer loses the answer, and
        "asleep" would then be a lie about work that is still in flight.
        """
        registry = self.registry
        channel = registry.channel(name)
        if channel.state in (channelstate.WORKING, channelstate.QUEUED):
            raise ValueError(f"{name} is {channel.state}; wait for it to finish before sleeping it")
        if channel.state == channelstate.SLEEPING:
            return {"channel": name, "state": channelstate.SLEEPING, "already": True}
        registry.set_state(channel, channelstate.SLEEPING)
        return {"channel": name, "state": channel.state}

    # -- wake --------------------------------------------------------------
    def wake(self, name: str) -> dict:
        """Bring a channel back and *verify* it, rather than assuming the flag is the truth.

        Waking is the only place that checks the three things a channel needs to answer: access to its own
        files, a provider that can be built (with a key, if the provider is remote), and a model that
        actually resolves. A wake that flipped a flag would let a broken channel look ready — which is
        exactly the state a purple dot is supposed to rule out.
        """
        registry = self.registry
        channel = registry.channel(name)
        channel.last_seen = time.time()
        if channel.state == channelstate.SLEEPING:
            registry.set_state(channel, channelstate.WAKING)   # visible while the checks run
        checks = {
            "channel": self._check_channel(channel),
            "provider": self._check_provider(channel),
            "model": self._check_model(channel),
        }
        ok = all(item["ok"] for item in checks.values())
        if channel.state == channelstate.WAKING:
            registry.set_state(channel, channelstate.IDLE if ok else channelstate.ERROR)
        return {"channel": name, "awake": ok, "state": channel.state, "checks": checks}

    def _check_channel(self, channel) -> dict:
        """1 · the profile exists and its session file is writable."""
        try:
            root = channel.root
            problems = [f"missing {required}"
                        for required in ("assistant/ASSISTANT.md", "memories/MEMORY.md")
                        if not (root / required).is_file()]
            if not root.is_dir():
                problems.insert(0, "profile directory missing")
            messages = channel.session.history()
            probe = root / "sessions" / ".wake-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return {"ok": not problems, "detail": "; ".join(problems) or f"{len(messages)} messages readable"}
        except OSError as exc:
            return {"ok": False, "detail": f"not writable: {exc}"}

    def _check_provider(self, channel) -> dict:
        """2 · the provider builds, and a remote one has a key."""
        try:
            provider = self.registry.provider_for(channel.name, channel.root)
            described = provider.describe() if hasattr(provider, "describe") else {}
            kind = str(described.get("provider") or "")
            key_missing = False
            if kind in ("openai", "anthropic"):
                from providers import base as providers_base
                key_missing = not providers_base.api_key(
                    kind, str(agentconfig.read(channel.root).get("api_key") or ""))
            return {"ok": not key_missing,
                    "detail": f"{kind or 'unknown'} · " + ("no API key" if key_missing else "ready")}
        except Exception as exc:                      # noqa: BLE001 — a wake reports, never raises
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

    def _check_model(self, channel) -> dict:
        """3 · the window holds the stack, and the model resolves (locally, from disk)."""
        try:
            model_cfg = agentconfig.read(channel.root).get("model") or {}
            local_name = agentconfig.local_model_name(model_cfg)
            window = effective_window(model_cfg or self.registry.cfg)
            problems = []
            if window < 32768:
                problems.append(f"window {window} below the 32768 floor")
            if local_name:
                from models import store as model_store
                try:
                    if not model_store.resolve(local_name).exists():
                        problems.append(f"local model {local_name!r} missing from disk")
                except RuntimeError as exc:
                    problems.append(str(exc))
                # the runtime starts on the first call, not here — waking must not load 8 GB of weights
            elif not str(model_cfg.get("name") or "").strip():
                problems.append("no model named for this channel")
            return {"ok": not problems,
                    "detail": "; ".join(problems) or f"{model_cfg.get('name') or local_name} · window {window}"}
        except Exception as exc:                      # noqa: BLE001
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

    def ensure_awake(self, channel) -> None:
        """Delivery wakes a sleeping channel — a message is a wake signal, not a rejection.

        The wake runs its full verification first: waking to accept work from an agent whose model is missing
        would queue a task that cannot be answered, and the caller would wait for a reply that can never come.
        """
        if channel.state != channelstate.SLEEPING:
            return
        result = self.wake(channel.name)
        if not result.get("awake"):
            failed = {k: v["detail"] for k, v in (result.get("checks") or {}).items() if not v["ok"]}
            detail = "; ".join(f"{k}: {v}" for k, v in failed.items()) or "unknown"
            raise ChannelAsleep(f"{channel.name} could not be woken to take this task — {detail}")
        trace.event("lifecycle.wake", channel=channel.name, why="incoming message")

    # -- the idle sweep ----------------------------------------------------
    def sweep_idle(self, now: float | None = None) -> list[str]:
        """Park channels that have sat idle past the configured window.

        Three exclusions, each a bug if dropped: a channel that is working or queued is not idle; one with
        `awaiting` is waiting on a peer and going to sleep would make the answer undeliverable; and one that
        is already asleep has nothing to do. Returns what it parked, so the log can say why a dot turned
        purple without anyone guessing.
        """
        minutes = float((self.registry.cfg.get("agents") or {}).get("idle_sleep_minutes") or 0)
        if minutes <= 0:
            return []
        limit = minutes * 60
        now = time.time() if now is None else now
        slept = []
        for channel in self.registry.channels_snapshot():
            idle_for = self._idle_for(channel, now)
            if idle_for is None or idle_for < limit:
                continue
            try:
                self.sleep(channel.name)
            except ValueError:
                continue
            slept.append(channel.name)
            trace.event("lifecycle.sleep", channel=channel.name, idle_s=_span(idle_for),
                        reason="idle sweep")
        return slept

    @staticmethod
    def _idle_for(channel, now: float) -> float | None:
        """Seconds idle, or None when the channel is not a candidate for parking."""
        if channel.state != channelstate.IDLE:
            return None
        with channel._lock:
            if channel.queue.qsize() or channel.awaiting:
                return None
            return now - channel.last_seen

    def start_sweeper(self) -> None:
        """One daemon thread; a sweep must never kill the loop it runs in."""
        import threading
        interval = float((self.registry.cfg.get("agents") or {}).get("sweep_seconds") or 30)
        threading.Thread(target=self._sweep_forever, args=(interval,), name="idle-sleep",
                         daemon=True).start()

    def _sweep_forever(self, interval: float) -> None:
        while True:
            time.sleep(interval)
            try:
                self.sweep_idle()
            except Exception:                         # noqa: BLE001 — a sweep must never kill the loop
                pass


def _span(seconds: float) -> str:
    """A duration a reader can trust: minutes only once they are meaningful."""
    return f"{seconds / 60:.0f} min" if seconds >= 120 else f"{seconds:.0f}s"
