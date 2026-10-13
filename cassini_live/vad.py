"""Offline Silero VAD: shared CPU model, independent recurrent state per track.

The input/state contract follows upstream Silero v6.2.1's ONNX wrapper.
Only the immutable inference session is shared. No downloads at runtime.
"""

import os
from functools import lru_cache
from pathlib import Path

import numpy as np

# Must precede the import: recent ORT builds initialize telemetry at import time.
os.environ["ORT_DISABLE_TELEMETRY"] = "1"
import onnxruntime as ort  # noqa: E402

ort.disable_telemetry_events()


def model_path():
    configured = os.environ.get("CASSINI_VAD_MODEL")
    if configured:
        return Path(configured)
    baked = Path("/opt/whistle/silero_vad.onnx")
    return (
        baked if baked.is_file() else Path(__file__).resolve().parents[1] / "models/silero_vad.onnx"
    )


@lru_cache(maxsize=1)
def session():
    path = model_path()
    if not path.is_file():
        raise RuntimeError(
            "Speech detector is missing; run scripts/prepare_vad.py or rebuild image"
        )
    options = ort.SessionOptions()
    options.inter_op_num_threads = 1
    options.intra_op_num_threads = 1
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])


class SpeechDetector:
    frame_size = 512

    def __init__(self):
        self.session = session()
        self.reset()

    def reset(self):
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, 64), dtype=np.float32)

    def probability(self, frame):
        samples = np.concatenate((self.context, frame.reshape(1, -1)), axis=1)
        output, self.state = self.session.run(
            None,
            {
                "input": samples,
                "state": self.state,
                "sr": np.array(16000, dtype=np.int64),
            },
        )
        self.context = samples[:, -64:].copy()
        return float(output[0, 0])
