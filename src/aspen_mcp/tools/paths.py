"""Path-based tools: get_value, set_value by backslash path.

All COM operations run inside aspen.call() lambdas on the dedicated
COM apartment thread.
"""

from __future__ import annotations

from ..com_bridge import aspen
from ._common import normalize_path


def tool_get_value(path: str, attribute: str | None = None) -> str:
    """Read a value by backslash path (e.g. Data\\Streams\\S1\\Output\\RES_TEMP).

    路径统一 / 与反斜杠、去首尾与重复分隔符后按对象名查找。

    attribute selects the view (default None = Value):
      None / "value" -> node.Value          (display value, follows unit set)
      "si"           -> ValueForUnit(row, SI col) (internal SI, stable)
      "attr"         -> node.AttributeValue(0)    (HAP_VALUE raw attribute)
      "unit"         -> node.UnitString     (display unit label)
    """
    try:
        normalized = normalize_path(path)
    except ValueError as exc:
        return f"Error: {exc}"
    lookup = "\\" + normalized
    try:
        def impl():
            tree = aspen._app.RootModel("")
            node = tree.FindNode(lookup)
            if node is None:
                return f"Node not found: {normalized}"
            attr = (attribute or "value").lower().strip()
            if attr in ("si", "internal", "siunit"):
                from .units import read_si
                return str(read_si(node))
            if attr in ("attr", "attribute", "attributevalue"):
                return str(node.AttributeValue(0))
            if attr in ("unit", "unitstring"):
                return str(node.UnitString)
            return str(node.Value)
        return aspen.call(impl)
    except Exception as exc:
        return f"Error: {exc}"


def tool_set_value(path: str, value: str | float, unit: str | None = None) -> str:
    """Write a value by backslash path.

    Handles parent nodes that have children by trying child matching first.
    Unverified stream composition/total/basis paths are rejected before COM.
    路径先统一分隔符与首尾分隔符，再作守卫与查找；不改真实对象名。
    """
    try:
        normalized = normalize_path(path)
    except ValueError as exc:
        return f"Error: {exc}"
    lookup = "\\" + normalized
    try:
        from .units import guard_stream_path, unsupported_report, write_path_value, format_write_result
        guard = guard_stream_path(lookup)
        if guard is not None:
            report = unsupported_report(lookup, guard)
            report["path"] = lookup
            return format_write_result(report)

        def impl():
            report = write_path_value(aspen._app.RootModel(""), lookup, value, unit=unit)
            report["path"] = lookup
            return format_write_result(report)
        result = aspen.call(impl)
        if isinstance(result, str) and result.startswith("Error"):
            return result
        return result
    except Exception as exc:
        return "Error: " + str(exc)
