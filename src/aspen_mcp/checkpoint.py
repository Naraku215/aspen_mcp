"""checkpoint 存档与续跑对账（纯状态管理，不含 COM）。

规则：
- 模型仅在 topology、relayout、CONFIGURE、RUN 及安全异常交接 checkpoint
  保存；checkpoint 记录阶段、父版本、对象快照、回执集合、路径及文件哈希。
- 索引先写 pending 并立即落盘（原子替换）；文件与索引核验后 committed。
- 覆盖“模型已保存但索引未提交”中断：凭 pending 及实际文件对账，不能只
  凭文件存在补标成功；身份不符、内容核验缺失或哈希不符都保持 pending。
- 续跑聚合核身份、checkpoint 及实际值：同任务内存一致可跳过；已保存一致
  可跳过；确认为旧 checkpoint 且缺少未持久项、无人工冲突时按原 manifest
  只补缺失部分，不盲重放/覆盖。
- 未知写入先只读对账，不捏造终态；人工差异列出并暂停。

本模块只做记录与对账；真实保存动作属原生操作，须独立授权。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time

SCHEMA_VERSION = 1
PHASES = ("topology", "relayout", "configure", "run", "safety_handoff")


def file_digest(path: str) -> str:
    """文件内容 SHA-256（分块读取，避免大文件整读）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class CheckpointIndex:
    """checkpoint 索引：pending 先落盘，核验文件后才 committed。"""

    def __init__(self, path: str | None = None):
        if path is None:
            from .execution import default_state_directory
            path = os.path.join(default_state_directory(), "checkpoints.json")
        self.path = path
        self._lock = threading.RLock()
        self._data = self._load()

    # ── 持久化 ──────────────────────────────────────────────────────────
    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            data = None
        except Exception:
            data = None
        if not isinstance(data, dict) or not isinstance(data.get("checkpoints"), dict):
            return {"schema_version": SCHEMA_VERSION, "checkpoints": {}}
        return data

    def _persist(self) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        descriptor, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(self._data, handle, ensure_ascii=False, allow_nan=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ── 记录 ────────────────────────────────────────────────────────────
    def begin(self, *, checkpoint_id: str, phase: str, path: str,
              parent_id: str | None = None, objects=None, receipts=None,
              document_id: str | None = None) -> dict:
        """保存前写 pending 记录并立即持久化；重复 ID 或非保存点阶段拒绝。"""
        if not isinstance(checkpoint_id, str) or not checkpoint_id.strip():
            raise ValueError("checkpoint_id 必填且为非空字符串")
        if phase not in PHASES:
            raise ValueError(f"phase 必须是保存点阶段之一 {PHASES}")
        if not isinstance(path, str) or not path.strip():
            raise ValueError("checkpoint 必须记录模型文件路径")
        with self._lock:
            if checkpoint_id in self._data["checkpoints"]:
                raise ValueError(f"重复 checkpoint_id: {checkpoint_id}")
            record = {"checkpoint_id": checkpoint_id, "phase": phase, "path": path,
                      "parent_id": parent_id, "objects": list(objects or []),
                      "receipts": list(receipts or []), "document_id": document_id,
                      "status": "pending", "created_at": time.time()}
            self._data["checkpoints"][checkpoint_id] = record
            self._persist()
            return dict(record)

    def commit(self, *, checkpoint_id: str, file_hash: str) -> dict:
        """文件与核验哈希一致才 committed；缺失或哈希不符拒绝，不补标。"""
        with self._lock:
            record = self._data["checkpoints"].get(checkpoint_id)
            if record is None:
                raise ValueError(f"未知 checkpoint: {checkpoint_id}")
            if record.get("status") == "committed":
                raise ValueError(f"checkpoint 已提交: {checkpoint_id}")
            path = record.get("path")
            if not isinstance(path, str) or not os.path.isfile(path):
                raise ValueError("文件不存在，不能只凭记录补标 committed")
            actual = file_digest(path)
            if not isinstance(file_hash, str) or actual != file_hash:
                raise ValueError("文件哈希不符（内容未核验通过）")
            record.update(status="committed", file_hash=actual,
                          size=os.path.getsize(path), committed_at=time.time())
            self._persist()
            return dict(record)

    def record(self, checkpoint_id: str) -> dict | None:
        with self._lock:
            entry = self._data["checkpoints"].get(checkpoint_id)
            return dict(entry) if entry else None

    def pending(self) -> list[dict]:
        with self._lock:
            return [dict(entry) for entry in self._data["checkpoints"].values()
                    if entry.get("status") == "pending"]

    def committed(self) -> list[dict]:
        with self._lock:
            return [dict(entry) for entry in self._data["checkpoints"].values()
                    if entry.get("status") == "committed"]


def reconcile_checkpoints(index: CheckpointIndex, *, file_check=None) -> dict:
    """凭 pending 记录与实际文件对账；只凭文件存在不得补标成功。

    file_check(path) -> {"exists": bool, "hash": str|None, "document_id": str|None}
    由调用方提供独立核验通道（重开/摘要核对）；无通道时保持 pending。
    """
    committed, still_pending, mismatched = [], [], []
    for record in index.pending():
        checkpoint_id = record["checkpoint_id"]
        path = record.get("path")
        info = None
        if file_check is not None:
            try:
                info = file_check(path)
            except Exception as exc:
                info = {"error": f"{type(exc).__name__}: {exc}"}
        if not isinstance(info, dict) or "exists" not in info:
            still_pending.append({"checkpoint_id": checkpoint_id, "path": path,
                                  "reason": "no_file_verification"})
            continue
        if not info.get("exists"):
            still_pending.append({"checkpoint_id": checkpoint_id, "path": path,
                                  "reason": "file_missing"})
            continue
        expected_doc = record.get("document_id")
        observed_doc = info.get("document_id")
        if expected_doc and observed_doc and observed_doc != expected_doc:
            mismatched.append({"checkpoint_id": checkpoint_id, "reason": "wrong_document",
                               "expected": expected_doc, "observed": observed_doc})
            continue
        digest = info.get("hash")
        if not isinstance(digest, str) or not digest:
            still_pending.append({"checkpoint_id": checkpoint_id, "path": path,
                                  "reason": "unverified_content"})
            continue
        try:
            done = index.commit(checkpoint_id=checkpoint_id, file_hash=digest)
        except ValueError as exc:
            mismatched.append({"checkpoint_id": checkpoint_id, "reason": "file_changed",
                               "error": str(exc)})
            continue
        committed.append({"checkpoint_id": checkpoint_id, "file_hash": done["file_hash"]})
    return {"committed": committed, "still_pending": still_pending, "mismatched": mismatched}


def _mark_reconciled(ledger, operation, entry, *, applied: bool) -> None:
    """对账核验后修正账本不确定状态；证据如实标注来源，不捏造终态。"""
    op_id = operation.get("op_id")
    target = operation.get("target")
    fingerprint = (entry or {}).get("fingerprint")
    if applied:
        ledger.note_operation(op_id=op_id, target=target, fingerprint=fingerprint,
                              state="applied",
                              detail={"reconciled": True, "reason": "reconciled_after_write"})
        ledger.record_receipt(op_id=op_id, target=target, write_status="applied",
                              verify_status="pass",
                              evidence={"reconciled": True, "source": "resume_probe"})
    else:
        ledger.note_operation(op_id=op_id, target=target, fingerprint=fingerprint,
                              state="failed",
                              detail={"reconciled": True, "reason": "reconciled_not_written"})
        ledger.record_receipt(op_id=op_id, target=target, write_status="failed",
                              verify_status="unknown",
                              evidence={"reconciled": True, "source": "resume_probe"})


def plan_resume(manifest, *, ledger, index=None, probe, file_check=None) -> dict:
    """续跑聚合核身份、checkpoint 及实际值；返回逐 op 决策与缺口清单。

    probe(operation) -> "matches"（现值与请求一致）| "diverges"（明确不一致）|
    "unknown"（不可核验）。只跳过或补缺，不盲重放/覆盖人工差异。
    """
    lineage = manifest.get("model_lineage_id") if isinstance(manifest, dict) else None
    active = ledger.active_lineage_id()
    identity = {"manifest_lineage": lineage, "active_lineage": active}
    if active is None:
        return {"schema_version": SCHEMA_VERSION, "status": "paused", "identity": identity,
                "checkpoints": {"committed": [], "still_pending": [], "mismatched": []},
                "operations": [], "missing": [], "paused": [],
                "errors": ["no_active_task: 续跑对账需要活动任务沿袭"]}
    if lineage != active:
        return {"schema_version": SCHEMA_VERSION, "status": "paused", "identity": identity,
                "checkpoints": {"committed": [], "still_pending": [], "mismatched": []},
                "operations": [], "missing": [], "paused": [],
                "errors": [f"identity_mismatch: manifest lineage {lineage!r} != active {active!r}"]}

    if index is not None:
        reconciled = reconcile_checkpoints(index, file_check=file_check)
        committed_ids = {item["checkpoint_id"] for item in reconciled["committed"]}
        checkpoints = {
            "committed": list(reconciled["committed"]) + [
                {"checkpoint_id": record["checkpoint_id"], "file_hash": record.get("file_hash")}
                for record in index.committed() if record["checkpoint_id"] not in committed_ids],
            "still_pending": reconciled["still_pending"],
            "mismatched": reconciled["mismatched"]}
        saved_ops = set()
        for record in index.committed():
            saved_ops.update(record.get("receipts") or [])
    else:
        checkpoints = {"committed": [], "still_pending": [], "mismatched": []}
        saved_ops = set()

    operations_out, missing, paused = [], [], []
    for operation in manifest.get("operations") or []:
        op_id = operation.get("op_id")
        entry = ledger.get_operation(op_id=op_id)
        state = (entry or {}).get("state")
        try:
            verdict = probe(operation)
        except Exception:
            verdict = "unknown"
        if verdict == "matches":
            if op_id in saved_ops:
                decision, state_class, reason = "skip", "saved_verified", "saved_and_verified"
            else:
                decision, state_class = "skip", "memory_verified"
                if state in ("pending", "outcome_unknown", "failed", None):
                    _mark_reconciled(ledger, operation, entry, applied=True)
                    reason = "reconciled_after_write"
                else:
                    reason = "already_verified"
        elif verdict == "diverges":
            if state is None:
                decision, state_class, reason = "reapply", "missing", "not_started"
            elif state == "pending":
                _mark_reconciled(ledger, operation, entry, applied=False)
                decision, state_class, reason = "reapply", "missing", "reconciled_not_written"
            elif state == "failed":
                decision, state_class, reason = "reapply", "missing", "retry_after_failure"
            else:
                decision, state_class, reason = "paused", "unknown", "manual_divergence"
        else:
            decision, state_class, reason = "paused", "unknown", "probe_unknown"
        operations_out.append({"op_id": op_id, "target": operation.get("target"),
                               "decision": decision, "state_class": state_class,
                               "reason": reason})
        if decision == "paused":
            paused.append(op_id)
        elif decision == "reapply":
            missing.append(op_id)
    return {"schema_version": SCHEMA_VERSION,
            "status": "paused" if paused else "ok",
            "identity": identity, "checkpoints": checkpoints,
            "operations": operations_out, "missing": missing, "paused": paused,
            "errors": []}