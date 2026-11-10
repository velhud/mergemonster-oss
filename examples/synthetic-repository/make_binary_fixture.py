"""Create a tiny binary file for manual scan demonstrations."""

from pathlib import Path


ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "assets" / "tiny-fixture.bin"


def main() -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_bytes(b"MMDEMO\x00\x01\x02\x03\xff\x00\x7f\x80\x00")
    print(f"Created {TARGET.relative_to(ROOT)} ({TARGET.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
