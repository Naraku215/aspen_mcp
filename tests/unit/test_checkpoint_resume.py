"""checkpoint 存档与续跑对账：区分内存/已存/未知；跳过或补缺，不盲重放/覆盖。"""
import hashlib
import importlib

import pytest


def checkpoint():
    assert importlib.util.find_spec("aspen_mcp.checkpoint") is not None, "缺少 checkpoint 模块"
    return importlib.import_module("aspen_mcp.checkpoint")


def execution():
    return importlib.import_module("aspen_mcp.execution")


def num(value, unit=None):
    spec = {"type": "number", "value": value}
    if unit is not None:
        spec["unit"] = unit
    return spec


def op(op_id, target, *, values=None):
    return {"op_id": op_id, "kind": "block", "target": target,
            "dependencies": [], "specs": {}, "values": values or {}}


def manifest(*ops, lineage="L1"):
    return {"schema_version": 1, "manifest_id": "M-1", "task_id": "T1",
            "model_lineage_id": lineage, "operations": list(ops)}


@pytest.fixture
def ledger(tmp_path):
    instance = execution().ExecutionLedger(path=str(tmp_path / "ledger.json"))
    instance.begin_task(task_id="T1", model_lineage_id="L1")
    return instance


@pytest.fixture
def index(tmp_path):
    return checkpoint().CheckpointIndex(path=str(tmp_path / "checkpoints.json"))


def seal(ledger, op_id, target, *, fingerprint="fp1", state="pending", receipt=None):
    """模拟写前/写中/写后各持久化层次的中断现场。"""
    ledger.mark_pending(op_id=op_id, target=target, detail={"fingerprint": fingerprint})
    ledger.note_operation(op_id=op_id, target=target, fingerprint=fingerprint, state=state)
    if receipt:
        ledger.record_receipt(op_id=op_id, target=target, **receipt)


def batch():
    return manifest(op("op1", "H1", values={"TEMP": num(150, "C")}))


def probe_value(value):
    return lambda operation: value


# ── checkpoint 索引：pending → committed ─────────────────────────────────

def test_checkpoint_pending_then_committed(index, tmp_path):
    model = tmp_path / "case.bkp"
    model.write_text("v1", encoding="utf-8")
    record = index.begin(checkpoint_id="ck1", phase="configure", path=str(model),
                         parent_id=None, objects=["H1"], receipts=["op1"],
                         document_id="DOC-1")
    assert record["status"] == "pending"
    # 保存前 pending 已立即落盘：新实例读回仍是 pending（保存后索引前中断的证据）
    reopened = checkpoint().CheckpointIndex(path=str(tmp_path / "checkpoints.json"))
    assert reopened.record("ck1")["status"] == "pending"
    assert reopened.record("ck1")["phase"] == "configure"
    assert reopened.record("ck1")["receipts"] == ["op1"]

    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    done = index.commit(checkpoint_id="ck1", file_hash=digest)
    assert done["status"] == "committed"
    assert done["file_hash"] == digest
    assert done["size"] == len(b"v1")
    assert done["committed_at"]
    assert index.pending() == []
    assert index.committed()[0]["checkpoint_id"] == "ck1"


def test_commit_requires_real_file_and_matching_hash(index, tmp_path):
    missing = tmp_path / "gone.bkp"
    index.begin(checkpoint_id="ck1", phase="run", path=str(missing))
    # 不能只凭记录补标成功：文件不存在拒绝
    with pytest.raises(ValueError):
        index.commit(checkpoint_id="ck1", file_hash="00" * 32)
    assert index.record("ck1")["status"] == "pending"

    model = tmp_path / "case.bkp"
    model.write_text("v1", encoding="utf-8")
    index.begin(checkpoint_id="ck2", phase="run", path=str(model))
    # 旧文件/被替换文件：核验哈希与实际内容不符 → 拒绝，不标 committed
    with pytest.raises(ValueError):
        index.commit(checkpoint_id="ck2", file_hash=hashlib.sha256(b"old-fraction").hexdigest())
    assert index.record("ck2")["status"] == "pending"
    # 未知 checkpoint 与重复 ID 拒绝
    with pytest.raises(ValueError):
        index.commit(checkpoint_id="ghost", file_hash="00" * 32)
    with pytest.raises(ValueError):
        index.begin(checkpoint_id="ck2", phase="run", path=str(model))
    # 非保存点阶段拒绝
    with pytest.raises(ValueError):
        index.begin(checkpoint_id="ck3", phase="random", path=str(model))
    # 已 committed 不得重复提交
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    index.commit(checkpoint_id="ck2", file_hash=digest)
    with pytest.raises(ValueError):
        index.commit(checkpoint_id="ck2", file_hash=digest)


