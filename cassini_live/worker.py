"""Private stdin/stdout protocol. One process owns one native Whistle stream.

Whistle has process-global state and is not thread safe. Never share it between
speakers, even when each speaker has a separate Python Whistle instance.
"""

import array
import base64
import json
import os
import sys

os.environ["NEEDLE_TELEMETRY"] = "0"
os.environ["DO_NOT_TRACK"] = "1"


def emit(kind, **payload):
    print(json.dumps({"type": kind, **payload}, ensure_ascii=False), flush=True)


def audio_chunks():
    for line in sys.stdin:
        message = json.loads(line)
        if message["type"] == "end":
            return
        if message["type"] != "audio":
            raise ValueError("Expected audio or end")
        raw = base64.b64decode(message["pcm"], validate=True)
        if not raw or len(raw) % 4 or len(raw) > 64000:
            raise ValueError("Expected at most one second of float32 mono PCM")
        samples = array.array("f")
        samples.frombytes(raw)
        if sys.byteorder != "little":
            samples.byteswap()
        yield samples
    raise EOFError("Audio stream closed without end")


def main():
    import needle

    model = needle.Whistle(weights=sys.argv[1] if len(sys.argv) > 1 else None)
    emit("ready")
    for line in sys.stdin:
        command = json.loads(line)
        if command["type"] == "quit":
            return
        if command["type"] != "start":
            raise ValueError("Expected start")
        language = command.get("language")
        for result in model.stream(
            audio_chunks(), language=None if language == "auto" else language
        ):
            emit("result", result=result)
        emit("done")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Errors contain no audio or transcript data. A failed worker is replaced.
        emit("error", message=str(error))
        sys.exit(1)
