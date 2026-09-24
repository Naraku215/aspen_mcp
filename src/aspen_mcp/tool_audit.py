"""服务端原始审计：注册前统一 wrapper，请求先落盘、返回后落响应。

- 保留函数签名/schema（functools.wraps），返回的标量/list 类型与业务元素不变。
- 记录 call_id、含时区时间、单调耗时、完整参数/返回和异常；
  返回的 "Error: ..." 字符串与 ok=False 的字典同样计为失败。
- 大输出写本地附件＋sha256；仅脱敏凭据等敏感字段；不污染 MCP stdout。
- 未配置日志目录时用服务工作目录专用子目录，不写入历史 case。
"""
from __future__ import annotations

import functools
import hashlib
import inspect
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from ._directories import default_directory

_ATTACHMENT_THRESHOLD = 64_000
_SENSITIVE_KEYS = {"password", "passwd", "token", "secret", "credential", "api_key"}
_ERROR_STATUSES = {"failed", "unsupported", "partial_failure"}

_logger = logging.getLogger(__name__)


def default_audit_directory() -> str:
    """审计目录：env 优先；cwd 子目录不可写时回落用户可写目录。"""
    return default_directory(env_var="ASPEN_MCP_AUDIT_DIR",
                             cwd_subdir=".aspen_mcp_audit",
                             fallback_subdir="audit")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: ("<redacted>" if str(key).lower() in _SENSITIVE_KEYS else _redact(item))
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


def _dump(value: Any) -> str:
    """尽力序列化：标准 JSON 优先，NaN/Inf 等降级为 Python 方言。"""
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, default=str)
    except (TypeError, ValueError):
        return json.dumps(value, ensure_ascii=False, default=str)


def _jsonable(value: Any) -> str:
    return _dump(_redact(value))


def _outcome_of(result: Any) -> str:
    """错误字符串/失败字典同样计为失败，不能只看 Python 异常。"""
    if isinstance(result, str):
        return "error" if result.lstrip().startswith("Error") else "ok"
    if isinstance(result, dict):
        if result.get("ok") is False:
            return "error"
        status = result.get("status")
        if isinstance(status, str) and status in _ERROR_STATUSES:
            return "error"
        error = result.get("error")
        if error:
            return "error"
    return "ok"


class AuditLog:
    """本地审计存储；索引 JSONL 追加写并立即 flush。"""

    def __init__(self, directory: str | None = None):
        self.directory = directory or default_audit_directory()
        self._lock = threading.RLock()
        self._entries: list[dict] = []
        self._index_path = os.path.join(self.directory, "audit.jsonl")
        self._attachment_dir = os.path.join(self.directory, "attachments")

    # ── 存储 ────────────────────────────────────────────────────────────
    def _append(self, entry: dict) -> None:
        """审计落盘是旁路能力：失败降级为警告，不影响业务调用。"""
        with self._lock:
            self._entries.append(entry)
            try:
                os.makedirs(self.directory, exist_ok=True)
                with open(self._index_path, "a", encoding="utf-8") as handle:
                    handle.write(_dump(entry) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            except Exception as exc:
                _logger.warning("审计落盘失败（已降级）: %s", exc)

    def read_entries(self) -> list[dict]:
        with self._lock:
            return [dict(entry) for entry in self._entries]

    def read_attachment(self, path: str) -> str:
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def _write_attachment(self, call_id: str, payload: Any) -> dict:
        os.makedirs(self._attachment_dir, exist_ok=True)
        path = os.path.join(self._attachment_dir, f"{call_id}.json")
        text = _jsonable(payload)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return {"__attachment__": True, "path": path, "sha256": digest, "bytes": len(text)}

    def _summarize(self, call_id: str, payload: Any) -> Any:
        """摘要失败降级为占位符，绝不因审计中断业务返回。"""
        try:
            text = _jsonable(payload)
            if len(text) > _ATTACHMENT_THRESHOLD:
                return self._write_attachment(call_id, payload)
            return json.loads(text)
        except Exception as exc:
            _logger.warning("审计摘要降级: %s", exc)
            return "<unserializable>"

    # ── 注册 wrapper ────────────────────────────────────────────────────
    def wrap_tool(self, fn: Callable, *, name: str | None = None,
                  context: dict | None = None) -> Callable:
        tool_name = name or getattr(fn, "__name__", "tool")

        def _bind_args(args: tuple, kwargs: dict) -> dict:
            try:
                bound = inspect.signature(fn).bind(*args, **kwargs)
                bound.apply_defaults()
                return dict(bound.arguments)
            except Exception:
                return {"args": list(args), "kwargs": dict(kwargs)}

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            call_id = uuid.uuid4().hex
            base = {"tool": tool_name, "call_id": call_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "timezone": "UTC",
                    "context": dict(context or {})}
            self._append({**base, "event": "request",
                          "args": _redact(_bind_args(args, kwargs))})
            started = time.monotonic()
            try:
                result = fn(*args, **kwargs)
            except BaseException as exc:
                self._append({**base, "event": "response", "outcome": "error",
                              "error": f"{type(exc).__name__}: {exc}",
                              "monotonic_seconds": time.monotonic() - started})
                raise
            self._append({**base, "event": "response", "outcome": _outcome_of(result),
                          "monotonic_seconds": time.monotonic() - started,
                          "result": self._summarize(call_id, result)})
            return result

        return wrapper


_singleton: AuditLog | None = None
_singleton_lock = threading.Lock()


def get_audit_log() -> AuditLog:
    """服务进程的共享审计存储（惰性单例）。"""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = AuditLog()
        return _singleton