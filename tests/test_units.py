r"""Unit tests for units.py — pure function layer, no COM/Aspen required.

Run with:  venv\Scripts\python.exe -m pytest tests\test_units.py -v
Or standalone:  venv\Scripts\python.exe tests\test_units.py
"""
import sys, os

# Ensure src on path when running standalone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ── Mock COM node ──────────────────────────────────────────────────────────
class MockNode:
    """Simulates a COM IHNode with .Name and .UnitString."""
    def __init__(self, name: str = "", unit_string: str = ""):
        self.Name = name
        self.UnitString = unit_string


# ── Import the module under test ────────────────────────────────────────────
from aspen_mcp.tools.units import (
    convert_value,
    convert_by_name,
    _norm,
    _CONVERSIONS,
    clear_unit_cache,
    format_conversion,
    describe_node_unit,
    _unit_cache,
)


# ── Helpers ────────────────────────────────────────────────────────────────
def _approx(v, expected, tol=1e-6):
    """Float comparison with tolerance."""
    return abs(v - expected) < tol


# ═══════════════════════════════════════════════════════════════════════════
#  1. Temperature conversions (internal = K)
# ═══════════════════════════════════════════════════════════════════════════
def test_temp_celsius():
    node = MockNode("TEMP", "K")
    assert _approx(convert_value(25, "C", node), 298.15)
    assert _approx(convert_value(0, "C", node), 273.15)
    assert _approx(convert_value(-40, "C", node), 233.15)

def test_temp_fahrenheit():
    node = MockNode("TEMP", "K")
    assert _approx(convert_value(32, "F", node), 273.15)
    assert _approx(convert_value(212, "F", node), 373.15)
    assert _approx(convert_value(-40, "F", node), 233.15)

def test_temp_kelvin_passthrough():
    node = MockNode("TEMP", "K")
    assert _approx(convert_value(298.15, "K", node), 298.15)

def test_temp_aliases():
    node = MockNode("TEMP", "K")
    assert _approx(convert_value(25, "DEGC", node), 298.15)
    assert _approx(convert_value(32, "DEGF", node), 273.15)
    assert _approx(convert_value(491.67, "RANKINE", node), 273.15)

def test_temp_celsius_long_form():
    node = MockNode("TEMP", "K")
    assert _approx(convert_value(100, "CELSIUS", node), 373.15)
    assert _approx(convert_value(100, "FAHRENHEIT", node), 310.9278, tol=1e-3)


# ═══════════════════════════════════════════════════════════════════════════
#  2. Pressure conversions (internal = N/SQM = Pa)
# ═══════════════════════════════════════════════════════════════════════════
def test_pres_bar():
    node = MockNode("PRES", "N/SQM")
    assert _approx(convert_value(1, "bar", node), 100000.0)
    assert _approx(convert_value(1.5, "BAR", node), 150000.0)

def test_pres_atm():
    node = MockNode("PRES", "N/SQM")
    assert _approx(convert_value(1, "atm", node), 101325.0)

def test_pres_psi():
    node = MockNode("PRES", "N/SQM")
    assert _approx(convert_value(14.696, "psi", node), 101325.0, tol=1.0)

def test_pres_kpa_mpa():
    node = MockNode("PRES", "N/SQM")
    assert _approx(convert_value(100, "kPa", node), 100000.0)
    assert _approx(convert_value(1, "MPa", node), 1_000_000.0)

def test_pres_aliases():
    node = MockNode("PRES", "N/SQM")
    assert _approx(convert_value(1, "mbar", node), 100.0)
    assert _approx(convert_value(760, "mmHg", node), 101325.0, tol=1.0)
    assert _approx(convert_value(760, "torr", node), 101325.0, tol=1.0)
    assert _approx(convert_value(1, "bara", node), 100000.0)

def test_pres_passthrough():
    node = MockNode("PRES", "N/SQM")
    assert _approx(convert_value(100000, "Pa", node), 100000.0)
    assert _approx(convert_value(100000, "N/SQM", node), 100000.0)


# ═══════════════════════════════════════════════════════════════════════════
#  3. Molar flow conversions (internal = kmol/sec)
# ═══════════════════════════════════════════════════════════════════════════
def test_molar_kmolhr():
    node = MockNode("FLOW", "KMOL/SEC")
    assert _approx(convert_value(40, "kmol/hr", node), 40 / 3600.0)
    assert _approx(convert_value(3600, "kmol/hr", node), 1.0)

def test_molar_aliases():
    node = MockNode("FLOW", "KMOL/SEC")
    assert _approx(convert_value(40, "kmol/h", node), 40 / 3600.0)

def test_molar_molhr():
    node = MockNode("FLOW", "KMOL/SEC")
    assert _approx(convert_value(3600000, "mol/hr", node), 1.0)

def test_molar_lbmolhr():
    node = MockNode("FLOW", "KMOL/SEC")
    assert _approx(convert_value(7936.64, "lbmol/hr", node), 1.0, tol=1e-3)

def test_molar_passthrough():
    node = MockNode("FLOW", "KMOL/SEC")
    assert _approx(convert_value(0.01, "kmol/sec", node), 0.01)
    assert _approx(convert_value(0.01, "kmol/s", node), 0.01)


# ═══════════════════════════════════════════════════════════════════════════
#  4. Mass flow conversions (internal = kg/sec)
# ═══════════════════════════════════════════════════════════════════════════
def test_mass_kghr():
    node = MockNode("MASSFLOW", "KG/SEC")
    assert _approx(convert_value(3600, "kg/hr", node), 1.0)

