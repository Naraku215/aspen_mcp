"""Unit conversion helpers — native Aspen COM unit API, Python-table fallback.

FACTS (established by the 2026-09-02 AttributeValue experiment matrix on
Aspen Plus V15 41.0):

  - ``node.Value`` is the LAST ENTERED raw number. It does NOT track the
    unit set. Reading ``Value`` + ``UnitString`` can silently mismatch
    after any unit-set switch (root cause of the historic ENG bugs).
  - Every numeric node carries a quantity-dimension id ``row``
    (``AttributeValue(2)``) and a unit id ``col`` (``AttributeValue(3)``).
  - ``node.SetValueAndUnit(value, col)`` interprets *value* in the unit
    denoted by *col* and normalises internally - native, exact, no python
    conversion table needed.
  - ``node.ValueForUnit(row, col)`` returns the value expressed in the
    absolute unit (*row*, *col*) - the reliable way to READ.
  - The SI column of the major quantity rows is ``col = 1``.

Strategy:
  - WRITE: if the user unit has a known (row, col) coordinate, use
    ``SetValueAndUnit(value, col)`` directly (Aspen converts). Otherwise
    fall back to the Python conversion table (to SI) and write with the
    SI column.
  - READ: use ``ValueForUnit(row, si_col)`` when the row is known;
    otherwise fall back to ``node.Value``.
"""
from __future__ import annotations

import json
import math
from typing import Any


def _norm(s: str) -> str:
    """Normalize a unit label for matching: uppercase, strip, collapse spaces."""
    return s.upper().strip().replace(" ", "")


# ── Native unit coordinates: normalized unit label -> (row, col) ──────────
# Sampled 2026-09-02 with a probe stream/block + SetValueAndUnit/ValueForUnit
# column scan (rows 0..47). Only Aspen-native units that also exist in
# _CONVERSIONS are listed; anything else falls back to the Python table.
_UNIT_COORDS: dict[str, tuple[int, int]] = {
    # temperature (row 22): K col1, F col2, C col4, R col5
    "K": (22, 1),
    "C": (22, 4), "CELSIUS": (22, 4), "DEGC": (22, 4),
    "F": (22, 2), "FAHRENHEIT": (22, 2), "DEGF": (22, 2),
    "RANKINE": (22, 5),
    # pressure (row 20): N/sqm col1, psia col2, atm col3, bar col5,
    # mmHg/torr col6, kPa col10, mbar col12, MPa col20
    "N/SQM": (20, 1), "PA": (20, 1),
    "PSIA": (20, 2), "PSI": (20, 2),
    "ATM": (20, 3),
    "BAR": (20, 5), "BARA": (20, 5),
    "MMHG": (20, 6), "TORR": (20, 6),
    "KPA": (20, 10), "MBAR": (20, 12), "MPA": (20, 20),
    # molar flow (row 11): kmol/sec col1, lbmol/hr col2, kmol/hr col3, mol/hr col15
    "KMOL/SEC": (11, 1), "KMOL/S": (11, 1),
    "LBMOL/HR": (11, 2),
    "KMOL/HR": (11, 3), "KMOL/H": (11, 3),
    "MOL/HR": (11, 15),
    # energy (row 13): W col1, Btu/hr col2, kcal/hr col6, kW col13,
    # MW col15, kJ/hr col17
    "W": (13, 1),
    "BTU/HR": (13, 2),
    "KCAL/HR": (13, 6),
    "KW": (13, 13),
    "MW": (13, 15),
    "KJ/HR": (13, 17),
}

# ── SI column per quantity row (all sampled rows use col 1) ───────────────
_ROW_SI_COL: dict[int, int] = {
    11: 1,   # molar flow   -> kmol/sec
    13: 1,   # energy       -> Watt
    20: 1,   # pressure     -> N/sqm
    22: 1,   # temperature  -> K
}

