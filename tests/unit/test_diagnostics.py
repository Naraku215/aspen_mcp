"""只读诊断（离线）：路径等价、错误分级、模式核验、Ports 连接与受限 fill。

验收：等价路径一致；fail/unknown 不晋级；诊断零写；fill 默认预览。
"""
import pytest


class _Unreadable:
    """Value 读取抛异常的模拟节点（必要属性不可读）。"""

    def __init__(self, name):
        self.Name = name
        self.children = {}

    @property
    def Value(self):
        raise RuntimeError("attribute unreadable")

    def Elements(self, key):
        return self.children.get(key)


class _StreamRef:
    def __init__(self, name):
        self.Name = name


class _Collection:
    """模拟 COM 端口集合（Count/Item，而非 IHNode 子节点）。"""

    def __init__(self, items):
        self._items = items

    @property
    def Count(self):
        return len(self._items)

    def Item(self, index):
        return self._items[index]


class _Port:
    def __init__(self, name, stream_names):
        self.Name = name
        self.Elements = _Collection([_StreamRef(s) for s in stream_names])


# ── 路径规范化与等价性 ────────────────────────────────────────────────────

def test_normalize_path_equivalent_forms():
    from aspen_mcp.tools._common import normalize_path, split_path

    for raw in (r"Data\Blocks\B1", "\\Data\\Blocks\\B1\\", "Data/Blocks/B1/",
                "/Data//Blocks/B1"):
        assert normalize_path(raw) == r"Data\Blocks\B1"
        assert split_path(raw) == ["Data", "Blocks", "B1"]


def test_normalize_path_rejects_non_string():
    from aspen_mcp.tools._common import normalize_path

    with pytest.raises(ValueError):
        normalize_path(None)


def test_explore_equivalent_paths_identical(fake_windows, node_class, monkeypatch):
    from aspen_mcp.tools import blocks as blocks_module

    bridge, tree = fake_windows
    monkeypatch.setattr(blocks_module, "aspen", bridge)
    tree.mount(r"Data\Blocks\B1", node_class("B1"))
    forms = (r"Data\Blocks\B1", "\\Data\\Blocks\\B1\\", "Data/Blocks/B1/",
             "/Data//Blocks/B1")
    results = [blocks_module.tool_explore(p) for p in forms]
    assert all("error" not in r for r in results), results
    assert all(r == results[0] for r in results)


def test_explore_classifies_missing_node(fake_windows, monkeypatch):
    from aspen_mcp.tools import blocks as blocks_module

    bridge, _tree = fake_windows
    monkeypatch.setattr(blocks_module, "aspen", bridge)
    result = blocks_module.tool_explore(r"Data\Blocks\NOPE")
    assert result["kind"] == "node_missing"
    assert "not found" in result["error"]


def test_explore_classifies_format_error(fake_windows, monkeypatch):
    from aspen_mcp.tools import blocks as blocks_module

    bridge, _tree = fake_windows
    monkeypatch.setattr(blocks_module, "aspen", bridge)
    assert blocks_module.tool_explore(None)["kind"] == "format_error"


def test_get_value_equivalent_paths(fake_windows, node_class):
    from aspen_mcp.tools import paths as paths_module

    _bridge, tree = fake_windows
    tree.mount(r"Data\Streams\S1", node_class("S1", value=42))
    for raw in (r"Data\Streams\S1", "Data/Streams/S1/", r"\Data\Streams\S1"):
        assert paths_module.tool_get_value(raw) == "42"


# ── 模式/必需参数核验：fail/unknown 不晋级 ────────────────────────────────

def _mount_heater(tree, node_class, name, mode, params):
    blk = tree.mount(rf"Data\Blocks\{name}", node_class(name, value="HEATER"))
    inp = blk.mount("Input", node_class("Input"))
    inp.mount("SPEC_OPT", node_class("SPEC_OPT", value=mode))
    for pname, value in params.items():
        inp.mount(pname, node_class(pname, value=value))
    return blk


def test_validate_mode_pass_when_complete(fake_windows, node_class, monkeypatch):
    from aspen_mcp.tools import analysis

    bridge, tree = fake_windows
    monkeypatch.setattr(analysis, "aspen", bridge)
    _mount_heater(tree, node_class, "H1", "TP", {"TEMP": 300.0, "PRES": 101325.0})
    out = analysis.tool_validate_block("H1")
    assert "Mode check: PASS" in out


