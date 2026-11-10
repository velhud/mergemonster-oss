from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path


OUTPUT_MARKER = ".codemerge-output"


class PathPurpose(str, Enum):
    INPUT_FILE = "input_file"
    INPUT_FOLDER = "input_folder"
    OUTPUT_FILE = "output_file"
    OUTPUT_FOLDER = "output_folder"
    DOWNLOAD_FILE = "download_file"
    OPEN_FILE = "open_file"
    FS_LIST = "fs_list"


class PathGrantPurpose(str, Enum):
    GENERATED_OUTPUT = "generated_output"
    DOWNLOAD = "download"
    OPEN = "open"
    STAT = "stat"
    LIST = "list"
    SOURCE = "source"


class PathGrantKind(str, Enum):
    FILE = "file"
    FOLDER = "folder"


class PathPolicyError(ValueError):
    """Base class for path policy failures."""


class PathInvalid(PathPolicyError):
    """Raised when a path is malformed or has the wrong filesystem shape."""


class PathDenied(PathPolicyError):
    """Raised when a path is valid but outside the current trust boundary."""


class PathConflict(PathPolicyError):
    """Raised when an overwrite/delete target is not safe."""


@dataclass(frozen=True)
class ResolvedPath:
    path: Path
    root: Path
    rel: str | None
    inside_root: bool
    external: bool
    existed_before: bool
    generated: bool = False


@dataclass(frozen=True)
class PathGrant:
    path: Path | str
    purpose: PathGrantPurpose | str
    kind: PathGrantKind | str
    root: Path | str | None = None
    created_at: str | None = None
    expires_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", PathPolicy._canonical(self.path))
        if self.root is not None:
            object.__setattr__(self, "root", PathPolicy._canonical(self.root))
        purpose = self.purpose if isinstance(self.purpose, PathGrantPurpose) else PathGrantPurpose(str(self.purpose))
        kind = self.kind if isinstance(self.kind, PathGrantKind) else PathGrantKind(str(self.kind))
        object.__setattr__(self, "purpose", purpose)
        object.__setattr__(self, "kind", kind)

    def is_expired(self, *, now: datetime | None = None) -> bool:
        if not self.expires_at:
            return False
        raw = self.expires_at.replace("Z", "+00:00")
        try:
            expires = datetime.fromisoformat(raw)
        except ValueError:
            return True
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        return expires <= (now or datetime.now(UTC))

    def matches(self, path: Path, purpose: PathGrantPurpose) -> bool:
        if self.is_expired() or self.purpose != purpose:
            return False
        if self.kind == PathGrantKind.FILE:
            return path == self.path
        return PathPolicy._is_relative_to(path, self.path)


