"""Download the pinned MIT-licensed VAD at build/setup time, never on a call."""

import hashlib
import sys
from pathlib import Path
from urllib.request import urlopen

REVISION = "63d4a2a7bfce8cd9bb39ed8fa0afcb5236d73e01"  # v6.3
SHA256 = "f6f4407b41e05e277e7f5cfd9b9ae957b1dfe763c6076a934734c0b448e09b31"
URL = f"https://raw.githubusercontent.com/snakers4/silero-vad/{REVISION}/src/silero_vad/data/silero_vad.onnx"


def main():
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "models")
    target.mkdir(parents=True, exist_ok=True)
    with urlopen(URL, timeout=60) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise RuntimeError("Speech detector checksum mismatch")
    (target / "silero_vad.onnx").write_bytes(data)


if __name__ == "__main__":
    main()
