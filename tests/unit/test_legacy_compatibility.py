"""旧位置参数消歧、签名一致、服务导入安全。"""
import importlib
import inspect

import pytest


@pytest.mark.parametrize("method", ["set_stream_param", "set_stream_composition", "set_stream_composition_batch", "set_value", "set_path_value"])
def test_bridge_signatures_identical(method):
    from aspen_mcp.bridge.base import AspenBridge
    from aspen_mcp.bridge.stub import StubBridge
    from aspen_mcp.bridge.windows import WindowsBridge
    signatures = [inspect.signature(getattr(cls, method)) for cls in (AspenBridge, StubBridge, WindowsBridge)]
    assert signatures[0] == signatures[1] == signatures[2]


@pytest.mark.parametrize("legacy", [(" C ",), (None, "C"), ("C", None)])
def test_deterministic_legacy_unit_positions(fake_windows, node_class, legacy):
    bridge, tree = fake_windows
    node = tree.mount(r"\Data\Streams\S\Input\TEMP\MIXED", node_class(row=22))
    result = bridge.set_stream_param("S", "TEMP", " 25 ", *legacy)
    assert isinstance(result, dict) and result["ok"], result
    assert node.writes == [(4, 25.0)]


@pytest.mark.parametrize("legacy,kwargs", [
    (("C", "K"), {}), (("MOLE-FLOW", "MASS-FLOW"), {}),
    (("unknown",), {}), (("C",), {"unit": "K"}),
    (("MOLE-FLOW",), {"basis": "MOLE-FLOW"}),
])
def test_ambiguous_positions_rejected_before_call(fake_windows, legacy, kwargs):
    bridge, _ = fake_windows
    bridge.call = lambda fn: pytest.fail("歧义未在调用前拒绝")
    result = bridge.set_stream_param("S", "TEMP", 25, *legacy, **kwargs)
    assert result["status"] == "invalid"
    assert not result["ok"]


def test_tools_delegate_with_keywords(monkeypatch):
    from aspen_mcp.tools import streams
    class KeywordBridge:
        def set_stream_param(self, *, stream_name, param, value, basis, unit, total_basis):
            return {"ok": True, "basis": basis, "unit": unit}
        def set_stream_composition(self, *, stream_name, component, flow, unit, basis, total_basis):
            return {"ok": True}
        def set_stream_composition_batch(self, *, stream_name, components, basis, total_flow, unit, total_basis, total_unit):
            return {"ok": True}
    monkeypatch.setattr(streams, "aspen", KeywordBridge())
    assert streams.tool_set_stream_param("S", "TEMP", 25, unit="C")["ok"]
    assert streams.tool_set_stream_composition("S", "A", 1)["ok"]
    assert streams.tool_set_stream_composition_batch("S", {"A": 1})["ok"]


def test_stub_default_does_not_self_authorize_composition():
    from aspen_mcp.bridge.stub import StubBridge
    bridge = StubBridge()
    bridge.add_stream_stub("S")
    before = list(bridge.calls)
    result = bridge.set_stream_composition_batch("S", {"A": 1})
    assert isinstance(result, dict) and result["status"] == "unsupported"
    assert bridge.calls == before


def test_stub_path_uses_same_unit_contract(node_class):
    from aspen_mcp.bridge.stub import StubBridge
    bridge = StubBridge()
    tree = node_class()
    node = tree.mount(r"\Data\Blocks\B\Input\TEMP", node_class(row=22))
    bridge._tree = tree
    result = bridge.set_path_value(r"\Data\Blocks\B\Input\TEMP", "25", unit="C")
    assert "Error" not in result
    assert node.writes == [(4, 25.0)]


def test_server_import_uses_injected_fake_factory():
    from aspen_mcp.bridge.stub import StubBridge
    module = importlib.import_module("aspen_mcp.server")
    assert isinstance(module.aspen, StubBridge)
    assert module.aspen.calls == []
