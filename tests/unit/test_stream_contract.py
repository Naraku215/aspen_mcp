"""流股能力 fixture 只代表离线模型，不代表原生 Aspen 验证。"""
import importlib
import math

import pytest

BASES = ("MOLE-FRAC", "MASS-FRAC", "MOLE-FLOW", "MASS-FLOW")
TOTALS = ("MOLE-FLOW", "MASS-FLOW")


def contract():
    assert importlib.util.find_spec("aspen_mcp.stream_contract") is not None, "缺少纯 Python 流股契约"
    return importlib.import_module("aspen_mcp.stream_contract")


class VerifiedFake:
    """组件固定为 A/B；MW=2，仅用来检验跨总量基准。"""
    def __init__(self, module):
        self.capabilities = {
            (b, t): module._VerifiedCapability(evidence="offline-fixture:MW=2", basis=b, total_basis=t)
            for b in BASES for t in TOTALS
        }
        self.components = {"A": 0.8, "B": 0.2}
        self.basis = "MOLE-FRAC"
        self.total_basis = "MOLE-FLOW"
        self.total = 1.0
        self.events = []
        self.fail = None
        self.bad_read = None
        self.offset = 0.0

    def component_ids(self, stream):
        return ("A", "B")

    def preflight(self, stream, plan):
        if self.fail == "preflight":
            raise ValueError("missing target B")

    def write_basis(self, stream, basis, total_basis):
        self.events.append("basis")
        if self.fail == "basis":
            raise RuntimeError("basis write failed")
        self.basis, self.total_basis = basis, total_basis
        return "OK"

    def read_basis(self, stream):
        if self.bad_read == "basis":
            return {"basis": "UNKNOWN", "total_basis": self.total_basis, "read_source": "fake:basis"}
        return {"basis": self.basis, "total_basis": self.total_basis, "read_source": "fake:basis"}

    def write_total(self, stream, value):
        self.events.append("total")
        if self.fail == "total":
            raise RuntimeError("total write failed")
        self.total = value

    def write_component(self, stream, component, value):
        self.events.append("component:" + component)
        if self.fail == component:
            raise RuntimeError("component write failed")
        self.components[component] = value

    def read_component(self, stream, component):
        if self.bad_read == component:
            raise RuntimeError("component read failed")
        return {"value": self.components[component] + self.offset, "read_source": "fake:component:" + component}

    def read_total(self, stream):
        if "FRAC" in self.basis:
            value = self.total
        else:
            value = sum(self.components.values())
            if self.basis != self.total_basis:
                value *= 2 if self.total_basis == "MASS-FLOW" else 0.5
        return {"value": value, "read_source": "fake:total"}


@pytest.fixture
def verified(fake_windows):
    module = contract()
    bridge, _ = fake_windows
    adapter = VerifiedFake(module)
    bridge._stream_adapter = adapter
    return module, bridge, adapter


def test_production_capability_matrix_is_all_unverified():
    matrix = contract().capability_matrix()
    assert len(matrix) == 8
    assert all(item["status"] == "unverified" for item in matrix)
    matrix[0]["status"] = "verified"
    assert all(item["status"] == "unverified" for item in contract().capability_matrix())


@pytest.mark.parametrize("basis", BASES)
def test_production_batch_unsupported_before_bridge_call(fake_windows, basis):
    bridge, _ = fake_windows
    bridge.call = lambda fn: pytest.fail("能力未验证时不得进入 COM")
    result = bridge.set_stream_composition_batch("FEED", {"A": 0.4, "B": 0.6}, basis=basis, total_flow=1)
    assert isinstance(result, dict), result
    assert result["status"] == "unsupported"
    assert result["attempted"] == []


@pytest.mark.parametrize("basis", BASES)
@pytest.mark.parametrize("total_basis", TOTALS)
def test_all_verified_fixture_combinations(verified, basis, total_basis):
    module, bridge, adapter = verified
    frac = "FRAC" in basis
    values = {"A": 0.4, "B": 0.6} if frac else {"A": 2, "B": 3}
    total = 10 if frac else 5
    if not frac and basis != total_basis:
        total *= 2 if total_basis == "MASS-FLOW" else 0.5
    result = bridge.set_stream_composition_batch("FEED", values, basis=basis,
                                                total_flow=total, total_basis=total_basis)
    assert result["ok"], result
    assert result["status"] == "ok"
    assert adapter.components == values
    assert result["capability_evidence"] == "offline-fixture:MW=2"
    assert result["verify"]["total"]["read_source"] == "fake:total"
    assert all(item["status"] == "unverified" for item in module.capability_matrix())


