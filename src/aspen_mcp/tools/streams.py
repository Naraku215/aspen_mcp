"""Stream tools: list, get, set params, composition, connect.

All COM operations run inside aspen.call() lambdas on the dedicated
COM apartment thread.
"""

from __future__ import annotations

from typing import Any

from ..com_bridge import aspen
from ._common import walk, ensure_parent

_STREAM_RESULT_KEYS = [
    "RES_TEMP", "RES_PRES", "RES_VFRAC", "RES_MASSFLOW",
    "RES_MOLEFLOW", "RES_VOLFLOW", "MW", "COMPTYPE",
]
_STREAM_META_KEYS = ["SOURCE", "DESTINATION", "FULLSOURCE", "FULLDEST"]


def _collect_values_on_com(node: Any, max_keys: int = 80) -> dict[str, Any]:
    """Return {Name: Value} for every scalar child of *node* (on COM thread)."""
    data: dict[str, Any] = {}
    seen = 0
    for i in range(10_000):
        try:
            c = node.Elements(i)
        except Exception:
            break
        if c is None:
            break
        if c.ValueType == 0:
            continue
        data[c.Name] = c.Value
        seen += 1
        if seen >= max_keys:
            break
    return data




def tool_list_all_streams(random_string: str | None = None) -> list[str]:
    """List all stream names in the flowsheet."""
    try:
        def impl():
            tree = aspen._app.RootModel("")
            streams_node = walk(tree, "Data", "Streams")
            if streams_node is None:
                return ["(no streams node)"]
            names = []
            for i in range(10000):
                try:
                    c = streams_node.Elements(i)
                except Exception:
                    break
                if c is None:
                    break
                names.append(c.Name)
            return names
        return aspen.call(impl)
    except Exception as exc:
        return [f"Error: {exc}"]


def tool_get_stream(name: str) -> dict[str, Any]:
    """Read stream properties — temperature, pressure, flows, and composition.

    Returns top-level keys: res_temp, res_pres, res_vfrac, res_massflow,
    res_moleflow, res_volflow, mw, comptype, destination.
    Plus mole_frac_liq (liquid mole fractions) and
    mole_frac_vap (vapor mole fractions) when available.
    """
    try:
        def impl():
            result: dict[str, Any] = {"name": name}
            tree = aspen._app.RootModel("")
            out = walk(tree, "Data", "Streams", name, "Output")
            if out is not None:
                all_vals = _collect_values_on_com(out)
                for k in _STREAM_RESULT_KEYS + _STREAM_META_KEYS:
                    if k in all_vals:
                        result[k.lower()] = all_vals[k]
                remaining = {k: v for k, v in all_vals.items()
                             if k not in _STREAM_RESULT_KEYS + _STREAM_META_KEYS
                             and k not in ("Unit Set", "User Table", "User Tree")}
                if remaining:
                    result["output"] = remaining
            else:
                result["output_error"] = "No Output node"

            # Read liquid and vapor mole fractions if available
            tree = aspen._app.RootModel("")
            for phase_key, out_key in [("X", "mole_frac_liq"), ("Y", "mole_frac_vap")]:
                phase_node = walk(tree, "Data", "Streams", name, "Output", phase_key)
                if phase_node is not None:
                    comps = {}
                    for ci in range(100):
                        try:
                            cc = phase_node.Elements(ci)
                        except Exception:
                            break
                        if cc is None:
                            break
                        if cc.ValueType != 0:
                            comps[cc.Name] = cc.Value
                    if comps:
                        result[out_key] = comps

            return result
        return aspen.call(impl)
    except Exception as exc:
        return {"error": str(exc)}


