import copy
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from nc_py_api.ex_app.ui.settings import SettingsForm

from cassini_live import admin_form, catalog
from cassini_live.admin_form import (
    CLEANUP_FIELD,
    ENGINE_FIELD,
    LICENSE_FIELD,
    MAX_LABEL,
    MAX_TEXT,
    build_form,
    option_label,
    parse_values,
    progress_percent,
    status_text,
)

ROOT = Path(__file__).resolve().parents[1]
MEMORY_REASON = (
    "The container memory limit is 2.0 GiB. Nemotron English needs at least 2.3 GiB "
    "(3 GiB recommended)."
)


def card(engine_id, **changes):
    engine = catalog.get(engine_id)
    req = engine.requirements
    lic = engine.license
    result = {
        "id": engine_id,
        "name": engine.name,
        "active": engine_id == "whistle",
        "summary": engine.summary,
        "accuracy": {"meetingWer": engine.meeting_wer, "note": engine.accuracy_note},
        "languages": list(engine.languages),
        "automaticLanguage": engine.automatic_language,
        "requirements": {
            "memoryLimitMiB": req.min_memory_mib,
            "recommendedMemoryLimitMiB": req.recommended_memory_mib,
            "hostMemoryMiB": 5120,
            "cpuQuota": req.min_cpu_quota,
            "downloadBytes": engine.asset.archive_bytes if engine.asset else 0,
            "diskBytes": catalog.download_disk_bytes(engine_id),
            "cpuFlags": list(req.cpu_flags),
            "provisional": req.provisional,
        },
        "license": {
            "id": lic.id,
            "name": lic.name,
            "url": lic.url,
            "version": lic.version,
            "requiresAcknowledgement": lic.requires_acknowledgement,
            "acknowledged": False,
            "notice": lic.notice,
            "summary": list(lic.summary),
        },
        "model": {
            "state": "bundled" if engine.asset is None else "absent",
            "bytesDone": 0,
            "bytesTotal": 0,
            "error": None,
        },
        "eligible": True,
        "advisory": False,
        "reasons": [],
    }
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = {**result[key], **value}
        else:
            result[key] = value
    return result


def panel(active="whistle", switch=None, cards=None, **changes):
    cards = cards or {}
    engines = [cards.get(e) or card(e, active=e == active) for e in catalog.ENGINES]
    result = {
        "active": active,
        "activeCalls": 0,
        "measurements": "provisional",
        "switch": {
            "state": "idle",
            "target": None,
            "previous": None,
            "startedAt": None,
            "updatedAt": None,
            "message": "",
            "progress": None,
            "error": None,
            "rolledBack": False,
            **(switch or {}),
        },
        "lastError": None,
        "lastRefusal": None,
        "host": {
            "cpuQuota": 4.0,
            "affinityCpus": 8,
            "memoryLimitBytes": 3 << 30,
            "hostMemoryBytes": 8 << 30,
            "freeDiskBytes": 40 << 30,
            "arch": "x86_64",
            "cpuFlags": ["avx2"],
        },
        "engines": engines,
    }
    result.update(changes)
    return result


def status(state="ready", capacity=None, **safety):
    return {
        "resourceSafety": {
            "state": state,
            "reason": safety.pop("reason", "Ready"),
            "adminActionRequired": False,
            **safety,
        },
        "capacityAssessment": capacity or {"state": "passed", "testedSpeakers": 2},
    }


def downloading(percent_bytes):
    return {
        "state": "downloading",
        "target": "nemotron-en",
        "previous": "whistle",
        "message": f"Downloading ({percent_bytes} bytes)",
        "progress": {"phase": "downloading", "bytesDone": percent_bytes, "bytesTotal": 1000},
    }


def labels(form):
    return [option["name"] for option in form.to_dict()["fields"][0]["options"]]


def field(form, field_id):
    return next((f for f in form.fields if f.id == field_id), None)


