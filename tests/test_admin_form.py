import copy
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from nc_py_api.ex_app.ui.settings import SettingsForm

from cassini_live import admin_form, catalog
from cassini_live.admin_form import (
    ENGINE_FIELD,
    MAX_LABEL,
    MAX_TEXT,
    PAGE_HINT,
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
    assert data["doc_url"] == admin_form.PAGE_PATH == admin_form.DEFAULT_PAGE_URL
    assert [f["id"] for f in data["fields"]] == [ENGINE_FIELD]
    assert [f["type"] for f in data["fields"]] == ["radio"]
    assert SettingsForm.from_dict(data).to_dict() == data
    values = [o["value"] for o in data["fields"][0]["options"]]
    assert values == list(catalog.ENGINES)
    assert data["description"].endswith(PAGE_HINT)
    assert data["fields"][0]["description"] == (
        "One engine serves the whole server. Changing it stops captions in active calls for a "
        "few minutes."
    )


def test_doc_url_is_the_page_url_argument():
    form = build_form(panel(), status(), page_url="/nc/index.php/apps/x/settings/index.html")
    assert form.to_dict()["doc_url"] == "/nc/index.php/apps/x/settings/index.html"


ADVISORY = [{"code": "cpu_quota", "message": "The CPU quota is too high."}]


@pytest.mark.parametrize(
    ("engine_id", "situation", "expected"),
    [
        ("whistle", {}, "Whistle — in use · 7 languages"),
        (
            "nemotron-en",
            {"cards": {"nemotron-en": card("nemotron-en", model={"state": "ready"})}},
            "Nemotron English — ready · English only",
        ),
        ("nemotron-en", {}, "Nemotron English — available · English only, 0.43 GiB download"),
        (
            "nemotron-multilingual",
            {},
            "Nemotron Multilingual — available · 7 languages, 0.44 GiB download",
        ),
        (
            "nemotron-multilingual",
            {"cards": {"nemotron-multilingual": card(
                "nemotron-multilingual", model={"state": "partial"}, advisory=True,
                reasons=ADVISORY,
            )}},
            "Nemotron Multilingual — available · 7 languages, 0.44 GiB download · warning",
        ),
        (
            "nemotron-en",
            {
                "cards": {
                    "nemotron-en": card(
                        "nemotron-en",
                        eligible=False,
                        reasons=[
                            {"code": "cpu_quota", "message": "The CPU quota is 0.5 CPUs."},
                            {"code": "memory_limit", "message": MEMORY_REASON},
                        ],
                    )
                }
            },
            "Nemotron English — unavailable: needs more CPU and more",
        ),
        (
            "nemotron-multilingual",
            {"cards": {"nemotron-multilingual": card(
                "nemotron-multilingual", eligible=False, reasons=[{
                    "code": "limits_missing",
                    "message": "Set CPU and memory limits and disable swap for the container "
                               "first.",
                }],
            )}},
            "Nemotron Multilingual — unavailable: container limits not set",
        ),
        (
            "nemotron-en",
            {"cards": {"nemotron-en": card("nemotron-en", eligible=False, reasons=[{
                "code": "cpu_instructions", "message": "This CPU lacks AVX2, so ...",
                "measured": None, "required": "avx2",
            }])}},
            "Nemotron English — unavailable: CPU lacks AVX2",
        ),
        (
            "whistle",
            {"switch": {"state": "validating", "target": "nemotron-en", "previous": "whistle"}},
            "Whistle — in use; checking the requirements",
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
            "Whistle — in use until the switch finishes (select to cancel)",
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
            "whistle",
            {"cards": {"whistle": card("whistle", advisory=True, reasons=ADVISORY)}},
            "Whistle — in use · 7 languages · warning",
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
    joined = " ".join(form_labels)
    assert not any(word in joined for word in ("word errors", "NVIDIA", "GiB memory"))


@pytest.mark.parametrize("code", sorted(admin_form.REASON_PHRASES))
def test_unavailable_labels_are_short_phrases_never_cut_sentences(code):
    reasons = [
        {"code": code, "message": MEMORY_REASON + " " + "x" * 300, "required": "avx2,fma"},
        {"code": "network", "message": "Cannot reach github.com to download the model."},
    ]
    for engine_id in ("nemotron-en", "nemotron-multilingual"):
        p = panel(cards={engine_id: card(engine_id, eligible=False, reasons=reasons)})
        label = option_label(next(c for c in p["engines"] if c["id"] == engine_id), p)
        assert MAX_LABEL == 90
        assert len(label) < MAX_LABEL and not label.endswith("…"), label
        assert "x" * 5 not in label and "container memory limit is" not in label
        assert label.endswith(" and more")


def test_unknown_reason_points_to_the_page():
    reasons = [{"code": "something_new", "message": "A long sentence " * 20}]
    p = panel(cards={"nemotron-en": card("nemotron-en", eligible=False, reasons=reasons)})
    label = option_label(p["engines"][1], p)
    assert label == "Nemotron English — unavailable: requirements not met (see ?)"


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
    assert first["fields"][0]["description"].endswith("2 calls have captions on now.")


LONG_FAILURE = {"state": "failed", "target": "nemotron-multilingual", "previous": "nemotron-en",
                "rolledBack": True, "error": {"message": "Capacity check failed " + "x" * 300}}
LONG_REFUSAL = {"code": "not_eligible", "engine": "nemotron-en",
                "message": "Nemotron English cannot run here.",
                "reasons": [{"message": "Reason one " + "y" * 60}, {"message": "Reason two."}]}


def test_refusal_comes_first_and_survives_a_long_status():
    p = panel("nemotron-multilingual", switch=LONG_FAILURE, lastRefusal=LONG_REFUSAL)
    notes = ("A change to Whistle is in progress. " + "n" * 200,)
    text = status_text(p, status("paused", reason="Memory " + "z" * 150), notes)
    assert text.startswith(
        "Not applied: Nemotron English. Nemotron English cannot run here. Reason one "
    )
    assert "Reason two" not in text  # only the first reason
    assert len(text) <= MAX_TEXT and text.endswith(" " + PAGE_HINT)
    assert "…" in text


def test_page_hint_is_never_cut():
    notes = tuple("A long note " + "x" * 200 for _ in range(10))
    text = status_text(panel(), status(), notes)
    assert len(text) == MAX_TEXT == 400
    assert text.endswith("… " + PAGE_HINT)
    assert PAGE_HINT == "Full settings and progress: click ? next to the title."


def test_refused_choice_is_visible_in_the_form_description():
    refusal = {"code": "not_eligible", "engine": "nemotron-en", "at": 1,
               "message": "Nemotron English cannot run here.",
               "reasons": [{"code": "memory_limit", "message": MEMORY_REASON}]}
    description = build_form(panel(lastRefusal=refusal), status()).to_dict()["description"]
    assert description == (
        "Not applied: Nemotron English. Nemotron English cannot run here. "
        + MEMORY_REASON + " Using Whistle. Captions are ready. " + PAGE_HINT
    )


@pytest.mark.parametrize(
    ("p", "s", "notes", "expected"),
    [
        (panel(), status(), (), "Using Whistle. Captions are ready."),
        (
            panel(),
            status("blocked", reason="Container memory limit is missing", adminActionRequired=True),
            (),
            "Using Whistle. Captions are paused: Container memory limit is missing. "
            "Administrator action required.",
        ),
        (
            panel(
                "nemotron-en",
                switch={"state": "succeeded", "target": "nemotron-en", "previous": "whistle"},
            ),
            status(),
            (),
            "Now using Nemotron English. Captions are ready.",
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
            "Could not switch to Nemotron English: Capacity check did not establish a safe "
            "speaking budget. Rolled back to Whistle. Captions are ready.",
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
            "Could not switch to Nemotron English: Load failed. Captions will recover "
            "automatically. Captions are ready.",
        ),
        (
            panel(switch={"state": "cancelled", "target": "nemotron-en", "previous": "whistle"}),
            status(),
            (),
            "The change to Nemotron English was cancelled. Captions are ready.",
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
            "Not applied: Nemotron English. Nemotron English cannot run here. Reason a. "
            "Using Whistle. Captions are ready.",
        ),
        (
            panel(lastError={"code": "restore", "message": "The model files were corrupt"}),
            status(),
            (),
            "Using Whistle. The model files were corrupt. Captions are ready.",
        ),
        (
            panel(switch=downloading(452)),
            status(),
            ("A change to Nemotron English is in progress. Wait for it to finish.",),
            "A change to Nemotron English is in progress. Wait for it to finish. "
            "Changing to Nemotron English: downloading 45%. Captions are ready.",
        ),
        (
            panel(switch={"state": "checking", "target": "nemotron-en", "previous": "whistle"}),
            status("checking", reason="Running the capacity check"),
            (),
            "Changing to Nemotron English: running the capacity check. Captions are off until "
            "it finishes. Captions are paused: Running the capacity check.",
        ),
    ],
)
def test_status_text(p, s, notes, expected):
    text = status_text(p, s, notes)
    assert text == expected + " " + PAGE_HINT
    assert len(text) <= MAX_TEXT
    for gone in ("Reload this page", "Capacity check:", "licensed under", "provisional"):
        assert gone not in text


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
    assert parse_values([]) == admin_form.Selection(None)
    record = SimpleNamespace(key=ENGINE_FIELD, value=" nemotron-en ")
    legacy = [("nvidia_oml_2025_10_24_accepted", "1"), ("remove_unused_models", "1")]
    selection = parse_values([record, ("unknown", "x"), *legacy])
    assert selection == admin_form.Selection("nemotron-en")
    assert parse_values([(ENGINE_FIELD, "")]).engine is None
    assert not hasattr(admin_form, "LICENSE_FIELD") and not hasattr(admin_form, "CLEANUP_FIELD")


def test_top_menu_page_is_gone():
    # The settings page is a standalone document served by the ExApp, not a top-menu page.
    static = ROOT / "cassini_live" / "static"
    assert not (static / "live.js").exists() and not (static / "live.css").exists()
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
