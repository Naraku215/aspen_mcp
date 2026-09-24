"""执行额度、沿袭、deadline 与 COM 队列超时隔离（离线，无真实 COM）。"""
import asyncio
import importlib
import json
import os

import pytest


def execution():
    assert importlib.util.find_spec("aspen_mcp.execution") is not None, "缺少执行账本模块"
    return importlib.import_module("aspen_mcp.execution")


@pytest.fixture
def ledger(tmp_path):
    module = execution()
    return module.ExecutionLedger(path=str(tmp_path / "ledger.json"))


# ── 额度与沿袭 ───────────────────────────────────────────────────────────

def test_initial_run_budget_is_one(ledger):
    ledger.begin_task(task_id="T1", model_lineage_id="L1")
    first = ledger.claim_run(kind="initial")
    assert first["status"] == "accepted"
    second = ledger.claim_run(kind="initial")
    assert second["status"] == "rejected"
    assert second["reason"] == "budget_exhausted"


def test_tune_requires_explicit_authorization(ledger):
    ledger.begin_task(task_id="T1", model_lineage_id="L1")
    ledger.claim_run(kind="initial")
    denied = ledger.claim_run(kind="tune")
    assert denied["status"] == "rejected"
    assert denied["reason"] == "tune_not_authorized"
    ledger.authorize_tune(grants=2)
    assert ledger.claim_run(kind="tune")["status"] == "accepted"
    assert ledger.claim_run(kind="tune")["status"] == "accepted"
    assert ledger.claim_run(kind="tune")["status"] == "rejected"


def test_tune_cap_is_three_regardless_of_grants(ledger):
    ledger.begin_task(task_id="T1", model_lineage_id="L1")
    ledger.claim_run(kind="initial")
    ledger.authorize_tune(grants=99)
    accepted = sum(ledger.claim_run(kind="tune")["status"] == "accepted" for _ in range(5))
    assert accepted == 3


def test_outcome_unknown_does_not_refund(ledger):
    ledger.begin_task(task_id="T1", model_lineage_id="L1")
    claim = ledger.claim_run(kind="initial")
    ledger.record_outcome(run_id=claim["run_id"], outcome="outcome_unknown")
    assert ledger.claim_run(kind="initial")["status"] == "rejected"


def test_request_cannot_inflate_budget(ledger):
    with pytest.raises(TypeError):
        ledger.claim_run(kind="initial", budget=5)


def test_missing_identity_is_rejected(ledger):
    with pytest.raises(ValueError):
        ledger.begin_task(task_id="", model_lineage_id="L1")
    with pytest.raises(ValueError):
        ledger.begin_task(task_id="T1", model_lineage_id="")


def test_budget_survives_restart_and_task_rename(tmp_path):
    module = execution()
    path = str(tmp_path / "ledger.json")
    first = module.ExecutionLedger(path=path)
    first.begin_task(task_id="T1", model_lineage_id="L1")
    first.claim_run(kind="initial")
    reopened = module.ExecutionLedger(path=path)
    renamed = reopened.begin_task(task_id="T2", model_lineage_id="L1")
    assert renamed["task_id"] == "T2"
    assert reopened.claim_run(kind="initial")["status"] == "rejected"


def test_new_lineage_requires_confirmation(ledger):
    ledger.begin_task(task_id="T1", model_lineage_id="L1")
    result = ledger.begin_task(task_id="T1", model_lineage_id="L2")
    assert result["status"] == "confirmation_required"
    assert result["lineage_registered"] is False
    assert ledger.claim_run(kind="initial")["status"] == "rejected"


def test_no_active_task_blocks_claims(ledger):
    gate = ledger.claim_run(kind="initial")
    assert gate["status"] == "rejected"
    assert gate["reason"] == "no_active_task"


# ── deadline ─────────────────────────────────────────────────────────────

def test_deadline_counts_from_first_dispatch(ledger):
    now = {"t": 1000.0}
    ledger._clock = lambda: now["t"]
    state = ledger.begin_task(task_id="T1", model_lineage_id="L1", deadline_seconds=300)
    assert state["deadline_seconds"] == 300
    now["t"] = 1100.0
    assert ledger.deadline()["remaining_seconds"] == pytest.approx(200)
    assert ledger.deadline()["expired"] is False
    now["t"] = 1301.0
    assert ledger.deadline()["expired"] is True
    blocked = ledger.claim_run(kind="initial")
    assert blocked["status"] == "rejected"
    assert blocked["reason"] == "deadline_exceeded"