def test_form_shape_round_trips():
    removable = card("nemotron-multilingual", model={"state": "ready"})
    form = build_form(panel(cards={"nemotron-multilingual": removable}), status())
    data = form.to_dict()
    assert data["id"] == "cassini_live_engine"
    assert (data["section_id"], data["section_type"]) == ("ai", "admin")
    assert data["title"] == "Cassini Live"
    assert data["doc_url"].endswith("docs/install.md#choose-a-speech-engine")
    assert [f["id"] for f in data["fields"]] == [ENGINE_FIELD, LICENSE_FIELD, CLEANUP_FIELD]
    assert [f["type"] for f in data["fields"]] == ["radio", "checkbox", "checkbox"]
    assert SettingsForm.from_dict(data).to_dict() == data
    values = [o["value"] for o in data["fields"][0]["options"]]
    assert values == list(catalog.ENGINES)
    assert LICENSE_FIELD == "nvidia_oml_2025_10_24_accepted"


@pytest.mark.parametrize(
    ("engine_id", "situation", "expected"),
    [
        ("whistle", {}, "Whistle — active · 7 languages, auto-detect · bundled, light"),
        (
            "nemotron-en",
            {"cards": {"nemotron-en": card("nemotron-en", model={"state": "ready"})}},
            "Nemotron English — ready · English only · 8.9% word errors on our meetings · "
            "NVIDIA-OML · needs 2.3 GiB memory (3 recommended), 1 CPU · "
            "accept license first · CPU estimate",
        ),
        (
            "nemotron-multilingual",
            {},
            "Nemotron Multilingual — available · 7 languages, auto-detect · "
            "12.8% word errors on our meetings · OpenMDW-1.1 · 0.44 GiB download, "
            "needs 2.3 GiB memory (3 recommended), 1 CPU · CPU estimate",
        ),
        (
            "nemotron-en",
            {
                "cards": {
                    "nemotron-en": card(
                        "nemotron-en",
                        eligible=False,
                        reasons=[
                            {"code": "memory_limit", "message": MEMORY_REASON},
                            {"code": "cpu_quota", "message": "The CPU quota is 0.5 CPUs."},
                        ],
                    )
                }
            },
            f"Nemotron English — unavailable: {MEMORY_REASON} (+1 more) · CPU estimate",
        ),
        ("nemotron-en", {"switch": downloading(452)}, "Nemotron English — downloading 45%"),
        (
            "nemotron-en",
            {"switch": {"state": "checking", "target": "nemotron-en", "previous": "whistle"}},
            "Nemotron English — switching: running the capacity check",
        ),
        (
            "nemotron-en",
            {"switch": {"state": "loading", "target": "nemotron-en", "previous": "whistle"}},
            "Nemotron English — switching: loading the engine",
        ),
        (
            "whistle",
            {"switch": downloading(10)},
            "Whistle — active until the switch finishes (select to cancel)",
        ),
        (
            "whistle",
            {"switch": {"state": "rolling_back", "target": "nemotron-en", "previous": "whistle"}},
            "Whistle — being restored; captions are off until then",
        ),
        (
            "whistle",
            {"switch": {"state": "checking", "target": "nemotron-en", "previous": "whistle"}},
            "Whistle — captions stopped during the change (select to cancel)",
        ),
        (
            "nemotron-en",
            {
                "cards": {
                    "nemotron-en": card(
                        "nemotron-en",
                        license={"acknowledged": True},
                        model={"state": "ready"},
                    )
                }
            },
            "Nemotron English — ready · English only · 8.9% word errors on our meetings · "
            "NVIDIA-OML · needs 2.3 GiB memory (3 recommended), 1 CPU · CPU estimate",
        ),
        (
            "whistle",
            {
                "cards": {
                    "whistle": card(
                        "whistle",
                        advisory=True,
                        reasons=[{"code": "cpu_quota", "message": "The CPU quota is too high."}],
                    )
                }
            },
            "Whistle — active · 7 languages, auto-detect · bundled, light · "
            "warning: The CPU quota is too high.",
        ),
    ],
)
def test_option_labels(engine_id, situation, expected):
    p = panel(**situation)
    target = next(c for c in p["engines"] if c["id"] == engine_id)
    assert option_label(target, p) == expected
    form_labels = labels(build_form(p, status()))
    assert expected in form_labels
    assert len(set(form_labels)) == 3
    assert all(len(label) <= MAX_LABEL for label in form_labels)


