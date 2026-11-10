"""Safe publication of completed text exports."""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory


@contextmanager
def atomic_text_output(destination: str | Path, *, overwrite: bool = True):
    """Stage beside the destination and publish only after the writer succeeds."""
    path = Path(destination)
    temporary_path = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", errors="replace", newline="",
            prefix=".mergemonster-", suffix=".tmp", dir=path.parent, delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        if overwrite:
            os.replace(temporary_path, path)
        else:
            os.link(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


_DIRECTORY_PUBLISH_LOCK = threading.Lock()


@contextmanager
def staged_output_directory(destination: str | Path, *, overwrite: bool = False):
    """Write a new directory beside its destination, then publish it as a unit."""
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".mergemonster-", dir=path.parent) as staging_dir:
        staging = Path(staging_dir)
        new = staging / "output"
        previous = staging / "previous"
        yield new
        with _DIRECTORY_PUBLISH_LOCK:
            if path.is_symlink() or (path.exists() and not overwrite):
                raise FileExistsError(f"Output already exists: {path.name}")
            if path.exists():
                path.rename(previous)
            try:
                new.rename(path)
            except BaseException:
                if previous.exists():
                    previous.rename(path)
                raise
