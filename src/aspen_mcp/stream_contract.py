"""流股纯 Python 契约；未验证的原生语义一律禁止写入。

这里不提供能力注册工具。仅可信的内部适配器可持有经验证契约；
离线 fixture 的能力不能改变生产能力矩阵。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType

from .tools.units import convert_by_name, finite_number

BASES = ("MOLE-FRAC", "MASS-FRAC", "MOLE-FLOW", "MASS-FLOW")
TOTAL_BASES = ("MOLE-FLOW", "MASS-FLOW")
_NATIVE_CAPABILITIES = MappingProxyType({(b, t): "unverified" for b in BASES for t in TOTAL_BASES})
_FLOW_UNITS = {"MOLE-FLOW": "KMOL/SEC", "MASS-FLOW": "KG/SEC"}


@dataclass(frozen=True)
class _VerifiedCapability:
    evidence: str
    basis: str
    total_basis: str


def capability_matrix() -> list[dict]:
    return [{"basis": b, "total_basis": t, "status": status}
            for (b, t), status in _NATIVE_CAPABILITIES.items()]


def normalize_basis(basis: str) -> str:
    if not isinstance(basis, str):
        raise ValueError("basis 必须是字符串")
    name = basis.strip().upper().replace("-", "").replace("_", "")
    aliases = {"MOLE": "MOLE-FLOW", "MASS": "MASS-FLOW",
               "MOLEFRACTION": "MOLE-FRAC", "MASSFRACTION": "MASS-FRAC"}
    aliases.update({b.replace("-", ""): b for b in BASES})
    if name not in aliases:
        raise ValueError(f"Invalid basis: {basis!r}")
    return aliases[name]


def _result(stream, scope, status="invalid", error=None):
    return {"ok": False, "status": status, "stream": stream, "scope": scope,
            "error": error, "attempted": [], "possibly_changed": [],
            "not_attempted": [], "write": {}, "verify": {}}


def _plan(components, basis, total_flow, unit, total_basis, total_unit, *, partial=False):
    basis = normalize_basis(basis)
    total_basis = normalize_basis(total_basis or basis.split("-")[0] + "-FLOW")
    if total_basis not in TOTAL_BASES:
        raise ValueError("total_basis 必须是 MOLE-FLOW 或 MASS-FLOW")
    if not isinstance(components, dict) or not components:
        raise ValueError("组成必须为非空完整向量")
    if any(not isinstance(c, str) or not c.strip() or c != c.strip() for c in components):
        raise ValueError("组分 ID 必须是明确、无首尾空白的名称")
    frac = basis.endswith("FRAC")
    values = {c: finite_number(v) for c, v in components.items()}
    if any(v < 0 for v in values.values()):
        raise ValueError("组分不得为负")
    if frac and partial and unit is not None:
        raise ValueError("单项 fraction 无量纲，不得携带 unit")
    if frac and unit is not None and total_unit is not None:
        raise ValueError("fraction 的 unit 与 total_unit 同时给出，存在歧义；只保留 total_unit")
    if not frac:
        values = {c: convert_by_name(v, unit, _FLOW_UNITS[basis]) for c, v in values.items()}
    summed = finite_number(math.fsum(values.values()))
    if frac and any(v > 1 + 1e-6 for v in values.values()):
        raise ValueError("fraction 不得大于 1")
    if not partial:
        if frac and abs(summed - 1.0) > 1e-6:
            raise ValueError("fraction 闭合误差超过 1e-6；不自动归一化")
        if not frac and summed <= 0:
            raise ValueError("feed 总流量必须为正")
        if frac and total_flow is None:
            raise ValueError("fraction 完整配置必须给出 total_flow")
    if total_unit is not None and total_flow is None:
        raise ValueError("total_unit 必须与 total_flow 配合")
    effective_total_unit = total_unit if total_unit is not None else unit
    total = None
    if total_flow is not None:
        total = convert_by_name(total_flow, effective_total_unit, _FLOW_UNITS[total_basis])
        if total <= 0:
            raise ValueError("feed total_flow 必须为正")
    if not partial and not frac:
        if total_basis == basis:
            if total is not None and not math.isclose(total, summed, rel_tol=1e-6, abs_tol=1e-9):
                raise ValueError("flow 向量求和与 total_flow 不符，不得再次乘总量")
            total = summed
        elif total is None:
            raise ValueError("跨量纲总流量必须显式提供，禁止猜测分子量")
    return {"basis": basis, "total_basis": total_basis, "components": values,
            "total": total, "write_total": frac and total is not None,
            "partial": partial}


def _write_step(adapter, report, key, writer, reader, expected, *, basis=False):
    report["attempted"].append(key)
    report["possibly_changed"].append(key)
    report["not_attempted"].remove(key)
    report["write"][key] = {"status": "failed"}
    report["verify"][key] = {"status": "not_attempted"}
    try:
        result = writer()
        if result is not None and result is not True and result != "OK":
            raise ValueError(f"写入未确认: {result!r}")
        report["write"][key]["status"] = "ok"
    except Exception as exc:
        report["write"][key]["error"] = f"{type(exc).__name__}: {exc}"
        return False
    return _verify_step(report, key, reader, expected, basis=basis)


def _verify_step(report, key, reader, expected, *, basis=False):
    verification = {"status": "failed", "expected": expected,
                    "read_source": f"adapter.read_{'basis' if basis else 'total' if key == 'total' else 'component'}"}
    report["verify"][key] = verification
    try:
        observed = reader()
        if not isinstance(observed, dict) or not observed.get("read_source"):
            raise ValueError("适配器必须返回真实 read_source")
        verification["read_source"] = observed["read_source"]
        if observed.get("ok") is False or observed.get("error"):
            raise ValueError(f"读取失败: {observed.get('error')}")
        if basis:
            ok = (observed.get("basis"), observed.get("total_basis")) == expected
        else:
            value = finite_number(observed.get("value"))
            if value < 0:
                raise ValueError("读回不得为负")
            verification["observed"] = value
            ok = math.isclose(value, expected, rel_tol=1e-6, abs_tol=1e-9)
        verification["status"] = "ok" if ok else "failed"
        if not ok:
            verification["error"] = "读回不符"
        return ok
    except Exception as exc:
        verification.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        return False


def _execute(adapter, stream, plan, report):
    try:
        ids = tuple(adapter.component_ids(stream))
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("无法确定完整组分集合")
        requested = set(plan["components"])
        if (not plan["partial"] and requested != set(ids)) or not requested.issubset(ids):
            raise ValueError(f"完整组分不符: missing={sorted(set(ids) - requested)}, extra={sorted(requested - set(ids))}")
        adapter.preflight(stream, plan)
        if plan["partial"]:
            current = adapter.read_basis(stream)
            if (current.get("basis"), current.get("total_basis")) != (plan["basis"], plan["total_basis"]):
                raise ValueError("部分更新不得改变基准并隐式继承其余组分，请提交完整向量")
        report["affected_components"] = list(ids)
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        return report
    report["status"] = "partial_failure"
    basis = plan["basis"]
    total_basis = plan["total_basis"]
    if not _write_step(adapter, report, "basis",
                       lambda: adapter.write_basis(stream, basis, total_basis),
                       lambda: adapter.read_basis(stream), (basis, total_basis), basis=True):
        return report
    if plan["write_total"]:
        if not _write_step(adapter, report, "total", lambda: adapter.write_total(stream, plan["total"]),
                           lambda: adapter.read_total(stream), plan["total"]):
            return report
    for comp, value in plan["components"].items():
        if not _write_step(adapter, report, "component:" + comp,
                           lambda: adapter.write_component(stream, comp, value),
                           lambda: adapter.read_component(stream, comp), value):
            return report
    if not plan["partial"]:
        if not _verify_step(report, "basis_final", lambda: adapter.read_basis(stream),
                            (basis, total_basis), basis=True):
            return report
        for comp, value in plan["components"].items():
            if not _verify_step(report, "component_final:" + comp,
                                lambda: adapter.read_component(stream, comp), value):
                return report
        if not _verify_step(report, "total", lambda: adapter.read_total(stream), plan["total"]):
            return report
    report.update(ok=True, status="ok")
    return report


def _stream_update(bridge, stream, components, basis="MOLE-FLOW", total_flow=None,
                   unit=None, *, total_basis=None, total_unit=None, partial=False):
    report = _result(stream, "partial" if partial else "complete")
    try:
        normalized = normalize_basis(basis)
        if total_basis is None:
            total_basis = normalized.split("-")[0] + "-FLOW"
        total_basis = normalize_basis(total_basis)
        if total_basis not in TOTAL_BASES:
            raise ValueError("total_basis 必须是流量基准")
        adapter = getattr(bridge, "_stream_adapter", None)
        capability = None if adapter is None else adapter.capabilities.get((normalized, total_basis))
        if (not isinstance(capability, _VerifiedCapability) or not capability.evidence
                or (capability.basis, capability.total_basis) != (normalized, total_basis)):
            report.update(status="unsupported", error="原生组合 unverified，禁止写入；需要独立验证的内部适配器",
                          basis=normalized, total_basis=total_basis)
            return report
        report["capability_evidence"] = capability.evidence
        plan = _plan(components, normalized, total_flow, unit, total_basis, total_unit, partial=partial)
        report.update(basis=normalized, total_basis=total_basis)
        report["not_attempted"] = (["basis"] + (["total"] if plan["write_total"] else [])
                                   + ["component:" + c for c in plan["components"]])
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        return report
    # 所有写与校验异常在内部被转为结果，避免外层桥将写操作重放。
    return bridge.call(lambda: _execute(adapter, stream, plan, report))


def _resolve_legacy(legacy, *, basis=None, unit=None):
    """只按不相交的已知 token 分类；重复、未知和歧义一律拒绝。"""
    from .tools.units import _CONVERSIONS, _norm
    if len(legacy) > 2:
        raise ValueError("旧位置参数最多两个，请显式使用 basis= 和 unit=")
    known_units = {u for u, _ in _CONVERSIONS}
    for token in legacy:
        if token is None:
            continue
        try:
            candidate_basis = normalize_basis(token)
        except ValueError:
            candidate_basis = None
        is_unit = isinstance(token, str) and _norm(token) in known_units
        if bool(candidate_basis) == is_unit:
            raise ValueError("basis/unit 歧义或未知旧位置参数")
        if candidate_basis:
            if basis is not None:
                raise ValueError("basis 重复，必须使用关键字消歧")
            basis = candidate_basis
        else:
            if unit is not None:
                raise ValueError("unit 重复，必须使用关键字消歧")
            unit = token
    if basis is not None:
        basis = normalize_basis(basis)
    if unit is not None and (not isinstance(unit, str) or _norm(unit) not in known_units):
        raise ValueError("未知 unit 或把 basis 放在 unit 参数中")
    return basis, unit


def _stream_param(bridge, stream_name, param, value, legacy=(), *, basis=None, unit=None, total_basis=None):
    try:
        basis, unit = _resolve_legacy(legacy, basis=basis, unit=unit)
        if param not in ("TEMP", "PRES", "VFRAC", "TOTAL", "TOTFLOW"):
            return _stream_update(bridge, stream_name, {param: value}, basis=basis or "MOLE-FLOW",
                                  unit=unit, total_basis=total_basis, partial=True)
        if param in ("TOTAL", "TOTFLOW"):
            return _result(stream_name, "partial", status="unsupported",
                           error="未验证 TOTAL/TOTFLOW 语义；请使用独立 total_basis 的完整组成接口")
        if basis is not None or total_basis is not None:
            raise ValueError("TEMP/PRES/VFRAC 不接受组成 basis 或 total_basis")
        value = finite_number(value)
    except Exception as exc:
        return _result(stream_name, "partial", error=f"{type(exc).__name__}: {exc}")

    def impl():
        from .tools.units import write_node_value
        tree = bridge._app.RootModel("")
        path = f"\\Data\\Streams\\{stream_name}\\Input\\{param}"
        node = tree.FindNode(path + "\\MIXED")
        if node is None:
            node = tree.FindNode(path)
        report = write_node_value(node, value, user_unit=unit,
                                  base_internal={"TEMP": "K", "PRES": "N/SQM", "VFRAC": "1"}[param])
        report.update(stream=stream_name, scope="partial", target=param,
                      status="ok" if report["ok"] else "failed")
        return report
    return bridge.call(impl)


def _stream_component(bridge, stream_name, component, flow, legacy=(), *, unit=None, basis=None, total_basis=None):
    try:
        basis, unit = _resolve_legacy(legacy, basis=basis, unit=unit)
    except Exception as exc:
        return _result(stream_name, "partial", error=f"{type(exc).__name__}: {exc}")
    return _stream_update(bridge, stream_name, {component: flow}, basis=basis or "MOLE-FLOW",
                          unit=unit, total_basis=total_basis, partial=True)
