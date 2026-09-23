"""Fill trivial/meaningless active-but-unset params so diagnostics show only what matters."""
from __future__ import annotations
from ..com_bridge import aspen
from ._common import walk


# Params that are safe to fill — they have NO engineering impact.
# These are COM infrastructure, UI artifacts, estimation placeholders,
# or dynamic/holdup params irrelevant in steady-state.
_TRIVIAL_PARAMS: dict[str, int | str] = {
    # --- Safety valve / UI artifacts ---
    "PSVBY": "",
    "PSVLABELBOX": "",
    "PSVROUTE0": "",
    "PSVROUTE1": "",
    "PSVROUTE2": "",
    "PSVROUTE3": "",
    "PSVROUTE4": "",
    "PSVROUTE5": "",
    "PSVBX": "",
    # --- XML / serialization junk ---
    "SSXML": "",
    # --- Passthrough / dummy ---
    "THRU": "",
    # --- User script method (not using custom scripts → irrelevant) ---
    "USER_METHOD": "",
    "GLOBALSCRIPT": "",
    "LOCALSCRIPT": "",
    "LSSOURCE": "",
    "INIT_SCRIPT": "",
    # --- Flash estimation (Aspen auto-calculates these) ---
    "P_EST": 0,
    "T_EST": 0,
    # --- Dynamic / holdup params (steady-state only → 0 is fine) ---
    "MEIN": 0,
    "MEOUT": 0,
    "V_IN": 0,
    "V_OUT": 0,
    "HOLDUP_MED": 0,
    "MASSEQP": 0,
    # --- Form / scaling ---
    "FORMSCALE": "",
    # --- Derivative method (auto-detect) ---
    "DERIV_METHOD": "",
    # --- Solver method (auto) ---
    "SOLVE_METHOD": "",
    "SOLVE_OPEN": "",
    "SPARSITY": "",
    # --- Misc infrastructure ---
    "BOUND_TYPE": "",
    "AUTO_COMPS": "",
    "HIERARCHY": "",
    "INTERACTIVE": "",
    "RAMPED": "",
    "RESTART": "",
    "SBWEIGHT": "",
    "SC_METHOD": "",
    "SC_TYPE": "",
    "THRESHOLD": "",
    "UTILITY_ID": "",
    "IDESCRIPTION": "",
    # --- Equation-oriented infrastructure (not used in SM) ---
    "EOVAR_TYPE": "",
    "EO_COMPS": "",
    "EO_DEP_COMPS": "",
    "EO_FORM": "",
    "EO_HEAVY_KEY": "",
    "EO_L2_COMP": "",
    "EO_LIGHT_KEY": "",
    "EO_PT_COMP": "",
    "EO_PT_PHASE": "",
    "EO_PT_PHYSQT": "",
    "EO_PT_TYPE": "",
    "EO_PT_UOM": "",
    "EO_PT_VALUE": 0,
    # --- Spec group infrastructure ---
    "IVENABLED": "",
    "IVLOWER": 0,
    "IVNAME": "",
    "IVSCALE": 1.0,
    "IVSPEC": "",
    "IVSTEP": 0,
    "IVUPPER": 0,
    "IVVALUE": 0,
    "SG_LOWER": 0,
    "SG_PHYS_QTY": "",
    "SG_SPEC": "",
    "SG_UOM": "",
    "SG_UPPER": 0,
    "SG_VALUE": 0,
    # --- Tables/curves (only relevant if INDEP_VAR is set) ---
    "NPOINTS": 0,
    "INCR": 0,
    "INDEP_VAR": "",
    "LINES": 0,
    "WIDE": 0,
    # --- PSD (particle size distribution — not relevant for most) ---
    # NOTE: PSD id-reference keys (PSDID / OV_PSDID) and mesh-definition keys
    # (INTERVAL / LOWER / UPPER / CUMFRAC / S_MESHOPT / S_OPT ...) MUST NOT be
    # filled with 0: writing 0 to an id reference makes Aspen auto-create a
    # bogus PSD mesh named "0" under Data\Setup\Stream-Class\Subs-Attr
    # (verified by experiment). Only pure-statistic keys below are safe.
    "OV_ATTR": 0,
    "OV_D50": 0,
    "OV_D63": 0,
    "OV_DIAM": 0,
    "OV_DISTFUN": 0,
    "OV_FRACTION": 0,
    "OV_GGSN": 0,
    "OV_GMDEV": 0,
    "OV_RRSBN": 0,
    "OV_STDDEV": 0,
    "OV_STEEPNES": 0,
    "SUBBYPASS": 0,
    "SUB_D50": 0,
    "SUB_D63": 0,
    "SUB_DIAM": 0,
    "SUB_GGSN": 0,
    "SUB_GMDEV": 0,
    "SUB_RRSBN": 0,
    "SUB_STDDEV": 0,
    "SUB_STEEPNES": 0,
    "S_DISTFUN": 0,
    "S_FRACTION": 0,
    "USER_OVLOWER": 0,
    "USER_OVUPPER": 0,
    "USER_OVVALUE": 0,
    "USER_SLOWER": 0,
    "USER_SUPPER": 0,
    "USER_SVALUE": 0,
    "REBWIZ": "",
    "CHEMISTRY": "",
    "ENABLED": "",
    "PHASE": "",
    "PHYS_QTY": "",
    "PROPERTIES": "",
    "UOM": "",
    "FLASH_METHOD": "",
    "FVC_ID1": "",
    "FVC_ID2": "",
    "FVC_SENTENCE": "",
    "FVC_VARIABLE": "",
    "FVN": 0,
    "FVN_ELEM": 0,
    "FVN_ID1": 0,
    "FVN_ID2": 0,
    "FVN_ID3": 0,
    "FVN_SENTENCE": "",
    "FVN_VARIABLE": "",
    "FVN_VARTYPE": 0,
    "HENRY_COMPS": "",
}


