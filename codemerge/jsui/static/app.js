(function () {
  const __nativeFetch = window.fetch.bind(window);
  let __csrfTokenPromise = null;

  function __sameOriginApi(url) {
    try {
      const resolved = new URL(url, window.location.href);
      return resolved.origin === window.location.origin && resolved.pathname.startsWith('/api/');
    } catch (_) {
      return false;
    }
  }

  async function __csrfToken() {
    if (!__csrfTokenPromise) {
      __csrfTokenPromise = __nativeFetch('/api/session', { method: 'GET', credentials: 'same-origin' })
        .then(async (res) => {
          const data = await res.json().catch(() => ({}));
          if (!res.ok || !data.csrf_token) throw new Error(data.error || 'CSRF bootstrap failed');
          return String(data.csrf_token);
        });
    }
    return __csrfTokenPromise;
  }

  async function __downloadUrlForPath(path) {
    const token = await __csrfToken();
    return '/download?path=' + encodeURIComponent(path) + '&download_token=' + encodeURIComponent(token);
  }

  window.__downloadUrlForPath = __downloadUrlForPath;

  window.fetch = async function codemergeFetch(input, init) {
    const opts = Object.assign({}, init || {});
    const method = String(opts.method || (input && input.method) || 'GET').toUpperCase();
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && __sameOriginApi(url)) {
      opts.credentials = opts.credentials || 'same-origin';
      opts.headers = Object.assign({}, opts.headers || {});
      opts.headers['X-CodeMerge-CSRF'] = await __csrfToken();
    }
    return __nativeFetch(input, opts);
  };

  async function providerConfirmedPayload(action, payload) {
    const body = Object.assign({}, payload || {}, { action });
    const res = await window.fetch('/api/provider/preflight', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const preflight = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(preflight.error || ('HTTP ' + res.status));
    if (!preflight.requires_confirmation) return payload || {};
    const budget = preflight.budget || {};
    const scope = preflight.scope || {};
    const risks = Array.isArray(preflight.risks) ? preflight.risks : [];
    const msg = [
      'This action may call an external AI provider.',
      `Action: ${preflight.action || action}`,
      `Scope: ${budget.estimated_files || scope.path_count || 0} files, ~${budget.estimated_tokens || 0} tokens`,
      `Estimated provider calls: ${budget.estimated_provider_calls || 0}`,
      risks.length ? ('Risk: ' + risks.join(' ')) : '',
      'Continue?',
    ].filter(Boolean).join('\n');
    if (!window.confirm(msg)) throw new Error('Provider-backed run cancelled.');
    return Object.assign({}, payload || {}, {
      provider_confirmed: true,
      provider_confirmation_token: preflight.confirmation_token,
    });
  }

  window.providerConfirmedPayload = providerConfirmedPayload;

  function __initUI() {
    if (window.__BOUND_DONE) return; // prevent double-binding
    window.__BOUND_DONE = true;
    const bind = (id, ev, fn) => { const el = document.getElementById(id); if (el) el.addEventListener(ev, fn); };
    loadConfig().then(() => { try { loadContextPackSubsystems(); } catch (_) { } });
    try { loadSettings(); } catch (_) { }
    try { loadUserPresetList(); } catch (_) { }
    // Scan/Rescan should preserve session-hidden for the current folder.
    // Use "Clear hidden" to reset the session-only hidden list.
    const btnScan = document.getElementById("do_scan"); if (btnScan) btnScan.addEventListener('click', () => doScan(false));
    const btnKeepOnly = document.getElementById('btn_keep_only'); if (btnKeepOnly) btnKeepOnly.addEventListener('click', showKeepOnlyModal);
    const btnBrowse = document.getElementById("btn_browse"); if (btnBrowse) btnBrowse.addEventListener("click", browseDir);
    const lastDir = document.getElementById('last_directory');
    if (lastDir) lastDir.addEventListener('change', async () => {
      const value = String(lastDir.value || '').trim();
      persistConfigPatch({ last_directory: value });
      try { await refreshOutputPathForNamingPrefs({ root: value }); } catch (_) { }
      try { loadContextPackSubsystems(); } catch (_) { }
    });
    bind("verify_tokens", "click", verifyTokens);
    const btnAddManual = document.getElementById('btn_add_manual'); if (btnAddManual) btnAddManual.addEventListener('click', addManualPaths);
    const btnCats = document.getElementById('btn_cats'); if (btnCats) btnCats.addEventListener('click', openCategoriesModal);
    bind('cats_close', 'click', closeCategoriesModal);
    bind('cats_apply', 'click', applyCategoriesAndClose);
    const catsModal = document.getElementById('categories_modal');
    if (catsModal) catsModal.addEventListener('click', (e) => { try { if (e && e.target === catsModal) closeCategoriesModal(); } catch (_) { } });
    const btnSettings = document.getElementById('btn_settings'); if (btnSettings) btnSettings.addEventListener('click', openSettingsModal);
    const exModal = document.getElementById('exclusions_modal');
    if (exModal) exModal.addEventListener('click', (e) => { try { if (e && e.target === exModal) closeExclusionsModal(); } catch (_) { } });
    try { initModalResizeHandles(); } catch (_) { }
    bind('btn_reload_xcode', 'click', reloadXcodeTargets);

    bind('btn_ios_preset', 'click', applyIOSPreset);
    document.querySelectorAll('.group-pills .pill').forEach(btn => btn.addEventListener('click', () => applyExtGroup(btn.getAttribute('data-group'))));

    bind('btn_collapse', 'click', collapseAll);
    bind('btn_expand', 'click', expandAll);
    bind('btn_remove_sel', 'click', excludeSelectedPersist);
    bind('btn_clear_all', 'click', clearAllVisible);
    const btnRestoreFull = document.getElementById('btn_restore'); if (btnRestoreFull) btnRestoreFull.addEventListener('click', restoreFullList);
    bind('btn_apply_mode', 'click', applyModeFromToolbar);
    bind('btn_refresh_summaries', 'click', refreshSummariesFromToolbar);
    bind('btn_repo_summary_quick', 'click', () => previewRepoSummaryVariant('quick'));
    bind('btn_repo_summary_full', 'click', () => previewRepoSummaryVariant('full'));
    const visMode = document.getElementById('visibility_mode'); if (visMode) visMode.addEventListener('change', handleVisibilityModeChange);
    const btnResetFilters = document.getElementById('btn_reset_filters'); if (btnResetFilters) btnResetFilters.addEventListener('click', resetFiltersToDefaults);
    const btnFilterAudit = document.getElementById('btn_filter_audit'); if (btnFilterAudit) btnFilterAudit.addEventListener('click', runDiagnostics);

    bind('btn_browse_out', 'click', browseOutputFile);
    const outEl = document.getElementById('output_file');
    if (outEl) outEl.addEventListener('change', persistOutputPath);
    const useFolder = document.getElementById('use_folder_name');
    if (useFolder) useFolder.addEventListener('change', async () => {
      await persistSettings({ use_folder_name: !!useFolder.checked });
      try { await refreshOutputPathForNamingPrefs(); } catch (_) { }
    });
    const appendDt = document.getElementById('append_date');
    if (appendDt) appendDt.addEventListener('change', async () => {
      await persistSettings({ append_datetime: !!appendDt.checked });
      try { await refreshOutputPathForNamingPrefs(); } catch (_) { }
    });
    bind('btn_open_output_folder', 'click', openOutputFolderFromUi);
    bind('btn_copy_chunks', 'click', copyAllOutputChunks);
    bind('btn_download_folder', 'click', downloadOutputFolder);
    bind('btn_merge', 'click', () => runMergeFromFooter({ preview: false }));
    bind('btn_preview_merge', 'click', () => runMergeFromFooter({ preview: true }));
    bind('btn_copy_merge', 'click', copyLastMergeToClipboard);
    bind('btn_select_all', 'click', selectAllRows);
    bind('btn_jobs', 'click', openJobsModal);
    bind('btn_session_export', 'click', exportWorkspaceSession);
    bind('btn_session_import', 'click', importWorkspaceSession);
    const sessionImport = document.getElementById('session_import_file');
    if (sessionImport) sessionImport.addEventListener('change', handleSessionImportFile);
    bind('btn_new_window', 'click', openNewWindow);
    bind('btn_open_full_ui', 'click', openFullUi);
    bind('btn_reset_tokens', 'click', resetTokenRange);

    const incAll = document.getElementById('include_all'); if (incAll) incAll.addEventListener('change', () => { try { handleIncludeAllToggle(); } catch (_) { } });
    bindScanEvents();
    bindAdvancedFilterEvents();
    bind("manual_cancel", "click", hideManualPicker);
    bind("manual_confirm", "click", manualPickerConfirm);
    bind("manual_picker_close", "click", hideManualPicker);
    bind("manual_up", "click", manualPickerUp);
    bind("manual_refresh", "click", manualPickerRefresh);
    const manualSearch = document.getElementById('manual_search');
    if (manualSearch) manualSearch.addEventListener('input', () => scheduleManualPickerSearch());
    const manualHidden = document.getElementById('manual_show_hidden');
    if (manualHidden) manualHidden.addEventListener('change', () => manualPickerRefresh());
    bind("manual_add_files", "click", manualPickerAddFiles);
    bind("manual_add_folder", "click", manualPickerAddFolder);
    bind("manual_remove", "click", manualPickerRemoveSelected);
    bind("manual_clear", "click", manualPickerClearSelection);
    const mpModal = document.getElementById('manual_picker_modal');
    if (mpModal) mpModal.addEventListener('click', (e) => { try { if (e && e.target === mpModal) hideManualPicker(); } catch (_) { } });
    bind("keep_only_cancel", "click", hideKeepOnlyModal);
    bind("keep_only_preview", "click", previewKeepOnly);
    bind("keep_only_back", "click", keepOnlyBack);
    bind("keep_only_copy", "click", copyKeepOnlyList);
    bind("keep_only_apply", "click", applyKeepOnly);
    const kModal = document.getElementById('keep_only_modal');
    if (kModal) kModal.addEventListener('click', (e) => { try { if (e && e.target === kModal) hideKeepOnlyModal(); } catch (_) { } });
    bind("why_close", "click", hideWhyModal);
    bind("why_action_close", "click", hideWhyModal);
    bind("why_action_open", "click", whyOpenFromModal);
    bind("why_action_exclude", "click", whyExcludeFromModal);
    bind("why_action_reset", "click", whyResetFiltersFromModal);
    bind("why_action_enable_category", "click", whyEnableCategoryFromModal);
    bind("why_action_clear_pattern", "click", whyClearPatternFromModal);
    bind("why_action_reset_ranges", "click", whyResetRangesFromModal);
    bind("why_action_reset_dates", "click", whyResetDatesFromModal);
    bind("why_action_clear_dates", "click", whyClearDatesFromModal);
    bind("why_action_add_always", "click", whyAddAlwaysIncludeFromModal);
    const wModal = document.getElementById('why_modal');
    if (wModal) wModal.addEventListener('click', (e) => { try { if (e && e.target === wModal) hideWhyModal(); } catch (_) { } });
    const btnDiag = document.getElementById('btn_diag'); if (btnDiag) btnDiag.addEventListener('click', runDiagnostics);
    bind("btn_diag_run", "click", runDiagnostics);
    bind("btn_summarize_sel", "click", summarizeSelected);
    bind("btn_summarizer_logs", "click", openSummarizerLogs);
    bind("logs_refresh", "click", refreshSummarizerLogs);
    bind("logs_close", "click", closeSummarizerLogs);
    bind("logs_copy", "click", copySummarizerLogs);
    const logsModal = document.getElementById('logs_modal');
    if (logsModal) logsModal.addEventListener('click', (e) => { try { if (e && e.target === logsModal) closeSummarizerLogs(); } catch (_) { } });
    bind("jobs_close", "click", closeJobsModal);
    bind("jobs_done", "click", closeJobsModal);
    bind("jobs_refresh", "click", refreshJobsModal);
    const jobsModal = document.getElementById('jobs_modal');
    if (jobsModal) jobsModal.addEventListener('click', (e) => { try { if (e && e.target === jobsModal) closeJobsModal(); } catch (_) { } });
    const watch = document.getElementById("watch_toggle");
    if (watch) watch.addEventListener("change", (e) => {
      const wi = document.getElementById('watch_int');
      const s = parseInt((wi && wi.value) ? wi.value : '3', 10) || 3;
      if (e.target.checked) startWatch(s); else stopWatch();
      persistConfigPatch({ watch_enabled: !!e.target.checked, watch_interval_sec: s });
    });
    const watchInt = document.getElementById('watch_int');
    if (watchInt) watchInt.addEventListener('change', () => {
      const s = parseInt(String(watchInt.value || '3'), 10) || 3;
      persistConfigPatch({ watch_interval_sec: s });
      const wt = document.getElementById('watch_toggle');
      if (wt && wt.checked) { try { startWatch(s); } catch (_) { } }
    });
    bind("start_prune", "click", startPrune);
    bind("check_prune", "click", checkPrune);
    // No separate rescan button — Scan always refreshes
    bind("preview_close", "click", hidePreviewModal);
    bind("preview_copy", "click", copyPreviewText);
    bind("preview_save", "click", savePreviewText);
    bind("preview_open_external", "click", openPreviewExternally);
    const pModal = document.getElementById('preview_modal');
    if (pModal) pModal.addEventListener('click', (e) => { try { if (e && e.target === pModal) hidePreviewModal(); } catch (_) { } });
    bind("diagnostics_close", "click", hideDiagnosticsModal);
    bind("diagnostics_copy", "click", copyDiagnosticsText);
    const dModal = document.getElementById('diagnostics_modal');
    if (dModal) dModal.addEventListener('click', (e) => { try { if (e && e.target === dModal) hideDiagnosticsModal(); } catch (_) { } });
    const overwriteModal = document.getElementById('overwrite_modal');
    if (overwriteModal) overwriteModal.addEventListener('click', (e) => { try { if (e && e.target === overwriteModal) hideOverwriteModal('cancel'); } catch (_) { } });
    bind("prune_summary_close", "click", hidePruneSummaryModal);
    bind("prune_summary_open", "click", openPruneSummaryOutput);
    bind("prune_summary_copy", "click", copyPruneSummaryPath);
    const pruneModal = document.getElementById('prune_summary_modal');
    if (pruneModal) pruneModal.addEventListener('click', (e) => { try { if (e && e.target === pruneModal) hidePruneSummaryModal(); } catch (_) { } });
    document.addEventListener('keydown', handleGlobalKey);
    const dlink = document.getElementById('download_link'); if (dlink) dlink.addEventListener('click', onDownloadLink);
    bind("btn_load_preset", "click", loadUserPreset);
    bind("btn_save_preset", "click", saveUserPreset);
    bind("btn_del_preset", "click", deleteUserPreset);
    bind("btn_export_preset", "click", exportUserPreset);
    bind("btn_import_preset", "click", importUserPreset);
    bind("btn_reset_modes", "click", resetPerFileModes);
    bind("btn_reset_ex_dirs", "click", resetExcludedDirs);
    bind("btn_reset_ex_files", "click", resetExcludedFiles);
    bind("btn_reset_ex_all", "click", resetExcludedAll);
    bind("btn_exclusions", "click", openExclusionsModal);
    bind("exclusions_close", "click", closeExclusionsModal);
    bind("exclusions_cancel", "click", closeExclusionsModal);
    bind("exclusions_save", "click", saveExclusionsModal);
    bind("ex_dirs_add", "click", addExcludedDir);
    bind("ex_dirs_remove", "click", removeExcludedDir);
    bind("ex_dirs_reset", "click", resetExcludedDirs);
    bind("ex_files_add", "click", addExcludedFiles);
    bind("ex_files_remove", "click", removeExcludedFiles);
    bind("ex_files_reset", "click", resetExcludedFiles);
    bind("ex_custom_add", "click", addCustomExcluded);
    bind("ex_custom_remove", "click", removeCustomExcluded);
    bind("ex_custom_clear", "click", resetExcludedCustom);
    bind("ex_tab_dirs_btn", "click", () => switchExclusionsTab('ex_tab_dirs', document.getElementById('ex_tab_dirs_btn')));
    bind("ex_tab_files_btn", "click", () => switchExclusionsTab('ex_tab_files', document.getElementById('ex_tab_files_btn')));
    bind("ex_tab_custom_btn", "click", () => switchExclusionsTab('ex_tab_custom', document.getElementById('ex_tab_custom_btn')));
    bind("btn_reset_always_docs", "click", resetAlwaysIncludeDocs);
    bind("ext_map_add", "click", addOrUpdateCustomExt);
    bind("ext_map_remove", "click", removeCustomExt);
    bind("ext_map_reload", "click", refreshCustomExtList);
    const extList = document.getElementById('ext_map_list');
    if (extList) extList.addEventListener('change', syncCustomExtEditorFromList);
    const presetImport = document.getElementById('preset_import_file');
    if (presetImport) presetImport.addEventListener('change', handlePresetImportFile);
    bind("pair_run", "click", pairCodebases);
    bind("pair_ai_run", "click", runPairAi);
    bind("pair_ai_save", "click", savePairAiReport);
    bind("rest_run", "click", restoreFromMerged);
    bind("pr_output_browse", "click", browsePruneOutput);
    bind("ctx_pack_refresh", "click", loadContextPackSubsystems);
    bind("ctx_pack_run", "click", buildContextPack);
    bind("ctx_pack_output_browse", "click", () => browsePathIntoInput('ctx_pack_output', { allowFiles: true }));
    const pruneFields = ['pr_rank', 'pr_engine', 'pr_neighbors', 'pr_mode', 'pr_budget', 'pr_dryrun', 'pr_output'];
    pruneFields.forEach((id) => bind(id, 'change', persistPruneConfigFromUi));
    bind("pair_a_browse", "click", () => browsePathIntoInput('pair_a', { allowDirs: true }));
    bind("pair_b_browse", "click", () => browsePathIntoInput('pair_b', { allowDirs: true }));
    bind("pair_dest_browse", "click", () => browsePathIntoInput('pair_dest', { allowDirs: true }));
    bind("pair_ai_a_browse", "click", () => browsePathIntoInput('pair_ai_a', { allowDirs: true }));
    bind("pair_ai_b_browse", "click", () => browsePathIntoInput('pair_ai_b', { allowDirs: true }));
    bind("rest_src_browse", "click", () => browsePathIntoInput('rest_src', { allowFiles: true }));
    bind("rest_dest_browse", "click", () => browsePathIntoInput('rest_dest', { allowDirs: true }));
    // Category presets
    document.querySelectorAll('.preset').forEach(btn => btn.addEventListener('click', () => applyPreset(btn.getAttribute('data-preset'))));
    const togDen = document.getElementById('toggle_density'); if (togDen) togDen.addEventListener('change', () => { window.__SHOW_DENS = togDen.checked; document.body.classList.toggle('hide-density', !togDen.checked); renderTable(); persistSettings({ show_density_column: !!togDen.checked }); });
    const togTotals = document.getElementById('toggle_totals'); if (togTotals) togTotals.addEventListener('change', () => { document.body.classList.toggle('hide-totals', !togTotals.checked); renderTable(); persistSettings({ show_totals_columns: !!togTotals.checked }); });
    const togFinal = document.getElementById('toggle_final_footer'); if (togFinal) togFinal.addEventListener('change', saveFooterFinalOnly);
    // Also refresh footer stats text when final-only toggle changes
    if (togFinal) togFinal.addEventListener('change', () => { try { updateScanStatsText(); } catch (_) { } });
    const togBytes = document.getElementById('toggle_bytes_footer'); if (togBytes) togBytes.addEventListener('change', () => { persistSettings({ show_bytes_footer: !!togBytes.checked }); updateScanStatsText(); });
    const mergeMode = document.getElementById('merge_structure'); if (mergeMode) mergeMode.addEventListener('change', updateMergeModeControls);
    updateMergeModeControls();
    const mergeContent = document.getElementById('merge_content_mode'); if (mergeContent) mergeContent.addEventListener('change', () => scheduleFinalTokenRecalc(200));
    const mergePrompt = document.getElementById('merge_prompt_template'); if (mergePrompt) mergePrompt.addEventListener('input', () => scheduleFinalTokenRecalc(400));
    const optTree = document.getElementById('opt_tree'); if (optTree) optTree.addEventListener('change', () => scheduleFinalTokenRecalc(200));
    const optRepo = document.getElementById('merge_include_repo_summary');
    if (optRepo) optRepo.addEventListener('change', () => {
      window.__INCLUDE_REPO_SUMMARY = !!optRepo.checked;
      persistConfigPatch({ include_repo_summary_in_merge: !!optRepo.checked });
      scheduleFinalTokenRecalc(200);
    });
    const repoMode = document.getElementById('merge_repo_summary_mode');
    if (repoMode) repoMode.addEventListener('change', () => {
      const val = String(repoMode.value || 'quick').trim().toLowerCase();
      const mode = (val === 'full') ? 'full' : 'quick';
      window.__REPO_SUMMARY_MODE = mode;
      repoMode.value = mode;
      persistConfigPatch({ include_repo_summary_mode: mode });
      scheduleFinalTokenRecalc(200);
    });
    const optFences = document.getElementById('opt_fences'); if (optFences) optFences.addEventListener('change', () => scheduleFinalTokenRecalc(200));
    const optToc = document.getElementById('opt_toc'); if (optToc) optToc.addEventListener('change', () => scheduleFinalTokenRecalc(200));
    const optEnum = document.getElementById('opt_enum'); if (optEnum) optEnum.addEventListener('change', () => scheduleFinalTokenRecalc(200));
    const optStrip = document.getElementById('opt_strip'); if (optStrip) optStrip.addEventListener('change', () => scheduleFinalTokenRecalc(200));
    const clearDatesBtn = document.getElementById('btn_clear_dates');
    if (clearDatesBtn) clearDatesBtn.addEventListener('click', () => clearDateFilters(true));
    document.querySelectorAll('.token-max').forEach(btn => btn.addEventListener('click', () => applyTokenMaxQuick(btn)));
    // View mode toggle should re-render immediately
    const viewSel = document.getElementById('view_mode');
    if (viewSel) viewSel.addEventListener('change', () => {
      renderTable();
      persistConfigPatch({ view_mode: viewSel.value || 'hierarchy' });
    });
    const viewRadios = Array.from(document.querySelectorAll('input[name="view_mode_radio"]'));
    viewRadios.forEach(radio => {
      radio.addEventListener('change', () => {
        if (!radio.checked || !viewSel) return;
        viewSel.value = radio.value || 'hierarchy';
        viewSel.dispatchEvent(new Event('change'));
      });
    });
    syncViewModeRadios();
    // Theme toggle
    bind('theme_toggle', 'click', toggleTheme);
    initTheme();
    // Apply UI settings on load (font, contrast, toggles)
    try { applyUiSettingsFromServer(); } catch (_) { }
    try { handlePanelRoute(); } catch (_) { }
  }
  if (document.readyState === 'loading') {
    window.addEventListener('DOMContentLoaded', __initUI);
  } else {
    try { __initUI(); } catch (e) { try { console.error('Init failed', e); } catch (_) { } }
  }
  // Clean up timers on unload
  window.addEventListener('beforeunload', () => { try { stopWatch(); } catch (_) { } });

  // Session-only hidden file tracking per root
  window.__HIDDEN_MAP = window.__HIDDEN_MAP || {};
  window.__CURRENT_ROOT = window.__CURRENT_ROOT || null;
  window.__FILTER_RUNNING = window.__FILTER_RUNNING || false;
  window.__FILTER_PENDING = window.__FILTER_PENDING || false;
  window.__LAST_FILTER_AT = window.__LAST_FILTER_AT || 0;
  window.__FILTER_TIMER = window.__FILTER_TIMER || null;
  window.__SUMMARY_ERRORS = window.__SUMMARY_ERRORS || new Set();
  window.__PATTERN_HITS = window.__PATTERN_HITS || new Set();
  window.__HEADER_SORT_ACTIVE = window.__HEADER_SORT_ACTIVE || false;
  window.__PREFER_CONFIG_SORT = window.__PREFER_CONFIG_SORT || false;
  window.__AUTO_REFRESH_SUMMARIES = window.__AUTO_REFRESH_SUMMARIES || false;

  function syncViewModeRadios() {
    const viewSel = document.getElementById('view_mode');
    const radios = Array.from(document.querySelectorAll('input[name="view_mode_radio"]'));
    if (!viewSel || !radios.length) return;
    const v = String(viewSel.value || 'hierarchy');
    radios.forEach(radio => { radio.checked = (radio.value === v); });
  }

  // Theme management
  function initTheme() {
    try {
      const saved = localStorage.getItem('theme');
      // Default to light theme for file management UI
      if (saved === 'dark') {
        document.body.classList.add('dark-theme');
      } else {
        document.body.classList.remove('dark-theme');
      }
      updateThemeButton();
    } catch (_) { }
  }

  function toggleTheme() {
    try {
      const isDark = document.body.classList.toggle('dark-theme');
      localStorage.setItem('theme', isDark ? 'dark' : 'light');
      updateThemeButton();
    } catch (_) { }
  }

  function updateThemeButton() {
    try {
      const btn = document.getElementById('theme_toggle');
      if (!btn) return;
      const isDark = document.body.classList.contains('dark-theme');
      btn.textContent = isDark ? '☀️' : '🌙';
      btn.title = isDark ? 'Switch to light theme' : 'Switch to dark theme';
    } catch (_) { }
  }

  function showOverlay(msg, onCancel) {
    const o = document.getElementById('overlay'); if (!o) return;
    o.style.display = 'flex';
    o.setAttribute('aria-busy', 'true');
    const m = document.getElementById('overlay_msg'); if (m) m.textContent = msg || 'Working…';
    const b = document.getElementById('overlay_cancel');
    if (b) {
      if (typeof onCancel === 'function') {
        b.style.display = 'inline-block';
        b.onclick = () => { try { onCancel(); } catch (_) { } };
      } else {
        b.style.display = 'none'; b.onclick = null;
      }
    }
  }
  function hideOverlay() { const o = document.getElementById('overlay'); if (o) { o.style.display = 'none'; o.setAttribute('aria-busy', 'false'); } const b = document.getElementById('overlay_cancel'); if (b) { b.style.display = 'none'; b.onclick = null; } }
  try { window.showOverlay = showOverlay; window.hideOverlay = hideOverlay; } catch (_) { }

  function onDownloadLink(e) {
    try {
      const a = e.currentTarget; if (!a || !a.href || a.href.endsWith('#')) return;
      e.preventDefault();
      fetch(a.href, { method: 'GET' }).then(res => {
        if (res.ok) { location.href = a.href; }
        else { showBanner('Download failed (' + res.status + '): access denied or not found', 'warn'); }
      }).catch(err => showBanner('Download failed: ' + err, 'warn'));
    } catch (err) { /* ignore */ }
  }

  // Prune job poller with summary rendering
  function pollPruneJob(jobId, outId, label) {
    const out = document.getElementById(outId);
    showOverlay(label || 'Working…');
    const tick = async () => {
      try {
        const res = await fetch('/api/jobs/' + jobId);
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
        if (data.progress && out) out.textContent = String(data.progress);
        if (data.status === 'done' || data.status === 'error' || data.status === 'cancelled') {
          try { handlePruneJobResult(data, out); } catch (_) { if (out) out.textContent = JSON.stringify(data, null, 2); }
          hideOverlay();
          return;
        }
      } catch (err) {
        if (out) out.textContent = 'Job check failed: ' + err;
        hideOverlay(); return;
      }
      setTimeout(tick, 1000);
    };
    tick();
  }
  try { window.pollPruneJob = pollPruneJob; } catch (_) { }
})();

function goMain() {
  try {
    // Prefer returning to the main view (especially important for panel routes).
    if (location && typeof location.pathname === 'string' && location.pathname.indexOf('/panel/') !== -1) {
      location.href = '/';
      return;
    }
  } catch (_) { }
  try { location.href = '/'; } catch (_) { }
}

function isPanelRoute() {
  try { return location && typeof location.pathname === 'string' && location.pathname.indexOf('/panel/') !== -1; } catch (_) { return false; }
}

function openFullUi() {
  try { window.open('/', '_blank'); } catch (_) { }
}

async function openNewWindow() {
  try {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.open_new_window) {
      const ok = await window.pywebview.api.open_new_window();
      if (ok) return;
    }
  } catch (_) { }
  try {
    window.open('/', '_blank', 'width=1200,height=850,noopener');
  } catch (_) {
    try { window.open('/', '_blank'); } catch (_) { }
  }
}

function handlePanelRoute() {
  if (!isPanelRoute()) return;
  document.body.classList.add('panel-mode');
  let name = '';
  try {
    name = String(location.pathname.split('/panel/')[1] || '').split('/')[0].toLowerCase();
  } catch (_) { name = ''; }
  if (!name || name === 'main' || name === 'full') {
    document.body.classList.remove('panel-mode');
    return;
  }
  if (name === 'categories') {
    openCategoriesModal();
    return;
  }
  openSettingsModal();
  const tabMap = {
    settings: 'tab_types',
    types: 'tab_types',
    filters: 'tab_filters',
    ui: 'tab_ui',
    output: 'tab_output',
    tools: 'tab_tools',
  };
  const tab = tabMap[name];
  if (tab) {
    const btn = document.querySelector(`.tabs-nav .tab-btn[onclick*="${tab}"]`);
    switchTab(tab, btn || null);
  }
}

function _epochSecondsToYmdUTC(value) {
  let n = 0;
  if (typeof value === 'number') n = value;
  else if (typeof value === 'string' && value.trim()) n = parseFloat(value);
  if (!isFinite(n) || n <= 0) return '';
  const d = new Date(n * 1000);
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, '0');
  const day = String(d.getUTCDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

const DATE_AFTER_DEFAULT = '1970-01-01';
const DATE_BEFORE_DEFAULT = '';

function _setDateManualFlag(el, manual, defaultValue) {
  if (!el) return;
  el.dataset.manual = manual ? '1' : '0';
  if (!manual) {
    const fallback = (defaultValue !== undefined) ? defaultValue : DATE_AFTER_DEFAULT;
    el.value = fallback;
  }
}

function _setDateAutoFlag(el) {
  if (!el) return;
  el.dataset.manual = '0';
}

function _getDateManualFlag(el, defaultValue) {
  if (!el) return false;
  if (el.dataset && el.dataset.manual) return el.dataset.manual === '1';
  const v = String(el.value || '').trim();
  const def = (defaultValue !== undefined) ? String(defaultValue) : DATE_AFTER_DEFAULT;
  return !!v && v !== def;
}

function _ymdToEpochSecondsUTC(ymd) {
  if (typeof ymd === 'number') return isFinite(ymd) ? Math.floor(ymd) : 0;
  const s = (ymd || '').trim();
  if (!s) return 0;
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (m) {
    const y = parseInt(m[1], 10);
    const mo = parseInt(m[2], 10);
    const d = parseInt(m[3], 10);
    const dt = new Date(Date.UTC(y, mo - 1, d));
    const ms = dt.getTime();
    return isFinite(ms) ? Math.floor(ms / 1000) : 0;
  }
  const ms = Date.parse(s);
  return isFinite(ms) ? Math.floor(ms / 1000) : 0;
}

function applyLastNDays(opts) {
  opts = opts || {};
  const triggerScan = (opts.triggerScan !== undefined) ? !!opts.triggerScan : true;
  const clearDatesWhenDisabled = (opts.clearDatesWhenDisabled !== undefined) ? !!opts.clearDatesWhenDisabled : true;
  const elDays = document.getElementById('f_last_n_days');
  const after = document.getElementById('f_date_after');
  const before = document.getElementById('f_date_before');
  if (!elDays || !after || !before) return;

  let days = parseInt(elDays.value || '0', 10);
  if (!isFinite(days)) days = 0;

  if (days <= 0) {
    if (clearDatesWhenDisabled) {
      _setDateManualFlag(after, false, DATE_AFTER_DEFAULT);
      _setDateManualFlag(before, false, DATE_BEFORE_DEFAULT);
    }
    if (triggerScan) { try { requestFilterApply(); } catch (_) { } }
    return;
  }

  const end = new Date();
  const start = new Date(end.getTime() - (days * 24 * 60 * 60 * 1000));
  const y = start.getUTCFullYear();
  const m = String(start.getUTCMonth() + 1).padStart(2, '0');
  const d = String(start.getUTCDate()).padStart(2, '0');
  after.value = `${y}-${m}-${d}`;
  before.value = DATE_BEFORE_DEFAULT;
  after.dataset.manual = '1';
  _setDateAutoFlag(before);
  if (triggerScan) { try { requestFilterApply(); } catch (_) { } }
}

function clearDateFilters(triggerScan = true) {
  const after = document.getElementById('f_date_after');
  const before = document.getElementById('f_date_before');
  if (after) { after.value = DATE_AFTER_DEFAULT; after.dataset.manual = '0'; }
  if (before) { before.value = DATE_BEFORE_DEFAULT; before.dataset.manual = '0'; }
  const lastDays = document.getElementById('f_last_n_days');
  if (lastDays) lastDays.value = '0';
  if (triggerScan) scheduleFilterScan(50);
}

function applyTokenMaxQuick(btn) {
  if (!btn) return;
  const raw = btn.getAttribute('data-value') || '';
  const val = parseInt(String(raw), 10);
  if (!isFinite(val) || val <= 0) return;
  const maxEl = document.getElementById('f_token_max');
  if (maxEl) maxEl.value = String(val);
  const minEl = document.getElementById('f_token_min');
  if (minEl) {
    const minVal = parseInt(String(minEl.value || '0'), 10) || 0;
    if (minVal > val) minEl.value = '0';
  }
  scheduleFilterScan(100);
}

function _applyConfigToInputs(cfg) {
  cfg = cfg || {};
  const lf = (cfg.last_filters && typeof cfg.last_filters === 'object') ? cfg.last_filters : {};
  const setVal = (id, v) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (v === undefined || v === null) return;
    el.value = String(v);
  };
  const setChecked = (id, v) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.checked = !!v;
  };

  // Core filters
  setVal('f_name_pattern', lf.name_pattern || '');
  setChecked('f_name_glob', cfg.name_filter_glob);
  let tokMin = (typeof lf.token_min === 'number') ? lf.token_min : parseInt(lf.token_min || '0', 10);
  if (!isFinite(tokMin)) tokMin = 0;
  let tokMax = (typeof lf.token_max === 'number') ? lf.token_max : parseInt(lf.token_max || '10000000', 10);
  if (!isFinite(tokMax)) tokMax = 10000000;
  let sizeMin = (typeof lf.size_min === 'number') ? lf.size_min : parseFloat(lf.size_min || '0');
  if (!isFinite(sizeMin)) sizeMin = 0;
  let sizeMax = (typeof lf.size_max === 'number') ? lf.size_max : parseFloat(lf.size_max || '1000000');
  if (!isFinite(sizeMax)) sizeMax = 1000000;
  setVal('f_token_min', tokMin);
  setVal('f_token_max', tokMax);
  setVal('f_size_min', sizeMin);
  setVal('f_size_max', sizeMax);
  const afterEl = document.getElementById('f_date_after');
  const beforeEl = document.getElementById('f_date_before');
  const afterManual = (lf.date_after_manual !== undefined) ? !!lf.date_after_manual : !!lf.date_after;
  const beforeManual = (lf.date_before_manual !== undefined) ? !!lf.date_before_manual : !!lf.date_before;
  const afterVal = afterManual ? (_epochSecondsToYmdUTC(lf.date_after) || DATE_AFTER_DEFAULT) : DATE_AFTER_DEFAULT;
  const beforeVal = beforeManual ? (_epochSecondsToYmdUTC(lf.date_before) || DATE_BEFORE_DEFAULT) : DATE_BEFORE_DEFAULT;
  if (afterEl) { afterEl.value = afterVal; afterEl.dataset.manual = afterManual ? '1' : '0'; }
  if (beforeEl) { beforeEl.value = beforeVal; beforeEl.dataset.manual = beforeManual ? '1' : '0'; }

  // Category selection
  let cats = Array.isArray(lf.categories) ? lf.categories : [];
  if (!cats.length && cfg.category_mask && typeof cfg.category_mask === 'object') {
    try {
      cats = Object.keys(cfg.category_mask).filter(k => !!cfg.category_mask[k]);
    } catch (_) { cats = []; }
  }
  if (cats && cats.length) {
    const set = new Set((cats || []).map(c => String(c).toUpperCase()));
    document.querySelectorAll('.f_cat').forEach(cb => {
      try { cb.checked = set.has(String(cb.value).toUpperCase()); } catch (_) { }
    });
  }

  // Content filters
  setVal('f_content', cfg.content_search || '');
  setChecked('f_content_regex', cfg.content_regex);
  setChecked('f_content_case', cfg.content_case_sensitive);
  setVal('f_content_kb', (cfg.content_max_kb !== undefined && cfg.content_max_kb !== null) ? cfg.content_max_kb : 256);
  setVal('f_dup_kb', (cfg.duplicate_hash_max_kb !== undefined && cfg.duplicate_hash_max_kb !== null) ? cfg.duplicate_hash_max_kb : 256);
  setChecked('f_hide_dups', cfg.content_hide_duplicates);
  setChecked('f_mark_dups', (cfg.content_mark_duplicates !== undefined) ? cfg.content_mark_duplicates : true);
  setChecked('f_apply_token_transforms', cfg.apply_token_filters_to_transforms);
  setChecked('f_always_docs', (cfg.always_include_docs !== undefined) ? cfg.always_include_docs : true);

  // Scan knobs
  setChecked('f_ignore_binary', cfg.ignore_binary_files);
  setVal('f_max_mb', (cfg.max_file_size_mb !== undefined && cfg.max_file_size_mb !== null) ? cfg.max_file_size_mb : 0);
  setVal('f_max_depth', (cfg.max_walk_depth !== undefined && cfg.max_walk_depth !== null) ? cfg.max_walk_depth : 0);
  setChecked('f_follow_symlinks', cfg.follow_symlinks);

  // Git knobs
  setChecked('f_use_git', cfg.use_git_files);
  setChecked('f_git_untracked', cfg.git_include_untracked);
  setChecked('f_git_changed', cfg.git_only_changed);
  setVal('f_git_since', cfg.git_since_ref || '');
  setVal('f_git_until', cfg.git_until_ref || 'HEAD');
  setChecked('f_ex_vendored', cfg.exclude_vendored);

  // Noise toggles
  setChecked('f_ex_node', (cfg.exclude_node_modules !== undefined) ? cfg.exclude_node_modules : true);
  setChecked('f_ex_ios', (cfg.exclude_ios_noise !== undefined) ? cfg.exclude_ios_noise : true);
  setChecked('f_ex_py', cfg.exclude_python_noise);
  setChecked('f_ex_java', cfg.exclude_java_gradle_noise);
  setChecked('f_ex_web', cfg.exclude_web_build_noise);

  // Watch defaults
  setChecked('watch_toggle', cfg.watch_enabled);
  setVal('watch_int', (cfg.watch_interval_sec !== undefined && cfg.watch_interval_sec !== null) ? cfg.watch_interval_sec : 3);

  // Sort defaults
  setVal('sort_by', cfg.sort_by || 'name');
  setChecked('sort_desc', cfg.sort_descending);

  // Quick date helper (apply only when enabled to avoid clobbering manual dates)
  const days = parseInt(String(cfg.date_last_n_days || '0'), 10) || 0;
  setVal('f_last_n_days', days);
  if (days > 0) {
    applyLastNDays({ triggerScan: false, clearDatesWhenDisabled: false });
  }
  setChecked('merge_include_repo_summary', cfg.include_repo_summary_in_merge);
  if (typeof cfg.include_repo_summary_in_merge === 'boolean') {
    window.__INCLUDE_REPO_SUMMARY = cfg.include_repo_summary_in_merge;
  }
  const repoMode = _normalizeRepoSummaryMode(
    cfg.include_repo_summary_mode || cfg.repo_summary_mode || 'quick'
  );
  setVal('merge_repo_summary_mode', repoMode);
  window.__REPO_SUMMARY_MODE = repoMode;
}

function _applyPruneConfig(cfg) {
  cfg = cfg || {};
  const setVal = (id, v) => { const el = document.getElementById(id); if (el) el.value = (v !== undefined && v !== null) ? String(v) : el.value; };
  const setChecked = (id, v) => { const el = document.getElementById(id); if (el) el.checked = !!v; };
  setVal('pr_rank', cfg.prune_ranking_mode || 'hybrid');
  setVal('pr_engine', cfg.prune_engine || 'local');
  setVal('pr_neighbors', cfg.prune_neighbors_mode || 'same_package');
  setVal('pr_mode', cfg.prune_mode || 'compact');
  setVal('pr_budget', cfg.prune_token_budget || 45000);
  setVal('pr_output', cfg.prune_output_path || '');
  setChecked('pr_dryrun', cfg.prune_dry_run);
}

function collectPruneConfigFromUi() {
  const val = (id, fallback) => {
    const el = document.getElementById(id);
    if (!el) return (fallback !== undefined && fallback !== null) ? String(fallback) : '';
    return String(el.value || '');
  };
  const checked = (id, fallback) => {
    const el = document.getElementById(id);
    if (!el) return !!fallback;
    return !!el.checked;
  };
  const outputPath = val('pr_output', '').trim();
  const budgetRaw = val('pr_budget', '45000');
  const budget = parseInt(String(budgetRaw || '45000'), 10);
  return {
    prune_output_path: outputPath || '',
    prune_ranking_mode: val('pr_rank', 'hybrid') || 'hybrid',
    prune_engine: val('pr_engine', 'local') || 'local',
    prune_neighbors_mode: val('pr_neighbors', 'same_package') || 'same_package',
    prune_mode: val('pr_mode', 'compact') || 'compact',
    prune_dry_run: checked('pr_dryrun', false),
    prune_token_budget: isFinite(budget) ? budget : 45000,
  };
}

async function persistPruneConfigFromUi() {
  const patch = collectPruneConfigFromUi();
  try { await persistConfigPatch(patch); } catch (_) { }
  try { if (patch.prune_output_path) await allowOutputPath(patch.prune_output_path); } catch (_) { }
}

async function loadConfig() {
  try {
    const res = await fetch("/api/config");
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    const cfg = (data && data.config) ? data.config : {};
    try { window.__CURRENT_CONFIG = cfg; window.__CONFIG_LOADED = true; } catch (_) { }
    // Fill form
    const setVal = (id, v) => { const el = document.getElementById(id); if (el) el.value = (v !== undefined && v !== null) ? String(v) : ''; };
    setVal("last_directory", cfg.last_directory || "");
    try { window.__OUTPUT_ROOT_LAST = String(cfg.last_directory || ''); } catch (_) { }
    setVal("output_file", cfg.last_output || (cfg.last_directory ? (cfg.last_directory.replace(/[\\/]$/, '') + '/merged_files.txt') : ""));
    setVal("view_mode", cfg.view_mode || "hierarchy");
    syncViewModeRadios();
    // Render file types toggles
    if (cfg.file_types) {
      renderFileTypes(cfg.file_types);
      const incAll = document.getElementById('include_all');
      try {
        const vals = Object.values(cfg.file_types || {});
        if (incAll && vals.length) incAll.checked = vals.every(v => !!v);
      } catch (_) { }
    }
    try { applyIncludeAllState({ silent: true }); } catch (_) { }
    // Apply persisted filter panel defaults (last_filters + scan knobs)
    try { _applyConfigToInputs(cfg); } catch (_) { }
    try { _applyPruneConfig(cfg); } catch (_) { }
    // Apply UI settings from server immediately (font, contrast, footer toggles, watch debounce)
    try { applyUiSettingsFromServer(); } catch (_) { }
    try { loadUserPresetList({ selectName: cfg.selected_preset || cfg.last_preset || '' }); } catch (_) { }
    // Auto-scan on load when we have a directory
    if (cfg.last_directory) {
      setTimeout(() => doScan(), 0);
    }
    // Auto-start watch if enabled in config
    try {
      if (cfg.watch_enabled && cfg.last_directory) {
        const intervalSec = parseInt(String(cfg.watch_interval_sec || '3'), 10) || 3;
        const wt = document.getElementById('watch_toggle'); if (wt) wt.checked = true;
        const wi = document.getElementById('watch_int'); if (wi) wi.value = String(intervalSec);
        setTimeout(() => { try { startWatch(intervalSec); } catch (_) { } }, 150);
      }
    } catch (_) { }
    try { updateActiveFiltersSummary(); } catch (_) { }
  } catch (err) {
    console.warn("Failed to load /api/config:", err);
  }
}

async function browseDir() {
  try {
    const initial = document.getElementById('last_directory').value || '';
    // Prefer native dialogs via pywebview in desktop wrapper mode.
    try {
      if (window.pywebview && window.pywebview.api && window.pywebview.api.browse_dir) {
        const chosen = await window.pywebview.api.browse_dir(initial || null);
        if (chosen && typeof chosen === 'string') {
          document.getElementById('last_directory').value = chosen;
          try { await fetch('/api/config', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ last_directory: chosen }) }); } catch (_e) { }
          try { await refreshOutputPathForNamingPrefs({ root: chosen }); } catch (_) { }
          await doScan();
          return;
        }
      }
    } catch (_) { /* fall back */ }
    // Fallback: manual picker in browser mode
    openManualPicker({
      mode: 'pick',
      allowDirs: true,
      allowFiles: false,
      single: true,
      path: initial || '',
      onConfirm: async (paths) => {
        const chosen = (paths && paths.length) ? paths[0] : '';
        if (chosen) {
          document.getElementById('last_directory').value = chosen;
          try { await fetch('/api/config', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ last_directory: chosen }) }); } catch (_e) { }
          try { await refreshOutputPathForNamingPrefs({ root: chosen }); } catch (_) { }
          await doScan();
        }
      },
    });
  } catch (err) {
    alert('Browse failed: ' + err);
  }
}

function renderFileTypes(ft) {
  const box = document.getElementById('f_types');
  if (!box) return;
  box.innerHTML = '';
  const keys = Object.keys(ft).sort();
  keys.forEach(k => {
    const id = 'ft_' + k.replace(/[^a-z0-9_]/gi, '_');
    const wrap = document.createElement('label');
    wrap.innerHTML = `<input type="checkbox" id="${escapeHtml(id)}"> ${escapeHtml(k)}`;
    const cb = wrap.querySelector('input');
    cb.dataset.ext = k;
    cb.classList.add('ft_cb');
    cb.checked = !!ft[k];
    cb.addEventListener('change', async () => {
      // Update config with new file_types
      try {
        const res = await fetch('/api/config', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ file_types: { [k]: cb.checked } }) });
        if (!res.ok) { const d = await res.json(); alert(d.error || 'Failed to update file_types'); }
        try { requestScan(50); } catch (_) { }
      } catch (e) { alert('Failed to update file_types: ' + e); }
      try { updateActiveFiltersSummary(); } catch (_) { }
    });
    box.appendChild(wrap);
  });
  try { applyIncludeAllState({ silent: true }); } catch (_) { }
  try { updateActiveFiltersSummary(); } catch (_) { }
}

function _collectFileTypeState() {
  const out = {};
  document.querySelectorAll('#f_types input.ft_cb').forEach(cb => {
    if (!cb || !cb.dataset) return;
    const ext = String(cb.dataset.ext || '');
    out[ext] = !!cb.checked;
  });
  return out;
}

function applyIncludeAllState(opts) {
  opts = opts || {};
  const incAll = document.getElementById('include_all');
  if (!incAll) return;
  const cbs = Array.from(document.querySelectorAll('#f_types input.ft_cb'));
  if (!cbs.length) return;
  if (incAll.checked) {
    const current = _collectFileTypeState();
    if (!window.__FILE_TYPES_PRE_ALL) {
      window.__FILE_TYPES_PRE_ALL = current;
    } else {
      const prior = window.__FILE_TYPES_PRE_ALL;
      Object.keys(current).forEach(ext => {
        if (!(ext in prior)) prior[ext] = true;
      });
    }
    cbs.forEach(cb => { cb.checked = true; cb.disabled = true; });
    if (!opts.silent) {
      const patch = {};
      cbs.forEach(cb => { patch[String(cb.dataset.ext || '')] = true; });
      persistConfigPatch({ file_types: patch });
    }
  } else {
    cbs.forEach(cb => { cb.disabled = false; });
    if (window.__FILE_TYPES_PRE_ALL) {
      const prior = window.__FILE_TYPES_PRE_ALL;
      cbs.forEach(cb => {
        const ext = String(cb.dataset.ext || '');
        if (ext in prior) cb.checked = !!prior[ext];
      });
      if (!opts.silent) persistConfigPatch({ file_types: prior });
      window.__FILE_TYPES_PRE_ALL = null;
    }
  }
}

function handleIncludeAllToggle() {
  applyIncludeAllState({ silent: false });
  try { updateActiveFiltersSummary(); } catch (_) { }
  try { doScan(false); } catch (_) { }
}

// ---------------- Dashboard helpers ----------------
async function persistConfigPatch(patch) {
  try {
    const res = await fetch('/api/config', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch || {}) });
    const data = await res.json().catch(() => ({}));
    const cfg = (data && data.config && typeof data.config === 'object') ? data.config : null;
    if (cfg) {
      try { window.__CURRENT_CONFIG = cfg; } catch (_) { }
      if (patch && typeof patch.last_directory === 'string') {
        try { _applyConfigToInputs(cfg); } catch (_) { }
        try { if (cfg.file_types) renderFileTypes(cfg.file_types); } catch (_) { }
        try { applyUiSettingsFromServer(); } catch (_) { }
        try { loadSettings(); } catch (_) { }
        try { updateActiveFiltersSummary(); } catch (_) { }
      }
    } else if (window.__CURRENT_CONFIG && typeof window.__CURRENT_CONFIG === 'object') {
      try { Object.assign(window.__CURRENT_CONFIG, patch || {}); } catch (_) { }
    }
  } catch (_) { }
}

let __FILTER_PERSIST_TIMER = null;
function schedulePersistFilters(delayMs = 500) {
  try { if (__FILTER_PERSIST_TIMER) clearTimeout(__FILTER_PERSIST_TIMER); } catch (_) { }
  __FILTER_PERSIST_TIMER = setTimeout(() => { try { persistFilterState(); } catch (_) { } }, Math.max(0, delayMs || 0));
}

function buildFilterConfigPatch() {
  const cfg = window.__CURRENT_CONFIG || {};
  const checked = (id, fallback) => {
    const el = document.getElementById(id);
    if (!el) return !!fallback;
    return !!el.checked;
  };
  const val = (id, fallback) => {
    const el = document.getElementById(id);
    if (!el) return (fallback !== undefined && fallback !== null) ? String(fallback) : '';
    return String(el.value !== undefined && el.value !== null ? el.value : '');
  };
  const asInt = (raw, fallback) => {
    const n = parseInt(String(raw || ''), 10);
    return isFinite(n) ? n : fallback;
  };
  const asFloat = (raw, fallback) => {
    const n = parseFloat(String(raw || ''));
    return isFinite(n) ? n : fallback;
  };

  const f = getFilters();
  const catEls = Array.from(document.querySelectorAll('.f_cat'));
  let categoryMask = {};
  if (catEls.length) {
    catEls.forEach(cb => { try { categoryMask[cb.value] = !!cb.checked; } catch (_) { } });
  } else if (cfg.category_mask && typeof cfg.category_mask === 'object') {
    categoryMask = Object.assign({}, cfg.category_mask);
  }

  const lastDays = asInt(val('f_last_n_days', cfg.date_last_n_days || 0), 0);
  return {
    category_mask: categoryMask,
    last_filters: {
      categories: f.categories || [],
      token_min: f.token_min,
      token_max: f.token_max,
      size_min: f.size_min,
      size_max: f.size_max,
      name_pattern: f.name_pattern || "",
      date_after: f.date_after,
      date_before: f.date_before,
      date_after_manual: _getDateManualFlag(document.getElementById('f_date_after'), DATE_AFTER_DEFAULT),
      date_before_manual: _getDateManualFlag(document.getElementById('f_date_before'), DATE_BEFORE_DEFAULT),
    },
    date_last_n_days: lastDays,
    name_filter_glob: checked('f_name_glob', cfg.name_filter_glob),
    content_search: f.content_search || '',
    content_regex: !!f.content_regex,
    content_case_sensitive: !!f.content_case_sensitive,
    content_max_kb: asInt(f.content_max_kb, 256),
    duplicate_hash_max_kb: asInt(f.duplicate_hash_max_kb, 256),
    content_hide_duplicates: !!f.content_hide_duplicates,
    content_mark_duplicates: !!f.content_mark_duplicates,
    apply_token_filters_to_transforms: !!f.apply_token_filters_to_transforms,
    always_include_docs: !!f.always_include_docs,
    ignore_binary_files: checked('f_ignore_binary', cfg.ignore_binary_files),
    max_file_size_mb: asFloat(val('f_max_mb', cfg.max_file_size_mb), 0.0),
    max_walk_depth: asInt(val('f_max_depth', cfg.max_walk_depth), 0),
    follow_symlinks: checked('f_follow_symlinks', cfg.follow_symlinks),
    exclude_node_modules: checked('f_ex_node', (cfg.exclude_node_modules !== undefined) ? cfg.exclude_node_modules : true),
    exclude_ios_noise: checked('f_ex_ios', (cfg.exclude_ios_noise !== undefined) ? cfg.exclude_ios_noise : true),
    exclude_python_noise: checked('f_ex_py', cfg.exclude_python_noise),
    exclude_java_gradle_noise: checked('f_ex_java', cfg.exclude_java_gradle_noise),
    exclude_web_build_noise: checked('f_ex_web', cfg.exclude_web_build_noise),
    use_git_files: checked('f_use_git', cfg.use_git_files),
    git_include_untracked: checked('f_git_untracked', cfg.git_include_untracked),
    git_only_changed: checked('f_git_changed', cfg.git_only_changed),
    git_since_ref: val('f_git_since', cfg.git_since_ref || ''),
    git_until_ref: val('f_git_until', cfg.git_until_ref || 'HEAD'),
    exclude_vendored: checked('f_ex_vendored', cfg.exclude_vendored),
    sort_by: (document.getElementById('sort_by') && document.getElementById('sort_by').value) ? document.getElementById('sort_by').value : (cfg.sort_by || 'name'),
    sort_descending: checked('sort_desc', cfg.sort_descending),
    view_mode: val('view_mode', cfg.view_mode || 'hierarchy'),
  };
}

function persistFilterState() {
  try { persistConfigPatch(buildFilterConfigPatch()); } catch (_) { }
}

function _validateNamePattern() {
  try { if (getVisibilityMode() === 'all') return true; } catch (_) { }
  const input = document.getElementById('f_name_pattern');
  const glob = document.getElementById('f_name_glob');
  if (!input || !glob) return true;
  const raw = String(input.value || '').trim();
  if (!raw || glob.checked) {
    if (window.__NAME_PATTERN_INVALID) {
      window.__NAME_PATTERN_INVALID = false;
      if (window.__NAME_PATTERN_BANNER && document.getElementById('banner') && document.getElementById('banner').textContent === window.__NAME_PATTERN_BANNER) {
        hideBanner();
      }
    }
    input.classList.remove('invalid');
    return true;
  }
  try {
    new RegExp(raw);
    if (window.__NAME_PATTERN_INVALID) {
      window.__NAME_PATTERN_INVALID = false;
      if (window.__NAME_PATTERN_BANNER && document.getElementById('banner') && document.getElementById('banner').textContent === window.__NAME_PATTERN_BANNER) {
        hideBanner();
      }
    }
    input.classList.remove('invalid');
    return true;
  } catch (err) {
    const msg = `Invalid regex: ${err && err.message ? err.message : err}`;
    window.__NAME_PATTERN_INVALID = true;
    window.__NAME_PATTERN_BANNER = msg;
    input.classList.add('invalid');
    showBanner(msg, 'warn');
    return false;
  }
}

let __FILTER_SCAN_TIMER = null;
function scheduleFilterScan(delayMs = 650) {
  if (!_validateNamePattern()) return;
  try { updateActiveFiltersSummary(); } catch (_) { }
  try { schedulePersistFilters(Math.max(200, delayMs || 0)); } catch (_) { }
  try { if (__FILTER_SCAN_TIMER) clearTimeout(__FILTER_SCAN_TIMER); } catch (_) { }
  __FILTER_SCAN_TIMER = setTimeout(() => { try { requestFilterApply(); } catch (_) { } }, Math.max(0, delayMs || 0));
}

let __FINAL_TOKEN_TIMER = null;
function scheduleFinalTokenRecalc(delayMs = 400) {
  try { if (__FINAL_TOKEN_TIMER) clearTimeout(__FINAL_TOKEN_TIMER); } catch (_) { }
  __FINAL_TOKEN_TIMER = setTimeout(() => {
    if (window.__SCAN_RUNNING || window.__FILTER_RUNNING) return;
    try { maybeComputeFinalTokens(window.__SCAN_ID); } catch (_) { }
  }, Math.max(0, delayMs || 0));
}

function bindScanEvents() {
  const onInput = (id, delayMs = 650) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('input', () => scheduleFilterScan(delayMs));
  };
  const onChange = (id, delayMs = 200) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('change', () => scheduleFilterScan(delayMs));
  };

  onInput('f_name_pattern', 650);
  onChange('f_name_glob', 150);
  onInput('f_content', 650);
  onChange('f_content_regex', 150);
  onChange('f_content_case', 150);
  onChange('f_content_kb', 150);
  onChange('f_dup_kb', 150);
  onInput('f_token_min', 400);
  onInput('f_token_max', 400);
  onInput('f_size_min', 400);
  onInput('f_size_max', 400);
  const after = document.getElementById('f_date_after');
  if (after) after.addEventListener('change', () => {
    const v = String(after.value || '').trim();
    after.dataset.manual = (v && v !== DATE_AFTER_DEFAULT) ? '1' : '0';
    scheduleFilterScan(200);
  });
  const before = document.getElementById('f_date_before');
  if (before) before.addEventListener('change', () => {
    const v = String(before.value || '').trim();
    before.dataset.manual = (v && v !== DATE_BEFORE_DEFAULT) ? '1' : '0';
    scheduleFilterScan(200);
  });
  onChange('f_last_n_days', 200);
  onChange('f_hide_dups', 200);
  onChange('f_mark_dups', 200);
  onChange('f_apply_token_transforms', 200);
  onChange('f_always_docs', 200);

  const sortBy = document.getElementById('sort_by');
  const sortDesc = document.getElementById('sort_desc');
  if (sortBy) sortBy.addEventListener('change', () => {
    window.__HEADER_SORT_ACTIVE = false;
    window.__PREFER_CONFIG_SORT = true;
    try { updateHeaderSortIndicators(); } catch (_) { }
    scheduleFilterScan(100);
    persistConfigPatch({ sort_by: sortBy.value || 'name', sort_descending: !!(sortDesc && sortDesc.checked) });
  });
  if (sortDesc) sortDesc.addEventListener('change', () => {
    window.__HEADER_SORT_ACTIVE = false;
    window.__PREFER_CONFIG_SORT = true;
    try { updateHeaderSortIndicators(); } catch (_) { }
    scheduleFilterScan(100);
    persistConfigPatch({ sort_by: (sortBy && sortBy.value) ? sortBy.value : 'name', sort_descending: !!sortDesc.checked });
  });

  const applyTrans = document.getElementById('f_apply_token_transforms');
  if (applyTrans) applyTrans.addEventListener('change', () => {
    persistConfigPatch({ apply_token_filters_to_transforms: !!applyTrans.checked });
  });

  const clearBtn = document.getElementById('btn_clear_pattern');
  if (clearBtn) clearBtn.addEventListener('click', () => {
    try { const el = document.getElementById('f_name_pattern'); if (el) el.value = ''; } catch (_) { }
    _validateNamePattern();
    scheduleFilterScan(50);
  });
}

function resetTokenRange() {
  const setVal = (id, v) => { const el = document.getElementById(id); if (el) el.value = String(v); };
  setVal('f_token_min', 0);
  setVal('f_token_max', 10000000);
  setVal('f_size_min', 0);
  setVal('f_size_max', 1000000);
  scheduleFilterScan(50);
}

function bindAdvancedFilterEvents() {
  const onChangeRescan = (id, delay = 200) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('change', () => {
      try { updateActiveFiltersSummary(); } catch (_) { }
      schedulePersistFilters(0);
      requestScan(delay);
    });
  };
  const onChangeRefilter = (id, delay = 200) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('change', () => {
      try { updateActiveFiltersSummary(); } catch (_) { }
      schedulePersistFilters(0);
      requestFilterApply(delay);
    });
  };

  const ignorePersisted = document.getElementById('f_ignore_persisted');
  if (ignorePersisted) ignorePersisted.addEventListener('change', () => { try { updateActiveFiltersSummary(); } catch (_) { } requestFilterApply(150); });

  onChangeRescan('f_ignore_binary', 200);
  onChangeRescan('f_max_mb', 200);
  onChangeRescan('f_max_depth', 200);
  onChangeRescan('f_follow_symlinks', 200);
  onChangeRescan('f_use_git', 200);
  onChangeRescan('f_git_untracked', 200);
  onChangeRescan('f_git_changed', 200);
  onChangeRescan('f_git_since', 200);
  onChangeRescan('f_git_until', 200);
  onChangeRescan('f_ex_vendored', 200);
  onChangeRescan('f_ex_node', 200);
  onChangeRescan('f_ex_ios', 200);
  onChangeRescan('f_ex_py', 200);
  onChangeRescan('f_ex_java', 200);
  onChangeRescan('f_ex_web', 200);
  onChangeRefilter('f_xcode_target', 200);
}

function openCategoriesModal() {
  const m = document.getElementById('categories_modal');
  if (!m) return;
  try { applyModalGeometry('categories_modal', 'modal_geometry_categories'); } catch (_) { }
  m.style.display = 'flex';
}
function closeCategoriesModal() {
  const m = document.getElementById('categories_modal');
  if (isPanelRoute()) { goMain(); return; }
  if (m) m.style.display = 'none';
  try { persistModalGeometry('categories_modal', 'modal_geometry_categories'); } catch (_) { }
}
function applyCategoriesAndClose() {
  closeCategoriesModal();
  try { schedulePersistFilters(0); } catch (_) { }
  try { updateActiveFiltersSummary(); } catch (_) { }
  requestFilterApply(50);
}

function openSettingsModal() {
  const m = document.getElementById('settings_modal');
  if (!m) return;
  try { applyModalGeometry('settings_modal', 'modal_geometry_settings'); } catch (_) { }
  // Snapshot UI state so Cancel can restore after preview changes.
  try {
    window.__UI_PREVIEW_SNAPSHOT = {
      fontSize: (document.documentElement && document.documentElement.style) ? (document.documentElement.style.fontSize || '') : '',
      contrast: !!document.body.classList.contains('contrast'),
      compact: !!document.body.classList.contains('compact'),
    };
  } catch (_) { }
  m.style.display = 'flex';
  try { loadSettings(); } catch (_) { }
  try {
    const firstBtn = m.querySelector('.tabs-nav .tab-btn');
    if (firstBtn) switchTab('tab_types', firstBtn);
  } catch (_) { }
}
function closeSettingsModal() {
  const m = document.getElementById('settings_modal');
  if (isPanelRoute()) { goMain(); return; }
  if (m) m.style.display = 'none';
  try { persistModalGeometry('settings_modal', 'modal_geometry_settings'); } catch (_) { }
  try {
    const snap = window.__UI_PREVIEW_SNAPSHOT;
    if (snap && typeof snap === 'object') {
      if (document.documentElement && document.documentElement.style) document.documentElement.style.fontSize = snap.fontSize || '';
      document.body.classList.toggle('contrast', !!snap.contrast);
      document.body.classList.toggle('compact', !!snap.compact);
    }
  } catch (_) { }
}

function switchTab(tabId, btnElement) {
  const modal = document.getElementById('settings_modal');
  if (!modal) return;
  modal.querySelectorAll('.tab-pane').forEach(el => el.classList.remove('active'));
  modal.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  const pane = document.getElementById(tabId);
  if (pane) pane.classList.add('active');
  if (btnElement) btnElement.classList.add('active');
}

function applyUiPreview() {
  try {
    const fsEl = document.getElementById('s_font');
    const fs = fsEl ? parseInt(String(fsEl.value || ''), 10) : NaN;
    if (isFinite(fs) && fs > 0) document.documentElement.style.fontSize = fs + 'px';
  } catch (_) { }
  try {
    const contrastEl = document.getElementById('s_contrast');
    if (contrastEl) document.body.classList.toggle('contrast', !!contrastEl.checked);
  } catch (_) { }
  try {
    const compactEl = document.getElementById('s_compact');
    if (compactEl) document.body.classList.toggle('compact', !!compactEl.checked);
  } catch (_) { }
}

function initModalResizeHandles() {
  const inners = Array.from(document.querySelectorAll('.modal_inner.resizable'));
  inners.forEach(inner => {
    if (!inner || inner.querySelector('.resize-grip')) return;
    const grip = document.createElement('div');
    grip.className = 'resize-grip';
    grip.setAttribute('aria-hidden', 'true');
    inner.appendChild(grip);
    bindResizeGrip(grip, inner);
  });
}

function bindResizeGrip(grip, inner) {
  const state = {
    active: false,
    pointerId: null,
    startX: 0,
    startY: 0,
    startW: 0,
    startH: 0,
    minW: 0,
    minH: 0,
    maxW: 0,
    maxH: 0,
  };
  const clamp = (value, min, max) => {
    let next = value;
    if (Number.isFinite(min)) next = Math.max(next, min);
    if (Number.isFinite(max)) next = Math.min(next, max);
    return next;
  };
  const computeBounds = () => {
    const cs = window.getComputedStyle(inner);
    const minW = parseFloat(cs.minWidth);
    const minH = parseFloat(cs.minHeight);
    const maxW = parseFloat(cs.maxWidth);
    const maxH = parseFloat(cs.maxHeight);
    state.minW = Number.isFinite(minW) ? minW : 240;
    state.minH = Number.isFinite(minH) ? minH : 200;
    const viewportMaxW = Math.max(240, window.innerWidth - 32);
    const viewportMaxH = Math.max(200, window.innerHeight - 32);
    state.maxW = Number.isFinite(maxW) ? Math.min(maxW, viewportMaxW) : viewportMaxW;
    state.maxH = Number.isFinite(maxH) ? Math.min(maxH, viewportMaxH) : viewportMaxH;
  };
  const startResize = (x, y, pointerId) => {
    const rect = inner.getBoundingClientRect();
    state.active = true;
    state.pointerId = pointerId;
    state.startX = x;
    state.startY = y;
    state.startW = rect.width;
    state.startH = rect.height;
    computeBounds();
    try { document.body.style.userSelect = 'none'; } catch (_) { }
  };
  const moveResize = (x, y) => {
    if (!state.active) return;
    const dx = x - state.startX;
    const dy = y - state.startY;
    const nextW = clamp(state.startW + dx, state.minW, state.maxW);
    const nextH = clamp(state.startH + dy, state.minH, state.maxH);
    inner.style.width = Math.round(nextW) + 'px';
    inner.style.height = Math.round(nextH) + 'px';
  };
  const endResize = () => {
    if (!state.active) return;
    state.active = false;
    state.pointerId = null;
    try { document.body.style.userSelect = ''; } catch (_) { }
  };

  if (window.PointerEvent) {
    grip.addEventListener('pointerdown', (e) => {
      if (!e) return;
      e.preventDefault();
      e.stopPropagation();
      startResize(e.clientX, e.clientY, e.pointerId);
      try { grip.setPointerCapture(e.pointerId); } catch (_) { }
    });
    window.addEventListener('pointermove', (e) => {
      if (!state.active) return;
      moveResize(e.clientX, e.clientY);
    });
    const finish = () => { endResize(); };
    window.addEventListener('pointerup', finish);
    window.addEventListener('pointercancel', finish);
  } else {
    grip.addEventListener('mousedown', (e) => {
      if (!e) return;
      e.preventDefault();
      e.stopPropagation();
      startResize(e.clientX, e.clientY, null);
    });
    window.addEventListener('mousemove', (e) => {
      if (!state.active) return;
      moveResize(e.clientX, e.clientY);
    });
    window.addEventListener('mouseup', () => { endResize(); });
  }
}

function applyModalGeometry(modalId, key) {
  try {
    const modal = document.getElementById(modalId);
    if (!modal) return;
    const inner = modal.querySelector('.modal_inner');
    if (!inner) return;
    const raw = localStorage.getItem(key || ('modal_geometry_' + modalId));
    if (!raw) return;
    const data = JSON.parse(raw);
    if (data && data.width) inner.style.width = data.width + 'px';
    if (data && data.height) inner.style.height = data.height + 'px';
  } catch (_) { }
}

function persistModalGeometry(modalId, key) {
  try {
    const modal = document.getElementById(modalId);
    if (!modal) return;
    const inner = modal.querySelector('.modal_inner');
    if (!inner) return;
    const rect = inner.getBoundingClientRect();
    const payload = { width: Math.round(rect.width), height: Math.round(rect.height) };
    localStorage.setItem(key || ('modal_geometry_' + modalId), JSON.stringify(payload));
  } catch (_) { }
}

async function persistOutputPath() {
  const el = document.getElementById('output_file');
  if (!el) return;
  const output = String(el.value || '').trim();
  try { await fetch('/api/config', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ last_output: output }) }); } catch (_) { }
  try { await allowOutputPath(output); } catch (_) { }
}

async function allowOutputPath(path) {
  const out = String(path || '').trim();
  if (!out) return;
  try { await fetch('/api/allow_path', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: out }) }); } catch (_) { }
}

async function pathExists(path) {
  try {
    const res = await fetch('/api/path/exists', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path }) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return { exists: false, is_file: false, is_dir: false };
    return data || { exists: false, is_file: false, is_dir: false };
  } catch (_) {
    return { exists: false, is_file: false, is_dir: false };
  }
}

function _splitPathExt(path) {
  const raw = String(path || '');
  const sepIdx = Math.max(raw.lastIndexOf('/'), raw.lastIndexOf('\\'));
  const dotIdx = raw.lastIndexOf('.');
  if (dotIdx > sepIdx) {
    return { base: raw.slice(0, dotIdx), ext: raw.slice(dotIdx) };
  }
  return { base: raw, ext: '' };
}

async function findAvailableFilename(path) {
  const out = String(path || '').trim();
  if (!out) return out;
  const parts = _splitPathExt(out);
  let counter = 2;
  while (counter < 10000) {
    const candidate = `${parts.base}_${counter}${parts.ext}`;
    try { await allowOutputPath(candidate); } catch (_) { }
    const exists = await pathExists(candidate);
    if (!exists.exists) return candidate;
    counter += 1;
  }
  return out;
}

function showOverwriteModal(path) {
  const modal = document.getElementById('overwrite_modal');
  if (!modal) {
    const ok = confirm(`Output file exists:\n${path}\nOverwrite?`);
    return Promise.resolve(ok ? 'overwrite' : 'cancel');
  }
  const pathEl = document.getElementById('overwrite_path');
  if (pathEl) pathEl.textContent = path;
  const btnOverwrite = document.getElementById('overwrite_confirm');
  const btnRename = document.getElementById('overwrite_rename');
  const btnCancel = document.getElementById('overwrite_cancel');
  return new Promise((resolve) => {
    window.__OVERWRITE_MODAL_RESOLVE = resolve;
    if (btnOverwrite) btnOverwrite.onclick = () => hideOverwriteModal('overwrite');
    if (btnRename) btnRename.onclick = () => hideOverwriteModal('rename');
    if (btnCancel) btnCancel.onclick = () => hideOverwriteModal('cancel');
    modal.style.display = 'flex';
  });
}

function hideOverwriteModal(action) {
  const modal = document.getElementById('overwrite_modal');
  if (modal) modal.style.display = 'none';
  const resolve = window.__OVERWRITE_MODAL_RESOLVE;
  window.__OVERWRITE_MODAL_RESOLVE = null;
  if (resolve) resolve(action || 'cancel');
}

async function confirmOverwriteIfNeeded(outputPath) {
  const out = String(outputPath || '').trim();
  if (!out) return { action: 'overwrite', path: out };
  try { await allowOutputPath(out); } catch (_) { }
  try {
    const data = await pathExists(out);
    if (data.exists && data.is_file) {
      const action = await showOverwriteModal(out);
      if (action === 'rename') {
        const alt = await findAvailableFilename(out);
        return { action: 'rename', path: alt };
      }
      if (action === 'overwrite') return { action: 'overwrite', path: out };
      return { action: 'cancel', path: out };
    }
  } catch (_) { }
  return { action: 'create', path: out };
}

async function browseOutputFile() {
  const el = document.getElementById('output_file');
  const cur = el ? String(el.value || '') : '';
  const lastDir = String((document.getElementById('last_directory') || {}).value || '').trim();
  // Prefer native dialogs via pywebview in desktop wrapper mode (best-effort).
  try {
    if (window.pywebview && window.pywebview.api) {
      const api = window.pywebview.api;
      if (api.save_file_dialog) {
        const chosen = await api.save_file_dialog(cur || null);
        if (chosen && typeof chosen === 'string') {
          if (el) el.value = chosen;
          await persistOutputPath();
          return;
        }
      }
    }
  } catch (_) { /* fall back */ }
  const value = cur.trim();
  const hint = _normalizePathSlashes(value);
  const metadata = hint && !hint.endsWith('/') ? await pathExists(value) : {};
  const tail = hint.split('/').pop() || '';
  const hasFilename = metadata.is_file || (
    !metadata.is_dir && tail.includes('.') && tail !== '.' && tail !== '..'
  );
  const parent = hint.slice(0, hint.lastIndexOf('/') + 1);
  const picked = await _pickPaths({
    allowDirs: true,
    allowFiles: false,
    single: true,
    path: (hasFilename ? parent : hint) || lastDir,
  });
  if (Array.isArray(picked) && picked.length) {
    const folder = _normalizePathSlashes(picked[0]);
    const defaultPath = _normalizePathSlashes(computeDefaultOutputPath(lastDir));
    const fileName = (hasFilename && tail) || defaultPath.split('/').pop() || 'merged_files.txt';
    if (el) el.value = folder ? `${_stripTrailingSlashes(folder)}/${fileName}` : fileName;
    await persistOutputPath();
  }
}

function _formatTimestampLocal() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  const y = d.getFullYear();
  const m = pad(d.getMonth() + 1);
  const day = pad(d.getDate());
  const hh = pad(d.getHours());
  const mm = pad(d.getMinutes());
  const ss = pad(d.getSeconds());
  return `${y}${m}${day}_${hh}${mm}${ss}`;
}

function computeDefaultOutputPath(root) {
  const base = String(root || '').replace(/[\\\/]+$/, '');
  const useFolder = !!(document.getElementById('use_folder_name') && document.getElementById('use_folder_name').checked);
  const appendDt = !!(document.getElementById('append_date') && document.getElementById('append_date').checked);
  const baseName = useFolder && base ? base.split(/[\\/]/).pop() : 'merged_files';
  const stamp = appendDt ? ('_' + _formatTimestampLocal()) : '';
  const name = `${baseName || 'merged_files'}${stamp}.txt`;
  return base ? (base + '/' + name) : name;
}

function _escapeRegExp(value) {
  return String(value || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function _isAutoOutputPath(path, root) {
  const rawPath = _stripTrailingSlashes(_normalizePathSlashes(String(path || '').trim()));
  if (!rawPath) return true;
  const rootNorm = _stripTrailingSlashes(_normalizePathSlashes(String(root || '').trim()));
  const rootName = rootNorm ? rootNorm.split('/').filter(Boolean).pop() : '';
  const nameParts = ['merged_files'];
  if (rootName) nameParts.push(rootName);
  const namePart = nameParts.map(_escapeRegExp).join('|');
  const suffix = '(?:_\\d{8}_\\d{6})?\\.txt$';
  const bareRe = new RegExp(`^(?:${namePart})${suffix}`);
  if (bareRe.test(rawPath)) return true;
  if (!rootNorm) return false;
  const withRootRe = new RegExp(`^${_escapeRegExp(rootNorm)}\\/(?:${namePart})${suffix}`);
  return withRootRe.test(rawPath);
}

async function refreshOutputPathForNamingPrefs(opts) {
  opts = opts || {};
  const el = document.getElementById('output_file');
  if (!el) return;
  const root = (opts.root !== undefined && opts.root !== null)
    ? String(opts.root || '')
    : String((document.getElementById('last_directory') || {}).value || '');
  const current = String(el.value || '').trim();
  const force = !!opts.force;
  const prevRoot = String(window.__OUTPUT_ROOT_LAST || '').trim();
  if (!force && current && !_isAutoOutputPath(current, root) && (!prevRoot || !_isAutoOutputPath(current, prevRoot))) return;
  const next = computeDefaultOutputPath(root);
  if (next && next !== current) {
    el.value = next;
    try { await persistOutputPath(); } catch (_) { }
  }
  try { window.__OUTPUT_ROOT_LAST = String(root || ''); } catch (_) { }
}

async function browsePathIntoInput(inputId, opts) {
  const el = document.getElementById(inputId);
  if (!el) return;
  const cur = String(el.value || '');
  const allowDirs = !!(opts && opts.allowDirs);
  const allowFiles = !!(opts && opts.allowFiles);
  const picked = await _pickPaths({
    allowDirs,
    allowFiles,
    single: true,
    path: cur || (document.getElementById('last_directory') || {}).value || '',
  });
  if (Array.isArray(picked) && picked.length && el) {
    el.value = picked[0];
    try { el.dispatchEvent(new Event('change')); } catch (_) { }
  }
}

async function browsePruneOutput() {
  const el = document.getElementById('pr_output');
  const cur = el ? String(el.value || '') : '';
  try {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.save_file_dialog) {
      const chosen = await window.pywebview.api.save_file_dialog(cur || null);
      if (chosen && typeof chosen === 'string') {
        if (el) el.value = chosen;
        persistConfigPatch({ prune_output_path: chosen });
        try { await allowOutputPath(chosen); } catch (_) { }
        return;
      }
    }
  } catch (_) { }
  const entered = prompt('Prune output file path:', cur || 'pruned_result.txt');
  if (entered && typeof entered === 'string') {
    if (el) el.value = entered;
    persistConfigPatch({ prune_output_path: entered });
    try { await allowOutputPath(entered); } catch (_) { }
  }
}

async function loadContextPackSubsystems() {
  const sel = document.getElementById('ctx_pack_subsystem');
  if (!sel) return;
  const rootEl = document.getElementById('last_directory');
  const root = (rootEl && rootEl.value) ? rootEl.value : ((window.__SCAN && window.__SCAN.root) || '');
  const base = String(root || '').trim();
  if (!base) {
    sel.innerHTML = '<option value="">(set source folder)</option>';
    return;
  }
  const prior = String(sel.value || '');
  sel.innerHTML = '<option value="">(loading...)</option>';
  try {
    const res = await fetch('/api/context_pack/subsystems?path=' + encodeURIComponent(base));
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    const raw = Array.isArray(data.subsystems) ? data.subsystems : [];
    const subsystems = raw
      .map(s => (typeof s === 'string' ? s.trim() : ''))
      .filter(Boolean);
    if (!subsystems.length) {
      sel.innerHTML = '<option value="">(none)</option>';
      return;
    }
    sel.innerHTML = subsystems.map(name => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join('');
    if (prior && subsystems.includes(prior)) sel.value = prior;
    else sel.value = subsystems[0];
  } catch (err) {
    sel.innerHTML = '<option value="">(error)</option>';
    const msg = err && err.message ? err.message : err;
    showBanner('Context pack load failed: ' + msg, 'warn');
  }
}

async function buildContextPack() {
  if (window.__CTX_PACK_BUSY) return;
  window.__CTX_PACK_BUSY = true;
  const out = document.getElementById('ctx_pack_out');
  try {
    const root = (document.getElementById('last_directory') || {}).value || (window.__SCAN && window.__SCAN.root) || '';
    const base = String(root || '').trim();
    if (!base) {
      showBanner('Set a source directory first.', 'warn');
      if (out) out.textContent = 'Missing source directory.';
      return;
    }
    const subsystemEl = document.getElementById('ctx_pack_subsystem');
    const subsystem = subsystemEl ? String(subsystemEl.value || '').trim() : '';
    if (!subsystem) {
      showBanner('Select a subsystem.', 'warn');
      if (out) out.textContent = 'No subsystem selected.';
      return;
    }
    const modeEl = document.getElementById('ctx_pack_mode');
    const modeRaw = modeEl ? String(modeEl.value || 'subsystem').trim().toLowerCase() : 'subsystem';
    const mode = modeRaw === 'audit' ? 'audit' : 'subsystem';
    const save = !!(document.getElementById('ctx_pack_save') && document.getElementById('ctx_pack_save').checked);
    const copy = !!(document.getElementById('ctx_pack_copy') && document.getElementById('ctx_pack_copy').checked);
    if (!save && !copy) {
      showBanner('Enable save and/or copy.', 'warn');
      if (out) out.textContent = 'No output target selected.';
      return;
    }
    const outputEl = document.getElementById('ctx_pack_output');
    let outputPath = outputEl ? String(outputEl.value || '').trim() : '';
    if (!save) outputPath = '';
    if (save && !outputPath) {
      showBanner('Provide an output file path or disable save.', 'warn');
      if (out) out.textContent = 'Missing output file path.';
      return;
    }
    if (out) out.textContent = 'Building context pack…';
    try { showOverlay('Building context pack…'); } catch (_) { }
    if (save && outputPath) { try { await allowOutputPath(outputPath); } catch (_) { } }
    const payload = {
      path: base,
      subsystem,
      mode,
      output_path: save ? (outputPath || undefined) : undefined,
      return_text: !!(copy || !save),
    };
    const res = await fetch('/api/context_pack/build', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    const result = data && data.result ? data.result : {};
    const included = Array.isArray(result.included_files) ? result.included_files : [];
    const warnings = Array.isArray(result.warnings) ? result.warnings : [];
    const output = result.output_path || '';
    const summary = [];
    summary.push(`Subsystem: ${result.subsystem || subsystem}`);
    summary.push(`Mode: ${result.mode || mode}`);
    if (output) summary.push(`Output: ${output}`);
    summary.push(`Files: ${included.length}`);
    if (warnings.length) {
      summary.push('Warnings:');
      warnings.forEach(w => summary.push(`- ${w}`));
    }
    if (out) out.textContent = summary.join('\n');
    if (output) { try { await allowOutputPath(output); } catch (_) { } }
    if (copy) {
      const text = typeof result.output_text === 'string' ? result.output_text : '';
      const ok = text ? await copyTextSmart(text) : false;
      showBanner(ok ? 'Context pack copied to clipboard.' : 'Copy failed.', ok ? 'ok' : 'warn');
    } else {
      showBanner('Context pack built.', 'ok');
    }
  } catch (err) {
    const msg = err && err.message ? err.message : err;
    if (out) out.textContent = 'Context pack failed: ' + msg;
    showError('Context Pack Failed', msg);
  } finally {
    window.__CTX_PACK_BUSY = false;
    try { hideOverlay(); } catch (_) { }
  }
}

function clearAllVisible() {
  const visible = (window.__VISIBLE || (window.__SCAN && window.__SCAN.files) || []);
  const count = visible.length || 0;
  if (!count) return;
  if (!confirm(`Clear all visible files (${count}) from the list?`)) return;
  const targets = visible.map(f => f && f.abs_path).filter(Boolean);
  if (!targets.length) return;
  try {
    excludePathsPersist(targets);
  } catch (err) {
    try { showBanner('Clear all failed: ' + err, 'warn'); } catch (_) { alert('Clear all failed: ' + err); }
  }
}

function restoreFullList() {
  (async () => {
    try {
      await fetch('/api/custom_excluded/clear', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    } catch (_) { }
    try { clearHidden(); } catch (_) { }
    try {
      if (window.__SCAN && window.__SCAN.root) await doFilterApply();
      else await doScan(true);
    } catch (_) { }
  })();
}

function applyModeFromToolbar() {
  const scope = (document.getElementById('mode_scope') || {}).value || 'selected';
  const mode = (document.getElementById('mode_target') || {}).value || 'CODE';
  let targets = [];
  if (scope === 'visible') {
    const visible = (window.__VISIBLE || (window.__SCAN && window.__SCAN.files) || []);
    targets = visible.map(f => f && f.abs_path).filter(Boolean);
  } else {
    targets = Array.from(window.__SEL || []);
  }
  targets = Array.from(new Set((targets || []).filter(p => typeof p === 'string' && p.trim()).map(p => p.trim())));
  if (!targets.length) {
    try { showBanner('No targets for mode apply (select rows or choose All Visible).', 'warn'); } catch (_) { }
    return;
  }
  setModeForPaths(targets, mode);
}

const EXT_GROUPS = {
  CODE: new Set(["py", "js", "jsx", "ts", "tsx", "go", "rs", "c", "cpp", "swift", "java", "m", "mm", "h", "hh", "hpp", "cxx", "mpp", "metal", "modulemap", "xcconfig"]),
  MARKUP: new Set(["html", "xml", "svg", "md", "rst", "storyboard", "xib"]),
  DATA: new Set(["json", "yml", "yaml", "toml", "sql", "csv", "plist", "pbxproj", "strings", "entitlements", "xcworkspacedata", "xcscheme", "xcuserstate", "xcsettings"]),
  BINARY: new Set(["png", "jpg", "jpeg", "gif", "ico", "webp", "mp4", "mp3", "wav"]),
};

async function applyExtGroup(groupName) {
  const name = String(groupName || '').trim();
  if (!name) return;

  const incAll = document.getElementById('include_all');
  if (incAll && incAll.checked) {
    try { showBanner('Uncheck "All Files" to use extension groups.', 'warn'); } catch (_) { }
    return;
  }

  const pills = Array.from(document.querySelectorAll('.group-pills .pill'));
  pills.forEach(p => p.classList.toggle('active', p.getAttribute('data-group') === name));

  let exts = [];
  const cbs = Array.from(document.querySelectorAll('#f_types input.ft_cb'));
  if (cbs.length) {
    exts = cbs.map(cb => cb.dataset.ext).filter(Boolean);
  } else {
    try {
      const res = await fetch('/api/config');
      const data = await res.json();
      const ft = (data && data.config && data.config.file_types) ? data.config.file_types : {};
      exts = Object.keys(ft || {});
    } catch (_) { exts = []; }
  }

  const patch = {};
  const upper = name.toUpperCase();
  const set = EXT_GROUPS[upper] || null;
  exts.forEach(ext => {
    if (upper === 'ALL') patch[ext] = true;
    else patch[ext] = !!(set && set.has(ext));
  });
  // Also ensure the group set keys exist in the map when using group-specific buttons.
  if (upper !== 'ALL' && set) {
    set.forEach(ext => { if (!(ext in patch)) patch[ext] = true; });
  }

  try {
    const res = await fetch('/api/config', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ file_types: patch }) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error((data && data.error) ? data.error : ('HTTP ' + res.status));
  } catch (err) {
    try { showBanner('Failed to apply group: ' + err, 'warn'); } catch (_) { }
    return;
  }

  // Reflect in UI (best-effort)
  cbs.forEach(cb => {
    const ext = cb.dataset.ext;
    if (!ext) return;
    if (upper === 'ALL') cb.checked = true;
    else cb.checked = !!(set && set.has(ext));
  });

  requestScan(50);
}

async function applyIOSPreset() {
  try {
    const incAll = document.getElementById('include_all'); if (incAll) incAll.checked = true;
    const ignBin = document.getElementById('f_ignore_binary'); if (ignBin) ignBin.checked = false;
    const maxMb = document.getElementById('f_max_mb'); if (maxMb) maxMb.value = '0';
    const follow = document.getElementById('f_follow_symlinks'); if (follow) follow.checked = false;
    const depth = document.getElementById('f_max_depth'); if (depth) depth.value = '0';
    const iosNoise = document.getElementById('f_ex_ios'); if (iosNoise) iosNoise.checked = true;
    document.querySelectorAll('.f_cat').forEach(cb => { cb.checked = true; });
    const pat = document.getElementById('f_name_pattern'); if (pat) pat.value = '';
    const glob = document.getElementById('f_name_glob'); if (glob) glob.checked = false;
    const docs = document.getElementById('f_always_docs'); if (docs) docs.checked = true;
    const vm = document.getElementById('view_mode'); if (vm) vm.value = 'hierarchy';
    syncViewModeRadios();
  } catch (_) { }
  try { applyIncludeAllState({ silent: false }); } catch (_) { }
  try { schedulePersistFilters(0); } catch (_) { }
  try { await doScan(false); } catch (_) { try { requestScan(); } catch (_) { } }
}

async function reloadXcodeTargets(pathOverride) {
  const dir = pathOverride || document.getElementById('last_directory').value;
  if (!dir) return;
  try {
    const resp = await fetch(`/api/xcode_targets?path=${encodeURIComponent(dir)}`);
    if (!resp.ok) return;
    const data = await resp.json();
    const sel = document.getElementById('f_xcode_target');
    if (sel && data.targets) {
      const cur = sel.value;
      sel.innerHTML = '<option value="">(All)</option>' + data.targets.map(t => `<option>${escapeHtml(t)}</option>`).join('');
      if (Array.from(sel.options).some(o => o.value === cur)) sel.value = cur;
    }
  } catch (_) { }
}

async function _downloadTextForPath(path) {
  const res = await fetch(await window.__downloadUrlForPath(path));
  if (!res.ok) throw new Error('Download failed (HTTP ' + res.status + ')');
  return await res.text();
}

async function _copyTextToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (_) { }
  // Fallback: hidden textarea
  try {
    const ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.left = '-9999px';
    document.body.appendChild(ta); ta.focus(); ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return !!ok;
  } catch (_) { return false; }
}

async function copyTextSmart(text) {
  if (text == null) return false;
  const str = String(text);
  if (!str) return false;
  try {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.copy_to_clipboard) {
      const ok = await window.pywebview.api.copy_to_clipboard(str);
      if (ok) return true;
    }
  } catch (_) { }
  return await _copyTextToClipboard(str);
}

async function copyLastMergeToClipboard() {
  return runMergeFromFooter({ preview: false, copy: true });
}

async function runMergeFromFooter(opts) {
  opts = opts || {};
  if (window.__MERGE_DASH_BUSY) return;
  window.__MERGE_DASH_BUSY = true;

  const status = document.getElementById('merge_status');
  const btnMerge = document.getElementById('btn_merge');
  const btnPrev = document.getElementById('btn_preview_merge');
  const btnCopy = document.getElementById('btn_copy_merge');

  const setBusy = (busy) => {
    try { if (btnMerge) btnMerge.disabled = busy; } catch (_) { }
    try { if (btnPrev) btnPrev.disabled = busy; } catch (_) { }
    try { if (btnCopy) btnCopy.disabled = busy; } catch (_) { }
  };

  setBusy(true);
  try {
    const root = (document.getElementById('last_directory') || {}).value || '';
    if (!root) throw new Error('Set Source first.');

    let selected_paths = Array.from(window.__SEL || []);
    selected_paths = Array.from(new Set(selected_paths));
    const selected_raw = selected_paths.slice();
    selected_paths = filterNonExcludedPaths(selected_paths);
    if (selected_raw.length && !selected_paths.length) {
      throw new Error('All selected files are excluded. Clear selection or change modes.');
    }
    if (!selected_paths.length) {
      const visible = (window.__VISIBLE || (window.__SCAN && window.__SCAN.files) || []);
      const candidates = visible
        .filter(f => f && f.abs_path && (String(f.mode || '').toUpperCase() !== 'EXCLUDE'))
        .map(f => f.abs_path);
      if (!candidates.length) throw new Error('No visible files to merge. Scan first or adjust filters.');
      selected_paths = Array.from(new Set(candidates));
    }

    setFooterStatus('Merging…', 'merge');

    const outEl = document.getElementById('output_file');
    let output_file = outEl ? String(outEl.value || '').trim() : '';
    const modeEl = document.getElementById('merge_structure');
    const mode = (modeEl && modeEl.value) ? String(modeEl.value) : 'single';
    const usePlan = true;
    const overwrite = !!(document.getElementById('merge_overwrite') && document.getElementById('merge_overwrite').checked);
    if (mode === 'single' && (!output_file || _isAutoOutputPath(output_file, root))) {
      output_file = computeDefaultOutputPath(root);
      if (outEl && output_file && output_file !== String(outEl.value || '').trim()) {
        outEl.value = output_file;
        try { await persistOutputPath(); } catch (_) { }
      }
    }
    let resolvedOutput = output_file;
    if (mode === 'single' && !resolvedOutput) {
      resolvedOutput = computeDefaultOutputPath(root);
    }

    const payload = {
      path: root,
      selected_paths,
      ...buildMergeOutputOptions(),
      use_plan: usePlan,
    };

    const doPreview = !!opts.preview;
    const doCopyButton = !!opts.copy;
    const copyOnMerge = !doPreview && !doCopyButton && !!(document.getElementById('copy_clipboard') && document.getElementById('copy_clipboard').checked);

    if (doPreview || doCopyButton) {
      const resPrev = await fetch('/api/merge/preview', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      const dataPrev = await resPrev.json();
      if (!resPrev.ok) throw new Error(dataPrev.error || ('HTTP ' + resPrev.status));
      const text = (dataPrev && typeof dataPrev.text === 'string') ? dataPrev.text : '';
      const metaBits = [];
      if (dataPrev.tokens_total !== undefined && dataPrev.tokens_total !== null) metaBits.push(`tokens ${dataPrev.tokens_total}`);
      if (dataPrev.tokens_content_only !== undefined && dataPrev.tokens_content_only !== null) metaBits.push(`content ${dataPrev.tokens_content_only}`);
      const meta = metaBits.join(' — ');
      if (doPreview) showPreviewModal({ title: 'Merge preview', meta, text, file_path: '' });
      if (doCopyButton) {
        if (!text) {
          showBanner('Copy failed: preview is empty', 'warn');
        } else {
          const ok = await copyTextSmart(text);
          if (ok) {
            showBanner('Merged output copied to clipboard', 'ok');
          } else {
            showBanner('Copy failed — showing preview', 'warn');
            try { showPreviewModal({ title: 'Merge preview', meta, text, file_path: '' }); } catch (_) { }
          }
        }
      }
      if (meta) setFooterStatus(meta, 'merge', { ttlMs: 4000 });
      return;
    }

    const startJob = async (endpoint, jobPayload) => {
      const res = await fetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(jobPayload) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
      const jobId = data.job_id;
      return await new Promise((resolve, reject) => {
        pollJobUntilDone(jobId, {
          label: 'Merging…',
          onProgress: (job) => {
            const pct = (typeof job.progress_pct === 'number') ? job.progress_pct : null;
            const msg = (job.progress && typeof job.progress === 'string') ? job.progress : 'Merging…';
            setFooterStatus(pct != null ? `${msg} (${pct}%)` : msg, 'merge');
            const om = document.getElementById('overlay_msg'); if (om) om.textContent = pct != null ? `${msg} (${pct}%)` : msg;
          },
          onDone: resolve,
          onError: (job) => {
            const errMsg = (job && job.error) ? job.error : job;
            reject(new Error(errMsg && errMsg.message ? errMsg.message : errMsg));
          },
        });
      });
    };

    const runWithOverwrite = async (endpoint, basePayload, forceOverwrite) => {
      try {
        return await startJob(endpoint, { ...basePayload, overwrite: !!forceOverwrite });
      } catch (err) {
        const msg = err && err.message ? err.message : err;
        const exists = /exist/i.test(String(msg || ''));
        if (!forceOverwrite && exists && confirm('Output folder exists. Overwrite?')) {
          return runWithOverwrite(endpoint, basePayload, true);
        }
        throw err;
      }
    };

    if (mode !== 'single' && output_file) payload.output_file = output_file;

    if (mode === 'single' && resolvedOutput) {
      const decision = await confirmOverwriteIfNeeded(resolvedOutput);
      if (decision.action === 'cancel') { setFooterStatus('Merge cancelled.', 'merge', { ttlMs: 4000 }); return; }
      const finalOut = decision.path || resolvedOutput;
      if (decision.action === 'rename' && outEl) {
        outEl.value = finalOut;
        try { await persistOutputPath(); } catch (_) { }
      }
      payload.output_file = finalOut;
      payload.overwrite = decision.action === 'overwrite';
    }

    if (mode === 'single') {
      const job = await startJob('/api/merge_job', payload);
      const data = (job && job.result) ? job.result : {};

      window.__LAST_MERGE_OUTPUT_PATH = data.output_path;
      window.__LAST_OUTPUT_FOLDER = null;
      if (outEl) outEl.value = data.output_path || '';
      try { await persistOutputPath(); } catch (_) { }

      handleMergeSummaryErrors(data, selected_paths);

      const a = document.getElementById('download_link');
      if (a && data.output_path) {
        a.href = await window.__downloadUrlForPath(data.output_path);
        a.style.display = 'inline';
      }
      updateOutputFolderButtons();

      const bits = [];
      if (data.output_path) bits.push(data.output_path);
      if (typeof data.size_bytes === 'number') bits.push(`bytes ${data.size_bytes}`);
      if (data.token_count !== undefined && data.token_count !== null) bits.push(`tokens ${data.token_count}`);
      setFooterStatus(bits.join(' — '), 'merge', { ttlMs: 6000 });

      if (copyOnMerge && data.output_path) {
        const text = await _downloadTextForPath(data.output_path);
        if (!text) {
          showBanner('Copy failed: output is empty', 'warn');
        } else {
          const ok = await copyTextSmart(text);
          if (ok) showBanner('Merged output copied to clipboard', 'ok'); else showBanner('Copy failed', 'warn');
        }
      }
    } else if (mode === 'multiple') {
      const split_into_n = parseInt((document.getElementById('merge_split_n') || {}).value || '5', 10);
      const basePayload = { ...payload, split_into_n };
      const job = await runWithOverwrite('/api/merge/multiple_job', basePayload, overwrite);
      const data = (job && job.result) ? job.result : {};
      handleMergeSummaryErrors(data, selected_paths);
      window.__LAST_OUTPUT_FOLDER = data.output_folder || null;
      setFooterStatus(`Output folder: ${data.output_folder}`, 'merge', { ttlMs: 6000 });
      const a = document.getElementById('download_link'); if (a) a.style.display = 'none';
      updateOutputFolderButtons();
    } else if (mode === 'token_split') {
      const token_limit = parseInt((document.getElementById('merge_token_limit') || {}).value || '1048576', 10);
      const basePayload = { ...payload, token_limit };
      try {
        const checkRes = await fetch('/api/merge/token_split/check', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(basePayload) });
        const checkData = await checkRes.json().catch(() => ({}));
        if (checkRes.ok) {
          const over = parseInt(checkData.over_limit_count || 0, 10) || 0;
          const largest = parseInt(checkData.largest_token_count || 0, 10) || 0;
          if (over > 0) {
            const msg = `Token-split warning:\n${over} file(s) exceed the ${token_limit} token limit.\nLargest file: ${largest} tokens.\n\nContinue?`;
            if (!confirm(msg)) { setFooterStatus('Merge cancelled.', 'merge', { ttlMs: 4000 }); return; }
          }
        }
      } catch (_) { }
      const job = await runWithOverwrite('/api/merge/token_split_job', basePayload, overwrite);
      const data = (job && job.result) ? job.result : {};
      handleMergeSummaryErrors(data, selected_paths);
      window.__LAST_OUTPUT_FOLDER = data.output_folder || null;
      setFooterStatus(`Output folder: ${data.output_folder}`, 'merge', { ttlMs: 6000 });
      const a = document.getElementById('download_link'); if (a) a.style.display = 'none';
      updateOutputFolderButtons();
    }
  } catch (err) {
    showError('Merge Failed', err && err.message ? err.message : err);
    setFooterStatus('Merge failed. See error details.', 'merge', { ttlMs: 8000 });
  } finally {
    setBusy(false);
    window.__MERGE_DASH_BUSY = false;
    try { hideOverlay(); } catch (_) { }
  }
}

function handleMergeSummaryErrors(data, requestedPaths) {
  if (!data || typeof data !== 'object') return;
  const errors = Array.isArray(data.summary_files_with_errors) ? data.summary_files_with_errors : [];
  const req = Array.isArray(requestedPaths) ? requestedPaths : [];
  if (errors.length || req.length) {
    updateSummaryErrors(errors, req);
  }
  if (errors.length) {
    try { showBanner(`Summary errors in ${errors.length} file(s).`, 'warn'); } catch (_) { }
  }
  if (data.summary_error) {
    try { showBanner(`Summary error: ${data.summary_error}`, 'warn'); } catch (_) { }
  }
}

function updateMergeModeControls() {
  const mode = (document.getElementById('merge_structure') || {}).value || 'single';
  const splitN = document.getElementById('merge_split_n');
  const tokenLimit = document.getElementById('merge_token_limit');
  const overwrite = document.getElementById('merge_overwrite');
  if (splitN) splitN.disabled = (mode !== 'multiple');
  if (tokenLimit) tokenLimit.disabled = (mode !== 'token_split');
  if (overwrite) overwrite.disabled = (mode === 'single');
  const btnPrev = document.getElementById('btn_preview_merge');
  const btnCopy = document.getElementById('btn_copy_merge');
  if (btnPrev) btnPrev.disabled = !!window.__MERGE_DASH_BUSY;
  if (btnCopy) btnCopy.disabled = !!window.__MERGE_DASH_BUSY;
  updateOutputFolderButtons();
}

function getMergePromptTemplate() {
  const el = document.getElementById('merge_prompt_template');
  if (!el) return '';
  const s = String(el.value || '').trim();
  return s;
}

function _normalizeRepoSummaryMode(value) {
  const raw = String(value || '').trim().toLowerCase();
  return raw === 'full' ? 'full' : 'quick';
}

function getRepoSummaryMode() {
  const el = document.getElementById('merge_repo_summary_mode');
  const mode = _normalizeRepoSummaryMode((el && el.value) || window.__REPO_SUMMARY_MODE || 'quick');
  if (el && el.value !== mode) el.value = mode;
  window.__REPO_SUMMARY_MODE = mode;
  return mode;
}

function buildMergeOutputOptions() {
  const repoMode = getRepoSummaryMode();
  const opts = {
    include_file_tree: !!(document.getElementById('opt_tree') && document.getElementById('opt_tree').checked),
    markdown_fences: !!(document.getElementById('opt_fences') && document.getElementById('opt_fences').checked),
    markdown_toc: !!(document.getElementById('opt_toc') && document.getElementById('opt_toc').checked),
    enumerate_lines: !!(document.getElementById('opt_enum') && document.getElementById('opt_enum').checked),
    remove_empty: !!(document.getElementById('opt_strip') && document.getElementById('opt_strip').checked),
    create_json_manifest: !!(document.getElementById('opt_manifest') && document.getElementById('opt_manifest').checked),
    content_mode: (document.getElementById('merge_content_mode') && document.getElementById('merge_content_mode').value) ? document.getElementById('merge_content_mode').value : 'code_only',
    include_repo_summary: !!(document.getElementById('merge_include_repo_summary') && document.getElementById('merge_include_repo_summary').checked),
    include_repo_summary_mode: repoMode,
  };
  const prompt_template = getMergePromptTemplate();
  if (prompt_template) opts.prompt_template = prompt_template;
  return opts;
}

function _setKeepOnlyView(view) {
  const vInput = document.getElementById('keep_only_view_input');
  const vPrev = document.getElementById('keep_only_view_preview');
  const btnPreview = document.getElementById('keep_only_preview');
  const btnBack = document.getElementById('keep_only_back');
  const btnCopy = document.getElementById('keep_only_copy');
  const btnApply = document.getElementById('keep_only_apply');

  const isPreview = (view === 'preview');
  if (vInput) vInput.style.display = isPreview ? 'none' : 'block';
  if (vPrev) vPrev.style.display = isPreview ? 'block' : 'none';
  if (btnPreview) btnPreview.style.display = isPreview ? 'none' : 'inline-block';
  if (btnBack) btnBack.style.display = isPreview ? 'inline-block' : 'none';
  if (btnCopy) btnCopy.style.display = isPreview ? 'inline-block' : 'none';
  if (btnApply) btnApply.style.display = isPreview ? 'inline-block' : 'none';
}

function showKeepOnlyModal() {
  const modal = document.getElementById('keep_only_modal');
  if (!modal) return;

  window.__KEEP_ONLY_CANDIDATES = [];
  window.__KEEP_ONLY_RESOLVED = [];
  window.__KEEP_ONLY_STATS = null;

  const ta = document.getElementById('keep_only_input');
  if (ta) ta.value = '';

  const smart = document.getElementById('keep_only_opt_smart'); if (smart) smart.checked = true;
  const auto = document.getElementById('keep_only_opt_autocorrect'); if (auto) auto.checked = true;
  const llm = document.getElementById('keep_only_opt_llm'); if (llm) llm.checked = false;
  const dirp = document.getElementById('keep_only_opt_dir_prefix'); if (dirp) dirp.checked = false;

  const summary = document.getElementById('keep_only_preview_summary');
  if (summary) summary.textContent = '';
  const tbody = document.querySelector('#keep_only_preview_table tbody');
  if (tbody) tbody.innerHTML = '';

  const btnApply = document.getElementById('keep_only_apply');
  if (btnApply) { btnApply.disabled = true; btnApply.textContent = 'Apply'; }
  const btnCopy = document.getElementById('keep_only_copy');
  if (btnCopy) btnCopy.disabled = true;

  _setKeepOnlyView('input');
  modal.style.display = 'flex';
  try { if (ta) ta.focus(); } catch (_) { }
}

function hideKeepOnlyModal() {
  const modal = document.getElementById('keep_only_modal');
  if (modal) modal.style.display = 'none';
}

function keepOnlyBack() {
  window.__KEEP_ONLY_CANDIDATES = [];
  window.__KEEP_ONLY_RESOLVED = [];
  window.__KEEP_ONLY_STATS = null;
  const summary = document.getElementById('keep_only_preview_summary');
  if (summary) summary.textContent = '';
  const tbody = document.querySelector('#keep_only_preview_table tbody');
  if (tbody) tbody.innerHTML = '';
  const btnCopy = document.getElementById('keep_only_copy');
  if (btnCopy) btnCopy.disabled = true;
  _setKeepOnlyView('input');
  try {
    const ta = document.getElementById('keep_only_input');
    if (ta) ta.focus();
  } catch (_) { }
}

async function copyKeepOnlyList() {
  const resolved = Array.isArray(window.__KEEP_ONLY_RESOLVED) ? window.__KEEP_ONLY_RESOLVED : [];
  const fallback = Array.isArray(window.__KEEP_ONLY_CANDIDATES) ? window.__KEEP_ONLY_CANDIDATES : [];
  const list = resolved.length ? resolved : fallback;
  const unique = Array.from(new Set(list.map(s => String(s || '').trim()).filter(Boolean)));
  if (!unique.length) {
    try { showBanner('No resolved paths to copy.', 'warn'); } catch (_) { }
    return;
  }
  const ok = await copyTextToClipboard(unique.join('\n'));
  try { showBanner(ok ? 'Cleaned list copied.' : 'Copy failed.', ok ? '' : 'warn'); setTimeout(hideBanner, 2000); } catch (_) { }
}

async function previewKeepOnly() {
  const ta = document.getElementById('keep_only_input');
  const text = (ta && typeof ta.value === 'string') ? ta.value : '';

  const options = {
    smart: !!(document.getElementById('keep_only_opt_smart') && document.getElementById('keep_only_opt_smart').checked),
    autocorrect: !!(document.getElementById('keep_only_opt_autocorrect') && document.getElementById('keep_only_opt_autocorrect').checked),
    use_llm: !!(document.getElementById('keep_only_opt_llm') && document.getElementById('keep_only_opt_llm').checked),
    dir_prefix: !!(document.getElementById('keep_only_opt_dir_prefix') && document.getElementById('keep_only_opt_dir_prefix').checked),
  };

  const btn = document.getElementById('keep_only_preview');
  const btnCancel = document.getElementById('keep_only_cancel');
  const prevLabel = btn ? btn.textContent : '';
  try {
    if (btn) { btn.disabled = true; btn.textContent = 'Previewing…'; }
    if (btnCancel) btnCancel.disabled = true;

    const res = await fetch('/api/path/normalize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, options }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error((data && data.error) ? data.error : ('HTTP ' + res.status));

    const requested = Array.isArray(data.requested_rel) ? data.requested_rel.filter(x => typeof x === 'string' && x.trim()) : [];
    window.__KEEP_ONLY_CANDIDATES = Array.from(new Set(requested.map(s => String(s).trim()).filter(Boolean)));
    window.__KEEP_ONLY_RESOLVED = [];

    const items = Array.isArray(data.items) ? data.items : [];
    const tbody = document.querySelector('#keep_only_preview_table tbody');
    if (tbody) tbody.innerHTML = '';
    items.forEach(it => {
      const raw = (it && it.raw != null) ? String(it.raw) : '';
      const resolved = (it && it.resolved != null) ? String(it.resolved) : ((it && it.cleaned != null) ? String(it.cleaned) : '');
      const status = (it && it.status != null) ? String(it.status) : '';
      const note = (it && it.note != null) ? String(it.note) : '';

      if (resolved) window.__KEEP_ONLY_RESOLVED.push(resolved);

      const tr = document.createElement('tr');
      if (status === 'kept') tr.classList.add('status-kept');
      else if (status && status.indexOf('corrected') === 0) tr.classList.add('status-corrected');
      else if (status === 'missed' || status === 'invalid') tr.classList.add('status-missed');
      else if (status === 'ignored') tr.classList.add('status-ignored');

      const tdRaw = document.createElement('td'); tdRaw.textContent = raw;
      const tdRes = document.createElement('td'); tdRes.textContent = resolved;
      const tdStatus = document.createElement('td');
      tdStatus.textContent = status || '';
      if (note) tdStatus.title = note;

      tr.appendChild(tdRaw);
      tr.appendChild(tdRes);
      tr.appendChild(tdStatus);
      if (tbody) tbody.appendChild(tr);
    });

    const stats = (data && typeof data.stats === 'object' && data.stats) ? data.stats : {};
    window.__KEEP_ONLY_STATS = stats;
    const kept = parseInt(stats.kept || 0, 10) || 0;
    const corrected = (parseInt(stats.corrected_fuzzy || 0, 10) || 0) + (parseInt(stats.corrected_llm || 0, 10) || 0);
    const missed = parseInt(stats.missed || 0, 10) || 0;
    const invalid = parseInt(stats.invalid || 0, 10) || 0;
    const ignored = parseInt(stats.ignored || 0, 10) || 0;

    const summary = document.getElementById('keep_only_preview_summary');
    if (summary) {
      const resolvedCount = window.__KEEP_ONLY_CANDIDATES.length;
      if (!resolvedCount) summary.textContent = `No matches. kept=${kept} corrected=${corrected} missed=${missed} invalid=${invalid} ignored=${ignored}`;
      else summary.textContent = `Resolved paths: ${resolvedCount} — kept=${kept} corrected=${corrected} missed=${missed} invalid=${invalid} ignored=${ignored}`;
    }

    const btnApply = document.getElementById('keep_only_apply');
    if (btnApply) {
      const n = window.__KEEP_ONLY_CANDIDATES.length;
      btnApply.textContent = `Keep ${n} file${n === 1 ? '' : 's'}`;
      btnApply.disabled = n === 0;
    }
    const btnCopy = document.getElementById('keep_only_copy');
    if (btnCopy) btnCopy.disabled = window.__KEEP_ONLY_RESOLVED.length === 0;

    _setKeepOnlyView('preview');
  } catch (err) {
    showError('Keep Only Preview Failed', err && err.message ? err.message : err);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = prevLabel || 'Preview'; }
    if (btnCancel) btnCancel.disabled = false;
  }
}

async function applyKeepOnly() {
  const keep = Array.isArray(window.__KEEP_ONLY_CANDIDATES) ? window.__KEEP_ONLY_CANDIDATES : [];
  const keep_rels = Array.from(new Set(keep.filter(x => typeof x === 'string' && x.trim()).map(x => x.trim())));
  if (!keep_rels.length) {
    showError('Keep Only', 'No resolved paths to apply. Preview first.');
    return;
  }
  try {
    const stats = (window.__KEEP_ONLY_STATS && typeof window.__KEEP_ONLY_STATS === 'object') ? window.__KEEP_ONLY_STATS : {};
    const corrected = (parseInt(stats.corrected_fuzzy || 0, 10) || 0) + (parseInt(stats.corrected_llm || 0, 10) || 0);
    const missed = parseInt(stats.missed || 0, 10) || 0;
    const invalid = parseInt(stats.invalid || 0, 10) || 0;
    const ignored = parseInt(stats.ignored || 0, 10) || 0;
    if (corrected || missed || invalid || ignored) {
      const msg = `Review corrections before applying?\ncorrected=${corrected}, missed=${missed}, invalid=${invalid}, ignored=${ignored}\n\nApply keep-only anyway?`;
      if (!confirm(msg)) return;
    }
  } catch (_) { }

  const btn = document.getElementById('keep_only_apply');
  const btnBack = document.getElementById('keep_only_back');
  const btnCancel = document.getElementById('keep_only_cancel');
  const prevLabel = btn ? btn.textContent : '';
  try {
    if (btn) { btn.disabled = true; btn.textContent = 'Applying…'; }
    if (btnBack) btnBack.disabled = true;
    if (btnCancel) btnCancel.disabled = true;

    const res = await fetch('/api/keep_only/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keep_rels }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error((data && data.error) ? data.error : ('HTTP ' + res.status));

    hideKeepOnlyModal();
    try {
      showBanner(`Keep only applied: kept=${data.kept != null ? data.kept : keep_rels.length}, excluded=${data.excluded || 0}`, '');
      setTimeout(hideBanner, 3500);
    } catch (_) { }

    await doScan(false);
  } catch (err) {
    showError('Keep Only Failed', err && err.message ? err.message : err);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = prevLabel || 'Apply'; }
    if (btnBack) btnBack.disabled = false;
    if (btnCancel) btnCancel.disabled = false;
  }
}

let __MANUAL_PICKER_STATE = null;
let __MANUAL_SEARCH_TIMER = null;

function _manualPickerState() {
  return (__MANUAL_PICKER_STATE && typeof __MANUAL_PICKER_STATE === 'object') ? __MANUAL_PICKER_STATE : null;
}

function openManualPicker(opts) {
  opts = opts || {};
  const cfg = window.__CURRENT_CONFIG || {};
  const root = (window.__SCAN && window.__SCAN.root) || document.getElementById('last_directory').value || '';
  const modal = document.getElementById('manual_picker_modal');
  if (!modal) return;
  __MANUAL_PICKER_STATE = {
    mode: opts.mode || 'manual_add',
    title: opts.title || (opts.mode === 'pick' ? 'Choose a path…' : 'Add files/folders…'),
    hint: opts.hint || (opts.mode === 'pick' ? 'Select a file or folder and confirm.' : 'Browse the filesystem and add multiple paths at once.'),
    allowFiles: opts.allowFiles !== false,
    allowDirs: opts.allowDirs !== false,
    single: !!opts.single,
    onConfirm: (typeof opts.onConfirm === 'function') ? opts.onConfirm : null,
    onCancel: (typeof opts.onCancel === 'function') ? opts.onCancel : null,
    _confirmed: false,
    selected: new Set((opts.selected || []).filter(p => typeof p === 'string' && p.trim()).map(p => p.trim())),
    removeSet: new Set(),
    activePath: null,
    activeIsDir: false,
    path: opts.path || root || cfg.last_directory || '~',
    parent: null,
    pattern: '',
    showHidden: false,
    restrictRoot: !!opts.restrictRoot,
    root: opts.root || root || cfg.last_directory || '',
  };

  const title = document.getElementById('manual_picker_title'); if (title) title.textContent = __MANUAL_PICKER_STATE.title;
  const hint = document.getElementById('manual_picker_hint'); if (hint) hint.textContent = __MANUAL_PICKER_STATE.hint;
  const search = document.getElementById('manual_search'); if (search) search.value = '';
  const showHidden = document.getElementById('manual_show_hidden'); if (showHidden) showHidden.checked = false;
  const rec = document.getElementById('manual_recurse'); if (rec) rec.checked = true;
  const res = document.getElementById('manual_respect_excludes'); if (res) res.checked = true;
  const md = document.getElementById('manual_max_depth'); if (md) md.value = String(cfg.max_walk_depth || 0);
  const hasNativePicker = !!(window.pywebview && window.pywebview.api && (window.pywebview.api.open_file_dialog || window.pywebview.api.browse_files));
  const addFiles = document.getElementById('manual_add_files');
  if (addFiles) {
    addFiles.disabled = !__MANUAL_PICKER_STATE.allowFiles;
    addFiles.textContent = hasNativePicker ? 'Add files…' : 'Add selected file';
    addFiles.title = hasNativePicker ? 'Use native file picker' : 'Select a file in the tree to add it';
  }
  const addFolder = document.getElementById('manual_add_folder'); if (addFolder) addFolder.disabled = !__MANUAL_PICKER_STATE.allowDirs;
  if (hint && !hasNativePicker) {
    hint.textContent = `${__MANUAL_PICKER_STATE.hint} (Browser mode: select from the tree above.)`;
  }

  const showOptions = __MANUAL_PICKER_STATE.mode !== 'pick';
  const optRecurse = document.getElementById('manual_recurse'); if (optRecurse) optRecurse.parentElement.style.display = showOptions ? '' : 'none';
  const optRespect = document.getElementById('manual_respect_excludes'); if (optRespect) optRespect.parentElement.style.display = showOptions ? '' : 'none';
  const optDepth = document.getElementById('manual_max_depth'); if (optDepth) optDepth.closest('label').style.display = showOptions ? '' : 'none';

  const confirmBtn = document.getElementById('manual_confirm');
  if (confirmBtn) confirmBtn.textContent = (__MANUAL_PICKER_STATE.mode === 'pick') ? 'Choose path' : 'Add to list';

  manualPickerRenderSelection();
  manualPickerRefresh();
  modal.style.display = 'flex';
}

function hideManualPicker() {
  const modal = document.getElementById('manual_picker_modal');
  const state = __MANUAL_PICKER_STATE;
  if (modal) modal.style.display = 'none';
  if (state && state.onCancel && !state._confirmed) {
    try { state.onCancel(); } catch (_) { }
  }
  __MANUAL_PICKER_STATE = null;
}

function manualPickerRefresh() {
  const state = _manualPickerState();
  if (!state) return;
  manualPickerLoadPath(state.path);
}

function manualPickerUp() {
  const state = _manualPickerState();
  if (!state) return;
  if (state.parent) manualPickerLoadPath(state.parent);
}

function scheduleManualPickerSearch() {
  try { if (__MANUAL_SEARCH_TIMER) clearTimeout(__MANUAL_SEARCH_TIMER); } catch (_) { }
  __MANUAL_SEARCH_TIMER = setTimeout(() => manualPickerRefresh(), 250);
}

async function manualPickerLoadPath(path) {
  const state = _manualPickerState();
  if (!state) return;
  const list = document.getElementById('manual_list');
  if (list) list.innerHTML = '<div class="muted">Loading…</div>';
  const search = document.getElementById('manual_search');
  const showHidden = document.getElementById('manual_show_hidden');
  state.pattern = search ? String(search.value || '').trim() : '';
  state.showHidden = !!(showHidden && showHidden.checked);
  const payload = {
    path,
    show_hidden: state.showHidden,
    pattern: state.pattern,
    dirs_only: state.allowDirs && !state.allowFiles,
    files_only: state.allowFiles && !state.allowDirs,
    limit: 3000,
    restrict_root: state.restrictRoot,
    root: state.root,
  };
  try {
    const res = await fetch('/api/fs/list', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    state.path = data.path || path;
    state.parent = data.parent || null;
    state.activePath = null;
    state.activeIsDir = false;
    const pathEl = document.getElementById('manual_path'); if (pathEl) pathEl.textContent = state.path;
    manualPickerRenderList(Array.isArray(data.entries) ? data.entries : []);
  } catch (err) {
    if (list) list.innerHTML = '<div class="muted">Failed to load directory.</div>';
    showBanner('Browse failed: ' + err, 'warn');
  }
}

function manualPickerRenderList(entries) {
  const list = document.getElementById('manual_list');
  if (!list) return;
  list.innerHTML = '';
  const state = _manualPickerState();
  if (!entries.length) {
    list.innerHTML = '<div class="muted">No entries.</div>';
    return;
  }
  entries.forEach(entry => {
    const name = (entry && entry.name != null) ? String(entry.name) : '';
    const path = (entry && entry.path != null) ? String(entry.path) : '';
    const isDir = !!(entry && entry.is_dir);
    const row = document.createElement('div');
    row.className = 'manual-entry' + (isDir ? ' is-dir' : '');
    row.dataset.path = path;
    row.dataset.dir = isDir ? '1' : '0';
    row.innerHTML = `<span class="manual-entry-name">${escapeHtml(name)}</span><span class="manual-entry-meta">${isDir ? 'Folder' : 'File'}</span>`;
    row.addEventListener('click', () => manualPickerSetActive(path, isDir));
    row.addEventListener('dblclick', () => {
      if (isDir) manualPickerLoadPath(path);
      else manualPickerAddPaths([path]);
    });
    list.appendChild(row);
  });
  if (state && state.activePath) manualPickerSetActive(state.activePath, state.activeIsDir);
}

function manualPickerSetActive(path, isDir) {
  const state = _manualPickerState();
  if (!state) return;
  state.activePath = path;
  state.activeIsDir = !!isDir;
  const list = document.getElementById('manual_list');
  if (!list) return;
  list.querySelectorAll('.manual-entry').forEach(el => {
    el.classList.toggle('active', el.dataset.path === path);
  });
}

function manualPickerRenderSelection() {
  const state = _manualPickerState();
  const list = document.getElementById('manual_selected_list');
  if (!list || !state) return;
  list.innerHTML = '';
  const items = Array.from(state.selected || []);
  items.sort();
  items.forEach(path => {
    const row = document.createElement('div');
    row.className = 'manual-selected-row';
    row.dataset.path = path;
    row.textContent = path;
    if (state.removeSet && state.removeSet.has(path)) row.classList.add('selected');
    row.addEventListener('click', () => {
      if (!state.removeSet) state.removeSet = new Set();
      if (state.removeSet.has(path)) state.removeSet.delete(path); else state.removeSet.add(path);
      row.classList.toggle('selected');
    });
    list.appendChild(row);
  });
  const confirmBtn = document.getElementById('manual_confirm');
  if (confirmBtn) confirmBtn.disabled = items.length === 0;
}

function manualPickerAddPaths(paths) {
  const state = _manualPickerState();
  if (!state) return;
  const clean = (paths || []).filter(p => typeof p === 'string' && p.trim()).map(p => p.trim());
  if (!clean.length) return;
  if (state.single) state.selected = new Set();
  clean.forEach(p => state.selected.add(p));
  state.removeSet = new Set();
  manualPickerRenderSelection();
}

async function manualPickerAddFiles() {
  const state = _manualPickerState();
  if (!state) return;
  if (state.activePath && !state.activeIsDir) {
    manualPickerAddPaths([state.activePath]);
    return;
  }
  try {
    if (window.pywebview && window.pywebview.api) {
      const api = window.pywebview.api;
      let picked = null;
      if (api.open_file_dialog) picked = await api.open_file_dialog();
      else if (api.browse_files) picked = await api.browse_files();
      if (Array.isArray(picked)) manualPickerAddPaths(picked);
      else if (typeof picked === 'string' && picked.trim()) manualPickerAddPaths([picked.trim()]);
      return;
    }
  } catch (_) { }
  showBanner('Native file picker unavailable. Select a file in the tree and click "Add selected file".', 'warn');
}

async function manualPickerAddFolder() {
  const state = _manualPickerState();
  if (!state) return;
  if (state.activePath && state.activeIsDir) {
    manualPickerAddPaths([state.activePath]);
    return;
  }
  try {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.browse_dir) {
      const chosen = await window.pywebview.api.browse_dir(state.path || null);
      if (chosen && typeof chosen === 'string') manualPickerAddPaths([chosen]);
      return;
    }
  } catch (_) { }
  const raw = prompt('Enter a folder path:');
  if (raw && raw.trim()) manualPickerAddPaths([raw.trim()]);
}

function manualPickerRemoveSelected() {
  const state = _manualPickerState();
  if (!state || !state.removeSet || !state.removeSet.size) return;
  state.removeSet.forEach(p => state.selected.delete(p));
  state.removeSet = new Set();
  manualPickerRenderSelection();
}

function manualPickerClearSelection() {
  const state = _manualPickerState();
  if (!state) return;
  state.selected = new Set();
  state.removeSet = new Set();
  manualPickerRenderSelection();
}

async function _postManualPaths(paths, opts) {
  opts = opts || {};
  const cfg = window.__CURRENT_CONFIG || {};
  const ignoreBinary = (opts.ignore_binary !== undefined)
    ? !!opts.ignore_binary
    : !!(document.getElementById('f_ignore_binary') && document.getElementById('f_ignore_binary').checked);
  const payload = {
    paths,
    recurse: (opts.recurse !== undefined) ? !!opts.recurse : !!(document.getElementById('manual_recurse') && document.getElementById('manual_recurse').checked),
    respect_excludes: (opts.respect_excludes !== undefined) ? !!opts.respect_excludes : !!(document.getElementById('manual_respect_excludes') && document.getElementById('manual_respect_excludes').checked),
    ignore_binary: ignoreBinary || !!cfg.ignore_binary_files,
    max_depth: (opts.max_depth !== undefined)
      ? parseInt(opts.max_depth, 10) || 0
      : (parseInt((document.getElementById('manual_max_depth') || {}).value || '0', 10) || 0),
  };
  const res = await fetch('/api/files/manual', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!res.ok) throw new Error((data && data.error) ? data.error : ('HTTP ' + res.status));
  return data || {};
}

async function manualPickerConfirm() {
  const state = _manualPickerState();
  if (!state) return;
  const list = Array.from(state.selected || []);
  if (!list.length) { showBanner('Select at least one path.', 'warn'); return; }
  if (state.mode === 'pick') {
    const picked = state.single ? [list[0]] : list;
    if (state.onConfirm) state.onConfirm(picked);
    state._confirmed = true;
    hideManualPicker();
    return;
  }
  try {
    const data = await _postManualPaths(list);
    try {
      if (data && Array.isArray(data.errors) && data.errors.length) showBanner(data.errors.join(' • '), 'warn');
      else showBanner(`Added ${data.added || list.length} item(s)`, '');
      setTimeout(hideBanner, 2500);
    } catch (_) { }
    state._confirmed = true;
    hideManualPicker();
    await doScan(false);
  } catch (err) {
    showError('Manual Add Failed', err && err.message ? err.message : err);
  }
}

function addManualPaths() {
  openManualPicker({ mode: 'manual_add', allowFiles: true, allowDirs: true, single: false });
}

function showError(title, details) {
  try { hideOverlay(); } catch (_) { }
  try {
    const o = document.getElementById('overlay'); if (o) o.style.display = 'none';
    const m = document.getElementById('error_modal');
    if (!m) { alert((title || 'Error') + '\\n' + (details || '')); return; }
    const t = document.getElementById('error_title'); if (t) t.textContent = title || 'Error';
    const pre = document.getElementById('error_text');
    let msg = details;
    if (typeof details === 'object') {
      try { msg = JSON.stringify(details, null, 2); } catch (_) { msg = '' + details; }
    }
    if (pre) pre.textContent = msg || '';
    m.style.display = 'flex';
    const bClose = document.getElementById('error_close');
    if (bClose) bClose.onclick = () => m.style.display = 'none';
    const bCopy = document.getElementById('error_copy');
    if (bCopy) bCopy.onclick = () => { try { navigator.clipboard.writeText(msg || ''); } catch (_) { } };
  } catch (e) { alert(title + ': ' + details); }
}

function getVisibilityMode() {
  const el = document.getElementById('visibility_mode');
  const val = el ? String(el.value || 'filters') : 'filters';
  return val === 'all' ? 'all' : 'filters';
}

function shouldRespectHidden(mode) {
  const m = mode || getVisibilityMode();
  return m !== 'all';
}

function handleVisibilityModeChange() {
  try { updateActiveFiltersSummary(); } catch (_) { }
  try { doScan(false); } catch (_) { try { requestScan(50); } catch (_) { } }
}

function resetFiltersToDefaults() {
  if (!confirm('Reset filters to defaults?')) return;
  const setVal = (id, v) => { const el = document.getElementById(id); if (el) el.value = String(v); };
  const setChecked = (id, v) => { const el = document.getElementById(id); if (el) el.checked = !!v; };
  setVal('f_name_pattern', '');
  setChecked('f_name_glob', false);
  setVal('f_token_min', 0);
  setVal('f_token_max', 10000000);
  setVal('f_size_min', 0);
  setVal('f_size_max', 1000000);
  setVal('f_content', '');
  setChecked('f_content_regex', false);
  setChecked('f_content_case', false);
  setVal('f_content_kb', 256);
  setVal('f_dup_kb', 256);
  setChecked('f_hide_dups', false);
  setChecked('f_mark_dups', true);
  setChecked('f_apply_token_transforms', false);
  setChecked('f_always_docs', true);
  setChecked('f_ignore_persisted', false);
  const lastDays = document.getElementById('f_last_n_days');
  if (lastDays) lastDays.value = '0';
  clearDateFilters(false);
  document.querySelectorAll('.f_cat').forEach(cb => { try { cb.checked = true; } catch (_) { } });
  _validateNamePattern();
  try { updateActiveFiltersSummary(); } catch (_) { }
  scheduleFilterScan(50);
}

function _formatDateChip(ts) {
  if (!ts || !isFinite(ts)) return '';
  try {
    const d = new Date(ts * 1000);
    return d.toISOString().slice(0, 10);
  } catch (_) {
    return '';
  }
}

function collectActiveFiltersSummary() {
  const items = [];
  const visibility = getVisibilityMode();
  if (visibility === 'all') {
    items.push('Show everything (filters ignored)');
    return items;
  }
  const cfg = window.__CURRENT_CONFIG || {};
  const settings = window.__CURRENT_SETTINGS || {};
  const f = getFilters();
  const includeAll = !!(document.getElementById('include_all') && document.getElementById('include_all').checked);
  if (!includeAll) {
    const ft = _collectFileTypeState();
    const total = Object.keys(ft).length;
    const enabled = Object.values(ft).filter(Boolean).length;
    if (total) items.push(`Extensions: ${enabled}/${total}`);
  }
  const catEls = Array.from(document.querySelectorAll('.f_cat'));
  if (catEls.length) {
    const enabled = catEls.filter(c => c.checked).length;
    if (enabled > 0 && enabled < catEls.length) items.push(`Categories: ${enabled}/${catEls.length}`);
  }
  if (f.name_pattern) items.push(`Name: ${f.name_pattern}${f.name_filter_glob ? ' (glob)' : ''}`);
  if (f.content_search) {
    const parts = ['Content'];
    if (f.content_regex) parts.push('regex');
    if (f.content_case_sensitive) parts.push('case');
    items.push(`${parts.join(' ')}: ${f.content_search}`);
  }
  if (f.token_min > 0 || f.token_max < 10000000) items.push(`Tokens: ${f.token_min}-${f.token_max}`);
  if (f.size_min > 0 || f.size_max < 1000000) items.push(`Size KB: ${f.size_min}-${f.size_max}`);
  const lastDays = parseInt(String((document.getElementById('f_last_n_days') || {}).value || '0'), 10) || 0;
  if (lastDays > 0) {
    items.push(`Date: last ${lastDays} days`);
  } else if (f.date_after || f.date_before) {
    const after = _formatDateChip(f.date_after);
    const before = _formatDateChip(f.date_before);
    if (after && before) items.push(`Date: ${after}..${before}`);
    else if (after) items.push(`Date after: ${after}`);
    else if (before) items.push(`Date before: ${before}`);
  }
  if (f.content_hide_duplicates) items.push('Hide duplicates');
  if (f.apply_token_filters_to_transforms) items.push('Token filters on transformed output');
  if (document.getElementById('f_always_docs') && !document.getElementById('f_always_docs').checked) items.push('Always-include docs off');
  if (f.ignore_binary) items.push('Ignore binary');
  if (f.max_file_size_mb > 0) items.push(`Max size: ${f.max_file_size_mb} MB`);
  if (f.max_walk_depth > 0) items.push(`Max depth: ${f.max_walk_depth}`);
  if (f.follow_symlinks) items.push('Follow symlinks');
  const useGit = !!(document.getElementById('f_use_git') && document.getElementById('f_use_git').checked);
  if (useGit) {
    items.push('Git tracked only');
    if (document.getElementById('f_git_untracked') && document.getElementById('f_git_untracked').checked) items.push('Git include untracked');
    if (document.getElementById('f_git_changed') && document.getElementById('f_git_changed').checked) items.push('Git changed only');
    const since = String((document.getElementById('f_git_since') || {}).value || '').trim();
    if (since) items.push(`Git since: ${since}`);
    const until = String((document.getElementById('f_git_until') || {}).value || '').trim();
    if (until && until !== 'HEAD') items.push(`Git until: ${until}`);
  }
  if (document.getElementById('f_ex_vendored') && document.getElementById('f_ex_vendored').checked) items.push('Exclude vendored');
  if (document.getElementById('f_ex_node') && document.getElementById('f_ex_node').checked) items.push('Exclude node_modules');
  if (document.getElementById('f_ex_ios') && document.getElementById('f_ex_ios').checked) items.push('Exclude iOS build');
  if (document.getElementById('f_ex_py') && document.getElementById('f_ex_py').checked) items.push('Exclude Python env');
  if (document.getElementById('f_ex_java') && document.getElementById('f_ex_java').checked) items.push('Exclude Java/Gradle');
  if (document.getElementById('f_ex_web') && document.getElementById('f_ex_web').checked) items.push('Exclude web build');
  if (document.getElementById('f_ignore_persisted') && document.getElementById('f_ignore_persisted').checked) items.push('Custom excludes ignored');
  const exDirs = settings.excluded_dirs || cfg.excluded_dirs || [];
  const exFiles = settings.excluded_files || cfg.excluded_files || [];
  const customExcluded = settings.custom_excluded || cfg.custom_excluded || [];
  if (Array.isArray(exDirs) && exDirs.length) items.push(`Excluded dirs: ${exDirs.length}`);
  if (Array.isArray(exFiles) && exFiles.length) items.push(`Excluded files: ${exFiles.length}`);
  if (!(document.getElementById('f_ignore_persisted') && document.getElementById('f_ignore_persisted').checked)) {
    if (Array.isArray(customExcluded) && customExcluded.length) items.push(`Custom excludes: ${customExcluded.length}`);
  }
  return items;
}

function updateActiveFiltersSummary() {
  const box = document.getElementById('filters_summary');
  if (!box) return;
  const items = collectActiveFiltersSummary();
  box.innerHTML = '';
  if (!items.length) {
    box.textContent = 'None';
    return;
  }
  items.forEach(label => {
    const el = document.createElement('span');
    el.className = 'pill filter-chip';
    el.textContent = label;
    box.appendChild(el);
  });
}

function buildScanPayload(path) {
  const visibilityMode = getVisibilityMode();
  const ignorePersisted = !!(document.getElementById('f_ignore_persisted') && document.getElementById('f_ignore_persisted').checked);
  const ignoreCustom = (visibilityMode === 'all') ? true : ignorePersisted;
  return {
    path,
    visibility_mode: visibilityMode,
    include_all: !!(document.getElementById('include_all') && document.getElementById('include_all').checked),
    sort_by: (document.getElementById('sort_by') && document.getElementById('sort_by').value)
      ? document.getElementById('sort_by').value
      : ((window.__CURRENT_CONFIG && window.__CURRENT_CONFIG.sort_by) ? window.__CURRENT_CONFIG.sort_by : 'name'),
    sort_descending: !!(document.getElementById('sort_desc') && document.getElementById('sort_desc').checked),
    ignore_custom_excluded: ignoreCustom,
    always_include_docs: document.getElementById("f_always_docs").checked,
    exclude_node_modules: document.getElementById("f_ex_node").checked,
    exclude_ios_noise: !!(document.getElementById("f_ex_ios") && document.getElementById("f_ex_ios").checked),
    exclude_python_noise: document.getElementById("f_ex_py").checked,
    exclude_java_gradle_noise: document.getElementById("f_ex_java").checked,
    exclude_web_build_noise: document.getElementById("f_ex_web").checked,
    use_git_files: document.getElementById("f_use_git").checked,
    git_include_untracked: document.getElementById("f_git_untracked").checked,
    git_only_changed: document.getElementById("f_git_changed").checked,
    git_since_ref: document.getElementById("f_git_since").value,
    git_until_ref: document.getElementById("f_git_until").value,
    exclude_vendored: document.getElementById("f_ex_vendored").checked,
    xcode_target: document.getElementById("f_xcode_target").value,
    ignore_binary: document.getElementById('f_ignore_binary').checked,
    max_file_size_mb: parseFloat(document.getElementById('f_max_mb').value || '0'),
    max_walk_depth: parseInt(document.getElementById('f_max_depth').value || '0', 10),
    follow_symlinks: document.getElementById('f_follow_symlinks').checked,
    filters: getFilters(),
  };
}

function applyScanResponse(data, path, scanId, opts) {
  if (scanId != null && scanId !== window.__SCAN_ID) return;
  const prevSort = (window.__SORT && typeof window.__SORT === 'object') ? { col: window.__SORT.col, desc: !!window.__SORT.desc } : null;
  const prevHeaderSort = !!window.__HEADER_SORT_ACTIVE;
  const preferConfigSort = !!window.__PREFER_CONFIG_SORT;
  const visibilityMode = (opts && opts.visibilityMode) ? opts.visibilityMode : getVisibilityMode();
  window.__SCAN_VISIBILITY_MODE = visibilityMode;
  window.__SCAN = data;
  try {
    const modes = (data && data.folder_modes && typeof data.folder_modes === 'object') ? data.folder_modes : {};
    window.__FOLDER_MODES = modes || {};
  } catch (_) { window.__FOLDER_MODES = {}; }
  try {
    if (data && typeof data.include_repo_summary_in_merge === 'boolean') {
      window.__INCLUDE_REPO_SUMMARY = data.include_repo_summary_in_merge;
      const cb = document.getElementById('merge_include_repo_summary');
      if (cb) cb.checked = !!data.include_repo_summary_in_merge;
    }
    if (data && typeof data.include_repo_summary_mode === 'string') {
      const repoMode = _normalizeRepoSummaryMode(data.include_repo_summary_mode);
      window.__REPO_SUMMARY_MODE = repoMode;
      const sel = document.getElementById('merge_repo_summary_mode');
      if (sel) sel.value = repoMode;
    }
  } catch (_) { }
  try {
    const hits = Array.isArray(data && data.pattern_hits) ? data.pattern_hits.filter(p => typeof p === 'string' && p.trim()) : [];
    window.__PATTERN_HITS = new Set(hits.map(p => p.trim()));
  } catch (_) { window.__PATTERN_HITS = new Set(); }
  try {
    const n = (data && typeof data.manual_total === 'number') ? data.manual_total : 0;
    const btn = document.getElementById('btn_clear_manual');
    if (btn) btn.disabled = !(n > 0);
  } catch (_) { }
  window.__DEFAULT_MODE = data.default_mode || window.__DEFAULT_MODE || 'CODE';
  try {
    if (data && data.file_types && typeof data.file_types === 'object') {
      renderFileTypes(data.file_types);
    }
  } catch (_) { }
  try {
    if (shouldRespectHidden(visibilityMode)) {
      const hidden = (window.__HIDDEN_MAP[path] || new Set());
      window.__VISIBLE = (data.files || []).filter(f => !hidden.has(f.abs_path));
    } else {
      window.__VISIBLE = (data.files || []);
    }
  } catch (_) { window.__VISIBLE = (data.files || []); }
  if (preferConfigSort) {
    window.__PREFER_CONFIG_SORT = false;
  } else if (prevHeaderSort && prevSort) {
    window.__SORT = prevSort;
    window.__HEADER_SORT_ACTIVE = true;
  }
  try { applyActiveHeaderSortToData(window.__VISIBLE); } catch (_) { }
  try {
    const scanWarnings = Array.isArray(data.warnings) ? data.warnings.slice(0) : [];
    const visSet = new Set((window.__VISIBLE || []).map(f => f.abs_path));
    const before = (window.__SEL && window.__SEL.size) ? window.__SEL.size : 0;
    if (before) {
      window.__SEL = new Set(Array.from(window.__SEL || []).filter(p => visSet.has(p)));
      const removed = before - window.__SEL.size;
      if (removed > 0) scanWarnings.push(`Selection updated: cleared ${removed} hidden item(s).`);
    }
    if (scanWarnings.length) showBanner(scanWarnings.join(' • '), 'warn'); else hideBanner();
  } catch (_) { }
  try { window.__SCAN_MAP = {}; (window.__VISIBLE || []).forEach(f => { window.__SCAN_MAP[f.abs_path] = f; }); } catch (_) { }
  window.__TOK_CONTENT = (window.__VISIBLE || []).reduce((s, f) => s + (f.token_count || 0), 0);
  window.__TOK_FINAL = undefined; window.__TOK_FINAL_SKIPPED = false; window.__TOK_FINAL_COUNT = undefined;
  renderTable();
  updateScanStatsText();
  try { maybeComputeFinalTokens(scanId); } catch (_) { }
  try { updateActiveFiltersSummary(); } catch (_) { }
  try {
    const wt = document.getElementById('watch_toggle');
    if (wt && wt.checked && window.__WATCH_NEEDS_TICK) {
      window.__WATCH_NEEDS_TICK = false;
      const sig = (__WATCH_CTRL && __WATCH_CTRL.signal) || undefined;
      watchTickVisible(sig, __WATCH_SESSION);
    }
  } catch (_) { }
}

function pollJobUntilDone(jobId, opts) {
  opts = opts || {};
  const interval = (typeof opts.interval === 'number' && opts.interval > 0) ? opts.interval : 700;
  const show = (opts.showOverlay !== false);

  if (show) {
    try { showOverlay(opts.label || 'Working…'); } catch (_) { }
  }

  const tick = async () => {
    try {
      const res = await fetch('/api/jobs/' + encodeURIComponent(jobId));
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
      if (typeof opts.onProgress === 'function') opts.onProgress(data);
      if (data.status === 'done') {
        if (show) { try { hideOverlay(); } catch (_) { } }
        if (typeof opts.onDone === 'function') opts.onDone(data);
        return;
      }
      if (data.status === 'error') {
        if (show) { try { hideOverlay(); } catch (_) { } }
        if (typeof opts.onError === 'function') opts.onError(data);
        return;
      }
      if (data.status === 'cancelled') {
        if (show) { try { hideOverlay(); } catch (_) { } }
        if (typeof opts.onCancel === 'function') opts.onCancel(data);
        else if (typeof opts.onError === 'function') opts.onError(data);
        return;
      }
    } catch (err) {
      if (show) { try { hideOverlay(); } catch (_) { } }
      if (typeof opts.onError === 'function') opts.onError({ error: err });
      return;
    }
    setTimeout(tick, interval);
  };
  tick();
}

async function doScan(clearHidden = false) {
  if (!_validateNamePattern()) return;
  window.__SCAN_ID = (window.__SCAN_ID || 0) + 1;
  const scanId = window.__SCAN_ID;
  setFooterStatus("Scanning… this may take a while on large repos.", 'stats');
  const path = document.getElementById("last_directory").value;
  const visibilityMode = getVisibilityMode();
  try {
    if (path) { try { await persistConfigPatch({ last_directory: path }); } catch (_) { } }
    // Optionally clear session-hidden (only when explicitly requested)
    if (clearHidden) {
      try { if (window.__HIDDEN_MAP && window.__HIDDEN_MAP[path]) delete window.__HIDDEN_MAP[path]; } catch (_) { }
      window.__SEL = new Set();
    }
    // Reset session-hidden when switching roots to avoid sticky exclusions across folders
    if (window.__CURRENT_ROOT !== path) {
      window.__CURRENT_ROOT = path;
      if (window.__HIDDEN_MAP && window.__HIDDEN_MAP[path]) delete window.__HIDDEN_MAP[path];
      window.__SUMMARY_ERRORS = new Set();
    }
    window.__SCAN_RUNNING = true;
    // If Xcode target is set, attempt to fetch targets list and ensure option is present
    try { await reloadXcodeTargets(path); } catch (_) { }
    const res = await fetch("/api/scan_job", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildScanPayload(path)),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
    const jobId = data.job_id;
    pollJobUntilDone(jobId, {
      label: 'Scanning…',
      onProgress: (job) => {
        const pct = (typeof job.progress_pct === 'number') ? job.progress_pct : null;
        const msg = (job.progress && typeof job.progress === 'string') ? job.progress : 'Scanning…';
        const text = pct != null ? `${msg} (${pct}%)` : msg;
        setFooterStatus(text, 'stats');
        const om = document.getElementById('overlay_msg'); if (om) om.textContent = text;
      },
      onDone: (job) => {
        const result = job && job.result ? job.result : null;
        if (result) applyScanResponse(result, path, scanId, { visibilityMode });
        window.__SCAN_RUNNING = false; window.__LAST_SCAN_AT = Date.now();
        if (window.__FILTER_PENDING) { window.__FILTER_PENDING = false; requestFilterApply(50); }
        if (window.__RESCAN_REQ) { window.__RESCAN_REQ = false; requestScan(200); }
      },
      onError: (job) => {
        const errMsg = (job && job.error) ? job.error : job;
        const text = "Scan failed: " + errMsg;
        setFooterStatus(text, 'stats', { ttlMs: 8000 });
        window.__SCAN_RUNNING = false; window.__LAST_SCAN_AT = Date.now();
        showError('Scan Failed', errMsg && errMsg.message ? errMsg.message : errMsg);
      },
    });
  } catch (err) {
    const text = "Scan failed: " + err;
    setFooterStatus(text, 'stats', { ttlMs: 8000 });
    window.__SCAN_RUNNING = false; window.__LAST_SCAN_AT = Date.now();
    try { showError('Scan Failed', err.message || err); } catch (_) { }
  }
}

function _visibleFilesForTotals() {
  // Totals should include all files in the current list, even when folders are collapsed.
  return (window.__VISIBLE || (window.__SCAN && window.__SCAN.files) || []);
}

function setFooterStatus(text, kind, opts) {
  const el = document.getElementById('merge_status');
  if (!el) return;
  el.textContent = String(text || '');
  if (kind) window.__FOOTER_STATUS_KIND = kind;
  if (opts && typeof opts.ttlMs === 'number' && opts.ttlMs > 0) {
    const expiresAt = Date.now() + opts.ttlMs;
    window.__FOOTER_STATUS_EXPIRES_AT = expiresAt;
    try {
      if (window.__FOOTER_STATUS_TIMER) clearTimeout(window.__FOOTER_STATUS_TIMER);
      window.__FOOTER_STATUS_TIMER = setTimeout(() => {
        if (window.__FOOTER_STATUS_KIND === kind && window.__FOOTER_STATUS_EXPIRES_AT === expiresAt) {
          window.__FOOTER_STATUS_KIND = 'stats';
          window.__FOOTER_STATUS_EXPIRES_AT = 0;
          try { updateScanStatsText(); } catch (_) { }
        }
      }, opts.ttlMs + 25);
    } catch (_) { }
  } else if (kind === 'stats') {
    window.__FOOTER_STATUS_EXPIRES_AT = 0;
  }
}

function updateFooterStatusFromStats(text) {
  const kind = window.__FOOTER_STATUS_KIND;
  if (kind && kind !== 'stats') {
    const exp = window.__FOOTER_STATUS_EXPIRES_AT || 0;
    if (!exp || Date.now() < exp) return;
  }
  setFooterStatus(text, 'stats');
}

function updateScanStatsText() {
  const tokenEl = document.getElementById('token_status');
  if (!window.__SCAN) {
    updateFooterStatusFromStats('Ready – 0 visible files');
    if (tokenEl) tokenEl.textContent = '';
    return;
  }
  const visible = _visibleFilesForTotals();
  const files = visible.length;
  const readErrors = visible.reduce((s, f) => s + (f.read_error ? 1 : 0), 0);
  const bytes = visible.reduce((s, f) => s + (f.size_bytes || 0), 0);
  const contentTok = visible.reduce((s, f) => s + (f.token_count || 0), 0);
  const finalTok = (typeof window.__TOK_FINAL === 'number') ? window.__TOK_FINAL : null;
  const finalSkipped = !!window.__TOK_FINAL_SKIPPED;
  const finalCount = (typeof window.__TOK_FINAL_COUNT === 'number') ? window.__TOK_FINAL_COUNT : null;
  const finalOnly = !!(document.getElementById('toggle_final_footer') && document.getElementById('toggle_final_footer').checked);
  const showBytes = !!(document.getElementById('toggle_bytes_footer') && document.getElementById('toggle_bytes_footer').checked);
  const fmtNum = (n) => {
    const v = Number(n || 0);
    return Number.isFinite(v) ? v.toLocaleString() : '0';
  };
  const fmtBytes = (b) => {
    const kb = (b || 0) / 1024.0;
    if (!isFinite(kb) || kb < 0) return '';
    if (kb >= 1024) return `${(kb / 1024.0).toFixed(1)} MB`;
    return `${kb.toFixed(1)} KB`;
  };
  const fmtCompact = (n) => {
    const v = Number(n || 0);
    if (!Number.isFinite(v)) return '0';
    if (v >= 1000000) return `${(v / 1000000).toFixed(1)}M`;
    if (v >= 1000) return `${(v / 1000).toFixed(1)}k`;
    return String(Math.round(v));
  };
  const statusText = readErrors
    ? `Ready – ${files} visible files (${readErrors} unreadable)`
    : `Ready – ${files} visible files`;
  updateFooterStatusFromStats(statusText);

  const tokenParts = [];
  const compactBase = (finalTok != null) ? finalTok : contentTok;
  const bytesText = showBytes ? fmtBytes(bytes) : '';
  if (finalOnly) {
    let label = `Tokens: ${fmtNum(compactBase)}`;
    if (bytesText) label += ` | ${bytesText}`;
    tokenParts.push(label);
  } else if (finalTok != null) {
    let label = `Visible tokens: ${fmtNum(contentTok)} (final: ${fmtNum(finalTok)})`;
    if (bytesText) label += ` | ${bytesText}`;
    tokenParts.push(label);
    tokenParts.push(`Tokens: ${fmtCompact(compactBase)}`);
  } else if (finalSkipped && finalCount != null) {
    let label = `Visible tokens: ${fmtNum(contentTok)} (final: auto-skip over ${fmtNum(finalCount)} files)`;
    if (bytesText) label += ` | ${bytesText}`;
    tokenParts.push(label);
    tokenParts.push(`Tokens: ${fmtCompact(compactBase)}`);
  } else {
    let label = `Visible tokens: ${fmtNum(contentTok)}`;
    if (bytesText) label += ` | ${bytesText}`;
    tokenParts.push(label);
    tokenParts.push(`Tokens: ${fmtCompact(compactBase)}`);
  }
  if (tokenEl) tokenEl.textContent = tokenParts.join('  ');
}

async function maybeComputeFinalTokens(scanId) {
  if (scanId != null && scanId !== window.__SCAN_ID) return;
  const path = document.getElementById('last_directory').value;
  // Footer "Final" always reflects the current visible list, not selection
  const visibleList = _visibleFilesForTotals();
  let selected_paths = visibleList.map(f => f.abs_path);
  try { selected_paths = filterNonExcludedPaths(selected_paths); } catch (_) { }
  // Avoid very heavy recompute on extremely large sets
  if (selected_paths.length > 3000) {
    window.__TOK_FINAL = undefined;
    window.__TOK_FINAL_SKIPPED = true;
    window.__TOK_FINAL_COUNT = selected_paths.length;
    updateScanStatsText();
    return;
  }
  try {
    const mergeOpts = buildMergeOutputOptions();
    const res = await fetch('/api/verify_tokens', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path, selected_paths, ensure_summaries: true, ...mergeOpts }) });
    const d = await res.json();
    if (scanId != null && scanId !== window.__SCAN_ID) return; // ignore stale
    if (res.ok) {
      window.__TOK_FINAL = d.tokens_total || 0;
      window.__TOK_CONTENT = d.tokens_content_only || window.__TOK_CONTENT || 0;
      try { window.__TOK_FINAL_COUNT = selected_paths.length; } catch (_) { }
      window.__TOK_FINAL_SKIPPED = false;
      updateScanStatsText();
      handleMergeSummaryErrors(d, selected_paths);
    }
  } catch (_) { }
}

function applyPreset(name) {
  const defs = {
    // express presets as include-only or exclude lists so new categories don't break silently
    ONLY_CODE: { exclude: ["DOCS", "CONFIG", "ASSETS", "BINARY"] },
    NO_FRONT: { exclude: ["FRONTEND"] },
    NO_CONFIG: { exclude: ["CONFIG"] },
    NO_ASSETS: { exclude: ["ASSETS"] },
    DOCS_ONLY: { include: ["DOCS"] },
    NO_DOCS: { exclude: ["DOCS"] },
  };
  const def = defs[name] || null;
  if (!def) return;
  const inc = new Set(def.include || []);
  const exc = new Set(def.exclude || []);
  document.querySelectorAll('.f_cat').forEach(cb => {
    cb.checked = def.include ? inc.has(cb.value) : !exc.has(cb.value);
  });
  try { schedulePersistFilters(0); } catch (_) { }
  try { requestFilterApply(); } catch (_) { }
}

async function loadUserPresetList(opts) {
  opts = opts || {};
  const sel = document.getElementById('user_preset_list');
  if (!sel) return;
  const prior = sel.value || '';
  try {
    const res = await fetch('/api/presets');
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    const names = Array.isArray(data) ? data : (data.names || []);
    const safeNames = names.filter(n => typeof n === 'string' && n.trim()).map(n => n.trim()).sort();
    sel.innerHTML = '<option value="">(select)</option>' + safeNames.map(n => `<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`).join('');
    let want = '';
    if (opts.selectName !== undefined && opts.selectName !== null) {
      want = String(opts.selectName);
    } else if (window.__CURRENT_CONFIG && window.__CURRENT_CONFIG.selected_preset) {
      want = String(window.__CURRENT_CONFIG.selected_preset);
    } else if (window.__CURRENT_CONFIG && window.__CURRENT_CONFIG.last_preset) {
      want = String(window.__CURRENT_CONFIG.last_preset);
    } else {
      want = prior;
    }
    if (want && safeNames.indexOf(want) !== -1) sel.value = want;
  } catch (err) {
    try { console.warn('Failed to load presets:', err); } catch (_) { }
  }
}

async function gatherUiSnapshot() {
  let base = window.__CURRENT_CONFIG || {};
  try {
    const res0 = await fetch('/api/config');
    if (res0.ok) {
      const d0 = await res0.json();
      if (d0 && d0.config) base = d0.config;
    }
  } catch (_) { }
  base = base || {};
  try { window.__CURRENT_CONFIG = base; window.__CONFIG_LOADED = true; } catch (_) { }

  const checked = (id, fallback) => {
    const el = document.getElementById(id);
    if (!el) return !!fallback;
    return !!el.checked;
  };
  const floatVal = (id, fallback) => {
    const el = document.getElementById(id);
    if (!el) return (typeof fallback === 'number' && isFinite(fallback)) ? fallback : 0.0;
    const n = parseFloat(String(el.value || ''));
    return isFinite(n) ? n : ((typeof fallback === 'number' && isFinite(fallback)) ? fallback : 0.0);
  };

  const snapshot = {};
  snapshot.include_all = checked('include_all', base.include_all);
  snapshot.view_mode = (document.getElementById('view_mode') && document.getElementById('view_mode').value) ? document.getElementById('view_mode').value : (base.view_mode || 'hierarchy');
  snapshot.sort_by = (document.getElementById('sort_by') && document.getElementById('sort_by').value) ? document.getElementById('sort_by').value : (base.sort_by || 'name');
  snapshot.sort_descending = checked('sort_desc', base.sort_descending);
  snapshot.ignore_binary_files = checked('f_ignore_binary', base.ignore_binary_files);
  snapshot.max_file_size_mb = floatVal('f_max_mb', base.max_file_size_mb);
  snapshot.follow_symlinks = checked('f_follow_symlinks', base.follow_symlinks);
  snapshot.exclude_ios_noise = checked('f_ex_ios', base.exclude_ios_noise);
  snapshot.exclude_node_modules = checked('f_ex_node', (base.exclude_node_modules !== undefined) ? base.exclude_node_modules : true);
  snapshot.exclude_python_noise = checked('f_ex_py', base.exclude_python_noise);
  snapshot.exclude_java_gradle_noise = checked('f_ex_java', base.exclude_java_gradle_noise);
  snapshot.exclude_web_build_noise = checked('f_ex_web', base.exclude_web_build_noise);
  snapshot.always_include_docs = checked('f_always_docs', (base.always_include_docs !== undefined) ? base.always_include_docs : true);
  snapshot.name_filter_glob = checked('f_name_glob', base.name_filter_glob);

  // Optional UI toggles persisted in the config snapshot.
  snapshot.show_totals_columns = checked('toggle_totals', base.show_totals_columns);
  snapshot.show_density_column = checked('toggle_density', base.show_density_column);
  snapshot.show_bytes_footer = checked('toggle_bytes_footer', (base.show_bytes_footer !== undefined) ? base.show_bytes_footer : true);

  // Categories
  const catEls = Array.from(document.querySelectorAll('.f_cat'));
  if (catEls.length) {
    const cm = {};
    catEls.forEach(cb => { try { cm[cb.value] = !!cb.checked; } catch (_) { } });
    snapshot.category_mask = cm;
  } else if (base.category_mask && typeof base.category_mask === 'object') {
    snapshot.category_mask = Object.assign({}, base.category_mask);
  } else {
    snapshot.category_mask = {};
  }

  // File types
  const ftEls = Array.from(document.querySelectorAll('#f_types input.ft_cb'));
  if (ftEls.length) {
    const ft = {};
    ftEls.forEach(cb => {
      if (!cb || !cb.dataset || !cb.hasAttribute || !cb.hasAttribute('data-ext')) return;
      const ext = String(cb.dataset.ext || '');
      ft[ext] = !!cb.checked;
    });
    snapshot.file_types = ft;
  } else if (base.file_types && typeof base.file_types === 'object') {
    snapshot.file_types = Object.assign({}, base.file_types);
  } else {
    snapshot.file_types = {};
  }

  // Filters (use same shape as config.last_filters)
  const f = getFilters();
  snapshot.last_filters = {
    categories: f.categories || [],
    token_min: f.token_min,
    token_max: f.token_max,
    size_min: f.size_min,
    size_max: f.size_max,
    name_pattern: f.name_pattern || "",
    date_after: f.date_after,
    date_before: f.date_before,
    date_after_manual: _getDateManualFlag(document.getElementById('f_date_after'), DATE_AFTER_DEFAULT),
    date_before_manual: _getDateManualFlag(document.getElementById('f_date_before'), DATE_BEFORE_DEFAULT),
  };

  // Extra list defaults (from config; not editable in filter panel)
  if (Array.isArray(base.always_include_docs_list)) snapshot.always_include_docs_list = base.always_include_docs_list;

  return snapshot;
}

async function saveUserPreset() {
  const sel = document.getElementById('user_preset_list');
  const current = (sel && sel.value) ? sel.value : '';
  const raw = prompt('Preset Name:', current);
  if (raw == null) return;
  const name = String(raw || '').trim();
  if (!name) return;
  try {
    if (sel) {
      const exists = Array.from(sel.options || []).some(o => o && o.value === name);
      if (exists && !confirm(`Overwrite preset "${name}"?`)) return;
    }
  } catch (_) { }

  try {
    const preset = await gatherUiSnapshot();
    const res = await fetch('/api/presets', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, preset }) });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    await loadUserPresetList({ selectName: name });
    try { await persistConfigPatch({ selected_preset: name }); } catch (_) { }
    try { showBanner(`Saved preset: ${name}`, ''); setTimeout(hideBanner, 2000); } catch (_) { }
  } catch (err) {
    alert('Save preset failed: ' + err);
  }
}

async function exportUserPreset() {
  const sel = document.getElementById('user_preset_list');
  const name = (sel && sel.value) ? sel.value : '';
  if (!name) { alert('Select a preset first.'); return; }
  try {
    const res = await fetch('/api/presets/' + encodeURIComponent(name));
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    const payload = { name: data.name || name, preset: data.preset || {} };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${name}.json`;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { try { URL.revokeObjectURL(url); } catch (_) { } }, 0);
    document.body.removeChild(a);
    try { showBanner(`Exported preset: ${name}`, ''); setTimeout(hideBanner, 2000); } catch (_) { }
  } catch (err) {
    alert('Export preset failed: ' + err);
  }
}

function importUserPreset() {
  const input = document.getElementById('preset_import_file');
  if (!input) return;
  try { input.value = ''; } catch (_) { }
  input.click();
}

async function handlePresetImportFile(e) {
  const input = e && e.target ? e.target : document.getElementById('preset_import_file');
  if (!input || !input.files || !input.files.length) return;
  const file = input.files[0];
  if (!file) return;
  try {
    const text = await file.text();
    let data = JSON.parse(text);
    let name = (data && typeof data.name === 'string') ? data.name : '';
    let preset = (data && typeof data === 'object' && data.preset) ? data.preset : data;
    if (!name) {
      const base = (file && file.name) ? file.name.replace(/\.json$/i, '') : '';
      const proposed = base || '';
      const raw = prompt('Preset name for import:', proposed);
      if (raw == null) return;
      name = String(raw || '').trim();
    }
    if (!name) { alert('Preset name is required.'); return; }
    if (!preset || typeof preset !== 'object') { alert('Invalid preset file.'); return; }
    const sel = document.getElementById('user_preset_list');
    try {
      const exists = sel && Array.from(sel.options || []).some(o => o && o.value === name);
      if (exists && !confirm(`Overwrite preset "${name}"?`)) return;
    } catch (_) { }
    const res = await fetch('/api/presets', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, preset }) });
    const payload = await res.json();
    if (!res.ok) throw new Error(payload.error || ('HTTP ' + res.status));
    await loadUserPresetList({ selectName: name });
    try { await persistConfigPatch({ selected_preset: name }); } catch (_) { }
    try { showBanner(`Imported preset: ${name}`, ''); setTimeout(hideBanner, 2000); } catch (_) { }
  } catch (err) {
    alert('Import preset failed: ' + err);
  } finally {
    try { input.value = ''; } catch (_) { }
  }
}

async function loadUserPreset() {
  const sel = document.getElementById('user_preset_list');
  const name = (sel && sel.value) ? sel.value : '';
  if (!name) { alert('Select a preset first.'); return; }
  try {
    const res = await fetch('/api/presets/load', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    const cfg = (data && data.config) ? data.config : {};
    try { window.__CURRENT_CONFIG = cfg; window.__CONFIG_LOADED = true; } catch (_) { }
    try { if (cfg.file_types) renderFileTypes(cfg.file_types); } catch (_) { }
    try { _applyConfigToInputs(cfg); } catch (_) { }
    try { applyIncludeAllState({ silent: true }); } catch (_) { }
    try {
      const vm = document.getElementById('view_mode');
      if (vm && cfg.view_mode) vm.value = String(cfg.view_mode);
      syncViewModeRadios();
    } catch (_) { }
    try { applyUiSettingsFromServer(); } catch (_) { }
    try { requestScan(50); } catch (_) { }
    try { showBanner(`Loaded preset: ${name}`, ''); setTimeout(hideBanner, 2000); } catch (_) { }
  } catch (err) {
    alert('Load preset failed: ' + err);
  }
}

async function deleteUserPreset() {
  const sel = document.getElementById('user_preset_list');
  const name = (sel && sel.value) ? sel.value : '';
  if (!name) { alert('Select a preset first.'); return; }
  if (!confirm(`Delete preset "${name}"?`)) return;
  try {
    const res = await fetch('/api/presets/' + encodeURIComponent(name), { method: 'DELETE' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    await loadUserPresetList();
    try {
      if (window.__CURRENT_CONFIG && window.__CURRENT_CONFIG.selected_preset === name) {
        window.__CURRENT_CONFIG.selected_preset = '';
        await persistConfigPatch({ selected_preset: '' });
      }
    } catch (_) { }
    try { showBanner(`Deleted preset: ${name}`, ''); setTimeout(hideBanner, 2000); } catch (_) { }
  } catch (err) {
    alert('Delete preset failed: ' + err);
  }
}

function renderTable() {
  const data = (window.__VISIBLE || (window.__SCAN && window.__SCAN.files) || []);
  const tbody = document.querySelector("#files_tbl tbody");
  if (!tbody) return;
  tbody.innerHTML = "";
  const sel = window.__SEL || new Set();
  const view = document.getElementById("view_mode").value || "hierarchy";
  // Ensure table-level selection/context menu handlers are bound immediately.
  bindRowCheckboxes(tbody);

  // Chunked renders keep the UI responsive on very large lists.
  window.__RENDER_SESSION = (window.__RENDER_SESSION || 0) + 1;
  const renderSession = window.__RENDER_SESSION;
  const willChunk = data.length > 2000;

  // Show a "Rendering..." overlay for chunked renders when we own the overlay.
  try {
    const overlay = document.getElementById('overlay');
    const overlayVisible = !!(overlay && overlay.style.display !== 'none');
    const owned = (typeof window.__RENDER_OVERLAY_SESSION === 'number');
    if (willChunk) {
      if (!overlayVisible) {
        showOverlay('Rendering…');
        window.__RENDER_OVERLAY_SESSION = renderSession;
      } else if (owned) {
        // Transfer ownership to the latest render session.
        const msg = document.getElementById('overlay_msg'); if (msg) msg.textContent = 'Rendering…';
        window.__RENDER_OVERLAY_SESSION = renderSession;
      }
    } else if (owned) {
      // A previous render might have left the overlay visible; clear it.
      hideOverlay();
      window.__RENDER_OVERLAY_SESSION = null;
    }
  } catch (_) { }

  if (view === "flat") {
    renderFlat(data, tbody, sel, renderSession);
    bindHeaderSorting(true);
  } else {
    renderHierarchy(data, tbody, sel, renderSession);
    bindHeaderSorting(true);
  }
  updateScanStatsText();
}

function renderFlat(data, tbody, sel, renderSession) {
  const _totEl = document.getElementById('toggle_totals');
  const showTotals = !!(_totEl && _totEl.checked);
  if (data.length > 2000) {
    let i = 0; const CH = 500;
    const step = () => {
      if (renderSession !== window.__RENDER_SESSION) return;
      const max = Math.min(i + CH, data.length);
      const frag = document.createDocumentFragment();
      for (; i < max; i++) {
        const f = data[i];
        const tr = document.createElement("tr");
        tr.setAttribute('role', 'row');
        tr.dataset.idx = i;
        tr.dataset.abs = f.abs_path;
        tr.tabIndex = 0;
        const isSelected = sel.has(f.abs_path);
        tr.setAttribute('aria-selected', isSelected ? 'true' : 'false');
        if (isSelected) tr.classList.add("selected");
        if (f.duplicate) tr.classList.add("dup");
        if (f.is_manual) tr.classList.add("manual");
        if (f.read_error) { tr.classList.add("read-error"); tr.title = 'Token count unavailable (binary or read error)'; }
        const hasErr = hasSummaryError(f.abs_path);
        if (hasErr) tr.classList.add("summary-error");
        const isMatch = isPatternHit(f.rel_path);
        if (isMatch) tr.classList.add("match");
        const tokVal = (f.token_count !== undefined && f.token_count !== null) ? f.token_count : null;
        if (tokVal != null && tokVal > 100000) tr.classList.add("high-tokens");
        const kbNum = (f.size_bytes || 0) / 1024;
        const kb = Math.floor(kbNum);
        const tokNum = (tokVal != null) ? tokVal : 0;
        const tokDisp = (tokVal != null) ? tokVal : '';
        const dens = (tokVal != null && kbNum > 0) ? (tokNum / kbNum).toFixed(1) : '';
        const tokTotal = (tokVal != null) ? tokVal : 0;
        const sizeTotal = f.size_bytes || 0;
        const catClass = categoryClass(f.category);
        const modeText = modeLabel(f.mode || window.__DEFAULT_MODE || 'CODE', hasErr);
        const catText = (f.category || '') ? String(f.category).toLowerCase() : '';
        if (catClass) tr.classList.add(catClass);
        const errBadge = f.read_error ? ' <span class="read-error-pill">ERR</span>' : '';
        tr.innerHTML = `
          <td>${escapeHtml(f.rel_path)}${errBadge}</td>
          <td>${escapeHtml(modeText)}</td>
          <td>${escapeHtml(f.ext || "")}</td>
          <td class="num">${kb}</td>
          <td class="num">${tokDisp}</td>
          <td class="num">${(window.__SHOW_DENS === false) ? '' : dens}</td>
          <td class="cat-cell ${catClass}">${escapeHtml(catText)}</td>
          <td class="num">${showTotals ? tokTotal : ''}</td>
          <td class="num">${showTotals ? sizeTotal : ''}</td>
        `;
        frag.appendChild(tr);
      }
      tbody.appendChild(frag);
      if (i < data.length) requestAnimationFrame(step);
      else {
        try {
          if (window.__RENDER_OVERLAY_SESSION === renderSession) {
            window.__RENDER_OVERLAY_SESSION = null;
            hideOverlay();
          }
        } catch (_) { }
      }
    };
    requestAnimationFrame(step);
  } else {
    data.forEach((f, idx) => {
      const tr = document.createElement("tr");
      tr.setAttribute('role', 'row');
      tr.dataset.idx = idx;
      tr.dataset.abs = f.abs_path;
      tr.tabIndex = 0;
      const isSelected = sel.has(f.abs_path);
      tr.setAttribute('aria-selected', isSelected ? 'true' : 'false');
      if (isSelected) tr.classList.add("selected");
      if (f.duplicate) tr.classList.add("dup");
      if (f.is_manual) tr.classList.add("manual");
      if (f.read_error) { tr.classList.add("read-error"); tr.title = 'Token count unavailable (binary or read error)'; }
      const hasErr = hasSummaryError(f.abs_path);
      if (hasErr) tr.classList.add("summary-error");
      const isMatch = isPatternHit(f.rel_path);
      if (isMatch) tr.classList.add("match");
      const tokVal = (f.token_count !== undefined && f.token_count !== null) ? f.token_count : null;
      if (tokVal != null && tokVal > 100000) tr.classList.add("high-tokens");
      const kbNum = (f.size_bytes || 0) / 1024;
      const kb = Math.floor(kbNum);
      const tokNum = (tokVal != null) ? tokVal : 0;
      const tokDisp = (tokVal != null) ? tokVal : '';
      const dens = (tokVal != null && kbNum > 0) ? (tokNum / kbNum).toFixed(1) : '';
      const tokTotal = (tokVal != null) ? tokVal : 0;
      const sizeTotal = f.size_bytes || 0;
      const catClass = categoryClass(f.category);
      const modeText = modeLabel(f.mode || window.__DEFAULT_MODE || 'CODE', hasErr);
      const catText = (f.category || '') ? String(f.category).toLowerCase() : '';
      if (catClass) tr.classList.add(catClass);
      const errBadge = f.read_error ? ' <span class="read-error-pill">ERR</span>' : '';
      tr.innerHTML = `
        <td>${escapeHtml(f.rel_path)}${errBadge}</td>
        <td>${escapeHtml(modeText)}</td>
        <td>${escapeHtml(f.ext || "")}</td>
        <td class="num">${kb}</td>
        <td class="num">${tokDisp}</td>
        <td class="num">${(window.__SHOW_DENS === false) ? '' : dens}</td>
        <td class="cat-cell ${catClass}">${escapeHtml(catText)}</td>
        <td class="num">${showTotals ? tokTotal : ''}</td>
        <td class="num">${showTotals ? sizeTotal : ''}</td>
      `;
      tbody.appendChild(tr);
    });
  }
  bindRowCheckboxes(tbody);
}

function buildTree(data) {
  const folderModes = (window.__FOLDER_MODES && typeof window.__FOLDER_MODES === 'object') ? window.__FOLDER_MODES : {};
  const rootAbs = _currentRootPath();
  const root = { name: "", path: "", type: "folder", children: new Map(), size: 0, tokens: 0, mtime: 0, abs: rootAbs, folderMode: folderModes[rootAbs] || null };
  data.forEach(f => {
    const parts = (f.rel_path || "").split("/");
    let node = root; let cur = "";
    for (let i = 0; i < parts.length - 1; i++) {
      const name = parts[i];
      cur = cur ? (cur + "/" + name) : name;
      if (!node.children.has(name)) {
        const abs = absPathForRel(cur);
        node.children.set(name, { name, path: cur, type: "folder", children: new Map(), size: 0, tokens: 0, mtime: 0, abs, folderMode: folderModes[abs] || null });
      }
      node = node.children.get(name);
    }
    const fname = parts[parts.length - 1];
    const fileNode = { name: fname, path: f.abs_path, rel_path: f.rel_path, type: "file", size: f.size_bytes, tokens: (f.token_count || 0), token_count: f.token_count, ext: f.ext, category: f.category, mode: f.mode, duplicate: !!f.duplicate, is_manual: !!f.is_manual, read_error: !!f.read_error, mtime: f.mtime || 0 };
    if (fname) node.children.set("\0" + f.abs_path, fileNode);
  });
  function agg(n) {
    if (n.type === 'file') return { size: n.size || 0, tokens: n.tokens || 0, mtime: n.mtime || 0 };
    let size = 0, tokens = 0, mtime = 0;
    for (const ch of n.children.values()) {
      const a = agg(ch);
      size += a.size;
      tokens += a.tokens;
      if (a.mtime > mtime) mtime = a.mtime;
    }
    n.size = size; n.tokens = tokens; n.mtime = mtime;
    return { size, tokens, mtime };
  }
  agg(root); return root;
}

function renderHierarchy(data, tbody, sel, renderSession) {
  if (!window.__EXPANDED) window.__EXPANDED = new Set([""]);
  const tree = buildTree(data);
  const _totEl = document.getElementById('toggle_totals');
  const showTotals = !!(_totEl && _totEl.checked);
  const headerSort = !!window.__HEADER_SORT_ACTIVE;
  const configSort = (() => {
    const sortByEl = document.getElementById('sort_by');
    const sortBy = sortByEl ? String(sortByEl.value || '') : ((window.__CURRENT_CONFIG && window.__CURRENT_CONFIG.sort_by) ? window.__CURRENT_CONFIG.sort_by : 'name');
    const sortDescEl = document.getElementById('sort_desc');
    const desc = sortDescEl ? !!sortDescEl.checked : !!(window.__CURRENT_CONFIG && window.__CURRENT_CONFIG.sort_descending);
    const map = { name: 'name', size: 'size_bytes', date: 'mtime', tokens: 'token_count', token_density: 'dens' };
    return { col: map[sortBy] || 'name', desc };
  })();
  const sortState = headerSort ? (window.__SORT || { col: 'rel_path', desc: false }) : configSort;
  const shouldSort = !!sortState;

  const nodeMetric = (node, col) => {
    switch (col) {
      case 'size_bytes':
      case 'kb_total':
        return node.size || 0;
      case 'token_count':
      case 'tok_total':
        return node.tokens || 0;
      case 'dens': {
        const kb = (node.size || 0) / 1024.0;
        return kb > 0 ? ((node.tokens || 0) / kb) : 0;
      }
      case 'mtime':
      case 'date':
        return node.mtime || 0;
      case 'ext':
        return (node.ext || '').toLowerCase();
      case 'mode':
        return (node.mode || '').toLowerCase();
      case 'category':
        return (node.category || (node.type === 'folder' ? 'folder' : '')).toLowerCase();
      default:
        return (node.name || '').toLowerCase();
    }
  };

  const sortNodes = (arr) => {
    if (!shouldSort || !sortState) return;
    const col = sortState.col || 'rel_path';
    arr.sort((a, b) => {
      const va = nodeMetric(a, col);
      const vb = nodeMetric(b, col);
      if (va < vb) return sortState.desc ? 1 : -1;
      if (va > vb) return sortState.desc ? -1 : 1;
      return (a.name || '').localeCompare(b.name || '');
    });
  };

  function rowFolder(node, depth) {
    const tr = document.createElement("tr");
    tr.classList.add("folder");
    tr.setAttribute('role', 'row');
    tr.dataset.folder = node.path;
    tr.dataset.folderAbs = node.abs || '';
    tr.dataset.folderMode = node.folderMode || '';
    tr.tabIndex = 0;
    const kbTotal = (node.size || 0);
    const tokSum = (node.tokens || 0);
    const open = window.__EXPANDED.has(node.path);
    tr.setAttribute('aria-expanded', open ? 'true' : 'false');
    tr.setAttribute('aria-selected', 'false');
    const pad = depth * 16;
    const pill = folderModePill(node.folderMode);
    tr.innerHTML = `
      <td><div class="tree-cell" style="padding-left:${pad}px"><span class="twisty" data-folder="${escapeHtml(node.path)}">${open ? '▾' : '▸'}</span>${escapeHtml(node.name || '(root)')}${pill}</div></td>
      <td></td>
      <td></td>
      <td class="num"></td>
      <td class="num"></td>
      <td class="num"></td>
      <td></td>
      <td class="num">${showTotals ? tokSum : ''}</td>
      <td class="num">${showTotals ? kbTotal : ''}</td>
    `;
    return tr;
  }
  function rowFile(f, depth) {
    const tr = document.createElement("tr");
    tr.setAttribute('role', 'row');
    const isSelected = sel.has(f.path);
    tr.setAttribute('aria-selected', isSelected ? 'true' : 'false');
    if (isSelected) tr.classList.add("selected");
    if (f.duplicate) tr.classList.add("dup");
    if (f.is_manual) tr.classList.add("manual");
    if (f.read_error) { tr.classList.add("read-error"); tr.title = 'Token count unavailable (binary or read error)'; }
    const hasErr = hasSummaryError(f.path);
    if (hasErr) tr.classList.add("summary-error");
    const isMatch = isPatternHit(f.rel_path || '');
    if (isMatch) tr.classList.add("match");
    const tokVal = (f.token_count !== undefined && f.token_count !== null) ? f.token_count : null;
    if (tokVal != null && tokVal > 100000) tr.classList.add("high-tokens");
    const kbNum = (f.size || 0) / 1024;
    const kb = Math.floor(kbNum);
    const tokNum = (tokVal != null) ? tokVal : 0;
    const tokDisp = (tokVal != null) ? tokVal : '';
    const dens = (tokVal != null && kbNum > 0) ? (tokNum / kbNum).toFixed(1) : '';
    const pad = depth * 16;
    const catClass = categoryClass(f.category || '');
    const modeText = modeLabel(f.mode || window.__DEFAULT_MODE || 'CODE', hasErr);
    const catText = (f.category || '') ? String(f.category).toLowerCase() : '';
    if (catClass) tr.classList.add(catClass);
    const errBadge = f.read_error ? ' <span class="read-error-pill">ERR</span>' : '';
    tr.innerHTML = `
      <td><div class="tree-cell" style="padding-left:${pad}px"><span class="twisty" style="visibility:hidden">•</span>${escapeHtml(f.name)}${errBadge}</div></td>
      <td>${escapeHtml(modeText)}</td>
      <td>${escapeHtml(f.ext || "")}</td>
      <td class="num">${kb}</td>
      <td class="num">${tokDisp}</td>
      <td class="num">${(window.__SHOW_DENS === false) ? '' : dens}</td>
      <td class="cat-cell ${catClass}">${escapeHtml(catText)}</td>
      <td class="num"></td>
      <td class="num"></td>
    `;
    tr.dataset.abs = f.path;
    tr.tabIndex = 0;
    return tr;
  }

  // Flatten the visible tree into row descriptors, then render in chunks.
  const rows = [];
  function walk(node, depth) {
    if (node !== tree) rows.push({ t: 'folder', n: node, d: depth });
    const open = window.__EXPANDED.has(node.path);
    if (!open) return;
    if (shouldSort) {
      const folders = []; const files = [];
      for (const child of node.children.values()) { if (child.type === 'folder') folders.push(child); else files.push(child); }
      sortNodes(folders);
      sortNodes(files);
      for (const ch of folders) walk(ch, depth + 1);
      for (const fi of files) rows.push({ t: 'file', n: fi, d: depth + 1 });
      return;
    }
    for (const child of node.children.values()) {
      if (child.type === 'folder') walk(child, depth + 1);
      else rows.push({ t: 'file', n: child, d: depth + 1 });
    }
  }
  if (shouldSort) {
    const topFolders = []; const topFiles = [];
    for (const child of tree.children.values()) { if (child.type === 'folder') topFolders.push(child); else topFiles.push(child); }
    sortNodes(topFolders);
    sortNodes(topFiles);
    for (const f of topFolders) walk(f, 0);
    for (const f of topFiles) rows.push({ t: 'file', n: f, d: 0 });
  } else {
    for (const child of tree.children.values()) {
      if (child.type === 'folder') walk(child, 0);
      else rows.push({ t: 'file', n: child, d: 0 });
    }
  }

  const doChunked = rows.length > 2000;
  if (doChunked) {
    let i = 0; const CH = 500;
    const step = () => {
      if (renderSession !== window.__RENDER_SESSION) return;
      const end = Math.min(i + CH, rows.length);
      const frag = document.createDocumentFragment();
      for (; i < end; i++) {
        const r = rows[i];
        if (r.t === 'folder') frag.appendChild(rowFolder(r.n, r.d));
        else frag.appendChild(rowFile(r.n, r.d));
      }
      tbody.appendChild(frag);
      if (i < rows.length) requestAnimationFrame(step);
      else {
        bindRowCheckboxes(tbody);
        try {
          if (window.__RENDER_OVERLAY_SESSION === renderSession) {
            window.__RENDER_OVERLAY_SESSION = null;
            hideOverlay();
          }
        } catch (_) { }
      }
    };
    requestAnimationFrame(step);
  } else {
    const frag = document.createDocumentFragment();
    rows.forEach(r => {
      if (r.t === 'folder') frag.appendChild(rowFolder(r.n, r.d));
      else frag.appendChild(rowFile(r.n, r.d));
    });
    tbody.appendChild(frag);
    bindRowCheckboxes(tbody);
    try {
      if (window.__RENDER_OVERLAY_SESSION === renderSession) {
        window.__RENDER_OVERLAY_SESSION = null;
        hideOverlay();
      }
    } catch (_) { }
  }
}

function bindRowCheckboxes(tbody) {
  // Setup one-time table-level handlers for row clicks and context menu
  const table = document.getElementById('files_tbl');
  if (!table.__boundRows) {
    const toggleFolder = (fp) => {
      if (!fp) return;
      if (!window.__EXPANDED) window.__EXPANDED = new Set(['']);
      if (window.__EXPANDED.has(fp)) window.__EXPANDED.delete(fp); else window.__EXPANDED.add(fp);
      renderTable();
    };
    table.addEventListener('click', (e) => {
      // Handle tree twisty toggles via delegation
      const tw = e.target.closest('.twisty');
      if (tw && table.contains(tw)) {
        const fp = tw.getAttribute('data-folder');
        if (fp) {
          e.preventDefault(); e.stopPropagation();
          toggleFolder(fp);
          return;
        }
      }
      const pill = e.target.closest('.mode-pill-btn');
      if (pill && table.contains(pill)) {
        const tr = pill.closest('tr');
        const abs = tr ? tr.getAttribute('data-abs') : null;
        if (abs) {
          e.preventDefault();
          e.stopPropagation();
          cycleModeForAbs(abs);
        }
        return;
      }
      const tr = e.target.closest('tr');
      if (!tr || !table.contains(tr)) return;
      // Ignore native checkbox clicks
      if (e.target && e.target.tagName === 'INPUT') return;
      const abs = tr.getAttribute('data-abs');
      if (!abs) return;
      if (e.shiftKey && window.__LAST_CLICK_PATH) {
        rangeSelectByAbs(window.__LAST_CLICK_PATH, abs);
      } else {
        toggleSelectAbs(abs, tr);
      }
      window.__LAST_CLICK_PATH = abs;
    });
    table.addEventListener('dblclick', (e) => {
      const tr = e.target.closest('tr');
      if (!tr || !table.contains(tr)) return;
      const folder = tr.getAttribute('data-folder');
      if (folder) {
        e.preventDefault();
        toggleFolder(folder);
        return;
      }
      const abs = tr.getAttribute('data-abs');
      if (!abs) return;
      e.preventDefault();
      previewFile(abs);
    });
    table.addEventListener('contextmenu', (e) => {
      const tr = e.target.closest('tr');
      if (!tr || !table.contains(tr)) return;
      e.preventDefault();
      showContextMenu(e.clientX, e.clientY, tr);
    });
    document.addEventListener('click', (e) => { hideContextMenu(); });
    table.__boundRows = true;
  }
}

function _isTypingField(el) {
  if (!el) return false;
  try { if (el.isContentEditable) return true; } catch (_) { }
  const tag = (el.tagName || '').toLowerCase();
  if (tag === 'textarea') return true;
  if (tag !== 'input') return false;
  const type = String(el.getAttribute('type') || '').toLowerCase();
  // Treat checkbox-like inputs as non-typing so global shortcuts still work.
  if (type === 'checkbox' || type === 'radio' || type === 'button' || type === 'submit' || type === 'reset') return false;
  return true;
}

function _isElementVisible(el) {
  if (!el) return false;
  try {
    const d = (el.style && el.style.display) ? el.style.display : '';
    if (d) return d !== 'none';
    return window.getComputedStyle(el).display !== 'none';
  } catch (_) { return false; }
}

  function _topVisibleModal() {
  let best = null;
  let bestZ = -Infinity;
  const nodes = Array.from(document.querySelectorAll('.modal'));
  for (const m of nodes) {
    if (!_isElementVisible(m)) continue;
    let z = 0;
    try {
      const zi = window.getComputedStyle(m).zIndex;
      z = parseInt(zi || '0', 10);
      if (!isFinite(z)) z = 0;
    } catch (_) { z = 0; }
    if (!best || z >= bestZ) { best = m; bestZ = z; }
  }

function _modalFocusable(modal) {
  if (!modal) return [];
  const selector = [
    'a[href]',
    'button:not([disabled])',
    'input:not([disabled])',
    'select:not([disabled])',
    'textarea:not([disabled])',
    '[tabindex]:not([tabindex="-1"])',
  ].join(',');
  return Array.from(modal.querySelectorAll(selector)).filter((el) => {
    try {
      return _isElementVisible(el) && !el.hasAttribute('disabled') && el.getAttribute('aria-hidden') !== 'true';
    } catch (_) { return false; }
  });
}

function _focusFirstInModal(modal) {
  const items = _modalFocusable(modal);
  const target = items[0] || modal;
  try { target.focus(); } catch (_) { }
}

function _trapModalTab(e, modal) {
  if (!modal || e.key !== 'Tab') return false;
  const items = _modalFocusable(modal);
  if (!items.length) {
    e.preventDefault();
    try { modal.focus(); } catch (_) { }
    return true;
  }
  const first = items[0];
  const last = items[items.length - 1];
  const active = document.activeElement;
  if (!modal.contains(active)) {
    e.preventDefault();
    try { first.focus(); } catch (_) { }
    return true;
  }
  if (e.shiftKey && active === first) {
    e.preventDefault();
    try { last.focus(); } catch (_) { }
    return true;
  }
  if (!e.shiftKey && active === last) {
    e.preventDefault();
    try { first.focus(); } catch (_) { }
    return true;
  }
  return false;
}
  return best;
}

function _closeModalEl(modalEl) {
  if (!modalEl) return;
  const id = modalEl.id || '';
  if (id === 'preview_modal') return hidePreviewModal();
  if (id === 'manual_picker_modal') return hideManualPicker();
  if (id === 'keep_only_modal') return hideKeepOnlyModal();
  if (id === 'why_modal') return hideWhyModal();
  if (id === 'categories_modal') return closeCategoriesModal();
  if (id === 'settings_modal') return closeSettingsModal();
  if (id === 'logs_modal') return closeSummarizerLogs();
  if (id === 'error_modal') { modalEl.style.display = 'none'; return; }
  if (id === 'overwrite_modal') { hideOverwriteModal('cancel'); return; }
  if (id === 'prune_summary_modal') { hidePruneSummaryModal(); return; }
  try { modalEl.style.display = 'none'; } catch (_) { }
}

function handleGlobalKey(e) {
  if (!e) return;

  // ESC: close modal > close context menu > blur input > clear selection
  if (e.key === 'Escape') {
    const modal = _topVisibleModal();
    if (modal) {
      e.preventDefault();
      _closeModalEl(modal);
      return;
    }
    const menu = document.getElementById('ctxmenu');
    if (menu && _isElementVisible(menu)) {
      e.preventDefault();
      try { hideContextMenu(); } catch (_) { try { menu.style.display = 'none'; } catch (_) { } }
      return;
    }
    const ae = document.activeElement;
    if (_isTypingField(ae)) {
      e.preventDefault();
      try { ae.blur(); } catch (_) { }
      return;
    }
    if (window.__SEL && window.__SEL.size) {
      e.preventDefault();
      clearSelection();
    }
    return;
  }

  // While a modal is open, avoid triggering global shortcuts behind it.
  const modal = _topVisibleModal();
  if (modal) {
    if (_trapModalTab(e, modal)) return;
    if (modal.id === 'preview_modal') {
      const isCmd = !!(e.metaKey || e.ctrlKey);
      if (isCmd && (e.key === 'a' || e.key === 'A')) {
        e.preventDefault();
        selectPreviewAll();
        return;
      }
      if (isCmd && (e.key === 'c' || e.key === 'C')) {
        e.preventDefault();
        copyPreviewSelectionOrAll();
        return;
      }
    }
    return;
  }

  // Skip when typing into a field (but allow global shortcuts when focused on non-typing controls).
  const active = document.activeElement;
  if (_isTypingField(active)) return;

  const isCmd = !!(e.metaKey || e.ctrlKey);
  const key = e.key;

  if (key === 'Delete' || key === 'Backspace') {
    // Prevent browser back navigation on Backspace.
    e.preventDefault();
    try { excludeSelectedPersist(); } catch (_) { }
    return;
  }

  if (isCmd && (key === 'a' || key === 'A')) {
    e.preventDefault();
    selectAllRows();
    return;
  }

  if (isCmd && (key === 'f' || key === 'F')) {
    const el = document.getElementById('f_name_pattern');
    if (el) {
      e.preventDefault();
      try { el.focus(); el.select(); } catch (_) { }
    }
    return;
  }

  if (key === ' ' || key === 'Spacebar') {
    const table = document.getElementById('files_tbl');
    const tr = (document.activeElement && document.activeElement.closest) ? document.activeElement.closest('tr') : null;
    if (tr && table && table.contains(tr)) {
      const abs = tr.getAttribute('data-abs');
      if (abs) {
        e.preventDefault();
        toggleSelectAbs(abs, tr);
      }
    }
    return;
  }

  if (key === 'ArrowDown' || key === 'ArrowUp') {
    const table = document.getElementById('files_tbl');
    if (!table) return;
    const tr = (document.activeElement && document.activeElement.closest) ? document.activeElement.closest('tr') : null;
    if (!tr || !table.contains(tr)) return;
    const rows = Array.from(table.querySelectorAll('tbody tr'));
    const idx = rows.indexOf(tr);
    if (idx < 0) return;
    const nextIdx = key === 'ArrowDown' ? Math.min(rows.length - 1, idx + 1) : Math.max(0, idx - 1);
    const next = rows[nextIdx];
    if (next && next !== tr) {
      e.preventDefault();
      try { next.focus(); } catch (_) { }
    }
    return;
  }
}

async function exportWorkspaceSession() {
  try {
    const selected_paths = Array.from(window.__SEL || []);
    const hiddenByRoot = (window.__HIDDEN_MAP && typeof window.__HIDDEN_MAP === 'object') ? window.__HIDDEN_MAP : {};
    const root = String((document.getElementById('last_directory') || {}).value || '');
    const hidden_paths = Array.from((root && hiddenByRoot[root]) ? hiddenByRoot[root] : []);
    const visible_paths = Array.from(window.__VISIBLE || []).map(f => f && f.abs_path).filter(Boolean);
    const res = await fetch('/api/session/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ selected_paths, hidden_paths, visible_paths }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    const blob = new Blob([JSON.stringify(data.session, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'codemerge-session.json';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    showBanner('Session exported', 'ok');
  } catch (err) {
    showError('Session Export Failed', err.message || err);
  }
}

function importWorkspaceSession() {
  const input = document.getElementById('session_import_file');
  if (!input) return;
  input.value = '';
  input.click();
}

async function handleSessionImportFile(e) {
  const input = e && e.target ? e.target : document.getElementById('session_import_file');
  const file = input && input.files && input.files[0];
  if (!file) return;
  try {
    const text = await file.text();
    const session = JSON.parse(text);
    const imported = await fetch('/api/session/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session }),
    });
    const data = await imported.json();
    if (!imported.ok) throw new Error(data.error || ('HTTP ' + imported.status));
    const selected = window.confirm('Apply imported session?');
    if (!selected) return;
    const applied = await fetch('/api/session/select', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session: data.session || session }),
    });
    const appliedData = await applied.json();
    if (!applied.ok) throw new Error(appliedData.error || ('HTTP ' + applied.status));
    if (appliedData.session && appliedData.session.root) {
      const rootEl = document.getElementById('last_directory');
      if (rootEl) rootEl.value = appliedData.session.root;
    }
    showBanner('Session imported', 'ok');
    try { await loadConfig(); } catch (_) { }
    try { await doScan(false); } catch (_) { }
  } catch (err) {
    showError('Session Import Failed', err.message || err);
  } finally {
    try { input.value = ''; } catch (_) { }
  }
}

async function openJobsModal() {
  const modal = document.getElementById('jobs_modal');
  if (!modal) return;
  modal.style.display = 'flex';
  await refreshJobsModal();
  _focusFirstInModal(modal);
}

function closeJobsModal() {
  const modal = document.getElementById('jobs_modal');
  if (modal) modal.style.display = 'none';
}

async function refreshJobsModal() {
  const out = document.getElementById('jobs_text');
  if (out) out.textContent = 'Loading...';
  try {
    const res = await fetch('/api/jobs?limit=50');
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    const jobs = Array.isArray(data.jobs) ? data.jobs : [];
    const lines = jobs.map((job) => {
      const bits = [
        job.id || '',
        job.type || 'unknown',
        job.status || '',
        (job.progress_pct !== undefined ? String(job.progress_pct) + '%' : ''),
        job.cancel_supported ? 'cancel_supported' : '',
        job.cancellable ? 'cancellable' : '',
        job.error ? ('error=' + job.error) : '',
      ].filter(Boolean);
      return bits.join('  ');
    });
    if (out) out.textContent = lines.length ? lines.join('\n') : 'No jobs.';
  } catch (err) {
    if (out) out.textContent = 'Failed to load jobs: ' + (err.message || err);
  }
}

function toggleSelectAbs(abs, tr) {
  if (!window.__SEL) window.__SEL = new Set();
  if (window.__SEL.has(abs)) window.__SEL.delete(abs); else window.__SEL.add(abs);
  if (tr) {
    tr.classList.toggle('selected');
    tr.setAttribute('aria-selected', window.__SEL.has(abs) ? 'true' : 'false');
  }
  // sync checkbox if present
  try {
    const chk = tr ? tr.querySelector('input[type="checkbox"][data-abs]') : null;
    if (chk && chk.getAttribute('data-abs') === abs) chk.checked = window.__SEL.has(abs);
  } catch (_) { }
  try { updateScanStatsText(); } catch (_) { }
}

function currentRenderOrder() {
  const data = (window.__VISIBLE || (window.__SCAN && window.__SCAN.files) || []);
  const mode = (document.getElementById('view_mode') || {}).value || 'hierarchy';
  const headerSort = !!window.__HEADER_SORT_ACTIVE;
  const configSort = (() => {
    const sortByEl = document.getElementById('sort_by');
    const sortBy = sortByEl ? String(sortByEl.value || '') : ((window.__CURRENT_CONFIG && window.__CURRENT_CONFIG.sort_by) ? window.__CURRENT_CONFIG.sort_by : 'name');
    const sortDescEl = document.getElementById('sort_desc');
    const desc = sortDescEl ? !!sortDescEl.checked : !!(window.__CURRENT_CONFIG && window.__CURRENT_CONFIG.sort_descending);
    const map = { name: 'name', size: 'size_bytes', date: 'mtime', tokens: 'token_count', token_density: 'dens' };
    return { col: map[sortBy] || 'name', desc };
  })();
  const sortState = headerSort ? (window.__SORT || { col: 'rel_path', desc: false }) : configSort;
  const shouldSort = !!sortState;
  if (!window.__EXPANDED) window.__EXPANDED = new Set(['']);
  if (mode === 'flat') {
    return data.map(f => f.abs_path);
  }
  // Hierarchy: mirror render order with current expansion state
  const tree = buildTree(data);
  const order = [];
  const metric = (n) => {
    const col = sortState ? sortState.col : 'rel_path';
    switch (col) {
      case 'size_bytes':
      case 'kb_total': return n.size || 0;
      case 'token_count':
      case 'tok_total': return n.tokens || 0;
      case 'dens': {
        const kb = (n.size || 0) / 1024.0;
        return kb > 0 ? ((n.tokens || 0) / kb) : 0;
      }
      case 'mtime':
      case 'date':
        return n.mtime || 0;
      case 'ext': return (n.ext || '').toLowerCase();
      case 'category': return (n.category || (n.type === 'folder' ? 'folder' : '')).toLowerCase();
      default: return (n.name || '').toLowerCase();
    }
  };
  const sorter = (a, b) => {
    if (!shouldSort || !sortState) return 0;
    const va = metric(a), vb = metric(b);
    if (va < vb) return sortState.desc ? 1 : -1;
    if (va > vb) return sortState.desc ? -1 : 1;
    return (a.name || '').localeCompare(b.name || '');
  };
  const walk = (node) => {
    const open = window.__EXPANDED && window.__EXPANDED.has(node.path);
    if (!open) return;
    if (shouldSort) {
      const folders = []; const files = [];
      for (const child of node.children.values()) { if (child.type === 'folder') folders.push(child); else files.push(child); }
      folders.sort(sorter);
      files.sort(sorter);
      for (const f of folders) {
        walk(f);
      }
      for (const f of files) {
        order.push(f.path);
      }
      return;
    }
    for (const child of node.children.values()) {
      if (child.type === 'folder') walk(child);
      else order.push(child.path);
    }
  };
  const roots = [];
  for (const child of tree.children.values()) roots.push(child);
  if (shouldSort) roots.sort(sorter);
  for (const r of roots) {
    if (r.type === 'folder') { walk(r); } else { order.push(r.path); }
  }
  return order;
}

function syncSelectionRows() {
  const table = document.getElementById('files_tbl');
  if (!table) return;
  table.querySelectorAll('tbody tr[data-abs]').forEach(tr => {
    const abs = tr.getAttribute('data-abs');
    const on = !!(window.__SEL && window.__SEL.has(abs));
    tr.classList.toggle('selected', on);
    const chk = tr.querySelector('input[type="checkbox"][data-abs]');
    if (chk) chk.checked = on;
  });
}

function rangeSelectByAbs(fromAbs, toAbs) {
  if (!fromAbs || !toAbs) return;
  const order = currentRenderOrder();
  const a = order.indexOf(fromAbs);
  const b = order.indexOf(toAbs);
  if (a < 0 || b < 0) return;
  const [start, end] = a <= b ? [a, b] : [b, a];
  if (!window.__SEL) window.__SEL = new Set();
  for (let i = start; i <= end; i++) {
    const abs = order[i];
    if (abs) window.__SEL.add(abs);
  }
  syncSelectionRows();
  try { updateScanStatsText(); } catch (_) { }
}

async function copyTextToClipboard(text) {
  const value = String(text || '');
  if (!value) return false;
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch (_) {
    // Fallback: hidden textarea
    try {
      const ta = document.createElement('textarea');
      ta.value = value;
      ta.style.position = 'fixed';
      ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      return !!ok;
    } catch (_e) {
      return false;
    }
  }
}

function _normalizePathSlashes(path) {
  return String(path || '').replace(/\\/g, '/');
}

function _stripTrailingSlashes(path) {
  return String(path || '').replace(/\/+$/, '');
}

function _normalizeRelPath(path) {
  return _normalizePathSlashes(path).replace(/^\/+/, '');
}

function _relFromAbs(abs, root) {
  const absNorm = _stripTrailingSlashes(_normalizePathSlashes(abs));
  const rootNorm = _stripTrailingSlashes(_normalizePathSlashes(root));
  if (!absNorm || !rootNorm) return '';
  if (absNorm === rootNorm) return '';
  if (absNorm.startsWith(rootNorm + '/')) return absNorm.slice(rootNorm.length + 1);
  return '';
}

function relPathForAbs(abs) {
  const meta = getMetaByAbs(abs);
  if (meta && meta.rel_path) return String(meta.rel_path);
  return _relFromAbs(abs, _currentRootPath());
}

function absPathForRel(rel) {
  const root = _currentRootPath();
  const relNorm = _normalizeRelPath(rel);
  if (!root) return relNorm;
  const rootNorm = _stripTrailingSlashes(_normalizePathSlashes(root));
  if (!relNorm) return rootNorm;
  let joined = rootNorm + '/' + relNorm.replace(/^\/+/, '');
  if (root.indexOf('\\') >= 0) joined = joined.replace(/\//g, '\\');
  return joined;
}

async function openPathsInEditor(paths) {
  const unique = Array.from(new Set((paths || []).filter(p => typeof p === 'string' && p.trim()).map(p => p.trim())));
  if (!unique.length) return;
  try {
    const res = await fetch('/api/open', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ paths: unique }) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error((data && data.error) ? data.error : ('HTTP ' + res.status));
    const opened = Array.isArray(data.opened) ? data.opened.length : 0;
    const errors = Array.isArray(data.errors) ? data.errors.length : 0;
    if (errors) showBanner(`Opened ${opened}/${unique.length} file(s) (${errors} error(s))`, 'warn');
    else showBanner(`Opened ${opened} file(s)`, '');
    setTimeout(hideBanner, 2000);
  } catch (err) {
    try { showBanner('Open failed: ' + (err && err.message ? err.message : err), 'warn'); } catch (_) { }
  }
}

function openOutputFolderFromUi() {
  const folder = (typeof window.__LAST_OUTPUT_FOLDER === 'string') ? window.__LAST_OUTPUT_FOLDER : '';
  if (!folder) return;
  openPathsInEditor([folder]);
}

function updateOutputFolderButtons() {
  const hasFolder = !!(window.__LAST_OUTPUT_FOLDER && typeof window.__LAST_OUTPUT_FOLDER === 'string');
  const btnOpen = document.getElementById('btn_open_output_folder'); if (btnOpen) btnOpen.style.display = hasFolder ? 'inline-block' : 'none';
  const btnCopy = document.getElementById('btn_copy_chunks'); if (btnCopy) btnCopy.style.display = hasFolder ? 'inline-block' : 'none';
  const btnDownload = document.getElementById('btn_download_folder'); if (btnDownload) btnDownload.style.display = hasFolder ? 'inline-block' : 'none';
}

async function copyAllOutputChunks() {
  const folder = (typeof window.__LAST_OUTPUT_FOLDER === 'string') ? window.__LAST_OUTPUT_FOLDER : '';
  if (!folder) return;
  try {
    const res = await fetch('/api/output/concat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ folder }) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    const text = (data && typeof data.text === 'string') ? data.text : '';
    const ok = await _copyTextToClipboard(text);
    const count = (data && typeof data.count === 'number') ? data.count : 0;
    if (ok) showBanner(`Copied ${count} chunk(s) to clipboard.`, 'ok'); else showBanner('Copy failed.', 'warn');
    setTimeout(hideBanner, 2000);
  } catch (err) {
    showError('Copy Chunks Failed', err && err.message ? err.message : err);
  }
}

async function downloadOutputFolder(opts) {
  opts = opts || {};
  const folder = (typeof window.__LAST_OUTPUT_FOLDER === 'string') ? window.__LAST_OUTPUT_FOLDER : '';
  if (!folder) return;
  try {
    const res = await fetch('/api/output/zip', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ folder, overwrite: !!opts.overwrite }) });
    const data = await res.json().catch(() => ({}));
    if (res.status === 409 && data && data.exists) {
      if (!confirm('Zip already exists. Overwrite?')) return;
      return downloadOutputFolder({ overwrite: true });
    }
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    if (data && data.download_url) {
      const a = document.getElementById('download_link');
      if (a) { a.href = data.download_url; a.style.display = 'inline'; }
      location.href = data.download_url;
    }
  } catch (err) {
    showError('Download Folder Failed', err && err.message ? err.message : err);
  }
}

async function selectPathsFromParent(pathOrDirAbs, on = true) {
  const raw = (pathOrDirAbs && typeof pathOrDirAbs === 'string') ? pathOrDirAbs.trim() : '';
  if (!raw) return;
  try {
    const res = await fetch('/api/files/from-parent', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: raw }) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error((data && data.error) ? data.error : ('HTTP ' + res.status));
    const paths = Array.isArray(data.paths) ? data.paths.filter(p => typeof p === 'string' && p.trim()).map(p => p.trim()) : [];
    if (!window.__SEL) window.__SEL = new Set();
    // Only select items that are currently visible in the UI.
    const visLookup = window.__SCAN_MAP ? window.__SCAN_MAP : null;
    const filtered = visLookup ? paths.filter(p => !!visLookup[p]) : paths;
    filtered.forEach(p => { if (on) window.__SEL.add(p); else window.__SEL.delete(p); });
    syncSelectionRows();
    try {
      showBanner(`${on ? 'Selected' : 'Deselected'} ${filtered.length} file(s)`, '');
      setTimeout(hideBanner, 1500);
    } catch (_) { }
  } catch (err) {
    try { showBanner('Folder select failed: ' + (err && err.message ? err.message : err), 'warn'); } catch (_) { }
  }
}

async function selectParentFolderForAbs(abs, on = true) {
  return selectPathsFromParent(abs, on);
}

async function selectFolder(folderRel, on = true) {
  const root = (window.__SCAN && window.__SCAN.root) || document.getElementById('last_directory').value || '';
  if (!root) return;
  const base = root.replace(/[/\\\\]+$/, '');
  const rel = String(folderRel || '').replace(/^[/\\\\]+/, '');
  const dirAbs = rel ? (base + '/' + rel) : base;
  return selectPathsFromParent(dirAbs, on);
}

function hideWhyModal() {
  const modal = document.getElementById('why_modal');
  if (modal) modal.style.display = 'none';
  window.__WHY_TARGET_ABS = null;
  window.__WHY_TARGET_REL = null;
  window.__WHY_LAST = null;
}

function _whyDecisionClass(text) {
  const s = String(text || '').toLowerCase();
  if (!s) return 'decision-neutral';
  if (s.includes('included') || s.includes('kept') || s.includes('manual')) return 'decision-positive';
  if (
    s.includes('exclude') ||
    s.includes('disabled') ||
    s.includes('outside') ||
    s.includes('did not match') ||
    s.includes('invalid') ||
    s.includes('before') ||
    s.includes('after') ||
    s.includes('vendored') ||
    s.includes('not changed') ||
    s.includes('duplicate')
  ) return 'decision-negative';
  return 'decision-neutral';
}

function _fmtKB(bytes) {
  const n = parseFloat(String(bytes || 0));
  if (!isFinite(n) || n < 0) return '-';
  return (n / 1024).toFixed(1) + ' KB';
}

function _fmtMtime(tsSeconds) {
  const n = parseFloat(String(tsSeconds || 0));
  if (!isFinite(n) || n <= 0) return '-';
  try { return new Date(n * 1000).toLocaleString(); } catch (_) { return String(n); }
}

async function showWhyFileModal(absPath) {
  const modal = document.getElementById('why_modal');
  if (!modal) return;
  const abs = (absPath && typeof absPath === 'string') ? absPath.trim() : '';
  if (!abs) { showError('File Diagnostics', 'Missing file path'); return; }

  const root = (window.__SCAN && window.__SCAN.root) || document.getElementById('last_directory').value || '';
  if (!root) { showError('File Diagnostics', 'Scan a directory first.'); return; }

  // Clear previous content
  try {
    const reasonsEl = document.getElementById('why_reasons'); if (reasonsEl) reasonsEl.innerHTML = '';
    const statusEl = document.getElementById('why_status'); if (statusEl) statusEl.textContent = 'Loading…';
    const nameEl = document.getElementById('why_file_name'); if (nameEl) nameEl.textContent = '';
    const pathEl = document.getElementById('why_file_path'); if (pathEl) pathEl.textContent = abs;
    const ids = ['why_meta_size', 'why_meta_tokens', 'why_meta_readable', 'why_meta_category', 'why_meta_mtime'];
    ids.forEach(id => { const el = document.getElementById(id); if (el) el.textContent = ''; });
  } catch (_) { }

  try { showOverlay('Loading diagnostics…'); } catch (_) { }

  try {
    const visibilityMode = getVisibilityMode();
    const ignorePersisted = !!(document.getElementById('f_ignore_persisted') && document.getElementById('f_ignore_persisted').checked);
    const ignoreCustom = (visibilityMode === 'all') ? true : ignorePersisted;
    const payload = {
      path: root,
      file_path: abs,
      filters: getFilters(),
      visibility_mode: visibilityMode,
      ignore_custom_excluded: ignoreCustom,
      // Include scan toggles so /api/why can mirror git/vendored logic.
      use_git_files: !!(document.getElementById('f_use_git') && document.getElementById('f_use_git').checked),
      git_include_untracked: !!(document.getElementById('f_git_untracked') && document.getElementById('f_git_untracked').checked),
      git_only_changed: !!(document.getElementById('f_git_changed') && document.getElementById('f_git_changed').checked),
      git_since_ref: (document.getElementById('f_git_since') && document.getElementById('f_git_since').value) || '',
      git_until_ref: (document.getElementById('f_git_until') && document.getElementById('f_git_until').value) || 'HEAD',
      exclude_vendored: !!(document.getElementById('f_ex_vendored') && document.getElementById('f_ex_vendored').checked),
      exclude_ios_noise: !!(document.getElementById('f_ex_ios') && document.getElementById('f_ex_ios').checked),
      xcode_target: (document.getElementById('f_xcode_target') && document.getElementById('f_xcode_target').value) || '(All)',
    };

    const res = await fetch('/api/why', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error((data && data.error) ? data.error : ('HTTP ' + res.status));

    window.__WHY_LAST = data;
    const meta = (data && typeof data.meta === 'object' && data.meta) ? data.meta : {};
    const absPathResolved = (meta.abs_path && typeof meta.abs_path === 'string') ? meta.abs_path : abs;
    const relPath = (meta.rel_path && typeof meta.rel_path === 'string') ? meta.rel_path : '';
    window.__WHY_TARGET_ABS = absPathResolved;
    window.__WHY_TARGET_REL = relPath;

    const fileName = relPath ? relPath.split('/').pop() : absPathResolved.split('/').pop();
    const nameEl = document.getElementById('why_file_name'); if (nameEl) nameEl.textContent = fileName || '(unknown)';
    const pathEl = document.getElementById('why_file_path'); if (pathEl) pathEl.textContent = absPathResolved;

    const kept = !!data.kept;
    const statusEl = document.getElementById('why_status');
    if (statusEl) statusEl.textContent = kept ? 'Included by current filters.' : 'Hidden by current filters.';

    const sizeEl = document.getElementById('why_meta_size'); if (sizeEl) sizeEl.textContent = _fmtKB(meta.size_bytes);
    const tokEl = document.getElementById('why_meta_tokens'); if (tokEl) tokEl.textContent = (meta.token_count != null) ? String(meta.token_count) : '-';
    const readEl = document.getElementById('why_meta_readable');
    if (readEl) readEl.textContent = meta.read_error ? 'unavailable' : 'ok';
    const catEl = document.getElementById('why_meta_category'); if (catEl) catEl.textContent = (meta.category != null) ? String(meta.category) : '-';
    const mtEl = document.getElementById('why_meta_mtime'); if (mtEl) mtEl.textContent = _fmtMtime(meta.mtime);

    const reasons = Array.isArray(data.reasons) ? data.reasons.filter(r => typeof r === 'string' && r.trim()).map(r => r.trim()) : [];
    const reasonsEl = document.getElementById('why_reasons');
    if (reasonsEl) {
      if (!reasons.length) {
        const row = document.createElement('div');
        row.className = 'decision-row decision-neutral';
        row.textContent = kept ? 'No specific filters matched; file is currently included.' : 'No specific filters matched.';
        reasonsEl.appendChild(row);
      } else {
        reasons.forEach(r => {
          const row = document.createElement('div');
          row.className = 'decision-row ' + _whyDecisionClass(r);
          row.textContent = r;
          reasonsEl.appendChild(row);
        });
      }
    }

    const btnExclude = document.getElementById('why_action_exclude');
    const btnReset = document.getElementById('why_action_reset');
    if (btnExclude) btnExclude.style.display = kept ? 'inline-block' : 'none';
    if (btnReset) btnReset.style.display = kept ? 'none' : 'inline-block';
    const btnEnable = document.getElementById('why_action_enable_category');
    if (btnEnable) {
      const cat = meta.category ? String(meta.category) : '';
      let enabled = false;
      if (cat) {
        document.querySelectorAll('.f_cat').forEach(cb => {
          if (String(cb.value).toUpperCase() === cat.toUpperCase() && cb.checked) enabled = true;
        });
      }
      btnEnable.style.display = cat ? 'inline-block' : 'none';
      btnEnable.disabled = enabled;
    }
    const btnAddAlways = document.getElementById('why_action_add_always');
    if (btnAddAlways) btnAddAlways.style.display = 'inline-block';

    modal.style.display = 'flex';
  } catch (err) {
    showError('File Diagnostics Failed', err && err.message ? err.message : err);
  } finally {
    try { hideOverlay(); } catch (_) { }
  }
}

function whyOpenFromModal() {
  const abs = (window.__WHY_TARGET_ABS && typeof window.__WHY_TARGET_ABS === 'string') ? window.__WHY_TARGET_ABS : null;
  if (!abs) return;
  openPathsInEditor([abs]);
}

async function whyExcludeFromModal() {
  const abs = (window.__WHY_TARGET_ABS && typeof window.__WHY_TARGET_ABS === 'string') ? window.__WHY_TARGET_ABS : null;
  if (!abs) return;
  try {
    await excludePathsPersist([abs]);
    hideWhyModal();
  } catch (_) { }
}

function _resetFiltersForWhy() {
  const setVal = (id, v) => { const el = document.getElementById(id); if (el) el.value = String(v); };
  const setChecked = (id, v) => { const el = document.getElementById(id); if (el) el.checked = !!v; };
  setVal('f_name_pattern', '');
  setChecked('f_name_glob', false);
  setVal('f_token_min', 0);
  setVal('f_token_max', 10000000);
  setVal('f_size_min', 0);
  setVal('f_size_max', 1000000);
  clearDateFilters(false);
  setVal('f_last_n_days', 0);
  setVal('f_content', '');
  setChecked('f_content_regex', false);
  setChecked('f_content_case', false);
  // Enable all categories if present
  try { document.querySelectorAll('.f_cat').forEach(cb => { cb.checked = true; }); } catch (_) { }
}

async function whyResetFiltersFromModal() {
  try {
    _resetFiltersForWhy();
    try { schedulePersistFilters(0); } catch (_) { }
    hideWhyModal();
    await doFilterApply();
  } catch (err) {
    showError('Reset Filters Failed', err && err.message ? err.message : err);
  }
}

function whyEnableCategoryFromModal() {
  try {
    const meta = window.__WHY_LAST && window.__WHY_LAST.meta ? window.__WHY_LAST.meta : {};
    const cat = meta.category ? String(meta.category).trim() : '';
    if (!cat) { showBanner('No category available for this file.', 'warn'); return; }
    let found = false;
    document.querySelectorAll('.f_cat').forEach(cb => {
      if (String(cb.value).toUpperCase() === cat.toUpperCase()) { cb.checked = true; found = true; }
    });
    if (!found) { showBanner(`Category not found: ${cat}`, 'warn'); return; }
    schedulePersistFilters(0);
    hideWhyModal();
    requestFilterApply(50);
  } catch (err) {
    showError('Enable Category Failed', err && err.message ? err.message : err);
  }
}

function whyClearPatternFromModal() {
  try {
    const el = document.getElementById('f_name_pattern');
    if (el) el.value = '';
    const glob = document.getElementById('f_name_glob');
    if (glob) glob.checked = false;
    scheduleFilterScan(50);
    hideWhyModal();
  } catch (err) {
    showError('Clear Pattern Failed', err && err.message ? err.message : err);
  }
}

function whyResetRangesFromModal() {
  try {
    const setVal = (id, v) => { const el = document.getElementById(id); if (el) el.value = String(v); };
    setVal('f_token_min', 0);
    setVal('f_token_max', 10000000);
    setVal('f_size_min', 0);
    setVal('f_size_max', 1000000);
    scheduleFilterScan(50);
    hideWhyModal();
  } catch (err) {
    showError('Reset Ranges Failed', err && err.message ? err.message : err);
  }
}

function whyResetDatesFromModal() {
  try {
    clearDateFilters(true);
    hideWhyModal();
  } catch (err) {
    showError('Reset Dates Failed', err && err.message ? err.message : err);
  }
}

function whyClearDatesFromModal() {
  try {
    clearDateFilters(true);
    hideWhyModal();
  } catch (err) {
    showError('Clear Dates Failed', err && err.message ? err.message : err);
  }
}

async function whyAddAlwaysIncludeFromModal() {
  try {
    const rel = (window.__WHY_TARGET_REL && typeof window.__WHY_TARGET_REL === 'string') ? window.__WHY_TARGET_REL : '';
    const abs = (window.__WHY_TARGET_ABS && typeof window.__WHY_TARGET_ABS === 'string') ? window.__WHY_TARGET_ABS : '';
    const base = rel ? rel.split(/[\\/]/).pop() : abs.split(/[\\/]/).pop();
    if (!base) { showBanner('Unable to identify file name.', 'warn'); return; }
    const stem = base.replace(/\.[^.]+$/, '').toLowerCase();
    if (!stem) { showBanner('Invalid file name.', 'warn'); return; }
    const res = await fetch(_settingsUrl());
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    const list = Array.isArray(data.always_include_docs_list) ? data.always_include_docs_list.slice() : [];
    if (!list.includes(stem)) list.push(stem);
    const resSave = await fetch('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(_settingsPayload({ always_include_docs_list: list })) });
    const saved = await resSave.json().catch(() => ({}));
    if (!resSave.ok) throw new Error(saved.error || ('HTTP ' + resSave.status));
    const ta = document.getElementById('s_always_docs'); if (ta) ta.value = list.join('\n');
    showBanner(`Added "${stem}" to Always-Include list.`, '');
    setTimeout(hideBanner, 2000);
    requestFilterApply(100);
    hideWhyModal();
  } catch (err) {
    showError('Always-Include Failed', err && err.message ? err.message : err);
  }
}

function showContextMenu(x, y, tr) {
  const menu = document.getElementById('ctxmenu');
  if (!menu) return;
  const abs = tr.getAttribute('data-abs') || '';
  const folder = tr.getAttribute('data-folder') || '';
  const folderAbs = tr.getAttribute('data-folder-abs') || '';
  const folderMode = tr.getAttribute('data-folder-mode') || '';
  const items = [];

  if (folder) {
    items.push({ id: 'selFolder', label: 'Select folder', action: () => selectFolder(folder, true) });
    items.push({ id: 'deselFolder', label: 'Deselect folder', action: () => selectFolder(folder, false) });
    if (folderAbs) {
      const modeUp = String(folderMode || '').toUpperCase();
      const hasSummary = modeUp.startsWith('FOLDER_SUMMARY');
      const isFull = modeUp === 'FOLDER_SUMMARY_FULL';
      items.push({ id: 'folderSummaryFull', label: isFull ? 'Folder Summary Full (on)' : 'Folder Summary Full', action: () => setFolderSummaryMode(folderAbs, 'FOLDER_SUMMARY_FULL') });
      if (hasSummary) {
        items.push({ id: 'folderSummaryClear', label: 'Clear Folder Summary', action: () => toggleFolderSummary(folderAbs, true) });
      }
      items.push({ id: 'folderSummaryPreviewFull', label: 'Preview Folder Summary (full)', action: () => previewFolderSummary(folderAbs, 'full') });
    }
  } else if (abs) {
    const selected = (window.__SEL && window.__SEL.has(abs));
    const targets = _modeTargets(abs);
    const n = targets.length || 1;
    const suffix = n > 1 ? ` (${n})` : '';
    const meta = getMetaByAbs(abs);

    items.push({ id: 'toggle', label: selected ? 'Deselect' : 'Select', action: () => toggleSelectAbs(abs, tr) });
    items.push({
      id: 'copyAbs', label: 'Copy absolute path', action: async () => {
        const text = targets.join("\n");
        const ok = await copyTextToClipboard(text);
        try { showBanner(ok ? 'Path(s) copied' : 'Copy failed', ok ? '' : 'warn'); setTimeout(hideBanner, 1200); } catch (_) { }
      }
    });
    items.push({
      id: 'copyRel', label: 'Copy relative path', action: async () => {
        const rels = targets.map(p => relPathForAbs(p));
        const ok = await copyTextToClipboard(rels.join("\n"));
        try { showBanner(ok ? 'Path(s) copied' : 'Copy failed', ok ? '' : 'warn'); setTimeout(hideBanner, 1200); } catch (_) { }
      }
    });
    items.push({ id: 'open', label: 'Open in editor' + suffix, action: () => openPathsInEditor(targets) });
    items.push({ id: 'preview', label: 'Preview file' + suffix, action: () => previewFile(abs) });

    items.push({ id: 'why', label: 'Why this file?', action: () => showWhyFileModal(abs) });

    const wantsSummary = (meta && modeRequiresSummary(meta.mode)) || hasSummaryError(abs);
    if (wantsSummary) {
      items.push({ id: 'summary_refresh', label: 'Refresh summary now' + suffix, action: () => refreshSummariesForPaths(targets) });
    }
    if (meta && meta.rel_path && String(meta.rel_path).includes('/')) {
      items.push({ id: 'selParent', label: 'Select parent folder', action: () => selectParentFolderForAbs(abs, true) });
      items.push({ id: 'deselParent', label: 'Deselect parent folder', action: () => selectParentFolderForAbs(abs, false) });
    }

    items.push({ id: 'modeCycle', label: 'Cycle mode' + suffix, action: () => cycleModeForAbs(abs) });
    items.push({ id: 'modeCode', label: 'Set mode: Code' + suffix, action: () => setModeForAbs(abs, 'CODE') });
    items.push({ id: 'modeSkel', label: 'Set mode: Skeleton' + suffix, action: () => setModeForAbs(abs, 'SKELETON') });
    items.push({ id: 'modeApiSurface', label: 'Set mode: API surface' + suffix, action: () => setModeForAbs(abs, 'API_SURFACE') });
    items.push({ id: 'modeSignatures', label: 'Set mode: Signatures' + suffix, action: () => setModeForAbs(abs, 'SIGNATURES') });
    items.push({ id: 'modeCskel', label: 'Set mode: Code + skeleton' + suffix, action: () => setModeForAbs(abs, 'CODE_PLUS_SKELETON') });
    items.push({ id: 'modeSs', label: 'Set mode: Summary (short)' + suffix, action: () => setModeForAbs(abs, 'SUMMARY_SHORT') });
    items.push({ id: 'modeSl', label: 'Set mode: Summary (long)' + suffix, action: () => setModeForAbs(abs, 'SUMMARY_LONG') });
    items.push({ id: 'modeSskel', label: 'Set mode: Short summary + skeleton' + suffix, action: () => setModeForAbs(abs, 'SUMMARY_SHORT_PLUS_SKELETON') });
    items.push({ id: 'modeLskel', label: 'Set mode: Long summary + skeleton' + suffix, action: () => setModeForAbs(abs, 'SUMMARY_LONG_PLUS_SKELETON') });
    items.push({ id: 'modeCpskel', label: 'Set mode: Code + short summary + skeleton' + suffix, action: () => setModeForAbs(abs, 'CODE_PLUS_SUMMARY_SHORT_PLUS_SKELETON') });
    items.push({ id: 'modeCplskel', label: 'Set mode: Code + long summary + skeleton' + suffix, action: () => setModeForAbs(abs, 'CODE_PLUS_SUMMARY_LONG_PLUS_SKELETON') });
    items.push({ id: 'modeCps', label: 'Set mode: Code + short' + suffix, action: () => setModeForAbs(abs, 'CODE_PLUS_SHORT') });
    items.push({ id: 'modeCpl', label: 'Set mode: Code + long' + suffix, action: () => setModeForAbs(abs, 'CODE_PLUS_LONG') });
    items.push({ id: 'modeEx', label: 'Set mode: Exclude' + suffix, action: () => setModeForAbs(abs, 'EXCLUDE') });
    items.push({ id: 'modeClr', label: 'Clear custom mode' + suffix, action: () => setModeForAbs(abs, 'CLEAR') });

    items.push({ id: 'hide', label: 'Hide' + suffix, action: () => hidePaths(targets) });
    items.push({ id: 'exclude', label: 'Persist exclude' + suffix, action: () => excludePathsPersist(targets) });
    items.push({ id: 'unexclude', label: 'Unexclude (persist)' + suffix, action: () => unexcludePathsPersist(targets) });
    items.push({ id: 'toggle_ex', label: 'Toggle exclude' + suffix, action: () => toggleExcludePathsPersist(targets) });
  }

  // Render menu
  menu.innerHTML = '';
  items.forEach(it => {
    const btn = document.createElement('button'); btn.type = 'button'; btn.setAttribute('role', 'menuitem'); btn.textContent = it.label; btn.addEventListener('click', () => { hideContextMenu(); try { it.action(); } catch (_) { } });
    menu.appendChild(btn);
  });
  menu.style.left = x + 'px';
  menu.style.top = y + 'px';
  menu.style.display = 'block';
  menu.setAttribute('aria-hidden', 'false');
}

function hideContextMenu() {
  const menu = document.getElementById('ctxmenu');
  if (menu) { menu.style.display = 'none'; menu.setAttribute('aria-hidden', 'true'); }
}

function getMetaByAbs(abs) {
  try {
    return (window.__SCAN && window.__SCAN_MAP && window.__SCAN_MAP[abs]) || null;
  } catch (_) { return null; }
}

function _isExcludeMode(meta) {
  const mode = (meta && meta.mode) ? String(meta.mode).toUpperCase() : '';
  return mode === 'EXCLUDE';
}

function filterNonExcludedPaths(paths) {
  const unique = Array.from(new Set((paths || []).filter(p => typeof p === 'string' && p.trim()).map(p => p.trim())));
  if (!unique.length) return [];
  return unique.filter(p => {
    const meta = getMetaByAbs(p);
    return !_isExcludeMode(meta);
  });
}

function _modeTargets(anchorAbs) {
  const sel = window.__SEL || new Set();
  if (sel && sel.size && sel.has(anchorAbs)) return Array.from(sel);
  return [anchorAbs];
}

function _applyLocalMode(paths, mode) {
  const eff = (mode && mode !== 'CLEAR') ? mode : (window.__DEFAULT_MODE || 'CODE');
  const set = new Set(paths || []);
  try { (window.__SCAN && window.__SCAN.files || []).forEach(f => { if (set.has(f.abs_path)) f.mode = eff; }); } catch (_) { }
  try { (window.__VISIBLE || []).forEach(f => { if (set.has(f.abs_path)) f.mode = eff; }); } catch (_) { }
  try { if (window.__SCAN_MAP) paths.forEach(p => { if (window.__SCAN_MAP[p]) window.__SCAN_MAP[p].mode = eff; }); } catch (_) { }
}

async function setModeForPaths(paths, mode) {
  const root = (window.__SCAN && window.__SCAN.root) || document.getElementById('last_directory').value;
  const unique = Array.from(new Set((paths || []).filter(p => typeof p === 'string' && p.trim()).map(p => p.trim())));
  if (!unique.length) return;
  try {
    const res = await fetch('/api/modes', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: root, selected_paths: unique, mode }) });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    if (data && data.default_mode) window.__DEFAULT_MODE = data.default_mode;
    _applyLocalMode(unique, mode);
    renderTable();
  } catch (err) {
    try { showBanner('Mode update failed: ' + err, 'warn'); } catch (_) { }
  }
}

async function resetPerFileModes() {
  if (!confirm('Reset all per-file mode overrides?')) return;
  try {
    try { showOverlay('Resetting per-file modes…'); } catch (_) { }
    const res = await fetch('/api/modes/reset', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    try { requestFilterApply(50); } catch (_) { }
    try { showBanner('Per-file mode overrides reset.', ''); setTimeout(hideBanner, 2000); } catch (_) { }
  } catch (err) {
    showError('Reset Modes Failed', err && err.message ? err.message : err);
  } finally {
    try { hideOverlay(); } catch (_) { }
  }
}

async function setModeForAbs(anchorAbs, mode) {
  const targets = _modeTargets(anchorAbs);
  return setModeForPaths(targets, mode);
}

function cycleModeForAbs(anchorAbs) {
  const order = ['CODE', 'SKELETON', 'API_SURFACE', 'SIGNATURES', 'CODE_PLUS_SKELETON', 'SUMMARY_SHORT', 'SUMMARY_LONG', 'SUMMARY_SHORT_PLUS_SKELETON', 'SUMMARY_LONG_PLUS_SKELETON', 'CODE_PLUS_SUMMARY_SHORT_PLUS_SKELETON', 'CODE_PLUS_SUMMARY_LONG_PLUS_SKELETON', 'CODE_PLUS_SHORT', 'CODE_PLUS_LONG', 'EXCLUDE'];
  const meta = getMetaByAbs(anchorAbs);
  const cur = (meta && meta.mode) ? ('' + meta.mode).toUpperCase() : ('' + (window.__DEFAULT_MODE || 'CODE')).toUpperCase();
  const idx = order.indexOf(cur);
  const next = order[(idx >= 0 ? (idx + 1) : 0) % order.length];
  setModeForAbs(anchorAbs, next);
}

function bindHeaderSorting(enable = true) {
  const thead = document.querySelector("#files_tbl thead");
  // If switching modes, allow rebinding once
  if (!enable) return;
  if (!thead.__bound) {
    thead.addEventListener("click", (e) => {
      const th = e.target.closest("th[data-col]");
      if (!th) return;
      const col = th.getAttribute("data-col");
      window.__HEADER_SORT_ACTIVE = true;
      sortBy(col);
      updateHeaderSortIndicators();
    });
    thead.__bound = true;
  }
  updateHeaderSortIndicators();
}

function updateHeaderSortIndicators() {
  const thead = document.querySelector('#files_tbl thead'); if (!thead) return;
  if (!window.__HEADER_SORT_ACTIVE) {
    thead.querySelectorAll('th[data-col]').forEach(th => th.classList.remove('sorted-asc', 'sorted-desc'));
    return;
  }
  const state = window.__SORT || { col: 'rel_path', desc: false };
  thead.querySelectorAll('th[data-col]').forEach(th => {
    th.classList.remove('sorted-asc', 'sorted-desc');
    const col = th.getAttribute('data-col');
    if (col === state.col) th.classList.add(state.desc ? 'sorted-desc' : 'sorted-asc');
  });
}

// expandAll defined below (single definition)

function _sortDataByState(data, state) {
  if (!Array.isArray(data) || !state) return;
  const col = state.col || 'rel_path';
  const desc = !!state.desc;
  const totals = {};
  if (col === 'tok_total' || col === 'kb_total') {
    data.forEach(f => {
      const folder = (f.rel_path && f.rel_path.includes('/')) ? f.rel_path.split('/').slice(0, -1).join('/') : '';
      const t = f.token_count || 0;
      const kb = f.size_bytes / 1024;
      const cur = totals[folder] || { tok: 0, kb: 0 };
      totals[folder] = { tok: cur.tok + t, kb: cur.kb + kb };
    });
  }
  const cmp = (a, b) => {
    let va = a[col];
    let vb = b[col];
    if (col === "tok_total" || col === "kb_total") {
      const fa = (a.rel_path && a.rel_path.includes('/')) ? a.rel_path.split('/').slice(0, -1).join('/') : '';
      const fb = (b.rel_path && b.rel_path.includes('/')) ? b.rel_path.split('/').slice(0, -1).join('/') : '';
      va = (col === 'tok_total') ? ((totals[fa] ? totals[fa].tok : 0) || 0) : ((totals[fa] ? totals[fa].kb : 0) || 0);
      vb = (col === 'tok_total') ? ((totals[fb] ? totals[fb].tok : 0) || 0) : ((totals[fb] ? totals[fb].kb : 0) || 0);
    } else if (col === "dens") {
      const ka = (a.size_bytes || 0) / 1024.0; const kbv = (b.size_bytes || 0) / 1024.0;
      va = ka > 0 ? ((a.token_count || 0) / ka) : 0;
      vb = kbv > 0 ? ((b.token_count || 0) / kbv) : 0;
    } else if (col === "size_bytes" || col === "token_count") { va = +va || 0; vb = +vb || 0; }
    else { va = ("" + va).toLowerCase(); vb = ("" + vb).toLowerCase(); }
    if (va < vb) return desc ? 1 : -1;
    if (va > vb) return desc ? -1 : 1;
    return 0;
  };
  data.sort(cmp);
}

function applyActiveHeaderSortToData(data) {
  if (!window.__HEADER_SORT_ACTIVE) return;
  const viewEl = document.getElementById('view_mode');
  const view = viewEl ? String(viewEl.value || '') : '';
  if (view && view !== 'flat') return;
  const state = window.__SORT || { col: 'rel_path', desc: false };
  _sortDataByState(data, state);
}

function sortBy(col) {
  const data = (window.__VISIBLE || (window.__SCAN && window.__SCAN.files) || []);
  const state = window.__SORT || { col: "rel_path", desc: false };
  window.__HEADER_SORT_ACTIVE = true;
  if (state.col === col) state.desc = !state.desc; else { state.col = col; state.desc = false; }
  window.__SORT = state;
  _sortDataByState(data, state);
  renderTable();
}

function getFilters() {
  const cfg = window.__CURRENT_CONFIG || {};
  const lf = (cfg.last_filters && typeof cfg.last_filters === 'object') ? cfg.last_filters : {};

  const val = (id, fallback) => {
    const el = document.getElementById(id);
    if (!el) return (fallback !== undefined && fallback !== null) ? String(fallback) : '';
    return (el.value !== undefined && el.value !== null) ? String(el.value) : '';
  };
  const checked = (id, fallback) => {
    const el = document.getElementById(id);
    if (!el) return !!fallback;
    return !!el.checked;
  };
  const asInt = (raw, fallback) => {
    const n = parseInt(String(raw || ''), 10);
    return isFinite(n) ? n : fallback;
  };
  const asFloat = (raw, fallback) => {
    const n = parseFloat(String(raw || ''));
    return isFinite(n) ? n : fallback;
  };

  const catEls = Array.from(document.querySelectorAll('.f_cat'));
  let cats = catEls.filter(c => c.checked).map(c => c.value);
  if (catEls.length === 0) {
    if (Array.isArray(lf.categories) && lf.categories.length) {
      cats = lf.categories;
    } else if (cfg.category_mask && typeof cfg.category_mask === 'object') {
      try { cats = Object.keys(cfg.category_mask).filter(k => !!cfg.category_mask[k]); } catch (_) { }
    }
  }

  const afterEl = document.getElementById("f_date_after");
  const beforeEl = document.getElementById("f_date_before");
  const afterManual = _getDateManualFlag(afterEl, DATE_AFTER_DEFAULT);
  const beforeManual = _getDateManualFlag(beforeEl, DATE_BEFORE_DEFAULT);
  let dateAfter = afterEl ? (afterManual ? _ymdToEpochSecondsUTC(afterEl.value) : 0) : asFloat(lf.date_after, 0);
  let dateBefore = beforeEl ? (beforeManual ? _ymdToEpochSecondsUTC(beforeEl.value) : 0) : asFloat(lf.date_before, 0);
  const lastDays = asInt(val('f_last_n_days', cfg.date_last_n_days || 0), 0);
  if (lastDays > 0) {
    const end = new Date();
    const start = new Date(end.getTime() - (lastDays * 24 * 60 * 60 * 1000));
    dateAfter = Math.floor(start.getTime() / 1000);
    dateBefore = 0;
  }

  return {
    name_pattern: val("f_name_pattern", lf.name_pattern || ""),
    name_filter_glob: checked("f_name_glob", cfg.name_filter_glob),
    token_min: asInt(val("f_token_min", lf.token_min || 0), 0),
    token_max: asInt(val("f_token_max", lf.token_max || 10000000), 10000000),
    size_min: asFloat(val("f_size_min", lf.size_min || 0), 0),
    size_max: asFloat(val("f_size_max", lf.size_max || 1000000), 1000000),
    date_after: dateAfter,
    date_before: dateBefore,
    categories: cats,
    content_search: val("f_content", cfg.content_search || ""),
    content_regex: checked("f_content_regex", cfg.content_regex),
    content_case_sensitive: checked("f_content_case", cfg.content_case_sensitive),
    content_max_kb: asInt(val("f_content_kb", (cfg.content_max_kb !== undefined && cfg.content_max_kb !== null) ? cfg.content_max_kb : 256), 256),
    duplicate_hash_max_kb: asInt(val("f_dup_kb", (cfg.duplicate_hash_max_kb !== undefined && cfg.duplicate_hash_max_kb !== null) ? cfg.duplicate_hash_max_kb : 256), 256),
    content_hide_duplicates: checked("f_hide_dups", cfg.content_hide_duplicates),
    content_mark_duplicates: checked("f_mark_dups", (cfg.content_mark_duplicates !== undefined) ? cfg.content_mark_duplicates : true),
    apply_token_filters_to_transforms: checked("f_apply_token_transforms", cfg.apply_token_filters_to_transforms),
    always_include_docs: checked("f_always_docs", (cfg.always_include_docs !== undefined) ? cfg.always_include_docs : true),
    // pass-through scan knobs
    ignore_binary: checked('f_ignore_binary', cfg.ignore_binary_files),
    max_file_size_mb: asFloat(val('f_max_mb', (cfg.max_file_size_mb !== undefined && cfg.max_file_size_mb !== null) ? cfg.max_file_size_mb : 0), 0),
    max_walk_depth: asInt(val('f_max_depth', (cfg.max_walk_depth !== undefined && cfg.max_walk_depth !== null) ? cfg.max_walk_depth : 0), 0),
    follow_symlinks: checked('f_follow_symlinks', cfg.follow_symlinks),
  };
}

function _uniquePaths(paths) {
  return Array.from(new Set((paths || []).filter(p => typeof p === 'string' && p.trim()).map(p => p.trim())));
}

async function ensureSummariesForPaths(paths, opts) {
  opts = opts || {};
  const unique = _uniquePaths(paths);
  if (!unique.length) return null;
  const root = (window.__SCAN && window.__SCAN.root) || document.getElementById('last_directory').value;
  if (!root) return null;
  if (opts.showOverlay) { try { showOverlay(opts.label || 'Refreshing summaries…'); } catch (_) { } }
  try {
    const payload = await window.providerConfirmedPayload('watch_tick', { path: root, selected_paths: unique, only_summary: true });
    const res = await fetch('/api/watch/tick', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: opts.signal || undefined,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    updateSummaryErrors(data.error_paths, data.requested_paths || unique);
    return data;
  } finally {
    if (opts.showOverlay) { try { hideOverlay(); } catch (_) { } }
  }
}

function _summaryTargetsFromVisible() {
  const visible = (window.__VISIBLE || (window.__SCAN && window.__SCAN.files) || []);
  return visible
    .filter(f => f && f.abs_path && (modeRequiresSummary(f.mode) || hasSummaryError(f.abs_path)))
    .map(f => f.abs_path);
}

function _summaryTargetsFromSelection() {
  const sel = Array.from(window.__SEL || []);
  return sel.filter(p => {
    const meta = getMetaByAbs(p);
    return (meta && modeRequiresSummary(meta.mode)) || hasSummaryError(p);
  });
}

async function refreshSummariesForPaths(paths) {
  const targets = _uniquePaths(paths);
  if (!targets.length) { try { showBanner('No summary-mode files in scope.', 'warn'); } catch (_) { } return; }
  try {
    const data = await ensureSummariesForPaths(targets, { showOverlay: true, label: 'Refreshing summaries…' });
    if (!data) return;
    const m = data.metrics || {};
    const ensured = m.ensured || 0;
    const errors = m.errors || 0;
    if (errors) showBanner(`Summaries refreshed: ${ensured} ensured — errors ${errors}`, 'warn');
    else showBanner(`Summaries refreshed: ${ensured} ensured`, 'ok');
    setTimeout(hideBanner, 2500);
  } catch (err) {
    showBanner('Refresh summaries failed: ' + err, 'warn');
  }
}

function refreshSummariesFromToolbar() {
  const scope = (document.getElementById('summary_scope') || {}).value || 'selected';
  const targets = (scope === 'visible') ? _summaryTargetsFromVisible() : _summaryTargetsFromSelection();
  if (!targets.length && scope !== 'visible') {
    const fallback = _summaryTargetsFromVisible();
    if (!fallback.length) {
      try { showBanner('No summary-mode files to refresh.', 'warn'); } catch (_) { }
      return;
    }
    refreshSummariesForPaths(fallback);
    return;
  }
  refreshSummariesForPaths(targets);
}

// Optional: watch loop using fingerprint (SSE when available, polling fallback)
let __WATCH_TIMER = null, __LAST_FP = null, __WATCH_CTRL = null, __WATCH_SRC = null, __WATCH_RUNNING = false, __WATCH_SESSION = 0;
async function startWatch(intervalSec = 3) {
  stopWatch();
  __WATCH_RUNNING = true;
  __WATCH_SESSION = (__WATCH_SESSION || 0) + 1;
  const session = __WATCH_SESSION;
  __LAST_FP = null;
  __WATCH_CTRL = (window.AbortController ? new AbortController() : null);
  const intervalMs = Math.max(1000, intervalSec * 1000);
  const sampleEl = document.getElementById('s_watch_sample_limit');
  const sampleParsed = sampleEl ? parseInt(String(sampleEl.value || ''), 10) : NaN;
  const sampleLimit = (isFinite(sampleParsed) && sampleParsed > 0)
    ? sampleParsed
    : ((typeof window.__WATCH_SAMPLE_LIMIT === 'number' && window.__WATCH_SAMPLE_LIMIT > 0) ? window.__WATCH_SAMPLE_LIMIT : 3000);

  const startPolling = () => {
    const tick = async () => {
      if (!__WATCH_RUNNING || session !== __WATCH_SESSION) return;
      try {
        const path = document.getElementById('last_directory').value;
        if (!path) return;
        const use_git = document.getElementById('f_use_git').checked;
        const inc_u = document.getElementById('f_git_untracked').checked;
        const signal = (__WATCH_CTRL && __WATCH_CTRL.signal) || undefined;
        const res = await fetch(`/api/watch/fingerprint?path=${encodeURIComponent(path)}&sample_limit=${encodeURIComponent(sampleLimit)}&use_git=${use_git ? 1 : 0}&include_untracked=${inc_u ? 1 : 0}`, { signal });
        const fp = await res.json();
        const key = `${fp.count}:${fp.max_mtime}`;
        if (!__WATCH_RUNNING || session !== __WATCH_SESSION) return;
        if (__LAST_FP && key !== __LAST_FP) {
          window.__WATCH_NEEDS_TICK = true;
          requestScan();
        }
        __LAST_FP = key;
        try { await watchTickVisible(signal, session); } catch (_) { }
      } catch (err) {
        // Ignore abort and transient watch errors.
        try { if (err && err.name === 'AbortError') return; } catch (_) { }
      } finally {
        if (!__WATCH_RUNNING || session !== __WATCH_SESSION) return;
        __WATCH_TIMER = setTimeout(tick, intervalMs);
      }
    };
    tick();
  };

  const trySse = () => {
    try {
      if (!window.EventSource) return false;
    } catch (_) { return false; }
    try {
      const path = document.getElementById('last_directory').value;
      if (!path) return false;
      const use_git = document.getElementById('f_use_git').checked;
      const inc_u = document.getElementById('f_git_untracked').checked;
      const url = `/api/watch/stream?path=${encodeURIComponent(path)}&interval_sec=${encodeURIComponent(intervalSec)}&sample_limit=${encodeURIComponent(sampleLimit)}&use_git=${use_git ? 1 : 0}&include_untracked=${inc_u ? 1 : 0}`;
      const src = new EventSource(url);
      __WATCH_SRC = src;
      src.onmessage = (ev) => {
        if (!__WATCH_RUNNING || session !== __WATCH_SESSION) return;
        try {
          const fp = JSON.parse((ev && ev.data) ? ev.data : '{}');
          const key = `${fp.count}:${fp.max_mtime}`;
          if (__LAST_FP && key !== __LAST_FP) {
            window.__WATCH_NEEDS_TICK = true;
            requestScan();
          }
          __LAST_FP = key;
          try { watchTickVisible(undefined, session); } catch (_) { }
        } catch (_) { }
      };
      src.onerror = () => {
        try { src.close(); } catch (_) { }
        if (!__WATCH_RUNNING || session !== __WATCH_SESSION) return;
        __WATCH_SRC = null;
        startPolling();
      };
      return true;
    } catch (_) {
      return false;
    }
  };

  if (!trySse()) startPolling();
}
function stopWatch() {
  __WATCH_RUNNING = false;
  __WATCH_SESSION = (__WATCH_SESSION || 0) + 1;
  if (__WATCH_TIMER) { clearTimeout(__WATCH_TIMER); __WATCH_TIMER = null; }
  try { if (__WATCH_SRC) __WATCH_SRC.close(); } catch (_) { }
  __WATCH_SRC = null;
  try { if (__WATCH_CTRL) __WATCH_CTRL.abort(); } catch (_) { }
  __WATCH_CTRL = null;
  __LAST_FP = null;
}

// Debounced scan request (used by watch)
function requestScan(throttleMs = 800) {
  if (!_validateNamePattern()) return;
  if (throttleMs === 800 && typeof window.__WATCH_DEBOUNCE_MS === 'number') throttleMs = window.__WATCH_DEBOUNCE_MS;
  const now = Date.now();
  if (window.__SCAN_RUNNING) { window.__RESCAN_REQ = true; return; }
  if (window.__LAST_SCAN_AT && (now - window.__LAST_SCAN_AT) < throttleMs) {
    clearTimeout(window.__SCAN_TIMER); window.__SCAN_TIMER = setTimeout(() => doScan(), throttleMs);
    return;
  }
  doScan();
}

function requestFilterApply(throttleMs = 300) {
  if (!_validateNamePattern()) return;
  if (getVisibilityMode() === 'all') {
    try { requestScan(100); } catch (_) { }
    return;
  }
  const now = Date.now();
  if (window.__SCAN_RUNNING || window.__FILTER_RUNNING) { window.__FILTER_PENDING = true; return; }
  if (window.__LAST_FILTER_AT && (now - window.__LAST_FILTER_AT) < throttleMs) {
    clearTimeout(window.__FILTER_TIMER);
    window.__FILTER_TIMER = setTimeout(() => doFilterApply(), throttleMs);
    return;
  }
  doFilterApply();
}

async function doFilterApply() {
  if (!_validateNamePattern()) return;
  const visibilityMode = getVisibilityMode();
  if (visibilityMode === 'all') { try { await doScan(false); } catch (_) { } return; }
  if (!window.__SCAN || !window.__SCAN.root) { try { await doScan(false); } catch (_) { } return; }
  window.__SCAN_ID = (window.__SCAN_ID || 0) + 1;
  const scanId = window.__SCAN_ID;
  const path = document.getElementById("last_directory").value;
  if (!path) return;

  window.__FILTER_RUNNING = true;
  setFooterStatus("Filtering…", 'stats');
  try { showOverlay('Filtering…'); } catch (_) { }
  try {
    const res = await fetch("/api/filters/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildScanPayload(path)),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      if (res.status === 409 || data.needs_scan) {
        await doScan(false);
        return;
      }
      throw new Error(data.error || ("HTTP " + res.status));
    }
    applyScanResponse(data, path, scanId, { visibilityMode });
  } catch (err) {
    const text = "Filter failed: " + err;
    setFooterStatus(text, 'stats', { ttlMs: 8000 });
    try { showError('Filter Failed', err && err.message ? err.message : err); } catch (_) { }
  } finally {
    window.__FILTER_RUNNING = false;
    window.__LAST_FILTER_AT = Date.now();
    if (window.__FILTER_PENDING) { window.__FILTER_PENDING = false; requestFilterApply(50); }
    try { hideOverlay(); } catch (_) { }
  }
}

async function watchTickVisible(signal, session) {
  if (window.__WATCH_TICK_BUSY) return;
  window.__WATCH_TICK_BUSY = true;
  try {
    const wt = document.getElementById('watch_toggle');
    if (!wt || !wt.checked) return;
    if (!__WATCH_RUNNING || (typeof session === 'number' && session !== __WATCH_SESSION)) return;
    if (window.__AUTO_REFRESH_SUMMARIES === false) return;
    const visible = (window.__VISIBLE || (window.__SCAN && window.__SCAN.files) || []);
    const targets = visible.filter(f => modeRequiresSummary(f.mode) || hasSummaryError(f.abs_path)).map(f => f.abs_path);
    if (!targets.length) return;
    const data = await ensureSummariesForPaths(targets, { signal });
    if (!data) return;
    const m = data.metrics || {};
    if (m && m.errors) showBanner(`Watch summaries: ensured ${m.ensured || 0} — errors ${m.errors}`, 'warn');
  } catch (err) {
    try { if (err && err.name === 'AbortError') return; } catch (_) { }
    showBanner('Watch tick failed: ' + err, 'warn');
  } finally {
    window.__WATCH_TICK_BUSY = false;
  }
}

function selectAllRows() {
  const data = (window.__VISIBLE || (window.__SCAN && window.__SCAN.files) || []);
  window.__SEL = new Set(data.map(f => f.abs_path));
  const hdr = document.getElementById("hdr_chk");
  if (hdr) hdr.checked = true;
  renderTable();
}

function clearSelection() {
  window.__SEL = new Set();
  window.__LAST_CLICK_PATH = null;
  const hdr = document.getElementById("hdr_chk");
  if (hdr) hdr.checked = false;
  renderTable();
}

// Add selected files to custom_excluded and rescan
async function excludeSelectedPersist() {
  const paths = Array.from(window.__SEL || []);
  if (!paths.length) return;
  return excludePathsPersist(paths);
}

async function excludePathsPersist(paths) {
  const unique = Array.from(new Set((paths || []).filter(p => typeof p === 'string' && p.trim()).map(p => p.trim())));
  if (!unique.length) return;
  const root = _currentRootPath();
  // Use ABSOLUTE paths to prevent cross-project contamination
  const absPaths = unique.map(p => {
    const raw = String(p || '').trim();
    if (!raw) return '';
    // If already absolute, use as-is
    if (_isAbsPath(raw)) return raw;
    // Convert relative to absolute using current root
    if (root && !root.endsWith('/')) return root + '/' + raw;
    return root + raw;
  }).filter(Boolean);
  if (!absPaths.length) return;
  try {
    const settings = await (await fetch(_settingsUrl())).json();
    const cur = new Set(settings.custom_excluded || []);
    absPaths.forEach(p => cur.add(p));
    const res = await fetch('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(_settingsPayload({ custom_excluded: Array.from(cur) })) });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    // Re-apply filters without full rescan
    await doFilterApply();
  } catch (err) {
    try { showBanner('Exclude failed: ' + err, 'warn'); } catch (_) { alert('Exclude failed: ' + err); }
  }
}

async function unexcludePathsPersist(paths) {
  const unique = Array.from(new Set((paths || []).filter(p => typeof p === 'string' && p.trim()).map(p => p.trim())));
  if (!unique.length) return;
  const root = _currentRootPath();
  // Use ABSOLUTE paths to match what was stored
  const absPaths = unique.map(p => {
    const raw = String(p || '').trim();
    if (!raw) return '';
    if (_isAbsPath(raw)) return raw;
    if (root && !root.endsWith('/')) return root + '/' + raw;
    return root + raw;
  }).filter(Boolean);
  if (!absPaths.length) return;
  try {
    const settings = await (await fetch(_settingsUrl())).json();
    const cur = new Set(settings.custom_excluded || []);
    // Delete both the absolute path and any matching relative path (for backward compat)
    absPaths.forEach(p => {
      cur.delete(p);
      // Also try deleting the relative version for backward compatibility
      if (root && p.startsWith(root)) {
        const rel = p.slice(root.length).replace(/^\//, '');
        cur.delete(rel);
      }
    });
    const res = await fetch('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(_settingsPayload({ custom_excluded: Array.from(cur) })) });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    await doFilterApply();
  } catch (err) {
    try { showBanner('Unexclude failed: ' + err, 'warn'); } catch (_) { alert('Unexclude failed: ' + err); }
  }
}

async function toggleExcludePathsPersist(paths) {
  const unique = Array.from(new Set((paths || []).filter(p => typeof p === 'string' && p.trim()).map(p => p.trim())));
  if (!unique.length) return;
  const root = (window.__SCAN && window.__SCAN.root) || document.getElementById('last_directory').value;
  try {
    const res = await fetch('/api/custom_excluded/toggle', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: root, paths: unique }) });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    await doFilterApply();
  } catch (err) {
    try { showBanner('Toggle exclude failed: ' + err, 'warn'); } catch (_) { alert('Toggle exclude failed: ' + err); }
  }
}

// Hide selected rows for this session and current root only (no persistence)
function hidePaths(paths) {
  const root = (window.__SCAN && window.__SCAN.root) || document.getElementById('last_directory').value;
  const unique = Array.from(new Set((paths || []).filter(p => typeof p === 'string' && p.trim()).map(p => p.trim())));
  if (!unique.length) return;
  if (!window.__HIDDEN_MAP[root]) window.__HIDDEN_MAP[root] = new Set();
  unique.forEach(p => window.__HIDDEN_MAP[root].add(p));
  if (!shouldRespectHidden()) {
    try { showBanner('Show everything is active; hidden items remain visible.', 'warn'); } catch (_) { }
    return;
  }
  const files = (window.__SCAN && window.__SCAN.files) || [];
  window.__VISIBLE = files.filter(f => !window.__HIDDEN_MAP[root].has(f.abs_path));
  try { applyActiveHeaderSortToData(window.__VISIBLE); } catch (_) { }
  try { window.__SCAN_MAP = {}; (window.__VISIBLE || []).forEach(f => { window.__SCAN_MAP[f.abs_path] = f; }); } catch (_) { }
  // Drop hidden files from current selection so downstream totals use only visible
  try {
    const visSet = new Set((window.__VISIBLE || []).map(f => f.abs_path));
    window.__SEL = new Set(Array.from(window.__SEL || []).filter(p => visSet.has(p)));
  } catch (_) { }
  window.__TOK_FINAL = undefined; window.__TOK_FINAL_SKIPPED = false; window.__TOK_FINAL_COUNT = undefined;
  renderTable();
  updateScanStatsText();
  try { maybeComputeFinalTokens(window.__SCAN_ID); } catch (_) { }
}

function clearHidden() {
  const root = (window.__SCAN && window.__SCAN.root) || document.getElementById('last_directory').value;
  if (window.__HIDDEN_MAP && window.__HIDDEN_MAP[root]) delete window.__HIDDEN_MAP[root];
  const files = (window.__SCAN && window.__SCAN.files) || [];
  window.__VISIBLE = files;
  try { applyActiveHeaderSortToData(window.__VISIBLE); } catch (_) { }
  try { window.__SCAN_MAP = {}; (window.__VISIBLE || []).forEach(f => { window.__SCAN_MAP[f.abs_path] = f; }); } catch (_) { }
  window.__TOK_FINAL = undefined; window.__TOK_FINAL_SKIPPED = false; window.__TOK_FINAL_COUNT = undefined;
  renderTable();
  updateScanStatsText();
  try { maybeComputeFinalTokens(window.__SCAN_ID); } catch (_) { }
}

function escapeHtml(s) {
  return ("" + s).replace(/[&<>\"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;" }[c]));
}

function modeLabel(mode, _hasErr = false) {
  const m = ("" + (mode || "")).toUpperCase();
  let label = "";
  if (m === "CODE") label = "Code";
  else if (m === "SKELETON") label = "Skeleton";
  else if (m === "API_SURFACE") label = "API surface";
  else if (m === "SIGNATURES") label = "Signatures";
  else if (m === "CODE_PLUS_SKELETON") label = "Code + skeleton";
  else if (m === "SUMMARY_SHORT") label = "Summary (short)";
  else if (m === "SUMMARY_LONG") label = "Summary (long)";
  else if (m === "SUMMARY_SHORT_PLUS_SKELETON") label = "Short summary + skeleton";
  else if (m === "SUMMARY_LONG_PLUS_SKELETON") label = "Long summary + skeleton";
  else if (m === "CODE_PLUS_SUMMARY_SHORT_PLUS_SKELETON") label = "Code + short summary + skeleton";
  else if (m === "CODE_PLUS_SUMMARY_LONG_PLUS_SKELETON") label = "Code + long summary + skeleton";
  else if (m === "CODE_PLUS_SHORT") label = "Code + short";
  else if (m === "CODE_PLUS_LONG") label = "Code + long";
  else if (m === "EXCLUDE") label = "Exclude";
  return label;
}

function modeRequiresSummary(mode) {
  const m = ("" + (mode || "")).toUpperCase();
  return m === "SUMMARY_SHORT" || m === "SUMMARY_LONG" || m === "CODE_PLUS_SHORT" || m === "CODE_PLUS_LONG" || m === "SUMMARY_SHORT_PLUS_SKELETON" || m === "SUMMARY_LONG_PLUS_SKELETON" || m === "CODE_PLUS_SUMMARY_SHORT_PLUS_SKELETON" || m === "CODE_PLUS_SUMMARY_LONG_PLUS_SKELETON";
}

function folderModePill(mode) {
  const m = ("" + (mode || "")).toUpperCase();
  if (m === "FOLDER_SUMMARY_FULL") {
    return ` <span class="mode-pill folder-pill" title="Folder summary (full)">FOLDER F</span>`;
  }
  if (m === "FOLDER_SUMMARY_QUICK" || m === "FOLDER_SUMMARY") {
    return ` <span class="mode-pill folder-pill" title="Folder summary (full)">FOLDER F</span>`;
  }
  return "";
}

function categoryClass(cat) {
  const raw = String(cat || '').trim().toLowerCase();
  if (!raw) return '';
  return 'cat-' + raw.replace(/[^a-z0-9_-]/g, '');
}

function hasSummaryError(abs) {
  try { return !!(window.__SUMMARY_ERRORS && window.__SUMMARY_ERRORS.has(abs)); } catch (_) { return false; }
}

function isPatternHit(relPath) {
  try { return !!(window.__PATTERN_HITS && window.__PATTERN_HITS.has(relPath)); } catch (_) { return false; }
}

function updateSummaryErrors(errorPaths, requestedPaths) {
  const set = window.__SUMMARY_ERRORS || new Set();
  const errList = Array.isArray(errorPaths) ? errorPaths.filter(p => typeof p === 'string' && p.trim()) : [];
  const reqList = Array.isArray(requestedPaths) ? requestedPaths.filter(p => typeof p === 'string' && p.trim()) : [];
  const errSet = new Set(errList);
  let changed = false;
  if (reqList.length) {
    reqList.forEach(p => {
      if (!errSet.has(p) && set.has(p)) { set.delete(p); changed = true; }
    });
  }
  errSet.forEach(p => { if (!set.has(p)) { set.add(p); changed = true; } });
  window.__SUMMARY_ERRORS = set;
  if (changed) renderTable();
}

// Expand/collapse all folders in hierarchy view
function expandAll() {
  const data = (window.__VISIBLE || (window.__SCAN && window.__SCAN.files) || []);
  const set = new Set(['']);
  data.forEach(f => {
    const rel = f.rel_path || '';
    if (!rel) return;
    const parts = rel.split('/');
    let cur = '';
    for (let i = 0; i < parts.length - 1; i++) {
      cur = cur ? (cur + '/' + parts[i]) : parts[i];
      set.add(cur);
    }
  });
  window.__EXPANDED = set;
  renderTable();
  updateScanStatsText();
}
function collapseAll() {
  window.__EXPANDED = new Set(['']);
  renderTable();
  updateScanStatsText();
}

async function previewFile(absPath) {
  const file_path = String(absPath || '').trim();
  if (!file_path) return;
  try {
    try { showOverlay('Loading preview…'); } catch (_) { }
    const res = await fetch('/api/file/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_path, max_kb: 256 }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    const metaBits = [];
    if (data.size_bytes !== undefined && data.size_bytes !== null) metaBits.push(`bytes ${data.size_bytes}`);
    if (data.shown_kb !== undefined && data.shown_kb !== null) metaBits.push(`shown ${data.shown_kb} KB`);
    if (data.truncated) metaBits.push('truncated');
    const meta = metaBits.join(' — ');
    showPreviewModal({ title: 'File preview', meta, text: data.text || '', file_path });
  } catch (err) {
    showError('Preview Failed', err && err.message ? err.message : err);
  } finally {
    try { hideOverlay(); } catch (_) { }
  }
}

function _numOrZero(val) {
  if (typeof val === 'number') return val;
  const n = parseInt(String(val || '0'), 10);
  return Number.isFinite(n) ? n : 0;
}

function extractPruneSummary(job) {
  if (!job || typeof job !== 'object') return null;
  const result = job.result && typeof job.result === 'object' ? job.result : null;
  const stats = result && result.plan && typeof result.plan === 'object'
    ? result.plan
    : (result && typeof result === 'object' ? result : null);
  if (!stats || typeof stats !== 'object') return null;
  const outputPath = (result && typeof result.output_path === 'string' && result.output_path.trim())
    ? result.output_path
    : (typeof stats.written === 'string' ? stats.written : '');
  return { stats, outputPath };
}

function formatPruneSummaryText(stats, outputPath) {
  const keptDefs = _numOrZero(stats.kept_defs);
  const keptFiles = _numOrZero(stats.kept_files);
  const keptTokens = _numOrZero(stats.tokens);
  const overflowFlag = stats.overflow ? 'Yes' : 'No';
  const overflowDefs = _numOrZero(stats.overflow_defs);
  const overflowFiles = _numOrZero(stats.overflow_files);
  const overflowTokens = _numOrZero(stats.overflow_tokens);
  const bits = [];
  if (outputPath) bits.push(`Output: ${outputPath}`);
  bits.push(`Pack A — defs: ${keptDefs}, files: ${keptFiles}, tokens: ${keptTokens}`);
  bits.push(`Overflow (${overflowFlag}) — defs: ${overflowDefs}, files: ${overflowFiles}, tokens: ${overflowTokens}`);
  return bits.join('\n');
}

function showPruneSummaryModal(stats, outputPath) {
  const modal = document.getElementById('prune_summary_modal');
  if (!modal) return;
  window.__PRUNE_LAST_OUTPUT = outputPath || '';
  window.__PRUNE_LAST_STATS = stats || null;

  const setText = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = String(v); };
  const keptDefs = _numOrZero(stats.kept_defs);
  const keptFiles = _numOrZero(stats.kept_files);
  const keptTokens = _numOrZero(stats.tokens);
  const overflowFlag = stats.overflow ? 'Yes' : 'No';
  const overflowDefs = _numOrZero(stats.overflow_defs);
  const overflowFiles = _numOrZero(stats.overflow_files);
  const overflowTokens = _numOrZero(stats.overflow_tokens);

  setText('prune_kept_defs', keptDefs);
  setText('prune_kept_files', keptFiles);
  setText('prune_kept_tokens', keptTokens);
  setText('prune_overflow_flag', overflowFlag);
  setText('prune_overflow_defs', overflowDefs);
  setText('prune_overflow_files', overflowFiles);
  setText('prune_overflow_tokens', overflowTokens);

  const metaBits = [];
  if (outputPath) metaBits.push(outputPath);
  const meta = document.getElementById('prune_summary_meta');
  if (meta) meta.textContent = metaBits.join(' — ');

  const extra = document.getElementById('prune_summary_extra');
  const stubEl = document.getElementById('prune_stub_suggestions');
  if (stats && stats.stub_suggestions) {
    if (extra) extra.style.display = 'block';
    if (stubEl) stubEl.textContent = JSON.stringify(stats.stub_suggestions, null, 2);
  } else {
    if (extra) extra.style.display = 'none';
    if (stubEl) stubEl.textContent = '';
  }

  modal.style.display = 'flex';
}

function hidePruneSummaryModal() {
  const modal = document.getElementById('prune_summary_modal');
  if (modal) modal.style.display = 'none';
}

async function openPruneSummaryOutput() {
  const path = typeof window.__PRUNE_LAST_OUTPUT === 'string' ? window.__PRUNE_LAST_OUTPUT : '';
  if (!path) return;
  try { await fetch('/api/open', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path }) }); } catch (_) { }
}

async function copyPruneSummaryPath() {
  const path = typeof window.__PRUNE_LAST_OUTPUT === 'string' ? window.__PRUNE_LAST_OUTPUT : '';
  if (!path) return;
  const ok = await copyTextToClipboard(path);
  if (ok) showBanner('Output path copied', 'ok'); else showBanner('Copy failed', 'warn');
}

function handlePruneJobResult(job, outEl) {
  if (job && job.status === 'error') {
    const msg = job.error || 'Prune failed.';
    if (outEl) outEl.textContent = `Prune failed: ${msg}`;
    try { showError('Prune Failed', msg); } catch (_) { }
    return;
  }
  const summary = extractPruneSummary(job);
  if (!summary) {
    if (outEl) outEl.textContent = JSON.stringify(job, null, 2);
    return;
  }
  const { stats, outputPath } = summary;
  showPruneSummaryModal(stats, outputPath);
  if (outEl) outEl.textContent = formatPruneSummaryText(stats, outputPath);
}

async function startPrune() {
  const out = document.getElementById("prune_out");
  const goal = document.getElementById("prune_goal").value.trim();
  if (!goal) { out.textContent = "Please enter a goal."; return; }
  const path = document.getElementById("last_directory").value;
  let selected_paths = Array.from(window.__SEL || []);
  selected_paths = Array.from(new Set(selected_paths));
  let visible_paths = null;
  if (!selected_paths.length) {
    const visible = (window.__VISIBLE || (window.__SCAN && window.__SCAN.files) || []);
    const hasScan = !!(window.__SCAN && Array.isArray(window.__SCAN.files));
    if (hasScan) {
      visible_paths = visible
        .filter(f => f && f.abs_path && (String(f.mode || '').toUpperCase() !== 'EXCLUDE'))
        .map(f => f.abs_path);
      visible_paths = Array.from(new Set(visible_paths));
    }
    const msg = hasScan
      ? `No files selected. Prune will use the current visible list (${(visible_paths || []).length} files). Continue?`
      : 'No files selected. Prune will compute scope from your current filters. Continue?';
    if (!confirm(msg)) { out.textContent = "Cancelled."; return; }
  }
  const executeWorkspace = !!document.getElementById('prune_execute_workspace')?.checked;
  if (executeWorkspace && !window.confirm(
    'Run code from this repository with your local user permissions? Continue only if you trust the repository.'
  )) return;
  out.textContent = "Starting prune…";
  try {
    const prunePatch = collectPruneConfigFromUi();
    const outputPath = prunePatch.prune_output_path || '';
    try { persistConfigPatch(prunePatch); } catch (_) { }
    try { if (outputPath) await allowOutputPath(outputPath); } catch (_) { }
    const prunePayload = await window.providerConfirmedPayload('prune', {
        path,
        goal,
        selected_paths,
        visible_paths,
        // Ensure server can reconstruct "visible files" scope when no explicit selection is provided
        include_all: false,
        ignore_custom_excluded: !!(document.getElementById('f_ignore_persisted') && document.getElementById('f_ignore_persisted').checked),
        always_include_docs: document.getElementById("f_always_docs").checked,
        exclude_node_modules: document.getElementById("f_ex_node").checked,
        exclude_ios_noise: !!(document.getElementById("f_ex_ios") && document.getElementById("f_ex_ios").checked),
        exclude_python_noise: document.getElementById("f_ex_py").checked,
        exclude_java_gradle_noise: document.getElementById("f_ex_java").checked,
        exclude_web_build_noise: document.getElementById("f_ex_web").checked,
        use_git_files: document.getElementById("f_use_git").checked,
        git_include_untracked: document.getElementById("f_git_untracked").checked,
        git_only_changed: document.getElementById("f_git_changed").checked,
        git_since_ref: document.getElementById("f_git_since").value,
        git_until_ref: document.getElementById("f_git_until").value,
        exclude_vendored: document.getElementById("f_ex_vendored").checked,
        xcode_target: document.getElementById("f_xcode_target").value,
        ignore_binary: document.getElementById('f_ignore_binary').checked,
        max_file_size_mb: parseFloat(document.getElementById('f_max_mb').value || '0'),
        max_walk_depth: parseInt(document.getElementById('f_max_depth').value || '0', 10),
        follow_symlinks: document.getElementById('f_follow_symlinks').checked,
        filters: getFilters(),
        output_path: outputPath || undefined,
        prune_ranking_mode: prunePatch.prune_ranking_mode,
        prune_engine: prunePatch.prune_engine,
        prune_neighbors_mode: prunePatch.prune_neighbors_mode,
        prune_mode: prunePatch.prune_mode,
        prune_dry_run: prunePatch.prune_dry_run,
        prune_execute_workspace: executeWorkspace,
        prune_token_budget: prunePatch.prune_token_budget,
      });
    const res = await fetch("/api/prune", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(prunePayload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
    window.__PRUNE_JOB = data.job_id;
    out.textContent = `Job started: ${data.job_id}`;
    const poller = (typeof window !== 'undefined') ? window.pollPruneJob : null;
    if (typeof poller === 'function') poller(window.__PRUNE_JOB, 'prune_out', 'Pruning…');
  } catch (err) {
    showError('Prune Failed', err.message || err);
  }
}

async function checkPrune() {
  const out = document.getElementById("prune_out");
  const id = window.__PRUNE_JOB;
  if (!id) { out.textContent = "No job id yet. Start prune first."; return; }
  out.textContent = "Checking…";
  try {
    const res = await fetch(`/api/jobs/${id}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
    if (data.status === 'done' || data.status === 'error') {
      handlePruneJobResult(data, out);
    } else {
      out.textContent = JSON.stringify(data, null, 2);
    }
  } catch (err) {
    out.textContent = "Failed to get job status: " + err;
  }
}

async function verifyTokens() {
  if (window.__VERIFY_BUSY) return; window.__VERIFY_BUSY = true;
  const out = document.getElementById("verify_out") || document.getElementById("merge_status");
  const path = document.getElementById("last_directory").value;
  let selected_paths = Array.from(window.__SEL || []);
  selected_paths = Array.from(new Set(selected_paths));
  const selected_raw = selected_paths.slice();
  selected_paths = filterNonExcludedPaths(selected_paths);
  if (selected_raw.length && selected_paths.length === 0) {
    if (out) out.textContent = "All selected files are excluded.";
    return;
  }
  if (selected_paths.length === 0) {
    const visible = (window.__VISIBLE || (window.__SCAN && window.__SCAN.files) || []);
    selected_paths = visible
      .filter(f => f && f.abs_path && (String(f.mode || '').toUpperCase() !== 'EXCLUDE'))
      .map(f => f.abs_path);
    selected_paths = Array.from(new Set(selected_paths));
  }
  if (selected_paths.length === 0) { if (out) out.textContent = "Nothing selected."; return; }
  if (out) out.textContent = "Computing tokens…";
  try {
    try { showOverlay('Computing tokens…'); } catch (_) { }
    const mergeOpts = buildMergeOutputOptions();
    const res = await fetch("/api/verify_tokens", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, selected_paths, ensure_summaries: true, ...mergeOpts }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
    if (out) out.textContent = `Exact tokens — total: ${data.tokens_total}, content-only: ${data.tokens_content_only}`;
    // reflect in scan footer as well
    window.__TOK_FINAL = data.tokens_total || 0;
    window.__TOK_CONTENT = data.tokens_content_only || window.__TOK_CONTENT || 0;
    updateScanStatsText();
    handleMergeSummaryErrors(data, selected_paths);
  } catch (err) {
    if (out) out.textContent = "Verify tokens failed: " + err;
  } finally { window.__VERIFY_BUSY = false; try { hideOverlay(); } catch (_) { } }
}

async function saveFooterFinalOnly() {
  try {
    const v = document.getElementById('toggle_final_footer').checked;
    await fetch('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(_settingsPayload({ footer_final_only: v })) });
  } catch (_) { }
}

function _readCustomExtMap() {
  const el = document.getElementById('s_ext_map');
  const raw = el ? String(el.value || '').trim() : '';
  if (!raw) return {};
  const parsed = JSON.parse(raw);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('Custom Extension Map must be a JSON object.');
  }
  return parsed;
}

function _writeCustomExtMap(map) {
  const el = document.getElementById('s_ext_map');
  if (!el) return;
  el.value = JSON.stringify(map || {}, null, 2);
}

function refreshCustomExtList() {
  const list = document.getElementById('ext_map_list');
  if (!list) return;
  let map = {};
  try {
    map = _readCustomExtMap();
  } catch (err) {
    showError('Custom Extensions', err && err.message ? err.message : err);
    return;
  }
  list.innerHTML = '';
  const keys = Object.keys(map || {}).sort();
  keys.forEach((ext) => {
    const val = map[ext];
    const label = `.${ext} -> ${val}`;
    const opt = document.createElement('option');
    opt.value = ext;
    opt.textContent = label;
    list.appendChild(opt);
  });
  if (keys.length === 0) {
    const opt = document.createElement('option');
    opt.value = '';
    opt.textContent = '(none)';
    list.appendChild(opt);
  }
}

function syncCustomExtEditorFromList() {
  const list = document.getElementById('ext_map_list');
  if (!list) return;
  const ext = list.value || '';
  if (!ext) return;
  let map = {};
  try {
    map = _readCustomExtMap();
  } catch (_) {
    return;
  }
  const extInput = document.getElementById('ext_map_ext');
  const catSel = document.getElementById('ext_map_cat');
  if (extInput) extInput.value = ext;
  if (catSel && map[ext]) catSel.value = String(map[ext]).toUpperCase();
}

function addOrUpdateCustomExt() {
  const extInput = document.getElementById('ext_map_ext');
  const catSel = document.getElementById('ext_map_cat');
  if (!extInput || !catSel) return;
  const ext = String(extInput.value || '').trim().replace(/^\./, '').toLowerCase();
  const cat = String(catSel.value || '').trim().toUpperCase();
  if (!ext) { showBanner('Enter an extension (e.g., swift).', 'warn'); return; }
  if (!cat) { showBanner('Select a category.', 'warn'); return; }
  let map = {};
  try {
    map = _readCustomExtMap();
  } catch (err) {
    showError('Custom Extensions', err && err.message ? err.message : err);
    return;
  }
  map[ext] = cat;
  _writeCustomExtMap(map);
  refreshCustomExtList();
  const list = document.getElementById('ext_map_list');
  if (list) list.value = ext;
}

function removeCustomExt() {
  const list = document.getElementById('ext_map_list');
  if (!list || !list.value) return;
  const ext = String(list.value || '').trim();
  if (!ext) return;
  let map = {};
  try {
    map = _readCustomExtMap();
  } catch (err) {
    showError('Custom Extensions', err && err.message ? err.message : err);
    return;
  }
  delete map[ext];
  _writeCustomExtMap(map);
  refreshCustomExtList();
  const extInput = document.getElementById('ext_map_ext');
  if (extInput) extInput.value = '';
}

function _currentRootPath() {
  return String((window.__SCAN && window.__SCAN.root) || (document.getElementById('last_directory') || {}).value || '').trim();
}

function _settingsUrl() {
  const root = _currentRootPath();
  return root ? (`/api/settings?path=${encodeURIComponent(root)}`) : '/api/settings';
}

function _settingsPayload(payload) {
  const root = _currentRootPath();
  if (!root) return payload || {};
  return Object.assign({ path: root }, payload || {});
}

function _normalizeListItems(items, opts) {
  const out = [];
  const seen = new Set();
  const list = Array.isArray(items) ? items : [];
  list.forEach((item) => {
    const raw = (item !== undefined && item !== null) ? String(item).trim() : '';
    if (!raw) return;
    const key = raw.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    out.push(raw);
  });
  if (!opts || opts.sort !== false) out.sort((a, b) => a.localeCompare(b));
  return out;
}

function _setListOptions(selectId, items, opts) {
  const sel = document.getElementById(selectId);
  if (!sel) return;
  sel.innerHTML = '';
  const list = _normalizeListItems(items, opts);
  if (!list.length) {
    const opt = document.createElement('option');
    opt.value = '';
    opt.textContent = '(none)';
    opt.disabled = true;
    opt.selected = true;
    sel.appendChild(opt);
    return;
  }
  list.forEach((val) => {
    const opt = document.createElement('option');
    opt.value = val;
    opt.textContent = val;
    sel.appendChild(opt);
  });
}

function _readListOptions(selectId) {
  const sel = document.getElementById(selectId);
  if (!sel) return null;
  return Array.from(sel.options || [])
    .filter(o => o && !o.disabled && String(o.value || '').trim())
    .map(o => String(o.value || '').trim());
}

function _mergeListOptions(selectId, items, opts) {
  const cur = _readListOptions(selectId) || [];
  const merged = cur.concat(Array.isArray(items) ? items : []);
  _setListOptions(selectId, merged, opts);
}

function _removeSelectedOptions(selectId) {
  const sel = document.getElementById(selectId);
  if (!sel) return;
  const remaining = Array.from(sel.options || []).filter(o => o && (!o.selected || o.disabled)).map(o => o.value);
  _setListOptions(selectId, remaining);
}

function updateExclusionsSummary(dirs, files, custom) {
  const el = document.getElementById('exclusions_summary');
  if (!el) return;
  const d = Array.isArray(dirs) ? dirs.length : 0;
  const f = Array.isArray(files) ? files.length : 0;
  const c = Array.isArray(custom) ? custom.length : 0;
  el.textContent = `Dirs: ${d} • Files: ${f} • Custom: ${c}`;
}

function applyExclusionsToUi(data) {
  const dirs = Array.isArray(data && data.excluded_dirs) ? data.excluded_dirs : [];
  const files = Array.isArray(data && data.excluded_files) ? data.excluded_files : [];
  const custom = Array.isArray(data && data.custom_excluded) ? data.custom_excluded : [];
  _setListOptions('ex_dirs_list', dirs);
  _setListOptions('ex_files_list', files);
  _setListOptions('ex_custom_list', custom, { sort: false });
  const elDirs = document.getElementById('s_ex_dirs'); if (elDirs) elDirs.value = dirs.join('\n');
  const elFiles = document.getElementById('s_ex_files'); if (elFiles) elFiles.value = files.join('\n');
  const elCustom = document.getElementById('s_custom_ex'); if (elCustom) elCustom.value = custom.join('\n');
  updateExclusionsSummary(dirs, files, custom);
  try {
    window.__CURRENT_SETTINGS = Object.assign({}, window.__CURRENT_SETTINGS || {}, {
      excluded_dirs: dirs,
      excluded_files: files,
      custom_excluded: custom,
    });
  } catch (_) { }
  try { updateActiveFiltersSummary(); } catch (_) { }
  window.__EXCLUSIONS_READY = true;
}

async function _fetchSettingsSnapshot() {
  const res = await fetch(_settingsUrl());
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
  return data || {};
}

function switchExclusionsTab(tabId, btnElement) {
  const modal = document.getElementById('exclusions_modal');
  if (!modal) return;
  modal.querySelectorAll('.tab-pane').forEach(el => el.classList.remove('active'));
  modal.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  const pane = document.getElementById(tabId);
  if (pane) pane.classList.add('active');
  if (btnElement) btnElement.classList.add('active');
}

async function openExclusionsModal() {
  const m = document.getElementById('exclusions_modal');
  if (!m) return;
  try { applyModalGeometry('exclusions_modal', 'modal_geometry_exclusions'); } catch (_) { }
  m.style.display = 'flex';
  try {
    const data = await _fetchSettingsSnapshot();
    applyExclusionsToUi(data);
  } catch (err) {
    showBanner('Failed to load exclusions: ' + err, 'warn');
  }
  try {
    const firstBtn = m.querySelector('.tabs-nav .tab-btn');
    if (firstBtn) switchExclusionsTab('ex_tab_dirs', firstBtn);
  } catch (_) { }
}

function _hideExclusionsModal() {
  const m = document.getElementById('exclusions_modal');
  if (m) m.style.display = 'none';
  try { persistModalGeometry('exclusions_modal', 'modal_geometry_exclusions'); } catch (_) { }
}

async function _commitExclusions(opts) {
  const close = !!(opts && opts.close);
  const forceClose = !!(opts && opts.forceClose);
  const silent = !!(opts && opts.silent);
  if (!window.__EXCLUSIONS_READY) {
    if (close && forceClose) _hideExclusionsModal();
    return false;
  }
  const dirs = _readListOptions('ex_dirs_list') || [];
  const files = _readListOptions('ex_files_list') || [];
  const custom = _readListOptions('ex_custom_list') || [];
  const payload = { excluded_dirs: dirs, excluded_files: files, custom_excluded: custom };
  let ok = false;
  try {
    const res = await fetch('/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(_settingsPayload(payload)),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    try { if (data && data.settings) window.__CURRENT_SETTINGS = data.settings; } catch (_) { }
    if (data && data.settings) {
      applyExclusionsToUi({
        excluded_dirs: data.settings.excluded_dirs || [],
        excluded_files: data.settings.excluded_files || [],
        custom_excluded: data.settings.custom_excluded || [],
      });
    } else {
      applyExclusionsToUi(payload);
    }
    if (!silent) {
      showBanner('Exclusions saved.', '');
      setTimeout(hideBanner, 2000);
    }
    requestScan(100);
    ok = true;
  } catch (err) {
    if (!silent) {
      showError('Save Exclusions Failed', err && err.message ? err.message : err);
    }
  }
  if (close && (ok || forceClose)) _hideExclusionsModal();
  return ok;
}

async function closeExclusionsModal() {
  await _commitExclusions({ close: true, forceClose: true, silent: true });
}

async function _pickPaths(opts) {
  const root = opts && opts.path ? opts.path : _currentRootPath();
  try {
    if (window.pywebview && window.pywebview.api) {
      if (opts.allowDirs && opts.allowFiles) {
        // Fall back to manual picker to support mixed selections.
      } else if (opts.allowDirs && !opts.allowFiles && window.pywebview.api.browse_dir) {
        const chosen = await window.pywebview.api.browse_dir(root || null);
        if (chosen && typeof chosen === 'string') return [chosen];
        return [];
      }
      if (opts.allowFiles && (window.pywebview.api.browse_files || window.pywebview.api.open_file_dialog)) {
        const picker = window.pywebview.api.browse_files || window.pywebview.api.open_file_dialog;
        const chosen = await picker.call(window.pywebview.api, root || null);
        if (Array.isArray(chosen)) return chosen.filter(p => typeof p === 'string');
        if (typeof chosen === 'string') return [chosen];
        return [];
      }
    }
  } catch (_) { }
  return await new Promise(resolve => {
    const done = (paths) => resolve(Array.isArray(paths) ? paths : []);
    openManualPicker({
      mode: 'pick',
      allowDirs: !!opts.allowDirs,
      allowFiles: !!opts.allowFiles,
      single: !!opts.single,
      path: root || '',
      onConfirm: done,
      onCancel: () => done([]),
    });
  });
}

function _isAbsPath(path) {
  if (!path) return false;
  if (path.startsWith('/') || path.startsWith('\\')) return true;
  return /^[A-Za-z]:[\\/]/.test(path);
}

function _resolveExcludePath(raw) {
  if (!raw) return '';
  const root = _currentRootPath();
  if (_isAbsPath(raw) || !root) return raw;
  const joiner = (root.endsWith('/') || root.endsWith('\\')) ? '' : '/';
  return root + joiner + raw;
}

function _normalizeExcludeDirEntry(path) {
  const raw = String(path || '').trim();
  if (!raw) return '';
  const trimmed = raw.replace(/[\\\/]+$/, '');
  return _resolveExcludePath(trimmed);
}

function _normalizeExcludeFileEntry(path) {
  const raw = String(path || '').trim();
  if (!raw) return '';
  return _resolveExcludePath(raw);
}

function _normalizeCustomExcludeEntry(path) {
  const raw = String(path || '').trim();
  if (!raw) return '';
  const root = _currentRootPath();
  if (root && raw.startsWith(root)) {
    return raw.slice(root.length).replace(/^[/\\]+/, '');
  }
  return raw;
}

async function addExcludedDir() {
  const picked = await _pickPaths({ allowDirs: true, allowFiles: false, single: true });
  if (!picked.length) return;
  const entries = picked.map(_normalizeExcludeDirEntry).filter(Boolean);
  _mergeListOptions('ex_dirs_list', entries);
  updateExclusionsSummary(_readListOptions('ex_dirs_list'), _readListOptions('ex_files_list'), _readListOptions('ex_custom_list'));
  await _commitExclusions({ silent: true });
}

function removeExcludedDir() {
  _removeSelectedOptions('ex_dirs_list');
  updateExclusionsSummary(_readListOptions('ex_dirs_list'), _readListOptions('ex_files_list'), _readListOptions('ex_custom_list'));
  _commitExclusions({ silent: true });
}

async function addExcludedFiles() {
  const picked = await _pickPaths({ allowDirs: false, allowFiles: true, single: false });
  if (!picked.length) return;
  const entries = picked.map(_normalizeExcludeFileEntry).filter(Boolean);
  _mergeListOptions('ex_files_list', entries);
  updateExclusionsSummary(_readListOptions('ex_dirs_list'), _readListOptions('ex_files_list'), _readListOptions('ex_custom_list'));
  await _commitExclusions({ silent: true });
}

function removeExcludedFiles() {
  _removeSelectedOptions('ex_files_list');
  updateExclusionsSummary(_readListOptions('ex_dirs_list'), _readListOptions('ex_files_list'), _readListOptions('ex_custom_list'));
  _commitExclusions({ silent: true });
}

async function addCustomExcluded() {
  const picked = await _pickPaths({ allowDirs: true, allowFiles: true, single: false });
  if (!picked.length) return;
  const entries = picked.map(_normalizeCustomExcludeEntry).filter(Boolean);
  _mergeListOptions('ex_custom_list', entries, { sort: false });
  updateExclusionsSummary(_readListOptions('ex_dirs_list'), _readListOptions('ex_files_list'), _readListOptions('ex_custom_list'));
  await _commitExclusions({ silent: true });
}

function removeCustomExcluded() {
  _removeSelectedOptions('ex_custom_list');
  updateExclusionsSummary(_readListOptions('ex_dirs_list'), _readListOptions('ex_files_list'), _readListOptions('ex_custom_list'));
  _commitExclusions({ silent: true });
}

async function resetExcludedCustom() {
  try {
    if (!confirm('Clear custom excluded list?')) return;
    const res = await fetch('/api/settings/excluded/reset', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(_settingsPayload({ dirs: false, files: false, custom: true })) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    const dirs = _readListOptions('ex_dirs_list') || (data.excluded_dirs || []);
    const files = _readListOptions('ex_files_list') || (data.excluded_files || []);
    applyExclusionsToUi({ excluded_dirs: dirs, excluded_files: files, custom_excluded: data.custom_excluded || [] });
    requestScan(100);
  } catch (err) {
    showError('Reset Custom Excluded Failed', err && err.message ? err.message : err);
  }
}

async function saveExclusionsModal() {
  await _commitExclusions({ close: true });
}

async function loadSettings() {
  try {
    const res = await fetch(_settingsUrl());
    const data = await res.json();
    if (!res.ok) { alert(data.error || 'Failed'); return; }
    try { window.__CURRENT_SETTINGS = data; window.__SETTINGS_LOADED = true; } catch (_) { }
    const setText = (id, v) => { const el = document.getElementById(id); if (el) el.value = String((v !== undefined && v !== null) ? v : ''); };
    setText('s_always_docs', (data.always_include_docs_list || []).join('\n'));
    setText('s_ext_map', JSON.stringify(data.custom_ext_map || {}, null, 2));
    setText('s_ex_dirs', (data.excluded_dirs || []).join('\n'));
    setText('s_ex_files', (data.excluded_files || []).join('\n'));
    setText('s_custom_ex', (data.custom_excluded || []).join('\n'));
    try { applyExclusionsToUi(data); } catch (_) { }
    // Advanced
    const set = (id, v) => {
      const el = document.getElementById(id);
      if (el == null) return;
      if (el.type === 'checkbox') el.checked = !!v; else el.value = (v !== undefined && v !== null) ? v : el.value;
    };
    set('s_use_batch', data.groq_use_batch);
    set('s_batch_progress', data.batch_progress);
    set('s_strict', data.groq_strict_mode);
    set('s_workers', data.groq_max_workers);
    set('s_sum_limit', data.summary_token_limit);
    set('s_timeout', data.groq_request_timeout_seconds || 120.0);
    set('s_retry_budget', data.groq_summary_retry_budget || 2);
    set('s_summary_default_mode', data.summary_default_mode || 'CODE');
    set('s_auto_refresh_summaries', (data.auto_refresh_summaries !== undefined) ? data.auto_refresh_summaries : true);
    set('s_summarize_vendored', data.summarize_vendored);
    set('s_fallback_to_code', (data.fallback_to_code_on_summary_error !== undefined) ? data.fallback_to_code_on_summary_error : false);
    set('s_strict_summary_required', !!data.strict_summary_required);
    set('s_summary_change_strategy', data.summary_change_strategy || 'mtime_size');
    set('s_summary_prompt_version', data.summary_prompt_version || 7);
    set('s_embed', data.embedding_provider || 'groq');
    set('s_retr', data.retrieval_backend || 'embeddings');
    set('s_font', data.ui_font_size || 10);
    set('s_contrast', data.ui_high_contrast);
    set('s_compact', data.ui_compact_mode);
    set('s_watch_debounce', data.ui_watch_debounce_ms || 800);
    set('s_watch_sample_limit', data.watch_sample_limit || 3000);
    set('use_folder_name', data.use_folder_name);
    set('append_date', data.append_datetime);
    set('toggle_final_footer', data.footer_final_only);
    set('toggle_bytes_footer', (data.show_bytes_footer !== undefined) ? data.show_bytes_footer : true);
    set('toggle_totals', data.show_totals_columns);
    set('toggle_density', data.show_density_column);
    set('opt_tree', data.include_file_tree);
    set('opt_manifest', data.include_json_manifest);
    set('opt_fences', data.output_markdown_fences);
    set('opt_toc', data.output_markdown_toc);
    set('opt_strip', data.remove_empty);
    set('opt_enum', data.enumerate_lines);
    set('f_apply_token_transforms', data.apply_token_filters_to_transforms);
    set('merge_content_mode', data.content_mode || 'code_only');
    const modeMap = { single_file: 'single', split_lines: 'multiple', split_tokens: 'token_split' };
    set('merge_structure', modeMap[data.structure_mode] || 'single');
    set('merge_split_n', data.split_into_n || 5);
    set('merge_token_limit', data.token_limit || 1048576);
    set('merge_prompt_template', data.prompt_template || '');
    set('s_custom_start', data.custom_start_label || '');
    set('s_custom_end', data.custom_end_label || '');
    set('s_custom_tree_start', data.custom_tree_start_label || '');
    set('s_custom_tree_end', data.custom_tree_end_label || '');
    try { updateMergeModeControls(); } catch (_) { }
    try { await refreshOutputPathForNamingPrefs(); } catch (_) { }
    try { refreshCustomExtList(); } catch (_) { }
    try { updateActiveFiltersSummary(); } catch (_) { }
  } catch (err) {
    alert('Failed to load settings: ' + err);
  }
}

async function saveSettings() {
  try { if (!window.__SETTINGS_LOADED) await loadSettings(); } catch (_) { }

  let base = window.__CURRENT_SETTINGS || {};
  try {
    const res0 = await fetch(_settingsUrl());
    const d0 = await res0.json();
    if (res0.ok && d0 && typeof d0 === 'object') {
      base = d0;
      try { window.__CURRENT_SETTINGS = d0; } catch (_) { }
    }
  } catch (_) { }

  const useDom = !!window.__SETTINGS_LOADED;
  const getText = (id, fallback) => {
    if (!useDom) return String((fallback !== undefined && fallback !== null) ? fallback : '');
    const el = document.getElementById(id);
    if (!el) return String((fallback !== undefined && fallback !== null) ? fallback : '');
    return String((el.value !== undefined && el.value !== null) ? el.value : '');
  };
  const getChecked = (id, fallback) => {
    if (!useDom) return !!fallback;
    const el = document.getElementById(id);
    if (!el) return !!fallback;
    return !!el.checked;
  };
  const getInt = (id, fallback) => {
    if (!useDom) return (typeof fallback === 'number' && isFinite(fallback)) ? fallback : 0;
    const el = document.getElementById(id);
    if (!el) return (typeof fallback === 'number' && isFinite(fallback)) ? fallback : 0;
    const n = parseInt(String((el.value !== undefined && el.value !== null) ? el.value : ''), 10);
    return isFinite(n) ? n : ((typeof fallback === 'number' && isFinite(fallback)) ? fallback : 0);
  };
  const linesToList = (raw) => String(raw || '').split(/\r?\n/).map(s => s.trim()).filter(Boolean);
  const getList = (id, fallback) => linesToList(getText(id, (fallback || []).join('\n')));
  const getListFromSelect = (selectId, fallback) => {
    const sel = document.getElementById(selectId);
    if (!sel) return null;
    if (!window.__EXCLUSIONS_READY) return null;
    const values = Array.from(sel.options || [])
      .filter(o => o && !o.disabled && String(o.value || '').trim())
      .map(o => String(o.value || '').trim());
    if (values.length) return values;
    return [];
  };
  const getJsonMap = (id, fallback) => {
    const raw = getText(id, JSON.stringify(fallback || {}, null, 2));
    const trimmed = String(raw || '').trim();
    if (!trimmed) return {};
    try { return JSON.parse(trimmed); } catch (e) { alert("Error: Invalid JSON in Custom Extension Map.\nPlease fix it before saving."); throw e; }
  };

  let payload = null;
  try {
    payload = {
      always_include_docs_list: getList('s_always_docs', base.always_include_docs_list || []),
      custom_ext_map: getJsonMap('s_ext_map', base.custom_ext_map || {}),
      excluded_dirs: getListFromSelect('ex_dirs_list', null) || getList('s_ex_dirs', base.excluded_dirs || []),
      excluded_files: getListFromSelect('ex_files_list', null) || getList('s_ex_files', base.excluded_files || []),
      custom_excluded: getListFromSelect('ex_custom_list', null) || getList('s_custom_ex', base.custom_excluded || []),
      groq_use_batch: !!base.groq_use_batch,
      batch_progress: !!base.batch_progress,
      groq_strict_mode: getChecked('s_strict', (base.groq_strict_mode !== undefined) ? base.groq_strict_mode : true),
      groq_max_workers: getInt('s_workers', base.groq_max_workers || 0),
      summary_token_limit: getInt('s_sum_limit', base.summary_token_limit || 0),
      groq_request_timeout_seconds: parseFloat(String(getText('s_timeout', (base.groq_request_timeout_seconds !== undefined) ? base.groq_request_timeout_seconds : 120.0))) || ((base.groq_request_timeout_seconds !== undefined) ? base.groq_request_timeout_seconds : 120.0),
      groq_summary_retry_budget: getInt('s_retry_budget', (base.groq_summary_retry_budget !== undefined) ? base.groq_summary_retry_budget : 2),
      summary_default_mode: getText('s_summary_default_mode', base.summary_default_mode || 'CODE'),
      auto_refresh_summaries: getChecked('s_auto_refresh_summaries', (base.auto_refresh_summaries !== undefined) ? base.auto_refresh_summaries : true),
      summarize_vendored: getChecked('s_summarize_vendored', base.summarize_vendored),
      fallback_to_code_on_summary_error: getChecked('s_fallback_to_code', (base.fallback_to_code_on_summary_error !== undefined) ? base.fallback_to_code_on_summary_error : false),
      strict_summary_required: getChecked('s_strict_summary_required', !!base.strict_summary_required),
      summary_change_strategy: getText('s_summary_change_strategy', base.summary_change_strategy || 'mtime_size'),
      summary_prompt_version: getInt('s_summary_prompt_version', base.summary_prompt_version || 7),
      embedding_provider: getText('s_embed', base.embedding_provider || 'groq'),
      retrieval_backend: getText('s_retr', base.retrieval_backend || 'embeddings'),
      ui_font_size: getInt('s_font', base.ui_font_size || 10),
      ui_high_contrast: getChecked('s_contrast', base.ui_high_contrast),
      ui_compact_mode: getChecked('s_compact', base.ui_compact_mode),
      ui_watch_debounce_ms: getInt('s_watch_debounce', base.ui_watch_debounce_ms || 800),
      watch_sample_limit: getInt('s_watch_sample_limit', base.watch_sample_limit || 3000),
      use_folder_name: getChecked('use_folder_name', base.use_folder_name),
      append_datetime: getChecked('append_date', base.append_datetime),
      footer_final_only: getChecked('toggle_final_footer', base.footer_final_only),
      show_bytes_footer: getChecked('toggle_bytes_footer', (base.show_bytes_footer !== undefined) ? base.show_bytes_footer : true),
      show_totals_columns: getChecked('toggle_totals', base.show_totals_columns),
      show_density_column: getChecked('toggle_density', base.show_density_column),
      include_file_tree: getChecked('opt_tree', base.include_file_tree),
      include_json_manifest: getChecked('opt_manifest', base.include_json_manifest),
      output_markdown_fences: getChecked('opt_fences', base.output_markdown_fences),
      output_markdown_toc: getChecked('opt_toc', base.output_markdown_toc),
      remove_empty: getChecked('opt_strip', base.remove_empty),
      enumerate_lines: getChecked('opt_enum', base.enumerate_lines),
      apply_token_filters_to_transforms: getChecked('f_apply_token_transforms', base.apply_token_filters_to_transforms),
      content_mode: getText('merge_content_mode', base.content_mode || 'code_only'),
      structure_mode: (() => {
        const raw = getText('merge_structure', base.structure_mode || 'single_file');
        if (raw === 'multiple') return 'split_lines';
        if (raw === 'token_split') return 'split_tokens';
        return 'single_file';
      })(),
      split_into_n: getInt('merge_split_n', base.split_into_n || 5),
      token_limit: getInt('merge_token_limit', base.token_limit || 1048576),
      prompt_template: getText('merge_prompt_template', base.prompt_template || ''),
      custom_start_label: getText('s_custom_start', base.custom_start_label || ''),
      custom_end_label: getText('s_custom_end', base.custom_end_label || ''),
      custom_tree_start_label: getText('s_custom_tree_start', base.custom_tree_start_label || ''),
      custom_tree_end_label: getText('s_custom_tree_end', base.custom_tree_end_label || ''),
    };
  } catch (_) {
    // JSON validation already alerted; abort save.
    return;
  }
  try {
    const res = await fetch('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(_settingsPayload(payload)) });
    const data = await res.json();
    if (!res.ok) { alert(data.error || 'Failed to save'); return; }
    try { if (data && data.settings) window.__CURRENT_SETTINGS = data.settings; } catch (_) { }
    try { applyExclusionsToUi(payload); } catch (_) { }
    alert('Settings saved');
    // Apply UI immediately
    try {
      if (typeof payload.ui_font_size === 'number' && payload.ui_font_size > 0) document.documentElement.style.fontSize = payload.ui_font_size + 'px';
      if (payload.ui_high_contrast) document.body.classList.add('contrast'); else document.body.classList.remove('contrast');
      if (payload.ui_compact_mode) document.body.classList.add('compact'); else document.body.classList.remove('compact');
      const tf = document.getElementById('toggle_final_footer'); if (tf && typeof payload.footer_final_only === 'boolean') tf.checked = payload.footer_final_only;
      const tb = document.getElementById('toggle_bytes_footer'); if (tb && typeof payload.show_bytes_footer === 'boolean') tb.checked = payload.show_bytes_footer;
      const tt = document.getElementById('toggle_totals'); if (tt && typeof payload.show_totals_columns === 'boolean') tt.checked = payload.show_totals_columns;
      const td = document.getElementById('toggle_density'); if (td && typeof payload.show_density_column === 'boolean') td.checked = payload.show_density_column;
      window.__SHOW_DENS = !!payload.show_density_column;
      document.body.classList.toggle('hide-totals', !payload.show_totals_columns);
      document.body.classList.toggle('hide-density', !payload.show_density_column);
      if (typeof payload.watch_sample_limit === 'number' && payload.watch_sample_limit > 0) {
        window.__WATCH_SAMPLE_LIMIT = payload.watch_sample_limit;
      }
      if (typeof payload.auto_refresh_summaries === 'boolean') {
        window.__AUTO_REFRESH_SUMMARIES = payload.auto_refresh_summaries;
      }
      if (payload.summary_default_mode) {
        window.__DEFAULT_MODE = String(payload.summary_default_mode).toUpperCase();
      }
      try { updateMergeModeControls(); } catch (_) { }
      updateScanStatsText();
    } catch (_) { }
  } catch (err) {
    alert('Save failed: ' + err);
  }
}

async function resetExcludedDirs() {
  try {
    if (!confirm('Reset excluded directories to defaults?')) return;
    const res = await fetch('/api/settings/excluded/reset', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(_settingsPayload({ dirs: true, files: false, custom: false })) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    const files = _readListOptions('ex_files_list') || (data.excluded_files || []);
    const custom = _readListOptions('ex_custom_list') || (data.custom_excluded || []);
    applyExclusionsToUi({ excluded_dirs: data.excluded_dirs || [], excluded_files: files, custom_excluded: custom });
    showBanner('Excluded dirs reset to defaults.', '');
    setTimeout(hideBanner, 2000);
    requestScan(100);
  } catch (err) {
    showError('Reset Excluded Dirs Failed', err && err.message ? err.message : err);
  }
}

async function resetExcludedFiles() {
  try {
    if (!confirm('Reset excluded files to defaults?')) return;
    const res = await fetch('/api/settings/excluded/reset', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(_settingsPayload({ dirs: false, files: true, custom: false })) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    const dirs = _readListOptions('ex_dirs_list') || (data.excluded_dirs || []);
    const custom = _readListOptions('ex_custom_list') || (data.custom_excluded || []);
    applyExclusionsToUi({ excluded_dirs: dirs, excluded_files: data.excluded_files || [], custom_excluded: custom });
    showBanner('Excluded files reset to defaults.', '');
    setTimeout(hideBanner, 2000);
    requestScan(100);
  } catch (err) {
    showError('Reset Excluded Files Failed', err && err.message ? err.message : err);
  }
}

async function resetExcludedAll() {
  try {
    if (!confirm('Reset excluded dirs/files and custom excludes to defaults?')) return;
    const res = await fetch('/api/settings/excluded/reset', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(_settingsPayload({ dirs: true, files: true, custom: true })) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    applyExclusionsToUi({ excluded_dirs: data.excluded_dirs || [], excluded_files: data.excluded_files || [], custom_excluded: data.custom_excluded || [] });
    showBanner('Excluded lists reset.', '');
    setTimeout(hideBanner, 2000);
    requestScan(100);
  } catch (err) {
    showError('Reset Excluded Lists Failed', err && err.message ? err.message : err);
  }
}

async function resetAlwaysIncludeDocs() {
  try {
    if (!confirm('Reset Always-Include docs list to defaults?')) return;
    const res = await fetch('/api/settings/always_include/reset', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(_settingsPayload({})) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    const el = document.getElementById('s_always_docs'); if (el) el.value = (data.always_include_docs_list || []).join('\n');
    showBanner('Always-Include list reset.', '');
    setTimeout(hideBanner, 2000);
    requestFilterApply(100);
  } catch (err) {
    showError('Reset Always-Include Failed', err && err.message ? err.message : err);
  }
}

async function persistSettings(partial) {
  try {
    await fetch('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(_settingsPayload(partial)) });
  } catch (_) { }
}

// Remove all persisted excludes (global). Simpler approach: clear all entries.
async function runDiagnostics() {
  try {
    const path = document.getElementById('last_directory').value;
    if (!path) { showError('Diagnostics', 'Set a source directory first.'); return; }
    try { showOverlay('Running diagnostics…'); } catch (_) { }
    const payload = buildScanPayload(path);
    const res = await fetch('/api/diagnostics', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    const report = (data && typeof data.report === 'string') ? data.report : '';
    showDiagnosticsModal({ title: 'Diagnostics', meta: path, text: report });
  } catch (err) {
    showError('Diagnostics Failed', err && err.message ? err.message : err);
  } finally {
    try { hideOverlay(); } catch (_) { }
  }
}

function _summarizeContentMode() {
  const pref = document.getElementById('s_summary_default_mode');
  const raw = pref ? String(pref.value || '').toUpperCase() : '';
  const map = {
    CODE: 'code_only',
    SKELETON: 'skeleton_only',
    API_SURFACE: 'skeleton_only',
    SIGNATURES: 'skeleton_only',
    CODE_PLUS_SKELETON: 'code_plus_skeleton',
    CODE_PLUS_SUMMARY_SHORT_PLUS_SKELETON: 'code_plus_short_plus_skeleton',
    CODE_PLUS_SUMMARY_LONG_PLUS_SKELETON: 'code_plus_long_plus_skeleton',
    SUMMARY_SHORT: 'short_only',
    SUMMARY_LONG: 'long_only',
    SUMMARY_SHORT_PLUS_SKELETON: 'short_plus_skeleton',
    SUMMARY_LONG_PLUS_SKELETON: 'long_plus_skeleton',
    CODE_PLUS_SHORT: 'code_plus_short',
    CODE_PLUS_LONG: 'code_plus_long',
  };
  if (map[raw]) return map[raw];
  const mergeMode = document.getElementById('merge_content_mode');
  return (mergeMode && mergeMode.value) ? String(mergeMode.value) : 'code_plus_short';
}

async function summarizeSelected() {
  const out = document.getElementById('summarize_out');
  try {
    const root = (window.__SCAN && window.__SCAN.root) || document.getElementById('last_directory').value;
    if (!root) { showBanner('Set a source directory first.', 'warn'); return; }
    let selected = Array.from(window.__SEL || []);
    selected = filterNonExcludedPaths(selected);
    if (!selected.length) {
      showBanner('Select file(s) to summarize.', 'warn');
      return;
    }
    if (out) out.textContent = 'Starting summarize job…';
    const content_mode = _summarizeContentMode();
    const payload = await window.providerConfirmedPayload('summarize', { path: root, selected_paths: selected, content_mode });
    const res = await fetch('/api/summarize', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    const jobId = data.job_id;
    pollJobUntilDone(jobId, {
      label: 'Summarizing…',
      onProgress: (job) => { if (out) out.textContent = JSON.stringify(job, null, 2); },
      onDone: (job) => {
        const result = job && job.result ? job.result : {};
        if (out) out.textContent = JSON.stringify(result, null, 2);
        if (result && result.errors) updateSummaryErrors(result.errors, selected);
        showBanner('Summaries refreshed.', '');
        setTimeout(hideBanner, 2000);
      },
      onError: (job) => {
        const err = (job && job.error) ? job.error : job;
        if (out) out.textContent = 'Summarize failed: ' + err;
        showError('Summarize Failed', err && err.message ? err.message : err);
      },
    });
  } catch (err) {
    if (out) out.textContent = 'Summarize failed: ' + err;
    showError('Summarize Failed', err && err.message ? err.message : err);
  }
}

function folderSummaryModeValue(mode) {
  const raw = String(mode || '').trim().toUpperCase();
  if (raw === 'FOLDER_SUMMARY_FULL' || raw === 'FULL') return 'FOLDER_SUMMARY_FULL';
  if (raw === 'FOLDER_SUMMARY_QUICK' || raw === 'FOLDER_SUMMARY' || raw === 'QUICK') return 'FOLDER_SUMMARY_QUICK';
  return '';
}

async function toggleFolderSummary(folderAbs, isSet, mode) {
  const abs = String(folderAbs || '').trim();
  if (!abs) return;
  try {
    const payload = { paths: [abs] };
    if (!isSet) {
      const resolved = folderSummaryModeValue(mode) || 'FOLDER_SUMMARY_QUICK';
      payload.mode = resolved;
    }
    const res = await fetch('/api/folder_modes', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    window.__FOLDER_MODES = (data && data.folder_modes && typeof data.folder_modes === 'object') ? data.folder_modes : {};
    renderTable();
    showBanner(isSet ? 'Folder summary cleared.' : 'Folder summary enabled.', '');
    setTimeout(hideBanner, 1200);
  } catch (err) {
    showError('Folder Summary Mode Failed', err && err.message ? err.message : err);
  }
}

async function setFolderSummaryMode(folderAbs, mode) {
  return toggleFolderSummary(folderAbs, false, mode);
}

async function previewFolderSummary(folderAbs, mode) {
  const abs = String(folderAbs || '').trim();
  if (!abs) { showBanner('Folder path missing.', 'warn'); return; }
  try {
    const variant = _normalizeSummaryVariant(mode);
    const payload = await window.providerConfirmedPayload('summarize_folder', { path: abs, mode: variant });
    const res = await fetch('/api/summarize_folder', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    const jobId = data.job_id;
    pollJobUntilDone(jobId, {
      label: `Summarizing folder (${variant})...`,
      onDone: (job) => {
        const result = job && job.result ? job.result : {};
        const text = (result && typeof result.summary_text === 'string') ? result.summary_text : '';
        showPreviewModal({ title: `Folder Summary (${variant})`, meta: abs, text, file_path: '' });
      },
      onError: (job) => {
        const err = (job && job.error) ? job.error : job;
        showError('Folder Summary Failed', err && err.message ? err.message : err);
      },
    });
  } catch (err) {
    showError('Folder Summary Failed', err && err.message ? err.message : err);
  }
}

function _normalizeSummaryVariant(mode) {
  const raw = String(mode || '').toLowerCase();
  return raw === 'quick' ? 'quick' : 'full';
}

async function previewRepoSummaryVariant(mode) {
  const root = _currentRootPath();
  if (!root) { showBanner('Set a source directory first.', 'warn'); return; }
  try {
    const variant = _normalizeSummaryVariant(mode);
    const payload = await window.providerConfirmedPayload('summarize_repo', { mode: variant });
    const res = await fetch('/api/summarize_repo', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    const jobId = data.job_id;
    pollJobUntilDone(jobId, {
      label: `Summarizing repo (${variant})...`,
      onDone: (job) => {
        const result = job && job.result ? job.result : {};
        const text = (result && typeof result.summary_text === 'string') ? result.summary_text : '';
        showPreviewModal({ title: `Repo Summary (${variant})`, meta: root, text, file_path: '' });
      },
      onError: (job) => {
        const err = (job && job.error) ? job.error : job;
        showError('Repo Summary Failed', err && err.message ? err.message : err);
      },
    });
  } catch (err) {
    showError('Repo Summary Failed', err && err.message ? err.message : err);
  }
}

async function openSummarizerLogs() {
  const modal = document.getElementById('logs_modal');
  if (modal) modal.style.display = 'flex';
  await refreshSummarizerLogs();
}

async function refreshSummarizerLogs() {
  const out = document.getElementById('logs_text');
  if (out) out.textContent = 'Loading logs…';
  try {
    const tailEl = document.getElementById('logs_tail');
    const tail = tailEl ? parseInt(String(tailEl.value || '1000'), 10) : 1000;
    const res = await fetch('/api/logs/summarizer?tail=' + encodeURIComponent(isFinite(tail) ? tail : 1000));
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    const lines = Array.isArray(data.lines) ? data.lines : [];
    if (out) out.textContent = lines.join('\n');
    const meta = document.getElementById('logs_meta');
    if (meta) meta.textContent = data.path ? `File: ${data.path}` : '';
  } catch (err) {
    if (out) out.textContent = 'Failed to load logs: ' + err;
  }
}

function closeSummarizerLogs() {
  const modal = document.getElementById('logs_modal');
  if (modal) modal.style.display = 'none';
}

function copySummarizerLogs() {
  const out = document.getElementById('logs_text');
  if (!out) return;
  try { navigator.clipboard.writeText(String(out.textContent || '')); } catch (_) { }
}

function _previewTextValue() {
  if (typeof window.__PREVIEW_TEXT === 'string') return window.__PREVIEW_TEXT;
  const pre = document.getElementById('preview_text');
  return pre ? String(pre.textContent || '') : '';
}

function selectPreviewAll() {
  const pre = document.getElementById('preview_text');
  if (!pre) return;
  try {
    const range = document.createRange();
    range.selectNodeContents(pre);
    const sel = window.getSelection();
    if (!sel) return;
    sel.removeAllRanges();
    sel.addRange(range);
  } catch (_) { }
}

async function copyPreviewSelectionOrAll() {
  try {
    const sel = window.getSelection();
    const selected = sel ? String(sel.toString() || '') : '';
    const text = selected || _previewTextValue();
    if (!text) return;
    await copyTextToClipboard(text);
  } catch (_) { }
}

async function copyPreviewText() {
  const text = _previewTextValue();
  if (!text) return;
  const ok = await copyTextToClipboard(text);
  try { showBanner(ok ? 'Preview copied' : 'Copy failed', ok ? '' : 'warn'); setTimeout(hideBanner, 1200); } catch (_) { }
}

function _sanitizeFilename(name) {
  const raw = String(name || '').split(/[\\/]/).pop();
  let safe = raw.replace(/[^a-zA-Z0-9._-]/g, '_').replace(/_+/g, '_');
  if (!safe) safe = 'preview.txt';
  if (!/\.[A-Za-z0-9]+$/.test(safe)) safe += '.txt';
  return safe;
}

function savePreviewText() {
  const text = _previewTextValue();
  if (!text) return;
  const name = _sanitizeFilename(window.__PREVIEW_SAVE_NAME || 'preview.txt');
  try {
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { try { URL.revokeObjectURL(url); } catch (_) { } }, 0);
    document.body.removeChild(a);
  } catch (_) { }
}

function showPreviewModal(opts) {
  const title = opts && opts.title ? String(opts.title) : 'Preview';
  const meta = opts && opts.meta ? String(opts.meta) : '';
  const text = opts && typeof opts.text === 'string' ? opts.text : '';
  const file_path = opts && opts.file_path ? String(opts.file_path) : '';

  try {
    const titleEl = document.getElementById('preview_title'); if (titleEl) titleEl.textContent = title;
    const metaEl = document.getElementById('preview_meta'); if (metaEl) metaEl.textContent = meta;
    const pre = document.getElementById('preview_text'); if (pre) pre.textContent = text || '';
    const modal = document.getElementById('preview_modal');
    const inToolsPanel = (location && typeof location.pathname === 'string' && location.pathname.indexOf('/panel/tools') !== -1);
    if (modal && !inToolsPanel) modal.style.display = 'flex';
    window.__PREVIEW_OPEN_PATH = file_path;
    window.__PREVIEW_TEXT = text || '';
    const base = file_path ? file_path.split(/[\\/]/).pop() : '';
    window.__PREVIEW_SAVE_NAME = base || (title.toLowerCase().includes('merge') ? 'merge_preview.txt' : 'preview.txt');
    const openBtn = document.getElementById('preview_open_external');
    if (openBtn) openBtn.style.display = file_path ? 'inline-block' : 'none';
  } catch (_) { }
}

function hidePreviewModal() {
  try {
    const modal = document.getElementById('preview_modal'); if (modal) modal.style.display = 'none';
    window.__PREVIEW_OPEN_PATH = null;
    window.__PREVIEW_TEXT = null;
    window.__PREVIEW_SAVE_NAME = null;
  } catch (_) { }
}

function showDiagnosticsModal(opts) {
  const title = opts && opts.title ? String(opts.title) : 'Diagnostics';
  const meta = opts && opts.meta ? String(opts.meta) : '';
  const text = opts && typeof opts.text === 'string' ? opts.text : '';
  try {
    const t = document.getElementById('diagnostics_title'); if (t) t.textContent = title;
    const m = document.getElementById('diagnostics_meta'); if (m) m.textContent = meta;
    const pre = document.getElementById('diagnostics_text'); if (pre) pre.textContent = text || '';
    const modal = document.getElementById('diagnostics_modal'); if (modal) modal.style.display = 'flex';
    window.__DIAG_TEXT = text || '';
  } catch (_) { }
}

function hideDiagnosticsModal() {
  try {
    const modal = document.getElementById('diagnostics_modal'); if (modal) modal.style.display = 'none';
  } catch (_) { }
}

function copyDiagnosticsText() {
  const text = (typeof window.__DIAG_TEXT === 'string') ? window.__DIAG_TEXT : '';
  if (!text) return;
  try { navigator.clipboard.writeText(text); } catch (_) { }
}

async function openPreviewExternally() {
  const p = window.__PREVIEW_OPEN_PATH;
  if (!p || typeof p !== 'string') return;
  try {
    await fetch('/api/open', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: p }) });
  } catch (err) {
    alert('Open failed: ' + err);
  }
}

// Apply UI settings (font size, contrast, footer toggles) from server
async function applyUiSettingsFromServer() {
  try {
    const res = await fetch(_settingsUrl());
    const data = await res.json();
    if (!res.ok) return;
    // Font size
    const size = parseInt(data.ui_font_size || 10, 10);
    if (!isNaN(size) && size > 0) document.documentElement.style.fontSize = size + 'px';
    // Contrast
    if (data.ui_high_contrast) document.body.classList.add('contrast'); else document.body.classList.remove('contrast');
    // Compact mode
    if (data.ui_compact_mode) document.body.classList.add('compact'); else document.body.classList.remove('compact');
    // Column toggles
    const tt = document.getElementById('toggle_totals'); if (tt) tt.checked = !!data.show_totals_columns;
    const td = document.getElementById('toggle_density'); if (td) td.checked = !!data.show_density_column;
    window.__SHOW_DENS = !!data.show_density_column;
    document.body.classList.toggle('hide-totals', !data.show_totals_columns);
    document.body.classList.toggle('hide-density', !data.show_density_column);
    // Footer toggles
    const tf = document.getElementById('toggle_final_footer'); if (tf) tf.checked = !!data.footer_final_only;
    const tb = document.getElementById('toggle_bytes_footer'); if (tb) tb.checked = !!((data.show_bytes_footer !== undefined) ? data.show_bytes_footer : true);
    // Merge naming (dashboard footer)
    const uf = document.getElementById('use_folder_name'); if (uf && typeof data.use_folder_name === 'boolean') uf.checked = data.use_folder_name;
    const ad = document.getElementById('append_date'); if (ad && typeof data.append_datetime === 'boolean') ad.checked = data.append_datetime;
    // Merge defaults
    const cm = document.getElementById('merge_content_mode'); if (cm && data.content_mode) cm.value = data.content_mode;
    const ms = document.getElementById('merge_structure');
    if (ms && data.structure_mode) {
      const map = { single_file: 'single', split_lines: 'multiple', split_tokens: 'token_split' };
      ms.value = map[data.structure_mode] || 'single';
    }
    const sn = document.getElementById('merge_split_n'); if (sn && typeof data.split_into_n === 'number') sn.value = String(data.split_into_n);
    const tl = document.getElementById('merge_token_limit'); if (tl && typeof data.token_limit === 'number') tl.value = String(data.token_limit);
    const optTree = document.getElementById('opt_tree'); if (optTree && data.include_file_tree !== undefined) optTree.checked = !!data.include_file_tree;
    const optManifest = document.getElementById('opt_manifest'); if (optManifest && data.include_json_manifest !== undefined) optManifest.checked = !!data.include_json_manifest;
    const optFences = document.getElementById('opt_fences'); if (optFences && data.output_markdown_fences !== undefined) optFences.checked = !!data.output_markdown_fences;
    const optToc = document.getElementById('opt_toc'); if (optToc && data.output_markdown_toc !== undefined) optToc.checked = !!data.output_markdown_toc;
    const optStrip = document.getElementById('opt_strip'); if (optStrip && data.remove_empty !== undefined) optStrip.checked = !!data.remove_empty;
    const optEnum = document.getElementById('opt_enum'); if (optEnum && data.enumerate_lines !== undefined) optEnum.checked = !!data.enumerate_lines;
    const prompt = document.getElementById('merge_prompt_template'); if (prompt && data.prompt_template !== undefined) prompt.value = String(data.prompt_template || '');
    const st = document.getElementById('s_custom_start'); if (st && data.custom_start_label) st.value = String(data.custom_start_label);
    const se = document.getElementById('s_custom_end'); if (se && data.custom_end_label) se.value = String(data.custom_end_label);
    const ts = document.getElementById('s_custom_tree_start'); if (ts && data.custom_tree_start_label) ts.value = String(data.custom_tree_start_label);
    const te = document.getElementById('s_custom_tree_end'); if (te && data.custom_tree_end_label) te.value = String(data.custom_tree_end_label);
    const trans = document.getElementById('f_apply_token_transforms'); if (trans && data.apply_token_filters_to_transforms !== undefined) trans.checked = !!data.apply_token_filters_to_transforms;
    try { updateMergeModeControls(); } catch (_) { }
    try { await refreshOutputPathForNamingPrefs(); } catch (_) { }
    // Watch debounce for rescan throttling
    if (typeof data.ui_watch_debounce_ms === 'number' && data.ui_watch_debounce_ms >= 0) {
      window.__WATCH_DEBOUNCE_MS = data.ui_watch_debounce_ms;
    }
    if (typeof data.watch_sample_limit === 'number' && data.watch_sample_limit > 0) {
      window.__WATCH_SAMPLE_LIMIT = data.watch_sample_limit;
      const ws = document.getElementById('s_watch_sample_limit'); if (ws) ws.value = String(data.watch_sample_limit);
    }
    if (typeof data.auto_refresh_summaries === 'boolean') {
      window.__AUTO_REFRESH_SUMMARIES = data.auto_refresh_summaries;
    }
    if (data.summary_default_mode) {
      window.__DEFAULT_MODE = String(data.summary_default_mode).toUpperCase();
    }
    updateScanStatsText();
    try { renderTable(); } catch (_) { }
  } catch (_) { }
}

// Simple error banner helpers
function showBanner(msg, type) {
  const b = document.getElementById('banner'); if (!b) return;
  b.textContent = msg; b.className = 'banner' + (type ? (' ' + type) : ''); b.style.display = 'block';
}
function hideBanner() { const b = document.getElementById('banner'); if (b) b.style.display = 'none'; }

function setToolsOutput(message) {
  const out = document.getElementById('tools_out');
  if (out) out.textContent = String(message || '');
}

async function pairCodebases() {
  const a = (document.getElementById('pair_a') || {}).value || '';
  const b = (document.getElementById('pair_b') || {}).value || '';
  const dest = (document.getElementById('pair_dest') || {}).value || '';
  const keep = !!(document.getElementById('pair_keep') && document.getElementById('pair_keep').checked);
  const pairSelected = !!(document.getElementById('pair_selected') && document.getElementById('pair_selected').checked);
  const labelA = (document.getElementById('pair_label_a') || {}).value || '';
  const labelB = (document.getElementById('pair_label_b') || {}).value || '';
  if (!a || !b || !dest) { alert('Provide A, B, and Dest paths.'); return; }
  setToolsOutput('Pairing codebases…');
  let selected_rel_paths = [];
  if (pairSelected) {
    const sel = Array.from(window.__SEL || []);
    if (!sel.length) { alert('Select files to pair from A.'); return; }
    const root = a.replace(/[\\\/]+$/, '');
    selected_rel_paths = sel.map(p => {
      if (!p || typeof p !== 'string') return '';
      if (p.startsWith(root + '/') || p.startsWith(root + '\\')) return p.slice(root.length + 1);
      return p;
    }).filter(Boolean);
    if (!selected_rel_paths.length) { alert('No valid selected paths.'); return; }
  }
  try {
    const payload = {
      src_a: a,
      src_b: b,
      dest_dir: dest,
      keep_structure: keep,
      pair_selected: pairSelected,
      selected_rel_paths,
      label_a: labelA || undefined,
      label_b: labelB || undefined,
    };
    const res = await fetch('/api/pair_codebases', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    const msg = 'Paired into: ' + (data.dest || dest);
    setToolsOutput(msg);
    alert(msg);
  } catch (err) {
    const msg = 'Pair failed: ' + err;
    setToolsOutput(msg);
    alert(msg);
  }
}

async function runPairAi() {
  const out = document.getElementById('pair_ai_out');
  const a = (document.getElementById('pair_ai_a') || {}).value || (document.getElementById('pair_a') || {}).value || '';
  const b = (document.getElementById('pair_ai_b') || {}).value || (document.getElementById('pair_b') || {}).value || '';
  if (!a || !b) { alert('Provide Source A and Source B for Pair+AI.'); return; }
  if (out) out.textContent = 'Running Pair+AI…';
  try {
    try { showOverlay('Running Pair+AI…'); } catch (_) { }
    const payload = await window.providerConfirmedPayload('pair_ai', { src_a: a, src_b: b });
    const res = await fetch('/api/pair_ai', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    const report = (data && typeof data.report === 'string') ? data.report : '';
    if (out) out.textContent = report || 'No report text returned.';
  } catch (err) {
    if (out) out.textContent = 'Pair+AI failed: ' + err;
    showError('Pair+AI Failed', err && err.message ? err.message : err);
  } finally {
    try { hideOverlay(); } catch (_) { }
  }
}

async function savePairAiReport() {
  const out = document.getElementById('pair_ai_out');
  const a = (document.getElementById('pair_ai_a') || {}).value || (document.getElementById('pair_a') || {}).value || '';
  const b = (document.getElementById('pair_ai_b') || {}).value || (document.getElementById('pair_b') || {}).value || '';
  if (!a || !b) { alert('Provide Source A and Source B for Pair+AI.'); return; }
  let output_file = '';
  const root = document.getElementById('last_directory').value || '';
  const defaultPath = root ? (root.replace(/[\\\/]+$/, '') + '/pair_ai_report.txt') : 'pair_ai_report.txt';
  try {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.save_file_dialog) {
      const chosen = await window.pywebview.api.save_file_dialog(defaultPath || null);
      if (chosen && typeof chosen === 'string') output_file = chosen;
    }
  } catch (_) { }
  if (!output_file) {
    const entered = prompt('Save Pair+AI report to:', defaultPath);
    if (entered && typeof entered === 'string') output_file = entered;
  }
  if (!output_file) return;
  try {
    if (out) out.textContent = 'Saving Pair+AI report…';
    try { showOverlay('Saving Pair+AI report…'); } catch (_) { }
    const payload = await window.providerConfirmedPayload('pair_ai', { src_a: a, src_b: b, output_file });
    const res = await fetch('/api/pair_ai', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    if (out) out.textContent = data.report || 'Report saved.';
    if (data.output_path) {
      await allowOutputPath(data.output_path);
      showBanner(`Report saved: ${data.output_path}`, '');
      setTimeout(hideBanner, 2500);
    }
  } catch (err) {
    if (out) out.textContent = 'Pair+AI save failed: ' + err;
    showError('Pair+AI Save Failed', err && err.message ? err.message : err);
  } finally {
    try { hideOverlay(); } catch (_) { }
  }
}

async function restoreFromMerged() {
  const src = (document.getElementById('rest_src') || {}).value || '';
  const dest = (document.getElementById('rest_dest') || {}).value || '';
  if (!src || !dest) { alert('Provide Source and Dest.'); return; }
  setToolsOutput('Restoring from merged file…');
  try {
    const custom_start_label = (document.getElementById('rest_start_label') || {}).value || '';
    const custom_end_label = (document.getElementById('rest_end_label') || {}).value || '';
    const res = await fetch('/api/restore', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        source: src,
        dest_dir: dest,
        custom_start_label: custom_start_label || undefined,
        custom_end_label: custom_end_label || undefined,
      })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    const msg = 'Restored into: ' + (data.dest || dest);
    setToolsOutput(msg);
    alert(msg);
  } catch (err) {
    const msg = 'Restore failed: ' + err;
    setToolsOutput(msg);
    alert(msg);
  }
}


for (const button of document.querySelectorAll('[data-settings-tab]')) {
  button.addEventListener('click', () => switchTab(button.dataset.settingsTab, button));
}
for (const [action, handler] of Object.entries({
  close: closeSettingsModal, load: loadSettings, save: saveSettings,
})) {
  for (const button of document.querySelectorAll(`[data-settings-action="${action}"]`)) {
    button.addEventListener('click', handler);
  }
}
for (const control of document.querySelectorAll('[data-settings-preview]')) {
  control.addEventListener('change', applyUiPreview);
}