# ── Python fallback conversions: (user_unit_norm, si_unit_norm) -> fn ──────
_CONVERSIONS: dict[tuple[str, str], Any] = {
    # Temperature (SI base = K)
    ("C", "K"):          lambda v: v + 273.15,
    ("CELSIUS", "K"):    lambda v: v + 273.15,
    ("F", "K"):          lambda v: (v - 32.0) * 5.0 / 9.0 + 273.15,
    ("FAHRENHEIT", "K"): lambda v: (v - 32.0) * 5.0 / 9.0 + 273.15,
    ("K", "K"):          lambda v: float(v),
    # Pressure (SI base = N/sqm = Pa)
    ("BAR", "N/SQM"):       lambda v: v * 1.0e5,
    ("ATM", "N/SQM"):       lambda v: v * 101325.0,
    ("PSI", "N/SQM"):       lambda v: v * 6894.757293168,
    ("PSIA", "N/SQM"):      lambda v: v * 6894.757293168,
    ("KPA", "N/SQM"):       lambda v: v * 1000.0,
    ("MPA", "N/SQM"):       lambda v: v * 1.0e6,
    ("PA", "N/SQM"):        lambda v: float(v),
    ("N/SQM", "N/SQM"):     lambda v: float(v),
    ("N/SQ.M", "N/SQM"):    lambda v: float(v),
    # Molar flow (SI base = kmol/sec)
    ("KMOL/HR", "KMOL/SEC"):  lambda v: v / 3600.0,
    ("KMOL/S", "KMOL/SEC"):   lambda v: float(v),
    ("KMOL/SEC", "KMOL/SEC"): lambda v: float(v),
    ("MOL/HR", "KMOL/SEC"):   lambda v: v / 3_600_000.0,
    ("LBMOL/HR", "KMOL/SEC"): lambda v: v * 0.45359237 / 3600.0,
    # Mass flow (SI base = kg/sec; python-only, no native node row)
    ("KG/HR", "KG/SEC"):  lambda v: v / 3600.0,
    ("KG/S", "KG/SEC"):   lambda v: float(v),
    ("KG/SEC", "KG/SEC"): lambda v: float(v),
    ("LB/HR", "KG/SEC"):  lambda v: v * 0.45359237 / 3600.0,
    # Energy / duty (SI base = W)
    ("KW", "W"):    lambda v: v * 1000.0,
    ("MW", "W"):    lambda v: v * 1.0e6,
    ("KJ/HR", "W"): lambda v: v / 3.6,
    ("W", "W"):     lambda v: float(v),
    # --- common aliases ---
    ("KMOL/H", "KMOL/SEC"):   lambda v: v / 3600.0,
    ("KG/H", "KG/SEC"):       lambda v: v / 3600.0,
    ("DEGC", "K"):            lambda v: v + 273.15,
    ("DEGF", "K"):            lambda v: (v - 32.0) * 5.0 / 9.0 + 273.15,
    ("RANKINE", "K"):         lambda v: v * 5.0 / 9.0,
    ("MBAR", "N/SQM"):        lambda v: v * 100.0,
    ("MMHG", "N/SQM"):        lambda v: v * 133.322,
    ("TORR", "N/SQM"):        lambda v: v * 133.322,
    ("BARA", "N/SQM"):        lambda v: v * 1.0e5,
    ("T/HR", "KG/SEC"):       lambda v: v * 1000.0 / 3600.0,
    ("HP", "W"):              lambda v: v * 745.7,
    ("BTU/HR", "W"):          lambda v: v * 0.29307107,
    ("KCAL/HR", "W"):         lambda v: v * 1.16222,
}


_ROW_BASE = {11: "KMOL/SEC", 13: "W", 20: "N/SQM", 22: "K"}


def finite_number(value) -> float:
    """统一接受 JSON 数字及带空白/科学计数法的数字字符串。"""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("需要有限数字，禁止 bool")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"非法数字: {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"禁止 NaN/Inf 或溢出: {value!r}")
    return number


def _coordinate(node: Any, attribute: int) -> int:
    try:
        value = node.AttributeValue(attribute)
    except Exception as exc:
        raise ValueError(f"AttributeValue({attribute}) 失败: {exc}") from exc
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value != int(value) or value < 0):
        raise ValueError(f"无效单位坐标 attribute={attribute}: {value!r}")
    return int(value)


def _row(node: Any) -> int:
    """严格读取量纲，不把读取异常解释成无量纲。"""
    return _coordinate(node, 2)


def _si_col(row: int | None) -> int | None:
    return _ROW_SI_COL.get(row)


def _coordinates(node: Any) -> tuple[int, int]:
    if node is None:
        raise ValueError("write_value: node is None")
    row, col = _row(node), _coordinate(node, 3)
    if (row, col) == (0, 0):
        return row, col
    if row not in _ROW_BASE or (row, col) not in _UNIT_COORDS.values():
        raise ValueError(f"unsupported: 未验证的单位坐标 row={row}, col={col}")
    return row, col


