"""Per-agent configuration — one profile, one provider, one persona.

A profile is an agent, and an agent has its own model, endpoint and credentials; the global block is the default a
profile inherits.

    profiles/<channel>/config.yaml
        persona:  name · role · tone · boundaries      (the human-facing description)
        model:    provider · base_url · name · window  (where this agent's answers come from)
        api_key:  <secret name>                        (a NAME, never the key)

**The key is never written here.** It goes to `.athena/.secret` as `<secret-name>=<key>`, and this file
carries only the name of that line. A profile directory gets copied, archived and read by the agent; a
secret file does not get copied with it.

Markdown-adjacent YAML with one comment line per field, matching the house rule for configs: a line above
the value travels with it in a diff, an inline comment dies the moment someone rewrites the line.
"""
from __future__ import annotations

import colorsys
import random
import os
from pathlib import Path

import yaml

FILENAME = "config.yaml"          # the SAME filename as the global config — one name, one schema
LEGACY_FILENAME = "agent.yaml"    # the pre-rename name; still read, migrated on first load

TEMPLATE = """# Agent configuration — created by the setup wizard. Everything here overrides config.yaml for this
# channel only. The API key is not in this file: `api_key` names a line in ~/.athena/.secret.

persona:
  # The name this agent answers to and shows in the interface.
  name: {name}

  # What this agent is for, in one sentence.
  role: {role}

  # How it should sound: tone, register, length.
  tone: {tone}

  # Anything it must never do.
  boundaries: {boundaries}

model:
  # Wire format of the endpoint: openai, anthropic, or local (served from models/).
  provider: {provider}

  # Base URL of the endpoint; ignored when provider is local.
  base_url: {base_url}

  # Model identifier exactly as the endpoint spells it.
  name: {model}

  # Context window in tokens; the prompt stack is sized as a share of this.
  window: {window}

  # Hard ceiling on one reply, in tokens.
  max_output_tokens: {max_output_tokens}

  # Sampling temperature.
  temperature: {temperature}

  # Local model to serve, when provider is local (a folder under .athena/models).
  local: {local}

# Colour that identifies this agent in the interface, chosen once when the profile is created.
accent: {accent}

# Name of the .secret line holding this agent's key; empty for a local model.
api_key: {api_key}
"""

