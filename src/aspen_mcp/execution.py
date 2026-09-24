"""执行账本：任务沿袭、操作状态、原生 RUN 额度与 deadline。

只管理额度、沿袭与截止时间，不建设调度平台。

规则：
- 默认首跑额度 1；TUNE 需显式授权，累计最多追加 3 次（总原生提交 ≤4）。
- sensitivity 等分析运行走独立 analysis 额度（显式授权 authorize_analysis），
  不占用建模 4 次提交；建模任务活跃期间 run_script 通道关闭（禁绕行）。
- 简单案例基准 deadline 300 秒，自首次派发前启动；以持久化的单调
  起点计时（跨进程通常可比），时钟源重置时回退持久墙钟。
- 提交前持久记账；outcome_unknown 不退额度；agent 不能通过请求字段增额。
- 记录缺失或归属不明拒绝；独立新案例（新 model lineage）需确认。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from typing import Any, Callable

from ._directories import default_directory

DEFAULT_DEADLINE_SECONDS = 300.0
INITIAL_RUN_BUDGET = 1
MAX_TUNE_RUNS = 3
MAX_TOTAL_SUBMISSIONS = INITIAL_RUN_BUDGET + MAX_TUNE_RUNS
# 分析运行（sensitivity 内部）独立额度：仅显式授权可增，上限为安全阀。
MAX_ANALYSIS_RUNS = 50


def default_state_directory() -> str:
    """状态目录：env 优先；cwd 子目录不可写时回落用户可写目录。"""
    return default_directory(env_var="ASPEN_MCP_STATE_DIR",
                             cwd_subdir=".aspen_mcp_state",
                             fallback_subdir="state")


class ExecutionLedger:
    """持久执行账本；所有写路径先记账再执行。"""

    def __init__(self, path: str | None = None, clock: Callable[[], float] | None = None):
        self.path = path or os.path.join(default_state_directory(), "execution_ledger.json")
        self._clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._data = self._load()
        self._entries: list[dict] = []
        self._events_path = self.path + ".events.jsonl"

    # ── 持久化 ──────────────────────────────────────────────────────────
    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            data = None
        except Exception:
            data = None
        if not isinstance(data, dict) or not isinstance(data.get("lineages"), dict):
            return {"schema_version": 1, "lineages": {}, "active_lineage": None,
                    "pending_confirmation": None}
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

    def _append_event(self, entry: dict) -> None:
        with self._lock:
            self._entries.append(entry)
            directory = os.path.dirname(os.path.abspath(self._events_path))
            os.makedirs(directory, exist_ok=True)
            with open(self._events_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False, allow_nan=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    def read_entries(self) -> list[dict]:
        with self._lock:
            return [dict(entry) for entry in self._entries]

    def _lineage(self) -> dict | None:
        return self._data["lineages"].get(self._data.get("active_lineage"))

    # ── 任务沿袭 ────────────────────────────────────────────────────────
    def begin_task(self, *, task_id: str, model_lineage_id: str,
                   case_id: str | None = None,
                   deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
                   confirm_new_lineage: bool = False) -> dict:
        if not task_id or not model_lineage_id:
            raise ValueError("task_id 与 model_lineage_id 必填；记录缺失或归属不明拒绝")
        with self._lock:
            lineages = self._data["lineages"]
            record = lineages.get(model_lineage_id)
            if record is None and lineages and not confirm_new_lineage:
                # 独立新案例未确认期间锁定账本，防止误用旧沿袭额度。
                self._data["pending_confirmation"] = model_lineage_id
                self._persist()
                return {"task_id": task_id, "model_lineage_id": model_lineage_id,
                        "status": "confirmation_required", "lineage_registered": False,
                        "reason": "独立新案例需用户确认"}
            if record is None:
                lineages[model_lineage_id] = {
                    "task_id": task_id, "case_id": case_id,
                    "runs": {"initial": 0, "tune": 0, "analysis": 0},
                    "tune_grants": 0, "analysis_grants": 0, "claims": [],
                    "deadline_seconds": float(deadline_seconds),
                    "started_at": time.time(),
                    "started_monotonic": self._clock(),
                }
                self._data["active_lineage"] = model_lineage_id
                self._data["pending_confirmation"] = None
                self._persist()
                return {"task_id": task_id, "model_lineage_id": model_lineage_id,
                        "status": "active", "lineage_registered": True,
                        "deadline_seconds": float(deadline_seconds)}
            renamed = record.get("task_id") != task_id
            record["task_id"] = task_id
            if case_id is not None:
                record["case_id"] = case_id
            self._data["active_lineage"] = model_lineage_id
            self._data["pending_confirmation"] = None
            self._persist()
            return {"task_id": task_id, "model_lineage_id": model_lineage_id,
                    "status": "resumed", "lineage_registered": True,
                    "task_renamed": renamed, "deadline_seconds": record["deadline_seconds"]}

    def authorize_tune(self, *, grants: int) -> dict:
        if isinstance(grants, bool) or not isinstance(grants, int) or grants <= 0:
            raise ValueError("grants 必须是正整数")
        with self._lock:
            record = self._lineage()
            if record is None:
                raise ValueError("没有活动任务")
            record["tune_grants"] = min(record.get("tune_grants", 0) + grants, 10 ** 6)
            self._persist()
            allowed = min(record["tune_grants"], MAX_TUNE_RUNS)
            return {"tune_authorized": allowed, "used": record["runs"]["tune"],
                    "remaining": max(0, allowed - record["runs"]["tune"]),
                    "max_total_submissions": MAX_TOTAL_SUBMISSIONS}

    def authorize_analysis(self, *, grants: int) -> dict:
        """显式授权分析原生运行次数（sensitivity 内部运行；独立于建模 4 次提交）。"""
        if isinstance(grants, bool) or not isinstance(grants, int) or grants <= 0:
            raise ValueError("grants 必须是正整数")
        with self._lock:
            record = self._lineage()
            if record is None:
                raise ValueError("没有活动任务")
            record["analysis_grants"] = min(record.get("analysis_grants", 0) + grants, 10 ** 6)
            self._persist()
            allowed = min(record["analysis_grants"], MAX_ANALYSIS_RUNS)
            used = record["runs"].get("analysis", 0)
            return {"analysis_authorized": allowed, "used": used,
                    "remaining": max(0, allowed - used),
                    "max_analysis_runs": MAX_ANALYSIS_RUNS}

    def script_guard(self) -> dict:
        """run_script 绕行守卫：确认挂起或建模任务活跃时关闭该通道。"""
        with self._lock:
            if self._data.get("pending_confirmation"):
                return {"allowed": False, "reason": "lineage_confirmation_required"}
            if self._lineage() is None:
                return {"allowed": True, "reason": "no_active_task"}
            return {"allowed": False, "reason": "active_task_requires_controlled_channel"}

    # ── 额度 ────────────────────────────────────────────────────────────
    def claim_run(self, *, kind: str = "initial") -> dict:
        if kind not in ("initial", "tune", "analysis"):
            raise ValueError("kind 必须是 initial、tune 或 analysis")
        with self._lock:
            if self._data.get("pending_confirmation"):
                return {"status": "rejected", "kind": kind,
                        "reason": "lineage_confirmation_required"}
            record = self._lineage()
            if record is None:
                return {"status": "rejected", "kind": kind, "reason": "no_active_task"}
            if self._remaining_unlocked(record) <= 0:
                return {"status": "rejected", "kind": kind, "reason": "deadline_exceeded"}
            runs = record["runs"]
            if kind == "analysis":
                runs.setdefault("analysis", 0)
                allowed = min(record.get("analysis_grants", 0), MAX_ANALYSIS_RUNS)
                if runs["analysis"] >= allowed:
                    return {"status": "rejected", "kind": kind,
                            "reason": "budget_exhausted" if allowed else "analysis_not_authorized"}
                runs["analysis"] += 1
            elif runs["initial"] + runs["tune"] >= MAX_TOTAL_SUBMISSIONS:
                return {"status": "rejected", "kind": kind, "reason": "budget_exhausted"}
            elif kind == "initial":
                if runs["initial"] >= INITIAL_RUN_BUDGET:
                    return {"status": "rejected", "kind": kind, "reason": "budget_exhausted"}
                runs["initial"] += 1
            else:
                allowed = min(record.get("tune_grants", 0), MAX_TUNE_RUNS)
                if runs["tune"] >= allowed:
                    return {"status": "rejected", "kind": kind,
                            "reason": "budget_exhausted" if allowed else "tune_not_authorized"}
                runs["tune"] += 1
            run_id = uuid.uuid4().hex
            record.setdefault("claims", []).append(
                {"run_id": run_id, "kind": kind, "at": time.time(), "outcome": "pending"})
            self._persist()  # 提交前持久记账
            return {"status": "accepted", "kind": kind, "run_id": run_id,
                    "remaining": {
                        "initial": INITIAL_RUN_BUDGET - runs["initial"],
                        "tune": max(0, min(record.get("tune_grants", 0), MAX_TUNE_RUNS)
                                    - runs["tune"]),
                        "analysis": max(0, min(record.get("analysis_grants", 0),
                                               MAX_ANALYSIS_RUNS)
                                        - runs.get("analysis", 0))}}

    def record_outcome(self, *, run_id: str, outcome: str) -> dict:
        with self._lock:
            record = self._lineage()
            if record is None:
                raise ValueError("没有活动任务")
            for claim in record.get("claims", []):
                if claim["run_id"] == run_id:
                    claim["outcome"] = outcome
                    claim["resolved_at"] = time.time()
                    break
            else:
                raise ValueError(f"未知 run_id: {run_id}")
            self._persist()
            return {"run_id": run_id, "outcome": outcome}

    # ── deadline ────────────────────────────────────────────────────────
    def _remaining_unlocked(self, record: dict) -> float:
        started_monotonic = record.get("started_monotonic")
        if started_monotonic is not None:
            elapsed = self._clock() - started_monotonic
        else:
            elapsed = time.time() - record.get("started_at", time.time())
        if elapsed < 0:
            # 时钟源重置（跨进程单调不可比）时回退持久墙钟，不虚增消耗。
            elapsed = max(0.0, time.time() - record.get("started_at", time.time()))
        return float(record.get("deadline_seconds", DEFAULT_DEADLINE_SECONDS)) - elapsed

    def deadline(self) -> dict:
        with self._lock:
            record = self._lineage()
            if record is None:
                return {"active": False, "remaining_seconds": None,
                        "expired": True, "reason": "no_active_task"}
            remaining = self._remaining_unlocked(record)
            return {"active": True, "deadline_seconds": record["deadline_seconds"],
                    "started_at": record.get("started_at"),
                    "remaining_seconds": remaining, "expired": remaining <= 0}

    # ── 操作状态记账（写前 intent、每对象 receipt） ─────────────────────
    def mark_pending(self, *, op_id: str, target: str, detail: dict | None = None) -> dict:
        entry = {"event": "pending", "op_id": op_id, "target": target,
                 "detail": detail or {}, "at": time.time()}
        self._append_event(entry)
        return entry

    def record_receipt(self, *, op_id: str, target: str, write_status: str,
                       verify_status: str, evidence: dict | None = None) -> dict:
        entry = {"event": "receipt", "op_id": op_id, "target": target,
                 "write_status": write_status, "verify_status": verify_status,
                 "evidence": evidence or {}, "at": time.time()}
        self._append_event(entry)
        return entry

    # ── 批配置操作指纹与摘要（同 ID 复用核验的权威记录） ─────────────
    def active_lineage_id(self) -> str | None:
        """当前活动沿袭；挂起确认期间视为无活动任务。"""
        with self._lock:
            if self._data.get("pending_confirmation"):
                return None
            return self._data.get("active_lineage")

    def note_operation(self, *, op_id: str, target: str, fingerprint: str,
                       state: str | None = None, detail: dict | None = None) -> dict:
        """操作指纹与回执摘要持久化；同 lineage/op_id 复用核验的权威记录。"""
        with self._lock:
            record = self._lineage()
            if record is None:
                raise ValueError("没有活动任务")
            ops = record.setdefault("ops", {})
            entry = ops.setdefault(op_id, {"op_id": op_id, "first_seen": time.time()})
            entry["target"] = target
            entry["fingerprint"] = fingerprint
            entry["updated_at"] = time.time()
            if state is not None:
                entry["state"] = state
            if detail is not None:
                entry["detail"] = detail
            self._persist()
            return dict(entry)

    def get_operation(self, *, op_id: str) -> dict | None:
        with self._lock:
            record = self._lineage()
            if record is None:
                return None
            entry = record.get("ops", {}).get(op_id)
            return dict(entry) if entry else None


_singleton: ExecutionLedger | None = None
_singleton_lock = threading.Lock()


def get_ledger() -> ExecutionLedger:
    """服务进程的共享账本（惰性单例）。"""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = ExecutionLedger()
        return _singleton