"""Cheap per-track speech gating before leasing an expensive recognizer.

Keep 320 ms before speech and 608 ms after it. Cap a recognition segment at
15 seconds, preferring a pause after 14 seconds. Mark shorter pauses in the
existing one-second buffer for bounded worker handoffs under contention.
No audio leaves memory. Silero consumes 32 ms of 16 kHz floating-point PCM.
"""

from collections import deque
from dataclasses import dataclass, field
from time import monotonic

import numpy as np

from .vad import SpeechDetector


@dataclass
class SpeechChunk:
    samples: np.ndarray
    end: bool = False
    sequence: int = 0
    created_at: float = field(default_factory=monotonic)
    pause_cut: int | None = None
    end_reason: str = "flush"


class SpeechGate:
    def __init__(self, detector=None):
        self.detector = detector or SpeechDetector()
        self.frame_size = getattr(self.detector, "frame_size", 320)
        self.tail = np.empty(0, dtype="<f4")
        self.pre = deque(maxlen=5120 // self.frame_size)
        self.votes = deque(maxlen=3)
        self.active = False
        self.quiet = 0
        self.duration = 0
        self.buffer = np.empty(0, dtype="<f4")
        self.sequence = 0
        self.pause_cut = None

    def _chunk(self, samples, end=False, reason="flush"):
        cut = self.pause_cut
        chunk = SpeechChunk(
            samples,
            end,
            self.sequence,
            pause_cut=cut if cut is not None and 0 < cut < len(samples) else None,
            end_reason=reason,
        )
        if cut is not None:
            self.pause_cut = cut - len(samples) if cut > len(samples) else None
        self.sequence += 1
        return chunk

    def feed(self, samples):
        self.tail = np.concatenate((self.tail, np.asarray(samples, dtype="<f4")))
        chunks = []
        while len(self.tail) >= self.frame_size:
            frame, self.tail = self.tail[: self.frame_size].copy(), self.tail[self.frame_size :]
            if hasattr(self.detector, "probability"):
                probability = self.detector.probability(frame)
                voiced = probability >= (0.35 if self.active else 0.5)
            else:  # Deterministic detector injection for transport tests.
                pcm = (np.clip(frame, -1, 1) * 32767).astype("<i2").tobytes()
                voiced = self.detector.is_speech(pcm, 16000)
            if not self.active:
                self.pre.append(frame)
                self.votes.append(voiced)
                if sum(self.votes) < 2:
                    continue
                self.active = True
                self.buffer = np.concatenate(list(self.pre))
                self.duration = len(self.buffer)
                self.pre.clear()
                self.votes.clear()
            else:
                self.buffer = np.concatenate((self.buffer, frame))
                self.duration += len(frame)
            self.quiet = 0 if voiced else self.quiet + 1
            quiet_samples = self.quiet * self.frame_size
            # A sustained VAD pause, not a single low-energy consonant. Keep
            # its centre in the existing buffer; never add lookahead or replay.
            if quiet_samples >= 2048:
                self.pause_cut = len(self.buffer) - 1024
            reason = None
            if quiet_samples >= 9600:
                reason = "silence"
            elif self.duration >= 14 * 16000 and quiet_samples >= 2048:
                reason = "durationPause"
            elif self.duration >= 15 * 16000:
                reason = "durationLimit"
            while len(self.buffer) > 16000:
                chunks.append(self._chunk(self.buffer[:16000]))
                self.buffer = self.buffer[16000:]
            if reason:
                chunks.append(self._chunk(self.buffer, end=True, reason=reason))
                self.buffer = np.empty(0, dtype="<f4")
                self.active = False
                self.quiet = 0
                self.pause_cut = None
            elif len(self.buffer) >= 16000:
                chunks.append(self._chunk(self.buffer[:16000]))
                self.buffer = self.buffer[16000:]
        return chunks

    def flush(self):
        chunks = []
        if self.active:
            samples = np.concatenate((self.buffer, self.tail))
            # A partial input frame can take the tail just beyond one second.
            if len(samples) > 16000:
                chunks.append(self._chunk(samples[:16000]))
                samples = samples[16000:]
            chunks.append(self._chunk(samples, end=True))
        sequence = self.sequence
        if hasattr(self.detector, "reset"):
            self.detector.reset()
        self.__init__(self.detector)
        self.sequence = sequence
        return chunks
