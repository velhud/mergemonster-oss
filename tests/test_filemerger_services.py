from __future__ import annotations

from pathlib import Path

from codemerge.models import Category, FileMeta, ScanContext
from codemerge.filemerger.services import git_service, merge_service, scan_service, watch_service
from codemerge.filemerger.services.dto import FilterOptions, MergeStructure, OutputOptions, ScanOptions, SummaryOptions, UiState


def make_ui_state(root: Path, *, footer_final_only: bool = False) -> UiState:
    scan = ScanOptions(
        include_all=True,
        ignore_binary=False,
        max_file_size_mb=0.0,
        sort_by="name",
        sort_desc=False,
        max_depth=0,
        follow_symlinks=False,
        use_git_files=False,
        git_include_untracked=False,
    )
    filters = FilterOptions(
        category_mask={c: True for c in Category},
        token_min=0,
        token_max=10_000_000,
        size_min_kb=0.0,
        size_max_kb=100_000.0,
        name_pattern="",
        name_glob=False,
        content_search="",
        content_regex=False,
        content_case=False,
        content_max_kb=256,
        date_after_ts=0.0,
        date_before_ts=0.0,
        hide_duplicates=False,
        mark_duplicates=True,
        apply_token_filters_to_transforms=False,
        enumerate_lines=False,
        remove_empty=False,
        xcode_target="(All)",
        always_include_docs=True,
        always_include_list=[],
    )
    output = OutputOptions(
        include_tree=True,
        json_manifest=False,
        custom_start="======= START OF FILE: {file_display} =======",
        custom_end="======= END OF FILE: {file_display} =======",
        tree_start="======= FILE TREE =======",
        tree_end="======= END OF FILE TREE =======",
        markdown_fences=False,
        markdown_toc=False,
        footer_final_only=footer_final_only,
    )
    merge = MergeStructure(mode="single_file", split_into_n=5, token_limit=1_000_000)
    summary = SummaryOptions(
        content_mode="code_only",
        prompt_template="",
        groq_workers=0,
        use_batch=False,
        batch_progress=False,
    )
    return UiState(
        root_dir=root,
        output_file=root / "merged_files.txt",
        file_types={},
        view_mode="hierarchy",
        copy_to_clipboard=False,
        scan=scan,
        filters=filters,
        output=output,
        merge=merge,
        summary=summary,
    )


class DummyFileProcessor:
    def __init__(self, *, merge_result: str = "", tokens: tuple[int, int] | None = None, raise_merge: bool = False, raise_tokens: bool = False, vendored=None) -> None:
        self._merge_result = merge_result
        self._tokens = tokens or (0, 0)
        self._raise_merge = raise_merge
        self._raise_tokens = raise_tokens
        self._vendored = vendored if vendored is not None else []
        self.merge_calls: list[tuple[str, list[str]]] = []
        self.token_calls: list[list[str]] = []
        self.vendored_calls: list[tuple[str, list[str]]] = []
        self.excluded_dirs: set[str] = set()
        self.excluded_files: set[str] = set()

    def merge_files(self, directory: str, files_to_merge: list[str], output_file: str, progress_callback, include_file_tree: bool, include_summaries: bool, custom_start_label: str, custom_end_label: str, file_tree_start_label: str, file_tree_end_label: str, create_json_manifest: bool, summaries_by_file, content_mode: str, files_with_summary_errors, prompt_template: str, output_stream, remove_empty: bool, enumerate_lines: bool, markdown_fences: bool, markdown_toc: bool, **_kwargs) -> None:  # noqa: D401,E501
        """Capture merge invocation and stream fixed text."""
        if self._raise_merge:
            raise RuntimeError("merge boom")
        self.merge_calls.append((directory, files_to_merge))
        output_stream.write(self._merge_result)

    def merge_group_token_details(self, directory: str, files_to_merge: list[str], include_file_tree: bool, custom_start_label: str, custom_end_label: str, file_tree_start_label: str, file_tree_end_label: str, prompt_template: str, remove_empty: bool, enumerate_lines: bool, markdown_fences: bool, markdown_toc: bool, content_mode: str, **_kwargs) -> tuple[int, int]:  # noqa: E501
        if self._raise_tokens:
            raise RuntimeError("token boom")
        self.token_calls.append(files_to_merge)
        return self._tokens

    def git_vendored_set(self, root: str, rel_paths: list[str]):
        if isinstance(self._vendored, Exception):
            raise self._vendored
        self.vendored_calls.append((root, rel_paths))
        return list(self._vendored)

    def git_files(self, *_args, **_kw):  # pragma: no cover - not used in tests
        return []

    def is_virtual_env(self, _path: str) -> bool:
        return False


