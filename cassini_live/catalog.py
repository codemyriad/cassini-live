"""Speech engines, their requirements and model pins: the single source of every threshold.

Resources, recovery, config, safety and the admin panel read these numbers, so the panel
and the runtime cannot disagree. Standard library only.
"""

import math
from dataclasses import dataclass
from typing import Literal

EngineId = Literal["whistle", "nemotron-en", "nemotron-multilingual"]
DEFAULT_ENGINE: EngineId = "whistle"
MIB = 1024 * 1024
# Native streams on a shared engine host. A speaker's stream opens at its first admitted
# speech, stays open across short pauses and closes after StreamTranscriber.STREAM_IDLE_S
# (60 s) of silence, so this bounds the people who spoke in the last minute, not everyone
# in the call: a 13th person speaking within the same minute is refused ("Too many open
# caption streams") until someone's stream idles out. It was safety.max_tracks (10) plus a
# margin of 2. Raising it raises the memory floor by stream_mib per stream (see _SHARED).
MAX_STREAMS = 12
# Memory is measured (local/stt-eval/resources/specs.md, 2026-10-15): resident 944-947 MiB
# after load (en), 957-965 MiB (multilingual), peak about 1.47 GiB with 6 streams. CPU comes
# from a harness at 1.3x speed, not yet from a Cassini capacity run, so it stays provisional.
# Shown on the settings page: when the requirements were measured and which figures are
# still estimates (engine cards mark the affected engines with requirements.provisional).
MEASURED_ON = "2026-10-15"
ESTIMATED = ("cpu",)

RELEASE_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
LANGUAGES = ("en", "de", "fr", "es", "it", "nl", "pl")


@dataclass(frozen=True)
class ModelFile:
    name: str
    sha256: str
    size: int


@dataclass(frozen=True)
class ModelAsset:
    archive: str
    url: str
    archive_bytes: int  # exact Content-Length; a changed asset is refused
    archive_sha256: str  # checked before tarfile reads a byte of the archive
    directory: str  # top-level directory inside the tar
    files: tuple[ModelFile, ...]

    @property
    def extracted_bytes(self) -> int:
        return sum(f.size for f in self.files)


@dataclass(frozen=True)
class License:
    id: str
    name: str
    url: str
    version: str  # license text version shown to administrators
    text_file: str  # relative to the repository root
    notice: str | None  # exact notice text that must travel with the weights
    requires_acknowledgement: bool
    summary: tuple[str, ...]


@dataclass(frozen=True)
class Requirements:
    min_memory_mib: int
    recommended_memory_mib: int
    min_cpu_quota: float
    cpu_flags: tuple[str, ...]  # required /proc/cpuinfo flags on x86_64
    base_mib: int
    model_mib: int
    stream_mib: int
    batch_mib: int
    max_batch: int
    start_timeout_s: float
    provisional: bool


@dataclass(frozen=True)
class Engine:
    id: EngineId
    name: str
    kind: Literal["worker_pool", "shared_host"]
    summary: str
    languages: tuple[str, ...]
    automatic_language: bool  # False: "auto" is accepted but means languages[0]
    meeting_wer: float | None  # on our English standup windows
    accuracy_note: str
    asset: ModelAsset | None  # None: bundled in the image
    license: License
    requirements: Requirements


def _asset(archive, sha256, files):
    return ModelAsset(
        archive=archive + ".tar.bz2",
        url=RELEASE_URL + archive + ".tar.bz2",
        archive_bytes=files[0],
        archive_sha256=sha256,
        directory=archive,
        files=tuple(ModelFile(*f) for f in files[1:]),
    )