def tool_get_stream_composition_info(name: str) -> dict[str, Any]:
    """Return known stream composition metadata."""
    try:
        def impl():
            from ..stream_contract import capability_matrix
            result: dict[str, Any] = {"stream": name, "capabilities": capability_matrix(),
                                      "read_source": "Value(raw)", "basis_status": "unknown"}
            tree = aspen._app.RootModel("")
            out = walk(tree, "Data", "Streams", name, "Output")
            if out is None:
                return {"error": f"Stream '{name}' not found"}
            for k in ("COMPTYPE", "MW", "RES_MOLEFLOW", "RES_MASSFLOW", "SOURCE", "DESTINATION"):
                node = walk(tree, "Data", "Streams", name, "Output", k)
                if node is not None:
                    result[k.lower()] = node.Value
            return result
        return aspen.call(impl)
    except Exception as exc:
        return {"error": str(exc)}


def tool_set_stream_param(stream_name: str, param: str, value: str | float,
                          basis: str | None = None, unit: str | None = None, *,
                          total_basis: str | None = None) -> dict:
    """流股单项更新；省略单位仅在已知量纲时解释为 SI。

    组成写入必须具有独立验证的内部能力；返回写/读回及部分更新范围。
    """
    try:
        return aspen.set_stream_param(stream_name=stream_name, param=param, value=value,
                                      basis=basis, unit=unit, total_basis=total_basis)
    except Exception as exc:
        return {"ok": False, "status": "failed", "error": str(exc), "scope": "partial"}


def tool_set_stream_composition(stream_name: str, component: str, flow: str | float,
                                unit: str | None = None, *, basis: str | None = None,
                                total_basis: str | None = None) -> dict:
    """单组分的显式部分更新；不补零、不改变其余组成的基准。"""
    try:
        return aspen.set_stream_composition(stream_name=stream_name, component=component,
                                            flow=flow, unit=unit, basis=basis, total_basis=total_basis)
    except Exception as exc:
        return {"ok": False, "status": "failed", "error": str(exc), "scope": "partial"}


def tool_add_stream(name: str) -> str:
    """Add a new stream via Elements collection."""
    try:
        def impl():
            tree = aspen._app.RootModel("")
            str_node = walk(tree, "Data", "Streams")
            if str_node is None:
                str_node = ensure_parent(tree, "Data", "Streams")
                if str_node is None:
                    return f"Error: Cannot create Streams node"
            str_node.Elements.Add(name)
            return f"Stream '{name}' added"
        return aspen.call(impl)
    except Exception as exc:
        return f"Error: {exc}"


def tool_set_stream_composition_batch(stream_name: str, components: dict[str, str | float],
                                      basis: str = "MOLE-FLOW", total_flow: float | None = None,
                                      unit: str | None = None, *, total_basis: str | None = None,
                                      total_unit: str | None = None) -> dict:
    """完整非负有限组成向量；组成基准与总量基准分别验证。

    fraction 闭合容差 1e-6，必须提供正总流量；flow 求和而不再乘总量。
    旧 fraction 的 unit 仅指总量单位。所有生产组合仍为 unverified。
    """
    try:
        return aspen.set_stream_composition_batch(
            stream_name=stream_name, components=components, basis=basis, total_flow=total_flow,
            unit=unit, total_basis=total_basis, total_unit=total_unit)
    except Exception as exc:
        return {"ok": False, "status": "failed", "error": str(exc), "scope": "complete"}


def tool_remove_stream(name: str) -> str:
    """Remove a stream via Elements.Remove."""
    try:
        def impl():
            tree = aspen._app.RootModel("")
            str_elems = walk(tree, "Data", "Streams").Elements
            str_elems.Remove(name)
            return f"Stream '{name}' removed"
        return aspen.call(impl)
    except Exception as exc:
        return f"Error: {exc}"