def test_validate_mode_fail_when_required_missing(fake_windows, node_class, monkeypatch):
    from aspen_mcp.tools import analysis

    bridge, tree = fake_windows
    monkeypatch.setattr(analysis, "aspen", bridge)
    _mount_heater(tree, node_class, "H1", "TP", {"TEMP": 300.0})
    out = analysis.tool_validate_block("H1")
    assert "Mode check: FAIL" in out
    assert "PRES" in out


def test_validate_mode_unknown_for_unknown_mode(fake_windows, node_class, monkeypatch):
    from aspen_mcp.tools import analysis

    bridge, tree = fake_windows
    monkeypatch.setattr(analysis, "aspen", bridge)
    _mount_heater(tree, node_class, "H1", "BOGUS", {"TEMP": 300.0, "PRES": 101325.0})
    out = analysis.tool_validate_block("H1")
    assert "Mode check: UNKNOWN" in out


def test_validate_mode_error_value_counts_as_missing(fake_windows, node_class, monkeypatch):
    from aspen_mcp.tools import analysis

    bridge, tree = fake_windows
    monkeypatch.setattr(analysis, "aspen", bridge)
    _mount_heater(tree, node_class, "H1", "TP",
                  {"TEMP": "#Error: unset", "PRES": 101325.0})
    out = analysis.tool_validate_block("H1")
    assert "Mode check: FAIL" in out
    assert "TEMP" in out


def test_validate_mode_unreadable_is_unknown(fake_windows, node_class, monkeypatch):
    from aspen_mcp.tools import analysis

    bridge, tree = fake_windows
    monkeypatch.setattr(analysis, "aspen", bridge)
    _mount_heater(tree, node_class, "H1", "TP", {"TEMP": 300.0})
    inp = tree.FindNode(r"Data\Blocks\H1\Input")
    inp.children["PRES"] = _Unreadable("PRES")
    out = analysis.tool_validate_block("H1")
    assert "Mode check: UNKNOWN" in out


def test_validate_icon2_requires_seff(fake_windows, node_class, monkeypatch):
    from aspen_mcp.tools import analysis

    bridge, tree = fake_windows
    monkeypatch.setattr(analysis, "aspen", bridge)
    blk = tree.mount(r"Data\Blocks\C1", node_class("C1", value="ICON2"))
    inp = blk.mount("Input", node_class("Input"))
    inp.mount("OPT_SPEC", node_class("OPT_SPEC", value="PRES"))
    inp.mount("PRES", node_class("PRES", value=2e5))
    out = analysis.tool_validate_block("C1")
    assert "Mode check: FAIL" in out
    assert "SEFF" in out


# ── Ports 连接索引：Output 为空不误报 ─────────────────────────────────────

def test_warnings_connected_port_suppresses_false_positive(fake_windows, node_class,
                                                           monkeypatch):
    from aspen_mcp.tools import analysis

    bridge, tree = fake_windows
    monkeypatch.setattr(analysis, "aspen", bridge)
    blk = tree.mount(r"Data\Blocks\B1", node_class("B1", value="HEATER"))
    ports = blk.mount("Ports", node_class("Ports"))
    ports.children["F(IN)"] = _Port("F(IN)", ["FEED"])
    feed = tree.mount(r"Data\Streams\FEED", node_class("FEED"))
    finp = feed.mount("Input", node_class("Input"))
    ftemp = finp.mount("TEMP", node_class("TEMP"))
    ftemp.mount("MIXED", node_class("MIXED", value=300.0))

    warnings = analysis.tool_simulation_warnings()
    assert not any("FEED" in w for w in warnings), warnings
    assert all(not n.writes for n in (blk, ports, feed, finp, ftemp))


def test_warnings_unconnected_stream_still_reported(fake_windows, node_class,
                                                    monkeypatch):
    from aspen_mcp.tools import analysis

    bridge, tree = fake_windows
    monkeypatch.setattr(analysis, "aspen", bridge)
    orphan = tree.mount(r"Data\Streams\ORPHAN", node_class("ORPHAN"))
    oinp = orphan.mount("Input", node_class("Input"))
    otemp = oinp.mount("TEMP", node_class("TEMP"))
    otemp.mount("MIXED", node_class("MIXED", value=300.0))

    warnings = analysis.tool_simulation_warnings()
    assert any("ORPHAN" in w for w in warnings), warnings


