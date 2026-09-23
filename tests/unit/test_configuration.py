"""通用批配置 manifest：静态整批零写、动态分级止损、指纹复用与对象锁。"""
import importlib
import threading
import time
from types import SimpleNamespace

import pytest


def configuration():
    assert importlib.util.find_spec("aspen_mcp.tools.configuration") is not None, "缺少批配置模块"
    return importlib.import_module("aspen_mcp.tools.configuration")


def execution():
    return importlib.import_module("aspen_mcp.execution")


def num(value, unit=None):
    spec = {"type": "number", "value": value}
    if unit is not None:
        spec["unit"] = unit
    return spec


def op(op_id, target, *, kind="block", deps=(), specs=None, values=None, **extra):
    payload = {"op_id": op_id, "kind": kind, "target": target,
               "dependencies": list(deps), "specs": specs or {}, "values": values or {}}
    payload.update(extra)
    return payload


def feed(op_id, target="FEED", *, components, basis="MOLE-FRAC", total=None,
         total_basis=None, deps=(), values=None):
    return op(op_id, target, kind="feed", deps=deps, values=values,
              components=components, composition_basis=basis, total=total, total_basis=total_basis)


def manifest(*ops, lineage="L1", **root):
    payload = {"schema_version": 1, "manifest_id": "M-1", "task_id": "T1",
               "model_lineage_id": lineage, "operations": list(ops)}
    payload.update(root)
    return payload


@pytest.fixture
def engine(fake_windows, tmp_path):
    module = configuration()
    ledger = execution().ExecutionLedger(path=str(tmp_path / "ledger.json"))
    ledger.begin_task(task_id="T1", model_lineage_id="L1")
    bridge, tree = fake_windows
    calls = []

    def record_call(fn):
        calls.append(fn)
        return fn()

    bridge.call = record_call
    instance = module.ConfigurationEngine(bridge=bridge, ledger=ledger)
    return SimpleNamespace(module=module, engine=instance, bridge=bridge, tree=tree,
                           ledger=ledger, calls=calls)


def mount(tree, node_class, path, *, row=None, col=1, value=None):
    return tree.mount(path, node_class(value=value, row=row, col=col))


def test_static_errors_reported_at_once_and_zero_write(engine):
    ops = [
        op("a", "H1", deps=["ghost"]),
        op("a", "H1"),
        op("b", "H2", values={"TEMP": num(float("nan"))}),
        op("c", "C1", deps=["d"]),
        op("d", "D1", deps=["c"]),
        op("e", "H3", values={"TEMP": num(1)}),
        op("f", "H3", values={"TEMP": num(2)}),
    ]
    result = engine.engine.apply(manifest(*ops))
    assert result["status"] == "rejected"
    assert result["phase"] == "static"
    assert result["receipts"] == []
    joined = " | ".join(result["errors"])
    for marker in ("duplicate op_id", "unknown dependency", "cycle", "conflict", "non-finite"):
        assert marker in joined, joined
    assert engine.calls == []


def test_root_and_op_format_validation(engine):
    module = engine.module
    errors = module.validate_manifest(
        {"schema_version": 2, "manifest_id": "", "task_id": "T", "model_lineage_id": "L",
         "operations": []})
    joined = " | ".join(errors)
    assert "schema_version" in joined and "operations" in joined

    bad_ops = [
        {"op_id": "x", "kind": "weird", "target": "H1", "dependencies": [],
         "specs": {}, "values": {}},
        {"op_id": "y", "kind": "block", "dependencies": [], "specs": {}, "values": {}},
        op("z", "H1", values={"TEMP": {"type": "number", "value": True}}),
        op("w", "H1", values={"PRES": {"type": "enum"}}),
        op("v", "H1", values={"TEMP": {"type": "bogus", "value": 1}}),
        op("u", "H1", values={"TEMP": num(25, "zorp")}),
        op("t", "H1", values={"TEMP": num(25, "")}),
        op("s", "H1", extra_field=1),
    ]
    errors = module.validate_manifest(manifest(*bad_ops))
    joined = " | ".join(errors)
    for marker in ("unknown kind", "target required", "non-finite", "missing value",
                   "unsupported value type", "unknown unit", "unit must be",
                   "unknown op field"):
        assert marker in joined, joined