def _write_atomic(target: Path, text: str) -> None:
    """tmp + fsync + replace. A `write_text` torn by a crash leaves YAML that `read()` swallows as `{}`, so the agent
    silently falls back to the global provider and loses its persona with no error anywhere."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, target)


def _hue_of(colour: str) -> float | None:
    """Hue in 0..1 for a #rrggbb string, or None when it is not one."""
    text = (colour or "").strip().lstrip("#")
    if len(text) != 6:
        return None
    try:
        red, green, blue = (int(text[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return None
    hue, _, _ = colorsys.rgb_to_hls(red, green, blue)
    return hue


def _hue_gap(first: float, second: float) -> float:
    """Shortest distance between two hues, 0..0.5."""
    diff = abs(first - second) % 1.0
    return min(diff, 1.0 - diff)


def random_accent(avoid: list[str] | None = None, min_gap: float = 0.10) -> str:
    """A random colour that stays legible on a dark card and tells agents apart.

    Random RGB alone can land on near-black or near-white, which is unreadable as a border on a dark
    surface — so the hue is random and the lightness is constrained. It is chosen once and stored, so an
    agent keeps its colour for its whole life instead of changing on every page load.

    `avoid` holds the colours already in use: two agents 3 degrees apart in hue look identical, and the
    colour's whole job is to answer "which agent is speaking" at a glance. Measured failure: hashing two
    names gave `#bee467` and `#c3e467` — the same colour to the eye.
    """
    hues = [hue for hue in (_hue_of(colour) for colour in (avoid or [])) if hue is not None]
    best = None
    best_gap = -1.0
    for _ in range(60):
        candidate = random.random()
        gap = min((_hue_gap(candidate, used) for used in hues), default=1.0)
        if gap >= min_gap:
            best = candidate
            break
        if gap > best_gap:
            best, best_gap = candidate, gap
    hue = best if best is not None else random.random()
    saturation = random.uniform(0.55, 0.90)
    lightness = random.uniform(0.55, 0.72)
    red, green, blue = colorsys.hls_to_rgb(hue, lightness, saturation)
    return "#{:02x}{:02x}{:02x}".format(round(red * 255), round(green * 255), round(blue * 255))


def stable_accent(name: str) -> str:
    """A colour derived from the name, for profiles that predate the field.

    Deterministic, so an old agent keeps one colour even though nothing was written to its file; a
    re-save promotes it into config.yaml and from then on it is stored rather than derived.
    """
    import hashlib

    digest = hashlib.sha256((name or "agent").encode("utf-8")).hexdigest()
    # the whole digest, not its first four hex chars: those are not uniformly distributed enough to keep
    # two short names apart, and `default`/`scout` landed on nearly the same hue
    hue = int(digest, 16) % 36000 / 36000
    red, green, blue = colorsys.hls_to_rgb(hue, 0.65, 0.70)
    return "#{:02x}{:02x}{:02x}".format(round(red * 255), round(green * 255), round(blue * 255))


def accent_for(name: str, config: dict | None = None) -> str:
    """The agent's colour: stored if it has one, otherwise derived from its name."""
    stored = str((config or {}).get("accent") or "").strip()
    return stored if stored.startswith("#") else stable_accent(name)


DEFAULTS = {
    "accent": "",
    "persona": {"name": "", "first_name": "", "last_name": "", "nickname": "", "role": "",
                "tone": "", "boundaries": "", "likes": "", "dislikes": "", "bio": "",
                "birth_date": "", "accent": ""},
    "model": {"provider": "openai", "base_url": "", "name": "", "window": 32768,
              "max_output_tokens": 1024, "temperature": 0.7, "local": "",
              "context_window_local": None, "context_window_provider": None,
               "context_window_provider_model": None},
    "api_key": "",
}


# Sections an agent may override. `server` (the bind address) and `agents` (queue admission) are properties of
# the multiplexer, not of one agent, so they are deliberately absent — listing them here would be a lie.
# `plugins` is here so the section exists in the generated file and a lane's list survives a re-save. Its `enabled`
# is legitimately blank — blank means "inherit the global ceiling", which is not the same as "no plugins".
AGENT_SECTIONS = ("model", "plugins", "budget", "tools", "metrics", "compression")
BLANK_BY_DESIGN = {
    "plugins": ("enabled",),
    # a lane has no floor of its own until one is set, and a provider-published window is absent until one is
    # fetched: blank is the honest value in both cases
    "model": ("context_window_local", "context_window_provider", "context_window_provider_model"),
}

PERSONA_TEMPLATE = """\
# Agent configuration — the same settings and the same descriptions as the global config.yaml, because this file
# is what an operator edits to change THIS agent. A blank value means "inherit the global one": delete what you
# do not want to override. Key values are never here — `model.key_name` names a line in a `.secret` file.
#
# Not listed, because they belong to the multiplexer rather than to one agent:
#   server   the bind address and port
#   agents   queue depth, idle-sleep window, sweep interval

persona:
  # The name this agent answers to and shows in the interface.
  name:

  # What this agent is for, in one sentence.
  role:

  # How it should sound: tone, register, length.
  tone:

  # Anything it must never do.
  boundaries:

  # The colour the interface uses for this agent; assigned once, at creation.
  accent:

"""


def _blank_values(block: str) -> str:
    """Copy a config block verbatim — comments and all — but drop the values, so they mean "inherit"."""
    import re as _re

    out = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            out.append(line)
            continue
        match = _re.match(r"^(\s*)([a-zA-Z_][a-zA-Z0-9_]*):(\s*)(.*)$", line)
        if match and not match.group(4).lstrip().startswith("#"):
            indent, key, _, value = match.groups()
            out.append(f"{indent}{key}:" if value.strip() else line)
            continue
        out.append(line)
    return "\n".join(out)


def template_for_agent(defaults_path: Path | None = None) -> str:
    """The agent `config.yaml`, generated from the global defaults so the two cannot disagree.

    Same keys, same comments, blank values. Generated rather than hand-maintained: a hand-written second copy
    drifts the moment a default is added, and then the file that claims to list "available settings" is wrong.
    """
    import re as _re

    defaults_path = defaults_path or (Path(__file__).resolve().parent.parent / "config" / "defaults.yaml")
    try:
        text = defaults_path.read_text(encoding="utf-8")
    except OSError:
        return PERSONA_TEMPLATE

    out = [PERSONA_TEMPLATE.rstrip(), ""]
    current, block = None, []
    for line in text.splitlines():
        header = _re.match(r"^([a-z_][a-zA-Z0-9_]*):\s*$", line)
        if header:
            if current in AGENT_SECTIONS and block:
                # the section HEADER has to be emitted too — without it the block had no parent key and the
                # generated file silently contained orphaned settings (`enabled:`) under `persona:`
                out.append(f"{current}:")
                out.append(_blank_values("\n".join(block)).strip("\n"))
                out.append("")
            current, block = header.group(1), []
            continue
        if current in AGENT_SECTIONS:
            block.append(line)
    if current in AGENT_SECTIONS and block:
        out.append(f"{current}:")
        out.append(_blank_values("\n".join(block)).strip("\n"))
    return "\n".join(out).rstrip() + "\n"


def _yaml_scalar(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        # `enabled: []` is a list, and stringifying it produced `enabled: "['clock']"` — one bogus name that
        # parses as a plugin called "['clock']". A list renders as a list.
        items = []
        for item in value:
            text = " ".join(str(item).split())
            items.append(f'"{text}"' if (not text or any(c in text for c in " ,:#[]{}")) else text)
        return "[" + ", ".join(items) + "]"
    text = " ".join(str(value if value is not None else "").split()).replace('"', "'")
    return f'"{text}"'


def _has_block_children(lines: list[str], index: int) -> bool:
    """True when the next non-blank line under this key is a `- item`, i.e. the template owns a block list."""
    indent = len(lines[index]) - len(lines[index].lstrip())
    for follow in lines[index + 1:]:
        if not follow.strip():
            continue
        deeper = len(follow) - len(follow.lstrip())
        if deeper <= indent:
            return False
        return follow.lstrip().startswith("- ")
    return False


def _fill(text: str, values_by_section: dict) -> str:
    """Put values back into the blank template, leaving every comment line untouched."""
    import re as _re

    out, current, subsection = [], None, None
    lines = text.splitlines()
    for index, line in enumerate(lines):
        header = _re.match(r"^([a-z_][A-Za-z0-9_]*):\s*$", line)
        if header:
            current, subsection = header.group(1), None
            out.append(line)
            continue
        # `key: []` is a leaf whose value is already written, and the template uses that form for lists
        leaf = _re.match(r"^(\s+)([a-zA-Z_][A-Za-z0-9_]*):\s*(\[\s*\])?\s*$", line)
        if leaf and current in values_by_section:
            section = values_by_section[current] or {}
            key = leaf.group(2)
            indent = len(leaf.group(1))
            if indent == 2 and (isinstance(section.get(key), dict) or key == "local"):
                # `local:` is a nested BLOCK. Treating it as a leaf when its value was the empty string made
                # its four-space children fall through to the model section, so `model.name` leaked into
                # `local.name` and the agent then looked like a local model serving "deepseek-chat".
                subsection = key
                out.append(line)
                continue
            source = section.get(subsection) if (indent > 2 and subsection) else section
            if isinstance(source, dict) and key in source and source[key] is not None:
                value = source[key]
                # A list may be written as a block (`key:` with `- item` children) or inline. Inlining one that owns
                # block children left them dangling and the file stopped parsing; leaving a childless one alone
                # produced `null`. So: look at what actually follows the header.
                if isinstance(value, (list, tuple)) and leaf.group(3) is None and _has_block_children(lines, index):
                    out.append(line)
                    continue
                out.append(f"{leaf.group(1)}{key}: {_yaml_scalar(value)}")
                continue
        out.append(line)
    return "\n".join(out)


def local_model_name(model_cfg: dict) -> str:
    """The downloaded model this config serves locally, or "" when it is not a local model.

    `model.local` is a **section** in both config files now (enabled, backend, name, port, …), so reading it as a
    string produced the whole dict dumped into a warning: `model "{'enabled': None, …}" is not in models`. The
    older agent files stored just the name, and that form is still accepted.
    """
    local = (model_cfg or {}).get("local")
    if isinstance(local, dict):
        # only an ENABLED local block means "served from models/". A leftover name is not a decision —
        # it is what made an openai agent look like a local model called "deepseek-chat".
        if not local.get("enabled"):
            return ""
        return str(local.get("name") or "").strip()
    return str(local or "").strip()


def path(profile_root: Path) -> Path:
    """The agent's `config.yaml` — the same filename as the global config, migrated from `config.yaml` once."""
    root = Path(profile_root)
    target = root / FILENAME
    legacy = root / LEGACY_FILENAME
    if not target.exists() and legacy.exists():
        try:
            legacy.rename(target)                 # one-time, in place, no content change
        except OSError:
            return legacy                         # read-only tree: the legacy key spelling still resolves
    return target


def read(profile_root: Path) -> dict:
    """This profile's agent config, or {} when it has none (a channel that inherits the global provider)."""
    target = path(profile_root)
    try:
        data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(data, dict):
        return {}
    merged = {**DEFAULTS, **data}
    merged["persona"] = {**DEFAULTS["persona"], **(data.get("persona") or {})}
    merged["model"] = {**DEFAULTS["model"], **(data.get("model") or {})}
    return merged


def _preserve_sections(text: str, existing_text: str) -> str:
    """Re-attach any top-level section that exists in the live file but not in the template.

    The template is the shape of a *new* agent. A lane may legitimately carry a section the template does not know
    about — `plugins:` is the first — and regenerating from the template would delete it on the next wizard save.
    """
    import re as _re

    def top_level(block: str) -> dict[str, str]:
        found: dict[str, str] = {}
        current = None
        for line in block.splitlines():
            header = _re.match(r"^([a-z_][A-Za-z0-9_]*):", line)
            if header:
                current = header.group(1)
                found[current] = line + "\n"
            elif current:
                found[current] += line + "\n"
        return found

    rendered, live = top_level(text), top_level(existing_text)
    extra = [name for name in live if name not in rendered and live[name].strip()]
    if not extra:
        return text
    tail = "\n".join(live[name].rstrip("\n") for name in extra)
    return text.rstrip("\n") + "\n\n" + tail + "\n"


def write(profile_root: Path, config: dict, base: dict | None = None) -> Path:
    """Write this agent's `config.yaml` — the global file's keys and comments, with its own values.

    The accent colour is assigned here, once: a profile keeps the colour it was born with, and a re-save
    (edited persona, changed model) never recolours the agent — that would make the interface's strongest
    identity cue the least stable thing on it.
    """
    existing = read(profile_root)
    existing_text = ""
    try:
        existing_text = (Path(profile_root) / FILENAME).read_text(encoding="utf-8")
    except OSError:
        pass
    accent = str(config.get("accent") or existing.get("accent") or random_accent())
    persona = {**DEFAULTS["persona"], **(config.get("persona") or {})}
    persona["accent"] = accent
    model = {**DEFAULTS["model"], **(config.get("model") or {})}
    if config.get("api_key"):
        model["key_name"] = config["api_key"]

    # EVERY applicable setting is written with its effective value: budget, tools, metrics and the whole
    # model block. The file is the surface an operator tunes, so nothing that can be changed may be invisible.
    # the lane's own plugin list, carried through a re-save: `None` means inherit, so it must stay blank rather
    # than become the ceiling (which would freeze it at creation time)
    plugin_list = None
    for source in (config.get("plugins"), existing.get("plugins")):
        if isinstance(source, dict) and source.get("enabled") is not None:
            plugin_list = source.get("enabled")
            break

    values: dict = {
        "plugins": {"enabled": plugin_list},
        "persona": {key: persona.get(key) for key in
                    ("name", "role", "tone", "boundaries", "accent")},
        "model": {key: model.get(key) for key in
                  ("provider", "base_url", "name", "window", "max_output_tokens",
                   "temperature", "key_name", "context_window_local", "context_window_provider",
                   "context_window_provider_model")},
    }
    # the effective global config supplies the values an agent has not overridden, so every key can be written
    if base is None:
        try:
            from config import loader as _loader
            base = _loader.load()
        except Exception:                             # noqa: BLE001 — a missing global config is not fatal
            base = {}
    for section in ("budget", "tools", "metrics", "compression"):
        # an explicit `None` in the agent file means "not set", not "set to null": merging it over the global
        # value would blank the section and the file would show every setting as empty
        override = {k: v for k, v in ((config or {}).get(section) or {}).items() if v is not None}
        merged_section = {**(base.get(section) or {}), **override}
        if merged_section:
            values[section] = merged_section
    local = model.get("local")
    if isinstance(local, str):
        local = {"name": local} if local.strip() else {}
    elif not isinstance(local, dict):
        local = {}
    is_local = str(model.get("provider") or "").lower() == "local"
    values["model"]["local"] = ({"enabled": True, "name": local.get("name") or model.get("name") or ""}
                                if is_local else {})


    # the template declares `api_key:` and the fill never supplied it, so the name of the secret was written
    # nowhere: the key existed in a .secret and the profile could not point at it
    values["api_key"] = str(model.get("key_name") or existing.get("api_key") or "")
    body = _preserve_sections(_fill(template_for_agent(), values), existing_text or "")
    target = path(profile_root)
    _write_atomic(target, body)
    return target


def write_identity(profile_root: Path, persona: dict) -> Path:
    """Write assistant/ASSISTANT.md from the persona.

    The wizard collects a persona, and the prompt stack reads ASSISTANT.md — so the persona has to land
    there, or the agent is described on one screen and behaves as another. Frontmatter carries the
    machine fields, the body carries the voice.
    """
    from core import profile as profile_mod

    profile_mod.ensure(profile_root)
    name = " ".join(str(persona.get("name") or "").split()) or "Athena"
    role = " ".join(str(persona.get("role") or "").split())
    tone = " ".join(str(persona.get("tone") or "").split())
    boundaries = " ".join(str(persona.get("boundaries") or "").split())

    first, _, last = name.partition(" ")
    body = ["---",
            f"name_first: {first or 'null'}",
            f"name_last: {last or 'null'}",
            "name_nick: null",
            "gender: null", "pronouns: null", "voice: null",
            "---", "", "# Who I am", ""]
    body.append(f"I am {name}.")
    if role:
        body.append(f"")
        body.append(role[0].upper() + role[1:])
    if tone:
        body.append(f"")
        body.append(f"I speak this way: {tone}.")
    if boundaries:
        body.append(f"")
        # "never guesses a fact" must not become "I do not: never guesses a fact" — state the limit as a
        # limit, with the persona's own wording kept intact
        body.append(f"I must never: {boundaries.rstrip('.')}.")
    body.append("")
    target = Path(profile_root) / "assistant" / "ASSISTANT.md"
    _write_atomic(target, "\n".join(body))
    return target


def as_cfg(agent_cfg: dict, base_cfg: dict) -> dict:
    """A global config with this agent's model settings applied — what `providers.build` expects."""
    import copy

    cfg = copy.deepcopy(base_cfg)
    model = cfg.setdefault("model", {})
    agent_model = agent_cfg.get("model") or {}

    # no `provider` declared: keep the global wire format rather than silently forcing "openai"
    provider = str(agent_model.get("provider") or (model.get("provider") or "openai")).lower()
    local_declared = agent_model.get("local")
    local_active = (provider == "local"
                    or (isinstance(local_declared, dict) and bool(local_declared.get("enabled"))))
    local_name = local_model_name({**agent_model,
                                   "local": {**local_declared, "enabled": True}}
                                  if (isinstance(local_declared, dict) and local_active) else agent_model)
    local_cfg = dict(model.get("local") or {})

    if provider == "local" or local_name:
        model["provider"] = "openai"
        local_cfg.update({"enabled": True, "name": local_name or local_cfg.get("name", "")})
        model["local"] = local_cfg
        model["name"] = str(agent_model.get("name") or local_cfg.get("name") or "")
    else:
        local_cfg["enabled"] = False
        model["local"] = local_cfg
        model["provider"] = provider
        if agent_model.get("base_url"):
            model["base_url"] = agent_model["base_url"]
        elif provider != str((base_cfg.get("model") or {}).get("provider") or "").lower():
            # a DIFFERENT wire format with no URL of its own must not inherit the global endpoint —
            # an anthropic agent would have posted to the deepseek URL
            from providers.base import DEFAULT_BASE_URLS
            model["base_url"] = DEFAULT_BASE_URLS.get(provider, "")
        model["name"] = str(agent_model.get("name") or model.get("name") or "")

    for key in ("window", "max_output_tokens", "temperature", "key_name", "context_window_local",
                "context_window_provider", "context_window_provider_model"):
        if agent_model.get(key) is not None:
            model[key] = agent_model[key]

    # the file lists budget / tools / metrics, so they must actually apply — an override that is documented and
    # ignored is worse than one that is absent
    for section in ("budget", "tools", "metrics"):
        agent_section = agent_cfg.get(section)
        if isinstance(agent_section, dict) and agent_section:
            merged_section = cfg.setdefault(section, {})
            if not isinstance(merged_section, dict):
                merged_section = {}
                cfg[section] = merged_section
            for key, value in agent_section.items():
                if value is not None:
                    merged_section[key] = value
    return cfg
