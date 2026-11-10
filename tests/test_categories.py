import pytest
from codemerge.core import categorise
from codemerge.models import Category

SPECIMENS = [
    ("src/main.py", False, Category.BACKEND),
    ("web/index.html", False, Category.FRONTEND),
    ("styles/site.css", False, Category.FRONTEND),
    ("docs/readme.md", False, Category.DOCS),
    ("src/docs/tutorial.txt", False, Category.DOCS),
    ("config/settings.yaml", False, Category.CONFIG),
    ("assets/logo.png", False, Category.ASSETS),
    ("image.JPG", False, Category.ASSETS),
    ("video/video.mp4", False, Category.ASSETS),
    ("bin/app", True, Category.BINARY),
    ("lib/module.so", True, Category.BINARY),
    ("README.RST", False, Category.DOCS),
    ("scripts/build.sh", False, Category.BACKEND),
    ("package.json", False, Category.CONFIG),
    ("node_modules/some.js", False, Category.FRONTEND),
    ("docs/guide/intro.mdx", False, Category.DOCS),
    ("src/component.jsx", False, Category.FRONTEND),
    ("src/app.ts", False, Category.FRONTEND),
    ("src/app.tsx", False, Category.FRONTEND),
    ("src/view.vue", False, Category.FRONTEND),
    ("data/image.gif", False, Category.ASSETS),
    ("docs/info.txt", False, Category.DOCS),
    ("config.toml", False, Category.CONFIG),
    ("theme/assets/icon.svg", False, Category.ASSETS),
    ("server/main.go", False, Category.BACKEND),
    ("docs/readme.TXT", False, Category.DOCS),
    ("setup.cfg", False, Category.CONFIG),
    ("logo.bmp", False, Category.ASSETS),
    ("makefile", False, Category.BACKEND),
    ("manual/docs/index.html", False, Category.DOCS),
]

def test_categorise_samples():
    for path, hint, expected in SPECIMENS:
        assert categorise(path, hint) is expected


@pytest.mark.parametrize("path", [
    "project/static/main.bundle",
    "myapp/public/runtime.d9f1c3a",
])
def test_static_public_is_frontend(path):
    assert categorise(path) is Category.FRONTEND