def test_feed_requires_explicit_total_declaration(engine):
    module = engine.module
    assert module.validate_manifest(manifest(
        feed("f1", components={"A": 0.4, "B": 0.6}, basis="MOLE-FRAC",
             total=1.0, total_basis="MOLE-FLOW"))) == []
    closed = module.validate_manifest(manifest(
        feed("f2", components={"A": 0.5, "B": 0.4}, basis="MOLE-FRAC",
             total=1.0, total_basis="MOLE-FLOW")))
    assert any("closure" in e for e in closed), closed
    missing = module.validate_manifest(manifest(
        feed("f3", components={"A": 0.4, "B": 0.6}, basis="MOLE-FRAC",
             total=None, total_basis=None)))
    assert any("total" in e for e in missing), missing
    assert module.validate_manifest(manifest(
        feed("f4", components={"A": 2.0, "B": 3.0}, basis="MOLE-FLOW",
             total=None, total_basis=None))) == []
    basis_missing = module.validate_manifest(manifest(
        feed("f5", components={"A": 2.0, "B": 3.0}, basis="MOLE-FLOW",
             total=5.0, total_basis=None)))
    assert any("total_basis" in e for e in basis_missing), basis_missing


def test_duplicate_keys_rejected_at_parse(engine):
    module = engine.module
    with pytest.raises(ValueError, match="重复键"):
        module.load_manifest('{"schema_version": 1, "schema_version": 2}')
    parsed = module.load_manifest('{"schema_version": 1, "manifest_id": "M"}')
    assert parsed["manifest_id"] == "M"
    assert module.load_manifest(parsed) is parsed


def test_operation_fingerprint_canonical_rules(engine):
    module = engine.module
    left = op("x", "H1", deps=["p", "q"], values={"TEMP": num(25, "C")})
    right = op("x", "H1", deps=["q", "p"],
               values={"TEMP": {"unit": "C", "value": "25", "type": "number"}})
    assert module.operation_fingerprint(left) == module.operation_fingerprint(right)
    assert module.operation_fingerprint(left) == module.operation_fingerprint({**left, "op_id": "other"})
    near_a = op("y", "H1", values={"TEMP": num(25.0000001, "C")})
    near_b = op("y", "H1", values={"TEMP": num(25.0000002, "C")})
    assert module.operation_fingerprint(near_a) != module.operation_fingerprint(near_b)
    with pytest.raises(ValueError):
        module.operation_fingerprint(op("z", "H1", values={"TEMP": num(float("inf"))}))
    with pytest.raises(ValueError):
        module.operation_fingerprint(op("w", "H1", values={"TEMP": {"type": "number", "value": True}}))


def test_local_failure_blocks_dependents_but_not_independent(engine, node_class):
    mount(engine.tree, node_class, r"\Data\Blocks\H1\Input\TEMP", row=22)
    mount(engine.tree, node_class, r"\Data\Blocks\H2\Input\TEMP", row=22)
    result = engine.engine.apply(manifest(
        op("op1", "H1", values={"TEMP": num(150, "C")}),
        op("op2", "H3", values={"TEMP": num(80, "C")}),
        op("op3", "H4", deps=["op2"], values={"TEMP": num(50, "C")}),
        op("op4", "H2", values={"TEMP": num(30, "C")}),
    ))
    assert result["status"] == "completed"
    receipts = {r["op_id"]: r for r in result["receipts"]}
    assert receipts["op1"]["write_status"] == "applied"
    assert receipts["op1"]["verify_status"] == "pass"
    assert receipts["op2"]["write_status"] == "failed"
    assert receipts["op3"]["write_status"] == "skipped"
    assert receipts["op3"]["reason"] == "dependency_blocked"
    assert receipts["op4"]["write_status"] == "applied"
    h2 = engine.tree.FindNode(r"\Data\Blocks\H2\Input\TEMP")
    assert len(h2.writes) == 1
    assert h2.si == 303.15


def test_partial_applied_when_values_partially_missing(engine, node_class):
    mount(engine.tree, node_class, r"\Data\Blocks\H1\Input\TEMP", row=22)
    result = engine.engine.apply(manifest(
        op("op1", "H1", values={"TEMP": num(150, "C"), "PRES": num(5, "bar")}),
    ))
    receipt = result["receipts"][0]
    assert receipt["write_status"] == "partial_applied"
    assert receipt["verify_status"] == "unknown"
    assert "PRES" in " | ".join(receipt["errors"])


