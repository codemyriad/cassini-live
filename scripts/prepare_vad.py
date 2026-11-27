"""Download the pinned MIT-licensed VAD at build/setup time."""

import sys
from pathlib import Path

from prepare_assets import Asset, download_cache, install

# Silero VAD v6.3, revision 63d4a2a7bfce8cd9bb39ed8fa0afcb5236d73e01.
SILERO = Asset(
    "silero_vad.onnx",
    "f6f4407b41e05e277e7f5cfd9b9ae957b1dfe763c6076a934734c0b448e09b31",
    2204103,
    "6f3f95fb26aa9f1b4418ff4f49463d6e6cf1ee0bd7a9f276ffe6d7b032435e78",
    1914337,
)


def main():
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "models")
    target.mkdir(parents=True, exist_ok=True)
    install(SILERO, target / SILERO.name, download_cache(target))


if __name__ == "__main__":
    main()
