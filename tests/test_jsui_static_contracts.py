from __future__ import annotations

from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[1] / "codemerge" / "jsui" / "static"


def test_app_js_does_not_hardcode_repo_summary_mode_to_full():
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "function getRepoSummaryMode() {\n  return 'full';\n}" not in app_js
    assert "const repoMode = 'full';" not in app_js
    assert "_normalizeRepoSummaryMode" in app_js


def test_settings_ui_surfaces_timeout_retry_and_offline_batch_notice():
    index_html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="s_timeout"' in index_html
    assert 'id="s_retry_budget"' in index_html
    assert 'id="s_strict_summary_required"' in index_html
    assert "Batch API (offline only)" in index_html
    assert "Interactive JSUI summary flows always run without the Batch API." in index_html


def test_jsui_static_accessibility_contracts():
    index_html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'id="banner" class="banner" style="display:none" role="status" aria-live="polite"' in index_html
    assert 'id="overlay" class="overlay" style="display:none" role="status" aria-live="polite" aria-busy="false"' in index_html
    assert '<table id="files_tbl" aria-label="Scanned files">' in index_html
    assert 'id="ctxmenu" class="ctxmenu" style="display:none" role="menu" aria-hidden="true"' in index_html
    assert "o.setAttribute('aria-busy', 'true')" in app_js
    assert "o.setAttribute('aria-busy', 'false')" in app_js
    assert "tr.setAttribute('aria-selected', isSelected ? 'true' : 'false')" in app_js
    assert "tr.setAttribute('aria-expanded', open ? 'true' : 'false')" in app_js
    assert "btn.setAttribute('role', 'menuitem')" in app_js


def test_jsui_deeper_workflow_accessibility_contracts():
    index_html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'id="jobs_modal" class="modal" style="display:none" role="dialog" aria-modal="true"' in index_html
    assert 'id="btn_jobs" type="button">Jobs</button>' in index_html
    assert 'id="btn_session_export" type="button">Export Session</button>' in index_html
    assert 'id="btn_session_import" type="button">Import Session</button>' in index_html
    assert 'id="session_import_file" type="file"' in index_html
    assert "function _trapModalTab" in app_js
    assert "_trapModalTab(e, modal)" in app_js
    assert "async function exportWorkspaceSession" in app_js
    assert "async function handleSessionImportFile" in app_js
    assert "async function openJobsModal" in app_js
    assert "fetch('/api/jobs?limit=50')" in app_js
    assert "data.status === 'cancelled'" in app_js
    assert "job.cancel_supported ? 'cancel_supported'" in app_js
    assert "job.cancellable ? 'cancellable'" in app_js