def test_systemic_unit_failure_stops_subsequent_writes(engine, node_class):
    mount(engine.tree, node_class, r"\Data\Blocks\H1\Input\TEMP")  # 量纲元数据不可读
    mount(engine.tree, node_class, r"\Data\Blocks\H2\Input\TEMP", row=22)
    result = engine.engine.apply(manifest(
        op("op1", "H1", values={"TEMP": num(150, "C")}),
        op("op2", "H2", values={"TEMP": num(80, "C")}),
    ))
    receipts = {r["op_id"]: r for r in result["receipts"]}
    assert receipts["op1"]["write_status"] == "failed"
    assert "无效单位坐标" in " | ".join(receipts["op1"]["errors"])
    assert receipts["op2"]["write_status"] == "skipped"
    assert receipts["op2"]["reason"] == "systemic_stop"
    assert engine.tree.FindNode(r"\Data\Blocks\H2\Input\TEMP").writes == []


def test_com_timeout_receipt_is_outcome_unknown(engine, node_class):
    def raise_timeout(fn):
        raise TimeoutError("COM request did not return")

    engine.bridge.call = raise_timeout
    result = engine.engine.apply(manifest(
        op("op1", "H1", values={"TEMP": num(150, "C")}),
        op("op2", "H2", values={"TEMP": num(80, "C")}),
    ))
    receipts = {r["op_id"]: r for r in result["receipts"]}
    assert receipts["op1"]["write_status"] == "outcome_unknown"
    assert receipts["op1"]["verify_status"] == "unknown"
    assert receipts["op2"]["write_status"] == "skipped"
    assert receipts["op2"]["reason"] == "systemic_stop"


def test_same_id_same_fingerprint_verifies_then_reuses(engine, node_class):
    node = mount(engine.tree, node_class, r"\Data\Blocks\H1\Input\TEMP", row=22)
    batch = manifest(op("op1", "H1", values={"TEMP": num(150, "C")}))
    first = engine.engine.apply(batch)
    assert first["receipts"][0]["write_status"] == "applied"
    assert len(node.writes) == 1
    second = engine.engine.apply(batch)
    receipt = second["receipts"][0]
    assert receipt["write_status"] == "skipped"
    assert receipt["reason"] == "reused_verified"
    assert receipt["verify_status"] == "pass"
    assert len(node.writes) == 1  # 未重复写
    node.si = 500.0  # 人工差异
    third = engine.engine.apply(batch)
    receipt = third["receipts"][0]
    assert receipt["write_status"] == "skipped"
    assert receipt["reason"] == "manual_divergence_paused"
    assert receipt["verify_status"] == "fail"
    assert node.si == 500.0  # 不用旧 state 覆盖


def test_same_op_id_different_request_rejected(engine, node_class):
    node = mount(engine.tree, node_class, r"\Data\Blocks\H1\Input\TEMP", row=22)
    engine.engine.apply(manifest(op("op1", "H1", values={"TEMP": num(150, "C")})))
    result = engine.engine.apply(manifest(op("op1", "H1", values={"TEMP": num(200, "C")})))
    receipt = result["receipts"][0]
    assert receipt["write_status"] == "failed"
    assert receipt["reason"] == "same_op_id_different_request"
    assert len(node.writes) == 1
    assert node.si == 423.15


def test_identity_mismatch_rejects_whole_batch(engine):
    result = engine.engine.apply(manifest(op("op1", "H1", values={"TEMP": num(1)}), lineage="L9"))
    assert result["status"] == "rejected"
    assert result["phase"] == "identity"
    assert "identity_mismatch" in " | ".join(result["errors"])
    assert engine.calls == []


def test_apply_without_active_lineage_rejected(fake_windows, tmp_path):
    module = configuration()
    ledger = execution().ExecutionLedger(path=str(tmp_path / "empty.json"))
    bridge, _ = fake_windows
    calls = []

    def record_call(fn):
        calls.append(fn)
        return fn()

    bridge.call = record_call
    instance = module.ConfigurationEngine(bridge=bridge, ledger=ledger)
    result = instance.apply(manifest(op("op1", "H1", values={"TEMP": num(1)})))
    assert result["status"] == "rejected"
    assert result["phase"] == "identity"
    assert "no_active_task" in " | ".join(result["errors"])
    assert calls == []


def test_intent_persisted_before_write_and_receipt_after(engine, node_class):
    mount(engine.tree, node_class, r"\Data\Blocks\H1\Input\TEMP", row=22)
    engine.engine.apply(manifest(op("op1", "H1", values={"TEMP": num(150, "C")})))
    events = engine.ledger.read_entries()
    assert [e["event"] for e in events] == ["pending", "receipt"]
    assert events[0]["op_id"] == "op1"
    assert events[0]["detail"]["fingerprint"]
    assert events[1]["write_status"] == "applied"
    assert events[1]["verify_status"] == "pass"