# ── 保存后索引前中断：凭 pending 及实际文件对账 ──────────────────────────

def test_reconcile_does_not_commit_on_existence_alone(index, tmp_path):
    module = checkpoint()
    model = tmp_path / "case.bkp"
    model.write_text("v1", encoding="utf-8")
    index.begin(checkpoint_id="ck1", phase="configure", path=str(model),
                document_id="DOC-1", receipts=["op1"])
    # 只报存在、无内容核验 → 不补标成功
    result = module.reconcile_checkpoints(index, file_check=lambda path: {"exists": True})
    assert result["committed"] == []
    assert result["still_pending"][0]["reason"] == "unverified_content"
    assert index.record("ck1")["status"] == "pending"
    # 无核验通道 → 同样不提交
    result = module.reconcile_checkpoints(index)
    assert result["still_pending"][0]["reason"] == "no_file_verification"
    # 错文档（身份不符）→ 不提交
    result = module.reconcile_checkpoints(
        index, file_check=lambda path: {"exists": True, "hash": "deadbeef",
                                        "document_id": "DOC-9"})
    assert result["mismatched"][0]["reason"] == "wrong_document"
    assert index.record("ck1")["status"] == "pending"
    # 文件丢失 → 保持 pending
    result = module.reconcile_checkpoints(index, file_check=lambda path: {"exists": False})
    assert result["still_pending"][0]["reason"] == "file_missing"
    # 内容核验一致 → committed
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    result = module.reconcile_checkpoints(
        index, file_check=lambda path: {"exists": True, "hash": digest,
                                        "document_id": "DOC-1"})
    assert result["committed"][0]["checkpoint_id"] == "ck1"
    assert index.record("ck1")["status"] == "committed"


def test_reconcile_rejects_stale_or_replaced_file(index, tmp_path):
    module = checkpoint()
    model = tmp_path / "case.bkp"
    model.write_text("v1", encoding="utf-8")
    index.begin(checkpoint_id="ck1", phase="run", path=str(model))
    # 旧分率文件：核验哈希与当前文件内容不符 → 不提交，不冒充本次保存
    stale = hashlib.sha256(b"old-fraction-file").hexdigest()
    result = module.reconcile_checkpoints(
        index, file_check=lambda path: {"exists": True, "hash": stale})
    assert result["committed"] == []
    assert result["mismatched"][0]["reason"] == "file_changed"
    assert index.record("ck1")["status"] == "pending"


# ── 续跑对账：intent 后、写后回执前、人工改值与未知 ──────────────────────

def test_intent_interrupted_before_write_reapplies(ledger):
    module = checkpoint()
    seal(ledger, "op1", "H1")  # 写前 intent 已持久化，未进入写入
    result = module.plan_resume(batch(), ledger=ledger, probe=probe_value("diverges"))
    entry = result["operations"][0]
    assert entry["decision"] == "reapply"
    assert entry["state_class"] == "missing"
    assert entry["reason"] == "reconciled_not_written"
    assert result["status"] == "ok"
    # 账本修正为 failed（允许核验后重试补缺），不盲重放已核对部分
    assert ledger.get_operation(op_id="op1")["state"] == "failed"
    receipts = [e for e in ledger.read_entries() if e["event"] == "receipt"]
    assert receipts[-1]["write_status"] == "failed"
    assert receipts[-1]["evidence"]["reconciled"] is True


