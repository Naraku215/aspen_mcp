r"""Tests for tool_set_unit_set: direct .Value assignment switching.

Verified 2026-09-02 on Aspen Plus V15 41.0: INSET/OUTSET are dropdown
enum nodes whose Elements access raises COM 2010; assigning node.Value
directly switches the unit set. The previous enum-child selection
approach was wrong and has been replaced.

Run:  venv\Scripts\python.exe tests\test_unitset_switch.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from types import SimpleNamespace

from aspen_mcp.tools import components


class FakeElements:
    def __init__(self, children):
        self._children = children

    def __call__(self, key):
        return self._children.get(key)


class FakeNode:
    def __init__(self, name="", children=None):
        self.Name = name
        self._value = None
        self.set_calls = []
        self.Elements = FakeElements(children or {})

    @property
    def Value(self):
        return self._value

    @Value.setter
    def Value(self, v):
        self._value = v

    def SetValue(self, idx, value):
        self.set_calls.append((idx, value))
        self._value = value


class RaisingValueNode(FakeNode):
    @property
    def Value(self):
        raise Exception("Value getter refused")

    @Value.setter
    def Value(self, v):
        raise Exception("Value setter refused")


class FakeAspen:
    def __init__(self, tree):
        self._app = SimpleNamespace(RootModel=lambda *a: tree)

    def call(self, fn):
        return fn()


def _global_tree(inset_raises=False, outset_raises=False, glob_ok=True):
    children = {}
    if glob_ok:
        children["GLOBDATASET"] = FakeNode("GLOBDATASET")
    children["INSET"] = RaisingValueNode("INSET") if inset_raises else FakeNode("INSET")
    children["OUTSET"] = RaisingValueNode("OUTSET") if outset_raises else FakeNode("OUTSET")
    inp = FakeNode("Input", children=children)
    glob_ = FakeNode("Global", children={"Input": inp})
    setup = FakeNode("Setup", children={"Global": glob_})
    data = FakeNode("Data", children={"Setup": setup})
    return FakeNode("", children={"Data": data})


def _with_fake(tree):
    class _Ctx:
        def __enter__(self):
            self.orig = components.aspen
            components.aspen = FakeAspen(tree)
            return self

        def __exit__(self, *exc):
            components.aspen = self.orig
    return _Ctx()


def _walk(tree, *parts):
    n = tree
    for p in parts:
        n = n.Elements(p)
        if n is None:
            return None
    return n


def test_set_unit_set_assigns_inset_outset_value():
    tree = _global_tree()
    inset = _walk(tree, "Data", "Setup", "Global", "Input", "INSET")
    outset = _walk(tree, "Data", "Setup", "Global", "Input", "OUTSET")
    with _with_fake(tree):
        result = components.tool_set_unit_set("METCBAR")
    assert "Unit set changed to METCBAR" in result
    assert "WARNING" not in result
    assert inset._value == "METCBAR"
    assert outset._value == "METCBAR"


def test_set_unit_set_globdataset_setvalue_used():
    tree = _global_tree()
    gd = _walk(tree, "Data", "Setup", "Global", "Input", "GLOBDATASET")
    with _with_fake(tree):
        components.tool_set_unit_set("SI")
    assert gd.set_calls == [(0, "SI")]


def test_set_unit_set_warns_when_inset_fails():
    tree = _global_tree(inset_raises=True)
    with _with_fake(tree):
        result = components.tool_set_unit_set("SI")
    assert "WARNING" in result and "INSET" in result


def test_set_unit_set_warns_when_outset_fails():
    tree = _global_tree(outset_raises=True)
    with _with_fake(tree):
        result = components.tool_set_unit_set("SI")
    assert "WARNING" in result and "OUTSET" in result


def test_set_unit_set_missing_globdataset():
    tree = _global_tree(glob_ok=False)
    with _with_fake(tree):
        result = components.tool_set_unit_set("SI")
    assert "GLOBDATASET node not found" in result


if __name__ == "__main__":
    import traceback
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception:
            print(f"  FAIL  {name}")
            traceback.print_exc()
            failed += 1
    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed, {len(tests)} total")
    sys.exit(1 if failed else 0)
