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


def _row(node: Any) -> int | None:
    """Quantity-dimension id of a node (HAP_UNITROW = 2)."""
    try:
        r = node.AttributeValue(2)
        return int(r) if isinstance(r, (int, float)) else None
    except Exception:
        return None


def _si_col(row: int | None) -> int | None:
    """SI unit column for a quantity row, or None if unknown."""
    if row is None:
        return None
    return _ROW_SI_COL.get(row)


def read_si(node: Any):
    """Read a node's value in its internal SI unit (stable across unit sets)."""
    if node is None:
        return None
    row = _row(node)
    col = _si_col(row)
    if col is not None:
        try:
            return node.ValueForUnit(row, col)
        except Exception:
            pass
    try:
        return node.Value
    except Exception:
        return None


def write_value(node: Any, value, user_unit: str | None = None,
                base_internal: str | None = None) -> None:
    """Write a numeric value into a COM node, unit-aware.

    Semantics:
      - user_unit None      => value is ALREADY in the node's SI unit; write
                               natively with the SI column (exact).
      - user_unit given     => if the unit has a native (row, col) coordinate,
                               use ``SetValueAndUnit(value, col)`` (Aspen
                               converts); otherwise convert with the Python
                               table to SI and write with the SI column.
      - base_internal       => known SI unit name of the node's quantity
                               (e.g. "K", "N/SQM", "W", "KMOL/SEC"), used for
                               the Python-table fallback look-up.

    ``SetValueAndUnit`` failures (dimensionless nodes, weird rows) fall back
    to the legacy ``node.SetValue(0, value)`` so existing behaviour is kept.
    """
    if node is None:
        raise ValueError("write_value: node is None")
    row = _row(node)
    si_col = _si_col(row)
    if user_unit is None:
        target_col = si_col
        target_value = value
    else:
        coord = _UNIT_COORDS.get(_norm(user_unit))
        if coord is not None:
            target_col = coord[1]
            target_value = value
        else:
            base = _norm(base_internal or "")
            if base:
                fn = _CONVERSIONS.get((_norm(user_unit), base))
            else:
                fn = None
            if fn is None and base:
                raise ValueError(
                    f"No unit conversion registered for '{user_unit}' -> "
                    f"'{base}'. If the value is already in internal SI "
                    f"({base}), call with unit=None."
                )
            target_value = value if fn is None else fn(value)
            target_col = si_col
    if target_col is not None:
        try:
            node.SetValueAndUnit(target_value, target_col)
            return
        except Exception:
            pass
    node.SetValue(0, target_value)


def convert_value(value, user_unit: str | None, node: Any = None):
    """Convert a user value to SI using the Python table.

    Kept for backward compatibility (several tools pre-convert before
    delegating to the bridge). ``node`` is ignored now - the conversion no
    longer depends on the node's misleading UnitString.

    unit=None passes through unchanged (assumes the value is already SI).
    Unknown units raise ValueError naming the supported unit labels.
    """
    if user_unit is None:
        return value
    return _convert_si(value, user_unit)


def convert_by_name(value, user_unit: str | None, internal_unit: str):
    """Convert a value when the SI unit of the quantity is known.

    unit=None passes through. Same-unit pairs pass through (Bug-4 fix).
    """
    if user_unit is None:
        return value
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
    return fn(value)


def _convert_si(value, user_unit: str) -> float:
    """Convert to SI, trying each known SI base till a mapping exists."""
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