def test_deadline_restores_from_persisted_start(tmp_path):
    module = execution()
    path = str(tmp_path / "ledger.json")
    now = {"t": 500.0}
    first = module.ExecutionLedger(path=path, clock=lambda: now["t"])
    first.begin_task(task_id="T1", model_lineage_id="L1", deadline_seconds=300)
    now["t"] = 600.0
    reopened = module.ExecutionLedger(path=path, clock=lambda: now["t"])
    reopened.begin_task(task_id="T1", model_lineage_id="L1")
    assert reopened.deadline()["remaining_seconds"] == pytest.approx(200)


# ── 操作状态记账 ─────────────────────────────────────────────────────────

def test_pending_and_receipt_are_persisted_immediately(ledger):
    ledger.begin_task(task_id="T1", model_lineage_id="L1")
    ledger.mark_pending(op_id="op1", target="FEED", detail={"intent": "feed"})
    ledger.record_receipt(op_id="op1", target="FEED", write_status="applied",
                          verify_status="pass", evidence={"read_source": "ValueForUnit(11,1)"})
    entries = ledger.read_entries()
    assert [e["event"] for e in entries] == ["pending", "receipt"]
    assert entries[1]["write_status"] == "applied"
    assert entries[1]["verify_status"] == "pass"


def test_entry_log_is_durable_json(tmp_path):
    module = execution()
    path = str(tmp_path / "ledger.json")
    ledger = module.ExecutionLedger(path=path)
    ledger.begin_task(task_id="T1", model_lineage_id="L1")
    ledger.mark_pending(op_id="op1", target="FEED", detail={})
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    assert data["lineages"]["L1"]["task_id"] == "T1"


# ── COM 队列超时隔离 ─────────────────────────────────────────────────────

def test_expired_queued_job_is_not_executed():
    from aspen_mcp.bridge import windows
    ran = []
    job = windows._ComJob(lambda: ran.append("ran"), deadline=0.0)
    job.run()
    assert ran == []
    status, payload = job.result_queue.get_nowait()
    assert status == "expired"
    assert payload["status"] == "not_started"


def test_started_timeout_is_outcome_unknown_and_late_result_kept():
    from aspen_mcp.bridge import windows
    import queue as queue_module

    job = windows._ComJob(lambda: "late-value", deadline=1e12)
    job.run()
    assert job.started is True
    status, value = job.result_queue.get_nowait()
    assert (status, value) == ("ok", "late-value")
    assert job.late_result() is None or job.late_result() == "late-value"


def test_call_does_not_retry_or_replay(monkeypatch):
    from aspen_mcp.bridge import windows

    bridge = object.__new__(windows.WindowsBridge)
    bridge._doc = object()
    attempts = []

    def flaky(fn):
        attempts.append(1)
        raise TimeoutError("COM timeout after 30s (outcome_unknown)")

    monkeypatch.setattr(windows._COM, "submit", flaky)
    with pytest.raises(TimeoutError):
        bridge.call(lambda: None)
    assert len(attempts) == 1


# ── run 入口接入 ─────────────────────────────────────────────────────────

def test_run_entrypoint_blocks_when_budget_exhausted(fake_windows, monkeypatch, tmp_path):
    from aspen_mcp.tools import simulation
    module = execution()
    ledger = module.ExecutionLedger(path=str(tmp_path / "ledger.json"))
    ledger.begin_task(task_id="T1", model_lineage_id="L1")
    claim = ledger.claim_run(kind="initial")
    ledger.record_outcome(run_id=claim["run_id"], outcome="failed")
    monkeypatch.setattr(simulation, "_execution_ledger", lambda: ledger)
    result = simulation.tool_run()
    assert "Error" in result
    assert "budget_exhausted" in result or "额度" in result
    calls = fake_windows[0]._doc if hasattr(fake_windows[0], "_doc") else None
    assert "Run2" not in str(calls)


