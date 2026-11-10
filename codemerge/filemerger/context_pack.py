from __future__ import annotations

import ast
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

from .output import atomic_text_output


class ContextPackError(RuntimeError):
    pass


MAX_FILE_BYTES = 512 * 1024
DEFAULT_EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
}
ALLOWED_NON_PY_EXTS = {
    ".md",
    ".txt",
    ".yml",
    ".yaml",
    ".toml",
    ".json",
    ".ini",
    ".cfg",
    ".plist",
    ".xml",
    ".sql",
}


@dataclass(frozen=True)
class SubsystemSpec:
    name: str
    roots: tuple[Path, ...]
    public_api: tuple[Path, ...]


@dataclass(frozen=True)
class ManifestSpec:
    subsystems: tuple[SubsystemSpec, ...]
    core_shared: tuple[Path, ...]


@dataclass(frozen=True)
class ContextPackResult:
    subsystem: str
    mode: str
    output_text: Optional[str]
    output_path: Optional[str]
    included_files: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ContextPackFilters:
    excluded_dirs: tuple[str, ...] = ()
    excluded_files: tuple[str, ...] = ()
    custom_excluded: tuple[str, ...] = ()
    max_file_bytes: int = MAX_FILE_BYTES
    allowed_non_py_exts: tuple[str, ...] = tuple(sorted(ALLOWED_NON_PY_EXTS))
    default_excluded_dirs: tuple[str, ...] = tuple(sorted(DEFAULT_EXCLUDED_DIRS))


@dataclass(frozen=True)
class _FilterState:
    excluded_dir_names: set[str]
    excluded_dir_paths: set[str]
    excluded_file_names: set[str]
    excluded_file_paths: set[str]
    custom_paths: set[str]


def list_manifest_subsystems(repo_path: str) -> list[str]:
    repo_root = _require_dir(repo_path, "repo_path")
    manifest_path = repo_root / "ai" / "manifest.yml"
    if not manifest_path.exists():
        raise ContextPackError(f"Missing required file: {manifest_path}")
    manifest = _load_manifest(manifest_path, repo_root)
    names: list[str] = []
    seen: set[str] = set()
    for spec in manifest.subsystems:
        if spec.name in seen:
            continue
        seen.add(spec.name)
        names.append(spec.name)
    return names


