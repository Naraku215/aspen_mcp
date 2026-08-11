"""Unit conversion helpers — convert user-supplied engineering units to Aspen COM internal SI.

Aspen COM tree nodes store values in internal SI units (K, N/sqm, kmol/sec, kg/sec, W)
regardless of the display Unit-Set. These helpers read a node's .UnitString to determine
the target internal unit, then convert a user-supplied (value, unit) pair accordingly.

Usage from other tool modules (must be called INSIDE aspen.call() lambda, on COM thread):

    from .units import convert_value
    node = walk(tree, "Data", "Streams", "FEED", "Input", "TEMP")
    v = convert_value(value, unit, node)   # unit=None -> returns value unchanged
    node.SetValue(0, v)

Design:
  - unit=None  =>  pass-through (backward compatible, assumes caller already gave SI)
  - unit given =>  read node.UnitString, look up (user_unit -> node_unit) factor, convert
  - unknown mapping =>  raise ValueError with a clear message (do NOT silently guess)
  - dimensionless (VFRAC, stage count, ratio) =>  node.UnitString may be empty; pass-through
"""
from __future__ import annotations

from typing import Any


def _norm(s: str) -> str:
    """Normalize a unit label for matching: uppercase, strip, collapse spaces."""
    return s.upper().strip().replace(" ", "")


# ── Conversion functions: (user_unit_norm, node_unitstring_norm) -> callable ──
_CONVERSIONS: dict[tuple[str, str], Any] = {
    # Temperature (internal = K)
    ("C", "K"):          lambda v: v + 273.15,
    ("CELSIUS", "K"):    lambda v: v + 273.15,
    ("F", "K"):          lambda v: (v - 32.0) * 5.0 / 9.0 + 273.15,
    ("FAHRENHEIT", "K"): lambda v: (v - 32.0) * 5.0 / 9.0 + 273.15,
    ("K", "K"):          lambda v: float(v),
    # Pressure (internal = N/sqm = Pa)
    ("BAR", "N/SQM"):       lambda v: v * 1.0e5,
    ("ATM", "N/SQM"):       lambda v: v * 101325.0,
    ("PSI", "N/SQM"):       lambda v: v * 6894.757293168,
    ("PSIA", "N/SQM"):      lambda v: v * 6894.757293168,
    ("KPA", "N/SQM"):       lambda v: v * 1000.0,
    ("MPA", "N/SQM"):       lambda v: v * 1.0e6,
    ("PA", "N/SQM"):        lambda v: float(v),
    ("N/SQM", "N/SQM"):     lambda v: float(v),
    ("N/SQ.M", "N/SQM"):    lambda v: float(v),
    # Molar flow (internal = kmol/sec)
    ("KMOL/HR", "KMOL/SEC"):  lambda v: v / 3600.0,
    ("KMOL/S", "KMOL/SEC"):   lambda v: float(v),
    ("KMOL/SEC", "KMOL/SEC"): lambda v: float(v),
    ("MOL/HR", "KMOL/SEC"):   lambda v: v / 3_600_000.0,
    ("LBMOL/HR", "KMOL/SEC"): lambda v: v * 0.45359237 / 3600.0,
    # Mass flow (internal = kg/sec)
    ("KG/HR", "KG/SEC"):  lambda v: v / 3600.0,
    ("KG/S", "KG/SEC"):   lambda v: float(v),
    ("KG/SEC", "KG/SEC"): lambda v: float(v),
    ("LB/HR", "KG/SEC"):  lambda v: v * 0.45359237 / 3600.0,
    # Energy / duty (internal = W)
    ("KW", "W"):    lambda v: v * 1000.0,
    ("MW", "W"):    lambda v: v * 1.0e6,
    ("KJ/HR", "W"): lambda v: v / 3.6,
    ("W", "W"):     lambda v: float(v),
    # --- common aliases (normalized by _norm: upper+strip) ---
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


# ── Node UnitString cache (avoid repeated COM reads for same node name) ──
_unit_cache: dict[str, str] = {}


def clear_unit_cache():
    """Clear the UnitString cache.

    Call after new_simulation or unit-set change, as node UnitString
    values may differ in the new context.
    """
    _unit_cache.clear()


def _node_unit_string(node: Any) -> str:
    """Read a COM node's UnitString, returning '' if unavailable."""
    try:
        nm = node.Name
    except Exception:
        nm = ""
    if nm and nm in _unit_cache:
        return _unit_cache[nm]
    try:
        us = node.UnitString
    except Exception:
        us = ""
    if us is None:
        us = ""
    us = str(us).strip()
    if nm:
        _unit_cache[nm] = us
    return us


def convert_value(value, user_unit: str | None, node: Any):
    """Convert a user-supplied value to the node's internal SI unit.

    Args:
        value: Numeric value from the caller.
        user_unit: User-facing unit label (e.g. "C", "bar", "kmol/hr").
                   None => pass-through unchanged (backward compatible).
        node: The target COM IHNode (must already be walked to). Used to read
               .UnitString so we know the internal unit.

    Returns:
        Converted numeric value ready for node.SetValue(0, v).

    Raises:
        ValueError: If user_unit is given but no conversion is known for
                   (user_unit, node_unit_string). The error message notes the
                   node's actual unit and suggests passing None if SI intended.
    """
    if user_unit is None:
        return value
    node_unit = _node_unit_string(node)
    if not node_unit:
        # Dimensionless node (VFRAC, stage count, ratio) - no conversion needed.
        return value
    key = (_norm(user_unit), _norm(node_unit))
    fn = _CONVERSIONS.get(key)
    if fn is None:
        raise ValueError(
            f"No unit conversion registered for '{user_unit}' -> '{node_unit}' "
            f"(node unit). If the value is already in internal SI ({node_unit}), "
            f"call with unit=None."
        )
    return fn(value)


def describe_node_unit(node: Any) -> str:
    """Return a human-readable description of a node's unit (for error messages)."""
    us = _node_unit_string(node)
    try:
        nm = node.Name
    except Exception:
        nm = "?"
    return f"node '{nm}' unit='{us}'"


def convert_by_name(value, user_unit: str | None, internal_unit: str):
    """Convert a value when the internal unit is known (no COM node needed).

    Use this when the physical quantity is known by context (e.g. set_tear_estimate
    knows temp->K, pres->N/SQM, total_flow->KMOL/SEC) and the COM node's
    UnitString may be unreliable (un-initialized streams).

    Args:
        value: Numeric value from the caller.
        user_unit: User-facing unit label, or None for pass-through.
        internal_unit: The known internal SI unit (e.g. "K", "N/SQM", "KMOL/SEC").

    Returns:
        Converted numeric value, or original if user_unit is None.

    Raises:
        ValueError: If no conversion is registered for the unit pair.
    """
    if user_unit is None:
        return value
    key = (_norm(user_unit), _norm(internal_unit))
    fn = _CONVERSIONS.get(key)
    if fn is None:
        raise ValueError(
            f"No unit conversion registered for '{user_unit}' -> '{internal_unit}' "
            f"(known internal unit). If the value is already in internal SI "
            f"({internal_unit}), call with unit=None."
        )
    return fn(value)


def format_conversion(orig_value, orig_unit: str | None,
                      conv_value, node_unit: str) -> str:
    """Return a human-readable string showing the conversion.

    Example: format_conversion(25, "C", 298.15, "K") -> "25 C -> 298.15 K"
    """
    if orig_unit:
        return f"{orig_value} {orig_unit} -> {conv_value} {node_unit}"
    return str(conv_value)
