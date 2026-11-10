from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Dict


def xcode_target_sources(root: str) -> dict[str, set[str]]:
    """Return a mapping of Xcode target names to source file paths."""

    result: dict[str, set[str]] = {}
    canonical_root = Path(root).resolve()
    try:
        for dirpath, _, _ in os.walk(root):
            if not dirpath.endswith(".xcodeproj"):
                continue

            pbxproj = os.path.join(dirpath, "project.pbxproj")
            project_file = Path(pbxproj)
            if not project_file.is_file() or not project_file.resolve().is_relative_to(canonical_root):
                continue

            try:
                cp = subprocess.run(
                    ["plutil", "-convert", "json", "-o", "-", pbxproj],
                    capture_output=True,
                )
                if cp.returncode != 0:
                    continue

                data: Dict[str, object] = json.loads(cp.stdout.decode("utf-8"))
            except Exception:
                continue

            objects: Dict[str, Dict[str, object]] = {}
            raw_objects = data.get("objects")
            if isinstance(raw_objects, dict):
                objects = {str(k): dict(v) for k, v in raw_objects.items() if isinstance(v, dict)}

            project_root = os.path.dirname(dirpath)
            file_ref_path: Dict[str, str] = {}
            file_ref_source: Dict[str, str] = {}
            build_file_ref: Dict[str, str] = {}
            sources_phase_files: Dict[str, list[str]] = {}

            for key, obj in objects.items():
                isa = obj.get("isa")

                if isa == "PBXFileReference":
                    path = obj.get("path") or obj.get("name")
                    if isinstance(path, str):
                        file_ref_path[key] = path
                    source_tree = obj.get("sourceTree")
                    if isinstance(source_tree, str):
                        file_ref_source[key] = source_tree

                elif isa == "PBXBuildFile":
                    fr = obj.get("fileRef")
                    if isinstance(fr, str):
                        build_file_ref[key] = fr

                elif isa == "PBXSourcesBuildPhase":
                    files = obj.get("files")
                    if isinstance(files, list):
                        sources_phase_files[key] = [str(f) for f in files]

            for key, obj in objects.items():
                if obj.get("isa") != "PBXNativeTarget":
                    continue

                name = obj.get("name")
                target_name = name if isinstance(name, str) else key
                phases = obj.get("buildPhases")
                phase_ids = [str(p) for p in phases] if isinstance(phases, list) else []

                target_sources = result.setdefault(target_name, set())

                for phase_id in phase_ids:
                    files = sources_phase_files.get(phase_id) or []
                    for build_file in files:
                        file_ref = build_file_ref.get(build_file)
                        path = file_ref_path.get(file_ref or "")
                        if not path:
                            continue
                        source_tree = file_ref_source.get(file_ref or "")
                        abs_path = path
                        if not os.path.isabs(abs_path):
                            base = project_root
                            if source_tree in {"SDKROOT", "DEVELOPER_DIR", "BUILT_PRODUCTS_DIR"}:
                                continue
                            abs_path = os.path.normpath(os.path.join(base, path))
                            if not os.path.exists(abs_path):
                                fallback = os.path.normpath(os.path.join(os.path.dirname(pbxproj), path))
                                if os.path.exists(fallback):
                                    abs_path = fallback
                                else:
                                    continue
                        try:
                            rel_path = Path(abs_path).resolve().relative_to(canonical_root)
                        except (ValueError, OSError, RuntimeError):
                            continue
                        target_sources.add(rel_path.as_posix())
        return result
    except Exception:
        return result
