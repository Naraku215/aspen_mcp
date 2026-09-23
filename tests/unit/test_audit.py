"""审计：请求先落盘、错误字符串计失败、返回类型不变、签名保留、大输出附件。"""
import asyncio
import importlib
import inspect
import json
import os

import pytest

from aspen_mcp.tool_audit import default_audit_directory


def audit():
    assert importlib.util.find_spec("aspen_mcp.tool_audit") is not None, "缺少审计模块"
    return importlib.import_module("aspen_mcp.tool_audit")


@pytest.fixture
def log(tmp_path):
    module = audit()
    return module.AuditLog(directory=str(tmp_path / "audit"))


def test_signature_and_name_preserved(log):
    def sample(alpha: int, beta: str = "") -> dict:
        return {"ok": True}

    wrapped = log.wrap_tool(sample)
    assert wrapped.__name__ == "sample"
    assert inspect.signature(wrapped) == inspect.signature(sample)


def test_request_flushed_before_response(log):
    events = []

    def fn(x: int) -> dict:
        events.append(("called", x))
        assert log.read_entries()[-1]["event"] == "request", "请求必须先落盘再执行"
        return {"ok": True, "x": x}

    wrapped = log.wrap_tool(fn, name="fn")
    assert wrapped(1) == {"ok": True, "x": 1}
    entries = log.read_entries()
    assert [e["event"] for e in entries] == ["request", "response"]
    assert entries[0]["args"] == {"x": 1}
    assert entries[1]["result"] == {"ok": True, "x": 1}
    assert entries[0]["call_id"] == entries[1]["call_id"]
    assert entries[1]["monotonic_seconds"] >= 0


def test_error_string_counts_as_failure(log):
    wrapped = log.wrap_tool(lambda: "Error: blocked", name="broken")
    assert wrapped() == "Error: blocked"
    entry = log.read_entries()[-1]
    assert entry["event"] == "response"
    assert entry["outcome"] == "error"


def test_ok_string_counts_as_success(log):
    wrapped = log.wrap_tool(lambda: "OK: fine", name="good")
    assert wrapped() == "OK: fine"
    assert log.read_entries()[-1]["outcome"] == "ok"


def test_scalar_and_list_types_unchanged(log):
    wrapped_int = log.wrap_tool(lambda: 42, name="int_tool")
    wrapped_list = log.wrap_tool(lambda: ["A", "B"], name="list_tool")
    wrapped_dict = log.wrap_tool(lambda: {"error": {"code": 1}}, name="dict_tool")
    assert wrapped_int() == 42
    assert wrapped_list() == ["A", "B"]
    assert wrapped_dict() == {"error": {"code": 1}}
    assert log.read_entries()[-1]["result"] == {"error": {"code": 1}}


def test_exception_recorded_and_reraised(log):
    def boom() -> dict:
        raise RuntimeError("native refusal")

    wrapped = log.wrap_tool(boom, name="boom")
    with pytest.raises(RuntimeError, match="native refusal"):
        wrapped()
    entry = log.read_entries()[-1]
    assert entry["event"] == "response"
    assert entry["outcome"] == "error"
    assert "native refusal" in entry["error"]


def test_large_output_uses_attachment_with_hash(log):
    payload = {"blob": "x" * 200_000}

    wrapped = log.wrap_tool(lambda: payload, name="big_tool")
    result = wrapped()
    assert result == payload
    entry = log.read_entries()[-1]
    assert entry["result"]["__attachment__"] is True
    assert len(entry["result"]["sha256"]) == 64
    attachment = log.read_attachment(entry["result"]["path"])
    assert json.loads(attachment) == payload


def test_stdout_stays_clean(log, capsys):
    wrapped = log.wrap_tool(lambda: {"ok": True}, name="quiet")
    wrapped()
    captured = capsys.readouterr()
    assert captured.out == ""


def test_context_ids_are_recorded(log):
    wrapped = log.wrap_tool(lambda: None, name="ctx")
    wrapped()
    entry = log.read_entries()[-1]
    for key in ("call_id", "timestamp", "timezone"):
        assert entry.get(key), f"缺少 {key}"
    assert entry["tool"] == "ctx"
    assert entry["outcome"] == "ok"


def test_server_entrypoints_wrapped_before_registration():
    """注册前的统一 wrapper：tool_* 入口被审计包装且签名保留。"""
    server = importlib.import_module("aspen_mcp.server")
    assert getattr(server.tool_status, "__audit_wrapped__", False), \
        "server 必须在注册前包装 tool_* 入口"
    original = importlib.import_module("aspen_mcp.tools.simulation").tool_status
    assert inspect.signature(server.tool_status) == inspect.signature(original)


def test_server_entrypoint_call_lands_in_audit_record():
    """服务入口调用记录 request/response；不假定 FastMCP 中间件存在。"""
    server = importlib.import_module("aspen_mcp.server")
    log = server.get_audit_log()
    before = len(log.read_entries())
    result = server.tool_status(None)
    assert result is not None
    entries = log.read_entries()[before:]
    assert [e["event"] for e in entries] == ["request", "response"]
    assert entries[0]["tool"] == "status"
    assert entries[0]["args"] == {"random_string": None}
    assert entries[0]["call_id"] == entries[1]["call_id"]


def test_server_registers_configuration_tools():
    """批配置与只读预检工具在 FastMCP 注册表中可用。"""
    server = importlib.import_module("aspen_mcp.server")
    tools = asyncio.run(server.mcp_server._list_tools())
    names = {tool.name for tool in tools}
    assert {"configure_model", "inspect_configuration"} <= names


def test_default_audit_directory_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("ASPEN_MCP_AUDIT_DIR", str(tmp_path / "audit"))
    assert default_audit_directory() == str(tmp_path / "audit")


def test_default_audit_directory_falls_back_when_cwd_unwritable(monkeypatch, tmp_path):
    monkeypatch.delenv("ASPEN_MCP_AUDIT_DIR", raising=False)
    blocker = tmp_path / "blocker"
    blocker.write_text("x")  # 文件挡住子目录创建 → OSError
    monkeypatch.setattr(os, "getcwd", lambda: str(blocker / "sub"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    result = default_audit_directory()
    assert result == str(tmp_path / "local" / "aspen-mcp" / "audit")
    assert os.path.isdir(result)