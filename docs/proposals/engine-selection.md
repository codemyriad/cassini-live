# Proposal: administrator-selected speech engine

> **History.** This is the 0.7.x design. Its automatic capacity check, the
> trip-and-cool-down state machine and the tested-speaker room cap were replaced
> in 0.8.0 by [instant admission](0.8.0-instant-admission.md).

> 2026-10-15 (0.7.1): the license acceptance step was removed; licenses are shown but
> not accepted. The engine choice gained an admin-only page served by the ExApp (see
> docs/install.md). Sections mentioning `license_required`, `LICENSE_FIELD`,
> `LICENSE_GRACE_S` and acknowledgements describe 0.7.0.
> Also in 0.7.1: unavailable option labels use a short phrase per reason code instead of
> the first reason's sentence; "(select to cancel)" appears only in the states the page
> can cancel (not `validating`); `measurements` is `{"measuredOn", "estimates"}`; a
> refusal noted during a change is cleared when that change ends; a switch or model
> removal while a change runs is refused with 409 `busy` instead of waiting for it.

Status: architecture spec for four parallel implementers. No production code is in
this document's change. Branch `feat/engine-selection`, on top of the uncommitted
0.6.2 work. Keep that work.

Sources read: the whole `cassini_live` package, `tests/`, `appinfo/info.xml`,
`Dockerfile`, `pyproject.toml`, `NOTICE.md`, `docs/`, `local/stt-eval/adoption-plan.md`
(called "the plan" below) and `local/stt-eval/resources/baseline.md` ("the
baseline"). `local/stt-eval/resources/specs.md` and `results.md` did not exist when
this was written. Every number marked **(P)** is provisional. Section 3.3 says how to
replace it.

**Revision 2 (UI placement, decided by the user).** There is no top-menu entry and no
custom page any more. The engine choice lives in a Nextcloud **administration
settings** form that the ExApp declares through AppAPI declarative settings
(section 11). The admin HTTP API of revision 1 is gone: the ExApp watches the form's
stored value instead (section 8.4). Sections 0, 2, 7.3, 8, 9, 10, 11, 12.4, 12.5, 13,
14 and 15 changed. Sections 3 to 6 and 7.1 to 7.2 are unchanged, except that
"panel" now means the settings form. Files that implementers already created
(`catalog.py`, `eligibility.py`, `engine_switch.py`, `engines/`, `model_store.py`,
`settings.py`) keep their place and owner.

## 0. Decisions in one page

* One engine for the whole server: `whistle` (default, bundled), `nemotron-en` or
  `nemotron-multilingual`. An administrator chooses it in the "Cassini Live" form
  under Nextcloud's administration settings (section "Artificial intelligence",
  `/settings/admin/ai`), declared with AppAPI declarative settings. The top-menu
  page, its script and its static files are removed.
* Declarative forms cannot disable an option or update live. So the option
  **labels** carry eligibility and status, the ExApp **re-registers** the form when
  its status changes (debounced), and the ExApp **rejects** an invalid choice by
  writing the active engine back into the stored value and explaining why in the
  status text. A page reload shows the fresh state.
* The ExApp sees an administrator's choice by **polling** its own app-config value
  (`nc.appconfig_ex`) every 3 s. AppAPI sends no event to ExApps for declarative
  settings changes.
* The image stays Whistle-only. The Nemotron models are downloaded **on demand**
  into `APP_PERSISTENT_STORAGE/models/<engine>/`, pinned by the SHA-256 of each
  extracted file.
* An engine the hardware cannot run is shown **greyed out with a reason**. One
  catalog table (`cassini_live/catalog.py`) holds every threshold.
* Switching: re-check eligibility, require the license acknowledgment, download
  while the old engine keeps serving, stop captions like an overload pause, load the
  new engine, run the existing capacity check, and **roll back automatically** on
  any failure. The choice is persisted and restored at startup.
* Whistle keeps its exact code path: `WorkerPool`, `worker.py`,
  `WhistleTranscriber` with 2 s handoffs, and the same resource formulas.
* Nemotron runs in **one shared engine-host subprocess** with one sherpa-onnx
  `OnlineRecognizer` and one continuous stream per speaker. Admission is a
  speaking-slot count equal to the measured `testedSpeakers`. There is no 2 s
  preemption.
* The supervisor process never imports `sherpa_onnx`. Only `engine_host.py` and
  integration tests do.

## 1. Rules that bind every implementer

* Do not commit, push, or switch branches. Keep the uncommitted 0.6.2 changes.
* Baseline: `uv run --frozen pytest -m "not integration" -q` gives 119 passed. All
  119 must still pass. Only change an existing test when this spec says so.
* **Memory safety.** Any process that loads a Nemotron model (about 1 GB) runs only
  inside
  `systemd-run --user --scope -p MemoryMax=3G -p MemorySwapMax=0 <cmd>`. Never run
  more than two at once. Unit tests use fakes. Tests that load a real model carry the
  `integration` marker and skip themselves outside a capped cgroup (section 12.6).
* Do not touch `stt-*` systemd units. Only read `local/stt-eval`.
* Edit only the files you own (section 2). To affect someone else's file, use the
  hook points in this spec. If a hook is missing, report it. Do not edit the other
  owner's file.
* Match the local style: short modules, a docstring that says why, few comments,
  `ruff` (line length 100, rules E, F, I).
* **Step 0 for every owner:** before writing logic, create your new public modules
  with the exact signatures from this spec. Bodies may be `raise NotImplementedError`.
  Then other owners' imports resolve while you work.

## 2. Module layout and file ownership

| File | Owner | New / changed | Purpose |
|---|---|---|---|
| `cassini_live/engines/__init__.py` | A | new | `Backend` / `Transcriber` protocols, `create_backend()` |
| `cassini_live/engines/shared.py` | A | new | `SharedEngine`: supervisor-side client of the engine host, speaking slots |
| `cassini_live/engine_host.py` | A | new | Subprocess `python -m cassini_live.engine_host` (only importer of `sherpa_onnx`) |
| `cassini_live/transcriber.py` | A | changed | `create()` factory, `abort()`, new `StreamTranscriber` subclass |
| `cassini_live/pool.py` | A | changed (additive) | `WorkerPool` gains the `Backend` members |
| `cassini_live/calibration.py` | A | changed | Uses `pool.transcriber()` and `abort()` |
| `cassini_live/talk/client.py` | A | changed | Calls `transcriber.create()`; tab-indented, ruff-excluded |
| `cassini_live/diagnostics.py`, `diagnostic_video.py` | A | changed | `pool.busy`, engine id in samples and header |
| `cassini_live/worker.py` | A | unchanged | Whistle worker: do not edit |
| `cassini_live/catalog.py` | B | new | Engine catalog, requirement table, model pins, license data, formulas |
| `cassini_live/model_store.py` | B | new | Download, resume, cancel, verify, install, delete |
| `cassini_live/eligibility.py` | B | new | Host facts, network probe, `assess()` |
| `LICENSES/NVIDIA-Open-Model-License.txt`, `LICENSES/NVIDIA-Open-Model-License-NOTICE.txt`, `LICENSES/OpenMDW-1.1.txt`, `LICENSES/LGPL-2.1.txt` | B | new | Fetched once, stored verbatim |
| `NOTICE.md` | B | changed | sherpa-onnx, alsa-lib, both models, on-demand download |
| `cassini_live/settings.py` | C | new | Persisted engine selection (atomic JSON) |
| `cassini_live/engine_switch.py` | C | new | `EngineManager`: restore, switch, rollback, panel dict, change hook |
| `cassini_live/settings_sync.py` | C | new | `SettingsSync`: polls the form's stored values, drives the manager, re-registers the form (8.4) |
| `cassini_live/app.py` | C | changed | Removes the top-menu/script registration and the `ui/`, `img/` routes; per-engine languages, status, lifespan and `enabled_handler` wiring of the manager and `SettingsSync` |
| `cassini_live/service.py` | C | owner, no change expected | Room teardown already does what we need |
| `cassini_live/safety.py` | C | changed | `replace_pool`, `start(fresh)`, `wait_outcome`, engine-aware budgets |
| `cassini_live/resources.py`, `recovery.py`, `capacity.py`, `config.py` | C | changed | Rules parameterized by engine, Whistle numbers unchanged |
| `appinfo/info.xml`, `pyproject.toml`, `uv.lock`, `Dockerfile` | C | changed | Removes the `ui/` and `img/` routes, `sherpa-onnx==1.13.8`, environment |
| `scripts/check_manifest.py` | C | changed | Route assertions follow info.xml (8.1); drops the `static/app.svg` comparison |
| `cassini_live/admin_form.py` | D | new | Pure builder of the declarative settings form from the panel dict, and parser of its stored values (section 11) |
| `cassini_live/static/` (`live.js`, `live.css`, `app.svg`) | D | deleted | The top-menu page is gone. `img/app.svg` at the repository root stays: it is the app icon |
| `README.md`, `docs/install.md`, `docs/capacity.md`, `docs/architecture.md`, `docs/privacy.md`, `docs/diagnostic-video.md` | D | changed | Documentation (section 13), including where the settings live and removal of every top-menu page reference |
| `docs/proposals/engine-selection.md` | none | this file | Read-only for implementers |

Tests: each owner owns the new test files in section 12. These existing test files
may be edited only by the owner named here, and only additively unless section 12
says otherwise: `test_streaming.py`, `test_speech.py`, `test_scheduling.py`,
`test_pause_boundaries.py`, `test_whistle.py`, `test_worker_limits.py`,
`test_diagnostics.py`, `test_webrtc.py`, `test_transport.py` (A);
`test_resources.py`, `test_recovery.py`, `test_safety.py`, `test_capacity.py`,
`test_app.py`, `test_service.py` (C).

Dependency direction. There are no cycles:

```text
catalog (B)  <-  resources, recovery, config, safety, capacity (C)
catalog (B)  <-  model_store, eligibility (B)
catalog (B)  <-  engines, engine_host, transcriber (A)
catalog (B), nc_py_api.ex_app.ui.settings  <-  admin_form (D)
engines (A), model_store (B), eligibility (B), settings (C)  <-  engine_switch (C)
engine_switch (C), admin_form (D)  <-  settings_sync (C)  <-  app (C)
```

`admin_form.py` never imports `engine_switch`, `safety` or `app`. It works on plain
dicts (the panel dict of 8.3 and the `/status` body), so D can test it alone.

`catalog.py` imports only the standard library. Neither `resources.py` nor
`catalog.py` imports the other's private parts: `catalog` defines its own
`MIB = 1024 * 1024`.

## 3. Catalog (owner B): `cassini_live/catalog.py`

### 3.1 Types

```python
EngineId = Literal["whistle", "nemotron-en", "nemotron-multilingual"]
DEFAULT_ENGINE: EngineId = "whistle"
MIB = 1024 * 1024
MAX_STREAMS = 12          # safety.max_tracks (10) plus a margin of 2
MEASUREMENTS = "provisional"   # or "measured: <date> specs.md" once replaced

@dataclass(frozen=True)
class ModelFile:
    name: str             # basename inside the archive directory
    sha256: str
    size: int

@dataclass(frozen=True)
class ModelAsset:
    archive: str          # release asset file name
    url: str              # https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/<archive>
    archive_bytes: int    # exact Content-Length (pinned; a changed asset is refused)
    directory: str        # top-level directory inside the tar
    files: tuple[ModelFile, ...]

    @property
    def extracted_bytes(self) -> int: ...

@dataclass(frozen=True)
class License:
    id: str               # "Apache-2.0" | "NVIDIA-OML" | "OpenMDW-1.1"
    name: str
    url: str
    version: str          # an acknowledgment is bound to this value
    text_file: str        # path relative to the repository root, e.g. "LICENSES/OpenMDW-1.1.txt"
    notice: str | None    # exact notice text that must travel with the weights
    requires_acknowledgement: bool
    summary: tuple[str, ...]   # plain-English points shown in the panel

@dataclass(frozen=True)
class Requirements:
    min_memory_mib: int           # container memory.max floor (shared host; Whistle uses the formula)
    recommended_memory_mib: int
    min_cpu_quota: float          # cgroup CPU quota, in CPUs
    cpu_flags: tuple[str, ...]    # required /proc/cpuinfo flags on x86_64; ignored elsewhere
    base_mib: int                 # supervisor, aiortc, VAD, Python
    model_mib: int                # resident model, rounded up
    stream_mib: int               # per open stream
    batch_mib: int                # activation arena per stream in one decode batch
    max_batch: int
    cpu_per_speaker: float        # CPU-s per audio-s x 1.3 calibration speed x margin
    overhead_cpu: float           # VAD on 10 tracks, event loop
    start_timeout_s: float        # safety's pool.start() timeout
    provisional: bool

@dataclass(frozen=True)
class Engine:
    id: EngineId
    name: str                     # "Whistle" | "Nemotron English" | "Nemotron Multilingual"
    kind: Literal["worker_pool", "shared_host"]
    summary: str                  # one line for the card
    languages: tuple[str, ...]    # Cassini codes, a subset of config.LANGUAGE_NAMES
    automatic_language: bool      # False: "auto" is accepted but means languages[0]
    meeting_wer: float | None     # on our English standup windows
    accuracy_note: str
    asset: ModelAsset | None      # None: bundled in the image
    license: License
    requirements: Requirements

ENGINES: dict[str, Engine]

def get(engine_id: str) -> Engine: ...          # KeyError for unknown ids
def memory_floor_mib(engine_id: str, workers: int) -> int: ...
def worker_ceiling(engine_id: str, effective_cpus: float) -> int: ...
def load_headroom_mib(engine_id: str, workers: int) -> int: ...
def download_disk_bytes(engine_id: str, partial_bytes: int = 0) -> int: ...
```