# ── fill 受限：默认预览零写 ───────────────────────────────────────────────

def _mount_trivial_target(tree, node_class, block_name, param_name):
    class _FillableNode(node_class):
        ValueType = 0

        def AttributeValue(self, attr):
            if attr == 7:
                return 1
            if attr == 11:
                return 0
            return super().AttributeValue(attr)

    blk = tree.mount(rf"Data\Blocks\{block_name}", node_class(block_name))
    inp = blk.mount("Input", node_class("Input"))
    return inp.mount(param_name, _FillableNode(param_name, value=None))


def test_fill_default_preview_is_zero_write(fake_windows, node_class, monkeypatch):
    from aspen_mcp.tools import fix_trivial

    bridge, tree = fake_windows
    monkeypatch.setattr(fix_trivial, "aspen", bridge)
    target = _mount_trivial_target(tree, node_class, "B1", "PSVBY")
    result = fix_trivial.tool_fill_trivial_params()
    assert target.writes == []
    assert "preview" in result.lower()


def test_fill_without_scope_refuses_write(fake_windows, node_class, monkeypatch):
    from aspen_mcp.tools import fix_trivial

    bridge, tree = fake_windows
    monkeypatch.setattr(fix_trivial, "aspen", bridge)
    target = _mount_trivial_target(tree, node_class, "B1", "PSVBY")
    result = fix_trivial.tool_fill_trivial_params(dry_run=False)
    assert "Error" in result
    assert target.writes == []


def test_fill_with_scopes_writes(fake_windows, node_class, monkeypatch):
    from aspen_mcp.tools import fix_trivial

    bridge, tree = fake_windows
    monkeypatch.setattr(fix_trivial, "aspen", bridge)
    target = _mount_trivial_target(tree, node_class, "B1", "PSVBY")
    result = fix_trivial.tool_fill_trivial_params(
        objects=["B1"], params=["PSVBY"], dry_run=False)
    assert target.writes, result
    assert "preview" not in result.lower()


# ── 导出回执：产物存在性核验，静默失败不谎报 ────────────────────────────────

def test_generate_input_summary_reports_missing_product(monkeypatch, tmp_path):
    from aspen_mcp.tools import analysis

    class FakeAspen:
        def generate_input_summary(self, path):
            pass  # 静默失败：不抛异常也不产出

    monkeypatch.setattr(analysis, "aspen", FakeAspen())
    target = str(tmp_path / "sub" / "out.bkp")
    result = analysis.tool_generate_input_summary(target)
    assert result.startswith("Error: export returned without error")
    assert not (tmp_path / "sub" / "out.bkp").exists()


def test_generate_input_summary_creates_parent_and_reports_size(monkeypatch, tmp_path):
    from aspen_mcp.tools import analysis

    class FakeAspen:
        def generate_input_summary(self, path):
            with open(path, "wb") as f:
                f.write(b"x" * 10)

    monkeypatch.setattr(analysis, "aspen", FakeAspen())
    target = str(tmp_path / "sub" / "out.bkp")
    result = analysis.tool_generate_input_summary(target)
    assert result.endswith("(10 bytes)")
    assert (tmp_path / "sub" / "out.bkp").exists()


def test_export_report_file_reports_missing_product(monkeypatch, tmp_path):
    from aspen_mcp.tools import analysis

    class FakeEngine:
        def ExportReport(self, path, code):
            pass  # 静默失败：不抛异常也不产出

    class FakeApp:
        Engine = FakeEngine()

    class FakeAspen:
        _app = FakeApp()

        def call(self, fn):
            return fn()

    monkeypatch.setattr(analysis, "aspen", FakeAspen())
    target = str(tmp_path / "sub" / "report.txt")
    result = analysis.tool_export_report_file(target)
    assert result.startswith("Error: export returned without error")
    assert not (tmp_path / "sub" / "report.txt").exists()


def test_export_report_file_creates_parent_and_reports_size(monkeypatch, tmp_path):
    from aspen_mcp.tools import analysis

    class FakeEngine:
        def ExportReport(self, path, code):
            with open(path, "wb") as f:
                f.write(b"x" * 10)

    class FakeApp:
        Engine = FakeEngine()

    class FakeAspen:
        _app = FakeApp()

        def call(self, fn):
            return fn()

    monkeypatch.setattr(analysis, "aspen", FakeAspen())
    target = str(tmp_path / "sub" / "report.txt")
    result = analysis.tool_export_report_file(target)
    assert result.endswith("(10 bytes)")
    assert (tmp_path / "sub" / "report.txt").exists()


