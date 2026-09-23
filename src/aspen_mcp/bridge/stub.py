"""StubBridge — 纯内存模拟 Aspen Plus。

不 import win32com，不碰 COM。所有操作在 Python dict 里完成。
用于：开发测试、LLM 搭建计划验证（dry-run）、CI/CD。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from .base import AspenBridge

logger = logging.getLogger(__name__)


class _StubNode:
    """模拟 Aspen COM 树节点。"""

    def __init__(self, name: str, value: Any = None):
        self.Name = name
        self.Value = value
        self._children: dict[str, "_StubNode"] = {}
        self._order: list[str] = []

    def Elements(self, key):
        if isinstance(key, int):
            if 0 <= key < len(self._order):
                return self._children[self._order[key]]
            return None
        if key not in self._children:
            self._children[key] = _StubNode(key)
            self._order.append(key)
        return self._children[key]

    @property
    def Elements_Count(self):
        return len(self._order)

    def SetValue(self, _, value):
        self.Value = value

    def FindNode(self, path: str) -> "_StubNode | None":
        parts = [p for p in path.strip("\\").split("\\") if p]
        node = self
        for p in parts:
            if p not in node._children:
                return None
            node = node._children[p]
        return node


class StubBridge(AspenBridge):
    """内存中的 Aspen Plus。支持完整的树读写和模拟运行。"""

    def __init__(self):
        self._tree = _StubNode("")
        self._blocks: dict[str, str] = {}       # name -> type
        self._streams: dict[str, dict] = {}     # name -> {from, to, ...}
        self._converged = False
        self._calls: list[tuple] = []           # (method, args, kwargs)
        self._connected = False

        # 初始化最小树结构
        data = self._tree.Elements("Data")
        data.Elements("Setup")
        data.Elements("Components").Elements("Specifications")
        data.Elements("Properties").Elements("Specifications")
        data.Elements("Flowsheet").Elements("Section")
        data.Elements("Streams")
        data.Elements("Blocks")
        data.Elements("Reactions")
        data.Elements("Convergence")
        data.Elements("Results Summary").Elements("Run-Status")

    def _record(self, method: str, *args, **kwargs):
        self._calls.append((method, args, kwargs))

    # ── AspenBridge 实现 ──────────────────────────────────────────────

    def call(self, fn: Callable, timeout: float | None = None) -> Any:
        """Stub 模式：同步执行，无需 COM 线程；timeout 仅为签名兼容。"""
        return fn()

    @property
    def _app(self):
        """Stub 把树挂在自己身上，兼容 walk(aspen._app.RootModel(""), ...)。"""
        return self

    def RootModel(self, path: str = ""):
        return self._tree

    def status(self) -> dict:
        return {
            "connected": self._connected,
            "app": "StubBridge",
            "engine": "stopped",
            "ready": self._connected,
        }

    def probe(self) -> dict:
        return {"app_name": "StubBridge", "root_type": "_StubNode",
                "root_0_name": "Data", "root_Data": "Data"}

    def connect(self):
        self._connected = True
        self._record("connect")

    def disconnect(self):
        self._connected = False
        self._record("disconnect")

    def open_file(self, path: str):
        self._connected = True
        self._record("open_file", path)

    def close_file(self):
        self._connected = False
        self._record("close_file")

    def save(self, path=None):
        self._record("save", path)

    def generate_input_summary(self, file_path: str):
        self._record("generate_input_summary", file_path)

    def run(self):
        self._converged = True
        self._record("run")

    def run_async(self):
        self._record("run_async")

    def reinit(self):
        self._record("reinit")

    def reinit_and_run(self):
        self._converged = True
        self._record("reinit_and_run")

    def stop(self):
        self._record("stop")

    def set_visible(self, show: bool):
        self._record("set_visible", show)

    def set_batch_refresh(self, off: bool):
        self._record("set_batch_refresh", off)

    def run_script(self, file_path: str):
        self._record("run_script", file_path)

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

    def get_value(self, *parts: str) -> Any:
        node = self._tree
        for p in parts:
            if p in node._children:
                node = node._children[p]
            else:
                return None
        return node.Value

    def set_value(self, value: Any, *parts: str, unit: str | None = None) -> bool:
        from ..tools.units import guard_stream_path, unsupported_report, write_path_value
        path = "\\" + "\\".join(parts)
        guard = guard_stream_path(path)
        if guard is not None:
            self._last_write_result = unsupported_report(path, guard)
            return False
        report = write_path_value(self._tree, path, value, unit=unit)
        self._last_write_result = report
        return report["ok"]

    def get_path_value(self, path: str) -> Any:
        node = self._tree.FindNode(path)
        return node.Value if node else None

    def set_path_value(self, path: str, value: Any, unit: str | None = None) -> str:
        from ..tools.units import guard_stream_path, unsupported_report, write_path_value, format_write_result
        guard = guard_stream_path(path)
        if guard is not None:
            self._last_write_result = unsupported_report(path, guard)
            return format_write_result(self._last_write_result)
        report = write_path_value(self._tree, path, value, unit=unit)
        self._last_write_result = report
        return format_write_result(report)

    def get_node(self, *parts: str) -> Any:
        node = self._tree
        for p in parts:
            if p in node._children:
                node = node._children[p]
            else:
                return None
        return node

    def find_node(self, path: str) -> Any:
        return self._tree.FindNode(path)

    def root(self) -> Any:
        return self._tree

    def set_node_attribute(self, path: str, attribute: Any, value: Any) -> str:
        from ..tools.units import guard_stream_path
        guard = guard_stream_path(path)
        if guard is not None:
            return "Error: " + guard
        self._record("set_node_attribute", path, attribute, value)
        return "OK"

    def enumerate_children(self, node: _StubNode) -> list:
        return [node._children[k] for k in node._order]

    def child_names(self, node: _StubNode) -> list[str]:
        return list(node._order)

    # ── Stub 专属工具方法 ─────────────────────────────────────────────

    @property
    def calls(self) -> list:
        """返回所有已记录的方法调用。"""
        return self._calls

    def add_block_stub(self, name: str, block_type: str):
        self._blocks[name] = block_type
        blk = self._tree.Elements("Data").Elements("Blocks").Elements(name)
        blk.Elements("Input")
        blk.Elements("Output")
        blk.Elements("Ports")
        self._record("add_block", name, block_type)

    def add_stream_stub(self, name: str):
        self._streams[name] = {}
        s = self._tree.Elements("Data").Elements("Streams").Elements(name)
        inp = s.Elements("Input")
        inp.Elements("TEMP").Elements("MIXED")
        inp.Elements("PRES").Elements("MIXED")
        inp.Elements("TOTAL").Elements("MIXED")
        inp.Elements("FLOW").Elements("MIXED")
        s.Elements("Output")
        self._record("add_stream", name)

    @property
    def block_list(self) -> list[str]:
        return list(self._blocks.keys())

    @property
    def stream_list(self) -> list[str]:
        return list(self._streams.keys())

    @property
    def converged(self) -> bool:
        return self._converged