def test_operation_lock_reentrant_and_serializes(engine):
    batch = manifest(op("op1", "H1", values={"TEMP": num(150, "C")}))
    with engine.engine.operation_lock:
        result = engine.engine.apply(batch)  # 同线程重入不得排队死锁
    assert result["status"] == "completed"
    failures = []

    def run():
        try:
            engine.engine.apply(batch)
        except Exception as exc:  # pragma: no cover - 线程内异常回传
            failures.append(exc)

    engine.engine.operation_lock.acquire()
    try:
        worker = threading.Thread(target=run)
        worker.start()
        time.sleep(0.3)
        assert worker.is_alive(), "跨线程必须被操作锁串行化"
    finally:
        engine.engine.operation_lock.release()
    worker.join(3)
    assert not worker.is_alive()
    assert failures == []


def test_inspect_configuration_is_read_only(engine, node_class):
    node = mount(engine.tree, node_class, r"\Data\Blocks\H1\Input\TEMP", row=22)
    batch = manifest(op("op1", "H1", values={"TEMP": num(150, "C")}))
    preview = engine.engine.inspect(batch)
    assert preview["status"] == "ok"
    assert preview["receipts"][0]["decision"] == "would_apply"
    assert node.writes == []
    assert engine.ledger.read_entries() == []
    engine.engine.apply(batch)
    preview = engine.engine.inspect(batch)
    assert preview["receipts"][0]["decision"] == "reuse"
    assert len(node.writes) == 1
    assert len(engine.ledger.read_entries()) == 2  # inspect 不产生事件


def test_feed_unverified_is_explicit_rejection_without_write(engine):
    result = engine.engine.apply(manifest(
        feed("f1", components={"A": 0.4, "B": 0.6}, basis="MOLE-FRAC",
             total=1.0, total_basis="MOLE-FLOW")))
    receipt = result["receipts"][0]
    assert receipt["write_status"] == "failed"
    assert receipt["verify_status"] == "unknown"
    joined = " | ".join(receipt["errors"])
    assert "unverified" in joined or "unsupported" in joined
    assert engine.calls == []  # 拒绝发生在进入 COM 之前


class FeedFake:
    """离线适配器：仅代表离线模型，不代表原生 Aspen 能力。"""

    def __init__(self):
        capability_class = importlib.import_module("aspen_mcp.stream_contract")._VerifiedCapability
        self.capabilities = {
            (basis, total): capability_class(
                evidence="offline-fixture:config", basis=basis, total_basis=total)
            for basis in ("MOLE-FRAC", "MASS-FRAC", "MOLE-FLOW", "MASS-FLOW")
            for total in ("MOLE-FLOW", "MASS-FLOW")
        }
        self.components = {"A": 0.0, "B": 0.0}
        self.basis = "MOLE-FRAC"
        self.total_basis = "MOLE-FLOW"
        self.total = 0.0
        self.events = []

    def component_ids(self, stream):
        return ("A", "B")

    def preflight(self, stream, plan):
        return None

    def write_basis(self, stream, basis, total_basis):
        self.events.append("basis")
        self.basis, self.total_basis = basis, total_basis
        return "OK"

    def read_basis(self, stream):
        return {"basis": self.basis, "total_basis": self.total_basis, "read_source": "fake:basis"}

    def write_total(self, stream, value):
        self.events.append("total")
        self.total = value

    def read_total(self, stream):
        return {"value": self.total, "read_source": "fake:total"}

    def write_component(self, stream, component, value):
        self.events.append("component:" + component)
        self.components[component] = value

    def read_component(self, stream, component):
        return {"value": self.components[component], "read_source": "fake:component:" + component}


def test_feed_applied_with_verified_adapter(engine):
    adapter = FeedFake()
    engine.bridge._stream_adapter = adapter
    result = engine.engine.apply(manifest(
        feed("f1", components={"A": 0.4, "B": 0.6}, basis="MOLE-FRAC",
             total=1.0, total_basis="MOLE-FLOW")))
    receipt = result["receipts"][0]
    assert receipt["write_status"] == "applied"
    assert receipt["verify_status"] == "pass"
    assert adapter.events == ["basis", "total", "component:A", "component:B"]
    assert adapter.components == {"A": 0.4, "B": 0.6}