r"""Unit tests for units.py — pure function layer, no COM/Aspen required.

Covers the stage-2 native-unit mechanism:
  - convert_value / convert_by_name: python conversion table (SI bases)
  - write_value: native SetValueAndUnit write, python fallback
  - read_si: ValueForUnit(row, si_col) read with node.Value fallback

Run with:  venv\Scripts\python.exe -m pytest tests\test_units.py -v
Or standalone:  venv\Scripts\python.exe tests\test_units.py
"""
import sys, os
import pytest

# Ensure src on path when running standalone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ── Mock COM node (native unit API surface) ───────────────────────────────
class MockNode:
    """Simulates a COM IHNode with AttributeValue/SetValueAndUnit/ValueForUnit."""
    def __init__(self, name="", row=None, value=None, unit_string="", col=1):
        self.Name = name
        self.UnitString = unit_string
        self.Value = value
        self._row = row
        self._col = col
        self.svau_calls = []   # (value, col)
        self.set_calls = []    # (idx, value)
        self.vfu_calls = []    # (row, col)

    def AttributeValue(self, attr):
        if attr == 2:
            if self._row is None:
                raise ValueError("no row")
            return self._row
        if attr == 3:
            return self._col
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


from aspen_mcp.tools.units import (
    convert_value,
    convert_by_name,
    _norm,
    _CONVERSIONS,
    _UNIT_COORDS,
    read_si,
    write_value,
    format_conversion,
    describe_node_unit,
)


# ── Helpers ───────────────────────────────────────────────────────────────
def _approx(v, expected, tol=1e-6):
    """Float comparison with tolerance."""
    return abs(v - expected) < tol


def _raises_valueerror(fn):
    try:
        fn()
    except ValueError as e:
        return e
    raise AssertionError("expected ValueError, got no exception")


# ═══ 1. Temperature conversions (SI base = K) ═════════════════════════
def test_temp_celsius():
    assert _approx(convert_value(25, "C"), 298.15)
    assert _approx(convert_value(0, "C"), 273.15)
    assert _approx(convert_value(-40, "C"), 233.15)

def test_temp_fahrenheit():
    assert _approx(convert_value(32, "F"), 273.15)
    assert _approx(convert_value(212, "F"), 373.15)
    assert _approx(convert_value(-40, "F"), 233.15)

def test_temp_kelvin_passthrough():
    assert _approx(convert_value(298.15, "K"), 298.15)

def test_temp_aliases():
    assert _approx(convert_value(25, "DEGC"), 298.15)
    assert _approx(convert_value(32, "DEGF"), 273.15)
    assert _approx(convert_value(491.67, "RANKINE"), 273.15)

def test_temp_celsius_long_form():
    assert _approx(convert_value(100, "CELSIUS"), 373.15)
    assert _approx(convert_value(100, "FAHRENHEIT"), 310.9278, tol=1e-3)


# ═══ 2. Pressure conversions (SI base = N/SQM = Pa) ══════════════════
def test_pres_bar():
    assert _approx(convert_value(1, "bar"), 100000.0)
    assert _approx(convert_value(1.5, "BAR"), 150000.0)

def test_pres_atm():
    assert _approx(convert_value(1, "atm"), 101325.0)

def test_pres_psi():
    assert _approx(convert_value(14.696, "psi"), 101325.0, tol=1.0)

def test_pres_kpa_mpa():
    assert _approx(convert_value(100, "kPa"), 100000.0)
    assert _approx(convert_value(1, "MPa"), 1_000_000.0)

def test_pres_aliases():
    assert _approx(convert_value(1, "mbar"), 100.0)
    assert _approx(convert_value(760, "mmHg"), 101325.0, tol=1.0)
    assert _approx(convert_value(760, "torr"), 101325.0, tol=1.0)
    assert _approx(convert_value(1, "bara"), 100000.0)

def test_pres_passthrough():
    assert _approx(convert_value(100000, "Pa"), 100000.0)
    assert _approx(convert_value(100000, "N/SQM"), 100000.0)


# ═══ 3. Molar flow conversions (SI base = kmol/sec) ══════════════════
def test_molar_kmolhr():
    assert _approx(convert_value(40, "kmol/hr"), 40 / 3600.0)
    assert _approx(convert_value(3600, "kmol/hr"), 1.0)

def test_molar_aliases():
    assert _approx(convert_value(40, "kmol/h"), 40 / 3600.0)

def test_molar_molhr():
    assert _approx(convert_value(3600000, "mol/hr"), 1.0)

