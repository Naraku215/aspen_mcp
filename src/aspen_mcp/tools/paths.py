"""Path-based tools: get_value, set_value by backslash path.

All COM operations run inside aspen.call() lambdas on the dedicated
COM apartment thread.
"""

from __future__ import annotations

from ..com_bridge import aspen


def tool_get_value(path: str) -> str:
    """Read a value by backslash path (e.g. Data\\Streams\\S1\\Output\\RES_TEMP)."""
    try:
        def impl():
            tree = aspen._app.RootModel("")
            node = tree.FindNode(path)
            if node is None:
                return f"Node not found: {path}"
            return str(node.Value)
        return aspen.call(impl)
    except Exception as exc:
        return f"Error: {exc}"


def tool_set_value(path: str, value: str | float, unit: str | None = None) -> str:
    """Write a value by backslash path.

    Handles parent nodes that have children by trying child matching first.
    """
    try:
        def impl():
            nonlocal value
            tree = aspen._app.RootModel("")
            node = tree.FindNode(path)
            if node is None:
                return "Error: Path not found"
            # Unit conversion: if unit given and value is numeric, convert to internal SI.
            # Block Input nodes have empty .UnitString, so convert_value() would
            # silently pass through. For known block params, convert by name using
            # the known internal unit; otherwise fall back to reading the node.
            if unit is not None and not isinstance(value, str):
                from .units import convert_value, convert_by_name
                from .blocks import _BLOCK_PARAM_UNITS
                param_name = path.rstrip("\\").split("\\")[-1]
                known = _BLOCK_PARAM_UNITS.get(param_name)
                try:
                    if known:
                        value = convert_by_name(value, unit, known)
                    else:
                        value = convert_value(value, unit, node)
                except ValueError as ve:
                    return f"Error: {ve}"
            # If node has children and value is a string, try matching child.
            # Accessing .Elements on a leaf node throws COM error 2010
            # ("list operation on leaf node") — must be wrapped in try/except.
            try:
                if node.Elements.Count > 0 and isinstance(value, str):
                    for i in range(node.Elements.Count):
                        try:
                            child = node.Elements(i)
                            if child.Name.upper() == value.upper():
                                child.SetValue(0, value)
                                return "Set " + path + " -> " + child.Name + " = " + value
                        except Exception:
                            pass
            except Exception:
                pass  # leaf node — Elements is not accessible, fall through
            node.SetValue(0, value)
            return "Set " + path + " = " + str(value)
        result = aspen.call(impl)
        if isinstance(result, str) and result.startswith("Error"):
            return result
        return result
    except Exception as exc:
        return "Error: " + str(exc)
