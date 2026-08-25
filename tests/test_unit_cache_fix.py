r"""Tests for Bug 3 fix: UnitString cache clearing + INSET/OUTSET enum select.

Bug 3: units._unit_cache was never cleared -> stale UnitString after a
unit-set change (e.g. TEMP cached as "F" while INSET was ENG).
Related: tool_set_unit_set swallowed INSET/OUTSET SetValue failures.

Run with:  venv\Scripts\python.exe -m pytest tests\test_unit_cache_fix.py -v
Or standalone:  venv\Scripts\python.exe tests\test_unit_cache_fix.py
"""
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from types import SimpleNamespace

from aspen_mcp.tools.units import _unit_cache, clear_unit_cache
from aspen_mcp.tools import components, simulation


# ── Fakes ────────────────────────────────────────────────────────────────────

class FakeElements:
    """Callable stand-in for IHNode.Elements: Elements(name) / Elements(i)."""
    def __init__(self, children):
        self._children = children  # dict: name -> FakeNode

    @property
    def Count(self):
        return len(self._children)

    def __call__(self, key):
        if isinstance(key, int):
            vals = list(self._children.values())
            return vals[key] if 0 <= key < len(vals) else None
        return self._children.get(key)


class FakeNode:
    def __init__(self, name="", children=None):
        self.Name = name
        self.Value = None
        self._set_calls = []
        self.Elements = FakeElements(children or {})

    def SetValue(self, idx, value):
        self._set_calls.append((idx, value))
        self.Value = value


class RaisingNode(FakeNode):
    """A node whose SetValue always fails (e.g. enum refusing direct set)."""
    def SetValue(self, idx, value):
        raise Exception("SetValue refused")


class FakeAspen:
    def __init__(self, tree):
        self._app = SimpleNamespace(
            RootModel=lambda *a: tree,
            InitNew=lambda: None,
        )

    def call(self, fn):
        return fn()

    def reinit(self):
        pass


def _global_tree(inset_options=None, outset_options=None,
                 inset_raises=False, outset_raises=False):
    """Build root -> Data/Setup/Global/Input with GLOBDATASET/INSET/OUTSET nodes.

    inset_options: list of enum option names to expose as INSET children.
    inset_raises: give INSET a RaisingNode (both enum select and direct
    SetValue fail).
    """
    inp_children = {"GLOBDATASET": FakeNode("GLOBDATASET")}
    if inset_options is not None:
        inp_children["INSET"] = FakeNode(
            "INSET", children={c: FakeNode(c) for c in inset_options})
    elif inset_raises:
        inp_children["INSET"] = RaisingNode("INSET")
    if outset_options is not None:
        inp_children["OUTSET"] = FakeNode(
            "OUTSET", children={c: FakeNode(c) for c in outset_options})
    elif outset_raises:
        inp_children["OUTSET"] = RaisingNode("OUTSET")
    inp = FakeNode("Input", children=inp_children)
    glob_ = FakeNode("Global", children={"Input": inp})
    setup = FakeNode("Setup", children={"Global": glob_})
    data = FakeNode("Data", children={"Setup": setup})
    # RootModel("") returns the tree ROOT; walk() steps into "Data" first.
    return FakeNode("", children={"Data": data})


def _inset_node(tree):
    return tree.Elements("Data").Elements("Setup").Elements("Global") \
               .Elements("Input").Elements("INSET")


def _with_fake(mod, tree):
    """Context-style helper: swap mod.aspen for FakeAspen(tree)."""
    class _Ctx:
        def __enter__(self):
            self.orig = mod.aspen
            mod.aspen = FakeAspen(tree)
            return self

        def __exit__(self, *exc):
            mod.aspen = self.orig
    return _Ctx()


# ═══════════════════════════════════════════════════════════════════════════
#  Bug 3 fix tests
# ═══════════════════════════════════════════════════════════════════════════

def test_set_unit_set_clears_cache():
    _unit_cache["TEMP"] = "F"  # poison the cache
    tree = _global_tree(inset_options=["SI", "ENG"],
                        outset_options=["SI", "ENG"])
    with _with_fake(components, tree):
        result = components.tool_set_unit_set("SI")
    assert "Unit set changed to SI" in result
    assert "TEMP" not in _unit_cache, \
        "stale UnitString cache must be cleared after unit-set change"


def test_set_unit_set_selects_inset_enum_child():
    tree = _global_tree(inset_options=["SI", "ENG"],
                        outset_options=["SI", "ENG"])
    inset_node = _inset_node(tree)
    si_child = inset_node.Elements("SI")
    with _with_fake(components, tree):
        result = components.tool_set_unit_set("SI")
    assert "Unit set changed to SI" in result
    assert "WARNING" not in result
    assert len(si_child._set_calls) == 1, \
        "enum child option must be selected via SetValue"
    assert len(inset_node._set_calls) == 0, \
        "parent enum node must NOT be SetValue'd directly"


def test_set_unit_set_reports_inset_failure():
    tree = _global_tree(inset_raises=True,
                        outset_options=["SI", "ENG"])
    with _with_fake(components, tree):
        result = components.tool_set_unit_set("SI")
    assert "WARNING" in result, "INSET failure must not be silent"
    assert "INSET" in result
    assert "could not set" in result


def test_new_simulation_clears_cache():
    _unit_cache["TEMP"] = "F"  # poison the cache
    tree = _global_tree()
    with _with_fake(simulation, tree):
        result = simulation.tool_new_simulation()
    assert "New blank simulation created" in result
    assert "TEMP" not in _unit_cache, \
        "new simulation must clear the stale cache"


def test_set_unit_set_success_message_clean():
    clear_unit_cache()
    tree = _global_tree(inset_options=["SI", "ENG"],
                        outset_options=["SI", "ENG"])
    with _with_fake(components, tree):
        result = components.tool_set_unit_set("SI")
    assert "Unit set changed to SI" in result
    assert "WARNING" not in result


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
    if failed:
        sys.exit(1)
    else:
        print("ALL TESTS PASSED")