def _connect_port_on_com(block_name: str, port_name: str, stream_name: str) -> str:
    """Connect a stream to a specific block port (on COM thread).

    APPEND mode (does NOT clear+add). Checks if the stream is already
    connected; only adds if not. Allows multiple streams on the same
    port (e.g. multiple feeds into MIXER F(IN)).

    NOTE: Manipulates Ports collection (data layer), not the graphical
    flowsheet. Re-open .apw to refresh visual connections.
    """
    tree = aspen._app.RootModel("")
    block = walk(tree, "Data", "Blocks", block_name)
    if block is None:
        return f"Block '{block_name}' not found"
    try:
        ports_node = block.Elements("Ports")
    except Exception:
        return f"No Ports on block '{block_name}'"
    try:
        port = ports_node.Elements(port_name)
    except Exception:
        return f"Port '{port_name}' not found on block '{block_name}'"
    if port is None:
        return f"Port '{port_name}' not found on block '{block_name}'"
    port_els = port.Elements
    
    # Check if the stream is already connected to this port
    already_connected = False
    for i in range(port_els.Count):
        try:
            if port_els.Item(i).Name == stream_name:
                already_connected = True
                break
        except Exception:
            pass
    
    if already_connected:
        return f"Stream '{stream_name}' already connected to {block_name}:{port_name}"
    
    port_els.Add(stream_name)
    return f"Stream '{stream_name}' connected to {block_name}:{port_name}"


def tool_connect(source: str, dest_block: str, stream_name: str | None = None,
                    source_port: str | None = None, dest_port: str | None = None) -> str:
    """Connect two blocks with a stream. mutates=True.

    Also works when *source* is a stream name (not a block):
    the existing stream is connected to *dest_block*'s input port.

    Default ports: P(OUT) on source block, F(IN) on dest.
    Auto-creates stream if not given (name = "source-dest").

    Examples:
      connect("H1", "F1")                           — heater → flash
      connect("F1", "GAS", source_port="V(OUT)")    — flash vapor outlet
      connect("F1", "LIQ", source_port="L(OUT)")    — flash liquid outlet
      connect("FEED", "H1")                         — feed stream → heater
    """
    try:
        def impl():
            tree = aspen._app.RootModel("")

            # ---- detect whether *source* is a block or a stream ----
            is_block = walk(tree, "Data", "Blocks", source) is not None
            is_stream = walk(tree, "Data", "Streams", source) is not None

            if not is_block and is_stream:
                # source is a feed stream → connect to dest only
                actual_stream = source
                actual_dest_port = dest_port or "F(IN)"
                return _connect_port_on_com(dest_block, actual_dest_port, actual_stream)

            # ---- source is a block → normal block-to-block connection ----
            actual_stream = stream_name or f"{source}-{dest_block}"
            actual_source_port = source_port or "P(OUT)"
            actual_dest_port = dest_port or "F(IN)"

            # Ensure stream exists
            if walk(tree, "Data", "Streams", actual_stream) is None:
                walk(tree, "Data", "Streams").Elements.Add(actual_stream)

            r1 = _connect_port_on_com(source, actual_source_port, actual_stream)
            if "Error" in r1:
                return f"Connect failed at source: {r1}"
            r2 = _connect_port_on_com(dest_block, actual_dest_port, actual_stream)
            if "Error" in r2:
                return f"Connect failed at dest: {r2}"
            return f"Connected {source}:{actual_source_port} --[{actual_stream}]--> {dest_block}:{actual_dest_port}"

        return aspen.call(impl)
    except Exception as exc:
        return f"Error: {exc}"


def tool_connect_port(block_name: str, port_name: str, stream_name: str) -> str:
    """Connect a stream to one block port (granular control)."""
    try:
        def impl():
            tree = aspen._app.RootModel("")
            if walk(tree, "Data", "Streams", stream_name) is None:
                str_elems = walk(tree, "Data", "Streams").Elements
                str_elems.Add(stream_name)
            return _connect_port_on_com(block_name, port_name, stream_name)
        return aspen.call(impl)
    except Exception as exc:
        return f"Error: {exc}"