def test_long_ineligible_label_is_truncated_and_keeps_reason_start():
    reasons = [{"code": "memory_limit", "message": MEMORY_REASON + " " + "x" * 300}]
    p = panel(cards={"nemotron-en": card("nemotron-en", eligible=False, reasons=reasons)})
    label = option_label(p["engines"][1], p)
    assert len(label) == MAX_LABEL and label.endswith("…")
    assert label.startswith("Nemotron English — unavailable: " + MEMORY_REASON[:40])


def test_radio_default_is_target_while_busy():
    field_default = lambda p: build_form(p, status()).fields[0].default  # noqa: E731
    assert field_default(panel()) == "whistle"
    assert field_default(panel(active="nemotron-en")) == "nemotron-en"
    assert field_default(panel(switch=downloading(1))) == "nemotron-en"
    failed = {"state": "failed", "target": "nemotron-en", "previous": "whistle"}
    assert field_default(panel(switch=failed)) == "whistle"


def test_progress_changes_labels_only_at_five_percent_steps():
    assert progress_percent(None) is None
    assert progress_percent({"bytesDone": 5, "bytesTotal": 0}) is None
    seen = []
    for done in range(0, 1001):
        seen.append(labels(build_form(panel(switch=downloading(done)), status()))[1])
    changes = [i for i in range(1, len(seen)) if seen[i] != seen[i - 1]]
    assert all(i % 50 == 0 for i in changes)
    assert len(changes) == 20
    assert seen[-1] == "Nemotron English — downloading 100%"


def test_build_is_deterministic():
    p = panel(switch=downloading(333), activeCalls=2)
    first = build_form(p, status(), ("A note.",)).to_dict()
    assert build_form(copy.deepcopy(p), status(), ("A note.",)).to_dict() == first
    assert "333" not in str(first)
    assert "2 calls have captions on now." in first["fields"][0]["description"]


def test_cleanup_field_only_when_something_is_removable():
    assert field(build_form(panel(), status()), CLEANUP_FIELD) is None
    partial = card("nemotron-en", model={"state": "partial", "bytesDone": 3 << 29})
    form = build_form(panel(cards={"nemotron-en": partial}), status())
    assert "frees 1.5 GiB" in field(form, CLEANUP_FIELD).label
    active = card("nemotron-en", active=True, model={"state": "ready"})
    assert (
        field(
            build_form(panel("nemotron-en", cards={"nemotron-en": active}), status()), CLEANUP_FIELD
        )
        is None
    )
    target = card("nemotron-en", model={"state": "partial", "bytesDone": 10})
    busy = panel(switch=downloading(10), cards={"nemotron-en": target})
    assert field(build_form(busy, status()), CLEANUP_FIELD) is None


def test_license_field_reports_acceptance():
    plain = field(build_form(panel(), status()), LICENSE_FIELD)
    assert plain.default is False
    assert plain.description.startswith("Required before switching to Nemotron English. ")
    assert catalog.NVIDIA_OML.url in plain.description
    accepted = card("nemotron-en", license={"acknowledged": True})
    form = build_form(panel(cards={"nemotron-en": accepted}), status())
    assert field(form, LICENSE_FIELD).description.startswith("Accepted. ")
    ticked = build_form(panel(licenseBoxTicked=True), status())
    assert field(ticked, LICENSE_FIELD).description.startswith("Accepted. ")
    english = next(c for c in panel()["engines"] if c["id"] == "nemotron-en")
    assert "accept license first" in option_label(english, panel())
    assert "accept license first" not in option_label(
        english, panel(licenseBoxTicked=True)
    )


