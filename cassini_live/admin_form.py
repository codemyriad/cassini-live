"""The "Cassini Live" administration settings form, built from the engine panel dict.

The form is the short version: a status line and the engine radio. Declarative forms
cannot disable an option or update live, so the option labels carry eligibility and state,
and the ExApp re-registers the form when they change. Details, live progress, cancel,
model removal and licenses live on the admin-only settings page that the form's "?" link
opens. The output is deterministic (no timestamps, progress in 5% steps) so identical
publishes are skipped. Pure: no I/O, no manager or app imports.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from nc_py_api.ex_app.ui.settings import SettingsField, SettingsFieldType, SettingsForm

from . import catalog

FORM_ID = "cassini_live_engine"
SECTION_ID = "ai"
SECTION_TYPE = "admin"
ENGINE_FIELD = "speech_engine"
# Root-relative path of the settings page through the AppAPI proxy; settings_page prefixes
# the Nextcloud web root.
PAGE_PATH = "/index.php/apps/app_api/proxy/live_transcription/settings/index.html"
DEFAULT_PAGE_URL = PAGE_PATH
PAGE_HINT = "Full settings and progress: click ? next to the title."
MAX_LABEL = 90
MAX_TEXT = 400
PROGRESS_STEP = 5

ACTIVE_STATES = frozenset(
    {"validating", "downloading", "verifying", "stopping", "loading", "checking", "rolling_back"}
)
STEPS = {
    "validating": "checking the requirements",
    "verifying": "verifying the model files",
    "stopping": "stopping captions",
    "loading": "loading the engine",
    "checking": "running the capacity check",
    "rolling_back": "rolling back",
}
LANGUAGE_NAMES = {
    "en": "English",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "nl": "Dutch",
    "pl": "Polish",
}
# States in which selecting the previous engine cancels; the settings page offers Cancel in
# the same ones (validating has no task to cancel yet, a rollback cannot be stopped).
CANCELLABLE = frozenset({"downloading", "verifying", "stopping", "loading", "checking"})
# One short phrase per eligibility reason; the full sentence is on the settings page.
REASON_PHRASES = {
    "limits_missing": "container limits not set",
    "memory_limit": "needs more container memory",
    "host_memory": "needs more host memory",
    "cpu_quota": "needs more CPU",
    "cpu_instructions": "CPU lacks {required}",
    "architecture": "unsupported processor",
    "storage": "no app storage",
    "disk_space": "not enough disk space",
    "network": "download site unreachable",
}
CAPTIONS_OFF = frozenset({"stopping", "loading", "checking", "rolling_back"})
ON_DISK = frozenset({"ready", "bundled"})
GIB = 1024 * catalog.MIB


@dataclass(frozen=True)
class Selection:
    engine: str | None


def _busy(panel):
    return (panel.get("switch") or {}).get("state") in ACTIVE_STATES


def _name(panel, engine_id):
    for card in panel.get("engines", ()):
        if card["id"] == engine_id:
            return card["name"]
    return catalog.get(engine_id).name if engine_id in catalog.ENGINES else str(engine_id)


def _sentence(text):
    text = str(text).strip()
    return text if not text or text[-1] in ".!?" else text + "."


def _cut(text, limit):
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def progress_percent(progress: dict | None) -> int | None:
    if not progress or not progress.get("bytesTotal"):
        return None
    percent = 100 * progress.get("bytesDone", 0) // progress["bytesTotal"]
    percent = min(100, max(0, int(percent)))
    return percent - percent % PROGRESS_STEP


def _step(switch):
    state = switch["state"]
    if state != "downloading":
        return STEPS.get(state, state.replace("_", " "))
    progress = switch.get("progress") or {}
    percent = progress_percent(progress)
    phase = progress.get("phase", "downloading")
    return phase if percent is None else f"{phase} {percent}%"


def _fact(card):
    """One key fact; accuracy, license and requirements are on the settings page."""
    languages = card["languages"]
    if len(languages) == 1:
        fact = LANGUAGE_NAMES.get(languages[0], languages[0]) + " only"
    else:
        fact = f"{len(languages)} languages"
    download = (card.get("requirements") or {}).get("downloadBytes")
    if card["model"]["state"] not in ON_DISK and download:
        fact += f", {download / GIB:.2f} GiB download"
    return fact


def _reason_phrase(reasons):
    """The first reason as a short phrase, never a cut sentence; "see ?" for unknown codes."""
    if not reasons:
        return "requirements not met (see ?)"
    phrase = REASON_PHRASES.get(reasons[0].get("code"))
    if phrase is None:
        return "requirements not met (see ?)"
    required = str(reasons[0].get("required") or "").upper().replace(",", ", ")
    if "{required}" in phrase and not required:
        return "CPU lacks required instructions"
    phrase = phrase.format(required=required)
    return phrase + (" and more" if len(reasons) > 1 else "")


def option_label(card: dict, panel: dict) -> str:
    switch = panel.get("switch") or {}
    reasons = card.get("reasons") or []
    if _busy(panel) and card["id"] == switch.get("target"):
        step = _step(switch)
        state = step if switch["state"] == "downloading" else "switching: " + step
        return _cut(f"{card['name']} — {state}", MAX_LABEL)
    if _busy(panel) and card["id"] == switch.get("previous"):
        if switch["state"] == "rolling_back":
            state = "being restored; captions are off until then"
        elif switch["state"] not in CANCELLABLE:
            state = "in use; checking the requirements"
        elif switch["state"] in CAPTIONS_OFF:
            state = "captions stopped during the change (select to cancel)"
        else:
            state = "in use until the switch finishes (select to cancel)"
        return _cut(f"{card['name']} — {state}", MAX_LABEL)
    if not card.get("active") and not card.get("eligible") and not card.get("advisory"):
        return _cut(f"{card['name']} — unavailable: {_reason_phrase(reasons)}", MAX_LABEL)
    if card.get("active"):
        state = "in use"
    else:
        state = "ready" if card["model"]["state"] == "ready" else "available"
    parts = [f"{card['name']} — {state}", _fact(card)]
    if card.get("advisory") and reasons:
        parts.append("warning")
    return _cut(" · ".join(parts), MAX_LABEL)


def _refusal(panel):
    """A choice made in the form and refused: shown first, so a silent revert is explained."""
    refusal = panel.get("lastRefusal")
    if not refusal:
        return []
    parts = [f"Not applied: {_name(panel, refusal.get('engine'))}.",
             _sentence(refusal.get("message", ""))]
    reasons = refusal.get("reasons") or []
    if reasons:
        parts.append(_sentence(reasons[0].get("message", "")))
    return parts


def _state(panel):
    switch = panel.get("switch") or {}
    state, parts = switch.get("state"), []
    if _busy(panel) and switch.get("target"):
        parts.append(f"Changing to {_name(panel, switch['target'])}: {_step(switch)}.")
        if state in CAPTIONS_OFF:
            parts.append("Captions are off until it finishes.")
        return parts
    target = _name(panel, switch["target"]) if switch.get("target") else None
    if state == "succeeded" and target:
        parts.append(f"Now using {target}.")
    elif state == "failed" and target:
        message = (switch.get("error") or {}).get("message") or "unknown error"
        parts.append(f"Could not switch to {target}: {_sentence(message)}")
        if switch.get("rolledBack") and switch.get("previous"):
            parts.append(f"Rolled back to {_name(panel, switch['previous'])}.")
        else:
            parts.append("Captions will recover automatically.")
    elif state == "cancelled" and target:
        parts.append(f"The change to {target} was cancelled.")
    else:
        parts.append(f"Using {_name(panel, panel['active'])}.")
    error = (panel.get("lastError") or {}).get("message")
    if error and error != (switch.get("error") or {}).get("message"):
        parts.append(_sentence(error))
    return parts


def _captions(status):
    safety = status.get("resourceSafety") or {}
    if not safety:
        return []
    if safety.get("state") == "ready":
        parts = ["Captions are ready."]
    else:
        parts = [f"Captions are paused: {_sentence(safety.get('reason') or safety.get('state'))}"]
    if safety.get("adminActionRequired"):
        parts.append("Administrator action required.")
    return parts


def status_text(panel: dict, status: dict, notes: tuple[str, ...] = ()) -> str:
    parts = _refusal(panel)
    parts += [_sentence(note) for note in notes]
    parts += _state(panel)
    parts += _captions(status)
    text = _cut(" ".join(p for p in parts if p), MAX_TEXT - len(PAGE_HINT) - 1)
    return f"{text} {PAGE_HINT}" if text else PAGE_HINT


def _engine_field(panel):
    switch = panel.get("switch") or {}
    cards = {card["id"]: card for card in panel.get("engines", ())}
    options = {option_label(cards[e], panel): e for e in catalog.ENGINES if e in cards}
    description = (
        "One engine serves the whole server. Changing it stops captions in active calls for a "
        "few minutes."
    )
    calls = panel.get("activeCalls", 0)
    if calls:
        description += f" {calls} call{' has' if calls == 1 else 's have'} captions on now."
    return SettingsField(
        id=ENGINE_FIELD,
        title="Speech engine",
        type=SettingsFieldType.RADIO,
        default=switch["target"] if _busy(panel) and switch.get("target") else panel["active"],
        options=options,
        description=description,
    )


def build_form(
    panel: dict, status: dict, notes: tuple[str, ...] = (), page_url: str = DEFAULT_PAGE_URL
) -> SettingsForm:
    return SettingsForm(
        id=FORM_ID,
        section_id=SECTION_ID,
        section_type=SECTION_TYPE,
        title="Cassini Live",
        description=status_text(panel, status, notes),
        doc_url=page_url,
        priority=50,
        fields=[_engine_field(panel)],
    )


def parse_values(records: Iterable) -> Selection:
    """Only the engine choice; keys left by 0.7.0 (license, cleanup) are ignored."""
    values = {}
    for record in records:
        key, value = (record.key, record.value) if hasattr(record, "key") else record
        values[key] = value
    engine = values.get(ENGINE_FIELD)
    engine = engine.strip() if isinstance(engine, str) else None
    return Selection(engine=engine or None)