def build_pack(
    repo_path: str,
    subsystem_name: str,
    mode: str,
    output_path: str | None = None,
    *,
    return_text: bool | None = None,
    filters: ContextPackFilters | None = None,
    is_virtual_env: Callable[[str], bool] | None = None,
) -> ContextPackResult:
    """Build a deterministic pack for one subsystem."""
    mode_norm = str(mode or "").strip().lower()
    if mode_norm not in {"subsystem", "audit"}:
        raise ContextPackError("mode must be 'subsystem' or 'audit'")

    repo_root = _require_dir(repo_path, "repo_path")
    map_path = repo_root / "ai" / "MAP.md"
    manifest_path = repo_root / "ai" / "manifest.yml"
    if not map_path.exists():
        raise ContextPackError(f"Missing required file: {map_path}")
    if not manifest_path.exists():
        raise ContextPackError(f"Missing required file: {manifest_path}")

    manifest = _load_manifest(manifest_path, repo_root)
    subsystem = _select_subsystem(manifest, subsystem_name)

    filters = filters or ContextPackFilters()
    state = _build_filter_state(filters)
    warnings: list[str] = []
    warn = _warning_adder(warnings)
    text_cache: dict[Path, str] = {}

    def _include_optional(path: Path, *, role: str | None = None) -> bool:
        return _include_file(
            path,
            repo_root,
            filters,
            state,
            text_cache,
            warn,
            role=role,
            is_virtual_env=is_virtual_env,
        )

    def _include_required(path: Path, *, role: str | None = None) -> None:
        if not _include_optional(path, role=role):
            rel = _rel_posix(repo_root, path)
            label = role or "required file"
            raise ContextPackError(f"Required file excluded or unreadable: {rel} ({label})")

    _require_file(map_path, "ai/MAP.md")
    _include_required(map_path, role="map")
    _require_file(manifest_path, "ai/manifest.yml")
    _include_required(manifest_path, role="manifest")

    contract_path = repo_root / "docs" / "CONTRACT.md"
    if contract_path.exists():
        _include_optional(contract_path, role="contract")

    core_shared = _resolve_required_files(
        repo_root, manifest.core_shared, "core_shared"
    )
    for path in sorted(core_shared, key=lambda p: _rel_posix(repo_root, p)):
        _include_required(path, role="core_shared")

    public_api_paths = _resolve_required_files(
        repo_root, subsystem.public_api, f"public_api for {subsystem.name}"
    )
    for path in sorted(public_api_paths, key=lambda p: _rel_posix(repo_root, p)):
        _include_required(path, role="public_api")

    root_files = _collect_files_from_roots(
        subsystem.roots,
        repo_root,
        filters,
        state,
        warn,
        text_cache,
        is_virtual_env,
    )
    root_files = [p for p in root_files if p not in public_api_paths]

    dependency_groups: dict[str, dict[str, list[Path]]] = {}
    if mode_norm == "audit":
        module_index, file_to_module = _build_module_index(
            repo_root, filters, state, is_virtual_env
        )
        unresolved: set[str] = set()
        direct_deps = _detect_dependencies(
            root_files,
            repo_root,
            manifest.subsystems,
            module_index,
            file_to_module,
            text_cache,
            unresolved,
        )
        for msg in sorted(unresolved):
            warn(msg)

        dep_results, dep_unresolved = _resolve_dependency_closure(
            direct_deps,
            manifest.subsystems,
            repo_root,
            module_index,
            file_to_module,
            filters,
            state,
            text_cache,
            warn,
            is_virtual_env,
        )
        for msg in sorted(dep_unresolved):
            warn(msg)
        dependency_groups = dep_results

    contract_tests = _collect_contract_tests(
        repo_root,
        subsystem.name,
        filters,
        state,
        warn,
        text_cache,
        is_virtual_env,
    )

    ordered_paths: list[Path] = []
    ordered_paths.extend([map_path, manifest_path])
    if contract_path.exists():
        if contract_path in text_cache:
            ordered_paths.append(contract_path)
    ordered_paths.extend(sorted(core_shared, key=lambda p: _rel_posix(repo_root, p)))
    ordered_paths.extend(sorted(public_api_paths, key=lambda p: _rel_posix(repo_root, p)))
    ordered_paths.extend(sorted(root_files, key=lambda p: _rel_posix(repo_root, p)))

    for dep_name in sorted(dependency_groups.keys()):
        group = dependency_groups[dep_name]
        ordered_paths.extend(sorted(group.get("public_api", []), key=lambda p: _rel_posix(repo_root, p)))
        ordered_paths.extend(sorted(group.get("closure", []), key=lambda p: _rel_posix(repo_root, p)))

    ordered_paths.extend(sorted(contract_tests, key=lambda p: _rel_posix(repo_root, p)))

    ordered_paths = _dedupe_paths(ordered_paths)
    included_rel = tuple(_rel_posix(repo_root, p) for p in ordered_paths)

    header = _render_header(
        mode_norm,
        subsystem.name,
        included_rel,
        warnings,
    )

    if return_text is None:
        return_text = output_path is None

    output_text: Optional[str] = None
    if output_path:
        output_file = Path(output_path).expanduser()
        if not output_file.is_absolute():
            output_file = (repo_root / output_file).resolve()
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with atomic_text_output(output_file) as fh:
            _write_pack(fh, repo_root, header, ordered_paths, text_cache)
        if return_text:
            output_text = output_file.read_text(encoding="utf-8", errors="replace")
        return ContextPackResult(
            subsystem=subsystem.name,
            mode=mode_norm,
            output_text=output_text,
            output_path=str(output_file),
            included_files=included_rel,
            warnings=tuple(warnings),
        )

    from io import StringIO

    buf = StringIO()
    _write_pack(buf, repo_root, header, ordered_paths, text_cache)
    output_text = buf.getvalue()
    return ContextPackResult(
        subsystem=subsystem.name,
        mode=mode_norm,
        output_text=output_text,
        output_path=None,
        included_files=included_rel,
        warnings=tuple(warnings),
    )