def test_molar_lbmolhr():
    assert _approx(convert_value(7936.64, "lbmol/hr"), 1.0, tol=1e-3)

def test_molar_passthrough():
    assert _approx(convert_value(0.01, "kmol/sec"), 0.01)
    assert _approx(convert_value(0.01, "kmol/s"), 0.01)


# ═══ 4. Mass flow conversions (SI base = kg/sec) ═════════════════════
def test_mass_kghr():
    assert _approx(convert_value(3600, "kg/hr"), 1.0)

def test_mass_aliases():
    assert _approx(convert_value(3600, "kg/h"), 1.0)
    assert _approx(convert_value(3.6, "t/hr"), 1.0)

def test_mass_lblr():
    assert _approx(convert_value(7936.64, "lb/hr"), 1.0, tol=1e-3)


# ═══ 5. Energy / duty conversions (SI base = W) ══════════════════════
def test_energy_kw_mw():
    assert _approx(convert_value(1, "kW"), 1000.0)
    assert _approx(convert_value(1, "MW"), 1_000_000.0)

def test_energy_kjhr():
    assert _approx(convert_value(3600, "kJ/hr"), 1000.0)

def test_energy_aliases():
    assert _approx(convert_value(1, "hp"), 745.7, tol=1e-1)
    assert _approx(convert_value(3412.14, "btu/hr"), 1000.0, tol=0.5)
    assert _approx(convert_value(860.42, "kcal/hr"), 1000.0, tol=0.5)


# ═══ 6. Pass-through (unit=None) ═════════════════════════════════════
def test_unit_none_passthrough():
    assert convert_value(298.15, None) == 298.15
    assert convert_value(100000, None) == 100000


# ═══ 7. Error handling ══════════════════════════════════════════════
def test_unknown_unit_raises():
    e = _raises_valueerror(lambda: convert_value(25, "xyz"))
    assert "No unit conversion registered" in str(e)


# ═══ 8. _norm function ══════════════════════════════════════════════
def test_norm_uppercase():
    assert _norm("c") == "C"
    assert _norm("bar") == "BAR"
    assert _norm("kmol/hr") == "KMOL/HR"

def test_norm_strip_spaces():
    assert _norm("  C  ") == "C"
    assert _norm(" kmol / hr ") == "KMOL/HR"
    assert _norm("N / SQM") == "N/SQM"


# ═══ 9. Native coordinate table ═════════════════════════════════════
def test_unit_coords_major_entries():
    assert _UNIT_COORDS["C"] == (22, 4)
    assert _UNIT_COORDS["F"] == (22, 2)
    assert _UNIT_COORDS["K"] == (22, 1)
    assert _UNIT_COORDS["BAR"] == (20, 5)
    assert _UNIT_COORDS["MPA"] == (20, 20)
    assert _UNIT_COORDS["KMOL/HR"] == (11, 3)
    assert _UNIT_COORDS["KW"] == (13, 13)

def test_coord_units_match_conversion_table():
    """Every coordinated unit also has a python table entry to its SI base."""
    si_bases = {11: "KMOL/SEC", 13: "W", 20: "N/SQM", 22: "K"}
    for u, (row, _col) in _UNIT_COORDS.items():
        assert (u, si_bases[row]) in _CONVERSIONS, f"{u} -> {si_bases[row]}"


# ═══ 10. write_value ════════════════════════════════════════════════
def test_write_unit_unset_uses_si_col():
    node = MockNode("TEMP", row=22)
    write_value(node, 300)
    assert node.svau_calls == [(300, 1)]

def test_write_native_coord_celsius():
    node = MockNode("TEMP", row=22)
    write_value(node, 40, "C")
    assert node.svau_calls == [(40, 4)]

def test_write_native_coord_fahrenheit():
    node = MockNode("TEMP", row=22)
    write_value(node, 212, "F")
    assert node.svau_calls == [(212, 2)]

def test_write_native_coord_bar():
    node = MockNode("PRES", row=20)
    write_value(node, 5, "bar")
    assert node.svau_calls == [(5, 5)]

def test_write_native_coord_kmolhr():
    node = MockNode("FLOW", row=11)
    write_value(node, 200, "kmol/hr")
    assert node.svau_calls == [(200, 3)]

def test_write_python_fallback_hp():
    # "hp" has no native column -> python table -> SI col write
    node = MockNode("DUTY", row=13)
    write_value(node, 10, "hp", base_internal="W")
    assert len(node.svau_calls) == 1
    v, col = node.svau_calls[0]
    assert col == 1 and _approx(v, 7457.0)

