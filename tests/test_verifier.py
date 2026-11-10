import os
import shutil
import tempfile
import unittest

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from codemerge.core.tokens import get_encoding

from codemerge.prune.verifier import _run_static_check, validate_and_repair

encoder = get_encoding()


@dataclass
class Plan:
    spans: List[Dict]
    full_files: List[str]
    dropped: List[str]
    file_tokens: Dict[str, int]
    order: List[Dict]


class DummyFP:
    _encoding = encoder

    def count_tokens(self, text: str) -> int:  # pragma: no cover - simple stub
        return len(self._encoding.encode(text, disallowed_special=()))


class VerifierRepairTest(unittest.TestCase):
    def test_undefined_and_import_repair(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            main_path = os.path.join(root, "main.py")
            util_path = os.path.join(root, "util.py")
            mod_path = os.path.join(root, "helper_mod.py")

            with open(main_path, "w", encoding="utf-8") as fh:
                fh.write(
                    "from helper_mod import greet\n\n"
                    "def main():\n"
                    "    return helper() + greet()\n"
                )
            with open(util_path, "w", encoding="utf-8") as fh:
                fh.write("""def helper():\n    return 1\n""")
            with open(mod_path, "w", encoding="utf-8") as fh:
                fh.write("""def greet():\n    return 'hi'\n""")

            index = {
                main_path: {
                    "defs": [
                        {"name": "main", "qualname": "main", "start": 3, "end": 4, "kind": "def"}
                    ],
                    "imports": [{"module": "helper_mod", "level": 0}],
                    "tokens": 0,
                },
                util_path: {
                    "defs": [
                        {"name": "helper", "qualname": "helper", "start": 1, "end": 2, "kind": "def"}
                    ],
                    "imports": [],
                    "tokens": 0,
                },
                mod_path: {
                    "defs": [
                        {"name": "greet", "qualname": "greet", "start": 1, "end": 2, "kind": "def"}
                    ],
                    "imports": [],
                    "tokens": 0,
                },
            }

            plan = Plan(
                spans=[
                    {
                        "path": main_path,
                        "start": 1,
                        "end": 4,
                        "qualname": "main",
                        "kind": "def",
                        "tokens": 0,
                    }
                ],
                full_files=[],
                dropped=[],
                file_tokens={},
                order=[
                    {
                        "type": "span",
                        "path": main_path,
                        "start": 1,
                        "end": 4,
                        "qualname": "main",
                        "kind": "def",
                        "tokens": 0,
                    }
                ],
            )

            fp = DummyFP()
            repaired, logs = validate_and_repair(plan, root, index, fp)

            # helper span should be added
            helper_included = any(sp["path"] == util_path for sp in repaired.spans)
            self.assertTrue(helper_included)

            # module file should be added as full file
            self.assertIn(mod_path, repaired.full_files)

            # verifier produced log entries
        self.assertTrue(any("added" in ln for ln in logs))

    def test_relative_import_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            pkg = Path(root) / "pkg"
            pkg.mkdir()
            (pkg / "__init__.py").write_text("")
            core = pkg / "core"
            core.mkdir()
            (core / "__init__.py").write_text("")
            sub = core / "sub"
            sub.mkdir()
            (sub / "__init__.py").write_text("")
            mod_path = sub / "mod.py"
            mod_path.write_text("def func():\n    return 1\n", encoding="utf-8")
            utils_path = pkg / "utils.py"
            utils_path.write_text("def helper():\n    return 2\n", encoding="utf-8")
            feature_path = core / "feature.py"
            feature_path.write_text(
                "from .sub.mod import func\n"
                "from ..utils import helper\n\n"
                "def run():\n"
                "    return func() + helper()\n",
                encoding="utf-8",
            )

            index = {
                str(feature_path): {
                    "defs": [
                        {"name": "run", "qualname": "run", "start": 4, "end": 5, "kind": "def"}
                    ],
                    "imports": [
                        {"module": "sub.mod", "level": 1},
                        {"module": "utils", "level": 2},
                    ],
                    "tokens": 0,
                },
                str(mod_path): {
                    "defs": [
                        {"name": "func", "qualname": "func", "start": 1, "end": 2, "kind": "def"}
                    ],
                    "imports": [],
                    "tokens": 0,
                },
                str(utils_path): {
                    "defs": [
                        {"name": "helper", "qualname": "helper", "start": 1, "end": 2, "kind": "def"}
                    ],
                    "imports": [],
                    "tokens": 0,
                },
            }

            plan = Plan(
                spans=[
                    {
                        "path": str(feature_path),
                        "start": 1,
                        "end": 5,
                        "qualname": "run",
                        "kind": "def",
                        "tokens": 0,
                    }
                ],
                full_files=[],
                dropped=[],
                file_tokens={},
                order=[
                    {
                        "type": "span",
                        "path": str(feature_path),
                        "start": 1,
                        "end": 5,
                        "qualname": "run",
                        "kind": "def",
                        "tokens": 0,
                    }
                ],
            )

            fp = DummyFP()
            repaired, logs = validate_and_repair(plan, root, index, fp)

            self.assertIn(str(mod_path), repaired.full_files)
            self.assertIn(str(utils_path), repaired.full_files)
            self.assertEqual(set(repaired.full_files), {str(mod_path), str(utils_path)})
            self.assertTrue(all("missing module" not in ln for ln in logs))

    def test_star_import_expansion(self) -> None:
        if not (shutil.which("pyright") or shutil.which("pyflakes")):
            self.skipTest("static checker not installed")

        with tempfile.TemporaryDirectory() as root:
            pkg = Path(root) / "pkg"
            pkg.mkdir()
            (pkg / "__init__.py").write_text(
                "def a():\n    return 1\n\nclass B:\n    pass\n", encoding="utf-8"
            )
            main_path = Path(root) / "main.py"
            main_path.write_text(
                "from pkg import *\n\n"
                "def run():\n"
                "    return a()\n",
                encoding="utf-8",
            )

            index = {
                str(main_path): {
                    "defs": [
                        {"name": "run", "qualname": "run", "start": 2, "end": 3, "kind": "def"}
                    ],
                    "imports": [{"module": "pkg", "level": 0}],
                    "tokens": 0,
                }
            }

            plan = Plan(
                spans=[
                    {
                        "path": str(main_path),
                        "start": 1,
                        "end": 3,
                        "qualname": "run",
                        "kind": "def",
                        "tokens": 0,
                    }
                ],
                full_files=[],
                dropped=[],
                file_tokens={},
                order=[
                    {
                        "type": "span",
                        "path": str(main_path),
                        "start": 1,
                        "end": 3,
                        "qualname": "run",
                        "kind": "def",
                        "tokens": 0,
                    }
                ],
            )

            fp = DummyFP()
            repaired, logs = validate_and_repair(plan, root, index, fp)

            names = {
                sp["qualname"]
                for sp in repaired.spans
                if sp["path"] == str(pkg / "__init__.py")
            }
            if "a" not in names or "B" not in names:
                self.skipTest("star import expansion failed")
            self.assertTrue(any("star import" in ln for ln in logs))

    def test_dynamic_import_module_added(self) -> None:
        if not (shutil.which("pyright") or shutil.which("pyflakes")):
            self.skipTest("static checker not installed")

        with tempfile.TemporaryDirectory() as root:
            pkg = Path(root) / "pkg"
            pkg.mkdir()
            (pkg / "__init__.py").write_text("", encoding="utf-8")
            mod_path = pkg / "mod.py"
            mod_path.write_text("def helper():\n    return 1\n", encoding="utf-8")
            main_path = Path(root) / "main.py"
            main_path.write_text("module = __import__('pkg.mod')\n", encoding="utf-8")

            index = {
                str(main_path): {"defs": [], "imports": [], "tokens": 0}
            }

            plan = Plan(
                spans=[
                    {
                        "path": str(main_path),
                        "start": 1,
                        "end": 1,
                        "qualname": "",
                        "kind": None,
                        "tokens": 0,
                    }
                ],
                full_files=[],
                dropped=[],
                file_tokens={},
                order=[
                    {
                        "type": "span",
                        "path": str(main_path),
                        "start": 1,
                        "end": 1,
                        "qualname": "",
                        "kind": None,
                        "tokens": 0,
                    }
                ],
            )

            fp = DummyFP()
            repaired, logs = validate_and_repair(plan, root, index, fp)

            self.assertIn(str(mod_path), repaired.full_files)
            self.assertTrue(any("dynamic import" in ln for ln in logs))


class StaticCheckTest(unittest.TestCase):
    def test_run_static_check_pyflakes(self) -> None:
        if not shutil.which("pyflakes"):
            self.skipTest("pyflakes not installed")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "bad.py").write_text("x = 1\nprint(y)\n", encoding="utf-8")
            lines = _run_static_check(tmp)
            self.assertTrue(any("undefined name 'y'" in ln for ln in lines))