def tool_disconnect(stream_name: str) -> str:
    """Disconnect a stream from all ports and remove it.

    Safety: snapshots ALL connections FIRST, then removes from ports one
    by one collecting errors, and only removes from the Streams collection
    if at least one port removal succeeded.  Never leaves the stream in a
    half-disconnected state that could crash remove_block().
    """
    try:
        def impl():
            tree = aspen._app.RootModel("")
            blocks_node = walk(tree, "Data", "Blocks")
            if blocks_node is None:
                return "Error: blocks node not found"

            # Phase 1: snapshot — find every (block, port) this stream lives on
            connections: list[tuple] = []
            for bi in range(500):
                try:
                    block = blocks_node.Elements(bi)
                except Exception:
                    break
                if block is None:
                    break
                try:
                    ports_node = block.Elements("Ports")
                except Exception:
                    continue
                for pi in range(30):
                    try:
                        port = ports_node.Elements(pi)
                    except Exception:
                        break
                    if port is None:
                        break
                    try:
                        port_els = port.Elements
                    except Exception:
                        continue
                    for k in range(port_els.Count):
                        try:
                            item = port_els.Item(k)
                            if item.Name == stream_name:
                                connections.append((block, port, port_els, k))
                                break
                        except Exception:
                            break

            # Phase 2: remove from ports (best-effort, collect errors)
            disconnected: list[str] = []
            failures: list[str] = []
            for block, port, port_els, idx in connections:
                try:
                    port_els.Remove(idx)
                    disconnected.append(f"{block.Name}:{port.Name}")
                except Exception as exc:
                    failures.append(f"{block.Name}:{port.Name}")

            # Phase 3: remove from Streams collection
            # Only if at least 1 port removal succeeded, so we don't orphan
            # the stream reference in the collection.
            str_removed = False
            if not failures:
                try:
                    str_elems = walk(tree, "Data", "Streams").Elements
                    str_elems.Remove(stream_name)
                    str_removed = True
                except Exception:
                    pass

            # Build result message
            parts = []
            if disconnected:
                parts.append(f"disconnected from {', '.join(disconnected)}")
            if failures:
                parts.append(f"FAILED on {', '.join(failures)}")
            if str_removed:
                parts.append("and removed")
            elif not failures and not disconnected:
                parts.append("was not connected to any block")
            else:
                parts.append("(kept in Streams collection — see failures above)")

            return f"Stream '{stream_name}' " + ", ".join(parts)
        _ensure_blocks_node()
        return aspen.call(impl)
    except Exception as exc:
        return f"Error disconnecting stream '{stream_name}': {exc}"

def _ensure_blocks_node():
    """Re-create Data/Blocks node if Aspen auto-removed it."""
    def impl():
        tree = aspen._app.RootModel("")
        data = walk(tree, "Data")
        if data is None:
            return False
        blks = walk(tree, "Data", "Blocks")
        if blks is not None:
            return True
        try:
            data.Elements.Add("Blocks")
            return True
        except Exception:
            return False
    return aspen.call(impl)


def tool_list_block_ports(block_name: str) -> str:
    """List all ports on a block and which streams are connected."""
    try:
        def impl():
            tree = aspen._app.RootModel("")
            block = walk(tree, "Data", "Blocks", block_name)
            if block is None:
                return f"Block '{block_name}' not found"
            try:
                ports_node = block.Elements("Ports")
            except Exception:
                return f"Block '{block_name}' has no ports"
            lines = [f"Ports for block '{block_name}':"]
            for pi in range(50):
                try:
                    p = ports_node.Elements(pi)
                except Exception:
                    break
                if p is None:
                    break
                els = p.Elements
                streams = []
                for j in range(els.Count):
                    try:
                        streams.append(els.Item(j).Name)
                    except Exception:
                        break
                streams_str = ", ".join(streams) if streams else "(empty)"
                lines.append(f"  {p.Name} -> [{streams_str}]")
            return "\n".join(lines)
        return aspen.call(impl)
    except Exception as exc:
        return f"Error: {exc}"