def tool_fill_trivial_params(random_string: str | None = None,
                             objects: list[str] | None = None,
                             params: list[str] | None = None,
                             dry_run: bool = True) -> str:
    """Preview or fill trivial/meaningless active-but-unset params.

    Default dry_run=True: preview only, zero writes. An explicit write
    requires both scope lists (objects and params) and dry_run=False;
    candidates stay limited to the _TRIVIAL_PARAMS safe list.

    Fills use safe defaults (0 for numbers, "" for strings).
    Returns a report of candidates (preview) or of what was filled.
    """
    object_filter = {str(o) for o in objects} if objects else None
    param_filter = {str(p) for p in params} if params else None
    write_mode = (not dry_run) and object_filter is not None and param_filter is not None
    if not dry_run and not write_mode:
        return ("Error: dry_run=False 需要同时提供 objects 与 params 两类 scope；"
                "无作用域的全局填充已移除。默认 dry_run=True 仅预览。")

    def impl():
        tree = aspen._app.RootModel("")
        blks = walk(tree, "Data", "Blocks")
        if blks is None:
            return "No Blocks node found"
        
        lines = []
        total_filled = 0
        
        for bi in range(500):
            try:
                b = blks.Elements(bi)
            except Exception:
                break
            if b is None:
                break
            
            bname = b.Name
            if object_filter is not None and bname not in object_filter:
                continue
            inp = walk(tree, "Data", "Blocks", bname, "Input")
            if inp is None:
                continue
            
            filled = []
            for ii in range(500):
                try:
                    n = inp.Elements(ii)
                except Exception:
                    break
                if n is None:
                    break
                
                pname = n.Name
                if pname not in _TRIVIAL_PARAMS:
                    continue
                if param_filter is not None and pname not in param_filter:
                    continue
                
                # Only fill if active (i7=1) and not user-set (i11=0)
                try:
                    i7 = n.AttributeValue(7)
                    i11 = n.AttributeValue(11)
                except Exception:
                    continue
                
                if i7 != 1 or i11 != 0:
                    continue
                
                # Check if already has a value
                try:
                    if n.ValueType != 0 and n.Value is not None:
                        if n.Value != 0 and n.Value != "":
                            continue
                except Exception:
                    pass
                
                # Explicit scoped write, or preview candidate
                default_val = _TRIVIAL_PARAMS[pname]
                if write_mode:
                    try:
                        n.SetValue(0, default_val)
                    except Exception as exc:
                        filled.append(f"{pname} (write failed: {exc})")
                        continue
                else:
                    filled.append(pname)
                total_filled += 1
            
            if filled:
                lines.append(f"  [{bname}]: {', '.join(filled)}")
        
        if not lines:
            if write_mode:
                return "No trivial params found to fill."
            return "FILL PREVIEW (nothing written): no trivial params found."
        
        if write_mode:
            report = f"Filled {total_filled} trivial params across {len(lines)} blocks:\n"
        else:
            report = (f"FILL PREVIEW (nothing written) — {total_filled} trivial candidates "
                      f"across {len(lines)} blocks:\n")
        report += "\n".join(lines)
        if write_mode:
            report += "\n\nNow run find_incomplete_inputs() to see what's truly missing."
        else:
            report += "\n\nPreview only; pass objects+params with dry_run=False to write explicitly."
        return report
    
    return aspen.call(impl)


if __name__ == "__main__":
    # Allow direct call for testing
    result = tool_fill_trivial_params()
    print(result)
