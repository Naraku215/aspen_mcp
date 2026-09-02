r"""Tests for get_value attribute views (stage-2 semantics).

Views: "value" (default, display), "si" (ValueForUnit internal SI),
"attr" (AttributeValue(0) = HAP_VALUE raw attribute),
"unit" (UnitString label).

Run:  venv\Scripts\python.exe tests\test_attrvalue_diag.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from types import SimpleNamespace

from aspen_mcp.tools import paths


class FakeNode:
    """Node exposing the views tool_get_value can read."""
    def __init__(self, value=0, attr0=0, row=22, si_value=313.15, unit_string="F"):
        self.Value = value
        self._attr0 = attr0
        self._row = row
        self._si = si_value
        self.UnitString = unit_string
        self.vfu_calls = []

    def AttributeValue(self, attr):
        if attr == 0:
            return self._attr0
        if attr == 2:
            return self._row
        if attr == 81:
            return self.UnitString
        raise ValueError(f"attr {attr} not simulated")

    def ValueForUnit(self, row, col):
        self.vfu_calls.append((row, col))
        return self._si

    def FindNode(self, path):
        return self


class _NoNodeTree:
    def FindNode(self, path):
        return None


class FakeAspen:
    def __init__(self, tree):
        self._app = SimpleNamespace(RootModel=lambda *a: tree)

    def call(self, fn):
        return fn()


def _with_fake(tree):
    class _Ctx:
        def __enter__(self):
            self.orig = paths.aspen
            paths.aspen = FakeAspen(tree)
            return self

        def __exit__(self, *exc):
            paths.aspen = self.orig
    return _Ctx()


# Display value 104.0 (F) vs internal SI 313.15 (K): the two views differ
# when the unit set is ENG, proving which view each attribute maps to.
_NODE = FakeNode(value=104.0, attr0=104.0, row=22, si_value=313.15,
                 unit_string="F")


def test_get_value_default_returns_display_value():
    with _with_fake(_NODE):
        result = paths.tool_get_value(r"\Data\Streams\FEED\Input\TEMP")
    assert result == "104.0"


def test_get_value_si_returns_internal_si():
    with _with_fake(_NODE):
        result = paths.tool_get_value(r"\Data\Streams\FEED\Input\TEMP",
                                      attribute="si")
    assert result == "313.15"
    assert _NODE.vfu_calls == [(22, 1)]


def test_get_value_attr_returns_hap_value():
    with _with_fake(_NODE):
        result = paths.tool_get_value(r"\Data\Streams\FEED\Input\TEMP",
                                      attribute="attr")
    assert result == "104.0"  # AttributeValue(0) = last entered raw value


def test_get_value_unit_returns_unitstring():
    with _with_fake(_NODE):
        result = paths.tool_get_value(r"\Data\Streams\FEED\Input\TEMP",
                                      attribute="unit")
    assert result == "F"


def test_get_value_explicit_value_view():
    with _with_fake(_NODE):
        result = paths.tool_get_value(r"X", attribute="value")
    assert result == "104.0"


def test_get_value_node_not_found():
    with _with_fake(_NoNodeTree()):
        result = paths.tool_get_value(r"\Data\Nowhere")
    assert "Node not found" in result


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
