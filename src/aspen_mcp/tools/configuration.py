"""通用批配置、回执与受控执行（manifest 合同）。

规则：
- 单一 operations 格式；静态检查整批格式、类型、单位维度、闭合、重复 ID、
  冲突目标和依赖环；一次返回全部错误，整批零写入。
- 动态检查分级：局部对象失败仅阻断本对象及依赖项，独立项继续；系统性
  单位失效、身份不明、COM 超时或结果未知停止全部后续写入。
- 线程内读写原语：对象在一次受控执行中按“规格→参数→聚合读回”完成，
  禁止在 COM 回调内调用会再次排队的公共工具。
- 所有写入口共享准入与操作锁（可重入），wrapper 不持锁等待自己排队。
- 回执 write_status：applied/failed/skipped/partial_applied；已进入 COM 但
  无确定结果记 outcome_unknown/pending，不捏造终态。verify_status：
  pass/fail/unknown，写入与验证分开。
- 请求指纹为规范 JSON 的 UTF-8 SHA-256：键排序、依赖排序、保留语义相关
  数组顺序、拒绝非有限数与重复键；不先四舍五入数值。动态时间/session/
  结果不计入 op 语义指纹。
- 同 lineage/op_id 同指纹先核现状再复用；同 ID 不同请求拒绝；人工差异
  列出并暂停，不用旧 state 覆盖。lite state 只保存引用与摘要。

离线 fixture 的能力不代表原生 Aspen 验证；未验证组合在写前明确拒绝。
"""
from __future__ import annotations

import hashlib
import json
import math
import threading

from ..stream_contract import BASES, TOTAL_BASES, normalize_basis, _stream_update, _stream_param
from .units import (_CONVERSIONS, _ROW_BASE, _UNIT_COORDS, _norm,
                    convert_by_name, finite_number, read_si_result, write_path_value)

SCHEMA_VERSION = 1
_OP_KINDS = ("feed", "block")
_VALUE_TYPES = ("number", "enum", "text")
_FEED_PARAMS = ("TEMP", "PRES", "VFRAC")
_SYSTEMIC_MARKERS = ("无效单位坐标", "AttributeValue(", "未验证的单位坐标")
_KNOWN_UNITS = frozenset(_UNIT_COORDS) | frozenset(user for user, _ in _CONVERSIONS)
_ALLOWED_OP_FIELDS = frozenset({"op_id", "kind", "target", "dependencies", "specs", "values",
                                "components", "composition_basis", "total", "total_basis"})


# ── 解析、规范 JSON 与指纹 ──────────────────────────────────────────────

def load_manifest(source):
    """接受 dict 或 JSON 文本；文本解析拒绝重复键。"""
    if isinstance(source, dict):
        return source
    if not isinstance(source, str):
        raise ValueError("manifest 必须是 JSON 文本或 dict")

    def _reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"重复键: {key}")
            result[key] = value
        return result

    try:
        return json.loads(source, object_pairs_hook=_reject_duplicates)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"manifest JSON 解析失败: {exc}") from exc


def _canonical_params(params):
    out = {}
    for key in sorted(params or {}):
        spec = params[key]
        item = {"type": spec.get("type")}
        if spec.get("type") == "number":
            item["value"] = finite_number(spec.get("value"))
            item["unit"] = spec.get("unit") or None
        else:
            item["value"] = spec.get("value")
        out[key] = item
    return out


def canonical_operation(operation) -> dict:
    """op 语义的规范形式（不含 op_id、动态字段与结果）；非法数值抛 ValueError。"""
    if not isinstance(operation, dict):
        raise ValueError("operation 必须是 dict")
    kind = operation.get("kind")
    canonical = {
        "kind": kind,
        "target": operation.get("target"),
        "dependencies": sorted(str(dep) for dep in (operation.get("dependencies") or [])),
        "specs": _canonical_params(operation.get("specs") or {}),
        "values": _canonical_params(operation.get("values") or {}),
    }
    if kind == "feed":
        canonical["components"] = {
            name: finite_number(value)
            for name, value in sorted((operation.get("components") or {}).items())}
        canonical["composition_basis"] = operation.get("composition_basis")
        total = operation.get("total")
        canonical["total"] = None if total is None else finite_number(total)
        canonical["total_basis"] = operation.get("total_basis")
    return canonical


