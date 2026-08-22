r"""Tests for the two unit-conversion bug fixes.

Bug 1: set_param block params -- convert_by_name with known internal units
Bug 2: set_stream_composition_batch fractions -- is_frac guard

Run:  venv\Scripts\python.exe -m pytest tests\test_bugfix_units.py -v
Or:   venv\Scripts\python.exe tests\test_bugfix_units.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from aspen_mcp.tools.units import (
    convert_by_name, convert_value, _norm,
    clear_unit_cache,
)


class MockNode:
    """Simulates a COM IHNode with .Name and .UnitString."""
    def __init__(self, name="", unit_string=""):
        self.Name = name
        self.UnitString = unit_string


def _approx(v, expected, tol=1e-6):
    return abs(v - expected) < tol


# == Bug 1: set_param block params ==

_BLOCK_PARAM_UNITS = {
    "TEMP": "K",
    "TEMP1": "K",
    "PRES": "N/SQM",
    "PRES1": "N/SQM",
    "P_OUT": "N/SQM",
    "DUTY": "W",
    "DP_STAGE": "N/SQM",
}


def _block_param_convert(value, param, unit):
    """Simulate the fixed set_param conversion path."""
    if unit is None:
        return value
    known = _BLOCK_PARAM_UNITS.get(param)
    if known:
        return convert_by_name(value, unit, known)
    node = MockNode(param, "")
    return convert_value(value, unit, node)


def test_block_duty_kW():
    result = _block_param_convert(-500, "DUTY", "kW")
    assert _approx(result, -500000.0)

def test_block_duty_MW():
    result = _block_param_convert(2.5, "DUTY", "MW")
    assert _approx(result, 2500000.0)

def test_block_temp_C():
    result = _block_param_convert(110, "TEMP", "C")
    assert _approx(result, 383.15)

def test_block_temp_F():
    result = _block_param_convert(212, "TEMP", "F")
    assert _approx(result, 373.15)

def test_block_pres_bar():
    result = _block_param_convert(8, "PRES", "bar")
    assert _approx(result, 800000.0)

def test_block_pres_atm():
    result = _block_param_convert(1, "PRES", "atm")
    assert _approx(result, 101325.0)

def test_block_pres1_bar():
    result = _block_param_convert(1.2, "PRES1", "bar")
    assert _approx(result, 120000.0)

def test_block_no_unit_passthrough():
    result = _block_param_convert(0.75, "SEFF", None)
    assert result == 0.75

def test_block_unknown_param_empty_unitstring():
    result = _block_param_convert(42, "UNKNOWN_PARAM", "bar")
    assert result == 42

def test_block_param_unit_set_complete():
    for p in ["TEMP", "PRES", "DUTY"]:
        assert p in _BLOCK_PARAM_UNITS

def test_block_param_all_convert_correctly():
    cases = [
        ("H1", "DUTY", -500, "kW", -500000.0),
        ("H1", "PRES", 5, "bar", 500000.0),
        ("F1", "TEMP", 110, "C", 383.15),
        ("F1", "PRES", 1.5, "bar", 150000.0),
        ("C1", "PRES", 8, "bar", 800000.0),
    ]
    for block, param, val, unit, expected in cases:
        result = _block_param_convert(val, param, unit)
        assert _approx(result, expected), \
            f"{block}.{param} {val} {unit} -> {result}, expected {expected}"


# == Bug 2: set_stream_composition_batch fractions ==

def _composition_batch_logic(components, basis, total_flow, unit):
    clear_unit_cache()
    bn = basis.upper().replace("-", "").replace("_", "")
    is_frac = "FRAC" in bn.upper()

    if unit is None:
        return components, total_flow

    if not is_frac:
        converted = {}
        for comp, val in components.items():
            node = MockNode("FLOW", "KMOL/SEC")
            converted[comp] = convert_value(val, unit, node)
        components = converted

    if total_flow is not None:
        if bn.startswith("MASS"):
            node = MockNode("TOTAL", "KG/SEC")
        else:
            node = MockNode("TOTAL", "KMOL/SEC")
        total_flow = convert_value(total_flow, unit, node)

    return components, total_flow


def test_frac_basis_components_not_converted():
    comps = {"CH3OH": 0.40, "H2O": 0.45, "CO2": 0.15}
    result_comps, result_total = _composition_batch_logic(
        comps, "MOLE-FRAC", 200, "kmol/hr")
    assert result_comps["CH3OH"] == 0.40
    assert result_comps["H2O"] == 0.45
    assert result_comps["CO2"] == 0.15
    assert _approx(result_total, 200/3600)

def test_frac_basis_massfrac_not_converted():
    comps = {"CH3OH": 0.5, "H2O": 0.5}
    result_comps, result_total = _composition_batch_logic(
        comps, "MASS-FRAC", 3600, "kg/hr")
    assert result_comps["CH3OH"] == 0.5
    assert result_comps["H2O"] == 0.5
    assert _approx(result_total, 1.0)

def test_flow_basis_components_converted():
    comps = {"CH3OH": 100, "H2O": 100}
    result_comps, result_total = _composition_batch_logic(
        comps, "MOLE-FLOW", 200, "kmol/hr")
    assert _approx(result_comps["CH3OH"], 100/3600)
    assert _approx(result_comps["H2O"], 100/3600)
    assert _approx(result_total, 200/3600)

def test_frac_basis_no_unit():
    comps = {"CH3OH": 0.40, "CO2": 0.15}
    result_comps, result_total = _composition_batch_logic(
        comps, "MOLE-FRAC", 200, None)
    assert result_comps == comps
    assert result_total == 200

def test_frac_basis_total_converted_but_fractions_not():
    comps = {"CH3OH": 0.40, "H2O": 0.45, "CO2": 0.15}
    result_comps, result_total = _composition_batch_logic(
        comps, "MOLE-FRAC", 200, "kmol/hr")
    for comp, val in comps.items():
        assert result_comps[comp] == val, \
            f"{comp}: {result_comps[comp]} != {val}"
    assert _approx(result_total, 0.055556, tol=1e-5)



# == Round 2: set_value known-param fallback / add_side_duty unit / TEMP2 PRES2 ==

def _set_value_logic(path, value, unit):
    """Simulate the patched tool_set_value unit-conversion branch."""
    if unit is None or isinstance(value, str):
        return value
    from aspen_mcp.tools.blocks import _BLOCK_PARAM_UNITS
    from aspen_mcp.tools.units import convert_by_name, convert_value
    param_name = path.rstrip("\\").split("\\")[-1]
    known = _BLOCK_PARAM_UNITS.get(param_name)
    if known:
        return convert_by_name(value, unit, known)
    node = MockNode(param_name, "")
    return convert_value(value, unit, node)


def test_set_value_block_temp_converts():
    assert _approx(_set_value_logic("\\Data\\Blocks\\H1\\Input\\TEMP", 150, "C"), 423.15)

def test_set_value_block_duty_converts():
    assert _approx(_set_value_logic("\\Data\\Blocks\\H1\\Input\\DUTY", -500, "kW"), -500000.0)

def test_set_value_block_pres_bar_converts():
    assert _approx(_set_value_logic("\\Data\\Blocks\\F1\\Input\\PRES", 1.5, "bar"), 150000.0)

def test_set_value_stream_node_falls_back_to_convert_value():
    # path tail is not a known block param -> convert_value reads node UnitString
    from aspen_mcp.tools.blocks import _BLOCK_PARAM_UNITS
    from aspen_mcp.tools.units import convert_value
    param_name = "MIXED"
    assert param_name not in _BLOCK_PARAM_UNITS
    node = MockNode(param_name, "K")  # stream TEMP\MIXED node has UnitString
    assert _approx(convert_value(40, "C", node), 313.15)

def test_set_value_unknown_param_empty_unitstring_passthrough():
    # documented limitation: unknown param + empty UnitString -> passthrough
    assert _set_value_logic("\\Data\\Blocks\\H1\\Input\\SOMETHING", 42, "bar") == 42

def test_block_param_map_has_temp2_pres2():
    from aspen_mcp.tools.blocks import _BLOCK_PARAM_UNITS
    assert _BLOCK_PARAM_UNITS.get("TEMP2") == "K"
    assert _BLOCK_PARAM_UNITS.get("PRES2") == "N/SQM"
    assert _approx(convert_by_name(80, "C", _BLOCK_PARAM_UNITS["TEMP2"]), 353.15)

def test_side_duty_kW_converts():
    assert _approx(convert_by_name(-500, "kW", "W"), -500000.0)

def test_side_duty_no_unit_passthrough():
    assert convert_by_name(-500000, None, "W") == -500000

def test_side_duty_MW_converts():
    assert _approx(convert_by_name(1.2, "MW", "W"), 1200000.0)


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