def test_action_notes_survive_a_long_status():
    failed = {"state": "failed", "target": "nemotron-multilingual", "previous": "nemotron-en",
              "rolledBack": True, "error": {"message": "Capacity check failed " + "x" * 300}}
    refusal = {"engine": "nemotron-en", "message": "Not eligible",
               "reasons": [{"message": "Reason " + "y" * 120}] * 3}
    p = panel("nemotron-multilingual", switch=failed, lastRefusal=refusal)
    text = status_text(p, status("paused", reason="Memory " + "z" * 150), ("Tick the box.",))
    assert "Tick the box." in text and len(text) <= MAX_TEXT


@pytest.mark.parametrize(
    ("p", "s", "notes", "expected"),
    [
        (
            panel(),
            status(),
            (),
            [
                "Active engine: Whistle.",
                "Captions are ready.",
                "Capacity check: 2 simultaneous speakers passed.",
                "Requirements are provisional estimates.",
            ],
        ),
        (
            panel(),
            status("blocked", reason="Container memory limit is missing", adminActionRequired=True),
            (),
            [
                "Captions are paused: Container memory limit is missing.",
                "Administrator action required.",
            ],
        ),
        (
            panel(),
            status(
                capacity={
                    "state": "insufficient",
                    "testedSpeakers": 0,
                    "scenarios": [{"passed": False, "reasons": ["caption_delay"]}],
                }
            ),
            (),
            ["Capacity check did not pass even for one speaker (caption delay)."],
        ),
        (
            panel(),
            status(capacity={"state": "not_tested", "testedSpeakers": 0}),
            (),
            ["Capacity check not run yet."],
        ),
        (
            panel(),
            status(capacity={"state": "limited", "testedSpeakers": 1}),
            (),
            ["Capacity check: 1 simultaneous speaker passed."],
        ),
        (
            panel(
                "nemotron-en",
                switch={"state": "succeeded", "target": "nemotron-en", "previous": "whistle"},
            ),
            status(),
            (),
            ["Active engine: Nemotron English.", "Now using Nemotron English."],
        ),
        (
            panel(
                switch={
                    "state": "failed",
                    "target": "nemotron-en",
                    "previous": "whistle",
                    "rolledBack": True,
                    "error": {
                        "code": "capacity_failed",
                        "message": "Capacity check did not establish a safe speaking budget",
                    },
                }
            ),
            status(),
            (),
            [
                "Could not switch to Nemotron English: Capacity check did not establish a safe "
                "speaking budget. Rolled back to Whistle."
            ],
        ),
        (
            panel(
                switch={
                    "state": "failed",
                    "target": "nemotron-en",
                    "previous": "whistle",
                    "error": {"code": "rollback_failed", "message": "Load failed."},
                }
            ),
            status(),
            (),
            [
                "Could not switch to Nemotron English: Load failed. Captions will recover "
                "automatically."
            ],
        ),
        (
            panel(switch={"state": "cancelled", "target": "nemotron-en", "previous": "whistle"}),
            status(),
            (),
            ["The change to Nemotron English was cancelled."],
        ),
        (
            panel(
                lastRefusal={
                    "code": "not_eligible",
                    "engine": "nemotron-en",
                    "at": 1,
                    "message": "Nemotron English cannot run here.",
                    "reasons": [{"code": c, "message": f"Reason {c}."} for c in "abcd"],
                }
            ),
            status(),
            (),
            [
                "Your choice of Nemotron English was not applied: Nemotron English cannot run "
                "here. Reason a. Reason b. Reason c."
            ],
        ),
        (
            panel(lastError={"code": "restore", "message": "The model files were corrupt"}),
            status(),
            (),
            ["The model files were corrupt."],
        ),
        (
            panel(),
            status(),
            ("Tick the NVIDIA Open Model License box to continue.",),
            ["Tick the NVIDIA Open Model License box to continue."],
        ),
        (
            panel(switch=downloading(452)),
            status(),
            (),
            [
                "Reload this page to see progress. Active engine: Whistle. Changing to Nemotron "
                "English: downloading 45%."
            ],
        ),
        (
            panel("nemotron-multilingual"),
            status(),
            (),
            [
                "Nemotron Multilingual is licensed under OpenMDW License 1.1: "
                "https://openmdw.ai/license/1-1/. "
                "nvidia/nemotron-3.5-asr-streaming-0.6b by NVIDIA Corporation"
            ],
        ),
        (
            panel("nemotron-en"),
            status(),
            (),
            [
                "Nemotron English is licensed under NVIDIA Open Model License: ",
                "Licensed by NVIDIA Corporation under the NVIDIA Open Model License.",
            ],
        ),
        (
            panel(switch={"state": "checking", "target": "nemotron-en", "previous": "whistle"}),
            status(),
            (),
            ["Captions are off until the change finishes."],
        ),
    ],
)
def test_status_text(p, s, notes, expected):
    text = status_text(p, s, notes)
    for part in expected:
        assert part in text
    assert len(text) <= MAX_TEXT
    assert "Powered by NVIDIA" not in text
    assert "Reason d." not in text
    if p["switch"]["state"] not in admin_form.ACTIVE_STATES:
        assert "Reload this page" not in text