NEMOTRON_EN_ASSET = _asset(
    "sherpa-onnx-nemotron-speech-streaming-en-0.6b-560ms-int8-2026-04-25",
    "78e2b79fcf7271553a74402a76b771b09ea40117a39566a79f52235b23db6358",
    (
        463945051,
        ("encoder.int8.onnx",
         "7d932213491ad355c6e5576705dc3494731a52af87d7a1b954559340147909d8", 652916849),
        ("decoder.int8.onnx",
         "0be9702c2f427a2b6bb241d298e0d3836a558de1f5b9fd3018f1cce6e2b3fa98", 7257753),
        ("joiner.int8.onnx",
         "a35eac38a22ebceb04d230ed7afe0d68f446ba6914a036b97f14fece95967e23", 1735862),
        ("tokens.txt",
         "dc0b4584ab2e4ddbf888425c076c61b736e7356a015250db7d307e6f1a8188ff", 8952),
    ),
)
NEMOTRON_MULTILINGUAL_ASSET = _asset(
    "sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11",
    "c6bf5e0df765f9d5b43bc9e0536d4b4b3e7d40bdf5ecf13e45f134c51c05ae3a",
    (
        475271763,
        ("encoder.int8.onnx",
         "012e9321373af99021415e0b0eb3ec827b4be3153be6f30d9b448fe65e896e68", 657601403),
        ("decoder.int8.onnx",
         "19f9c98fc6d0a2c33a65a43b36fdb2e914c26c0aa9764be3aebc502a1e982fb0", 14978075),
        ("joiner.int8.onnx",
         "4101c7c679a0bc30483794b27a059e34e79232aa2068d78d51231a22c8b0d7ce", 9504438),
        ("tokens.txt",
         "729cc103155bafa785f9cd45746cd41cabe97eab7182fc04d594129587958f8a", 131440),
    ),
)

APACHE = License(
    id="Apache-2.0",
    name="Apache License 2.0",
    url="https://www.apache.org/licenses/LICENSE-2.0",
    version="2.0",
    text_file="LICENSES/Apache-2.0.txt",
    notice=None,
    requires_acknowledgement=False,
    summary=("Bundled with Cassini Live.",),
)
NVIDIA_NOTICE = "Licensed by NVIDIA Corporation under the NVIDIA Open Model License"
NVIDIA_OML = License(
    id="NVIDIA-OML",
    name="NVIDIA Open Model License",
    url="https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-license/",
    version="2025-10-24",
    text_file="LICENSES/NVIDIA-Open-Model-License.txt",
    notice=NVIDIA_NOTICE,
    requires_acknowledgement=False,
    summary=(
        "Free for commercial and non-commercial use.",
        "NVIDIA may update the license; later versions apply to continued use.",
        "It ends if you bypass the model's safety guardrails or sue over the model.",
        "You are responsible for the captions it produces.",
        "Use must follow NVIDIA's Trustworthy AI terms and export rules.",
        "You agree to indemnify NVIDIA for claims arising from your use or distribution.",
        "Captions you produce are not claimed by NVIDIA.",
        "Not legal advice. Read the full text.",
    ),
)
OPENMDW = License(
    id="OpenMDW-1.1",
    name="OpenMDW License 1.1",
    url="https://openmdw.ai/license/1-1/",
    version="1.1",
    text_file="LICENSES/OpenMDW-1.1.txt",
    notice=(
        "nvidia/nemotron-3.5-asr-streaming-0.6b by NVIDIA Corporation, exported to ONNX by "
        "the k2-fsa/sherpa-onnx project. Licensed under OpenMDW-1.1."
    ),
    requires_acknowledgement=False,
    summary=(
        "Permissive: no field-of-use limits, no copyleft.",
        "Keep the license and origin notices with the model files.",
        "Rights end if you start patent or copyright litigation over the model.",
    ),
)

# Shared-host floor: (base + model + stream * MAX_STREAMS + batch * max_batch) / 0.7, rounded
# up to 256 MiB: (192 + 1024 + 16*12 + 45*4) / 0.7 = 2269 -> 2304. The 0.7 covers a cold host
# restart, which briefly charges the model's page cache: about 1678 MiB = 73% (en) and 75%
# (multilingual), below the 80% trip. A 2048 floor would trip there. Host >= 2 x 2304 MiB.
_SHARED = dict(
    recommended_memory_mib=3072,
    min_cpu_quota=1.0,
    cpu_flags=("avx2",),  # provisional: no AVX2-less measurement exists
    base_mib=192,  # supervisor measured at 123-155 MiB
    stream_mib=16,  # provisional; about 7 measured serially
    batch_mib=45,  # baseline: batched activation arena
    max_batch=4,
    start_timeout_s=120,  # cold disk; 3-5 s warm
    provisional=True,
)