@dataclass(frozen=True)
class PathPolicy:
    project_root: Path | str
    output_root: Path | str | None = None
    allow_external_output: bool = False
    allow_external_browse: bool = False
    generated_outputs: frozenset[Path | str] = field(default_factory=frozenset)
    explicit_user_paths: frozenset[Path | str] = field(default_factory=frozenset)
    grants: tuple[PathGrant, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        root = self._canonical(self.project_root)
        object.__setattr__(self, "project_root", root)
        if self.output_root is not None:
            object.__setattr__(self, "output_root", self._canonical(self.output_root))
        object.__setattr__(
            self,
            "generated_outputs",
            frozenset(self._canonical(path) for path in self.generated_outputs),
        )
        object.__setattr__(
            self,
            "explicit_user_paths",
            frozenset(self._canonical(path) for path in self.explicit_user_paths),
        )
        object.__setattr__(
            self,
            "grants",
            tuple(grant for grant in self.grants if not grant.is_expired()),
        )

    @staticmethod
    def _canonical(path: Path | str) -> Path:
        raw = os.path.expandvars(os.path.expanduser(str(path)))
        return Path(raw).resolve(strict=False)

    @staticmethod
    def _is_relative_to(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _resolve_raw(self, raw: object, *, base: Path | None = None, required: bool = True) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            if required:
                raise PathInvalid("path must be a non-empty string")
            raise PathInvalid("path is empty")
        expanded = os.path.expandvars(os.path.expanduser(raw.strip()))
        path = Path(expanded)
        if not path.is_absolute():
            path = (base or self.project_root) / path
        return path.resolve(strict=False)

    def _rel(self, path: Path) -> str | None:
        try:
            return path.relative_to(self.project_root).as_posix()
        except ValueError:
            return None

    def _is_under_output_root(self, path: Path) -> bool:
        output_root = self.output_root
        return isinstance(output_root, Path) and self._is_relative_to(path, output_root)

    def _is_generated(self, path: Path) -> bool:
        if self._has_grant(path, PathGrantPurpose.GENERATED_OUTPUT):
            return True
        if path in self.generated_outputs:
            return True
        for grant in self.generated_outputs:
            if self._is_relative_to(path, grant):
                return True
        if path.is_dir() and (path / OUTPUT_MARKER).exists():
            return True
        return False

    def is_generated_output(self, raw: Path | str) -> bool:
        return self._is_generated(self._canonical(raw))

    def _is_explicit_user_path(self, path: Path) -> bool:
        if path in self.explicit_user_paths:
            return True
        return any(self._is_relative_to(path, grant) for grant in self.explicit_user_paths)

    def _has_grant(self, path: Path, purpose: PathGrantPurpose) -> bool:
        return any(grant.matches(path, purpose) for grant in self.grants)

    def _resolved(self, path: Path, *, generated: bool = False) -> ResolvedPath:
        inside_root = self._is_relative_to(path, self.project_root)
        generated = generated or self._is_generated(path)
        return ResolvedPath(
            path=path,
            root=self.project_root,
            rel=self._rel(path),
            inside_root=inside_root,
            external=not inside_root,
            existed_before=path.exists(),
            generated=generated,
        )

    def resolve_input_file(self, raw: object) -> ResolvedPath:
        path = self._resolve_raw(raw)
        if not path.exists() or not path.is_file():
            raise PathInvalid(f"input file does not exist: {path}")
        if not self._is_relative_to(path, self.project_root):
            raise PathDenied(f"input file is outside project root: {path}")
        return self._resolved(path)

    def resolve_input_folder(self, raw: object) -> ResolvedPath:
        path = self._resolve_raw(raw)
        if not path.exists() or not path.is_dir():
            raise PathInvalid(f"input folder does not exist: {path}")
        if not self._is_relative_to(path, self.project_root):
            raise PathDenied(f"input folder is outside project root: {path}")
        return self._resolved(path)

    def resolve_output_file(self, raw: object | None, *, default_name: str) -> ResolvedPath:
        path = self._resolve_raw(raw, required=False) if isinstance(raw, str) and raw.strip() else self.project_root / default_name
        return self._resolve_output(path)

    def resolve_output_folder(self, raw: object | None, *, default_name: str) -> ResolvedPath:
        path = self._resolve_raw(raw, required=False) if isinstance(raw, str) and raw.strip() else self.project_root / default_name
        return self._resolve_output(path)

    def _resolve_output(self, path: Path) -> ResolvedPath:
        inside_root = self._is_relative_to(path, self.project_root)
        inside_output_root = self._is_under_output_root(path)
        if not inside_root and not inside_output_root and not self.allow_external_output:
            raise PathDenied(f"output path is outside project root: {path}")
        return self._resolved(path)

    def assert_safe_overwrite_folder(self, resolved: ResolvedPath) -> None:
        path = resolved.path
        if not path.exists():
            return
        if not path.is_dir():
            raise PathConflict(f"overwrite target is not a folder: {path}")
        if path == self.project_root:
            raise PathConflict("refusing to overwrite the project root")
        generated = self._is_generated(path)
        if (
            not resolved.inside_root
            and not self._is_under_output_root(path)
            and not self.allow_external_output
            and not generated
        ):
            raise PathConflict(f"refusing to overwrite external folder: {path}")
        if not generated:
            raise PathConflict(f"refusing to overwrite non-generated folder: {path}")

    def can_download(self, raw: Path | str) -> bool:
        path = self._canonical(raw)
        if not path.exists() or not path.is_file():
            return False
        return (
            self._is_relative_to(path, self.project_root)
            or self._is_generated(path)
            or self._has_grant(path, PathGrantPurpose.DOWNLOAD)
        )

    def can_open(self, raw: Path | str) -> bool:
        if str(raw).startswith(("http://", "https://", "file://")):
            return False
        path = self._canonical(raw)
        if not path.exists():
            return False
        return (
            self._is_generated(path)
            or self._is_explicit_user_path(path)
            or self._has_grant(path, PathGrantPurpose.OPEN)
        )

    def can_list(self, raw: Path | str, *, restrict_root: bool = True) -> bool:
        path = self._canonical(raw)
        if not path.exists() or not path.is_dir():
            return False
        if restrict_root:
            return self._is_relative_to(path, self.project_root) or self._has_grant(path, PathGrantPurpose.LIST)
        return (
            self.allow_external_browse
            or self._is_relative_to(path, self.project_root)
            or self._has_grant(path, PathGrantPurpose.LIST)
        )

    def can_stat(self, raw: Path | str) -> bool:
        path = self._canonical(raw)
        return (
            self._is_relative_to(path, self.project_root)
            or self._is_generated(path)
            or self._is_explicit_user_path(path)
            or self._has_grant(path, PathGrantPurpose.STAT)
        )

    def can_source(self, raw: Path | str) -> bool:
        path = self._canonical(raw)
        if not path.exists():
            return False
        return self._is_relative_to(path, self.project_root) or self._has_grant(path, PathGrantPurpose.SOURCE)
