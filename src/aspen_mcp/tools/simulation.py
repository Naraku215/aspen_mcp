"""仿真控制工具：打开、关闭、运行、保存、状态。"""

from __future__ import annotations

from ..com_bridge import aspen
from ._common import walk
from .analysis import _get_convergence_report


def _check_all_uncalculated() -> str | None:
    """Check blocks after run: collect BLKMSG errors.
    In Aspen v15 (41.0), BLKSTAT=0 means "converged", not "uncalculated".
    BLKSTAT=2 means "not converged" (ran but diverged).
    BLKMSG has the actual Aspen error text.
    This function checks BLKMSG and BLKSTAT=2 to find actual problems.
    """
    try:
        def impl():
            tree = aspen._app.RootModel("")
            blks = walk(tree, "Data", "Blocks")
            if blks is None:
                return None
            issues = []
            for i in range(500):
                try:
                    b = blks.Elements(i)
                except Exception:
                    break
                if b is None:
                    break
                bname = b.Name
                blkstat = walk(tree, "Data", "Blocks", bname, "Output", "BLKSTAT")
                blkmsg = walk(tree, "Data", "Blocks", bname, "Output", "BLKMSG")
                if blkmsg is not None and blkmsg.Value:
                    issues.append("  " + bname + ": BLKMSG=" + str(blkmsg.Value))
                elif blkstat is not None and blkstat.Value == 2:
                    issues.append("  " + bname + ": BLKSTAT=2 (not converged)")
            if issues:
                return "Block issues after run:\n" + "\n".join(issues)
            return None
        return aspen.call(impl)
    except Exception:
        return None
def _check_feeds_before_run() -> str | None:
    """Check feed stream inputs before running.
    A feed stream has no upstream block (no SOURCE in Output).
    Those require the user to set TEMP, PRES, and composition.
    Returns an error message if any feed is incomplete, None if all OK.
    """
    try:
        def impl():
            tree = aspen._app.RootModel("")
            streams_node = walk(tree, "Data", "Streams")
            if streams_node is None:
                return None
            issues = []
            for i in range(500):
                try:
                    s = streams_node.Elements(i)
                except Exception:
                    break
                if s is None:
                    break
                sname = s.Name
                # 检查此流股是否连接在某个模块的输出端口上
                is_output = False
                blks = walk(tree, "Data", "Blocks")
                if blks is not None:
                    for bi in range(500):
                        try:
                            b2 = blks.Elements(bi)
                        except Exception:
                            break
                        if b2 is None:
                            break
                        ports_node = walk(tree, "Data", "Blocks", b2.Name, "Ports")
                        if ports_node is not None:
                            for pi in range(50):
                                try:
                                    port = ports_node.Elements(pi)
                                except Exception:
                                    break
                                if port is None:
                                    break
                                if "(OUT)" in port.Name:
                                    for ei in range(port.Elements.Count):
                                        try:
                                            if port.Elements.Item(ei).Name == sname:
                                                is_output = True
                                                break
                                        except Exception:
                                            pass
                                if is_output:
                                    break
                        if is_output:
                            break
                if is_output:
                    continue
                missing = []
                temp = walk(tree, "Data", "Streams", sname, "Input", "TEMP", "MIXED")
                if temp is None or temp.Value is None:
                    missing.append("TEMP")
                pres = walk(tree, "Data", "Streams", sname, "Input", "PRES", "MIXED")
                if pres is None or pres.Value is None:
                    missing.append("PRES")
                flow_node = walk(tree, "Data", "Streams", sname, "Input", "FLOW", "MIXED")
                has_comp = False
                if flow_node is not None:
                    for ci in range(100):
                        try:
                            comp = flow_node.Elements(ci)
                        except Exception:
                            break
                        if comp is None:
                            break
                        if comp.Value is not None and comp.Value > 0:
                            has_comp = True
                            break
                if not has_comp:
                    total = walk(tree, "Data", "Streams", sname, "Input", "TOTAL", "MIXED")
                    if total is None or total.Value is None or total.Value == 0:
                        missing.append("composition (no component flow or total flow)")
                if missing:
                    issues.append(
                        "  Stream '" + sname + "' missing: " + ", ".join(missing)
                    )
            if issues:
                NL = "\n"
                result = "Feed stream inputs incomplete. "
                result += "Please set these before running:" + NL
                result += NL.join(issues)
                result += NL + NL + "Tip: set_stream_param('name', 'TEMP', value)"
                result += " / set_stream_param('name', 'PRES', value)" + NL
                result += "      set_stream_composition('name', 'component', flow)"
                return result
            return None
        return aspen.call(impl)
    except Exception:
        return None