ENGINES: dict[str, Engine] = {
    "whistle": Engine(
        id="whistle",
        name="Whistle",
        kind="worker_pool",
        summary="Light and bundled. Seven languages with automatic detection.",
        languages=LANGUAGES,
        automatic_language=True,
        meeting_wer=0.19,
        accuracy_note="Word error rate on our recorded English meetings; lower is better.",
        asset=None,
        license=APACHE,
        requirements=Requirements(
            min_memory_mib=576,  # memory_floor_mib at one worker
            recommended_memory_mib=2048,
            min_cpu_quota=2.0,  # quota stalls at 1 CPU
            cpu_flags=(),
            base_mib=384,
            model_mib=192,  # per worker process
            stream_mib=0,
            batch_mib=0,
            max_batch=1,
            start_timeout_s=30,
            provisional=False,
        ),
    ),
    "nemotron-en": Engine(
        id="nemotron-en",
        name="Nemotron English",
        kind="shared_host",
        summary="Most accurate. English only.",
        languages=("en",),
        automatic_language=False,
        meeting_wer=0.089,
        accuracy_note="Word error rate on our recorded English meetings; lower is better.",
        asset=NEMOTRON_EN_ASSET,
        license=NVIDIA_OML,
        requirements=Requirements(min_memory_mib=2304, model_mib=1024, **_SHARED),
    ),
    "nemotron-multilingual": Engine(
        id="nemotron-multilingual",
        name="Nemotron Multilingual",
        kind="shared_host",
        summary="More accurate than Whistle. Seven languages with automatic detection.",
        languages=LANGUAGES,
        automatic_language=True,
        meeting_wer=0.128,
        accuracy_note="Word error rate on our recorded English meetings; lower is better.",
        asset=NEMOTRON_MULTILINGUAL_ASSET,
        license=OPENMDW,
        requirements=Requirements(min_memory_mib=2304, model_mib=1056, **_SHARED),
    ),
}


def get(engine_id: str) -> Engine:
    return ENGINES[engine_id]


def memory_floor_mib(engine_id: str, workers: int) -> int:
    engine = get(engine_id)
    if engine.kind == "worker_pool":
        return 384 + 192 * workers
    return engine.requirements.min_memory_mib


def worker_ceiling(engine_id: str, effective_cpus: float) -> int:
    engine = get(engine_id)
    if engine.kind == "worker_pool":
        return min(4, max(1, int(effective_cpus // 2)))
    # Two speakers per recognizer thread (0.8.0 Decision 2): the 1.3x harness failed every
    # 3-speaker cell at one thread and passed 4 at two, failing 5 (stt-eval results.md
    # 118-126, 133-141). Staging (2 CPUs, 1 thread) therefore seats 2 until a 1.0x run there
    # says otherwise. Admission still sheds on live lag below this ceiling.
    return min(4, 2 * engine_threads(effective_cpus))


def engine_threads(effective_cpus: float) -> int:
    """Recognizer threads for a shared host, leaving one CPU of the quota for the supervisor
    and ONNX Runtime's spin-waiting: threads equal to the quota stalled on staging (2 CPUs)."""
    return max(1, min(4, math.floor(effective_cpus + 1e-9) - 1))


def download_disk_bytes(engine_id: str, partial_bytes: int = 0) -> int:
    return asset_disk_bytes(get(engine_id).asset, partial_bytes)


def asset_disk_bytes(asset: ModelAsset | None, partial_bytes: int = 0) -> int:
    """Free space a download still needs: the rest of the archive, the files and a margin."""
    if asset is None:
        return 0
    return asset.archive_bytes - partial_bytes + asset.extracted_bytes + 256 * MIB