def test_mass_aliases():
    node = MockNode("MASSFLOW", "KG/SEC")
    assert _approx(convert_value(3600, "kg/h", node), 1.0)
    assert _approx(convert_value(3.6, "t/hr", node), 1.0)

def test_mass_lblr():
    node = MockNode("MASSFLOW", "KG/SEC")
    assert _approx(convert_value(7936.64, "lb/hr", node), 1.0, tol=1e-3)


# ═══════════════════════════════════════════════════════════════════════════
#  5. Energy / duty conversions (internal = W)
# ═══════════════════════════════════════════════════════════════════════════
def test_energy_kw_mw():
    node = MockNode("DUTY", "W")
    assert _approx(convert_value(1, "kW", node), 1000.0)
    assert _approx(convert_value(1, "MW", node), 1_000_000.0)

def test_energy_kjhr():
    node = MockNode("DUTY", "W")
    assert _approx(convert_value(3600, "kJ/hr", node), 1000.0)

def test_energy_aliases():
    node = MockNode("DUTY", "W")
    assert _approx(convert_value(1, "hp", node), 745.7, tol=1e-1)
    assert _approx(convert_value(3412.14, "btu/hr", node), 1000.0, tol=0.5)
    assert _approx(convert_value(860.42, "kcal/hr", node), 1000.0, tol=0.5)


# ═══════════════════════════════════════════════════════════════════════════
#  6. Pass-through cases (unit=None, dimensionless)
# ═══════════════════════════════════════════════════════════════════════════
def test_unit_none_passthrough():
    node = MockNode("TEMP", "K")
    assert convert_value(298.15, None, node) == 298.15
    assert convert_value(100000, None, node) == 100000

def test_empty_unitstring_passthrough():
    """Dimensionless nodes (VFRAC, stage count) have empty UnitString."""
    node = MockNode("VFRAC", "")
    assert convert_value(0.5, "C", node) == 0.5  # no conversion on dimensionless
    assert convert_value(0.5, None, node) == 0.5


# ═══════════════════════════════════════════════════════════════════════════
#  7. Error handling
# ═══════════════════════════════════════════════════════════════════════════
def test_unknown_unit_raises():
    node = MockNode("TEMP", "K")
    try:
        convert_value(25, "xyz", node)
        assert False, "should have raised ValueError"
    except ValueError as e:
        assert "No unit conversion registered" in str(e)

def test_unknown_node_unit_raises():
    node = MockNode("WEIRD", "FOOBAR")
    try:
        convert_value(25, "C", node)
        assert False, "should have raised ValueError"
    except ValueError as e:
        assert "No unit conversion registered" in str(e)


# ═══════════════════════════════════════════════════════════════════════════
#  8. _norm function
# ═══════════════════════════════════════════════════════════════════════════
def test_norm_uppercase():
    assert _norm("c") == "C"
    assert _norm("bar") == "BAR"
    assert _norm("kmol/hr") == "KMOL/HR"

def test_norm_strip_spaces():
    assert _norm("  C  ") == "C"
    assert _norm(" kmol / hr ") == "KMOL/HR"
    assert _norm("N / SQM") == "N/SQM"


# ═══════════════════════════════════════════════════════════════════════════
#  9. Cache management
# ═══════════════════════════════════════════════════════════════════════════
def test_cache_populated_and_cleared():
    node = MockNode("TEMP", "K")
    _unit_cache.clear()
    convert_value(25, "C", node)
    assert "TEMP" in _unit_cache
    assert _unit_cache["TEMP"] == "K"
    clear_unit_cache()
    assert len(_unit_cache) == 0


# ═══════════════════════════════════════════════════════════════════════════
# 10. format_conversion helper
# ═══════════════════════════════════════════════════════════════════════════
def test_format_conversion_with_unit():
    s = format_conversion(25, "C", 298.15, "K")
    assert "25" in s and "C" in s and "298.15" in s and "K" in s

def test_format_conversion_without_unit():
    s = format_conversion(298.15, None, 298.15, "K")
    assert "298.15" in s


# ═══════════════════════════════════════════════════════════════════════════
# 11. describe_node_unit helper
# ═══════════════════════════════════════════════════════════════════════════
def test_describe_node_unit():
    node = MockNode("TEMP", "K")
    s = describe_node_unit(node)
    assert "TEMP" in s and "K" in s


# ═══════════════════════════════════════════════════════════════════════════
# 12. Conversion table completeness sanity
# ═══════════════════════════════════════════════════════════════════════════
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


# ═══════════════════════════════════════════════════════════════════════════
# 13. convert_by_name (no COM node needed — uses known internal units)
# ═══════════════════════════════════════════════════════════════════════════
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

def test_convert_by_name_alias():
    assert _approx(convert_by_name(25, "degC", "K"), 298.15)
    assert _approx(convert_by_name(40, "kmol/h", "KMOL/SEC"), 40 / 3600.0)

def test_convert_by_name_error():
    try:
        convert_by_name(25, "xyz", "K")
        assert False, "should have raised"
    except ValueError as e:
        assert "No unit conversion registered" in str(e)


# ── Standalone runner ───────────────────────────────────────────────────────
if __name__ == "__main__":
    # Run all test functions when executed directly (no pytest needed)
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