def test_write_unknown_unit_raises():
    e = _raises_valueerror(
        lambda: write_value(MockNode("DUTY", row=13), 10, "SNAIL/HR",
                            base_internal="W"))
    assert "No unit conversion registered" in str(e)

def test_write_unknown_row_rejected():
    node = MockNode("VFRAC", row=None)
    with pytest.raises(ValueError):
        write_value(node, 0.5)
    assert node.svau_calls == node.set_calls == []

def test_write_svau_exception_does_not_retry_raw():
    class _NoSvauNode(MockNode):
        def SetValueAndUnit(self, value, col):
            raise RuntimeError("SetValueAndUnit refused")
    node = _NoSvauNode("TEMP", row=22)
    with pytest.raises(RuntimeError, match="SetValueAndUnit refused"):
        write_value(node, 300)
    assert node.svau_calls == []
    assert node.set_calls == []

def test_write_none_node_raises():
    e = _raises_valueerror(lambda: write_value(None, 1))
    assert "node is None" in str(e)

def test_write_node_returns_none():
    node = MockNode("TEMP", row=22)
    assert write_value(node, 300) is None


# ═══ 11. read_si ════════════════════════════════════════════════════
def test_read_si_with_row():
    node = MockNode("TEMP", row=22, value=313.15)
    assert read_si(node) == 313.15
    assert node.vfu_calls == [(22, 1)]

def test_read_si_unknown_row_rejected():
    node = MockNode("VFRAC", row=None, value=0.5)
    with pytest.raises(ValueError):
        read_si(node)

def test_read_si_vfu_exception_never_returns_raw():
    class _NoVfuNode(MockNode):
        def ValueForUnit(self, row, col):
            raise RuntimeError("ValueForUnit refused")
    node = _NoVfuNode("TEMP", row=22, value=313.15)
    with pytest.raises(ValueError, match="ValueForUnit refused"):
        read_si(node)

def test_read_si_none_node():
    assert read_si(None) is None


# ═══ 12. convert_by_name ════════════════════════════════════════════
def test_convert_by_name_temp():
    assert _approx(convert_by_name(25, "C", "K"), 298.15)
    assert _approx(convert_by_name(0, "C", "K"), 273.15)

def test_convert_by_name_pres():
    assert _approx(convert_by_name(1, "bar", "N/SQM"), 100000.0)
    assert _approx(convert_by_name(2, "bar", "N/SQM"), 200000.0)

def test_convert_by_name_flow():
    assert _approx(convert_by_name(40, "kmol/hr", "KMOL/SEC"), 40 / 3600.0)
    assert _approx(convert_by_name(3600, "kmol/hr", "KMOL/SEC"), 1.0)

def test_convert_by_name_none_passthrough():
    assert convert_by_name(298.15, None, "K") == 298.15
    assert convert_by_name(100000, None, "N/SQM") == 100000

def test_convert_by_name_same_unit_passthrough():
    # Bug-4 regression: same-unit pairs must NOT go through the table
    assert convert_by_name(5.0, "F", "F") == 5.0
    assert convert_by_name(1.0, "bar", "BAR") == 1.0
    assert convert_by_name(2.0, "degC", "DEGC") == 2.0
    assert convert_by_name(0.5, "kmol/hr", "KMOL/HR") == 0.5

def test_convert_by_name_alias():
    assert _approx(convert_by_name(25, "degC", "K"), 298.15)
    assert _approx(convert_by_name(40, "kmol/h", "KMOL/SEC"), 40 / 3600.0)

def test_convert_by_name_error():
    e = _raises_valueerror(lambda: convert_by_name(25, "xyz", "K"))
    assert "No unit conversion registered" in str(e)


# ═══ 13. format_conversion / describe_node_unit ═════════════════════
def test_format_conversion_with_unit():
    s = format_conversion(25, "C", 298.15, "K")
    assert "25" in s and "C" in s and "298.15" in s and "K" in s

def test_format_conversion_without_unit():
    s = format_conversion(298.15, None, 298.15, "K")
    assert "298.15" in s

def test_describe_node_unit():
    node = MockNode("TEMP", unit_string="K")
    s = describe_node_unit(node)
    assert "TEMP" in s and "K" in s