@pytest.mark.parametrize("values,basis,total", [
    ({"A": -1, "B": 2}, "MOLE-FRAC", 1),
    ({"A": True, "B": 0}, "MOLE-FRAC", 1),
    ({"A": math.nan, "B": 1}, "MOLE-FRAC", 1),
    ({"A": 0.4, "B": 0.5}, "MOLE-FRAC", 1),
    ({"A": 0.4, "B": 0.6}, "MOLE-FRAC", 0),
    ({"A": 0.4, "B": 0.6}, "MOLE-FRAC", None),
    ({"A": 0, "B": 0}, "MOLE-FLOW", None),
    ({"A": 1, "B": 2}, "MOLE-FLOW", 6),
    ({"A": 1}, "MOLE-FRAC", 1),
    ({"A": 0.4, "B": 0.6, "C": 0}, "MOLE-FRAC", 1),
])
def test_invalid_or_incomplete_vector_never_writes(verified, values, basis, total):
    _, bridge, adapter = verified
    result = bridge.set_stream_composition_batch("FEED", values, basis=basis, total_flow=total)
    assert not result["ok"]
    assert adapter.events == []
    assert result["attempted"] == []


@pytest.mark.parametrize("failure", ["basis", "basis_read", "total", "A", "B", "read_A", "preflight"])
def test_failure_scope_and_fail_fast(verified, failure):
    _, bridge, adapter = verified
    if failure == "basis_read":
        adapter.bad_read = "basis"
    elif failure == "read_A":
        adapter.bad_read = "A"
    else:
        adapter.fail = failure
    result = bridge.set_stream_composition_batch("FEED", {"A": 0.4, "B": 0.6}, basis="MOLE-FRAC", total_flow=10)
    assert not result["ok"]
    assert result["attempted"] == adapter.events
    assert "not_attempted" in result and "possibly_changed" in result
    if failure in ("basis", "basis_read"):
        assert adapter.events == ["basis"]
        assert result["not_attempted"] == ["total", "component:A", "component:B"]
    if failure == "read_A":
        assert adapter.events == ["basis", "total", "component:A"]
        assert "component read failed" in str(result)


def test_flow_sum_is_not_multiplied_by_total_and_unit_conversion(verified):
    _, bridge, adapter = verified
    result = bridge.set_stream_composition_batch("FEED", {"A": " 7.2e3 ", "B": 10800},
        basis="MOLE-FLOW", total_flow=18000, unit="kmol/hr")
    assert result["ok"], result
    assert adapter.components == {"A": 2, "B": 3}
    assert result["verify"]["total"]["expected"] == 5


def test_fraction_unit_applies_only_to_independent_total(verified):
    _, bridge, adapter = verified
    result = bridge.set_stream_composition_batch("FEED", {"A": 0.4, "B": 0.6}, basis="MOLE-FRAC",
        total_basis="MASS-FLOW", total_flow=3600, total_unit="kg/hr")
    assert result["ok"], result
    assert adapter.components == {"A": 0.4, "B": 0.6}
    assert adapter.total == 1


@pytest.mark.parametrize("offset,ok", [(5e-7, True), (2e-6, False)])
def test_fraction_closure_tolerance(verified, offset, ok):
    _, bridge, adapter = verified
    result = bridge.set_stream_composition_batch("FEED", {"A": 0.4, "B": 0.6 + offset},
        basis="MOLE-FRAC", total_flow=1)
    assert result["ok"] is ok
    if not ok:
        assert adapter.events == []


@pytest.mark.parametrize("offset,ok", [(1e-10, True), (1e-4, False)])
def test_readback_tolerances(verified, offset, ok):
    _, bridge, adapter = verified
    adapter.offset = offset
    result = bridge.set_stream_composition_batch("FEED", {"A": 0, "B": 1}, basis="MOLE-FRAC", total_flow=1)
    assert result["ok"] is ok