def _execution_ledger():
    """共享执行账本；独立函数便于离线测试注入。"""
    from ..execution import get_ledger
    return get_ledger()

def _claim_native_run() -> dict:
    """三 run 入口共用准入：首跑用尽后，仅有显式 TUNE 授权才可继续。"""
    ledger = _execution_ledger()
    claim = ledger.claim_run(kind="initial")
    if claim["status"] == "accepted":
        return claim
    if claim["reason"] == "budget_exhausted":
        tune = ledger.claim_run(kind="tune")
        if tune["status"] == "accepted":
            return tune
    return claim

def _blocked_run_message(claim: dict) -> str:
    return ("Error: RUN 被阻断（" + str(claim.get("reason")) + "）。"
            "额度、授权与沿袭绑定任务，不能通过请求增额；请人工确认后继续。")

_RUN_TIMEOUT_MESSAGE = ("Error: RUN 超时，结果未知（outcome_unknown）。已保留主机侧证据，"
                        "不自动重试；请人工确认引擎状态后再决定恢复方式。")

def _record_run_outcome(claim: dict, exc: Exception | None = None) -> None:
    """记录终态；超时类异常记为 outcome_unknown，额度不退。"""
    if exc is None:
        outcome = "completed"
    elif isinstance(exc, TimeoutError):
        outcome = "outcome_unknown"
    else:
        outcome = "failed"
    _execution_ledger().record_outcome(run_id=claim["run_id"], outcome=outcome)
def tool_status(random_string: str | None = None) -> dict:
    """Return the current Aspen Plus connection / engine status."""
    return aspen.status()

def tool_probe(random_string: str | None = None) -> dict:
    """Diagnose COM state (app, root, tree access)."""
    return aspen.probe()

def tool_open_file(file_path: str) -> str:
    """Open an Aspen Plus simulation .apw file."""
    try:
        aspen.open_file(file_path)
        return "Opened " + file_path
    except Exception as exc:
        exc_str = str(exc)
        if "32" in exc_str and "being used" in exc_str.lower():
            return "File is locked: " + str(exc) + ". Try close_file() first, then retry open_file()."
        if "RPC" in exc_str:
            return "COM error: " + str(exc) + ". Try close_file() then open_file() again."
        return "Error: " + str(exc)
def tool_close_file(random_string: str | None = None) -> str:
    """Close the currently-open simulation file."""
    try:
        aspen.close_file()
        return "File closed"
    except Exception as exc:
        return "Error: " + str(exc)
def tool_run(random_string: str | None = None) -> str:
    """Run the simulation (synchronous) and return convergence diagnostics."""
    try:
        feed_ok = _check_feeds_before_run()
        if feed_ok:
            return feed_ok
        claim = _claim_native_run()
        if claim["status"] != "accepted":
            return _blocked_run_message(claim)
        try:
            aspen.run()
        except Exception as exc:
            _record_run_outcome(claim, exc)
            raise
        _record_run_outcome(claim)
        report = aspen.call(lambda: _get_convergence_report())
        result = "Simulation run completed.\n\n" + (report or "")
        warning = _check_all_uncalculated()
        if warning:
            result += "\n\n" + (warning or "")
        return result
    except Exception as exc:
        exc_str = str(exc)
        if isinstance(exc, TimeoutError):
            return _RUN_TIMEOUT_MESSAGE
        if "RPC" in exc_str or chr(26381) in exc_str:
            return "COM error: " + str(exc) + ". Try close_file() then open_file() to recover, then reinit_and_run()."
        return "Error: " + str(exc)
def tool_run_async(random_string: str | None = None) -> str:
    """Run the simulation (asynchronous, non-blocking).

    异步派发后保持运行态占用（claim 待定），直到可靠终态；
    派发成功返回 run_id，终态需经 report_run_outcome 显式回收。
    禁止第二跑与模型写入，允许受控只读状态轮询。
    """
    try:
        claim = _claim_native_run()
        if claim["status"] != "accepted":
            return _blocked_run_message(claim)
        try:
            aspen.run_async()
        except Exception as exc:
            _record_run_outcome(claim, exc)
            raise
        return (f"Async simulation started (run_id={claim['run_id']})。"
                "终态需显式回收：运行结束后调用 report_run_outcome(run_id, outcome)。")
    except Exception as exc:
        return "Error: " + str(exc)


_TERMINAL_OUTCOMES = ("completed", "failed", "outcome_unknown")


