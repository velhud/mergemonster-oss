"""Start the installed headless CLI and verify its loopback health endpoint."""

from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> int:
    port = _free_port()
    env = os.environ.copy()
    temp_root = env.get("MERGEMONSTER_SMOKE_TEMP_DIR")
    with tempfile.TemporaryDirectory(
        prefix="mergemonster-cli-smoke-", dir=temp_root
    ) as workdir:
        env["MERGEMONSTER_HOME"] = str(Path(workdir) / ".mergemonster")
        process = subprocess.Popen(
            [sys.executable, "-m", "codemerge.cli", "--serve", "--port", str(port)],
            cwd=workdir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    output = process.stdout.read() if process.stdout else ""
                    raise RuntimeError(f"CLI server exited early ({process.returncode}):\n{output}")
                try:
                    with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.5) as response:
                        if response.status == 200:
                            print(f"Headless CLI served /api/health on loopback port {port}.")
                            return 0
                except (OSError, URLError):
                    time.sleep(0.15)
            output = process.stdout.read() if process.stdout else ""
            raise RuntimeError(f"CLI server did not become healthy:\n{output}")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Server smoke failed: {exc}", file=sys.stderr)
        raise
