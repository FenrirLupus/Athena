"""Routing — who a message goes to, and what the roster says it can do.

Extracted from `ChannelManager` (optimization plan B2a). The manager owns *lifecycle* (awake, busy, parked) and
*admission* (the queue); this owns *routing*: the roster an agent reads, the instructions that carry it, and the
three-pass mention exchange.

It talks to the manager through a narrow protocol rather than the class itself, so routing can be tested — and
changed — without a queue, a worker thread or a provider:

    profiles()      which agents exist on disk
    channels        the resident ones, by name
    agents_dir      where their state files live
    enqueue(...)    the one way a message is admitted
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from core import channelstate
from core import mentions
from server.instructions import DEFAULT_INSTRUCTIONS, HIVE_INSTRUCTIONS


class ChannelRegistry(Protocol):
    """What routing needs from the thing that owns the channels."""

    agents_dir: Path
    channels: dict

    def profiles(self) -> list[str]: ...

    def enqueue(self, name: str, text: str, *, author: str = "", hops: int = 0,
                reply_to: str | None = None) -> dict: ...


class MentionRouter:
    def __init__(self, registry: ChannelRegistry):
        self.registry = registry

    # -- the roster --------------------------------------------------------
    def roster(self, exclude: str) -> str:
        """Every peer with its live state — `@scout — idle`, `@atlas — working, 2 queued`.

        Delegation is a choice between an agent that can start now and one that will get to it later, and
        that choice only exists if the prompt says which is which. A resident channel reports its real
        state; a profile that is not resident reports what is on disk, with a stale in-flight state shown
        as `stale` rather than `working` (nothing is working it).
        """
        registry = self.registry
        live = dict(registry.channels)
        rows = []
        for name in registry.profiles():
            if name == exclude:
                continue
            channel = live.get(name)
            if channel is not None:
                detail = channel.state
                pending = channel.queue.qsize()
                if pending:
                    detail += f", {pending} queued"
            else:
                recorded = str(channelstate.read(registry.agents_dir / name).get("state")
                               or channelstate.AVAILABLE)
                detail = "asleep" if recorded == channelstate.SLEEPING else (
                    "stale" if recorded in (channelstate.WORKING, channelstate.QUEUED) else recorded)
            rows.append(f"@{name} — {detail}")
        return "; ".join(rows)

    def instructions_for(self, channel) -> str:
        """The shared instructions plus the hive roster, so `@name` is usable without prior context.

        An agent that does not know which peers exist cannot address one — the mention rule would be
        documented and unusable. The roster is the profiles directory, minus this channel, with states.
        """
        peers = self.roster(channel.name)
        text = DEFAULT_INSTRUCTIONS + (" " + HIVE_INSTRUCTIONS.format(roster=peers) if peers else "")

        # skills are advertised, not loaded: one line each, so a library costs lines rather than documents
        from core import skills as skills_mod

        # a finding the agent cannot see is a finding nobody acts on
        try:
            root = getattr(channel, "root", None)
            report = (root.parents[1] / "security" / "REPORT.md") if root else None
            if report is not None and report.is_file():
                head = report.read_text(encoding="utf-8")[:400]
                if "Nothing differs from the baseline" not in head:
                    text += ("\n\nSECURITY: the red/blue watchdogs have outstanding findings. Call "
                             "`security_report` before continuing, tell the user what it says, and do not attempt "
                             "repairs yourself.")
        except OSError:
            pass

        root_for_skills = getattr(channel, "root", None)
        athena_for_skills = root_for_skills.parents[1] if root_for_skills else None
        # `self.cfg` is not set on this object, so the lookup yields [] and no plugin-shipped skills are advertised.
        enabled_for_skills = []
        if root_for_skills is not None:
            try:
                from core import agentconfig as _agentconfig
                from core import plugins as _plugins

                lane_block = {"plugins": (_agentconfig.read(root_for_skills) or {}).get("plugins")}
                ceiling = getattr(getattr(self, "registry", None), "cfg", None) or {}
                enabled_for_skills = _plugins.lane_enabled(ceiling, lane_block)[0]
            except Exception:                  # noqa: BLE001 — an unreadable lane config must not break a prompt
                enabled_for_skills = []
        index = skills_mod.index(root_for_skills, athena_root=athena_for_skills,
                                 plugin_names=enabled_for_skills)
        if index:
            text += ("\n\nSkills available to you (open one with skill_read when the task matches its description):\n"
                     + "\n".join(index))
        return text

    # -- the three-pass exchange ------------------------------------------
    def route(self, channel, result: dict, job: dict) -> None:
        """Send an answer home, dispatch the mentions in it, and clear the asker's pending state."""
        registry = self.registry
        hops = int(job.get("hops") or 0)
        reply_to = job.get("reply_to")

        # whoever asked is no longer waiting, even if there is no reply to send: a failed turn that
        # leaves an agent "waiting on Scout" forever is worse than a failure that says nothing
        if reply_to and reply_to != channel.name:
            asker = registry.channels.get(reply_to)
            if asker is not None:
                with asker._lock:
                    # one answer clears ONE outstanding ask. Popping outright lost the second ask when a
                    # follow-up was already queued — the asker then showed nothing pending while still
                    # waiting, which is the exact state the indicator exists to report
                    remaining = asker.awaiting.get(channel.name, 1) - 1
                    if remaining > 0:
                        asker.awaiting[channel.name] = remaining
                    else:
                        asker.awaiting.pop(channel.name, None)

        if result.get("status") not in ("completed", "exhausted_budget", "exhausted_turns"):
            return

        # turn 3 · the answer goes back to whoever asked
        if reply_to and reply_to != channel.name:
            try:
                registry.enqueue(reply_to, text=(result.get("reply") or "").strip() or "(no reply)",
                                 author=channel.name, hops=hops + 1)
            except (KeyError, ValueError):
                pass

        # turn 1 · this agent addressed another one
        targets = mentions.find(result.get("reply") or "", known=registry.profiles(), exclude=channel.name)
        for target in targets:
            if hops + 1 > mentions.MAX_HOPS:
                break
            question = mentions.question_for(result.get("reply") or "", target)
            try:
                registry.enqueue(target, text=question, author=channel.name,
                                 hops=hops + 1, reply_to=channel.name)
            except KeyError:
                continue
            except ValueError as exc:
                # a mention to a sleeping channel must not vanish: the asker is told, as its own inbound
                # message, that the delivery failed and why — otherwise it waits forever for an answer
                try:
                    registry.enqueue(channel.name, text=f"[delivery failed] {exc}",
                                     author="", hops=hops + 1)
                except ValueError:
                    pass
