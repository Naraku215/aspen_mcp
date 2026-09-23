"""白名单离线测试隔离：先封锁原生依赖，再导入桥及服务。"""
import importlib
import os
import sys
import tempfile
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
_isolation = pytest.MonkeyPatch()

# 审计/账本默认目录隔离到临时区，防止离线测试污染工作目录或历史 case。
_session_tmp = tempfile.mkdtemp(prefix="aspen_mcp_offline_")
_isolation.setenv("ASPEN_MCP_AUDIT_DIR", os.path.join(_session_tmp, "audit"))
_isolation.setenv("ASPEN_MCP_STATE_DIR", os.path.join(_session_tmp, "state"))


def forbidden(*args, **kwargs):
    raise AssertionError("离线测试禁止真实 COM、线程调度及 Aspen 初始化")


pythoncom = ModuleType("pythoncom")
pythoncom.CoInitialize = lambda: None
pythoncom.CoUninitialize = forbidden
pythoncom.PumpWaitingMessages = forbidden
pythoncom.DISPATCH_PROPERTYPUT = 4
win32com = ModuleType("win32com")
win32com.__path__ = []
client = ModuleType("win32com.client")
client.Dispatch = forbidden
client.DispatchEx = forbidden
win32com.client = client
for name, module in (("pythoncom", pythoncom), ("win32com", win32com), ("win32com.client", client)):
    _isolation.setitem(sys.modules, name, module)

import aspen_mcp.bridge as bridge_factory
from aspen_mcp.bridge.stub import StubBridge

# 必须在 com_bridge/server/tools 的首次导入之前替换工厂。
_isolation.setattr(bridge_factory, "make_bridge", lambda: StubBridge())
windows = importlib.import_module("aspen_mcp.bridge.windows")
_isolation.setattr(windows._COM, "submit", forbidden)
_isolation.setattr(windows.WindowsBridge, "_connect", forbidden)


def pytest_unconfigure(config):
    _isolation.undo()


class Children:
    def __init__(self, node):
        self.node = node

    @property
    def Count(self):
        return len(self.node.children)

    def __call__(self, key):
        if isinstance(key, int):
            return list(self.node.children.values())[key]
        return self.node.children.get(key)


class Node:
    def __init__(self, name="", value=None, row=None, col=1):
        self.Name, self.Value = name, value
        self.row, self.col = row, col
        self.children = {}
        self.Elements = Children(self)
        self.writes = []
        self.si = value
        self.write_error = self.read_error = None
        self.read_override = None

    def AttributeValue(self, attr):
        if attr == 2:
            return self.row
        if attr == 3:
            return self.col
        raise ValueError(f"未模拟属性 {attr}")

    def SetValue(self, index, value):
        self.writes.append(("raw", value))
        if self.write_error:
            raise RuntimeError(self.write_error)
        self.Value = self.si = value

    def SetValueAndUnit(self, value, col):
        self.writes.append((col, value))
        if self.write_error:
            raise RuntimeError(self.write_error)
        self.Value = value
        if self.row == 22 and col == 4:
            self.si = value + 273.15
        elif self.row == 20 and col == 5:
            self.si = value * 100000
        elif self.row == 11 and col == 3:
            self.si = value / 3600
        else:
            self.si = value

    def ValueForUnit(self, row, col):
        if self.read_error:
            raise RuntimeError(self.read_error)
        return self.si if self.read_override is None else self.read_override

    def FindNode(self, path):
        node = self
        for part in path.replace("/", "\\").strip("\\").split("\\"):
            node = node.children.get(part)
            if node is None:
                return None
        return node

    def mount(self, path, node):
        parts = path.strip("\\").split("\\")
        parent = self
        for part in parts[:-1]:
            parent = parent.children.setdefault(part, Node(part))
        node.Name = parts[-1]
        parent.children[parts[-1]] = node
        return node


@pytest.fixture
def fake_windows(monkeypatch):
    tree = Node()
    bridge = object.__new__(windows.WindowsBridge)
    bridge._doc = SimpleNamespace(RootModel=lambda _: tree)
    bridge.call = lambda fn: fn()
    for name in ("blocks", "paths", "streams"):
        module = importlib.import_module(f"aspen_mcp.tools.{name}")
        monkeypatch.setattr(module, "aspen", bridge)
    return bridge, tree


@pytest.fixture
def node_class():
    return Node
