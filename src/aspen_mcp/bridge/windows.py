"""Windows COM Bridge — 真实 pywin32 连接 Aspen Plus V15 (41.0)。

从 com_bridge.py 的 AspenConnection 迁移而来，继承 AspenBridge 抽象基类。
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable

import pythoncom
import win32com.client

from .base import AspenBridge

try:
    pythoncom.CoInitialize()
except Exception:
    pass

logger = logging.getLogger(__name__)

# 普通 COM 请求 ≤30 秒、RUN 生命周期 ≤60 秒；取消三次重试与自动重连重放。
_TIMEOUT_SECONDS = 30
_RUN_TIMEOUT_SECONDS = 60
_STOP_SENTINEL = object()


# ── COM 套间线程 ──────────────────────────────────────────────────────


class _ComJob:
    """一次 COM 派发；未开始且过期不执行，迟到结果保留、不重放。"""

    def __init__(self, fn, deadline=None, clock=None):
        self.fn = fn
        self.deadline = deadline
        self._clock = clock or time.monotonic
        self.started = False
        self._late = None
        self._late_error = None
        self.result_queue = queue.Queue(1)

    def run(self):
        if self.deadline is not None and self._clock() >= self.deadline:
            self.result_queue.put(("expired", {"status": "not_started"}))
            return
        self.started = True
        try:
            self.result_queue.put(("ok", self.fn()))
        except BaseException as exc:
            self.result_queue.put(("exc", exc))

    def wait(self, timeout):
        try:
            status, value = self.result_queue.get(timeout=timeout)
        except queue.Empty:
            if not self.started:
                raise TimeoutError(
                    f"COM job expired before start after {timeout}s (not_started)")
            raise TimeoutError(f"COM timeout after {timeout}s (outcome_unknown)")
        if status == "expired":
            raise TimeoutError("COM job expired before start (not_started)")
        if status == "exc":
            raise value
        return value

    def late_result(self):
        """取回超时后才完成的结果；无迟到结果返回 None。"""
        try:
            status, value = self.result_queue.get_nowait()
        except queue.Empty:
            return self._late
        if status == "ok":
            self._late = value
        elif status == "exc":
            self._late_error = value
        return self._late


class _ComApartment:
    def __init__(self):
        self._job_queue = queue.Queue()
        self._thread = None
        self._lock = threading.Lock()

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run, name="com-apartment", daemon=True,
            )
            self._thread.start()

    def stop(self):
        self._job_queue.put(_STOP_SENTINEL)

    def _run(self):
        pythoncom.CoInitialize()
        while True:
            item = self._job_queue.get()
            if item is _STOP_SENTINEL:
                break
            assert isinstance(item, _ComJob)
            item.run()
            try:
                pythoncom.PumpWaitingMessages()
            except Exception:
                pass
        pythoncom.CoUninitialize()

    def submit(self, fn, timeout=_TIMEOUT_SECONDS):
        self.start()
        job = _ComJob(fn, deadline=time.monotonic() + timeout)
        self._job_queue.put(job)
        return job.wait(timeout)


_COM = _ComApartment()


# ── WindowsBridge ─────────────────────────────────────────────────────


class WindowsBridge(AspenBridge):

    def __init__(self):
        self._doc = None

    def _connect(self):
        if self._doc is not None:
            return
        app = win32com.client.Dispatch("Apwn.Document")
        app.SuppressDialogs = True
        _ = app.Name
        self._doc = app
        logger.info("Connected to Aspen Plus (v15 / 41.0)")

    def _disconnect(self):
        self._doc = None

    def call(self, fn, timeout=None):
        """单次派发到 COM 套间线程；超时或断连不重试、不重放。

        超时后结果未知（outcome_unknown），不盲目重连或重发；
        恢复方式由用户确认后另行操作。
        """
        if self._doc is None:
            _COM.submit(self._connect)
        if timeout is None:
            return _COM.submit(fn)
        return _COM.submit(fn, timeout)

    @property
    def _app(self):
        return self._doc

    def status(self):
        def impl():
            if self._doc is None:
                return {"connected": False, "engine": "stopped"}
            try:
                e = self._doc.Engine
                return {"connected": True, "app": self._doc.Name,
                        "engine": "running" if e.IsRunning else "stopped", "ready": e.Ready}
            except Exception:
                return {"connected": False, "engine": "stopped"}
        return self.call(impl)

    def probe(self):
        def impl():
            r = {}
            try:
                r["app_name"] = self._doc.Name if self._doc else "NO_APP"
            except Exception as e:
                r["app_error"] = str(e)[:60]
            try:
                if self._doc is None:
                    return r
                root = self._doc.RootModel("")
                r["root_type"] = type(root).__name__
                r["root_0_name"] = root.Elements(0).Name
                r["root_Data"] = root.Elements("Data").Name
            except Exception as e:
                r["root_error"] = str(e)[:60]
            return r
        return self.call(impl)

    def connect(self):
        self.call(lambda: self._connect() or True)

    def disconnect(self):
        self._disconnect()

    def open_file(self, path):
        self.call(lambda: self._doc.InitFromFile(path))

    def close_file(self):
        if self._doc is None:
            return
        try:
            self.call(lambda: self._doc.Close())
        except Exception:
            pass
        self._doc = None

    def save(self, path=None):
        if path:
            self.call(lambda: self._doc.SaveAs(path))
        else:
            self.call(lambda: self._doc.Save())

    def generate_input_summary(self, file_path):
        self.call(lambda: self._doc.Generate(file_path, 0))

    def run(self):
        self.call(lambda: self._doc.Run2(), _RUN_TIMEOUT_SECONDS)

    def run_async(self):
        self.call(lambda: self._doc.Engine.Run(), _RUN_TIMEOUT_SECONDS)

    def reinit(self):
        self.call(lambda: self._doc.Engine.Reinit())

    def reinit_and_run(self):
        self.call(lambda: (self._doc.Engine.Reinit(), self._doc.Run2()),
                  _RUN_TIMEOUT_SECONDS)

    def stop(self):
        self.call(lambda: self._doc.Engine.Stop())

    def set_visible(self, show):
        self.call(lambda: setattr(self._doc, "Visible", show))

    def set_batch_refresh(self, off):
        self.call(lambda: setattr(self._doc, "RefreshOff", off))

    def run_script(self, file_path):
        self.call(lambda: self._doc.RunScript(file_path))

    def set_stream_param(self, stream_name: str, param: str, value: str | float,
                         *legacy: str, basis: str | None = None, unit: str | None = None,
                         total_basis: str | None = None) -> dict:
        from ..stream_contract import _stream_param
        return _stream_param(self, stream_name, param, value, legacy=legacy,
                             basis=basis, unit=unit, total_basis=total_basis)

    def set_stream_composition(self, stream_name: str, component: str, flow: str | float,
                               *legacy: str, unit: str | None = None, basis: str | None = None,
                               total_basis: str | None = None) -> dict:
        from ..stream_contract import _stream_component
        return _stream_component(self, stream_name, component, flow, legacy=legacy,
                                 unit=unit, basis=basis, total_basis=total_basis)

    def set_stream_composition_batch(self, stream_name: str, components: dict,
                                     basis: str = "MOLE-FLOW", total_flow: float | None = None,
                                     unit: str | None = None, *, total_basis: str | None = None,
                                     total_unit: str | None = None) -> dict:
        from ..stream_contract import _stream_update
        return _stream_update(self, stream_name, components, basis=basis, total_flow=total_flow,
                              unit=unit, total_basis=total_basis, total_unit=total_unit)

    def get_value(self, *parts):
        def impl():
            node = _walk(self._doc.RootModel(""), *parts)
            return node.Value if node is not None else None
        return self.call(impl)

    def get_si_value(self, *parts):
        def impl():
            from ..tools.units import read_si
            node = _walk(self._doc.RootModel(""), *parts)
            return read_si(node)
        return self.call(impl)

    def set_value(self, value: Any, *parts: str, unit: str | None = None) -> bool:
        from ..tools.units import guard_stream_path, unsupported_report, write_path_value
        path = "\\" + "\\".join(parts)
        guard = guard_stream_path(path)
        if guard is not None:
            self._last_write_result = unsupported_report(path, guard)
            return False

        def impl():
            report = write_path_value(self._doc.RootModel(""), path, value, unit=unit)
            self._last_write_result = report
            return report["ok"]
        return self.call(impl)

    def get_path_value(self, path):
        def impl():
            node = self._doc.RootModel("").FindNode(path)
            return node.Value if node is not None else None
        return self.call(impl)

    def set_path_value(self, path: str, value: Any, unit: str | None = None) -> str:
        from ..tools.units import guard_stream_path, unsupported_report, write_path_value, format_write_result
        guard = guard_stream_path(path)
        if guard is not None:
            self._last_write_result = unsupported_report(path, guard)
            return format_write_result(self._last_write_result)

        def impl():
            report = write_path_value(self._doc.RootModel(""), path, value, unit=unit)
            self._last_write_result = report
            return format_write_result(report)
        return self.call(impl)

    def set_node_attribute(self, path, attribute, value):
        from ..tools.units import guard_stream_path
        guard = guard_stream_path(path)
        if guard is not None:
            return "Error: " + guard

        def impl():
            node = self._doc.RootModel("").FindNode(path)
            if node is None:
                return f"Node not found: {path}"
            dispid = node._oleobj_.GetIDsOfNames("AttributeValue")
            if isinstance(dispid, tuple):
                dispid = dispid[0]
            node._oleobj_.Invoke(dispid, 0, pythoncom.DISPATCH_PROPERTYPUT, 0,
                                 attribute, False, value)
            return "OK"
        return self.call(impl)

    def get_node(self, *parts):
        return self.call(lambda: _walk(self._doc.RootModel(""), *parts))

    def find_node(self, path):
        return self.call(lambda: self._doc.RootModel("").FindNode(path))

    def root(self):
        return self.call(lambda: self._doc.RootModel(""))

    def enumerate_children(self, node):
        kids = []
        for i in range(10000):
            try:
                child = node.Elements(i)
            except Exception:
                break
            if child is None:
                break
            kids.append(child)
        return kids

    def child_names(self, node):
        return [c.Name for c in self.enumerate_children(node)]


def _walk(root, *parts):
    node = root
    for p in parts:
        try:
            node = node.Elements(p)
        except Exception:
            return None
        if node is None:
            return None
    return node