def test_scan_service_apply_filters_returns_inputs(tmp_path: Path) -> None:
    ui_state = make_ui_state(tmp_path)
    file_meta = FileMeta(
        abs_path=tmp_path / "demo.py",
        rel_path=Path("demo.py"),
        size_bytes=10,
        mtime=1.0,
        token_count=None,
        ext=".py",
        category=Category.BACKEND,
    )
    ctx = ScanContext(
        root=tmp_path,
        files=[file_meta],
        aggregate_tokens=0,
        aggregate_bytes=0,
        by_ext={},
        by_category={},
    )

    visible, folder_totals, dup_set, warnings, pattern_hits = scan_service.apply_filters(
        ctx,
        ui_state,
        DummyFileProcessor(),
        git_changed=None,
        vendored=None,
    )
    assert isinstance(pattern_hits, set)

    assert visible == [file_meta]
    assert folder_totals == {}
    assert dup_set == set()
    assert warnings == []


def test_scan_service_merge_totals_footer_text_handles_modes(tmp_path: Path) -> None:
    ui_state = make_ui_state(tmp_path)
    file_meta = FileMeta(
        abs_path=tmp_path / "demo.py",
        rel_path=Path("demo.py"),
        size_bytes=10,
        mtime=1.0,
        token_count=None,
        ext=".py",
        category=Category.BACKEND,
    )
    dummy_fp = DummyFileProcessor(tokens=(321, 123))

    text = scan_service.merge_totals_footer_text(dummy_fp, ui_state, [file_meta], content_mode_map=None)
    assert text == "Visible tokens: 123 (final: 321)"

    ui_footer_only = make_ui_state(tmp_path, footer_final_only=True)
    text_footer_only = scan_service.merge_totals_footer_text(dummy_fp, ui_footer_only, [file_meta], content_mode_map=None)
    assert text_footer_only == "Tokens: 321"

    dummy_fp_error = DummyFileProcessor(tokens=(0, 0), raise_tokens=True)
    fallback = scan_service.merge_totals_footer_text(dummy_fp_error, ui_state, [file_meta], content_mode_map=None)
    assert fallback == "Tokens: -"


def test_merge_service_preview_merge_text_streams(tmp_path: Path) -> None:
    ui_state = make_ui_state(tmp_path)
    dummy_fp = DummyFileProcessor(merge_result="joined text")

    result = merge_service.preview_merge_text(dummy_fp, ui_state, ["a.py", "b.py"])

    assert result == "joined text"
    assert dummy_fp.merge_calls[0][1] == ["a.py", "b.py"]


def test_merge_service_preview_merge_text_uses_canonical_summary_defaults(tmp_path: Path) -> None:
    ui_state = make_ui_state(tmp_path)

    class CapturingFileProcessor(DummyFileProcessor):
        def __init__(self) -> None:
            super().__init__(merge_result="joined text")
            self.last_kwargs = None

        def merge_files(self, *args, **kwargs) -> None:  # noqa: D401
            self.last_kwargs = dict(kwargs)
            super().merge_files(*args, **kwargs)

    dummy_fp = CapturingFileProcessor()

    result = merge_service.preview_merge_text(dummy_fp, ui_state, ["a.py"])

    assert result == "joined text"
    assert dummy_fp.last_kwargs is not None
    assert dummy_fp.last_kwargs["strict_summary_required"] is False
    assert dummy_fp.last_kwargs["fallback_to_code_on_summary_error"] is False


def test_git_service_vendored_set_handles_exceptions() -> None:
    dummy_fp = DummyFileProcessor(vendored={"a.py"})
    result = git_service.vendored_set(dummy_fp, "root", ["a.py"])
    assert result == {"a.py"}

    dummy_fp_error = DummyFileProcessor(vendored=RuntimeError("boom"))
    result_error = git_service.vendored_set(dummy_fp_error, "root", ["a.py"])
    assert result_error == set()


def test_watch_service_invalid_directory_returns_zero(tmp_path: Path) -> None:
    ui_state = make_ui_state(tmp_path / "missing")
    assert watch_service.compute_fingerprint(DummyFileProcessor(), ui_state) == (0, 0.0)
