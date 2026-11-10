from __future__ import annotations

from pathlib import Path
import re

from fastapi.testclient import TestClient

APP_JS = Path(__file__).resolve().parents[1] / "codemerge" / "jsui" / "static" / "app.js"


def test_session_endpoint_returns_csrf_token():
    import codemerge.jsui.server as jsserver

    client = TestClient(jsserver.app)
    res = client.get("/api/session")

    assert res.status_code == 200
    data = res.json()
    assert isinstance(data.get("csrf_token"), str)
    assert len(data["csrf_token"]) >= 32
    assert data["csrf_header"] == "X-CodeMerge-CSRF"


def test_csrf_enforcement_rejects_missing_and_invalid_tokens(monkeypatch):
    import codemerge.jsui.server as jsserver

    monkeypatch.setenv("CODEMERGE_REQUIRE_CSRF", "1")
    client = TestClient(jsserver.app)

    missing = client.put("/api/config", json={})
    assert missing.status_code == 403
    assert missing.json()["error"] == "missing CSRF token"

    invalid = client.put("/api/config", json={}, headers={"X-CodeMerge-CSRF": "bad"})
    assert invalid.status_code == 403
    assert invalid.json()["error"] == "invalid CSRF token"


def test_csrf_enforcement_accepts_session_token(monkeypatch, tmp_path):
    import codemerge.jsui.server as jsserver

    monkeypatch.setenv("CODEMERGE_REQUIRE_CSRF", "1")
    client = TestClient(jsserver.app)
    token = client.get("/api/session").json()["csrf_token"]

    res = client.put(
        "/api/config",
        json={"last_directory": str(tmp_path)},
        headers={"X-CodeMerge-CSRF": token},
    )

    assert res.status_code == 200
    assert res.json()["config"]["last_directory"] == str(tmp_path)


def test_pywebview_bridge_policy_is_specific_to_desktop_server(monkeypatch):
    import codemerge.jsui.server as jsserver

    monkeypatch.setenv("CODEMERGE_REQUIRE_CSRF", "1")
    headless = TestClient(jsserver.app)
    desktop = TestClient(jsserver.desktop_app)

    assert "'unsafe-eval'" not in headless.get("/").headers["Content-Security-Policy"]
    assert "script-src 'self' 'unsafe-eval';" in desktop.get("/").headers["Content-Security-Policy"]
    assert desktop.put("/api/config", json={}).status_code == 403
    assert desktop.get("/", headers={"origin": "https://example.test"}).status_code == 403


def test_app_js_installs_csrf_fetch_wrapper():
    app_js = APP_JS.read_text(encoding="utf-8")

    assert "__nativeFetch('/api/session'" in app_js
    assert "window.fetch = async function codemergeFetch" in app_js
    assert "opts.headers['X-CodeMerge-CSRF']" in app_js


def test_app_js_builds_tokenized_download_urls():
    app_js = APP_JS.read_text(encoding="utf-8")

    assert "async function __downloadUrlForPath" in app_js
    assert "window.__downloadUrlForPath = __downloadUrlForPath" in app_js
    assert "download_token" in app_js
    assert "await window.__downloadUrlForPath" in app_js


def test_app_js_exports_helpers_used_after_bootstrap_closure():
    app_js = APP_JS.read_text(encoding="utf-8")
    _, after_bootstrap = app_js.split("})();", 1)

    assert "window.__downloadUrlForPath = __downloadUrlForPath" in app_js
    assert "window.providerConfirmedPayload = providerConfirmedPayload" in app_js
    assert not re.search(r"(?<![\w.])__downloadUrlForPath\(", after_bootstrap)
    assert not re.search(r"(?<![\w.])providerConfirmedPayload\(", after_bootstrap)