# ── 撕裂流估计：写被拒绝必须显式报错，不得谎报成功 ──────────────────────────

def test_tear_estimate_reports_rejected_total_write(fake_windows, monkeypatch):
    from aspen_mcp.tools import analysis

    bridge, _tree = fake_windows
    calls = []

    def record_call(fn):
        calls.append(fn)
        return fn()

    bridge.call = record_call
    monkeypatch.setattr(analysis, "aspen", bridge)
    result = analysis.tool_set_tear_estimate("FEED", total_flow=100.0)
    assert result.startswith("Error"), result
    assert "TOTAL" in result
    assert calls == []  # 守卫在进入 COM 之前拒绝


def test_tear_estimate_still_reports_writable_param(fake_windows, node_class, monkeypatch):
    from aspen_mcp.tools import analysis

    bridge, tree = fake_windows
    monkeypatch.setattr(analysis, "aspen", bridge)
    tree.mount(r"\Data\Streams\FEED\Input\TEMP\MIXED", node_class("MIXED", row=22))
    result = analysis.tool_set_tear_estimate("FEED", temp=150.0)
    assert result.startswith("Tear stream 'FEED' estimate set")
    assert "TEMP=150" in result


# ── 枚举健壮性：Count 优先、探测回退、异常标记 UNKNOWN ────────────────────

class _ProbeCollection:
    """模拟 Elements 集合：Count 可开关；越界异常类型可注入。"""

    def __init__(self, items, *, expose_count, overflow):
        self._items = items
        self._expose_count = expose_count
        self._overflow = overflow
        self.probed = []

    @property
    def Count(self):
        if not self._expose_count:
            raise AttributeError("Count")
        return len(self._items)

    def __call__(self, key):
        if not isinstance(key, int):
            return {item.Name: item for item in self._items}.get(key)
        self.probed.append(key)
        if key >= len(self._items):
            raise self._overflow(f"index {key} out of range")
        return self._items[key]


def _mount_streams_with_probe(tree, node_class, *, expose_count, overflow):
    tree.mount(r"Data\Streams\S1", node_class("S1"))
    tree.mount(r"Data\Streams\S2", node_class("S2"))
    streams = tree.FindNode(r"Data\Streams")
    collection = _ProbeCollection(list(streams.children.values()),
                                  expose_count=expose_count, overflow=overflow)
    streams.Elements = collection
    return collection


def test_warnings_count_first_avoids_overflow_probe(fake_windows, node_class, monkeypatch):
    from aspen_mcp.tools import analysis

    bridge, tree = fake_windows
    monkeypatch.setattr(analysis, "aspen", bridge)
    collection = _mount_streams_with_probe(tree, node_class,
                                           expose_count=True, overflow=RuntimeError)

    warnings = analysis.tool_simulation_warnings()

    assert not any(w.startswith("UNKNOWN") for w in warnings), warnings
    assert 2 not in collection.probed  # Count 已告知边界，无需触达越界异常


def test_warnings_without_count_probes_until_index_error(fake_windows, node_class,
                                                         monkeypatch):
    from aspen_mcp.tools import analysis

    bridge, tree = fake_windows
    monkeypatch.setattr(analysis, "aspen", bridge)
    collection = _mount_streams_with_probe(tree, node_class,
                                           expose_count=False, overflow=IndexError)

    warnings = analysis.tool_simulation_warnings()

    assert not any(w.startswith("UNKNOWN") for w in warnings), warnings
    assert collection.probed == [0, 1, 2]  # 越界即正常结束


def test_warnings_enumeration_error_marks_unknown(fake_windows, node_class, monkeypatch):
    from aspen_mcp.tools import analysis

    bridge, tree = fake_windows
    monkeypatch.setattr(analysis, "aspen", bridge)
    _mount_streams_with_probe(tree, node_class,
                              expose_count=False, overflow=RuntimeError)

    warnings = analysis.tool_simulation_warnings()

    assert any(w.startswith("UNKNOWN") for w in warnings), warnings