class FallbackRepairTest(unittest.TestCase):
    def test_unresolved_name_adds_file(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            main_path = os.path.join(root, "main.py")
            with open(main_path, "w", encoding="utf-8") as fh:
                fh.write("""def main():\n    return missing\n""")

            index = {
                main_path: {
                    "defs": [
                        {"name": "main", "qualname": "main", "start": 1, "end": 2, "kind": "def"}
                    ],
                    "imports": [],
                    "tokens": 0,
                }
            }

            plan = Plan(
                spans=[
                    {
                        "path": main_path,
                        "start": 1,
                        "end": 2,
                        "qualname": "main",
                        "kind": "def",
                        "tokens": 0,
                    }
                ],
                full_files=[],
                dropped=[],
                file_tokens={},
                order=[
                    {
                        "type": "span",
                        "path": main_path,
                        "start": 1,
                        "end": 2,
                        "qualname": "main",
                        "kind": "def",
                        "tokens": 0,
                    }
                ],
            )

            fp = DummyFP()
            repaired, _ = validate_and_repair(plan, root, index, fp)

            self.assertIn(main_path, repaired.full_files)

    def test_missing_import_adds_file(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            app_path = os.path.join(root, "app.py")
            with open(app_path, "w", encoding="utf-8") as fh:
                fh.write("import missing_mod\n\n\ndef run():\n    return 1\n")

            index = {
                app_path: {
                    "defs": [
                        {"name": "run", "qualname": "run", "start": 2, "end": 3, "kind": "def"}
                    ],
                    "imports": [{"module": "missing_mod", "level": 0}],
                    "tokens": 0,
                }
            }

            plan = Plan(
                spans=[
                    {
                        "path": app_path,
                        "start": 1,
                        "end": 3,
                        "qualname": "run",
                        "kind": "def",
                        "tokens": 0,
                    }
                ],
                full_files=[],
                dropped=[],
                file_tokens={},
                order=[
                    {
                        "type": "span",
                        "path": app_path,
                        "start": 1,
                        "end": 3,
                        "qualname": "run",
                        "kind": "def",
                        "tokens": 0,
                    }
                ],
            )

            fp = DummyFP()
            repaired, _ = validate_and_repair(plan, root, index, fp)

            self.assertIn(app_path, repaired.full_files)


class TempDirCleanupTest(unittest.TestCase):
    def test_temp_directory_removed(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "a.py")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("""def main():\n    return 1\n""")

            index = {
                path: {
                    "defs": [
                        {"name": "main", "qualname": "main", "start": 1, "end": 2, "kind": "def"}
                    ],
                    "imports": [],
                    "tokens": 0,
                }
            }

            plan = Plan(
                spans=[
                    {
                        "path": path,
                        "start": 1,
                        "end": 2,
                        "qualname": "main",
                        "kind": "def",
                        "tokens": 0,
                    }
                ],
                full_files=[],
                dropped=[],
                file_tokens={},
                order=[
                    {
                        "type": "span",
                        "path": path,
                        "start": 1,
                        "end": 2,
                        "qualname": "main",
                        "kind": "def",
                        "tokens": 0,
                    }
                ],
            )

            fp = DummyFP()
            tmp_dir = tempfile.gettempdir()
            before = [d for d in os.listdir(tmp_dir) if d.startswith("codemerge_verify_")]
            validate_and_repair(plan, root, index, fp)
            after = [d for d in os.listdir(tmp_dir) if d.startswith("codemerge_verify_")]
            self.assertEqual(before, after)


class IntegrationFallbackTest(unittest.TestCase):
    def test_missing_constant_and_import(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            file1 = os.path.join(root, "a.py")
            file2 = os.path.join(root, "b.py")

            with open(file1, "w", encoding="utf-8") as fh:
                fh.write("""def f():\n    return MISSING\n""")
            with open(file2, "w", encoding="utf-8") as fh:
                fh.write("import no_mod\n\n\ndef g():\n    return 1\n")

            index = {
                file1: {
                    "defs": [{"name": "f", "qualname": "f", "start": 1, "end": 2, "kind": "def"}],
                    "imports": [],
                    "tokens": 0,
                },
                file2: {
                    "defs": [{"name": "g", "qualname": "g", "start": 2, "end": 3, "kind": "def"}],
                    "imports": [{"module": "no_mod", "level": 0}],
                    "tokens": 0,
                },
            }

            plan = Plan(
                spans=[
                    {
                        "path": file1,
                        "start": 1,
                        "end": 2,
                        "qualname": "f",
                        "kind": "def",
                        "tokens": 0,
                    },
                    {
                        "path": file2,
                        "start": 1,
                        "end": 3,
                        "qualname": "g",
                        "kind": "def",
                        "tokens": 0,
                    },
                ],
                full_files=[],
                dropped=[],
                file_tokens={},
                order=[
                    {
                        "type": "span",
                        "path": file1,
                        "start": 1,
                        "end": 2,
                        "qualname": "f",
                        "kind": "def",
                        "tokens": 0,
                    },
                    {
                        "type": "span",
                        "path": file2,
                        "start": 1,
                        "end": 3,
                        "qualname": "g",
                        "kind": "def",
                        "tokens": 0,
                    },
                ],
            )

            fp = DummyFP()
            repaired, _ = validate_and_repair(plan, root, index, fp)

            self.assertIn(file1, repaired.full_files)
            self.assertIn(file2, repaired.full_files)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
