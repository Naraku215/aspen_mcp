"""Path-based tools: get_value, set_value by backslash path.

All COM operations run inside aspen.call() lambdas on the dedicated
COM apartment thread.
"""

from __future__ import annotations

from ..com_bridge import aspen


def tool_get_value(path: str, attribute: str | None = None) -> str:
    """Read a value by backslash path (e.g. Data\\Streams\\S1\\Output\\RES_TEMP).

    attribute selects the view (default None = Value):
      None / "value" -> node.Value          (display value, follows unit set)
      "si"           -> ValueForUnit(row, SI col) (internal SI, stable)
      "attr"         -> node.AttributeValue(0)    (HAP_VALUE raw attribute)
      "unit"         -> node.UnitString     (display unit label)
    """
    try:
        def impl():
            tree = aspen._app.RootModel("")
            node = tree.FindNode(path)
            if node is None:
                return f"Node not found: {path}"
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
    """
    try:
        def impl():
            nonlocal value
            tree = aspen._app.RootModel("")
            node = tree.FindNode(path)
            if node is None:
                return "Error: Path not found"
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
            if isinstance(value, str):
                node.SetValue(0, value)
            else:
                from .units import write_value as _wv
                from .blocks import _BLOCK_PARAM_UNITS
                param_name = path.rstrip("\\").split("\\")[-1]
                _wv(node, value, unit,
                    base_internal=_BLOCK_PARAM_UNITS.get(param_name))
            return "Set " + path + " = " + str(value)
        result = aspen.call(impl)
        if isinstance(result, str) and result.startswith("Error"):
            return result
        return result
    except Exception as exc:
        return "Error: " + str(exc)
