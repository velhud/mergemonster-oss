import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from codemerge.core.tokens import get_encoding

from codemerge.prune.utils import list_python_defs, prune_python_defs  # noqa: E402

encoder = get_encoding()


def test_list_and_prune_handles_decorators_and_nested(tmp_path):
    src = (
        "@dec1\n"
        "@dec2\n"
        "\n"
        "def top():\n"
        "    pass\n"
        "\n"
        "class Outer:\n"
        "\n"
        "    @staticmethod\n"
        "    def keep():\n"
        "        pass\n"
        "\n"
        "    def drop(self):\n"
        "        pass\n"
        "\n"
        "    class Inner:\n"
        "        pass\n"
    )
    p = tmp_path / "mod.py"
    p.write_text(src)
    spans = {s.qualname: s for s in list_python_defs(str(p))}
    assert spans["top"].start == 1
    assert spans["Outer.keep"].start == 9
    assert "Outer.Inner" in spans
    pruned = prune_python_defs(str(p), {"Outer.keep"}, {"top", "Outer.drop", "Outer"})
    assert "top" not in pruned
    assert "drop" not in pruned
    assert pruned.splitlines()[0] == "class Outer:"
    assert "class Inner" in pruned