def read_si_result(node: Any) -> dict:
    """读取结果保留实际来源和异常，失败后绝不返回原始 Value。"""
    result = {"ok": False, "value": None, "read_source": None, "error": None}
    try:
        result["read_source"] = "unit_metadata"
        row, col = _coordinates(node)
        result.update(row=row, col=col)
        if row == 0:
            result["read_source"] = "Value(dimensionless)"
            value = node.Value
        else:
            result["read_source"] = f"ValueForUnit({row},{_si_col(row)})"
            value = node.ValueForUnit(row, _si_col(row))
        result.update(ok=True, value=finite_number(value))
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def read_si(node: Any):
    """兼容数值返回接口；SI 失败显式抛错而非返回显示值。"""
    if node is None:
        return None
    result = read_si_result(node)
    if not result["ok"]:
        raise ValueError(f"{result['read_source']}: {result['error']}")
    return result["value"]


def _prepare_numeric(node, value, user_unit=None, base_internal=None):
    value = finite_number(value)
    row, _ = _coordinates(node)
    if row == 0:
        if user_unit is not None or base_internal not in (None, "1"):
            raise ValueError("无量纲数值不得携带物理单位")
        return value, None, value
    base = _ROW_BASE[row]
    if base_internal is not None and _norm(base_internal) != base:
        raise ValueError(f"量纲不符: row={row}, base_internal={base_internal}")
    expected = convert_by_name(value, user_unit, base)
    if user_unit is None:
        return value, _si_col(row), expected
    coord = _UNIT_COORDS.get(_norm(user_unit))
    if coord is not None:
        if coord[0] != row:
            raise ValueError(f"单位与量纲不符: {user_unit}, row={row}")
        return value, coord[1], expected
    return expected, _si_col(row), expected


def write_value(node: Any, value, user_unit: str | None = None,
                base_internal: str | None = None) -> None:
    """仅按预验证的单位方案写一次；原生写失败不重试、不裸写。"""
    if node is None:
        raise ValueError("write_value: node is None")
    target, col, _ = _prepare_numeric(node, value, user_unit, base_internal)
    if col is None:
        node.SetValue(0, target)
    else:
        node.SetValueAndUnit(target, col)


def write_node_value(node, value, user_unit=None, base_internal=None) -> dict:
    """写与读回各自报告状态；任何写异常都表示该目标可能已改变。"""
    report = {"ok": False, "write": {"status": "not_attempted"},
              "verify": {"status": "not_attempted"}}
    try:
        if node is None:
            raise ValueError("node is None")
        is_text = isinstance(node.Value, str)
        if is_text:
            if user_unit is not None or not isinstance(value, str):
                raise ValueError("文本/枚举必须保持文本且不得携带 unit")
            target, col, expected = value, None, value
        else:
            target, col, expected = _prepare_numeric(node, value, user_unit, base_internal)
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        return report
    report["write"] = {"status": "failed", "possibly_changed": True,
                       "write_source": "SetValue" if col is None else f"SetValueAndUnit({col})"}
    try:
        if col is None:
            node.SetValue(0, target)
        else:
            node.SetValueAndUnit(target, col)
    except Exception as exc:
        report["write"]["error"] = f"{type(exc).__name__}: {exc}"
        return report
    report["write"]["status"] = "ok"
    if is_text:
        verification = {"ok": False, "read_source": "Value(text)", "value": None}
        try:
            verification["value"] = node.Value
            verification["ok"] = verification["value"] == expected
        except Exception as exc:
            verification["error"] = f"{type(exc).__name__}: {exc}"
    else:
        verification = read_si_result(node)
        if verification["ok"]:
            verification["ok"] = math.isclose(verification["value"], expected,
                                               rel_tol=1e-6, abs_tol=1e-9)
    verification.update(status="ok" if verification["ok"] else "failed", expected=expected)
    report["verify"] = verification
    report["ok"] = verification["ok"]
    return report


def format_write_result(report: dict) -> str:
    return ("OK: " if report["ok"] else "Error: ") + json.dumps(report, ensure_ascii=False, allow_nan=False)


# ── 未验证流股路径守卫 ─────────────────────────────────────────────────
# FLOW/TOTAL/TOTFLOW/BASIS 的原生语义尚未验证（不得猜测 attribute 13、
# TOTAL 或 TOTFLOW），通用路径入口必须在写前拒绝并指向显式契约接口。
_STREAM_GUARDED_SECTIONS = {"FLOW", "TOTAL", "TOTFLOW"}