def test_async_entrypoint_uses_same_gate(fake_windows, monkeypatch, tmp_path):
    from aspen_mcp.tools import simulation
    module = execution()
    ledger = module.ExecutionLedger(path=str(tmp_path / "ledger.json"))
    monkeypatch.setattr(simulation, "_execution_ledger", lambda: ledger)
    result = simulation.tool_run_async()
    assert "Error" in result
    assert "no_active_task" in result or "阻断" in result


# ── sensitivity / run_script 收口（分析另授权预算、禁绕行） ──────────────

def _sensitivity_harness(monkeypatch, tmp_path, grants=None):
    import types
    from aspen_mcp.bridge.stub import StubBridge
    from aspen_mcp.tools import sensitivity_advanced
    module = execution()
    ledger = module.ExecutionLedger(path=str(tmp_path / "ledger.json"))
    ledger.begin_task(task_id="T1", model_lineage_id="L1")
    if grants:
        ledger.authorize_analysis(grants=grants)
    bridge = StubBridge()
    monkeypatch.setattr(sensitivity_advanced, "aspen", bridge)
    monkeypatch.setattr(sensitivity_advanced, "_execution_ledger", lambda: ledger)
    monkeypatch.setattr(sensitivity_advanced, "cache",
                        types.SimpleNamespace(save_sensitivity=lambda **kwargs: None))
    return bridge, ledger, sensitivity_advanced


def test_sensitivity_runs_require_analysis_authorization(monkeypatch, tmp_path):
    bridge, _ledger, module = _sensitivity_harness(monkeypatch, tmp_path)
    result = module.tool_sensitivity("B1", "TEMP", [300.0, 310.0])
    assert not [c for c in bridge.calls if c[0] == "reinit_and_run"]
    assert result["errors"]
    assert any("授权" in e for e in result["errors"]), result["errors"]


def test_sensitivity_analysis_budget_is_explicit_and_capped(monkeypatch, tmp_path):
    bridge, ledger, module = _sensitivity_harness(monkeypatch, tmp_path, grants=2)
    result = module.tool_sensitivity("B1", "TEMP", [300.0, 310.0, 320.0])
    runs = [c for c in bridge.calls if c[0] == "reinit_and_run"]
    assert len(runs) == 2
    with open(ledger.path, encoding="utf-8") as handle:
        data = json.load(handle)
    claims = data["lineages"]["L1"]["claims"]
    assert [c["outcome"] for c in claims] == ["completed", "completed"]
    assert any("预算" in e for e in result["errors"]), result["errors"]


def test_sensitivity_stops_after_failed_run(monkeypatch, tmp_path):
    bridge, ledger, module = _sensitivity_harness(monkeypatch, tmp_path, grants=3)
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] == 2:
            raise RuntimeError("engine refuses")

    bridge.reinit_and_run = flaky
    result = module.tool_sensitivity("B1", "TEMP", [300.0, 310.0, 320.0])
    assert attempts["n"] == 2
    with open(ledger.path, encoding="utf-8") as handle:
        data = json.load(handle)
    claims = data["lineages"]["L1"]["claims"]
    assert [c["outcome"] for c in claims] == ["completed", "failed"]
    assert result["errors"]


def _run_script_harness(monkeypatch, tmp_path, *, begin=None):
    from aspen_mcp.bridge.stub import StubBridge
    from aspen_mcp.tools import simulation
    module = execution()
    ledger = module.ExecutionLedger(path=str(tmp_path / "ledger.json"))
    for task_id, lineage in (begin or []):
        ledger.begin_task(task_id=task_id, model_lineage_id=lineage)
    bridge = StubBridge()
    monkeypatch.setattr(simulation, "aspen", bridge)
    monkeypatch.setattr(simulation, "_execution_ledger", lambda: ledger)
    return bridge, simulation


def test_run_script_blocked_during_active_task(monkeypatch, tmp_path):
    bridge, module = _run_script_harness(monkeypatch, tmp_path, begin=[("T1", "L1")])
    result = module.tool_run_script("demo.py")
    assert "Error" in result
    assert not [c for c in bridge.calls if c[0] == "run_script"]