# ═══ 14. Conversion table completeness sanity ═══════════════════════
def test_conversion_table_has_all_categories():
    """Verify the table covers all 5 physical quantity categories."""
    temps = [k for k in _CONVERSIONS if k[1] == "K"]
    press = [k for k in _CONVERSIONS if k[1] == "N/SQM"]
    molar = [k for k in _CONVERSIONS if k[1] == "KMOL/SEC"]
    mass = [k for k in _CONVERSIONS if k[1] == "KG/SEC"]
    energy = [k for k in _CONVERSIONS if k[1] == "W"]
    assert len(temps) >= 6, f"temperature: only {len(temps)} entries"
    assert len(press) >= 10, f"pressure: only {len(press)} entries"
    assert len(molar) >= 5, f"molar flow: only {len(molar)} entries"
    assert len(mass) >= 5, f"mass flow: only {len(mass)} entries"
    assert len(energy) >= 6, f"energy: only {len(energy)} entries"


@pytest.mark.parametrize("value", [" 2.5e1 ", 25, 25.0])
def test_numeric_representations(value):
    node = MockNode("TEMP", row=22)
    write_value(node, value, "C")
    assert node.svau_calls == [(25.0, 4)]
    assert convert_value(value, "C") == pytest.approx(298.15)


@pytest.mark.parametrize("value", [True, False, float("nan"), float("inf"), "NaN", "-Inf", "1e999", "abc"])
def test_invalid_numeric_values_never_write(value):
    node = MockNode("TEMP", row=22)
    with pytest.raises(ValueError):
        write_value(node, value, "K")
    with pytest.raises(ValueError):
        convert_by_name(value, None, "K")
    assert node.svau_calls == node.set_calls == []


@pytest.mark.parametrize("row,col,unit,base", [
    (22, 1, "bar", None), (20, 1, "C", None), (22, 1, "C", "W"),
    (None, 1, "C", "K"), (999, 1, None, None), (True, 1, None, None),
    (22.5, 1, None, None), (22, True, None, None), (22, -1, None, None),
    (22, 1.5, None, None), (22, 999, None, None), (0, 0, "K", None),
    (22, 1, "", None),
])
def test_dimension_and_coordinate_validation(row, col, unit, base):
    node = MockNode(row=row, col=col)
    with pytest.raises(ValueError):
        write_value(node, 1, unit, base_internal=base)
    assert node.svau_calls == node.set_calls == []


def test_dimensionless_separate_contract():
    node = MockNode("VFRAC", row=0, col=0, value=0.1)
    write_value(node, " 5e-1 ")
    assert node.set_calls == [(0, 0.5)]
    assert read_si(node) == 0.5
    assert node.vfu_calls == []


def test_conversion_with_node_checks_dimension_for_shared_callers():
    with pytest.raises(ValueError):
        convert_value(1, "C", MockNode("PRES1", row=20))


def test_conversion_overflow_rejected():
    with pytest.raises(ValueError):
        convert_by_name(1e308, "MW", "W")


def test_read_diagnostics_keep_actual_source_and_error():
    from aspen_mcp.tools.units import read_si_result
    class Broken(MockNode):
        def ValueForUnit(self, row, col):
            raise RuntimeError("native read failed")
    result = read_si_result(Broken(row=22, value=999))
    assert result["value"] is None
    assert result["read_source"] == "ValueForUnit(22,1)"
    assert "native read failed" in result["error"]
    assert result["ok"] is False


# ── Standalone runner ───────────────────────────────────────────────────────
def _param_combinations(fn):
    """展开 pytest.mark.parametrize（含多层叠加）；普通测试返回 [{}]。"""
    markers = getattr(fn, "pytestmark", [])
    if not isinstance(markers, list):
        markers = [markers]
    combos = [{}]
    for mark in markers:
        if getattr(mark, "name", "") != "parametrize":
            continue
        argnames, argvalues = mark.args[0], mark.args[1]
        names = ([n.strip() for n in argnames.split(",")]
                 if isinstance(argnames, str) else list(argnames))
        expanded = []
        for base in combos:
            for values in argvalues:
                values = values if isinstance(values, tuple) else (values,)
                merged = dict(base)
                merged.update(dict(zip(names, values)))
                expanded.append(merged)
        combos = expanded
    return combos


if __name__ == "__main__":
    import traceback
    tests = [(name, fn) for name, fn in sorted(globals().items())
            if name.startswith("test_") and callable(fn)]
    passed = 0
    failed = 0
    total = 0
    for name, fn in tests:
        for kwargs in _param_combinations(fn):
            total += 1
            label = name if not kwargs else f"{name}[{','.join(map(str, kwargs.values()))}]"
            try:
                fn(**kwargs)
                print(f"  PASS  {label}")
                passed += 1
            except Exception:
                print(f"  FAIL  {label}")
                traceback.print_exc()
                failed += 1
    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed, {total} total")
    if failed:
        sys.exit(1)
    else:
        print("ALL TESTS PASSED")