def test_receipt_lost_after_write_skips_without_rewrite(ledger):
    module = checkpoint()
    seal(ledger, "op1", "H1")
    result = module.plan_resume(batch(), ledger=ledger, probe=probe_value("matches"))
    entry = result["operations"][0]
    assert entry["decision"] == "skip"
    assert entry["state_class"] == "memory_verified"
    assert entry["reason"] == "reconciled_after_write"
    # 对账核验补记，不重写：只保留一次 pending 事件
    events = [e["event"] for e in ledger.read_entries()]
    assert events.count("pending") == 1
    assert ledger.get_operation(op_id="op1")["state"] == "applied"
    receipts = [e for e in ledger.read_entries() if e["event"] == "receipt"]
    assert receipts[-1]["write_status"] == "applied"
    assert receipts[-1]["verify_status"] == "pass"
    assert receipts[-1]["evidence"]["reconciled"] is True


def test_saved_verified_skips_without_rewrite(ledger, index, tmp_path):
    module = checkpoint()
    model = tmp_path / "case.bkp"
    model.write_text("v1", encoding="utf-8")
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    index.begin(checkpoint_id="ck1", phase="configure", path=str(model),
                receipts=["op1"])
    index.commit(checkpoint_id="ck1", file_hash=digest)
    seal(ledger, "op1", "H1", state="applied",
         receipt={"write_status": "applied", "verify_status": "pass"})
    result = module.plan_resume(batch(), ledger=ledger, index=index,
                                probe=probe_value("matches"))
    entry = result["operations"][0]
    assert entry["decision"] == "skip"
    assert entry["state_class"] == "saved_verified"
    assert result["checkpoints"]["committed"][0]["checkpoint_id"] == "ck1"


def test_manual_divergence_pauses_without_overwrite(ledger):
    module = checkpoint()
    seal(ledger, "op1", "H1", state="applied",
         receipt={"write_status": "applied", "verify_status": "pass"})
    before = ledger.read_entries()
    result = module.plan_resume(batch(), ledger=ledger, probe=probe_value("diverges"))
    entry = result["operations"][0]
    assert entry["decision"] == "paused"
    assert entry["reason"] == "manual_divergence"
    assert result["status"] == "paused"
    # 不用旧 state 覆盖人工差异：账本不新增事件、状态不变
    assert ledger.get_operation(op_id="op1")["state"] == "applied"
    assert ledger.read_entries() == before


def test_unknown_write_pauses_and_keeps_pending(ledger):
    module = checkpoint()
    seal(ledger, "op1", "H1")
    result = module.plan_resume(batch(), ledger=ledger, probe=probe_value("unknown"))
    entry = result["operations"][0]
    assert entry["decision"] == "paused"
    assert entry["state_class"] == "unknown"
    assert entry["reason"] == "probe_unknown"
    # 先只读对账，不捏造终态
    assert ledger.get_operation(op_id="op1")["state"] == "pending"
    assert [e["event"] for e in ledger.read_entries()] == ["pending"]


def test_outcome_unknown_keeps_paused_status(ledger):
    module = checkpoint()
    seal(ledger, "op1", "H1", state="outcome_unknown",
         receipt={"write_status": "outcome_unknown", "verify_status": "unknown"})
    result = module.plan_resume(batch(), ledger=ledger, probe=probe_value("unknown"))
    assert result["operations"][0]["decision"] == "paused"
    assert ledger.get_operation(op_id="op1")["state"] == "outcome_unknown"


def test_not_started_operation_is_missing(ledger):
    module = checkpoint()
    result = module.plan_resume(batch(), ledger=ledger, probe=probe_value("diverges"))
    entry = result["operations"][0]
    assert entry["decision"] == "reapply"
    assert entry["state_class"] == "missing"
    assert entry["reason"] == "not_started"
    assert result["missing"] == ["op1"]


# ── 身份与沿袭 ──────────────────────────────────────────────────────────

def test_resume_identity_mismatch_pauses(ledger):
    module = checkpoint()
    result = module.plan_resume(manifest(op("op1", "H1"), lineage="L9"),
                                ledger=ledger, probe=probe_value("matches"))
    assert result["status"] == "paused"
    assert result["operations"] == []
    assert "identity_mismatch" in " | ".join(result["errors"])


def test_resume_without_active_task_pauses(tmp_path):
    module = checkpoint()
    empty = execution().ExecutionLedger(path=str(tmp_path / "empty.json"))
    result = module.plan_resume(batch(), ledger=empty, probe=probe_value("matches"))
    assert result["status"] == "paused"
    assert "no_active_task" in " | ".join(result["errors"])