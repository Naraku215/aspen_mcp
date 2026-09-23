"""真实 Python 入口与隔离桥的单位契约测试。"""
import json

import pytest


@pytest.mark.parametrize("entry", ["block", "alias", "path", "bridge_path", "bridge_parts"])
@pytest.mark.parametrize("value", [" 2.5e1 ", 25, 25.0])
def test_numeric_entrypoints(fake_windows, node_class, entry, value):
    from aspen_mcp.tools.blocks import tool_set_param
    from aspen_mcp.tools.paths import tool_set_value
    bridge, tree = fake_windows
    param = "P_OUT" if entry == "alias" else "TEMP"
    unit = "bar" if entry == "alias" else "C"
    path = rf"\Data\Blocks\B1\Input\{param}"
    node = tree.mount(path, node_class(row=20 if entry == "alias" else 22))
    if entry in ("block", "alias"):
        result = tool_set_param("B1", "PRES" if entry == "alias" else param, value, unit=unit)
    elif entry == "path":
        result = tool_set_value(path, value, unit=unit)
    elif entry == "bridge_path":
        result = bridge.set_path_value(path, value, unit=unit)
    else:
        result = bridge.set_value(value, *path.strip("\\").split("\\"), unit=unit)
    assert node.writes == [(5 if entry == "alias" else 4, 25.0)]
    assert "Error" not in str(result)


@pytest.mark.parametrize("entry", ["block", "path", "bridge_path"])
@pytest.mark.parametrize("value", [True, "NaN", "Inf", float("nan"), "1e999"])
def test_bad_numeric_entrypoints_never_write(fake_windows, node_class, entry, value):
    from aspen_mcp.tools.blocks import tool_set_param
    from aspen_mcp.tools.paths import tool_set_value
    bridge, tree = fake_windows
    path = r"\Data\Blocks\B1\Input\TEMP"
    node = tree.mount(path, node_class(row=22))
    if entry == "block":
        result = tool_set_param("B1", "TEMP", value)
    elif entry == "path":
        result = tool_set_value(path, value)
    else:
        result = bridge.set_path_value(path, value)
    assert node.writes == []
    assert "Error" in str(result)


@pytest.mark.parametrize("unit", [None, "K"])
def test_numeric_looking_name_remains_text(fake_windows, node_class, unit):
    from aspen_mcp.tools.paths import tool_set_value
    _, tree = fake_windows
    path = r"\Data\Blocks\B1\Input\NAME"
    node = tree.mount(path, node_class(value="100"))
    result = tool_set_value(path, "200", unit=unit)
    assert node.writes == ([("raw", "200")] if unit is None else [])
    assert ("Error" in result) == (unit is not None)


def test_matching_child_with_unit_rejected_without_parent_fallback(fake_windows, node_class):
    from aspen_mcp.tools.paths import tool_set_value
    _, tree = fake_windows
    path = r"\Data\Blocks\B1\Input\CHOICE"
    parent = tree.mount(path, node_class())
    child = tree.mount(path + r"\100", node_class(value="100"))
    result = tool_set_value(path, "100", unit="K")
    assert "Error" in result
    assert parent.writes == child.writes == []


@pytest.mark.parametrize("failure", ["write", "read", "mismatch"])
def test_write_and_verification_are_separate(fake_windows, node_class, failure):
    from aspen_mcp.tools.paths import tool_set_value
    _, tree = fake_windows
    path = r"\Data\Blocks\B1\Input\TEMP"
    node = tree.mount(path, node_class(row=22, value=12))
    if failure == "write":
        node.write_error = "native write failed"
    elif failure == "read":
        node.read_error = "native read failed"
    else:
        node.read_override = 10
    result = tool_set_value(path, 25, unit="C")
    assert "Error" in result
    report = json.loads(result.split(": ", 1)[1])
    assert report["write"]["status"] == ("failed" if failure == "write" else "ok")
    assert report["verify"]["status"] == ("not_attempted" if failure == "write" else "failed")
    if failure != "write":
        assert report["verify"]["read_source"] == "ValueForUnit(22,1)"
    assert len(node.writes) == 1


def test_get_si_does_not_mislabel_value(fake_windows, node_class):
    from aspen_mcp.tools.paths import tool_get_value
    _, tree = fake_windows
    path = r"\Data\Blocks\B1\Input\TEMP"
    node = tree.mount(path, node_class(row=22, value=999))
    node.read_error = "no SI"
    result = tool_get_value(path, "si")
    assert "Error" in result and "no SI" in result
    assert "999" not in result


@pytest.mark.parametrize("entry", ["tool", "bridge", "parts", "attribute"])
@pytest.mark.parametrize("suffix", ["FLOW\\MIXED\\A", "TOTAL\\MIXED", "TOTFLOW\\MIXED", "BASIS"])
def test_generic_paths_cannot_bypass_stream_capability(fake_windows, node_class, entry, suffix):
    from aspen_mcp.tools.paths import tool_set_value
    bridge, tree = fake_windows
    path = "\\Data\\Streams\\S\\Input\\" + suffix
    node = tree.mount(path, node_class(row=11, value=1))
    if entry == "tool":
        result = tool_set_value(path, 2)
    elif entry == "bridge":
        result = bridge.set_path_value(path, 2)
    elif entry == "parts":
        result = bridge.set_value(2, *path.strip("\\").split("\\"))
    else:
        result = bridge.set_node_attribute(path, 13, "MOLE-FLOW")
    assert node.writes == []
    assert result is False or "unsupported" in str(result)


@pytest.mark.parametrize("entry", ["tool", "bridge", "parts", "attribute"])
def test_unverified_path_guard_before_com_call(fake_windows, entry):
    from aspen_mcp.tools.paths import tool_set_value
    bridge, _ = fake_windows
    bridge.call = lambda fn: pytest.fail("未验证流股路径不得触发 COM")
    path = r"\Data\Streams\S\Input\FLOW\MIXED\A"
    if entry == "tool":
        result = tool_set_value(path, 1)
    elif entry == "bridge":
        result = bridge.set_path_value(path, 1)
    elif entry == "parts":
        result = bridge.set_value(1, *path.strip("\\").split("\\"))
    else:
        result = bridge.set_node_attribute(path, 13, "MOLE-FLOW")
    assert result is False or "unsupported" in str(result)


def test_child_write_failure_is_not_retried_on_parent(fake_windows, node_class):
    from aspen_mcp.tools.paths import tool_set_value
    _, tree = fake_windows
    path = r"\Data\Blocks\B\Input\CHOICE"
    parent = tree.mount(path, node_class())
    child = tree.mount(path + r"\100", node_class(value="100"))
    child.write_error = "child refused"
    result = tool_set_value(path, "100")
    assert "child refused" in result
    assert parent.writes == [] and len(child.writes) == 1


def test_heater_mode_write_failure_is_reported_separately(fake_windows, node_class):
    from aspen_mcp.tools.blocks import tool_set_param
    _, tree = fake_windows
    tree.mount(r"\Data\Blocks\H", node_class(value="HEATER"))
    temp = tree.mount(r"\Data\Blocks\H\Input\TEMP", node_class(row=22))
    mode = tree.mount(r"\Data\Blocks\H\Input\SPEC_OPT", node_class(value="CONST-DUTY"))
    mode.write_error = "mode refused"
    result = tool_set_param("H", "TEMP", 25, unit="C")
    assert "Error" in result and "mode refused" in result
    assert len(temp.writes) == len(mode.writes) == 1