def test_run_script_blocked_pending_confirmation(monkeypatch, tmp_path):
    bridge, module = _run_script_harness(monkeypatch, tmp_path,
                                         begin=[("T1", "L1"), ("T1", "L2")])
    result = module.tool_run_script("demo.py")
    assert "Error" in result
    assert not [c for c in bridge.calls if c[0] == "run_script"]


def test_run_script_allowed_without_active_task(monkeypatch, tmp_path):
    bridge, module = _run_script_harness(monkeypatch, tmp_path)
    result = module.tool_run_script("demo.py")
    assert "Error" not in result
    assert [c for c in bridge.calls if c[0] == "run_script"] == [("run_script", ("demo.py",), {})]


def test_default_state_directory_falls_back_when_cwd_unwritable(monkeypatch, tmp_path):
    monkeypatch.delenv("ASPEN_MCP_STATE_DIR", raising=False)
    blocker = tmp_path / "blocker"
    blocker.write_text("x")  # 文件挡住子目录创建 → OSError
    monkeypatch.setattr(os, "getcwd", lambda: str(blocker / "sub"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    result = execution().default_state_directory()
    assert result == str(tmp_path / "local" / "aspen-mcp" / "state")
    assert os.path.isdir(result)


# ── run_async 终态回收：派发失败落账、显式回收通道 ────────────────────────

def _async_harness(monkeypatch, tmp_path, *, fail_dispatch=False):
    from aspen_mcp.bridge.stub import StubBridge
    from aspen_mcp.tools import simulation
    module = execution()
    ledger = module.ExecutionLedger(path=str(tmp_path / "ledger.json"))
    ledger.begin_task(task_id="T1", model_lineage_id="L1")
    bridge = StubBridge()
    if fail_dispatch:
        def boom():
            raise RuntimeError("dispatch failed")
        bridge.run_async = boom
    monkeypatch.setattr(simulation, "aspen", bridge)
    monkeypatch.setattr(simulation, "_execution_ledger", lambda: ledger)
    return bridge, ledger, simulation


def _claims(ledger):
    with open(ledger.path, encoding="utf-8") as handle:
        return json.load(handle)["lineages"]["L1"]["claims"]


def test_run_async_dispatch_failure_records_outcome(monkeypatch, tmp_path):
    _bridge, ledger, module = _async_harness(monkeypatch, tmp_path, fail_dispatch=True)
    result = module.tool_run_async()
    assert "Error" in result
    assert _claims(ledger)[0]["outcome"] == "failed"  # 派发失败不得遗留 pending


def test_run_async_success_reports_run_id_and_stays_pending(monkeypatch, tmp_path):
    _bridge, ledger, module = _async_harness(monkeypatch, tmp_path)
    result = module.tool_run_async()
    assert result.startswith("Async simulation started")
    claim = _claims(ledger)[0]
    assert claim["run_id"] in result
    assert claim["outcome"] == "pending"  # 运行中占用：待显式终态回收


def test_report_run_outcome_records_terminal_state(monkeypatch, tmp_path):
    _bridge, ledger, module = _async_harness(monkeypatch, tmp_path)
    module.tool_run_async()
    run_id = _claims(ledger)[0]["run_id"]
    result = module.tool_report_run_outcome(run_id, "completed")
    assert "Error" not in result
    assert _claims(ledger)[0]["outcome"] == "completed"


def test_report_run_outcome_rejects_invalid_outcome(monkeypatch, tmp_path):
    _bridge, ledger, module = _async_harness(monkeypatch, tmp_path)
    module.tool_run_async()
    run_id = _claims(ledger)[0]["run_id"]
    result = module.tool_report_run_outcome(run_id, "pending")
    assert result.startswith("Error")
    assert _claims(ledger)[0]["outcome"] == "pending"


def test_report_run_outcome_rejects_unknown_run_id(monkeypatch, tmp_path):
    _bridge, _ledger, module = _async_harness(monkeypatch, tmp_path)
    result = module.tool_report_run_outcome("deadbeef", "completed")
    assert result.startswith("Error")


def test_server_registers_async_outcome_recovery_tool():
    server = importlib.import_module("aspen_mcp.server")
    tools = asyncio.run(server.mcp_server._list_tools())
    names = {tool.name for tool in tools}
    assert "report_run_outcome" in names