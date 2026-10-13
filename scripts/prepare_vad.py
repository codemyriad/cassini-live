"""Download the pinned MIT-licensed VAD at build/setup time, never on a call."""

import hashlib
import sys
from pathlib import Path
from urllib.request import urlopen

REVISION = "7e30209a3e901f9842f81b225f3e93d8199902b1"
SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"
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