def build_subsystem_context_pack(
    repo_path: str,
    subsystem_name: str,
    output_path: str | None = None,
    *,
    return_text: bool | None = None,
    filters: ContextPackFilters | None = None,
    is_virtual_env: Callable[[str], bool] | None = None,
) -> ContextPackResult:
    """Backward-compatible wrapper for mode='subsystem'."""
    return build_pack(
        repo_path,
        subsystem_name,
        "subsystem",
        output_path,
        return_text=return_text,
        filters=filters,
        is_virtual_env=is_virtual_env,
    )


# ---------------------- Manifest parsing ----------------------

def _load_manifest(path: Path, repo_root: Path) -> ManifestSpec:
    if not path.resolve().is_relative_to(repo_root) or not path.is_file():
        raise ContextPackError("Manifest must be a file inside the repository.")
    text = path.read_text(encoding="utf-8", errors="replace")
    data: dict
    try:
        import yaml  # type: ignore
    except Exception:
        data = _parse_simple_yaml(text)
    else:
        try:
            data = yaml.safe_load(text) or {}
        except Exception as exc:
            raise ContextPackError(f"Failed to parse manifest YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise ContextPackError("Manifest must be a mapping at the top level")

    subsystems_raw = data.get("subsystems")
    if not isinstance(subsystems_raw, list):
        raise ContextPackError("Manifest is missing 'subsystems' list")

    subsystems: list[SubsystemSpec] = []
    for idx, entry in enumerate(subsystems_raw):
        if not isinstance(entry, dict):
            raise ContextPackError(f"Subsystem entry {idx} is not a mapping")
        name = entry.get("name")
        root = entry.get("root")
        public_api = entry.get("public_api")
        if not isinstance(name, str) or not name.strip():
            raise ContextPackError(
                f"Subsystem entry {idx} missing required keys. Expected keys: name, root, public_api"
            )
        roots = _normalize_path_list(root, "root", idx)
        apis = _normalize_path_list(public_api, "public_api", idx)
        subsystems.append(
            SubsystemSpec(
                name=name.strip(),
                roots=tuple(_resolve_paths(repo_root, roots)),
                public_api=tuple(_resolve_paths(repo_root, apis)),
            )
        )

    core_shared_raw = data.get("core_shared", [])
    if core_shared_raw is None:
        core_shared_raw = []
    core_shared = _normalize_path_list(core_shared_raw, "core_shared", None, allow_empty=True)
    return ManifestSpec(
        subsystems=tuple(subsystems),
        core_shared=tuple(_resolve_paths(repo_root, core_shared)),
    )


def _normalize_path_list(value: object, field: str, idx: Optional[int], *, allow_empty: bool = False) -> list[str]:
    if isinstance(value, str):
        val = value.strip()
        if not val and not allow_empty:
            raise ContextPackError(_missing_key_message(idx))
        return [val] if val else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ContextPackError(_missing_key_message(idx))
            val = item.strip()
            if val:
                out.append(val)
        if not out and not allow_empty:
            raise ContextPackError(_missing_key_message(idx))
        return out
    if allow_empty and value is None:
        return []
    raise ContextPackError(_missing_key_message(idx))


def _missing_key_message(idx: Optional[int]) -> str:
    if idx is None:
        return "Invalid manifest entry. Expected keys: name, root, public_api"
    return f"Subsystem entry {idx} missing required keys. Expected keys: name, root, public_api"


def _resolve_paths(repo_root: Path, paths: Iterable[str]) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_absolute() or ".." in p.parts:
            raise ContextPackError("Manifest paths must be relative to the repository.")
        resolved = (repo_root / p).resolve()
        if not resolved.is_relative_to(repo_root):
            raise ContextPackError("A manifest path resolves outside the repository.")
        out.append(resolved)
    return out


def _select_subsystem(manifest: ManifestSpec, name: str) -> SubsystemSpec:
    for spec in manifest.subsystems:
        if spec.name == name:
            return spec
    available = ", ".join(sorted(s.name for s in manifest.subsystems))
    raise ContextPackError(f"Subsystem '{name}' not found. Available: {available}")


# ---------------------- Filtering helpers ----------------------

def _build_filter_state(filters: ContextPackFilters) -> _FilterState:
    excluded_dir_names = set(filters.default_excluded_dirs)
    excluded_dir_paths: set[str] = set()
    excluded_file_names: set[str] = set()
    excluded_file_paths: set[str] = set()
    custom_paths: set[str] = set()

    for entry in filters.excluded_dirs:
        _add_exclusion_entry(entry, excluded_dir_names, excluded_dir_paths)
    for entry in filters.excluded_files:
        _add_exclusion_entry(entry, excluded_file_names, excluded_file_paths)
    for entry in filters.custom_excluded:
        _add_custom_exclusion_entry(entry, custom_paths)

    return _FilterState(
        excluded_dir_names=excluded_dir_names,
        excluded_dir_paths=excluded_dir_paths,
        excluded_file_names=excluded_file_names,
        excluded_file_paths=excluded_file_paths,
        custom_paths=custom_paths,
    )


def _add_exclusion_entry(entry: object, names: set[str], paths: set[str]) -> None:
    raw = str(entry or "").strip()
    if not raw:
        return
    if os.path.sep not in raw and "/" not in raw:
        names.add(raw)
    paths.add(raw)
    paths.add(os.path.normpath(raw))
    paths.add(os.path.normcase(os.path.normpath(raw)))
    if os.path.isabs(raw):
        paths.add(_norm_abs(raw))


def _add_custom_exclusion_entry(entry: object, paths: set[str]) -> None:
    raw = str(entry or "").strip()
    if not raw:
        return
    paths.add(raw)
    paths.add(os.path.normpath(raw))
    paths.add(os.path.normcase(os.path.normpath(raw)))
    if os.path.isabs(raw):
        paths.add(_norm_abs(raw))


def _is_excluded_dir(
    path: Path,
    repo_root: Path,
    state: _FilterState,
    is_virtual_env: Callable[[str], bool] | None,
) -> bool:
    try:
        rel = os.path.normpath(os.path.relpath(str(path), str(repo_root)))
    except Exception:
        rel = os.path.normpath(str(path))
    rel_posix = Path(rel).as_posix()
    abs_norm = _norm_abs(str(path))
    name = path.name

    if name in state.excluded_dir_names:
        return True
    if rel in state.excluded_dir_paths or rel_posix in state.excluded_dir_paths:
        return True
    if abs_norm in state.excluded_dir_paths:
        return True
    if rel in state.custom_paths or rel_posix in state.custom_paths:
        return True
    if abs_norm in state.custom_paths:
        return True
    if is_virtual_env is not None:
        try:
            if is_virtual_env(str(path)):
                return True
        except Exception:
            pass
    return False


def _is_excluded_file(path: Path, repo_root: Path, state: _FilterState) -> bool:
    name = path.name
    try:
        rel = os.path.normpath(os.path.relpath(str(path), str(repo_root)))
    except Exception:
        rel = os.path.normpath(str(path))
    rel_posix = Path(rel).as_posix()
    abs_norm = _norm_abs(str(path))

    if name in state.excluded_file_names:
        return True
    if rel in state.excluded_file_paths or rel_posix in state.excluded_file_paths:
        return True
    if abs_norm in state.excluded_file_paths:
        return True
    if rel in state.custom_paths or rel_posix in state.custom_paths:
        return True
    if abs_norm in state.custom_paths:
        return True
    return False


def _path_has_excluded_dir(
    path: Path,
    repo_root: Path,
    state: _FilterState,
    is_virtual_env: Callable[[str], bool] | None,
) -> bool:
    for parent in path.parents:
        if parent == repo_root:
            break
        if _is_excluded_dir(parent, repo_root, state, is_virtual_env):
            return True
    return False


def _include_file(
    path: Path,
    repo_root: Path,
    filters: ContextPackFilters,
    state: _FilterState,
    text_cache: dict[Path, str],
    warn: Callable[[str], None],
    *,
    role: str | None = None,
    is_virtual_env: Callable[[str], bool] | None = None,
) -> bool:
    if not path.resolve().is_relative_to(repo_root) or not path.is_file():
        return False
    if _is_excluded_file(path, repo_root, state):
        return False
    if _path_has_excluded_dir(path, repo_root, state, is_virtual_env):
        return False

    suffix = path.suffix.lower()
    if suffix != ".py" and suffix not in set(filters.allowed_non_py_exts):
        warn(f"Skipped unsupported extension: {_rel_posix(repo_root, path)}")
        return False

    try:
        size = path.stat().st_size
    except Exception:
        warn(f"Skipped unreadable file: {_rel_posix(repo_root, path)}")
        return False

    if size > filters.max_file_bytes:
        warn(f"Skipped large file (>512KB): {_rel_posix(repo_root, path)}")
        return False

    try:
        data = path.read_bytes()
        text = data.decode("utf-8")
    except Exception:
        warn(f"Skipped non-UTF8 (binary) file: {_rel_posix(repo_root, path)}")
        return False

    text_cache[path] = text
    return True


# ---------------------- Collection helpers ----------------------

def _collect_files_from_roots(
    roots: Sequence[Path],
    repo_root: Path,
    filters: ContextPackFilters,
    state: _FilterState,
    warn: Callable[[str], None],
    text_cache: dict[Path, str],
    is_virtual_env: Callable[[str], bool] | None,
) -> list[Path]:
    out: list[Path] = []
    for root in roots:
        if not root.exists():
            warn(f"Missing subsystem root: {_rel_posix(repo_root, root)}")
            continue
        if root.is_file():
            if _include_file(
                root,
                repo_root,
                filters,
                state,
                text_cache,
                warn,
                is_virtual_env=is_virtual_env,
            ):
                out.append(root)
            continue
        if _is_excluded_dir(root, repo_root, state, is_virtual_env):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            filenames.sort()
            pruned: list[str] = []
            for entry in list(dirnames):
                dpath = Path(dirpath) / entry
                if _is_excluded_dir(dpath, repo_root, state, is_virtual_env):
                    pruned.append(entry)
            for entry in pruned:
                if entry in dirnames:
                    dirnames.remove(entry)
            for filename in filenames:
                fpath = Path(dirpath) / filename
                if _include_file(
                    fpath,
                    repo_root,
                    filters,
                    state,
                    text_cache,
                    warn,
                    is_virtual_env=is_virtual_env,
                ):
                    out.append(fpath)
    return out


def _collect_contract_tests(
    repo_root: Path,
    subsystem_name: str,
    filters: ContextPackFilters,
    state: _FilterState,
    warn: Callable[[str], None],
    text_cache: dict[Path, str],
    is_virtual_env: Callable[[str], bool] | None,
) -> list[Path]:
    contract_root = repo_root / "tests" / "contract" / subsystem_name
    if not contract_root.exists() or not contract_root.is_dir():
        warn(f"Missing contract tests folder: tests/contract/{subsystem_name}")
        return []
    return _collect_files_from_roots(
        [contract_root],
        repo_root,
        filters,
        state,
        warn,
        text_cache,
        is_virtual_env,
    )


def _resolve_required_files(repo_root: Path, paths: Iterable[Path], label: str) -> list[Path]:
    out: list[Path] = []
    for path in paths:
        p = path
        if not p.exists() or not p.is_file():
            raise ContextPackError(f"Missing required file for {label}: {p}")
        out.append(p)
    return out


# ---------------------- Import resolution ----------------------

def _build_module_index(
    repo_root: Path,
    filters: ContextPackFilters,
    state: _FilterState,
    is_virtual_env: Callable[[str], bool] | None,
) -> tuple[dict[str, Path], dict[Path, str]]:
    init_dirs: set[Path] = set()
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames.sort()
        if _is_excluded_dir(Path(dirpath), repo_root, state, is_virtual_env):
            dirnames[:] = []
            continue
        if "__init__.py" in filenames:
            init_dirs.add(Path(dirpath).resolve())

    top_packages: list[Path] = []
    for pkg_dir in init_dirs:
        if not _has_init_ancestor(pkg_dir, init_dirs, repo_root):
            top_packages.append(pkg_dir)

    module_index: dict[str, Path] = {}
    file_to_module: dict[Path, str] = {}
    for top in sorted(top_packages, key=lambda p: p.as_posix()):
        top_name = top.name
        for dirpath, dirnames, filenames in os.walk(top):
            dirnames.sort()
            if _is_excluded_dir(Path(dirpath), repo_root, state, is_virtual_env):
                dirnames[:] = []
                continue
            for filename in sorted(filenames):
                if not filename.endswith(".py"):
                    continue
                path = Path(dirpath) / filename
                if _is_excluded_file(path, repo_root, state):
                    continue
                try:
                    rel = path.relative_to(top)
                except Exception:
                    continue
                if filename == "__init__.py":
                    parts = rel.parts[:-1]
                else:
                    parts = rel.with_suffix("").parts
                module_parts = [top_name] + list(parts) if parts else [top_name]
                module_name = ".".join(module_parts)
                module_index[module_name] = path.resolve()
                file_to_module[path.resolve()] = module_name
    return module_index, file_to_module


def _has_init_ancestor(dir_path: Path, init_dirs: set[Path], repo_root: Path) -> bool:
    try:
        dir_path.resolve().relative_to(repo_root.resolve())
    except Exception:
        return True
    for parent in dir_path.resolve().parents:
        if parent == repo_root:
            break
        if parent in init_dirs:
            return True
    return False


def _detect_dependencies(
    python_files: Sequence[Path],
    repo_root: Path,
    subsystems: Sequence[SubsystemSpec],
    module_index: dict[str, Path],
    file_to_module: dict[Path, str],
    text_cache: dict[Path, str],
    unresolved: set[str],
) -> set[str]:
    deps: set[str] = set()
    for path in python_files:
        if path.suffix != ".py":
            continue
        text = _read_cached_or_disk(path, text_cache)
        if text is None:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            unresolved.add(f"Unresolved import in {_rel_posix(repo_root, path)}: <syntax error>")
            continue
        resolved_paths, unresolved_imports = _resolve_imports(
            path,
            tree,
            module_index,
            file_to_module,
        )
        for missing in unresolved_imports:
            unresolved.add(f"Unresolved import in {_rel_posix(repo_root, path)}: {missing}")
        for rpath in resolved_paths:
            owner = _subsystem_for_path(rpath, subsystems)
            if owner and owner != _subsystem_for_path(path, subsystems):
                deps.add(owner)
    return deps


def _resolve_dependency_closure(
    dependencies: set[str],
    subsystems: Sequence[SubsystemSpec],
    repo_root: Path,
    module_index: dict[str, Path],
    file_to_module: dict[Path, str],
    filters: ContextPackFilters,
    state: _FilterState,
    text_cache: dict[Path, str],
    warn: Callable[[str], None],
    is_virtual_env: Callable[[str], bool] | None,
) -> tuple[dict[str, dict[str, list[Path]]], set[str]]:
    subs_by_name = {s.name: s for s in subsystems}
    pending: set[str] = set(dependencies)
    processed: set[str] = set()
    results: dict[str, dict[str, list[Path]]] = {}
    unresolved: set[str] = set()

    while pending:
        name = sorted(pending)[0]
        pending.remove(name)
        if name in processed:
            continue
        processed.add(name)
        spec = subs_by_name.get(name)
        if not spec:
            continue

        public_api_paths = _resolve_required_files(
            repo_root, spec.public_api, f"public_api for {spec.name}"
        )
        public_api_included: list[Path] = []
        for path in sorted(public_api_paths, key=lambda p: _rel_posix(repo_root, p)):
            if not _include_file(
                path,
                repo_root,
                filters,
                state,
                text_cache,
                warn,
                is_virtual_env=is_virtual_env,
            ):
                rel = _rel_posix(repo_root, path)
                raise ContextPackError(
                    f"Required file excluded or unreadable: {rel} (public_api for {spec.name})"
                )
            public_api_included.append(path)

        closure: list[Path] = []
        queue: list[Path] = list(public_api_included)
        visited: set[Path] = set(public_api_included)

        while queue:
            current = queue.pop(0)
            if current.suffix != ".py":
                continue
            text = _read_cached_or_disk(current, text_cache)
            if text is None:
                continue
            try:
                tree = ast.parse(text)
            except SyntaxError:
                unresolved.add(
                    f"Unresolved import in {_rel_posix(repo_root, current)}: <syntax error>"
                )
                continue
            resolved_paths, unresolved_imports = _resolve_imports(
                current,
                tree,
                module_index,
                file_to_module,
            )
            for missing in unresolved_imports:
                unresolved.add(
                    f"Unresolved import in {_rel_posix(repo_root, current)}: {missing}"
                )
            for rpath in resolved_paths:
                owner = _subsystem_for_path(rpath, subsystems)
                if owner == spec.name:
                    if rpath in visited:
                        continue
                    if not _include_file(
                        rpath,
                        repo_root,
                        filters,
                        state,
                        text_cache,
                        warn,
                        is_virtual_env=is_virtual_env,
                    ):
                        continue
                    visited.add(rpath)
                    closure.append(rpath)
                    queue.append(rpath)
                elif owner and owner not in processed:
                    pending.add(owner)

        results[spec.name] = {
            "public_api": public_api_included,
            "closure": closure,
        }

    return results, unresolved


def _resolve_imports(
    file_path: Path,
    tree: ast.AST,
    module_index: dict[str, Path],
    file_to_module: dict[Path, str],
) -> tuple[set[Path], set[str]]:
    resolved: set[Path] = set()
    unresolved: set[str] = set()

    current_module = file_to_module.get(file_path.resolve())
    if current_module:
        if file_path.name == "__init__.py":
            package_parts = current_module.split(".")
        else:
            package_parts = current_module.split(".")[:-1]
    else:
        package_parts = None

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name
                path = module_index.get(mod)
                if path:
                    resolved.add(path)
                else:
                    unresolved.add(mod)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                if package_parts is None:
                    for alias in node.names:
                        if alias.name != "*":
                            unresolved.add(alias.name)
                    continue
                trim = node.level - 1
                base_parts = package_parts[: len(package_parts) - trim]
                if node.module:
                    base_parts = base_parts + node.module.split(".")
                base_module = ".".join(base_parts) if base_parts else ""
            else:
                base_module = node.module or ""

            for alias in node.names:
                if alias.name == "*":
                    if base_module and base_module in module_index:
                        resolved.add(module_index[base_module])
                    else:
                        unresolved.add(base_module or "*")
                    continue
                candidate = f"{base_module}.{alias.name}" if base_module else alias.name
                if candidate in module_index:
                    resolved.add(module_index[candidate])
                elif base_module and base_module in module_index:
                    resolved.add(module_index[base_module])
                else:
                    unresolved.add(candidate)
    return resolved, unresolved


def _subsystem_for_path(path: Path, subsystems: Sequence[SubsystemSpec]) -> Optional[str]:
    best_name: Optional[str] = None
    best_len = -1
    p = path.resolve()
    for spec in subsystems:
        for root in spec.roots:
            root_path = root.resolve()
            if root_path.is_dir():
                try:
                    p.relative_to(root_path)
                except Exception:
                    continue
                match_len = len(str(root_path))
            else:
                if p != root_path:
                    continue
                match_len = len(str(root_path))
            if match_len > best_len:
                best_len = match_len
                best_name = spec.name
    return best_name


# ---------------------- Output helpers ----------------------

def _render_header(
    mode: str,
    subsystem_name: str,
    included_files: Sequence[str],
    warnings: Sequence[str],
) -> str:
    lines: list[str] = []
    lines.append("===== PACK HEADER =====")
    lines.append(f"Mode: {mode}")
    lines.append(f"Subsystem: {subsystem_name}")
    lines.append(f"Included file count: {len(included_files)}")
    lines.append("")
    lines.append("Included files:")
    if included_files:
        lines.extend(f"- {p}" for p in included_files)
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append("Warnings:")
    if warnings:
        lines.extend(f"- {w}" for w in warnings)
    else:
        lines.append("- (none)")
    lines.append("===== END PACK HEADER =====")
    lines.append("")
    return "\n".join(lines)


def _write_pack(
    output_stream,
    repo_root: Path,
    header: str,
    ordered_paths: Sequence[Path],
    text_cache: dict[Path, str],
) -> None:
    output_stream.write(header)
    for path in ordered_paths:
        rel = _rel_posix(repo_root, path)
        output_stream.write(f"===== FILE: {rel} =====\n")
        content = text_cache.get(path)
        if content is None:
            try:
                content = path.read_text(encoding="utf-8")
            except Exception:
                content = ""
        output_stream.write(content)
        if not content.endswith("\n"):
            output_stream.write("\n")
        output_stream.write("\n")


def _dedupe_paths(paths: Sequence[Path]) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        out.append(path)
    return out


def _warning_adder(warnings: list[str]) -> Callable[[str], None]:
    seen: set[str] = set()

    def _add(msg: str) -> None:
        if not msg:
            return
        if msg in seen:
            return
        seen.add(msg)
        warnings.append(msg)

    return _add


def _read_cached_or_disk(path: Path, text_cache: dict[Path, str]) -> Optional[str]:
    cached = text_cache.get(path)
    if cached is not None:
        return cached
    try:
        resolved = path.resolve()
    except Exception:
        resolved = None
    if resolved is not None:
        cached = text_cache.get(resolved)
        if cached is not None:
            return cached
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def _require_dir(path: str | Path, label: str) -> Path:
    p = Path(path).expanduser().resolve()
    if not p.exists() or not p.is_dir():
        raise ContextPackError(f"{label} is not a directory: {p}")
    return p


def _require_file(path: Path, label: str) -> None:
    if not path.exists() or not path.is_file():
        raise ContextPackError(f"Missing required file: {path} ({label})")


def _rel_posix(repo_root: Path, path: Path) -> str:
    try:
        rel = path.relative_to(repo_root)
    except Exception:
        rel = path
    return rel.as_posix()


def _norm_abs(path: str) -> str:
    try:
        return os.path.normcase(os.path.abspath(path))
    except Exception:
        try:
            return os.path.normcase(os.path.normpath(path))
        except Exception:
            return str(path)


# ---------------------- Minimal YAML parser ----------------------

def _parse_simple_yaml(text: str) -> dict:
    lines = _preprocess_yaml_lines(text)
    if not lines:
        return {}

    root: dict = {}
    stack: list[tuple[int, object]] = [(0, root)]

    idx = 0
    while idx < len(lines):
        indent, raw = lines[idx]
        while stack and indent < stack[-1][0]:
            stack.pop()
        if not stack:
            raise ContextPackError("Invalid YAML indentation structure")
        container = stack[-1][1]
        if raw.startswith("- "):
            if not isinstance(container, list):
                raise ContextPackError("YAML structure error: list item without list container")
            item_text = raw[2:].strip()
            if not item_text:
                item: dict = {}
                container.append(item)
                next_indent = _peek_indent(lines, idx)
                if next_indent is not None:
                    stack.append((next_indent, item))
            elif ":" in item_text:
                key, val = _split_key_value(item_text)
                item = {key: _parse_scalar(val)}
                container.append(item)
                next_indent = _peek_indent(lines, idx)
                if next_indent is not None and next_indent > indent:
                    stack.append((next_indent, item))
            else:
                container.append(_parse_scalar(item_text))
        else:
            if not isinstance(container, dict):
                raise ContextPackError("YAML structure error: mapping entry without dict container")
            key, val = _split_key_value(raw)
            if val:
                container[key] = _parse_scalar(val)
            else:
                next_indent = _peek_indent(lines, idx)
                if next_indent is None or next_indent <= indent:
                    container[key] = {}
                else:
                    next_raw = _peek_raw(lines, idx)
                    if next_raw and next_raw.startswith("- "):
                        new_list: list = []
                        container[key] = new_list
                        stack.append((next_indent, new_list))
                    else:
                        new_dict: dict = {}
                        container[key] = new_dict
                        stack.append((next_indent, new_dict))
        idx += 1
    return root


def _preprocess_yaml_lines(text: str) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    for line in text.splitlines():
        raw = _strip_yaml_comment(line)
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        result.append((indent, raw.strip()))
    return result


def _strip_yaml_comment(line: str) -> str:
    in_single = False
    in_double = False
    for idx, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return line[:idx].rstrip()
    return line.rstrip()


def _split_key_value(raw: str) -> tuple[str, str]:
    if ":" not in raw:
        raise ContextPackError(f"Invalid YAML line: {raw}")
    key, val = raw.split(":", 1)
    return key.strip(), val.strip()


def _parse_scalar(val: str) -> str:
    if not val:
        return ""
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        return val[1:-1]
    return val


def _peek_indent(lines: Sequence[tuple[int, str]], idx: int) -> Optional[int]:
    for j in range(idx + 1, len(lines)):
        return lines[j][0]
    return None


def _peek_raw(lines: Sequence[tuple[int, str]], idx: int) -> Optional[str]:
    for j in range(idx + 1, len(lines)):
        return lines[j][1]
    return None
