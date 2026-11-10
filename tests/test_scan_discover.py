from codemerge.filemerger import FileProcessor
from codemerge.filemerger.scan import discover_extensions


def test_discover_extensions_respects_excludes_and_depth(tmp_path):
    # Create source files with a mix of supported frontend extensions.
    (tmp_path / "app.ts").write_text("const answer = 42\n")
    (tmp_path / "component.jsx").write_text("export const C = () => null\n")

    src = tmp_path / "src"
    src.mkdir()
    (src / "feature.vue").write_text("<template>42</template>\n")
    (src / "widget.tsx").write_text("export const Widget = () => null\n")

    # Directories that should be ignored when respecting excludes.
    node_modules = src / "node_modules"
    node_modules.mkdir()
    (node_modules / "ignore.dont").write_text("skip\n")

    venv_dir = tmp_path / ".venv"
    venv_dir.mkdir()
    (venv_dir / "pyvenv.cfg").write_text("version = 3.11\n")
    (venv_dir / "module.py").write_text("print('ignore')\n")

    deep = tmp_path / "deep" / "level1"
    deep.mkdir(parents=True)
    (deep / "styles.scss").write_text(".app { color: red; }\n")

    fp = FileProcessor()
    fp.excluded_dirs.add("node_modules")

    all_exts = discover_extensions(fp, str(tmp_path))
    assert {"ts", "tsx", "jsx", "vue", "scss"}.issubset(all_exts)
    assert "dont" not in all_exts  # Skipped due to node_modules exclusion
    assert "py" not in all_exts  # Virtual env directory skipped

    shallow_exts = discover_extensions(fp, str(tmp_path), max_depth=1)
    assert "ts" in shallow_exts
    assert "tsx" in shallow_exts
    assert "vue" in shallow_exts
    assert "jsx" in shallow_exts
    assert "scss" not in shallow_exts  # Too deep when max_depth == 1