def tool_report_run_outcome(run_id: str, outcome: str) -> str:
    """回收异步运行的显式终态记录；不退还额度，仅终结 pending claim。"""
    if outcome not in _TERMINAL_OUTCOMES:
        return ("Error: outcome 必须是 " + "|".join(_TERMINAL_OUTCOMES)
                + " 之一（不接受内部状态 pending）")
    try:
        _execution_ledger().record_outcome(run_id=run_id, outcome=outcome)
    except Exception as exc:
        return "Error: " + str(exc)
    return f"Run {run_id} recorded as {outcome}"


def tool_save(file_path: str | None = None) -> str:
    """Save the simulation. Optionally provide SaveAs path."""
    try:
        aspen.save(file_path)
        return "Saved to " + str(file_path) if file_path else "File saved"
    except Exception as exc:
        return "Error: " + str(exc)
def tool_reinit(random_string: str | None = None) -> str:
    """Reinitialize the simulation (reset results before re-running)."""
    try:
        aspen.reinit()
        return "Simulation reinitialized"
    except Exception as exc:
        return "Error: " + str(exc)
def tool_reinit_and_run(random_string: str | None = None) -> str:
    """Reinitialize then run synchronously.
    Reinit() clears old engine caches so Aspen rebuilds calculation order;
    Run2(0) executes synchronously.
    """
    try:
        feed_ok = _check_feeds_before_run()
        if feed_ok:
            return feed_ok
        claim = _claim_native_run()
        if claim["status"] != "accepted":
            return _blocked_run_message(claim)
        try:
            aspen.reinit_and_run()
        except Exception as exc:
            _record_run_outcome(claim, exc)
            raise
        _record_run_outcome(claim)
        report = aspen.call(lambda: _get_convergence_report())
        result = "Simulation reinitialized and run completed.\n\n" + (report or "")
        warning = _check_all_uncalculated()
        if warning:
            result += "\n\n" + (warning or "")
        return result
    except Exception as exc:
        exc_str = str(exc)
        if isinstance(exc, TimeoutError):
            return _RUN_TIMEOUT_MESSAGE
        if "RPC" in exc_str or chr(26381) in exc_str:
            return "COM error: " + str(exc) + ". Try close_file() then open_file() to recover, then reinit_and_run() again."
        return "Error: " + str(exc)
def tool_new_simulation(random_string: str | None = None) -> str:
    """创建一个新的空白模拟。

    使用 Aspen 内置的 InitNew() 创建空白模拟，
    包含 0 个组分和空拓扑。
    无需外部模板文件。
    """
    try:
        try:
            aspen.call(lambda: aspen._app.InitNew())
        except Exception:
            # 如果已初始化（错误 2001），仅执行 reinit
            aspen.reinit()
        return ("New blank simulation created.\n"
                "  - Components: none (add with add_component)\n"
                "  - Property: PENG-ROB (change with set_property_method)\n"
                "  - Topology: empty (add with add_block / add_stream)\n"
                "Tip: run reinit_and_run() before first use to clear old results.")
    except Exception as exc:
        return "Error: " + str(exc)


def tool_stop_simulation(random_string: str | None = None) -> str:
    """Stop the currently running simulation."""
    try:
        aspen.stop()
        return "Simulation stopped."
    except Exception as exc:
        return "Error: " + str(exc)
def tool_visible(show: bool) -> str:
    """Show or hide the Aspen Plus GUI window."""
    try:
        aspen.set_visible(show)
        return "Aspen Plus window is now " + ("visible" if show else "hidden") + "."
    except Exception as exc:
        return "Error: " + str(exc)
def tool_run_script(file_path: str) -> str:
    '''Run a Python script inside Aspen Plus.
    The script runs in Aspen's internal Python environment,
    not the MCP host environment.
    建模任务活跃或沿袭确认挂起期间，本通道关闭（禁绕行原生运行）。
    '''
    guard = _execution_ledger().script_guard()
    if not guard["allowed"]:
        return ("Error: run_script 被阻断（" + guard["reason"] + "）；"
                "建模任务服务端禁止 run_script 绕行原生运行，请使用受控 run 入口。")
    try:
        aspen.run_script(file_path)
        return "Script '" + file_path + "' executed."
    except Exception as exc:
        return "Error: " + str(exc)
def tool_batch_refresh(off: bool) -> str:
    """Disable or re-enable GUI refresh.
    Turn refresh OFF before a series of bulk operations for speed,
    then turn it back ON when done.
    """
    try:
        aspen.set_batch_refresh(off)
        return "GUI refresh " + ("disabled" if off else "enabled") + "."
    except Exception as exc:
        return "Error: " + str(exc)