def operation_fingerprint(operation) -> str:
    """请求指纹：规范 JSON 的 UTF-8 SHA-256（键排序、依赖排序、数值归一、不四舍五入）。"""
    canonical = canonical_operation(operation)
    text = json.dumps(canonical, ensure_ascii=False, sort_keys=True,
                      allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── 静态检查（整批零写、一次返回全部错误） ─────────────────────────────

def _validate_spec(loc: str, section: str, key, spec) -> list[str]:
    where = f"{loc} {section}.{key}"
    if not isinstance(key, str) or not key.strip():
        return [f"{loc} {section} 参数名必须是非空字符串"]
    if not isinstance(spec, dict):
        return [f"{where} 必须是 dict"]
    errors = []
    for field in sorted(set(spec) - {"type", "value", "unit"}):
        errors.append(f"{where} unknown field '{field}'")
    value_type = spec.get("type")
    if value_type not in _VALUE_TYPES:
        errors.append(f"{where} unsupported value type {value_type!r}")
        return errors
    if "value" not in spec or spec.get("value") is None:
        errors.append(f"{where} missing value")
    elif value_type == "number":
        try:
            finite_number(spec["value"])
        except ValueError as exc:
            errors.append(f"{where} non-finite or invalid number: {exc}")
    else:
        value = spec.get("value")
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{where} {value_type} value 必须是非空字符串")
        if spec.get("unit") is not None:
            errors.append(f"{where} {value_type} 不得携带 unit")
    if value_type == "number":
        unit = spec.get("unit")
        if unit is not None:
            if not isinstance(unit, str) or not unit.strip():
                errors.append(f"{where} unit must be a non-empty label")
            elif _norm(unit) not in _KNOWN_UNITS:
                errors.append(f"{where} unknown unit {unit!r}")
    return errors


def _feed_numbers(components) -> list[float]:
    numbers = []
    for name, value in components.items():
        if not isinstance(name, str) or not name.strip() or name != name.strip():
            continue
        try:
            numbers.append(finite_number(value))
        except ValueError:
            continue
    return numbers


def _validate_feed(loc: str, operation) -> list[str]:
    errors = []
    components = operation.get("components")
    if not isinstance(components, dict) or not components:
        errors.append(f"{loc} feed components 必须为非空完整向量")
        components = None
    else:
        for name, value in components.items():
            if not isinstance(name, str) or not name.strip() or name != name.strip():
                errors.append(f"{loc} feed 组分 ID 必须是无首尾空白的名称: {name!r}")
                continue
            try:
                number = finite_number(value)
            except ValueError as exc:
                errors.append(f"{loc} feed 组分 {name} non-finite or invalid number: {exc}")
                continue
            if number < 0:
                errors.append(f"{loc} feed 组分 {name} 不得为负")
    basis = None
    try:
        basis = normalize_basis(operation.get("composition_basis"))
    except Exception as exc:
        errors.append(f"{loc} composition_basis 非法: {exc}")
    if "total" not in operation or "total_basis" not in operation:
        errors.append(f"{loc} feed 必须显式声明 total 与 total_basis（流求和模式需显式 null）")
    total_value = None
    total_ok = True
    total = operation.get("total")
    if total is not None:
        try:
            total_value = finite_number(total)
        except ValueError as exc:
            errors.append(f"{loc} feed total non-finite or invalid number: {exc}")
            total_ok = False
    total_basis = None
    if operation.get("total_basis") is not None:
        try:
            total_basis = normalize_basis(operation.get("total_basis"))
            if total_basis not in TOTAL_BASES:
                errors.append(f"{loc} total_basis 必须是 MOLE-FLOW 或 MASS-FLOW")
        except Exception as exc:
            errors.append(f"{loc} total_basis 非法: {exc}")
    if basis is not None:
        numbers = _feed_numbers(components) if components else []
        if basis.endswith("FRAC"):
            if total is None or not total_ok:
                errors.append(f"{loc} fraction 完整配置必须给出正 total")
            elif total_value <= 0:
                errors.append(f"{loc} feed total 必须为正")
            if total_basis is None:
                errors.append(f"{loc} fraction 必须显式声明 total_basis")
            if numbers:
                if any(value > 1 + 1e-6 for value in numbers):
                    errors.append(f"{loc} fraction 不得大于 1")
                summed = math.fsum(numbers)
                if abs(summed - 1.0) > 1e-6:
                    errors.append(f"{loc} fraction closure 误差超过 1e-6；不自动归一化 (sum={summed})")
        else:
            if total_value is not None and total_basis is None:
                errors.append(f"{loc} flow 模式提供 total 时必须显式声明 total_basis")
            if total_value is None and total_basis is not None:
                errors.append(f"{loc} total_basis 非 null 时必须提供 total")
            if numbers:
                summed = math.fsum(numbers)
                if summed <= 0:
                    errors.append(f"{loc} feed 总流量必须为正")
                elif total_value is not None and total_basis == basis \
                        and not math.isclose(total_value, summed, rel_tol=1e-6, abs_tol=1e-9):
                    errors.append(f"{loc} flow 向量求和与 total 不符，不得再次乘总量")
    return errors


def _find_cycle(dependencies: dict, operations: list) -> list[str] | None:
    id_to_index = {}
    for index, operation in enumerate(operations):
        if isinstance(operation, dict) and isinstance(operation.get("op_id"), str):
            id_to_index.setdefault(operation["op_id"], index)
    color: dict[int, int] = {}

    def visit(index: int, path: list):
        color[index] = 1
        path.append(index)
        for dep in dependencies.get(index, []):
            target = id_to_index.get(dep)
            if target is None:
                continue
            if color.get(target) == 1:
                start = path.index(target)
                return [operations[i].get("op_id") for i in path[start:]] + [dep]
            if color.get(target) is None:
                found = visit(target, path)
                if found:
                    return found
        path.pop()
        color[index] = 2
        return None

    for index in range(len(operations)):
        if color.get(index) is None:
            found = visit(index, [])
            if found:
                return found
    return None


def validate_manifest(manifest) -> list[str]:
    """静态检查；返回全部错误（空列表表示通过），通过前不触碰桥与账本。"""
    if not isinstance(manifest, dict):
        return ["manifest 必须是 dict"]
    errors: list[str] = []
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version 必须为 {SCHEMA_VERSION}")
    for key in ("manifest_id", "task_id", "model_lineage_id"):
        value = manifest.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{key} 必填且为非空字符串")
    for key in ("case_id", "document_id", "checkpoint_id"):
        value = manifest.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            errors.append(f"{key} 若提供必须为非空字符串")
    operations = manifest.get("operations")
    if not isinstance(operations, list) or not operations:
        errors.append("operations 必须是非空列表")
        return errors
    seen: dict[str, int] = {}
    for index, operation in enumerate(operations):
        loc = f"operations[{index}]"
        if not isinstance(operation, dict):
            errors.append(f"{loc} 必须是 dict")
            continue
        op_id = operation.get("op_id")
        if not isinstance(op_id, str) or not op_id.strip():
            errors.append(f"{loc} op_id 必填且为非空字符串")
        else:
            if op_id in seen:
                errors.append(f"{loc} duplicate op_id '{op_id}'（首见 operations[{seen[op_id]}]）")
            else:
                seen[op_id] = index
        if operation.get("kind") not in _OP_KINDS:
            errors.append(f"{loc} unknown kind {operation.get('kind')!r}（必须是 feed 或 block）")
        target = operation.get("target")
        if not isinstance(target, str) or not target.strip():
            errors.append(f"{loc} target required（非空字符串）")
        for field in sorted(set(operation) - _ALLOWED_OP_FIELDS):
            errors.append(f"{loc} unknown op field '{field}'")
        for section in ("specs", "values"):
            params = operation.get(section)
            if params is None:
                continue
            if not isinstance(params, dict):
                errors.append(f"{loc} {section} 必须是 dict")
                continue
            for key, spec in params.items():
                errors.extend(_validate_spec(loc, section, key, spec))
        if operation.get("kind") == "feed":
            errors.extend(_validate_feed(loc, operation))
    # 依赖引用、环与冲突目标
    known_ids = set(seen)
    dependencies: dict[int, list] = {}
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            continue
        deps = operation.get("dependencies")
        if deps is None:
            deps = []
        if not isinstance(deps, list) or any(not isinstance(dep, str) for dep in deps):
            errors.append(f"operations[{index}] dependencies 必须是字符串列表")
            deps = []
        dependencies[index] = deps
        for dep in deps:
            if dep not in known_ids:
                errors.append(f"operations[{index}] unknown dependency '{dep}'")
            if dep == operation.get("op_id"):
                errors.append(f"operations[{index}] 依赖自身 '{dep}'")
    cycle = _find_cycle(dependencies, operations)
    if cycle:
        errors.append("dependency cycle: " + " -> ".join(str(item) for item in cycle))
    claims: dict[tuple, list] = {}
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            continue
        kind = operation.get("kind")
        target = operation.get("target")
        keys = set(operation.get("specs") or {}) | set(operation.get("values") or {})
        owner = operation.get("op_id") if isinstance(operation.get("op_id"), str) \
            else f"operations[{index}]"
        for key in sorted(keys):
            claims.setdefault((kind, target, key), []).append(owner)
    for (kind, target, key), owners in sorted(claims.items(), key=lambda item: repr(item[0])):
        if len(owners) > 1:
            errors.append(f"conflict: target {target!r} ({kind}) param {key!r} set by "
                          + ", ".join(owners))
    return errors


def _topological_order(operations: list) -> list | None:
    """稳定拓扑序；存在环（静态已拦）时返回 None。"""
    id_to_index = {}
    for index, operation in enumerate(operations):
        if isinstance(operation, dict) and isinstance(operation.get("op_id"), str):
            id_to_index.setdefault(operation["op_id"], index)
    indegree = {index: 0 for index in range(len(operations))}
    children: dict[int, list] = {index: [] for index in range(len(operations))}
    for index, operation in enumerate(operations):
        for dep in (operation.get("dependencies") or []):
            parent = id_to_index.get(dep)
            if parent is None or parent == index:
                continue
            children[parent].append(index)
            indegree[index] += 1
    queue = [index for index in range(len(operations)) if indegree[index] == 0]
    order = []
    while queue:
        index = queue.pop(0)
        order.append(index)
        for child in children[index]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if len(order) != len(operations):
        return None
    return [operations[index] for index in order]


# ── 线程内读写原语（单次受控执行内完成） ───────────────────────────────

def _execute_block_nodes(operation, tree) -> dict:
    """规格→参数→聚合读回；节点存在性与写异常都变为回执条目，不重放。"""
    items = []
    errors = []
    for section in ("specs", "values"):
        for key in sorted(operation.get(section) or {}):
            spec = operation[section][key]
            path = f"\\Data\\Blocks\\{operation['target']}\\Input\\{key}"
            item = {"key": key, "section": section, "path": path,
                    "write": "not_attempted", "verify": "unknown"}
            if tree.FindNode(path) is None:
                item.update(write="failed", error=f"node not found: {path}")
                errors.append(f"{operation['op_id']}: {path} not found")
                items.append(item)
                continue
            unit = spec.get("unit") if spec.get("type") == "number" else None
            report = write_path_value(tree, path, spec.get("value"), unit=unit)
            write_state = (report.get("write") or {}).get("status")
            if write_state == "ok":
                item["write"] = "ok"
                item["expected"] = (report.get("verify") or {}).get("expected")
            else:
                item["write"] = "failed"
                message = (report.get("write") or {}).get("error") or report.get("error") \
                    or "write failed"
                item["error"] = message
                errors.append(f"{operation['op_id']}: {path} write failed: {message}")
            verify_state = (report.get("verify") or {}).get("status")
            item["verify"] = {"ok": "pass", "failed": "fail"}.get(verify_state, "unknown")
            items.append(item)
    for item in items:
        if item["write"] != "ok":
            continue
        spec = operation[item["section"]][item["key"]]
        node = tree.FindNode(item["path"])
        if spec.get("type") == "number":
            result = read_si_result(node)
            if not result.get("ok"):
                item["final"] = "unknown"
                errors.append(f"{operation['op_id']}: {item['key']} 聚合读回失败: "
                              f"{result.get('error')}")
            else:
                expected = item.get("expected")
                try:
                    expected_value = finite_number(expected)
                    matches = math.isclose(result["value"], expected_value,
                                           rel_tol=1e-6, abs_tol=1e-9)
                except ValueError:
                    matches = False
                item["final"] = "pass" if matches else "fail"
        else:
            item["final"] = "pass" if node is not None and node.Value == spec.get("value") else "fail"
    return {"write_status": _aggregate_write(items),
            "verify_status": _aggregate_verify(items),
            "items": items, "errors": errors}


def _aggregate_write(items: list) -> str:
    ok = sum(1 for item in items if item["write"] == "ok")
    partial = sum(1 for item in items if item["write"] == "partial_applied")
    failed = sum(1 for item in items if item["write"] == "failed")
    if ok and not failed and not partial:
        return "applied"
    if not ok and not partial:
        return "failed"
    return "partial_applied"


def _aggregate_verify(items: list) -> str:
    finals = [item.get("final", "unknown") for item in items]
    if any(value == "fail" for value in finals):
        return "fail"
    if not finals or any(item["write"] != "ok" for item in items):
        return "unknown"
    if any(value == "unknown" for value in finals):
        return "unknown"
    return "pass"


def _probe_block_nodes(operation, tree) -> dict:
    """只读现状读取；不写、不产生事件。"""
    readings = {}
    for section in ("specs", "values"):
        for key, spec in (operation.get(section) or {}).items():
            path = f"\\Data\\Blocks\\{operation['target']}\\Input\\{key}"
            node = tree.FindNode(path)
            if node is None:
                readings[key] = {"state": "missing"}
                continue
            if spec.get("type") == "number":
                result = read_si_result(node)
                if result.get("ok"):
                    readings[key] = {"state": "read", "value": result["value"],
                                     "read_source": result.get("read_source")}
                else:
                    readings[key] = {"state": "unreadable", "error": result.get("error")}
            else:
                try:
                    readings[key] = {"state": "read", "value": node.Value,
                                     "read_source": "Value(text)"}
                except Exception as exc:
                    readings[key] = {"state": "unreadable", "error": str(exc)}
    return readings


def _expected_si(spec) -> float | None:
    try:
        value = finite_number(spec.get("value"))
    except ValueError:
        return None
    unit = spec.get("unit")
    if unit is None:
        return value
    coord = _UNIT_COORDS.get(_norm(unit))
    if coord is not None:
        return convert_by_name(value, unit, _ROW_BASE[coord[0]])
    user = _norm(unit)
    for (candidate, _si), converter in _CONVERSIONS.items():
        if candidate == user:
            return converter(value)
    return None


def _probe_verdict(operation, probe):
    """True=现状与请求一致；False=明确不一致；None=无法判定（返回 unknown）。"""
    if not isinstance(probe, dict) or probe.get("error") or probe.get("supported") is False:
        return None
    for section in ("specs", "values"):
        for key, spec in (operation.get(section) or {}).items():
            reading = probe.get(key)
            if not isinstance(reading, dict) or reading.get("state") != "read":
                return None
            if spec.get("type") == "number":
                expected = _expected_si(spec)
                if expected is None:
                    return None
                try:
                    current = finite_number(reading.get("value"))
                except ValueError:
                    return None
                if not math.isclose(current, expected, rel_tol=1e-6, abs_tol=1e-9):
                    return False
            elif reading.get("value") != spec.get("value"):
                return False
    return True


def _payload_systemic(payload: dict) -> bool:
    if payload.get("write_status") == "outcome_unknown":
        return True
    joined = " | ".join(payload.get("errors") or [])
    return any(marker in joined for marker in _SYSTEMIC_MARKERS)


# ── 执行引擎 ───────────────────────────────────────────────────────────

class ConfigurationEngine:
    """批配置受控执行；共享准入与可重入操作锁，wrapper 不自排队。"""

    def __init__(self, *, bridge=None, ledger=None):
        self._bridge = bridge
        self._ledger = ledger
        self.operation_lock = threading.RLock()

    def _bridge_instance(self):
        if self._bridge is not None:
            return self._bridge
        from ..com_bridge import aspen
        return aspen

    def _ledger_instance(self):
        if self._ledger is not None:
            return self._ledger
        from ..execution import get_ledger
        return get_ledger()

    # ── 应用 ────────────────────────────────────────────────────────────
    def apply(self, manifest) -> dict:
        manifest = load_manifest(manifest)
        errors = validate_manifest(manifest)
        if errors:
            return self._reject("static", errors, manifest)
        ledger = self._ledger_instance()
        active = ledger.active_lineage_id()
        if active is None:
            return self._reject("identity",
                                ["no_active_task: 批配置必须在活动任务沿袭内执行"], manifest)
        if manifest.get("model_lineage_id") != active:
            return self._reject("identity",
                                [f"identity_mismatch: manifest lineage "
                                 f"{manifest.get('model_lineage_id')!r} != active {active!r}"],
                                manifest)
        with self.operation_lock:
            order = _topological_order(manifest["operations"])
            if order is None:
                return self._reject("static",
                                    ["dependency cycle: 无法建立执行顺序"], manifest)
            receipts = []
            outcomes = {}
            stopped = False
            stop_reason = None
            for operation in order:
                if stopped:
                    receipt = self._finish(ledger, operation, "skipped", "unknown",
                                           "systemic_stop",
                                           [f"系统性停止后续写入: {stop_reason}"],
                                           track=False)
                    receipts.append(receipt)
                    outcomes[operation.get("op_id")] = receipt
                    continue
                blocked = [dep for dep in (operation.get("dependencies") or [])
                           if outcomes.get(dep, {}).get("write_status")
                           not in ("applied", None)]
                if blocked:
                    receipt = self._finish(ledger, operation, "skipped", "unknown",
                                           "dependency_blocked",
                                           [f"依赖未完成: {', '.join(blocked)}"],
                                           track=False)
                    receipts.append(receipt)
                    outcomes[operation.get("op_id")] = receipt
                    continue
                receipt = self._execute_operation(operation, ledger)
                receipts.append(receipt)
                outcomes[operation.get("op_id")] = receipt
                if receipt.get("systemic"):
                    stopped = True
                    stop_reason = receipt.get("reason") or "unit_or_identity_failure"
            return {"schema_version": SCHEMA_VERSION,
                    "manifest_id": manifest.get("manifest_id"),
                    "status": "completed", "phase": "execution",
                    "stopped": stopped, "stop_reason": stop_reason,
                    "receipts": receipts, "errors": []}

    def _execute_operation(self, operation, ledger) -> dict:
        fingerprint = operation_fingerprint(operation)
        prior = ledger.get_operation(op_id=operation.get("op_id"))
        if prior is not None and prior.get("fingerprint") != fingerprint:
            return self._finish(ledger, operation, "failed", "unknown",
                                "same_op_id_different_request",
                                [f"同 op_id 不同请求指纹：prior={prior.get('fingerprint')} "
                                 f"now={fingerprint}"],
                                track=False)
        if prior is not None:
            state = prior.get("state")
            if state in ("pending", "outcome_unknown"):
                return self._finish(ledger, operation, "skipped", "unknown",
                                    "prior_outcome_unknown_paused",
                                    [f"先例状态 {state!r}：已进入 COM 无确定结果，不盲重放"])
            verdict = _probe_verdict(operation, self._probe_current(operation))
            if verdict is True:
                return self._finish(ledger, operation, "skipped", "pass",
                                    "reused_verified",
                                    ["现状与请求一致；复用不重复写"],
                                    fingerprint=fingerprint)
            if state != "failed":
                if verdict is False:
                    return self._finish(ledger, operation, "skipped", "fail",
                                        "manual_divergence_paused",
                                        ["现状与请求不符：列人工差异并暂停，不用旧 state 覆盖"],
                                        fingerprint=fingerprint)
                return self._finish(ledger, operation, "skipped", "unknown",
                                    "reuse_verification_unknown",
                                    ["现状无法核验（未知读回）；保守暂停不写"],
                                    fingerprint=fingerprint)
            # state == "failed"：核验后允许重试写入（目标可能已被修复）
        # 写前 intent 立即持久化
        ledger.mark_pending(op_id=operation.get("op_id"), target=operation.get("target"),
                            detail={"fingerprint": fingerprint})
        ledger.note_operation(op_id=operation.get("op_id"), target=operation.get("target"),
                              fingerprint=fingerprint, state="pending")
        bridge = self._bridge_instance()
        try:
            if operation.get("kind") == "feed":
                payload = self._execute_feed(operation, bridge)
            else:
                def callback():
                    tree = bridge._app.RootModel("")
                    return _execute_block_nodes(operation, tree)
                payload = bridge.call(callback)
        except TimeoutError as exc:
            return self._finish(ledger, operation, "outcome_unknown", "unknown",
                                "timeout_outcome_unknown",
                                [f"TimeoutError: {exc}（已进入 COM 无确定结果，不捏造终态）"],
                                fingerprint=fingerprint, systemic=True)
        except Exception as exc:
            return self._finish(ledger, operation, "failed", "unknown",
                                "execution_error",
                                [f"{type(exc).__name__}: {exc}"],
                                fingerprint=fingerprint)
        reason = None
        if payload.get("write_status") == "failed":
            reason = "write_failed"
        receipt = self._finish(ledger, operation, payload.get("write_status", "failed"),
                               payload.get("verify_status", "unknown"), reason,
                               payload.get("errors") or [], items=payload.get("items"),
                               fingerprint=fingerprint)
        if _payload_systemic(payload):
            receipt["systemic"] = True
        return receipt

    def _execute_feed(self, operation, bridge) -> dict:
        items = []
        errors = []
        for key in sorted(operation.get("values") or {}):
            spec = operation["values"][key]
            if key not in _FEED_PARAMS:
                items.append({"key": key, "section": "values", "write": "failed",
                              "final": "unknown",
                              "error": f"unsupported feed param '{key}'（组成走 components）"})
                errors.append(f"{operation['op_id']}: feed param '{key}' unsupported")
                continue
            unit = spec.get("unit") if spec.get("type") == "number" else None
            report = _stream_param(bridge, operation["target"], key, spec.get("value"), unit=unit)
            write_ok = (report.get("write") or {}).get("status") == "ok"
            item = {"key": key, "section": "values",
                    "write": "ok" if write_ok else "failed",
                    "final": {"ok": "pass", "failed": "fail"}.get(
                        (report.get("verify") or {}).get("status"), "unknown")}
            if not write_ok:
                message = (report.get("write") or {}).get("error") or report.get("error") \
                    or "write failed"
                item["error"] = message
                errors.append(f"{operation['op_id']}: feed {key} write failed: {message}")
            items.append(item)
        report = _stream_update(bridge, operation["target"], operation["components"],
                                basis=operation.get("composition_basis"),
                                total_flow=operation.get("total"),
                                total_basis=operation.get("total_basis"))
        status = report.get("status")
        if status == "ok":
            item = {"key": "composition", "section": "components",
                    "write": "ok", "final": "pass"}
        elif status == "partial_failure":
            item = {"key": "composition", "section": "components",
                    "write": "partial_applied", "final": "fail",
                    "error": report.get("error")}
        else:
            item = {"key": "composition", "section": "components",
                    "write": "failed", "final": "unknown",
                    "error": report.get("error") or status}
        if item.get("error"):
            errors.append(f"{operation['op_id']}: composition {status}: {item['error']}")
        items.append(item)
        return {"write_status": _aggregate_write(items),
                "verify_status": _aggregate_verify(items),
                "items": items, "errors": errors}

    def _probe_current(self, operation):
        if operation.get("kind") == "feed":
            return {"supported": False,
                    "reason": "feed 现状核验需原生适配器读取；未知组合返回 unknown"}
        bridge = self._bridge_instance()

        def callback():
            tree = bridge._app.RootModel("")
            return _probe_block_nodes(operation, tree)

        try:
            return bridge.call(callback)
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    # ── 只读 inspect ────────────────────────────────────────────────────
    def inspect(self, manifest) -> dict:
        manifest = load_manifest(manifest)
        errors = validate_manifest(manifest)
        if errors:
            return self._reject("static", errors, manifest)
        ledger = self._ledger_instance()
        active = ledger.active_lineage_id()
        if active is not None and manifest.get("model_lineage_id") != active:
            return self._reject("identity",
                                [f"identity_mismatch: manifest lineage "
                                 f"{manifest.get('model_lineage_id')!r} != active {active!r}"],
                                manifest)
        receipts = []
        for operation in manifest["operations"]:
            fingerprint = operation_fingerprint(operation)
            prior = ledger.get_operation(op_id=operation.get("op_id")) if active else None
            probe = self._probe_current(operation)
            decision = "would_apply"
            if prior is not None and prior.get("fingerprint") == fingerprint:
                verdict = _probe_verdict(operation, probe)
                decision = {True: "reuse", False: "manual_divergence", None: "unknown"}[verdict]
            elif prior is not None:
                decision = "different_request"
            receipts.append({"op_id": operation.get("op_id"),
                             "kind": operation.get("kind"),
                             "target": operation.get("target"),
                             "fingerprint": fingerprint, "prior": prior,
                             "current": probe, "decision": decision})
        return {"schema_version": SCHEMA_VERSION,
                "manifest_id": manifest.get("manifest_id"),
                "status": "ok", "phase": "inspect",
                "receipts": receipts, "errors": []}

    # ── 回执 ────────────────────────────────────────────────────────────
    def _finish(self, ledger, operation, write_status, verify_status, reason,
                errors, items=None, fingerprint=None, systemic=False,
                track=True) -> dict:
        fingerprint = fingerprint or operation_fingerprint(operation)
        receipt = {"op_id": operation.get("op_id"), "kind": operation.get("kind"),
                   "target": operation.get("target"), "fingerprint": fingerprint,
                   "write_status": write_status, "verify_status": verify_status,
                   "reason": reason, "errors": list(errors)}
        if items is not None:
            receipt["items"] = items
        if systemic:
            receipt["systemic"] = True
        if track:
            ledger.note_operation(op_id=receipt["op_id"], target=receipt["target"],
                                  fingerprint=fingerprint, state=write_status,
                                  detail={"reason": reason, "verify_status": verify_status})
        ledger.record_receipt(op_id=receipt["op_id"], target=receipt["target"],
                              write_status=write_status, verify_status=verify_status,
                              evidence={"fingerprint": fingerprint, "reason": reason,
                                        "errors": list(errors)})
        return receipt

    @staticmethod
    def _reject(phase, errors, manifest) -> dict:
        return {"schema_version": SCHEMA_VERSION,
                "manifest_id": manifest.get("manifest_id") if isinstance(manifest, dict) else None,
                "status": "rejected", "phase": phase, "receipts": [], "errors": list(errors)}


_engine_singleton: ConfigurationEngine | None = None
_engine_lock = threading.Lock()


def get_engine() -> ConfigurationEngine:
    """服务进程的共享配置引擎（惰性单例；含共享操作锁）。"""
    global _engine_singleton
    with _engine_lock:
        if _engine_singleton is None:
            _engine_singleton = ConfigurationEngine()
        return _engine_singleton


def tool_configure_model(manifest) -> dict:
    """应用批配置 manifest；静态检查整批零写，动态分级止损并返回回执。"""
    return get_engine().apply(manifest)


def tool_inspect_configuration(manifest) -> dict:
    """只读检查 manifest 各对象的当前状态与复用判定；不写模型与账本。"""
    return get_engine().inspect(manifest)