r"""Regression tests for the unit-conversion bug fixes (stage-2 semantics).

Bug 1: block params convert even when the node carries no UnitString
       (now handled natively by units.write_value with the known-unit map)
Bug 2: fraction compositions must not be unit-converted (guard lives in
       bridge.WindowsBridge.set_stream_composition_batch: batch_is_frac)
Bug 4: same-unit convert_by_name must pass through unchanged

Run:  venv\Scripts\python.exe -m pytest tests\test_bugfix_units.py -v
Or:   venv\Scripts\python.exe tests\test_bugfix_units.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from aspen_mcp.tools.units import (
    convert_by_name, convert_value, _norm, write_value,
)
from aspen_mcp.tools.blocks import _BLOCK_PARAM_UNITS


class MockNode:
    """COM IHNode stand-in with the native unit API surface."""
    def __init__(self, name="", row=None, unit_string=""):
        self.Name = name
        self.UnitString = unit_string
        self.Value = None
        self._row = row
        self.svau_calls = []
        self.set_calls = []
        self.vfu_calls = []

    def AttributeValue(self, attr):
        if attr == 2:
            if self._row is None:
                raise ValueError("no row")
            return self._row
        if attr == 81:
            return self.UnitString
        raise ValueError(f"attr {attr} not simulated")

    def SetValueAndUnit(self, value, col):
        self.svau_calls.append((value, col))
        self.Value = value

    def ValueForUnit(self, row, col):
        self.vfu_calls.append((row, col))
        return self.Value

    def SetValue(self, idx, value):
        self.set_calls.append((idx, value))
        self.Value = value


def _approx(v, expected, tol=1e-6):
    return abs(v - expected) < tol


# == Bug 1: block params via write_value (native col OR python fallback) ==

_ROWS = {"TEMP": 22, "TEMP1": 22, "TEMP2": 22, "PRES": 20, "PRES1": 20,
         "PRES2": 20, "P_OUT": 20, "DP_STAGE": 20, "DUTY": 13}


def _block_param_write(param, value, unit):
    node = MockNode(param, row=_ROWS.get(param))
    write_value(node, value, unit, base_internal=_BLOCK_PARAM_UNITS.get(param))
    return node


def test_block_temp_C_native_col():
    node = _block_param_write("TEMP", 110, "C")
    assert node.svau_calls == [(110, 4)]


def test_block_temp_F_native_col():
    node = _block_param_write("TEMP", 212, "F")
    assert node.svau_calls == [(212, 2)]


def test_block_pres_bar_native_col():
    node = _block_param_write("PRES", 8, "bar")
    assert node.svau_calls == [(8, 5)]


def test_block_duty_kW_native_col():
    node = _block_param_write("DUTY", -500, "kW")
    assert node.svau_calls == [(-500, 13)]


def test_block_no_unit_writes_si_col():
    node = _block_param_write("TEMP", 298.15, None)
    assert node.svau_calls == [(298.15, 1)]


def test_block_unknown_unit_python_fallback():
    # "hp" has no native column -> python table with the known base "W"
    node = _block_param_write("DUTY", 10, "hp")
    assert len(node.svau_calls) == 1
    v, col = node.svau_calls[0]
    assert col == 1 and _approx(v, 7457.0)
    assert node.set_calls == []


def test_block_param_map_complete():
    for p in ["TEMP", "TEMP1", "TEMP2", "PRES", "PRES1", "PRES2",
              "P_OUT", "DUTY", "DP_STAGE"]:
        assert p in _BLOCK_PARAM_UNITS


# == Bug 2: fraction compositions stay unit-free (raw write) ==
# The guard now lives in the bridge (batch_is_frac): components get
# write_value(node, val, None) while total_flow keeps the user unit.
# Python-side: fraction writes with unit=None go through the SI column
# on a row'd node and fall back to SetValue on dimensionless nodes.

def test_frac_component_raw_write_with_row():
    node = MockNode("CH3OH", row=11)
    write_value(node, 0.40, None, base_internal="KMOL/SEC")
    assert node.svau_calls == [(0.40, 1)]


def test_frac_component_raw_write_no_row():
    node = MockNode("CH3OH")  # dimensionless fraction node
    write_value(node, 0.15, None)
    assert node.set_calls == [(0, 0.15)]


def test_frac_total_flow_keeps_unit():
    node = MockNode("TOTAL", row=11)
    write_value(node, 200, "kmol/hr")
    assert node.svau_calls == [(200, 3)]


# == Bug 4: same-unit pairs pass through unchanged ==

def test_convert_by_name_same_unit_passthrough():
    assert convert_by_name(5.0, "F", "F") == 5.0
    assert convert_by_name(1.0, "bar", "BAR") == 1.0
    assert convert_by_name(2.0, "degC", "DEGC") == 2.0
    assert convert_by_name(0.5, "kmol/hr", "KMOL/HR") == 0.5


def test_convert_by_name_normalizes():
    assert _approx(convert_by_name(25, "C", "K"), 298.15)
    assert _approx(convert_by_name(1, "bar", "N/SQM"), 100000.0)


def test_convert_by_name_unknown_raises():
    try:
        convert_by_name(5, "SNAIL/HR", "K")
        assert False, "should have raised"
    except ValueError as e:
        assert "No unit conversion" in str(e)


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
