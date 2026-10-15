"""The "Cassini Live" administration settings form, built from the engine panel dict.

Declarative forms cannot disable an option or update live, so the option labels carry
eligibility and status, and the ExApp re-registers the form when they change. The output
is deterministic (no timestamps, progress in 5% steps) so identical publishes are skipped.
Pure: no I/O, no manager or app imports.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass

from nc_py_api.ex_app.ui.settings import SettingsField, SettingsFieldType, SettingsForm

from . import catalog

FORM_ID = "cassini_live_engine"
SECTION_ID = "ai"
SECTION_TYPE = "admin"
ENGINE_FIELD = "speech_engine"
LICENSE_FIELD = (
    "nvidia_oml_" + catalog.get("nemotron-en").license.version.replace("-", "_") + "_accepted"
)
CLEANUP_FIELD = "remove_unused_models"
DOC_URL = (
    "https://github.com/codemyriad/cassini-live/blob/main/docs/install.md#choose-a-speech-engine"
)
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
MAX_LABEL = 200
MAX_TEXT = 1000
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
CAPTIONS_OFF = frozenset({"stopping", "loading", "checking", "rolling_back"})
REMOVABLE = frozenset({"ready", "partial", "corrupt"})
GIB = 1024 * catalog.MIB


@dataclass(frozen=True)
class Selection:
    engine: str | None
    accept_license: bool
    cleanup: bool


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


def _number(value, digits=1):
    return f"{round(value, digits):g}"


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


def _facts(card):
    languages = card["languages"]
    if len(languages) == 1:
        facts = [LANGUAGE_NAMES.get(languages[0], languages[0]) + " only"]
    else:
        facts = [
            f"{len(languages)} languages"
            + (", auto-detect" if card.get("automaticLanguage") else "")
        ]
    if card["model"]["state"] == "bundled":
        return facts + ["bundled, light"]
    wer = (card.get("accuracy") or {}).get("meetingWer")
    if wer is not None:
        facts.append(f"{_number(wer * 100)}% word errors on our meetings")
    if card.get("license"):
        facts.append(card["license"]["id"])
    need = card["requirements"]
    cpus = need["cpuQuota"]
    resources = (
        f"needs {_number(math.ceil(need['memoryLimitMiB'] / 102.4 - 1e-9) / 10)} GiB memory "
        f"({_number(need['recommendedMemoryLimitMiB'] / 1024)} recommended), "
        f"{_number(cpus)} CPU{'' if cpus == 1 else 's'}"
    )
    if card["model"]["state"] != "ready" and need.get("downloadBytes"):
        resources = f"{_number(need['downloadBytes'] / GIB, 2)} GiB download, " + resources
    return facts + [resources]


def option_label(card: dict, panel: dict) -> str:
    switch = panel.get("switch") or {}
    reasons = card.get("reasons") or []
    nemotron = card["model"]["state"] != "bundled"
    tail = []
    if _busy(panel) and card["id"] == switch.get("target"):
        return _cut(
            f"{card['name']} — "
            + (
                _step(switch) if switch["state"] == "downloading" else "switching: " + _step(switch)
            ),
            MAX_LABEL,
        )
    if _busy(panel) and card["id"] == switch.get("previous"):
        if switch["state"] == "rolling_back":
            state = "being restored; captions are off until then"
        elif switch["state"] in CAPTIONS_OFF:
            state = "captions stopped during the change"
        else:
            state = "active until the switch finishes"
        if switch["state"] != "rolling_back":
            state += " (select to cancel)"
        return _cut(f"{card['name']} — {state}", MAX_LABEL)
    if card.get("active"):
        state, facts = "active", _facts(card)
    elif not card.get("eligible") and not card.get("advisory"):
        more = f" (+{len(reasons) - 1} more)" if len(reasons) > 1 else ""
        first = reasons[0]["message"] if reasons else "requirements not met"
        state, facts = f"unavailable: {first}{more}", []
    else:
        state = "ready" if card["model"]["state"] == "ready" else "available"
        facts = _facts(card)
        license = card.get("license") or {}
        if license.get("requiresAcknowledgement") and not _accepted(panel, card):
            tail.append("accept license first")
    if card.get("advisory") and reasons:
        tail.append("warning: " + reasons[0]["message"])
    if nemotron and (card.get("requirements") or {}).get("provisional"):
        tail.append("CPU estimate")
    return _cut(" · ".join([f"{card['name']} — {state}", *facts, *tail]), MAX_LABEL)


def _result(panel):
    switch = panel.get("switch") or {}
    state, parts = switch.get("state"), []
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
    refusal = panel.get("lastRefusal")
    if refusal:
        parts.append(
            f"Your choice of {_name(panel, refusal.get('engine'))} was not applied: "
            + _sentence(refusal.get("message", ""))
        )
        parts += [_sentence(r["message"]) for r in (refusal.get("reasons") or [])[:3]]
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


def _capacity(status):
    check = status.get("capacityAssessment") or {}
    state, speakers = check.get("state", "not_tested"), check.get("testedSpeakers", 0)
    if state in {"passed", "limited"} and speakers:
        noun = "speaker" if speakers == 1 else "speakers"
        return f"Capacity check: {speakers} simultaneous {noun} passed."
    if state == "checking":
        return "Capacity check running."
    if state == "not_tested":
        return "Capacity check not run yet."
    if state == "insufficient":
        failed = [s for s in check.get("scenarios", ()) if not s.get("passed")]
        reasons = failed[-1].get("reasons", ()) if failed else ()
        why = ", ".join(r.replace("_", " ") for r in reasons)
        return "Capacity check did not pass even for one speaker" + (f" ({why})." if why else ".")
    if state == "interrupted":
        return "Capacity check was interrupted."
    return "Capacity check did not complete" + (
        f" ({check['error']})." if check.get("error") else "."
    )


def status_text(panel: dict, status: dict, notes: tuple[str, ...] = ()) -> str:
    switch = panel.get("switch") or {}
    parts = []
    if _busy(panel):
        parts.append("Reload this page to see progress.")
    parts.append(f"Active engine: {_name(panel, panel['active'])}.")
    if _busy(panel) and switch.get("target"):
        parts.append(f"Changing to {_name(panel, switch['target'])}: {_step(switch)}.")
        if switch["state"] in CAPTIONS_OFF:
            parts.append("Captions are off until the change finishes.")
    parts += _result(panel)
    # Notes tell the admin what to do, so they come before anything the length cap may cut.
    parts += [_sentence(note) for note in notes]
    parts += _captions(status)
    parts.append(_capacity(status))
    shown = (panel["active"], switch.get("target") if _busy(panel) else None)
    for engine_id in dict.fromkeys(e for e in shown if e and e != "whistle"):
        if engine_id in catalog.ENGINES:
            license = catalog.get(engine_id).license
            parts.append(f"{_name(panel, engine_id)} is licensed under {license.name}: "
                         f"{license.url}. {_sentence(license.notice or '')}")
    if panel.get("measurements") == "provisional":
        parts.append("Requirements are provisional estimates.")
    return _cut(" ".join(p for p in parts if p), MAX_TEXT)


def _engine_field(panel):
    switch = panel.get("switch") or {}
    cards = {card["id"]: card for card in panel.get("engines", ())}
    options = {option_label(cards[e], panel): e for e in catalog.ENGINES if e in cards}
    description = (
        "One engine serves the whole server. Changing it ends captions in active calls for a "
        "few minutes; participants turn captions on again afterwards."
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


def _license_field(panel):
    license = catalog.get("nemotron-en").license
    card = next((c for c in panel.get("engines", ()) if c["id"] == "nemotron-en"), {})
    accepted = _accepted(panel, card)
    description = (
        ("Accepted. " if accepted else "")
        + "Required before switching to Nemotron English. "
        + " ".join(license.summary)
        + f" Full text: {license.url}"
    )
    return SettingsField(
        id=LICENSE_FIELD,
        title="NVIDIA model license",
        type=SettingsFieldType.CHECKBOX,
        default=False,
        label="I have read and accept the NVIDIA Open Model License for this server.",
        description=description,
    )


def _accepted(panel, card):
    """Saved with a switch, or ticked in the form and not yet used by one."""
    license = card.get("license") or {}
    return bool(license.get("acknowledged") or (
        card.get("id") == "nemotron-en" and panel.get("licenseBoxTicked")
    ))


def _removable_bytes(card):
    model = card["model"]
    if model["state"] == "partial":
        return model.get("bytesDone", 0)
    asset = catalog.get(card["id"]).asset
    return asset.extracted_bytes if asset else 0


def _cleanup_field(panel):
    switch = panel.get("switch") or {}
    keep = {panel["active"], switch.get("target") if _busy(panel) else None}
    removable = [
        c
        for c in panel.get("engines", ())
        if c["id"] not in keep and c["model"]["state"] in REMOVABLE
    ]
    if not removable:
        return None
    size = sum(_removable_bytes(c) for c in removable) / GIB
    return SettingsField(
        id=CLEANUP_FIELD,
        title="Downloaded models",
        type=SettingsFieldType.CHECKBOX,
        default=False,
        label=f"Remove downloaded models that are not in use (frees {size:.1f} GiB)",
    )


def build_form(panel: dict, status: dict, notes: tuple[str, ...] = ()) -> SettingsForm:
    fields = [_engine_field(panel), _license_field(panel), _cleanup_field(panel)]
    return SettingsForm(
        id=FORM_ID,
        section_id=SECTION_ID,
        section_type=SECTION_TYPE,
        title="Cassini Live",
        description=status_text(panel, status, notes),
        doc_url=DOC_URL,
        priority=50,
        fields=[f for f in fields if f is not None],
    )


def _flag(value):
    return value is True or (isinstance(value, str) and value.strip().lower() in TRUE_VALUES)


def parse_values(records: Iterable) -> Selection:
    values = {}
    for record in records:
        key, value = (record.key, record.value) if hasattr(record, "key") else record
        values[key] = value
    engine = values.get(ENGINE_FIELD)
    engine = engine.strip() if isinstance(engine, str) else None
    return Selection(
        engine=engine or None,
        accept_license=_flag(values.get(LICENSE_FIELD)),
        cleanup=_flag(values.get(CLEANUP_FIELD)),
    )
