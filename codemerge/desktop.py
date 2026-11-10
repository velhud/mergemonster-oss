"""Application entry point for launching the MergeMonster UI."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

def _pick_port(preferred: int = 8765) -> int:
    """Find an available port close to the preferred default."""
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", preferred))
        return preferred
    except OSError:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _ensure_default_directory() -> None:
    """Ensure a sensible default directory so the UI shows files on launch."""
    from codemerge.filemerger import AppConfig
    from codemerge.logger import get_logger

    logger = get_logger(__name__)
    try:
        cfg_mgr = AppConfig()
        cfg = cfg_mgr.get_config()
        if not cfg.get("last_directory") or not os.path.isdir(cfg.get("last_directory", "")):
            cwd = os.getcwd()
            logger.info("Setting default directory to current working directory: %s", cwd)
            cfg["last_directory"] = cwd
            cfg_mgr.update_config(cfg)
    except Exception:
        logger.warning("Failed to initialize default directory in config.")


def run_js(port: int) -> None:
    """Launch the modern JS/Webview UI."""
    from codemerge.core.tokens import assert_o200k_available
    from codemerge.logger import get_logger

    logger = get_logger(__name__)
    assert_o200k_available()
    logger.info("Launching Modern JS UI.")

    # Dependency checks
    try:
        import uvicorn  # type: ignore
        import fastapi  # type: ignore  # noqa: F401
    except ImportError:
        print("\nERROR: Missing dependencies for Web UI.")
        print("Reinstall MergeMonster to restore its server dependencies.\n")
        sys.exit(1)

    try:
        import webview  # type: ignore
    except ImportError:
        print("\nERROR: Missing dependency for Desktop Window.")
        print("Install MergeMonster with the desktop extra.\n")
        sys.exit(1)

    _ensure_default_directory()

    # Start Uvicorn server in a background thread
    url = f"http://127.0.0.1:{port}/"
    logger.info("Starting server at %s", url)

    config = uvicorn.Config(
        "codemerge.jsui.server:desktop_app",
        host="127.0.0.1",
        port=port,
        reload=False,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    # Wait for server readiness
    import http.client

    def _wait_ready(max_wait: float = 5.0) -> bool:
        deadline = time.time() + max_wait
        while time.time() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=0.3)
                conn.request("GET", "/api/health")
                resp = conn.getresponse()
                if resp.status == 200:
                    return True
            except Exception:
                time.sleep(0.1)
        return False

    if not _wait_ready():
        logger.error("Server failed to start within 5 seconds.")
        print("Error: Backend server did not start.")
        sys.exit(1)

    # PyWebview API bridge
    class AppApi:
        def __init__(self, port: int) -> None:
            self.port = port

        def browse_dir(self, initial: str | None = None):
            try:
                window = webview.windows[0] if webview.windows else None
                if not window:
                    return None
                result = window.create_file_dialog(
                    webview.FOLDER_DIALOG,
                    directory=initial or os.getcwd(),
                )
                if isinstance(result, (list, tuple)):
                    return result[0] if result else None
                return result
            except Exception:
                return None

        def browse_files(self, initial: str | None = None):
            try:
                window = webview.windows[0] if webview.windows else None
                if not window:
                    return None
                result = window.create_file_dialog(
                    webview.OPEN_DIALOG,
                    directory=initial or os.getcwd(),
                    allow_multiple=True,
                )
                if isinstance(result, (list, tuple)):
                    return list(result)
                if isinstance(result, str):
                    return [result]
                return None
            except Exception:
                return None

        def open_file_dialog(self, initial: str | None = None):
            return self.browse_files(initial)

        def save_file_dialog(self, initial: str | None = None):
            try:
                window = webview.windows[0] if webview.windows else None
                if not window:
                    return None
                directory = os.getcwd()
                save_filename = ""
                if isinstance(initial, str):
                    hint = os.path.expanduser(initial.strip())
                    if hint:
                        if os.path.isdir(hint):
                            directory = hint
                        else:
                            parent = os.path.dirname(hint)
                            if parent and os.path.isdir(parent):
                                directory = parent
                            save_filename = os.path.basename(hint) or ""
                result = window.create_file_dialog(
                    webview.SAVE_DIALOG,
                    directory=directory,
                    save_filename=save_filename,
                )
                if isinstance(result, (list, tuple)):
                    return result[0] if result else None
                return result
            except Exception:
                return None

        def copy_to_clipboard(self, text: str | None):
            try:
                if text is None:
                    return False
                if not isinstance(text, str):
                    text = str(text)
                # Prefer pyperclip if available.
                try:
                    import pyperclip  # type: ignore
                    pyperclip.copy(text)
                    return True
                except Exception:
                    pass
                if sys.platform == "darwin":
                    proc = subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False)
                    return proc.returncode == 0
                if sys.platform.startswith("win"):
                    proc = subprocess.run("clip", input=text.encode("utf-16le"), shell=True, check=False)
                    return proc.returncode == 0
                for cmd in (["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
                    try:
                        proc = subprocess.run(cmd, input=text.encode("utf-8"), check=False)
                        if proc.returncode == 0:
                            return True
                    except FileNotFoundError:
                        continue
            except Exception:
                return False
            return False

        def open_new_window(self):
            try:
                url = f"http://127.0.0.1:{self.port}/"
                width, height = 1200, 850
                try:
                    screens = getattr(webview, "screens", None)
                    if isinstance(screens, (list, tuple)) and screens:
                        screen0 = screens[0]
                        sw = None
                        sh = None
                        if isinstance(screen0, dict):
                            sw = screen0.get("width") or screen0.get("w")
                            sh = screen0.get("height") or screen0.get("h")
                        else:
                            sw = getattr(screen0, "width", None) or getattr(screen0, "w", None)
                            sh = getattr(screen0, "height", None) or getattr(screen0, "h", None)
                        if isinstance(sw, (int, float)) and isinstance(sh, (int, float)) and sw > 0 and sh > 0:
                            width = max(1000, int(sw * 0.8))
                            height = max(700, int(sh * 0.8))
                except Exception:
                    pass

                kwargs = {
                    "width": width,
                    "height": height,
                    "resizable": True,
                    "js_api": AppApi(self.port),
                }
                try:
                    import inspect

                    sig = inspect.signature(webview.create_window)
                    if "min_size" in sig.parameters:
                        kwargs["min_size"] = (740, 560)
                    if "text_select" in sig.parameters:
                        kwargs["text_select"] = True
                except Exception:
                    pass

                webview.create_window("MergeMonster", url, **kwargs)
                return True
            except Exception:
                return False

        def open_panel(self, name: str):
            try:
                panel_url = f"http://127.0.0.1:{self.port}/panel/{name}"
                width, height = 1024, 800
                try:
                    screens = getattr(webview, "screens", None)
                    if isinstance(screens, (list, tuple)) and screens:
                        screen0 = screens[0]
                        sw = None
                        sh = None
                        if isinstance(screen0, dict):
                            sw = screen0.get("width") or screen0.get("w")
                            sh = screen0.get("height") or screen0.get("h")
                        else:
                            sw = getattr(screen0, "width", None) or getattr(screen0, "w", None)
                            sh = getattr(screen0, "height", None) or getattr(screen0, "h", None)
                        if isinstance(sw, (int, float)) and isinstance(sh, (int, float)) and sw > 0 and sh > 0:
                            width = max(900, int(sw * 0.8))
                            height = max(650, int(sh * 0.8))
                except Exception:
                    pass

                kwargs = {"width": width, "height": height, "resizable": True}
                try:
                    import inspect

                    if "min_size" in inspect.signature(webview.create_window).parameters:
                        kwargs["min_size"] = (740, 560)
                except Exception:
                    pass

                webview.create_window(name.capitalize(), panel_url, **kwargs)
                return True
            except Exception:
                return False

    # Launch window
    kwargs = {
        "width": 1200,
        "height": 850,
        "resizable": True,
        "js_api": AppApi(port),
    }
    try:
        import inspect

        if "text_select" in inspect.signature(webview.create_window).parameters:
            kwargs["text_select"] = True
    except Exception:
        pass

    try:
        webview.create_window("MergeMonster", url, **kwargs)
        webview.start()
    finally:
        server.should_exit = True
        server_thread.join(timeout=5)
