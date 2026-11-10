"""Command-line entry point for MergeMonster."""

from __future__ import annotations

import argparse
import sys
from importlib.metadata import PackageNotFoundError, version


def _version() -> str:
    try:
        return version("mergemonster")
    except PackageNotFoundError:
        return "0.1.0"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mergemonster",
        description="Build precise, inspectable context packages from local repositories.",
        epilog=(
            "By default, launch the native desktop window. Use --serve for a local, "
            "loopback-only server without opening a browser."
        ),
    )
    parser.add_argument("--version", action="version", version=f"MergeMonster {_version()}")
    parser.add_argument(
        "--serve",
        action="store_true",
        help="serve the local UI/API without a desktop window or browser",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        metavar="PORT",
        help="loopback port for the desktop backend or --serve mode (default: 8765)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the desktop application or the explicit headless local server."""
    args = _parser().parse_args(argv)
    if not 1 <= args.port <= 65535:
        _parser().error("--port must be between 1 and 65535")

    if args.serve:
        # Keep help and version usable without importing the application runtime.
        from codemerge.core.tokens import assert_o200k_available

        assert_o200k_available()
        try:
            import uvicorn
        except ImportError as exc:  # pragma: no cover - declared dependency
            raise SystemExit("The local server requires uvicorn; reinstall MergeMonster.") from exc

        print(f"MergeMonster local server: http://127.0.0.1:{args.port}/", flush=True)
        uvicorn.run(
            "codemerge.jsui.server:app",
            host="127.0.0.1",
            port=args.port,
            reload=False,
            workers=1,
            log_level="warning",
        )
        return 0

    from .desktop import _pick_port, run_js

    run_js(_pick_port(args.port or 8765))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