def test_status_text_is_bounded():
    notes = tuple("A long note " + "x" * 200 for _ in range(10))
    text = status_text(panel(), status(), notes)
    assert len(text) == MAX_TEXT and text.endswith("…")


def test_status_text_skips_restore_error_repeated_by_failed_switch():
    error = {"code": "capacity_failed", "message": "No safe budget"}
    p = panel(
        switch={
            "state": "failed",
            "target": "nemotron-en",
            "previous": "whistle",
            "rolledBack": True,
            "error": error,
        },
        lastError=error,
    )
    assert status_text(p, status()).count("No safe budget") == 1


def test_parse_values():
    assert parse_values([]) == admin_form.Selection(None, False, False)
    record = SimpleNamespace(key=ENGINE_FIELD, value=" nemotron-en ")
    selection = parse_values([record, ("unknown", "x"), (LICENSE_FIELD, "1")])
    assert selection == admin_form.Selection("nemotron-en", True, False)
    assert parse_values([(ENGINE_FIELD, "")]).engine is None
    for value in ("1", "true", "TRUE", "on", "yes", True):
        assert parse_values([(LICENSE_FIELD, value), (CLEANUP_FIELD, value)]) == (
            admin_form.Selection(None, True, True)
        )
    for value in ("0", "", "false", False, None):
        assert parse_values([(LICENSE_FIELD, value)]).accept_license is False


def test_top_menu_page_is_gone():
    assert not (ROOT / "cassini_live" / "static").exists()
    assert (ROOT / "img" / "app.svg").exists()
    pattern = re.compile(r"live\.js|live\.css|top-menu|top menu", re.IGNORECASE)
    proposal = ROOT / "docs" / "proposals" / "engine-selection.md"
    files = [
        p
        for base in ("cassini_live", "docs")
        for p in (ROOT / base).rglob("*")
        if p.is_file() and p != proposal and p.suffix in {".py", ".md", ".js", ".css", ".xml"}
    ]
    offenders = [str(p.relative_to(ROOT)) for p in files if pattern.search(p.read_text())]
    assert offenders == []


def test_available_labels_fit_without_truncation():
    p = panel()
    for card_ in p["engines"]:
        label = option_label(card_, p)
        assert len(label) < MAX_LABEL and not label.endswith("…"), label