def test_single_basis_ok_is_not_treated_as_error(verified):
    from aspen_mcp.tools.streams import tool_set_stream_param
    _, _, adapter = verified
    result = tool_set_stream_param("FEED", "A", 0.4, basis="MOLE-FRAC")
    assert result["ok"], result
    assert result["scope"] == "partial"
    assert adapter.events == ["basis", "component:A"]


def test_request_cannot_supply_capability(fake_windows):
    bridge, _ = fake_windows
    with pytest.raises(TypeError):
        bridge.set_stream_composition_batch("FEED", {"A": 1}, capability={"verified": True})


def test_unknown_inspect_is_read_only(fake_windows, node_class):
    from aspen_mcp.tools.streams import tool_get_stream_composition_info
    _, tree = fake_windows
    node = tree.mount(r"\Data\Streams\FEED\Output\COMPTYPE", node_class(value="UNKNOWN"))
    result = tool_get_stream_composition_info("FEED")
    assert result["comptype"] == "UNKNOWN"
    assert all(c["status"] == "unverified" for c in result["capabilities"])
    assert node.writes == []


@pytest.mark.parametrize("changed", ["component", "basis"])
def test_complete_update_rechecks_final_state(verified, changed):
    _, bridge, adapter = verified
    write = adapter.write_component
    def corrupt_after_b(stream, component, value):
        write(stream, component, value)
        if component == "B":
            if changed == "component":
                adapter.components["A"] = 0.9
            else:
                adapter.basis = "MASS-FRAC"
    adapter.write_component = corrupt_after_b
    result = bridge.set_stream_composition_batch("FEED", {"A": 0.4, "B": 0.6}, basis="MOLE-FRAC", total_flow=1)
    assert not result["ok"]
    assert result["status"] == "partial_failure"


@pytest.mark.parametrize("kwargs", [
    {"unit": "C", "total_unit": "kg/hr", "total_basis": "MASS-FLOW"},
    {"unit": "kmol/hr", "total_unit": "kg/hr", "total_basis": "MASS-FLOW"},
    {"total_basis": ""}, {"total_basis": "MOLE-FRAC"},
])
def test_total_unit_and_basis_ambiguity_rejected(verified, kwargs):
    _, bridge, adapter = verified
    result = bridge.set_stream_composition_batch("FEED", {"A": 0.4, "B": 0.6},
        basis="MOLE-FRAC", total_flow=1, **kwargs)
    assert not result["ok"]
    assert adapter.events == []


def test_partial_fraction_cannot_change_existing_basis(verified):
    _, bridge, adapter = verified
    result = bridge.set_stream_composition("FEED", "A", 0.4, basis="MASS-FRAC")
    assert not result["ok"]
    assert adapter.events == []


def test_dictionary_claim_is_not_verified_capability(verified):
    _, bridge, adapter = verified
    adapter.capabilities[("MOLE-FRAC", "MOLE-FLOW")] = {"status": "verified", "evidence": "claimed"}
    result = bridge.set_stream_composition_batch("FEED", {"A": 0.4, "B": 0.6}, basis="MOLE-FRAC", total_flow=1)
    assert result["status"] == "unsupported"
    assert adapter.events == []


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1e-10])
def test_nonfinite_or_negative_readback_rejected(verified, bad):
    import json
    _, bridge, adapter = verified
    adapter.read_component = lambda stream, comp: {"value": bad, "read_source": "fake:bad"}
    result = bridge.set_stream_composition_batch("FEED", {"A": 0, "B": 1}, basis="MOLE-FRAC", total_flow=1)
    assert not result["ok"]
    json.dumps(result, allow_nan=False)


def test_single_production_write_has_no_attribute13_guess(fake_windows):
    from aspen_mcp.tools.streams import tool_set_stream_param
    bridge, _ = fake_windows
    bridge.call = lambda fn: pytest.fail("禁止原生猜测")
    result = tool_set_stream_param("S", "A", 1, basis="MOLE-FLOW")
    assert result["status"] == "unsupported"