### 3.2 Formulas

These functions are the only source of the numbers. Resources, recovery, config,
safety and eligibility all call them.

| Function | `whistle` (must equal today's code exactly) | shared host (`nemotron-*`) |
|---|---|---|
| `memory_floor_mib(e, W)` | `384 + 192 * W` | `requirements.min_memory_mib` (independent of W) |
| `worker_ceiling(e, cpus)` | `min(4, max(1, int(cpus // 2)))` | `min(4, max(1, floor((cpus - overhead_cpu) / cpu_per_speaker + 1e-9)))` |
| `load_headroom_mib(e, W)` | `192 * W` | `model_mib + stream_mib * W + batch_mib * min(W, max_batch)` |
| `download_disk_bytes(e, p)` | `0` | `archive_bytes - p + extracted_bytes + 256 MiB` |

The shared-host floor is derived as
`ceil((base + model + stream * MAX_STREAMS + batch * max_batch) / 0.7)`, rounded up
to the next 256 MiB. Store the result as a constant, with the derivation in a
comment. With the (P) values: `(384 + 1024 + 16*12 + 45*4) / 0.7 = 2543`, so
**2560 MiB**. The 0.7 keeps the steady state below the 80% trip with room for
spikes.

### 3.3 The table

Replace the (P) values with the measured ones if `local/stt-eval/resources/specs.md`
or `results.md` exists when you implement. Then set `provisional=False` and
`MEASUREMENTS` to `"measured: <date>"`. Otherwise keep these:

| Field | whistle | nemotron-en | nemotron-multilingual |
|---|---|---|---|
| name | Whistle | Nemotron English | Nemotron Multilingual |
| kind | worker_pool | shared_host | shared_host |
| languages | en de fr es it nl pl | en | en de fr es it nl pl |
| automatic_language | True | False ("auto" means en) | True (`auto` per stream) |
| meeting_wer | 0.19 | 0.089 | 0.128 |
| min_memory_mib | formula (576 at W=1) | 2560 (P) | 2560 (P) |
| recommended_memory_mib | 2048 (today's docs) | 3072 | 3072 |
| host RAM (derived, shown) | 2 x container limit (existing rule) | >= 5 GiB (P) | >= 5 GiB (P) |
| min_cpu_quota | 1.0 (budget: one worker per 2 CPUs) | 1.0 | 1.0 |
| cpu_flags (x86_64) | () | ("avx2",) (P) | ("avx2",) (P) |
| base_mib | 384 | 384 | 384 |
| model_mib | 192 (per worker) | 1024 (930 measured + 10%) | 1056 (960-967 measured + 10%) |
| stream_mib | 0 | 16 (P; about 7 measured serially) | 16 (P) |
| batch_mib / max_batch | 0 / 1 | 45 / 4 (baseline: batched arena) | 45 / 4 |
| cpu_per_speaker | 2.0 | 0.75 (0.43 RTF x 1.3 x margin) (P) | 0.75 (P) |
| overhead_cpu | 0 | 0.25 (P) | 0.25 (P) |
| start_timeout_s | 30 | 120 (cold disk; 3-5 s warm) | 120 |
| license | Apache-2.0 (confirm the weights license from `scripts/prepare_model.py`'s manifest) | NVIDIA-OML, version `2025-10-24`, ack required, notice `Licensed by NVIDIA Corporation under the NVIDIA Open Model License` | OpenMDW-1.1, version `1.1`, no ack, notice: origin line (see 3.4) |

Model assets. The archive sizes are from HEAD requests in the plan. The file hashes
were recomputed for this spec from `local/stt-eval/models/` and match the plan:

```text
nemotron-en
  archive   sherpa-onnx-nemotron-speech-streaming-en-0.6b-560ms-int8-2026-04-25.tar.bz2   463945051 B
  directory sherpa-onnx-nemotron-speech-streaming-en-0.6b-560ms-int8-2026-04-25
  7d932213491ad355c6e5576705dc3494731a52af87d7a1b954559340147909d8  encoder.int8.onnx  652916849
  0be9702c2f427a2b6bb241d298e0d3836a558de1f5b9fd3018f1cce6e2b3fa98  decoder.int8.onnx    7257753
  a35eac38a22ebceb04d230ed7afe0d68f446ba6914a036b97f14fece95967e23  joiner.int8.onnx     1735862
  dc0b4584ab2e4ddbf888425c076c61b736e7356a015250db7d307e6f1a8188ff  tokens.txt              8952
nemotron-multilingual
  archive   sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11.tar.bz2    475271763 B
  directory sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11
  012e9321373af99021415e0b0eb3ec827b4be3153be6f30d9b448fe65e896e68  encoder.int8.onnx  657601403
  19f9c98fc6d0a2c33a65a43b36fdb2e914c26c0aa9764be3aebc502a1e982fb0  decoder.int8.onnx   14978075
  4101c7c679a0bc30483794b27a059e34e79232aa2068d78d51231a22c8b0d7ce  joiner.int8.onnx     9504438
  729cc103155bafa785f9cd45746cd41cabe97eab7182fc04d594129587958f8a  tokens.txt            131440
```

The archive SHA-256 is **not** pinned. `asr-models` is a rolling tag, and we do not
download 1 GB just to learn it. The pinned `archive_bytes` and the four file hashes
are authoritative. The store records the observed archive hash in the model
manifest for provenance.

### 3.4 License summaries (settings form text, plain English)

* NVIDIA-OML (`nemotron-en`):
  * "Free for commercial and non-commercial use."
  * "NVIDIA can end the license. It also ends if you bypass the model's safety
    guardrails or sue over the model."
  * "Use must follow NVIDIA's Trustworthy AI terms and export rules."
  * "You agree to indemnify NVIDIA for claims arising from your use or
    distribution."
  * "Captions you produce are not claimed by NVIDIA."
  * "Not legal advice. Read the full text."
* OpenMDW-1.1 (`nemotron-multilingual`):
  * "Permissive: no field-of-use limits, no copyleft."
  * "Keep the license and origin notices with the model files."
  * "Rights end if you start patent or copyright litigation over the model."
  * Origin notice: `nvidia/nemotron-3.5-asr-streaming-0.6b by NVIDIA Corporation,
    exported to ONNX by the k2-fsa/sherpa-onnx project. Licensed under OpenMDW-1.1.`
* Apache-2.0 (Whistle): "Bundled with Cassini Live."

## 4. Model store (owner B): `cassini_live/model_store.py`

### 4.1 Interface

```python
ModelStateName = Literal["bundled", "absent", "partial", "downloading", "verifying",
                         "installing", "ready", "corrupt", "error"]

@dataclass
class Progress:                       # mutated by the worker thread; read by the event loop
    engine: str
    phase: Literal["downloading", "extracting", "verifying", "installing"]
    bytes_done: int = 0               # archive bytes on disk (download), or extracted bytes
    bytes_total: int = 0
    started_at: float = 0.0           # wall clock

@dataclass(frozen=True)
class ModelState:
    engine: str
    state: ModelStateName
    path: Path | None                 # final directory when ready
    partial_bytes: int = 0            # resumable bytes already on disk
    error: str | None = None          # human-readable, no URLs with tokens, no paths outside root

class ModelError(RuntimeError):
    code: str                         # "disk_space" | "network" | "size_mismatch" | "hash_mismatch"
                                      # | "archive_layout" | "cancelled" | "busy" | "storage"

class ModelStore:
    def __init__(self, root: Path | None, *, engines=catalog.ENGINES,
                 opener=urllib.request.urlopen, disk_usage=shutil.disk_usage,
                 clock=time.time, license_root: Path = REPO_ROOT): ...
    root: Path | None                 # None: persistent storage unavailable; downloads refused
    def model_dir(self, engine_id: str) -> Path       # root / engine_id
    def state(self, engine_id: str) -> ModelState     # cheap: manifest + sizes only, no hashing
    async def verify(self, engine_id: str, *, deep: bool = True) -> ModelState
    async def download(self, engine_id: str) -> Path  # returns the final directory; raises ModelError
    def progress(self, engine_id: str) -> Progress | None
    def cancel(self, engine_id: str, *, discard: bool = False) -> bool
    async def delete(self, engine_id: str) -> None    # removes final dir and partial; refuses during download
    @property
    def busy(self) -> str | None                       # engine id being downloaded, or None
```

`whistle` always reports `bundled`. `download("whistle")` is a no-op that returns the
directory of the configured weights.

### 4.2 Layout under `root = <state dir>/models`

```text
models/
  nemotron-en/                    final; replaced atomically, never edited in place
    encoder.int8.onnx decoder.int8.onnx joiner.int8.onnx tokens.txt
    LICENSE.txt NOTICE.txt        copied from LICENSES/ (catalog.License.text_file, .notice)
    manifest.json                 written last before the rename
  .partial/<archive>              resumable download
  .partial/<archive>.json         {"url","etag","lastModified","archiveBytes"}
  .staging-<engine>-<random>/     extraction target
  .old-<engine>-<random>/         previous version during a replace
```

`manifest.json`:

```json
{"engine": "nemotron-en", "archive": "<name>", "url": "<url>", "archiveBytes": 463945051,
 "archiveSha256Observed": "<hex>", "files": {"encoder.int8.onnx": {"sha256": "...", "size": 652916849}},
 "license": "NVIDIA-OML", "licenseVersion": "2025-10-24", "installedAt": 1760000000}
```

### 4.3 Algorithm

All blocking work runs in one worker thread (`asyncio.to_thread`). Cancellation uses a
`threading.Event` that the thread checks at least every 1 MiB. Only one download
runs at a time; a second call raises `ModelError("busy")`.

1. **Precheck.** `root` must be set and writable. `disk_usage(root).free >=
   catalog.download_disk_bytes(e, partial_bytes)`, or raise `disk_space` with a
   message that gives both numbers in GiB.
2. **Download** to `.partial/<archive>`. Use `urllib.request.Request(url, headers=
   {"User-Agent": "cassini-live/<version>"})`, a timeout of 30 s per read, and
   1 MiB reads. If a partial file exists and the sidecar has an ETag or
   Last-Modified, send `Range: bytes=<n>-` plus `If-Range`. Status 206 appends.
   Status 200 truncates and restarts. Anything else raises `network`. The total
   length must equal `archive_bytes`: check it from the Content-Length or
   Content-Range header before writing, and never write past it. Otherwise raise
   `size_mismatch` ("The upstream model archive changed; this version of Cassini
   Live cannot verify it"). Update `Progress.bytes_done` as you go. Honor
   `HTTPS_PROXY`, which urllib does by default. Redirects to
   `objects.githubusercontent.com` are expected.
3. **Extract** with bounded memory: open the completed partial file and call
   `tarfile.open(fileobj=f, mode="r|bz2")`, which streams sequentially. Hash the
   archive bytes while reading them. Never call `extract`/`extractall`. For each
   member:
   * accept it only if `member.isfile()` and `member.name == f"{directory}/{file}"`
     for a pinned file. Also reject a duplicate;
   * skip everything else (`test_wavs/`, `README.md`, directories). Count symlinks,
     hardlinks, devices, absolute names and `..` parts as layout errors only if
     they claim a pinned name;
   * `member.size` must equal the pinned size;
   * copy with `tar.extractfile(member)` in 1 MiB blocks to
     `.staging-…/<file>` opened with `O_CREAT|O_EXCL`, mode 0o640, while hashing.
     Fsync each file.
   * After the stream ends, all four files must be present with matching SHA-256.
     Otherwise raise `hash_mismatch` or `archive_layout`.
4. **Install.** Copy the license text and notice into the staging directory. Write
   `manifest.json` (temporary file, fsync, rename). Fsync the staging directory.
   If the final directory exists, rename it to `.old-…`. Then
   `os.replace(staging, final)`, fsync `root`, remove `.old-…`, and delete the
   partial file and its sidecar.
5. **Failure** at any step removes the staging directory and keeps the partial
   file. A hash or layout failure deletes the partial file too, so the next try
   downloads from scratch. **Cancel** keeps the partial file unless
   `discard=True`.
6. **Startup cleanup**: `ModelStore.__init__` removes leftover `.staging-*` and
   `.old-*` directories. It never touches final directories.

`verify(deep=True)` re-hashes every pinned file against the **catalog** (not the
manifest), in a thread. That is about 650 MB of sequential reads, a few seconds.
`state()` checks only that the manifest exists and the sizes match.

Path safety: every path is built from catalog constants under `root`. Member names
are compared, never joined. `root` is resolved once, and the store refuses to run if
`root` is a symlink.

## 5. Eligibility (owner B): `cassini_live/eligibility.py`

```python
@dataclass(frozen=True)
class Reason:
    code: Literal["limits_missing", "memory_limit", "host_memory", "cpu_quota",
                  "cpu_instructions", "disk_space", "network", "storage", "architecture"]
    message: str                       # one sentence, numbers in GiB / CPUs, what to change
    measured: float | int | str | None = None
    required: float | int | str | None = None

@dataclass(frozen=True)
class Eligibility:
    engine: str
    eligible: bool
    reasons: tuple[Reason, ...]        # empty when eligible; Whistle may carry advisory reasons
    advisory: bool = False             # True: reasons shown but selection allowed (Whistle only)

@dataclass(frozen=True)
class HostFacts:
    cpu_quota: float | None
    affinity_cpus: int
    effective_cpus: float
    memory_limit_bytes: int | None
    swap_limit_bytes: int | None
    host_memory_bytes: int | None
    free_disk_bytes: int | None        # on the model store root; None if storage is unavailable
    arch: str                          # platform.machine()
    cpu_flags: frozenset[str]          # from /proc/cpuinfo, cached for the process lifetime
    network: dict[str, Literal["reachable", "unreachable", "unknown"]]   # per engine id with an asset

def host_facts(*, reader=resources.snapshot, store_root: Path | None,
               probe: "NetworkProbe | None" = None, cpuinfo=Path("/proc/cpuinfo")) -> HostFacts: ...

def assess(engine: catalog.Engine, facts: HostFacts, model: ModelState) -> Eligibility: ...

class NetworkProbe:
    """HEAD the asset URL (redirects followed), 5 s timeout, results cached for 10 minutes."""
    def __init__(self, opener=urllib.request.urlopen, clock=time.monotonic, ttl=600): ...
    def status(self, engine_id: str) -> Literal["reachable", "unreachable", "unknown"]
    async def refresh(self, engine_ids: Iterable[str]) -> None   # runs in a thread; one at a time
```

`assess` rules for a shared-host engine. Evaluate all of them and report every
failing one:

| Code | Condition that disables | Example message |
|---|---|---|
| `limits_missing` | no CPU quota or memory limit, or swap is not 0 | "Set CPU and memory limits and disable swap for the container first." |
| `memory_limit` | `memory_limit < min_memory_mib` | "The container memory limit is 2.0 GiB. Nemotron English needs at least 2.5 GiB (3 GiB recommended)." |
| `host_memory` | `host_memory < 2 * max(memory_limit, min_memory_mib)` | "The host has 4.0 GiB of RAM. Nemotron needs at least 5.0 GiB, twice the container limit." |
| `cpu_quota` | `cpu_quota < min_cpu_quota` | "The CPU quota is 0.5 CPUs. Nemotron needs at least 1 CPU." |
| `cpu_instructions` | x86_64 and a flag in `cpu_flags` is missing | "This CPU lacks AVX2, so Nemotron would not keep up in real time." |
| `architecture` | `arch` not in {x86_64, aarch64} | "This processor architecture is not supported." |
| `storage` | the model is not ready and `free_disk_bytes is None` | "Persistent app storage is unavailable, so the model cannot be downloaded." |
| `disk_space` | the model is not ready and free space `< download_disk_bytes` | "Downloading needs 1.3 GiB of free space; 0.8 GiB is free." |
| `network` | the model is not ready and the probe says `unreachable` | "Cannot reach github.com to download the model." (`unknown` does not disable) |

Whistle is never disabled. Its card shows advisory reasons from
`resources.budget_problem` (C's function) only when it fails. Rules that reuse the
existing `budget_problem` checks (quota <= affinity/2, PID limit) are not repeated
here. The safety controller enforces them at load time for every engine. The
memory and CPU numbers must come from the catalog functions so that the panel and
the runtime never disagree.

## 6. Engine abstraction (owner A)

### 6.1 Protocols in `cassini_live/engines/__init__.py`

```python
class Backend(Protocol):
    """What safety, capacity, diagnostics, app and transcribers may use. Nothing else."""
    engine_id: str
    size: int                         # Whistle: worker processes; shared: speaker ceiling
    admission_limit: int              # written by safety = testedSpeakers
    closed: bool
    resource_check: Callable[[], str | None] | None   # written by safety; before and after load
    load_check: Callable[[], str | None] | None       # written by safety; before load only (shared host)
    failure_circuit_open: bool
    failures: int; capacity_waits: int; dropped_chunks: int; scheduling_yields: int
    last_pressure: float
    stream_ends: Counter; chunks_processed: int; audio_seconds: float
    finals_emitted: int; partials_emitted: int
    workers: set                      # diagnostics "scribe" numbering
    leased: set
    @property
    def waiting(self) -> int: ...
    @property
    def busy(self) -> int: ...        # len(leased)
    def is_idle(self) -> bool: ...    # fully started, nothing leased or open
    def resize(self, size: int) -> None: ...   # only while closed
    def note_drop(self) -> None: ...
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    def status(self) -> dict: ...     # today's keys + "engine": engine_id
    def transcriber(self, session_id, language, transcript_queue, *translation_hooks) -> "Transcriber": ...

class Transcriber(Protocol):
    session_id: str; language: str; queue: asyncio.Queue; gate: SpeechGate
    pending: str; lost_chunks: int; lease_samples: int; worker: object | None
    async def connect(self) -> None: ...
    async def start(self, stream) -> None: ...
    async def shutdown(self) -> None: ...
    async def set_language(self, language: str) -> None: ...
    async def abort(self) -> None: ...         # drop native state without restarting shared processes
    # Used by calibration only:
    async def _put_audio(self, samples) -> None: ...
    def _flush_gate(self) -> None: ...
    async def _decode(self) -> None: ...

def create_backend(engine_id: str, *, size: int, weights: str | None = None,
                   model_dir: Path | None = None, threads: int | None = None) -> Backend:
    """whistle -> WorkerPool(size, weights); nemotron-* -> SharedEngine(catalog.get(id), model_dir, size)."""
```

### 6.2 `WorkerPool` additions (`pool.py`, additive, no behavior change)

```python
engine_id = "whistle"
load_check = None
@property busy -> int: return len(self.leased)
def is_idle(self) -> bool: return self.idle.qsize() == self.size
def resize(self, size): self.size = size; self.idle = asyncio.Queue(maxsize=size)
def transcriber(self, session_id, language, q, *hooks): return WhistleTranscriber(session_id, language, q, *hooks, pool=self)
status(): adds "engine": "whistle"
```

`resize` raises `RuntimeError` if `not self.closed`. Import `WhistleTranscriber`
lazily inside `transcriber()`, because `transcriber.py` imports `pool.py`.

### 6.3 `transcriber.py`

* Keep `WhistleTranscriber` byte-for-byte, except for one new method:
  `async def abort(self): await self._release(healthy=False)`.
* Add a factory. `talk/client.py` and nothing else calls it:

  ```python
  def create(session_id, language, transcript_queue, *translation_hooks, pool=None):
      backend = pool if pool is not None else POOL
      if backend is None:
          return WhistleTranscriber(session_id, language, transcript_queue, *translation_hooks)
      return backend.transcriber(session_id, language, transcript_queue, *translation_hooks)
  ```

  A `WhistleTranscriber` built with no backend raises `CapacityError` from
  `connect()`, as today.
* `POOL` stays the module global. **Hook:** only `SafetyController.replace_pool` (C)
  and the app lifespan (C) assign it.
* New `class StreamTranscriber(WhistleTranscriber)` for shared engines. It inherits
  capture, gate, queue, `_emit`, `shutdown` and `set_language`. It overrides
  `_decode`, `_decode_audio`, `_finish`, `_release` and `abort`:
  * State: `self.stream: EngineStream | None` (the native stream; do not confuse
    it with the media `self.stream`; name it `self.engine_stream`),
    `self.slot: Slot | None`, and `self.worker` (an alias of `self.slot`, so that
    diagnostics lanes work unchanged).
  * `_decode_audio(chunk)`: if no slot, `self.slot = await
    pool.acquire_slot(timeout=2)`. On `CapacityError` the existing path drops the
    chunk. After acquiring, apply the existing 4 s staleness check (release the
    slot, note the drop, return). If `engine_stream` is None,
    `await pool.open_stream(self.language)`. Send the chunk (at most 1 s) with
    `await engine_stream.audio(samples)`, then `_emit(result)`. Count
    `lease_samples`, `chunks_processed` and `audio_seconds` as Whistle does.
    **No preemption block**: `pool.waiting` is never consulted.
  * Segment end (`chunk.end`): `result = await engine_stream.commit()`, then
    `_emit(result)`. Release the slot. `stream_ends[chunk.end_reason] += 1`. The
    native stream **stays open**.
  * Audio gap or expired audio: `finish()` the stream (hard pad, final text,
    stream closed), release the slot, clear the preview, and count
    `audioGap`/`expiredAudio`. The next speech opens a new stream. Never splice
    audio across lost audio.
  * End of track (`None` chunk): `finish()` if there is uncommitted audio,
    otherwise `cancel()`. Then return. This is the only "real end", where the 1.2 s
    zero pad and `input_finished` are used.
  * Per-stream `EngineStreamError`: `cancel()` the stream, release the slot,
    clear the preview, and log the exception type only. `EngineFailed` (host died):
    drop the references, release the slot, clear the preview. Neither restarts
    anything from the transcriber.
  * `_release(healthy)`: release the slot. If `not healthy`, also `cancel()` the
    stream. `abort()` is `_release(healthy=False)`.
  * Language: `open_stream` receives the room language. `SharedEngine` maps it
    (6.5). `_emit` already prefers `result["language"]`.
* `scenario()` in `calibration.py`: build tracks with
  `pool.transcriber(str(i), "en", captions)` and clean up with
  `asyncio.gather(*(t.abort() for t in tracks))`. Nothing else changes, so the pass
  criteria are identical for every engine.
* `talk/client.py`: replace the `WhistleTranscriber(...)` construction with
  `create_transcriber(...)`, same arguments. Change the log message to "Could not
  start caption stream." The type hint may stay as is.

### 6.4 `SharedEngine` (`engines/shared.py`)

```python
class CapacityError: imported from cassini_live.pool (same class Whistle uses)
class EngineFailed(RuntimeError): ...        # host exited or timed out; all streams invalid
class EngineStreamError(RuntimeError): ...   # per-stream error reply; host alive

@dataclass(eq=False)
class Slot:
    index: int                                # 1-based, stable while held; used by diagnostics

class EngineStream:
    id: int
    alive: bool
    async def audio(self, samples: np.ndarray) -> dict     # {"text": str, "pending": str, "language"?: str}
    async def commit(self) -> dict                         # final text since the last commit
    async def finish(self) -> dict                         # pad 1.2 s, input_finished, final; closes
    async def cancel(self) -> None                         # closes without decoding; never raises

class SharedEngine:                                         # implements Backend
    def __init__(self, engine: catalog.Engine, model_dir: Path, size: int, *,
                 threads: int | None = None, max_streams: int = catalog.MAX_STREAMS,
                 max_batch: int | None = None, request_timeout: float = 5.0,
                 ready_timeout: float = 120.0, process_factory=EngineHostProcess,
                 clock=time.monotonic): ...
    async def acquire_slot(self, timeout: float = 2) -> Slot
    def release_slot(self, slot: Slot) -> None
    async def open_stream(self, language: str) -> EngineStream

class EngineHostProcess:                                    # thin wrapper; replaced by a fake in tests
    async def start(self, argv: list[str], env: dict) -> None
    async def send(self, message: dict) -> None
    async def receive(self) -> dict                         # raises EOFError on exit
    async def close(self) -> None                           # quit, terminate after 3 s, kill after 3 s
    returncode: int | None
```

Behavior:

* **Slots.** There are `admission_limit` speaking slots, FIFO waiters with the same
  hand-off rules as `WorkerPool._offer`/`acquire_wait` (port that logic, including
  the cancellation race). `leased` and `workers` are both the set of held `Slot`
  objects. `waiting` counts live waiters. `is_idle()` means the host is ready, no
  slot is held and no stream is open. `size` is the speaker ceiling, and
  `resize()` changes only `size`.
* **Streams.** Open streams are bounded by `max_streams`. Beyond that,
  `open_stream` raises `CapacityError`. Each stream has at most one outstanding
  request. Replies are demultiplexed by `id` to per-request futures in one reader
  task. A reply with an unknown id is logged by id only and ignored. A stream with
  no audio for 120 s may be cancelled lazily by its transcriber at the next segment
  start; this is optional and only saves memory.
* **Start.** If `load_check()` returns a problem, raise `CapacityError(problem)`.
  Then spawn the host and wait for `ready` (`ready_timeout`). Run
  `resource_check()` after the load and close the host on failure. Use generation
  fences exactly as `WorkerPool._new` does: a `close()` during `start()` wins.
* **Host failure** (EOF, `error` without an id, or a request that takes longer than
  `request_timeout`): fail every pending future with `EngineFailed` and mark every
  stream `alive=False`. Increment `failures` and record the time in
  `failure_times`. Three failures within 60 s set `failure_circuit_open = True`,
  and safety then trips. Otherwise restart the host in a background task, with
  `load_check` before the load. A `load_check` failure opens the circuit too.
  Waiters for slots are not failed; slots are independent of the host.
* **Close.** `generation += 1`, `closed = True`, fail the waiters with
  `CapacityError("Caption service is stopping")`, fail pending futures, then close
  the process (send `quit`, wait 3 s, terminate, wait 3 s, kill).
* `status()` returns today's `WorkerPool.status()` keys with the same meaning:
  `capacity = size`, `available = admission_limit - busy`, `workers = 1` while the
  host runs, and `speechGating` / `speechDetector` unchanged. It adds
  `"engine"`, `"openStreams"` and `"hostRestarts"`.
* **Language mapping** in `open_stream`: for `nemotron-en`, any value becomes
  `"en"`. The app has already rejected the others. For `nemotron-multilingual`,
  `"auto"` becomes `"auto"`, and a Cassini code becomes `LANGUAGE_OPTION[code]`.
  `LANGUAGE_OPTION` is a dict in `engine_host.py`: `"en"` or `"en-US"`, whichever
  form sherpa-onnx 1.13.8 accepts. The integration test decides which.
* **Environment for the host:** `OMP_NUM_THREADS=1`,
  `ORT_DISABLE_TELEMETRY=1`, plus `CASSINI_ENGINE_THREADS` (default 1, range 1-4)
  passed as `--threads`.

### 6.5 Engine host process (`engine_host.py`)

Command line:
`python -m cassini_live.engine_host --engine <id> --model-dir <dir> --threads 1 --max-streams 12 --max-batch 4 [--segments continuous|per_segment]`

Startup:

1. Write `1000` to `/proc/self/oom_score_adj`, so the kernel kills the host and not
   the supervisor.
2. `sherpa_onnx.OnlineRecognizer.from_transducer(tokens=…, encoder=…, decoder=…,
   joiner=…, num_threads=threads)`, exactly as `local/stt-eval/engines.py` does.
3. Call `os.posix_fadvise(fd, 0, 0, POSIX_FADV_DONTNEED)` on each `.onnx` file.
   The baseline showed that a cold load charges about 630 MiB of page cache to the
   container, and that page cache trips the 80% rule.
4. Emit `{"type":"ready","engine":id,"languages":[…],"chunkMs":560,"rssMiB":n}`.

Protocol. JSON lines; requests on stdin, replies on stdout; stderr is native
logging only and never carries text or audio. **Exactly one terminal reply per
request.** A stream has at most one outstanding request.

| Request | Terminal reply | Effect |
|---|---|---|
| `{"type":"open","id":N,"language":"en"\|"auto"\|<option>}` | `{"type":"opened","id":N}` | `create_stream()`; on the multilingual model, `set_option("language", …)` |
| `{"type":"audio","id":N,"pcm":"<b64 float32 LE, 1..16000 samples>"}` | `{"type":"result","id":N,"result":{"text":"","pending":"…"}}` | `accept_waveform`, then decode until `is_ready` is false; `pending` is the text since the last commit |
| `{"type":"commit","id":N}` | `{"type":"result","id":N,"result":{"text":"<final>","pending":""}}` | accept `COMMIT_PAD_S` (0.56 s) of zeros, decode while ready, return the text since the last commit as final, advance the commit offset; the stream stays open |
| `{"type":"finish","id":N}` | `{"type":"result","id":N,"result":{"text":"<final>","pending":""}}` | accept 1.2 s of zeros, `input_finished()`, decode while ready, return the final text, free the stream |
| `{"type":"cancel","id":N}` | `{"type":"closed","id":N}` | free without decoding |
| `{"type":"quit"}` | none | exit 0 |
| any per-stream failure | `{"type":"error","id":N,"code":"language"\|"limit"\|"protocol","message":"…"}` | stream freed; process lives on |
| fatal failure | `{"type":"error","message":"…"}`, then exit 1 | supervisor restarts the host |

Notes:

* **Decode loop.** A reader thread puts parsed requests on a queue. The main thread
  accepts audio, gathers the streams where `is_ready(s)` is true (oldest request
  first), and calls `decode_streams(batch[:max_batch])`. After that, it completes
  each request whose stream is no longer ready. Only the main thread writes to
  stdout. This is the prototype in `local/stt-eval/resources/shared_host.py`,
  changed to keep streams open.
* **Text offsets.** Greedy transducer output only grows. The host keeps
  `committed = len(text)` per stream. `pending = text[committed:].strip()`. After
  30 minutes of audio on one stream, the next `commit` is followed by a silent
  rotation (finish, then open with the same id), which keeps memory bounded.
* **Automatic language tags.** On the multilingual model with `auto`, strip every
  `<xx-YY>` or `<xx>` tag from the text. Report the last one as
  `result["language"] = "xx"`, but only if `xx` is a Cassini language code.
* **Segments mode.** `per_segment` makes `commit` behave like `finish` followed by
  a fresh `open` with the same id and language. It is the fallback if the
  integration test shows that `commit` truncates finals (section 14).
* **Validation.** Reject more than 16000 samples, an odd byte length, an unknown
  id, a duplicate open, more than `max_streams` streams, or a second request
  outstanding on one stream. Each of these is an `error` with an id.
* Structure the module so tests can call `serve(stdin, stdout, recognizer,
  options)` with a fake recognizer. Import `sherpa_onnx` only inside `main()`.

## 7. Supervisor side (owner C)

### 7.1 Settings: `cassini_live/settings.py`

```python
@dataclass
class Acknowledgement:
    version: str
    user: str           # "" from the settings form: AppAPI does not tell the ExApp who saved
    at: float

@dataclass
class EngineSettings:
    engine: str = catalog.DEFAULT_ENGINE
    pending: str | None = None          # target of a switch in progress (crash detection)
    acknowledgements: dict[str, Acknowledgement] = field(default_factory=dict)
    last_error: dict | None = None      # {"code","message","engine","at"} shown in the panel until the next switch
    updated_at: float = 0

class SettingsStore:
    def __init__(self, path: Path | None): ...   # None: in memory only (tests, no storage)
    def read(self) -> EngineSettings              # raises ValueError for an invalid file; size cap 4096 B
    def write(self, settings: EngineSettings) -> None   # tmp + fsync + replace, like RecoveryJournal
```

Path: `state_dir() / "cassini-live-settings.json"`. `state_dir()` is a new function
in `config.py`:
`Path(os.environ.get("APP_PERSISTENT_STORAGE", "/home/cassini/.local/state"))`.
`app.py` uses it for the journal, settings and `models/`. Unknown engine ids in the
file are invalid. On `ValueError`, use the defaults (whistle) and set `last_error`
to "Saved engine settings were unreadable; using Whistle".

### 7.2 Safety, resources, recovery, capacity, config

Every change keeps Whistle's numbers. `engine_id` defaults to `"whistle"`.

* `resources.budget_problem(data, workers, engine_id="whistle")`. The common checks
  stay first and unchanged: finite limits, swap 0, quota <= affinity/2,
  memory <= host/2, PIDs <= 256. Then:
  * Whistle keeps its two existing lines verbatim.
  * Shared host: if `workers > catalog.worker_ceiling(e, cpu)` → "Requested
    simultaneous speakers exceed the CPU budget for <name>". If
    `cpu < requirements.min_cpu_quota` → "<name> needs a CPU quota of at least 1
    CPU". If `memory < memory_floor_mib(e, workers) * MIB` → "The memory budget is
    too small for <name>: set at least 2.5 GiB (3 GiB recommended)".
  * Then `pressure_problem(data)`, as today.
* New `resources.load_problem(data, engine_id) -> str | None`. For a shared host,
  return "Not enough free container memory to load <name>" when
  `memory.current + model_mib * MIB > 0.8 * memory.max`, where `memory.current` is
  `memoryFraction * memoryLimitBytes`. Otherwise return `pressure_problem(data)`.
  Whistle returns `None`.
* `recovery.recovery_problem(data, workers, engine_id="whistle")` replaces
  `workers * 192 * MIB` with `catalog.load_headroom_mib(engine_id, workers) * MIB`.
  Whistle gives the identical number.
* `config.py`: `state_dir()` as above. `Config.from_env` keeps its Whistle default,
  which equals `worker_ceiling("whistle", cpus)`. Add
  `Config.workers_explicit: bool = False` (from `CASSINI_LIVE_WORKERS`). Add it as
  the **last** field with a default so `Config(None, 2, "secret", "")` in the tests
  still works.
* `SafetyController` (`safety.py`):
  * `engine_id` property → `getattr(self.pool, "engine_id", "whistle")`.
  * `__init__` and `replace_pool` set both hooks: `pool.resource_check = lambda:
    pressure_problem(self.reader())` and `pool.load_check = lambda:
    load_problem(self.reader(), self.engine_id)`.
  * `def replace_pool(self, pool)`: requires that no monitor is running (raise
    `RuntimeError` otherwise). Sets `self.pool`, `self.assessment.pool`, both hooks
    and `transcriber.POOL = pool`.
  * Auto-size: replace the direct `size`/`idle` assignment with
    `self.pool.resize(catalog.worker_ceiling(self.engine_id, data["effectiveCpus"]))`.
  * `budget_problem(data, self.pool.size, self.engine_id)` and
    `recovery_problem(..., self.engine_id)`.
  * `_attempt`: `asyncio.timeout(catalog.get(self.engine_id).requirements.start_timeout_s)`.
    That is 30 s for Whistle, as today.
  * `async def start(self, *, fresh: bool = False)`. `fresh=True` is used only by
    `EngineManager`. It still validates the journal, then ignores the persisted
    cooldown and failure count for this attempt: `failures=0`, `next_attempt=0`,
    `must_settle=False`. The budget and headroom gates still apply. Write the
    journal with the new state before the load. Rationale: the cooldown protects
    the host from repeated loads of the same failing engine. A deliberate switch
    and its rollback are bounded by the manager, at most two loads per admin
    action.
  * Outcome signal: `self._outcome: asyncio.Future | None`, created by `start()`
    when it launches a monitor, and resolved once with `(ok: bool, reason: str)`:
    * `_watch` reaches `ready` → `(True, "Capacity check passed")`;
    * `trip()` → `(False, reason)`;
    * a non-transient `budget_problem` in `_supervise`, or an unreadable journal →
      `(False, problem)`;
    * `_close()` → `(False, "Caption service stopped")`.
    * Transient waiting does not resolve it.

    `async def wait_outcome(self, timeout: float) -> tuple[bool, str]` returns
    `(False, "The capacity check did not finish in time")` on timeout.
  * `async def close(self, reason: str | None = None)`: the same as today, but uses
    `reason` instead of "Caption service stopped" when given.
* `capacity.py`: `CapacityAssessment.run` uses `pool.is_idle()` instead of
  `pool.idle.qsize() != pool.size`, and adds `"engine": pool.engine_id` to the
  report. The standalone CLI gains `--engine` (default whistle) and `--model-dir`,
  and builds the pool with `engines.create_backend`. A Nemotron CLI run must be
  started under `systemd-run … MemoryMax=3G`; say so in `--help`.

### 7.3 `EngineManager` (`cassini_live/engine_switch.py`)

```python
SwitchState = Literal["idle", "validating", "downloading", "verifying", "stopping",
                      "loading", "checking", "rolling_back", "succeeded", "failed", "cancelled"]
ACTIVE_STATES = {"validating", "downloading", "verifying", "stopping", "loading", "checking", "rolling_back"}
CRITICAL_STATES = {"stopping", "loading", "checking", "rolling_back"}   # captions are down

@dataclass
class SwitchStatus:
    state: SwitchState = "idle"
    target: str | None = None
    previous: str | None = None
    started_at: float | None = None
    updated_at: float | None = None
    message: str = ""                       # human-readable current step
    error: dict | None = None               # {"code": str, "message": str}
    rolled_back: bool = False
    def to_json(self) -> dict: ...          # camelCase, section 8.3

class SwitchRefused(Exception):
    status: int                             # 400 / 409 (kept as a severity hint; no HTTP API uses it)
    code: str                               # "not_eligible" | "license_required" | "busy" | "disabled"
                                            # | "unknown_engine" | "active" | "idle"
    reasons: tuple[Reason, ...] = ()

class EngineManager:
    def __init__(self, safety, service, config, store: ModelStore, settings: SettingsStore, *,
                 backend_factory=engines.create_backend, facts=eligibility.host_facts,
                 probe: NetworkProbe | None = None, clock=time.time,
                 check_timeout: float = 600): ...
    active: str                              # engine id of safety.pool
    status: SwitchStatus
    on_change: Callable[[], None] | None = None   # hook for SettingsSync (8.4); sync, never raises
    last_refusal: dict | None                # {"code","message","engine","reasons":[Reason json],"at"}
    @property
    def busy(self) -> bool                   # status.state in ACTIVE_STATES
    @property
    def critical(self) -> bool               # status.state in CRITICAL_STATES
    @property
    def desired(self) -> str                 # status.target while busy, else active
    async def restore(self) -> None          # lifespan, before any safety.start
    async def start_safety(self) -> None     # the only way app.py starts safety
    async def request_switch(self, engine: str, accept_license: bool, user: str = "") -> SwitchStatus
    async def cancel(self, reason: str = "Cancelled by an administrator") -> SwitchStatus
    async def delete_model(self, engine: str) -> dict          # returns that engine's card
    async def delete_unused_models(self) -> list[str]          # every ready/partial model that is
                                                               # neither active nor the switch target
    def note_refusal(self, refused: SwitchRefused, engine: str) -> None   # sets last_refusal, notifies
    async def refresh_network(self) -> None  # one NetworkProbe.refresh for engines whose model is not ready
    async def stop(self) -> None             # disable/lifespan: cancel any switch, no restart
    def languages(self) -> dict               # Talk supported_languages for the active engine
    def engine_status(self) -> dict           # /status "engine" block (8.3)
    def panel(self) -> dict                   # settings-form input (8.3); cheap, never probes
```

`on_change` is called (synchronously, exceptions logged and swallowed) after every
change of `status`, `active`, `last_refusal` or `settings.last_error`, and from the
download loop at most once per second while `store.progress()` advances. The
manager never awaits it and never imports `settings_sync`. `request_switch` clears
`last_refusal`. `panel()` no longer schedules a network probe; probes run only
through `refresh_network()`, which `SettingsSync` calls once when the service is
enabled, and `request_switch` awaits in `validating` (bounded by the probe's 5 s
timeout).

`backend_factory` is called as
`backend_factory(engine_id, size=…, weights=config.weights, model_dir=store.model_dir(id))`.
`size` is `config.workers` when `config.workers_explicit`, and otherwise
`catalog.worker_ceiling(id, resources.limits()["effectiveCpus"])`.

## 8. Nextcloud integration and HTTP API (owner C)

Revision 2 removes the admin HTTP API (`/api/v1/admin/...`) entirely. The
administrator acts through the declarative settings form (section 11). The ExApp
reads the form's stored values from AppAPI and writes the form back (8.4). No new
route is declared.

### 8.1 Routes in `appinfo/info.xml`

Remove these two route entries and the three FastAPI handlers in `app.py` (`/ui/live.js`,
`/ui/live.css`, `/img/app.svg`), plus the `ASSETS` constant and the `FileResponse`
import:

```xml
<route><url>^ui\/live\.(js|css)$</url>...</route>
<route><url>^img\/app\.svg$</url>...</route>
```

Keep `^status$` (USER) unchanged; see section 14 for whether to drop it later.
`GET /status` itself stays: the capacity CLI and `scripts/check_deployment.py`
call it directly in the container with the AppAPI secret, not through the proxy.
`scripts/check_manifest.py` then asserts that `status` is the only public route and
no longer compares `img/app.svg` with `cassini_live/static/app.svg`.

In `enabled_handler`, delete both `nc.ocs(...)` calls that register the top menu
(`/ocs/v2.php/apps/app_api/api/v1/ui/top-menu`) and its script
(`.../ui/script`). An instance upgraded from 0.6.x still has the old top-menu entry
registered. On enable, call once, best effort (exceptions logged at debug level):
`await nc.ui.resources.delete_script("top_menu", "live", "ui/live")` and
`await nc.ui.top_menu.unregister("live")`. Both are no-ops when absent
(nc_py_api 0.24.2: `not_fail=True` by default).

### 8.2 Endpoints

There are no admin endpoints. Changed existing endpoints:

* `/capabilities` → `supported_languages: manager.languages()`. `/api/v1/languages`
  → `manager.languages()`. Whistle and multilingual give today's 8 entries,
  including `auto`. English gives `{"auto": {"name": "Automatic (English)", …},
  "en": {"name": "English", …}}`. `auto` is kept because Talk sends
  `langId="auto"` by default (section 14).
* `/api/v1/call/transcribe` and `/set-language`: after the pydantic literal check,
  a `langId` that is not in `manager.languages()` returns 400 "The active speech
  engine does not support this language".
* `/capacity/check` returns 409 while `manager.busy`.
* `/api/v1/languages` and `/capabilities` change when the engine changes. Talk
  fetches them from the ExApp; whether Talk caches them is not verified (section
  14). Nothing is re-registered with Talk.
* `/status`: `"model"` becomes the engine name (today it is hard-coded "Whistle").
  `"languages"` and `"automaticLanguage"` come from the active engine. Add
  `"engine": manager.engine_status()`. The pool fields come from
  `safety.pool.status()`, not from the startup pool.

### 8.3 JSON shapes

`engine_status()`, which goes in `/status` (container-local callers and the proxied
`status` route):

```json
{"id": "nemotron-en", "name": "Nemotron English", "languages": ["en"],
 "automaticLanguage": false, "license": "NVIDIA Open Model License",
 "switching": false, "switchState": "idle"}
```

`Panel`, the return value of `manager.panel()`. It is internal now: `admin_form.build_form`
is its only consumer, and tests assert its keys. `lastRefusal` is new in revision 2:

```json
{
  "active": "whistle",
  "activeCalls": 1,
  "measurements": "provisional",
  "switch": {"state": "downloading", "target": "nemotron-en", "previous": "whistle",
             "startedAt": 1760000000, "updatedAt": 1760000042,
             "message": "Downloading Nemotron English (212 of 442 MiB)",
             "progress": {"phase": "downloading", "bytesDone": 222298112, "bytesTotal": 463945051},
             "error": null, "rolledBack": false},
  "lastError": null,
  "lastRefusal": {"code": "not_eligible", "engine": "nemotron-en", "at": 1760000000,
                  "message": "Nemotron English cannot run here.", "reasons": [Reason]},
  "host": {"cpuQuota": 4.0, "affinityCpus": 8, "memoryLimitBytes": 3221225472,
           "hostMemoryBytes": 8589934592, "freeDiskBytes": 40000000000,
           "arch": "x86_64", "cpuFlags": ["avx2"]},
  "engines": [EngineCard, EngineCard, EngineCard]
}
```

`EngineCard`:

```json
{
  "id": "nemotron-en", "name": "Nemotron English", "active": false,
  "summary": "Most accurate. English only.",
  "accuracy": {"meetingWer": 0.089, "note": "Word error rate on our recorded English meetings; lower is better."},
  "languages": ["en"], "automaticLanguage": false,
  "requirements": {"memoryLimitMiB": 2560, "recommendedMemoryLimitMiB": 3072, "hostMemoryMiB": 5120,
                   "cpuQuota": 1.0, "downloadBytes": 463945051, "diskBytes": 1394300000,
                   "cpuFlags": ["avx2"], "provisional": true},
  "license": {"id": "NVIDIA-OML", "name": "NVIDIA Open Model License", "url": "https://…",
              "version": "2025-10-24", "requiresAcknowledgement": true,
              "acknowledged": false, "notice": "Licensed by NVIDIA Corporation under the NVIDIA Open Model License",
              "summary": ["…"]},
  "model": {"state": "partial", "bytesDone": 120000000, "bytesTotal": 463945051, "error": null},
  "eligible": false,
  "advisory": false,
  "reasons": [{"code": "memory_limit", "message": "…", "measured": 2147483648, "required": 2684354560}]
}
```

`model.state` uses the `ModelStateName` values. For Whistle, `"bundled"` and
`downloadBytes: 0`. `acknowledged` is true only if the stored acknowledgment
version equals `license.version`.

### 8.4 `SettingsSync` (`cassini_live/settings_sync.py`)

Facts from the locked `nc-py-api` 0.24.2 and from AppAPI (`main`, listeners in
`lib/Listener/DeclarativeSettings/`), verified for this revision:

* `nc.ui.settings.register_form(SettingsForm | dict)` POSTs
  `{"formScheme": form.to_dict()}` to `/ocs/v2.php/apps/app_api/api/v1/ui/settings`.
  Registering again with the same `id` **replaces** the form ("Registers or edit").
  `unregister_form(form_id)` and `get_entry(form_id)` exist. All are async on
  `AsyncNextcloudApp`.
* For a form with `section_type="admin"`, AppAPI's `SetValueListener` stores each
  field's value in the ExApp's **app config** (`oc_appconfig_ex`) under the **field
  id**, with no form prefix. `GetValueListener` returns the stored value, or the
  field's `default` when no row exists. So the ExApp reads with
  `await nc.appconfig_ex.get_values([...])` (one OCS call, returns `CfgRecord(key,
  value)` for existing keys only), writes with `set_value(key, str)` and resets to
  the default with `delete(key)`.
* AppAPI forwards no change event to the ExApp. `SettingsField.notify` is a
  placeholder ("to be supported in future") and is always sent as `false`.
  Nextcloud's `webhook_listeners` cannot observe `oc_appconfig_ex` either. Polling
  is therefore the only mechanism.
* Nextcloud core enforces admin rights: `DeclarativeManager` refuses admin-section
  values for non-admins, and `getFormIDs` hides admin forms from them. No route in
  `info.xml` is involved.

```python
POLL_S = 3.0                 # appconfig_ex poll while enabled
BACKOFF_S = 30.0             # after a failed OCS call; reset on success
PUBLISH_MIN_S = 3.0          # at most one register_form per 3 s (trailing publish guaranteed)
PROGRESS_STEP = 5            # percent; progress alone republishes only on a new 5% step
LICENSE_GRACE_S = 30.0       # see "license order" below

class SettingsSync:
    def __init__(self, manager: EngineManager, nc_factory, status: Callable[[], dict], *,
                 build=admin_form.build_form, parse=admin_form.parse_values,
                 clock=time.monotonic, poll_s=POLL_S, publish_min_s=PUBLISH_MIN_S): ...
    async def start(self) -> None     # idempotent; sets manager.on_change, publishes, refresh_network, starts loop
    async def stop(self, final: str | None = None) -> None   # cancels the loop; if final, publishes once more
                                                             # with that note (best effort, 5 s timeout)
    def notify(self) -> None          # manager.on_change target: marks dirty, wakes the publisher
    async def poll_once(self) -> None # one read + decision (tests call it directly)
    async def publish(self, *, force: bool = False) -> None
    notes: list[str]                  # transient sync-side notes shown in the form (e.g. pending license)
```

`status` is a zero-argument callable that returns the `/status` body. `app.py`
extracts the body of its `status()` handler into a local `status_body()` function and
passes it. `nc_factory` is the same factory `create_app` receives; the sync keeps one
`AsyncNextcloudApp` instance and recreates it after an error.

**Publishing.** `publish()` builds `build(manager.panel(), status(), tuple(notes))`
and calls `nc.ui.settings.register_form(form)`. It skips the call when the form's
`to_dict()` equals the last one sent, unless `force`. A publisher task coalesces
`notify()` calls: it waits until `PUBLISH_MIN_S` has passed since the last publish,
then publishes once. While a download runs, `admin_form` rounds the percentage down
to a multiple of `PROGRESS_STEP`, so byte-level progress does not change the form
and does not cause a call. A failed `register_form` is logged once per error kind at
warning level and retried at the next notify or poll.

**Polling.** Every `poll_s` (`BACKOFF_S` after an error) the loop reads
`[admin_form.ENGINE_FIELD, admin_form.LICENSE_FIELD, admin_form.CLEANUP_FIELD]` and
calls `parse(records)` → `admin_form.Selection(engine: str | None, accept_license:
bool, cleanup: bool)`. Decision rules, in order:

1. **First poll after `start()`.** Remember the value as `seen`. If `engine` is set
   and differs from `manager.desired`, write `manager.desired` back and add no
   switch. A choice saved while the ExApp was down, or left over from a rollback or a
   restore fallback, is never acted on at startup. This keeps the "no automatic
   retry of a failed engine" rule of section 9.
2. **Cleanup.** If `cleanup` is true: `await manager.delete_unused_models()`, then
   `appconfig_ex.delete(CLEANUP_FIELD)` (the box returns to its default, unchecked).
3. **No change.** If `engine is None` or `engine == seen`, go to rule 6.
4. **New choice** (`engine != seen`). Set `seen = engine`. Then:
   * Unknown id (not in `catalog.ENGINES`): `note_refusal(code="unknown_engine")`,
     write back.
   * `manager.busy`: if `engine == status.previous` and the state is not
     `rolling_back`, call `manager.cancel("Cancelled in the administration
     settings")`. Otherwise write `status.target` back and add the note "A change to
     <target> is in progress. Wait for it to finish."
   * `engine == manager.active`: nothing.
   * Otherwise `await manager.request_switch(engine, accept_license, user="")`.
     `SwitchRefused` → see "license order" for `license_required`; any other code →
     `manager.note_refusal(e, engine)` and write `manager.desired` back.
5. **License order.** Nextcloud saves each field on its own, so an administrator may
   pick "Nemotron English" a few seconds before ticking the license box. On a
   `license_required` refusal, keep `pending = (engine, now)` and the note "Tick the
   NVIDIA Open Model License box to continue." Each later poll retries
   `request_switch` for `pending` while `accept_license` is false and less than
   `LICENSE_GRACE_S` has passed. When the box is ticked, the retry succeeds. After
   the grace, `note_refusal(license_required)`, write the active engine back and
   clear `pending`. Choosing another engine clears `pending`.
6. **Reconcile.** If `not manager.busy` and the stored `engine` differs from
   `manager.active` (a switch ended in `failed`/`cancelled`/rollback), write
   `manager.active` back and set `seen` to it. Write-backs use
   `appconfig_ex.set_value(ENGINE_FIELD, value)` and are followed by `notify()`.

The license box is the administrator's statement and is never written by the ExApp.
The acknowledgment that counts is the one `request_switch` records in
`EngineSettings.acknowledgements` (with the license version and `user=""`). The
field id carries the license version (`LICENSE_FIELD`, section 11), so a future
license version needs a fresh tick.

**Lifecycle (wired in `app.py`).** `SettingsSync` exists only in production
wiring, that is when `create_app` builds the pool itself (`pool is None`), exactly
like `EngineManager.restore()`. Injected-pool tests have no sync, so the existing
`test_app.py` cases keep their fake Nextcloud objects.

* `enabled_handler(True)`, after `service.discover` succeeds: `await sync.start()`.
  It replaces the removed top-menu registration.
* `restore_connection` (startup with the app already enabled): `await sync.start()`
  after `service.enabled = True`.
* `enabled_handler(False)`: `await manager.stop()`, then
  `await sync.stop(final="Cassini Live is disabled. Enable it to change the speech
  engine.")`, then the existing closes.
* Lifespan shutdown: `await sync.stop()` first (no final publish), then the order of
  section 10.

A sync failure never affects captions: every OCS error is caught, logged, and
retried after `BACKOFF_S`.

## 9. Switching state machine (owner C)

```text
                  new value in the settings form (SettingsSync)
 idle/succeeded/failed/cancelled ──────────────► validating
                                                   │ refuse → note_refusal, value written back, state unchanged
                                                   ▼
                        model ready? ──no──► downloading ──► verifying ──┐
                              │yes                 │cancel/err      │err │
                              │                    ▼                ▼    │
                              │              cancelled/failed  (old engine never stopped)
                              ▼◄──────────────────────────────────────────┘
                           stopping ──► loading ──► checking ──► succeeded
                              │            │           │
                              └─── any failure, timeout or cancel ───► rolling_back ──► failed/cancelled
                                                                     (rolledBack = true if the previous
                                                                      engine passed its check again)
```

| State | Entered by | Work | Exit |
|---|---|---|---|
| validating | `request_switch` (from `SettingsSync`) | `refresh_network()` for the target (outside the lock), re-read facts, `assess()`, check the license ack (record it with `user=""` if `acceptLicense`), check that the service is enabled, `settings.last_error = None`, `last_refusal = None` | raise `SwitchRefused` synchronously, or start the task |
| downloading | task | `store.download(target)`; progress from `store.progress()` | ready → verifying; `ModelError` → failed (code = `error.code`); cancel → cancelled |
| verifying | task | `store.verify(target, deep=True)` (also when the model was already present) | ok → stopping; corrupt → failed (`hash_mismatch`) |
| stopping | task | persist `settings(engine=previous, pending=target)`; `safety.close(reason="Changing the speech engine")` (closes rooms like an overload pause) | → loading |
| loading | task | `backend = factory(target)`; `safety.replace_pool(backend)`; `safety.start(fresh=True)` | → checking |
| checking | task | `ok, why = await safety.wait_outcome(check_timeout)` | ok → succeeded; else → rolling_back |
| succeeded | task | persist `settings(engine=target, pending=None)`; `active = target` | terminal (shown until the next switch) |
| rolling_back | task | `safety.close(reason=…)`; if `previous` is not whistle and fails `store.verify`, use whistle; `replace_pool(factory(previous))`; `start(fresh=True)`; `wait_outcome(check_timeout)` | ok → failed/cancelled with `rolledBack=True`; else → failed with `rolledBack=False` (safety keeps recovering the previous engine on its normal schedule) |

Failure codes in `switch.error.code`: `not_eligible`, `license_required`, `disk_space`,
`network`, `size_mismatch`, `hash_mismatch`, `archive_layout`, `storage`,
`load_failed`, `capacity_failed`, `timeout`, `cancelled`, `disabled`,
`rollback_failed`. The message is the safety or store reason, for example "Capacity
check did not establish a safe speaking budget" or "The caption memory limit was
exceeded".

After rolling back, persist `settings(engine=previous, pending=None,
last_error={…, "engine": target})`.

**Restore at startup** (`EngineManager.restore()`, called in the lifespan before
`restore_connection`):

1. `settings.read()`. If the file is invalid, use the defaults and set
   `last_error`.
2. If `pending` is set, a switch was interrupted, for example by a crash or OOM.
   Clear it, keep `engine` (the previous engine), and set `last_error = "The change
   to <target> was interrupted by a restart"`. Persist.
3. If `engine != "whistle"`: `store.verify(engine, deep=True)`, then `assess()`,
   ignoring the `network`, `disk_space` and `storage` reasons because the model is
   present. On failure, use whistle, persist `engine="whistle"`, and set
   `last_error` with the reason. **No automatic retry of a failed engine at the
   next restart**: an administrator selects it again.
4. `safety.replace_pool(factory(engine))`. Set `active`.
5. If `engine != "whistle"`, set `restore_watch = True`. The first
   `start_safety()` then also starts a task that awaits the first outcome. On
   failure it runs the rolling_back path to whistle (status `failed`,
   `rolledBack`, and `last_error`), and persists whistle. A later trip after a
   successful start is **not** a rollback trigger. Safety's normal recovery
   handles it for the same engine.

Tests (pool injected) skip `restore()`. In that case `active =
safety.pool.engine_id`.

## 10. Concurrency rules

* One event loop. These are all asyncio locks. Never hold a lock across a download.
* **Lock order:** `EngineManager.lock` → `SafetyController.lock`. Safety, store and
  backends never call into the manager.
* `EngineManager.lock` guards `request_switch` validation and task creation,
  `cancel`, `delete_model`, `restore`, `start_safety` and `stop`. The switch task
  takes it only to change `status` and to run the critical sequence
  stopping→checking (or rolling_back). `start_safety`, called from
  `enabled_handler` and `restore_connection`, waits on the lock and then does
  nothing if `critical` (the task starts safety itself). Otherwise it calls
  `safety.start()`, plus the restore watch from section 9.
* Only one switch task exists. `request_switch` while `busy` raises
  `SwitchRefused("busy")`; `SettingsSync` avoids that by checking `busy` first.
* `SettingsSync` holds no lock of its own across manager calls. It has one poll
  task and one publisher task; `notify()` only sets an `asyncio.Event`, so the
  manager can call it while holding `EngineManager.lock`. The poll task awaits
  `request_switch`, `cancel` and `delete_unused_models`, which return as soon as
  the switch task is created; it never awaits the switch itself. OCS calls made by
  the sync are bounded by `asyncio.timeout(10)`.
* `enabled_handler(False)` first calls `await manager.stop()`. That cancels the
  task. The task's `CancelledError` handler restores the previous backend with
  `replace_pool`, without starting safety, persists `engine=previous,
  pending=None`, and re-raises. Then the handler runs the existing
  `safety.close()` and `service.close()`. A disabled service refuses new switches
  (`SwitchRefused("disabled")`).
* `cancel()` during downloading or verifying calls `store.cancel(target)`. The
  download thread sees the event within 1 MiB and raises `ModelError("cancelled")`.
  Captions are unaffected. `cancel()` during stopping, loading or checking
  cancels the waiting step and goes to rolling_back. `cancel()` during
  rolling_back raises `SwitchRefused("busy")`, which the sync turns into a note.
* `replace_pool` is legal only while no safety monitor runs. The manager always
  calls `safety.close()` first.
* Room teardown happens only through `safety._teardown` → `service.stop_rooms()`,
  as on an overload pause. Transcribe requests during a switch get the existing 503
  with `Retry-After`, because `safety.state != "ready"`.
* `ModelStore` keeps its own state: one download at a time, its progress object, and
  a `threading.Event` per download. `NetworkProbe.refresh` runs one probe at a
  time in a thread. `panel()` stays synchronous and cheap. It uses cached facts no
  older than 5 s and never starts a probe (see 7.3 for when probes run).
* Lifespan shutdown order: `sync.stop()`, `manager.stop()`, `safety.close()`, `service.close()`,
  `safety.pool.close()`, `transcriber.POOL = None`.
* The engine host's reader task is the only reader of its stdout. Writes are
  serialized by an `asyncio.Lock` in `SharedEngine`.

## 11. Administration settings form (owner D): `cassini_live/admin_form.py`

The top-menu page is removed: D deletes `cassini_live/static/live.js`, `live.css`
and `app.svg` (and the directory). C removes their routes and registration (8.1).
Non-administrators no longer see the engine anywhere except through Talk's
language list; this is accepted.

The form appears under **Administration settings → Artificial intelligence**
(`/settings/admin/ai`) with the title "Cassini Live". Nextcloud lists a declarative
form only inside a section that a PHP app has registered (`CommonSettingsTrait::
formatSections` iterates registered sections), and AppAPI offers no way for an
ExApp to register its own section. So a separate "Cassini Live" entry in the
settings navigation is not possible. `ai` is registered by the core `settings` app
on every Nextcloud 33+, and it is where AppAPI providers are configured.

`admin_form.py` is pure: no I/O, no `await`, no imports from `engine_switch`,
`safety` or `app`. It imports `catalog` and
`nc_py_api.ex_app.ui.settings.{SettingsForm, SettingsField, SettingsFieldType}`.

```python
FORM_ID = "cassini_live_engine"
SECTION_ID = "ai"
SECTION_TYPE = "admin"
ENGINE_FIELD = "speech_engine"
LICENSE_FIELD = "nvidia_oml_" + catalog.get("nemotron-en").license.version.replace("-", "_") + "_accepted"
CLEANUP_FIELD = "remove_unused_models"
DOC_URL = "https://github.com/codemyriad/cassini-live/blob/main/docs/install.md#choose-a-speech-engine"
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})   # checkbox values as AppAPI stores them; case-insensitive
MAX_LABEL = 200
MAX_TEXT = 1000

@dataclass(frozen=True)
class Selection:
    engine: str | None        # None when no row is stored (the default applies)
    accept_license: bool
    cleanup: bool

def build_form(panel: dict, status: dict, notes: tuple[str, ...] = ()) -> SettingsForm: ...
def parse_values(records: Iterable) -> Selection: ...   # CfgRecord-like (.key, .value) or (key, value) pairs
def option_label(card: dict, panel: dict) -> str: ...
def status_text(panel: dict, status: dict, notes: tuple[str, ...] = ()) -> str: ...
def progress_percent(progress: dict | None) -> int | None: ...   # floor to a multiple of 5
```

`build_form` returns
`SettingsForm(id=FORM_ID, section_id=SECTION_ID, section_type=SECTION_TYPE,
title="Cassini Live", description=status_text(...), doc_url=DOC_URL, priority=50,
fields=[...])`. The output must be **deterministic**: equal inputs give equal
`to_dict()`, and no timestamps or byte counts finer than the 5% step appear in it.
`SettingsSync` relies on that to skip identical publishes.

**Fields**

1. `ENGINE_FIELD`, `RADIO`, title "Speech engine", `default=panel["switch"]["target"]
   if a switch is active else panel["active"]`. `options` is a dict `{label: engine
   id}` in catalog order (whistle, nemotron-en, nemotron-multilingual); nc_py_api
   sends it as `[{"name": label, "value": id}]`. Labels must be unique, which they
   are because each starts with the engine name. `description`: one line, "One engine
   serves the whole server. Changing it ends captions in active calls for a few
   minutes; participants turn captions on again afterwards." plus
   "{activeCalls} calls have captions on now." when `activeCalls > 0`.
2. `LICENSE_FIELD`, `CHECKBOX`, title "NVIDIA model license", `default=False`,
   `label` "I have read and accept the NVIDIA Open Model License for this server.",
   `description`: "Required before switching to Nemotron English. " + the 3.4
   summary sentences + " Full text: <license url>". If the stored acknowledgment
   matches the current version, start with "Accepted. ".
3. `CLEANUP_FIELD`, `CHECKBOX`, title "Downloaded models", `default=False`, label
   "Remove downloaded models that are not in use (frees {GiB})". Present only when
   at least one card that is neither active nor the switch target has
   `model.state` in {ready, partial, corrupt}.

Nemotron Multilingual's OpenMDW-1.1 license needs no acceptance and has no
checkbox. When it is the active engine or the switch target, the status text adds
"Nemotron Multilingual is licensed under OpenMDW-1.1: {url}. {origin notice}".

**Option labels** (`option_label`). Format `"{name} — {state}"`, then
` · `-separated facts, truncated with "…" at `MAX_LABEL`. The em dash and the
wording "unavailable:" are fixed, because the docs quote them.

| Card situation | Label |
|---|---|
| active, idle | `Whistle — active · 7 languages, automatic detection · bundled, light` |
| eligible, model ready | `Nemotron English — ready · English only · 8.9% word errors on our meetings · needs 2.5 GiB memory (3 GiB recommended), 1 CPU` |
| eligible, not downloaded | `Nemotron Multilingual — available · 7 languages, automatic detection · 12.8% word errors · 0.44 GiB download, needs 2.5 GiB memory` |
| ineligible | `Nemotron English — unavailable: The container memory limit is 2.0 GiB. Nemotron English needs at least 2.5 GiB (3 GiB recommended).` (first reason; then ` (+N more)`) |
| switch target, downloading | `Nemotron English — downloading 45%` |
| switch target, other active state | `Nemotron English — switching: {state message}` (for example "loading", "running the capacity check") |
| switch previous, while switching | `Whistle — active until the switch finishes (select to cancel)`; omit "(select to cancel)" in `rolling_back` |
| needs license, not acknowledged (nemotron-en) | eligible label + ` · accept license first` |

Add ` · figures provisional` to Nemotron labels while `requirements.provisional`.
A Whistle card with advisory reasons gets ` · warning: {first reason}`.

**Status text** (`status_text`, the form description, at most `MAX_TEXT` chars).
Plain sentences separated by spaces; Nextcloud renders the description as plain
text, so no Markdown and no reliance on newlines. In this order, skipping empty
parts:

0. While a switch is active: "Reload this page to see progress."
1. "Active engine: {name}." plus, while switching,
   "Changing to {target}: {switch.message}" and the 5% percentage while downloading.
2. Last result: `switch.state == "succeeded"` → "Now using {name}.";
   `failed` → "Could not switch to {target}: {error.message}" + " Rolled back to
   {previous}." or " Captions will recover automatically."; `cancelled` →
   "The change to {target} was cancelled.". Then `lastRefusal` → "Your choice of
   {engine} was not applied: {message}" plus up to three reason messages. Then
   `lastError` from a restore → its message.
3. Captions: from `status["resourceSafety"]`: `state == "ready"` → "Captions are
   ready." otherwise "Captions are paused: {reason}." Add "Administrator action
   required." when `adminActionRequired`.
4. Capacity check: from `status["capacityAssessment"]`: "Capacity check: {testedSpeakers}
   simultaneous speakers passed." or its failure reason, or "Capacity check not run
   yet.".
5. `notes` from `SettingsSync`, verbatim.
6. "Requirements are provisional estimates." when `measurements == "provisional"`.

**Parsing** (`parse_values`). Unknown keys are ignored. `engine` is the raw stored
string stripped, or `None` when missing or empty. Checkboxes are true when the
stored string, lowercased, is in `TRUE_VALUES` (also accept Python `True`). The
exact string Nextcloud stores for a checkbox is not pinned by AppAPI (section 14),
so this tolerance is required.

The disabled state cannot be shown by the form itself: an ineligible option stays
clickable. `SettingsSync` rejects it within one poll, writes the active engine
back and explains why in the status text (8.4). The administrator sees the result
after reloading the page.

## 12. Test plan

All unit tests use fakes. Real models appear only in tests marked `integration`.

### 12.1 Shared fakes

* `tests/fakes_engine.py` (A): `FakeRecognizer` (create_stream, accept_waveform,
  is_ready, decode_streams, get_result, input_finished, set_option; it emits one
  word per 0.56 s of non-zero audio, and an `<de-DE>` tag in auto mode),
  `FakeHostProcess` (runs `engine_host.serve` in-process over asyncio pipes, or
  scripted replies), and `FakeBackend` (Backend protocol, no process).
* `tests/fakes_models.py` (B): `make_archive(tmp, files, extra_members=())` builds a
  small `.tar.bz2`. `FakeOpener` serves it with Content-Length, Range, If-Range,
  scripted 200/206/404, and stalls. `fake_catalog(...)` gives pins for the tiny
  files.

### 12.2 Owner A

* `test_engine_host.py`: `serve()` with `FakeRecognizer`:
  * one terminal reply per request;
  * 10 interleaved streams demultiplexed;
  * `commit` keeps the stream and returns only new text; `finish` frees it;
  * `cancel` during an outstanding audio request;
  * oversize, odd-length and unknown-id inputs give per-stream errors without
    exiting;
  * batch never exceeds `max_batch`;
  * `<de-DE>` stripped and reported as `de`;
  * EN ignores the language option;
  * a fatal error exits 1;
  * rotation after 30 minutes;
  * `per_segment` mode.
* `test_shared_engine.py`:
  * slot FIFO, timeout, the cancellation race (port `test_scheduling.py`);
  * `load_check` refusal prevents the spawn; `resource_check` after load closes
    the host;
  * a per-stream error does not restart the host; host exit restarts it; three
    exits in 60 s open the circuit;
  * a request timeout counts as a host failure;
  * close during start (generation fence);
  * `is_idle`, `status()` keys, `max_streams`.
* `test_stream_transcriber.py`, using `FakeBackend` or `SharedEngine` with a fake
  process:
  * a waiting speaker never preempts an open segment; it gets a slot at segment end
    or drops after 2 s;
  * the stream stays open across two segments (one `open`, two `commit`s);
  * a gap gives `finish` and a new stream;
  * end of track gives `finish`;
  * `abort()` cancels without a host restart;
  * auto-language results give `Transcript.lang_id == "de"`;
  * `set_language` restarts the track with the new option;
  * staleness drop after 4 s.
* Whistle regression: the existing `test_streaming`, `test_speech`,
  `test_scheduling`, `test_pause_boundaries`, `test_webrtc`, `test_transport` and
  `test_diagnostics` pass **unchanged**. Add `test_worker_pool_backend_members`
  (busy, is_idle, resize refuses while open, transcriber returns a
  `WhistleTranscriber` bound to the pool, `status()["engine"]`). Add a test that
  `transcriber.create` dispatches on `POOL`. Add a test that calibration calls
  `abort()`.
* `test_engine_integration.py` (`integration`): see 12.6.

### 12.3 Owner B

* `test_catalog.py`:
  * for W 1-4 and cpus in {0.5, 1, 2, 3, 4, 7.5, 8, 40}, `memory_floor_mib`,
    `worker_ceiling` and `load_headroom_mib` for whistle equal today's literals;
  * nemotron ceiling is 1, 2, 3, 4, 4 at 1, 2, 3, 4, 8 CPUs; floor 2560;
  * every pin is a 64-hex string; sizes are positive; `extracted_bytes` sums;
  * each `License.text_file` exists in the repository; the NVIDIA notice equals
    the exact string, and so does the content of
    `LICENSES/NVIDIA-Open-Model-License-NOTICE.txt`.
* `test_model_store.py`:
  * happy path (final directory, manifest, license copies, partial removed,
    progress monotonic);
  * Content-Length mismatch;
  * resume with 206; a server ignoring Range (200 restart); If-Range mismatch;
  * cancel keeps the partial file; `discard=True` removes it;
  * disk precheck refusal (fake `disk_usage`); `root=None` gives `storage`;
  * a wrong hash leaves no final directory and removes the partial file;
  * members `../x`, `/abs`, a symlink or hardlink or device named like a pinned
    file, a duplicate member, a wrong size, a missing file;
  * an extra `test_wavs/` member is skipped;
  * replacing an existing install leaves the old version intact when the new one
    fails;
  * startup cleanup of `.staging-*` and `.old-*`;
  * a concurrent download gives `busy`;
  * `verify(deep)` detects a flipped byte; `state()` stays cheap (no hashing,
    asserted with a spy).
* `test_eligibility.py`: table-driven `HostFacts` → reasons for each row of
  section 5; Whistle is never ineligible; `network: unknown` does not disable;
  disk and network are ignored when the model is ready; no AVX2 requirement on
  aarch64; `host_facts` with a fake reader and fake cpuinfo.
* Optional `integration` test: hash the reference copies in
  `local/stt-eval/models/` against the catalog. This only reads files and loads no
  model, so it needs no capped scope.

### 12.4 Owner C

* `test_settings.py`: atomic write, size cap, invalid JSON or an unknown engine
  gives `ValueError`, round-trip of acknowledgments, in-memory mode.
* `test_engine_switch.py`. Use the real `SafetyController` with a fake reader and
  `FakeBackend`, as `test_recovery.py` does, plus a fake store and fake facts.
  Cases:
  * whistle → nemotron-en happy path (state sequence, settings persisted,
    `transcriber.POOL` replaced, rooms closed through `service.stop_rooms`);
  * ineligible gives 400 with reasons; no license gives 400; busy gives 409;
    disabled gives 409;
  * a download failure gives failed while the old pool was never closed;
  * a capacity failure gives a rollback to whistle with `rolledBack` true; a
    rollback failure gives `rolledBack` false;
  * cancel in every state;
  * disable during checking (no restart, previous persisted);
  * restore with pending; restore with a corrupt model or an ineligible host
    (falls back to whistle and persists whistle); restore whose first outcome
    fails (rollback); a later trip does not roll back;
  * delete refusals.
* `test_resources.py` / `test_recovery.py` / `test_safety.py` (additive):
  * Whistle `budget_problem` and `recovery_problem` give results identical to
    today on the existing fixtures;
  * nemotron refused at 2 GiB and accepted at 3 GiB, with a ceiling;
  * `load_problem`;
  * `replace_pool` refuses while monitoring;
  * `start(fresh=True)` ignores the cooldown but not the budget;
  * `wait_outcome` for ready, trip and blocked;
  * `close(reason)`;
  * auto-size through `resize`.
* `test_capacity.py`: `is_idle` is used, and the report has `engine`.
* `test_settings_sync.py` (new). A fake Nextcloud object with
  `appconfig_ex.get_values/set_value/delete` over a dict and `ui.settings.register_form`
  recording the forms; a fake manager (or the real `EngineManager` with the fakes
  above); a fake clock. Call `poll_once()` and `publish()` directly:
  * the first poll writes back a stale stored value and starts no switch;
  * a new value calls `request_switch(engine, accept_license, user="")`;
  * a refusal calls `note_refusal`, writes the active engine back and republishes;
  * license order: the engine first, the box 5 s later → one switch; the box never
    ticked → refusal after `LICENSE_GRACE_S`, value written back;
  * selecting the previous engine while downloading calls `cancel`; while
    `rolling_back` it does not; selecting a third engine while busy writes the
    target back;
  * reconcile after a failed switch writes the active engine back;
  * cleanup box → `delete_unused_models` and `delete(CLEANUP_FIELD)`;
  * publishing: identical forms are not re-sent; many `notify()` calls within
    `PUBLISH_MIN_S` give one publish; a trailing publish happens; progress below the
    next 5% step does not publish;
  * an OCS exception switches to `BACKOFF_S` and never propagates;
  * `stop(final=...)` publishes the final note once.
* `test_app.py` (additive): `enabled_handler` no longer calls the `ui/top-menu` or
  `ui/script` OCS endpoints; it removes the legacy entries best effort;
  `/ui/live.js`, `/ui/live.css`, `/img/app.svg` return 404; `/capabilities` and
  `/api/v1/languages` per engine; `transcribe` with `langId=de` on EN gives 400;
  `/status.engine`; `/capacity/check` gives 409 while busy. The existing lifespan
  tests (injected pool, fake Nextcloud without `ui`) pass unchanged, which proves
  that no sync starts without production wiring.
* `scripts/check_manifest.py` (run by a test or by CI as today): `status` is the
  only public route; no `call/` route; no `ui/` or `img/` route.

### 12.5 Owner D

* `tests/test_admin_form.py` (new), pure, table-driven over panel dicts built from
  the 8.3 shape:
  * the form has `id`, `section_id="ai"`, `section_type="admin"`, the three field ids,
    and `to_dict()` is accepted by `SettingsForm.from_dict` round-trip;
  * every row of the label table in section 11; labels are unique and at most
    `MAX_LABEL`; an ineligible card's label contains "— unavailable: " and its
    first reason;
  * the radio default is the switch target while busy, else the active engine;
  * progress 0-100 gives labels that change only at multiples of 5;
  * determinism: two builds from equal inputs give equal `to_dict()`;
  * the cleanup field appears only when something is removable;
  * `status_text` covers ready, paused, admin action, capacity passed/failed,
    succeeded, failed with and without rollback, refusal with reasons, restore
    `lastError`, notes; at most `MAX_TEXT`; contains no "Powered by NVIDIA";
  * `parse_values`: missing rows give `None`/false; "1", "true", "TRUE", "on",
    `True` are true; "0", "", "false" are false; unknown keys ignored.
* `cassini_live/static/` no longer exists, and no file under `cassini_live/` or
  `docs/` (except this proposal) mentions `live.js`, `live.css`, `top-menu` or
  `top menu` (a grep test; the legacy cleanup in `app.py` uses `top_menu`, which the
  grep does not match).
* Manual check on a Nextcloud 33 test instance with fakes (no real model): the
  form shows under `/settings/admin/ai` for an admin and not for a user; selecting
  an ineligible engine reverts within one poll and the reason appears after a
  reload; the license-order flow; light and dark themes. Record how the
  description renders line breaks and what string a checkbox stores, and report it
  (section 14).

### 12.6 Real-model integration tests (owner A runs them; never in the default run)

`tests/test_engine_integration.py`, marked `integration`. It skips unless both
hold:

* `CASSINI_TEST_MODEL_DIR` points to a pinned model directory;
* the process's own cgroup has `memory.max <= 3 GiB` and `memory.swap.max == 0`.
  Read `/proc/self/cgroup`, then `/sys/fs/cgroup/<path>/memory.max`.

Run one model per invocation, and never more than two at once:

```sh
CASSINI_TEST_MODEL_DIR=local/stt-eval/models/sherpa-onnx-nemotron-speech-streaming-en-0.6b-560ms-int8-2026-04-25 \
  systemd-run --user --scope -p MemoryMax=3G -p MemorySwapMax=0 \
  uv run --frozen pytest -m integration tests/test_engine_integration.py -q
```

Checks:

* two streams on one host (speech and silence);
* fixture WER <= 0.25 against `jfk.txt`;
* `commit` versus `finish` text on the fixture: report the difference; if it is
  above 2 words, switch the default to `per_segment`;
* the multilingual `LANGUAGE_OPTION` form, and the `auto` tag format;
* host RSS after load, reported;
* `/proc/<pid>/task` count <= threads + 4;
* the host pid is reaped on close;
* `calibration.scenario(SharedEngine, 1)` passes.

## 13. Documentation, notices and packaging

| File | Owner | Change |
|---|---|---|
| `LICENSES/NVIDIA-Open-Model-License.txt` | B | The full text from nvidia.com (version 2025-10-24), fetched once and stored as plain text with the source URL and fetch date at the top |
| `LICENSES/NVIDIA-Open-Model-License-NOTICE.txt` | B | Exactly `Licensed by NVIDIA Corporation under the NVIDIA Open Model License` plus a newline |
| `LICENSES/OpenMDW-1.1.txt` | B | The full text from openmdw.ai, with source and date |
| `LICENSES/LGPL-2.1.txt` | B | Needed because the sherpa-onnx wheel vendors `libasound` (alsa-lib, LGPL-2.1-or-later), plan §7.2 |
| `NOTICE.md` | B | New sections: sherpa-onnx 1.13.8 (Apache-2.0) and its bundled ONNX Runtime (MIT) and alsa-lib (LGPL-2.1+, with the exact version and a source URL found in `sherpa_onnx.libs`); both models (HF repo, sherpa export asset name, file SHA-256 values, license); the statement that model weights are **not** in the image, are downloaded on an administrator's request from the sherpa-onnx GitHub release, and are stored with their license and notice next to them. Change "Whistle adapter" to "speech-engine adapter". |
| `pyproject.toml`, `uv.lock` | C | `sherpa-onnx==1.13.8`; `uv lock`; keep `cactus-needle`; integration marker text: "requires real Whistle weights or a Nemotron model directory". **Check that the lock resolves a binary manylinux wheel for cp312 on x86_64 and aarch64.** The local cache shows a locally built wheel. If PyPI has only an sdist for a platform, report it; do not add a compiler to the image. |
| `Dockerfile` | C | No model is baked. Add `CASSINI_ENGINE_THREADS=1` to `ENV`. Everything else stays. The image grows by the wheel only (about 43 MB). |
| `appinfo/info.xml` | C | Remove the `ui/` and `img/` routes (8.1); summary "Private live captions for Nextcloud Talk, with a choice of speech engines."; the description names Whistle (bundled, 7 languages) and the optional Nemotron engines (downloaded on request, their licenses, about 3 GiB container and 6 GiB host memory); `CASSINI_LIVE_WORKERS` description adds "With Nemotron: maximum simultaneous speakers". No version bump in this change. |
| `README.md` | D | "Where it stands": engine choice in the administration settings (no top-menu page any more), memory needs per engine, model licenses named separately from AGPL |
| `docs/install.md` | D | New section "Choose a speech engine" (anchor `#choose-a-speech-engine`, used as the form's `doc_url`): where it lives (Administration settings → Artificial intelligence → Cassini Live), that the page must be reloaded to see progress, that an unavailable choice is reverted with the reason in the status text, the license-box order, download size, persistent storage, example `docker update --cpus=4 --memory=3g --memory-swap=3g`, host >= 6 GiB, rollback behavior, removing a model with the "Remove downloaded models" box. Remove every mention of the Cassini Live top-menu page |
| `docs/capacity.md` | D | Per-engine rules table generated from the catalog numbers in section 3.3; the speaker ceiling; no handoffs on Nemotron; capacity check per engine |
| `docs/architecture.md` | D | Engine host diagram (plan §1.1), stream protocol, the Whistle path unchanged |
| `docs/privacy.md` | D | Admin-initiated model download and the network probe go to github.com (and objects.githubusercontent.com) once when the service is enabled and when an administrator chooses an engine. Settings store the license version and time of the acknowledgment, not a user id (AppAPI does not tell the ExApp who saved the form). The ExApp polls its own app-config values in Nextcloud every 3 s. |
| `docs/diagnostic-video.md` | D | With Nemotron, "workers" are speaking slots |

## 14. Open questions and risks

* **Commit pad.** Whether a 0.56 s soft pad without `input_finished` flushes the
  last words is unverified. It is decided by the integration test, with
  `per_segment` as the fallback.
* **Batched memory.** About 40 MiB per stream in a batch (baseline). That is why
  `max_batch = 4` and the 2560 MiB floor. If specs.md shows otherwise, update the
  catalog only.
* **Talk and an English-only language list.** We keep `auto` in the EN list because
  Talk defaults to it. Verify against Talk 33-35 that a room language of `de` gets a
  clean error.
* **The `sherpa-onnx` wheel** may not exist as a binary for every platform (see 13).
* **AVX2 threshold** is provisional. No AVX2-only measurement exists.
* **License acceptance** by an administrator through a checkbox is a product
  choice, not legal review. On-demand download means the image does not
  redistribute NVIDIA weights. The maintainers should still confirm the wording.
* **Talk language caching.** If Talk caches `supported_languages`, a room may keep
  offering languages that `nemotron-en` refuses until the cache expires; the
  transcribe endpoint's 400 then applies. Check on the test instance.
* **Settings section.** No ExApp-owned section exists in the settings navigation;
  the form goes in `ai`. If the user prefers Talk's section, change `SECTION_ID` to
  `talk` (registered by spreed); nothing else changes.
* **Checkbox storage and description rendering** are not pinned by AppAPI. The
  parser is tolerant (section 11). D reports the observed behavior from the manual
  check.
* **Staleness of the form.** Declarative forms do not update live. An
  administrator sees progress and the result of a rejection only after reloading
  the page. The status text starts with "Reload this page to see progress." while a
  switch is active (section 11).
* **Polling cost.** One small OCS request every 3 s while enabled. Acceptable; raise
  `POLL_S` if Nextcloud logs complain.
* **`^status$` proxy route.** No browser page reads it any more. It is kept
  because removing it is not part of this decision. Dropping it later reduces what
  ordinary users can see (host limits, active rooms).
* **Restore policy:** a failed engine falls back to whistle and is **not** retried
  automatically. This avoids 1 GB load loops across restarts.

## 15. Work order

1. Step 0, all owners, the first action: stubs of the public modules with the exact
   signatures (A: `engines/__init__.py`, `engines/shared.py`, `transcriber.create`;
   B: `catalog.py` with the full table, `model_store.py`, `eligibility.py`;
   C: `settings.py`, `engine_switch.py`, `settings_sync.py`; D: `admin_form.py`).
2. Parallel implementation against fakes. C tests the manager with `FakeBackend`
   (from A) or its own minimal fake, until A's file lands.
3. C wires `app.py` last, then runs the full unit suite.
4. A runs the capped integration tests, one model at a time, and reports the
   commit-pad result and the language option form. B updates the catalog numbers
   if `specs.md` exists.
5. D builds `admin_form.py` against panel dicts written by hand from 8.3, then
   checks the labels and status text against `manager.panel()` from C's tests, and
   does the manual check of 12.5 with fakes; no real model.
6. C wires `SettingsSync` into `app.py` after `admin_form.py` exists (D's step 0
   stub: the constants, `Selection`, and the four function signatures).
