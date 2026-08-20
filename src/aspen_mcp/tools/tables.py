"""Generic table tools: insert rows into existing table nodes.

All COM operations run inside aspen.call() lambdas on the dedicated
COM apartment thread.
"""

from __future__ import annotations

from ..com_bridge import aspen


def tool_add_table_row(path: str, label: str | None = None, col: int = 0) -> str:
    """Insert a row into an existing table node and optionally label it.

    Generic building block for table editing:
      - RYIELD / RSTOIC yield tables: add a substream row, then fill
        cells with set_value("\\Data\\Blocks\\X\\Input\\YIELD\\<col>\\<row>", v)
      - any other COM table container reachable by backslash path.

    The node at `path` must already exist (block/components created
    first); this tool only adds rows, it never creates the table node.

    Idempotent: if a row whose label in column `col` equals `label`
    already exists, no row is inserted and that row index is returned.

    Returns the 0-based row index of the new (or existing) row, for
    follow-up set_value() calls.
    """
    try:
        def impl():
            tree = aspen._app.RootModel("")
            p = path if path.startswith("\\") else "\\" + path
            node = tree.FindNode(p)
            if node is None:
                return f"Error: node not found: {p} (block/components must exist first)"
            elems = node.Elements
            if elems is None:
                return f"Error: node has no Elements (not a table container): {p}"

            if label is not None:
                target = label.strip().upper()
                for i in range(elems.Count):
                    try:
                        if str(elems.GetLabel(col, i)).strip().upper() == target:
                            return f"Row {i} already exists with label '{label}' (no insert)"
                    except Exception:
                        continue

            row = elems.Count
            elems.InsertRow(col, row)
            if label is not None:
                elems.SetLabel(col, row, False, label)
            note = f" with label '{label}'" if label else " (blank)"
            return f"Inserted row {row}{note} into {p}"
        return aspen.call(impl)
    except Exception as exc:
        return f"Error: {exc}"