def guard_stream_path(path: str) -> str | None:
    """受限流股路径检查；返回拒绝原因，None 表示可通过。"""
    if not isinstance(path, str) or not path:
        return None
    parts = [p.strip().upper() for p in path.replace("/", "\\").strip("\\").split("\\") if p.strip()]
    for i, token in enumerate(parts):
        if token != "STREAMS" or i + 2 >= len(parts) or parts[i + 2] != "INPUT":
            continue
        section = parts[i + 3] if i + 3 < len(parts) else ""
        if section in _STREAM_GUARDED_SECTIONS or section.startswith("BASIS"):
            return (f"unsupported: 流股路径 '{section}' 属于未验证的组成/总量契约；"
                    f"必须使用 set_stream_composition_batch 等显式能力接口，禁止通用猜写")
        return None
    return None


def unsupported_report(path: str, reason: str) -> dict:
    """通用入口的未支持报告；保持与写报告同形，不声称已尝试。"""
    return {"ok": False, "status": "unsupported", "error": reason, "target": path,
            "write": {"status": "not_attempted"}, "verify": {"status": "not_attempted"}}


def write_path_value(tree, path, value, unit=None) -> dict:
    """公共路径写入；匹配子名称只用于选择文本节点，不吞掉写异常。"""
    guard = guard_stream_path(path)
    if guard is not None:
        return unsupported_report(path, guard)
    node = tree.FindNode(path)
    if node is not None and isinstance(value, str):
        try:
            count = node.Elements.Count
        except Exception:
            count = 0
        for i in range(count):
            child = node.Elements(i)
            if child is not None and child.Name.upper() == value.upper():
                node = child
                break
    return write_node_value(node, value, user_unit=unit)


def convert_value(value, user_unit: str | None, node: Any = None):
    """Convert a user value to SI using the Python table.

    Kept for backward compatibility (several tools pre-convert before
    delegating to the bridge). ``node`` is ignored now - the conversion no
    longer depends on the node's misleading UnitString.

    unit=None passes through unchanged (assumes the value is already SI).
    Unknown units raise ValueError naming the supported unit labels.
    """
    value = finite_number(value)
    if node is not None:
        row, _ = _coordinates(node)
        if row == 0:
            if user_unit is not None:
                raise ValueError("无量纲数值不得携带物理单位")
            return value
        return convert_by_name(value, user_unit, _ROW_BASE[row])
    if user_unit is None:
        return value
    return _convert_si(value, user_unit)


def convert_by_name(value, user_unit: str | None, internal_unit: str):
    """Convert a value when the SI unit of the quantity is known.

    unit=None passes through. Same-unit pairs pass through (Bug-4 fix).
    """
    value = finite_number(value)
    if user_unit is None:
        return value
    if not isinstance(user_unit, str) or not user_unit.strip():
        raise ValueError("unit 必须是非空字符串")
    sn, tn = _norm(user_unit), _norm(internal_unit)
    if sn == tn:
        return float(value)
    fn = _CONVERSIONS.get((sn, tn))
    if fn is None:
        raise ValueError(
            f"No unit conversion registered for '{user_unit}' -> "
            f"'{internal_unit}'. If the value is already in internal SI "
            f"({internal_unit}), call with unit=None."
        )
    return finite_number(fn(value))


def _convert_si(value, user_unit: str) -> float:
    """Convert to SI, trying each known SI base till a mapping exists."""
    if not isinstance(user_unit, str) or not user_unit.strip():
        raise ValueError("unit 必须是非空字符串")
    sn = _norm(user_unit)
    for target in ("K", "N/SQM", "KMOL/SEC", "KG/SEC", "W"):
        fn = _CONVERSIONS.get((sn, target))
        if fn is not None:
            return fn(value)
    raise ValueError(
        f"No unit conversion registered for '{user_unit}' (exhaustive SI "
        f"bases tried). Check the unit label; supported: "
        f"{sorted({u for (u, _t) in _CONVERSIONS})}"
    )


def describe_node_unit(node: Any) -> str:
    """Human-readable description of a node's unit (for error messages)."""
    try:
        nm = node.Name
    except Exception:
        nm = "?"
    try:
        us = node.AttributeValue(81)  # HAP_UOM
    except Exception:
        us = "?"
    return f"node '{nm}' unit='{us}'"


def format_conversion(orig_value, orig_unit: str | None,
                      conv_value, node_unit: str) -> str:
    """Return a human-readable string showing the conversion."""
    if orig_unit:
        return f"{orig_value} {orig_unit} -> {conv_value} {node_unit}"
    return str(conv_value)
