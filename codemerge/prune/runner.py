from __future__ import annotations
from typing import Dict, List, Optional
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import shutil
import string


def run_with_coverage(
    pruned_dir: str,
    scripts: List[str],
    *,
    timeout_sec: int = 20,
    env: Optional[Dict[str, str]] = None,
) -> Dict:
    """
    Execute harness scripts in a subprocess with a tracer. This is not a sandbox.
    Returns trace dict: {executed: {file: [lines...]}, errors: [..], missing_modules:[..]}
    """
    pruned_dir = str(Path(pruned_dir).resolve())
    template = textwrap.dedent(r"""
    import sys, json, os, importlib, traceback
    ROOT = $ROOT
    executed = {}

    # block network
    try:
        import socket
        class _NoNet:
            def __init__(self, *a, **k): raise RuntimeError("network disabled")
        socket.socket = _NoNet
    except Exception:
        pass

    def _tracer(frame, event, arg):
        if event != 'line': return _tracer
        fn = frame.f_code.co_filename
        try:
            if fn and fn.startswith(ROOT):
                ln = frame.f_lineno
                executed.setdefault(fn, set()).add(ln)
        except Exception:
            pass
        return _tracer

    sys.settrace(_tracer)

    errors = []
    missing = []
    def _run(code):
        try:
            glb = {}
            exec(code, glb, glb)
        except ModuleNotFoundError as e:
            missing.append(getattr(e, 'name', str(e)))
            errors.append("ModuleNotFoundError: " + repr(e))
        except Exception as e:
            errors.append(type(e).__name__ + ": " + repr(e) + "\n" + traceback.format_exc())

    SCRIPTS = $SCRIPTS
    for c in SCRIPTS:
        _run(c)

    out = {
        "executed": {k: sorted(list(v)) for (k,v) in executed.items()},
        "errors": errors,
        "missing_modules": sorted(list(set(missing))),
    }
    sys.stdout.write(json.dumps(out))
    """)

    trace_py = string.Template(template).substitute(
        ROOT=repr(pruned_dir),
        SCRIPTS=repr(scripts),
    )

    tmp_dir = Path(tempfile.mkdtemp(prefix="codemerge_run_"))
    pyfile = tmp_dir / "runner.py"
    pyfile.write_text(trace_py, encoding="utf-8")
    try:
        inherited_keys = ("PATH", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP")
        env2 = {key: os.environ[key] for key in inherited_keys if key in os.environ}
        env2.update(env or {})
        env2["PYTHONPATH"] = pruned_dir
        res = subprocess.run(
            [sys.executable, str(pyfile)],
            cwd=pruned_dir,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=env2,
        )
        out = res.stdout.strip() or "{}"
        try:
            data = json.loads(out)
        except Exception:
            data = {"executed": {}, "errors": ["UNPARSEABLE_OUTPUT"], "missing_modules": []}
        if res.returncode != 0:
            data.setdefault("errors", []).append(f"RC={res.returncode}")
        return data
    finally:
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass
