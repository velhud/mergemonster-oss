"""Run the synthetic offline workflow through the installed headless API.

Use --scratch to place the disposable workspace and app state on a volume with
enough free space. The temporary run directory is removed on exit.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


EXAMPLES = Path(__file__).resolve().parent
SYNTHETIC_REPO = EXAMPLES / "synthetic-repository"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _call(base_url: str, route: str, *, method: str = "GET", token: str | None = None, payload: object = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["X-CodeMerge-CSRF"] = token
    req = Request(f"{base_url}{route}", data=body, headers=headers, method=method)
    with urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scratch",
        type=Path,
        required=True,
        help="existing temporary-data directory on a volume with adequate free space",
    )
    args = parser.parse_args()
    scratch = args.scratch.expanduser().resolve()
    _require(scratch.is_dir(), "--scratch must be an existing directory")
    executable = shutil.which("mergemonster")
    _require(executable is not None, "Install MergeMonster so the mergemonster command is on PATH")

    with tempfile.TemporaryDirectory(prefix="mergemonster-demo-", dir=scratch) as temp_name:
        run_root = Path(temp_name)
        repo = run_root / "synthetic-repository"
        shutil.copytree(SYNTHETIC_REPO, repo)
        subprocess.run(
            [sys.executable, str(repo / "make_binary_fixture.py")],
            check=True,
            cwd=repo,
            stdout=subprocess.DEVNULL,
        )
        state_home = run_root / "state"
        env = os.environ.copy()
        env["MERGEMONSTER_HOME"] = str(state_home)
        for name in ("GROQ_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL"):
            env.pop(name, None)

        port = _free_port()
        process = subprocess.Popen(
            [executable, "--serve", "--port", str(port)],
            cwd=run_root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base_url = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("MergeMonster server exited before becoming healthy")
                try:
                    health = _call(base_url, "/api/health")
                    if health.get("status") == "ok":
                        break
                except (OSError, URLError):
                    time.sleep(0.15)
            else:
                raise RuntimeError("MergeMonster server did not become healthy")

            session = _call(base_url, "/api/session")
            token = session["csrf_token"]
            scan = _call(
                base_url,
                "/api/scan",
                method="POST",
                token=token,
                payload={"path": str(repo), "show_all": True, "ignore_binary": True},
            )
            _require("files" in scan, f"scan failed: {scan}")
            present = {item["rel_path"].replace("\\", "/") for item in scan["files"]}
            required = {"src/service.py", "src/client.ts", "src/worker.go", "docs/overview.md"}
            _require(required <= present, f"scan omitted expected synthetic files: {sorted(required - present)}")
            binary_rows = [item for item in scan["files"] if item["rel_path"].replace("\\", "/") == "assets/tiny-fixture.bin"]
            if binary_rows:
                _require(binary_rows[0].get("category") == "BINARY", "binary fixture was not classified as binary")

            mode_plan = {
                "src/service.py": "SKELETON",
                "src/client.ts": "CODE",
                "docs/overview.md": "CODE",
                "src/invalid_syntax.py": "SKELETON",
                "src/settings.toml": "EXCLUDE",
            }
            for path, mode in mode_plan.items():
                if path in present:
                    result = _call(
                        base_url,
                        "/api/modes",
                        method="PUT",
                        token=token,
                        payload={"path": str(repo), "selected_paths": [path], "mode": mode},
                    )
                    _require(result.get("ok") is True, f"could not set {mode} for a demo file")

            selected = sorted(required | {"src/invalid_syntax.py"})
            preview_payload = {"path": str(repo), "selected_paths": selected}
            preview = _call(
                base_url,
                "/api/merge/preview",
                method="POST",
                token=token,
                payload=preview_payload,
            )
            _require(isinstance(preview.get("text"), str), f"preview failed: {preview}")
            first_text = preview["text"]
            _require("# SKELETON" in first_text, "merge preview did not render a structural view")
            _require("parser_status: error" in first_text and "Python syntax error" in first_text, "invalid Python did not produce an explicit skeleton diagnostic")
            _require("service.py" in first_text and "client.ts" in first_text, "preview omitted expected files")
            _require("settings.toml" not in first_text, "excluded file appeared in preview")
            _require(str(repo) not in first_text, "export included the temporary absolute workspace path")

            merged = _call(
                base_url,
                "/api/merge",
                method="POST",
                token=token,
                payload={**preview_payload, "output_file": "demo-context.txt", "create_json_manifest": True},
            )
            output = repo / "demo-context.txt"
            manifest_path = repo / "demo-context.txt.manifest.json"
            _require(output.is_file(), f"merge output was not created: {merged}")
            _require(manifest_path.is_file(), "merge did not create the requested JSON manifest")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_paths = [item["relative_path"] for item in manifest["merged_files"]]
            _require(manifest_paths == sorted(manifest_paths), "manifest paths are not sorted")
            _require(str(repo) not in manifest_path.read_text(encoding="utf-8"), "manifest leaked the temporary absolute path")
            _require("assets/tiny-fixture.bin" not in manifest_paths, "binary fixture was written into the context manifest")
            token_parity = preview.get("tokens_total") == merged.get("token_count")
            _require(token_parity, f"preview/writer token mismatch: {preview.get('tokens_total')} != {merged.get('token_count')}")

            service = repo / "src" / "service.py"
            original = service.read_text(encoding="utf-8")
            service.write_text(original + "\n    def version(self) -> str:\n        return \"demo-v2\"\n", encoding="utf-8")
            rescanned = _call(
                base_url,
                "/api/scan",
                method="POST",
                token=token,
                payload={"path": str(repo), "show_all": True, "ignore_binary": True},
            )
            _require("files" in rescanned, f"rescan failed: {rescanned}")
            refreshed = _call(
                base_url,
                "/api/merge/preview",
                method="POST",
                token=token,
                payload=preview_payload,
            )
            _require("version" in refreshed.get("text", ""), "updated skeleton did not reflect source change")
            _require(refreshed.get("text") != first_text, "source edit did not change rendered context")

            _require((repo / "assets" / "tiny-fixture.bin").is_file(), "binary fixture was not generated")
            _require(not str(repo) in output.read_text(encoding="utf-8"), "written export contains the temporary absolute workspace path")
            print(f"Server health: {health['status']}")
            print(f"Scanned synthetic files: {scan['count']}")
            print("Modes applied: service.py=skeleton, client.ts=code, overview.md=code, invalid_syntax.py=skeleton, settings.toml=exclude")
            print(f"Preview token estimate: {preview.get('tokens_total', 'unavailable')}")
            print(f"Preview/writer token parity: {token_parity}")
            print(f"Merged output bytes: {output.stat().st_size}")
            print(f"Manifest paths: {', '.join(manifest_paths)}")
            print(f"Source freshness: edit changed skeleton output ({first_text != refreshed['text']})")
            print("Export path check: no temporary absolute workspace path")
            print("Provider calls: none; provider credentials removed for this run")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    print("Temporary demo workspace and app state removed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Offline demo failed: {exc}", file=sys.stderr)
        raise
