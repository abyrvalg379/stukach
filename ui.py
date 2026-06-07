# -*- coding:utf-8 -*-
import bpy
from .manager import MeshCheck as MC
from .properties import CHECK_CATEGORIES

# ── Per-check severity ────────────────────────────────────────────────────────
# BLOCKER  → any count > 0 contributes to CRITICAL status
# WARNING  → any count > 0 contributes to WARNING status only
# INFO     → informational only; never affects object/asset status
# Threshold (CHECK_THRESHOLDS) is still used for per-check UI colour (yellow/red dot).

CHECK_SEVERITY: dict = {
    # ── BLOCKERS — real pipeline stoppers ────────────────────────────────────
    "non_manifold":          "BLOCKER",   # breaks subdivision, export, Boolean ops
    "z_fighting":            "BLOCKER",   # coplanar faces destroy render
    "duplicate_verts":       "BLOCKER",   # coincident verts break normals / simulations
    "zero_area":             "BLOCKER",   # degenerate faces → NaN normals, broken UV
    "non_applied_transform": "BLOCKER",   # rotation breaks export normals / physics
    "scale":                 "BLOCKER",   # non-unit scale breaks simulations / TD
    "uv_overlap":            "BLOCKER",   # breaks baking / lightmap
    "uv_udim_bounds":        "BLOCKER",   # island spans two UDIM tiles — breaks UDIM texturing
    "uv_material_udim":      "BLOCKER",   # mixed materials on one UDIM — breaks baking/automation
    "mat_assignment":        "BLOCKER",   # missing material slot = black render
    "missing_textures":      "BLOCKER",   # broken file reference

    # ── WARNINGS — artist must review before delivery ────────────────────────
    "isolated_verts":        "WARNING",   # cleanup noise
    "ngons":                 "WARNING",   # context-dependent but usually a problem
    "flipped_normals":       "WARNING",   # may be intentional (sky dome, double-sided)
    "invalid_normals":       "WARNING",   # custom normals issue
    "modifier_stack":        "WARNING",   # unapplied modifiers change exported geo
    "smooth_by_angle":       "WARNING",   # Smooth by Angle modifier missing / misconfigured
    "uv_single_set":         "WARNING",   # extra UV layers
    "uv_micro_shell":        "WARNING",   # tiny UV islands
    "uv_stretch":            "WARNING",   # angle distortion visible on textures
    "obj_naming":            "WARNING",   # naming convention
    "col_naming":            "WARNING",
    "mat_numbering":         "WARNING",   # Material.001 leftover names
    "mat_suffix":            "WARNING",   # material name convention
    "unused_data":           "WARNING",   # empty vgroups / leftover attributes

    # ── INFO — artist awareness, no pipeline impact ───────────────────────────
    "boundary_edges":        "INFO",      # open edges — may be intentional
    "face_aspect_ratio":     "INFO",      # elongated quads — artist judgement
    "triangles":             "INFO",      # midpoly workflow — artist review only
    "poles":                 "INFO",      # topology consideration
    "origin_at_zero":        "INFO",      # pivot off-origin — may be intentional
    "symmetry_x":            "INFO",      # symmetry check — informational
    "symmetry_y":            "INFO",
    "symmetry_z":            "INFO",
    "uv_texel_density":      "INFO",      # TD reference — informational unless target set
    "uv_padding":            "INFO",      # padding may vary intentionally per UDIM
}

# ── Per-check count thresholds for UI colour (yellow dot vs red dot) ──────────
# count == 0              → green
# 0 < count <= threshold  → yellow
# count >  threshold      → red
# (threshold 0 means any count > 0 = red immediately)
#
# triangles / ngons / poles are user-configurable in Preferences → Check Thresholds.
# All other checks default to 0 (any issue → red immediately).
CHECK_THRESHOLDS: dict = {
    "non_manifold":          0,
    "boundary_edges":        0,
    "isolated_verts":        0,
    "duplicate_verts":       0,
    "face_aspect_ratio":     0,
    "flipped_normals":       0,
    "zero_area":             0,
    "z_fighting":            0,
    "invalid_normals":       0,
    "triangles":             50,
    "ngons":                 10,
    "poles":                 20,
    "non_applied_transform": 0,
    "scale":                 0,
    "origin_at_zero":        0,
    "modifier_stack":        0,
    "smooth_by_angle":       0,
    "symmetry_x":            0,
    "symmetry_y":            0,
    "symmetry_z":            0,
    "uv_single_set":         0,
    "uv_overlap":            0,
    "uv_micro_shell":        0,
    "uv_texel_density":      0,
    "uv_stretch":            0,
    "uv_padding":            0,
    "uv_udim_bounds":        0,
    "uv_material_udim":      0,
    "obj_naming":            0,
    "col_naming":            0,
    "mat_numbering":         0,
    "mat_suffix":            0,
    "mat_assignment":        0,
    "missing_textures":      0,
    "unused_data":           0,
}

# Checks whose thresholds are user-configurable in Preferences
_PREFS_THRESHOLDS: frozenset = frozenset({'triangles', 'ngons', 'poles'})


def _get_threshold(check: str, prefs=None) -> int:
    """Return the yellow/red dot threshold for *check*.

    For triangles / ngons / poles the value is read from MeshCheckPreferences
    (passed in as *prefs* when available, otherwise fetched from bpy.context).
    All other checks use the hardcoded CHECK_THRESHOLDS table.
    """
    if check in _PREFS_THRESHOLDS:
        p = prefs
        if p is None:
            try:
                addon = __name__.split(".")[0]
                p = bpy.context.preferences.addons[addon].preferences
            except Exception:
                p = None
        if p is not None:
            return int(getattr(p, f"threshold_{check}",
                               CHECK_THRESHOLDS.get(check, 0)))
    return CHECK_THRESHOLDS.get(check, 0)


_STATUS_ICON = {
    "grey":   "RADIOBUT_OFF",
    "green":  "CHECKMARK",
    "yellow": "INFO",
    "red":    "ERROR",
}

_STATUS_TEXT = {
    "grey":   "",
    "green":  "",
    "yellow": "!",
    "red":    "!!",
}


def _get_check_count(mc_obj, check: str) -> int:
    checker = mc_obj._checks.get(check)
    if checker is None:
        return 0
    val = checker.count
    return int(val) if not callable(val) else 0


def get_category_status(mesh_check, category: str) -> str:
    active_checks = [
        c for c in CHECK_CATEGORIES.get(category, [])
        if getattr(mesh_check, c, False)
    ]
    if not active_checks:
        return "grey"

    total = sum(
        _get_check_count(mc_obj, check)
        for mc_obj in MC.objects.values()
        for check in active_checks
    )

    if total == 0:
        return "green"

    threshold = max(_get_threshold(c) for c in active_checks)
    return "red" if total > threshold else "yellow"


def _compute_asset_summary(mc) -> dict:
    """Aggregate issue counts per category.

    Returns per-category (blocker_count, warning_count) tuples and overall totals.
    Severity is taken from CHECK_SEVERITY; CHECK_THRESHOLDS is NOT used here —
    it only governs the per-check UI dot colour.
    """
    cat_counts: dict = {}
    total_blockers = total_warnings = 0

    for cat, checks in CHECK_CATEGORIES.items():
        cat_b = cat_w = 0
        for check in checks:
            if not getattr(mc, check, False):
                continue
            total = sum(_get_check_count(mc_obj, check) for mc_obj in MC.objects.values())
            if total == 0:
                continue
            sev = CHECK_SEVERITY.get(check, "WARNING")
            if sev == "BLOCKER":
                cat_b += total
            elif sev != "INFO":
                cat_w += total
        cat_counts[cat] = (cat_b, cat_w)
        total_blockers += cat_b
        total_warnings += cat_w

    return {
        "obj_count":       len(MC.objects),
        "total_blockers":  total_blockers,
        "total_warnings":  total_warnings,
        "total_issues":    total_blockers + total_warnings,
        "cat_counts":      cat_counts,
    }


def _get_object_status(mc_obj, mc) -> str:
    """Returns 'critical', 'warning', or 'clean' for a single tracked object."""
    has_blocker = has_warning = False
    for cat_checks in CHECK_CATEGORIES.values():
        for check in cat_checks:
            if not getattr(mc, check, False):
                continue
            count = _get_check_count(mc_obj, check)
            if count == 0:
                continue
            sev = CHECK_SEVERITY.get(check, "WARNING")
            if sev == "BLOCKER":
                has_blocker = True
            elif sev != "INFO":
                has_warning = True
    if has_blocker:
        return "critical"
    if has_warning:
        return "warning"
    return "clean"


def _get_asset_status(mc) -> str:
    """Returns 'none', 'ready', 'warning', or 'critical'.

    CRITICAL requires at least one BLOCKER check to have count > 0.
    WARNING checks (triangles, ngons, etc.) never escalate to CRITICAL.
    """
    if not MC.objects:
        return "none"

    has_any_active = False
    has_blocker    = False
    has_warning    = False

    for cat_checks in CHECK_CATEGORIES.values():
        for check in cat_checks:
            if not getattr(mc, check, False):
                continue
            has_any_active = True
            total = sum(
                _get_check_count(mc_obj, check)
                for mc_obj in MC.objects.values()
            )
            if total == 0:
                continue
            sev = CHECK_SEVERITY.get(check, "WARNING")
            if sev == "BLOCKER":
                has_blocker = True
            elif sev != "INFO":
                has_warning = True

    if not has_any_active:
        return "none"
    if has_blocker:
        return "critical"
    if has_warning:
        return "warning"
    return "ready"


def _draw_delta_row(layout, label: str, was: int, now: int, *,
                    icon: str = "NONE",
                    is_new: bool = False,
                    is_gone: bool = False,
                    big: bool = False,
                    blocker: bool = False) -> None:
    """One comparison row: label  |  was → now  delta-badge."""
    row = layout.row(align=True)
    if big:
        row.scale_y = 1.1

    # Label column (left, stretches)
    lbl = row.row()
    lbl.alignment = "LEFT"

    if is_gone:
        lbl.enabled = False
        lbl.label(text=label, icon=icon if icon != "NONE" else "OBJECT_DATA")
    elif is_new:
        lbl.alert = (now > 0)
        lbl.label(text=f"[NEW]  {label}", icon=icon if icon != "NONE" else "OBJECT_DATA")
    else:
        lbl.label(text=label, icon=icon)

    # Delta column (right, fixed)
    right = row.row()
    right.alignment = "RIGHT"

    if is_gone:
        right.enabled = False
        right.label(text=f"{was} → —")
        return

    if is_new:
        right.alert = (now > 0)
        right.label(text=f"→ {now}",
                    icon="TRIA_UP" if now > 0 else "CHECKMARK")
        return

    delta = now - was
    if delta < 0:
        # Improved — no alert (shows as normal/positive)
        right.label(text=f"{was} → {now}  ↓{abs(delta)}", icon="TRIA_DOWN_BAR")
    elif delta > 0:
        right.alert = True
        right.label(text=f"{was} → {now}  ↑{delta}", icon="TRIA_UP_BAR")
    else:
        # Unchanged
        if now == 0:
            right.enabled = False
            right.label(text="clean", icon="CHECKMARK")
        else:
            right.label(text=f"{was} → {now}  =", icon="REMOVE")


def draw_coordinator_panel(layout, mc, context) -> None:
    """Full Coordinator Mode view — checkpoint vs current validation delta."""
    import json as _json
    from .properties import _compute_current_results, _AC_CHECKPOINT_KEY

    # ── Header ────────────────────────────────────────────────────────────────
    hdr = layout.row(align=True)
    hdr.prop(mc, "coordinator_mode",
             text="", icon="TRIA_LEFT", emboss=False)
    hdr.label(text="Coordinator Mode", icon="COMMUNITY")

    # ── Checkpoint controls ───────────────────────────────────────────────────
    raw = context.scene.get(_AC_CHECKPOINT_KEY)
    ctrl = layout.row(align=True)
    ctrl.operator("asset_checker.save_checkpoint",
                  text="Save Checkpoint" if not raw else "Update Checkpoint",
                  icon="BOOKMARKS")
    ctrl.operator("asset_checker.load_checkpoint",
                  text="", icon="IMPORT")
    if raw:
        ctrl.operator("asset_checker.clear_checkpoint",
                      text="", icon="X", emboss=False)

    layout.separator(factor=0.5)

    if not raw:
        info = layout.box()
        info.label(text="No checkpoint saved yet.", icon="INFO")
        col = info.column(align=True)
        col.scale_y = 0.8
        col.enabled = False
        col.label(text="1. Run validation (Run STUKACH)")
        col.label(text="2. Press  Save Checkpoint")
        col.label(text="3. Artist iterates, you re-run")
        col.label(text="4. Coordinator Mode shows delta")
        return

    # ── Parse checkpoint ──────────────────────────────────────────────────────
    try:
        cp = _json.loads(raw)
    except Exception:
        layout.label(text="Checkpoint data corrupted — clear and re-save", icon="ERROR")
        return

    # Timestamp badge — show source (live session vs loaded from JSON file)
    ts = layout.row()
    ts.scale_y = 0.75
    ts.enabled = False
    src_icon = "IMPORT" if cp.get("_source") == "report" else "BOOKMARKS"
    src_hint = "  (from JSON report)" if cp.get("_source") == "report" else ""
    ts.label(text=f"  Checkpoint: {cp.get('timestamp', '?')}{src_hint}",
             icon=src_icon)

    if not MC.objects:
        warn = layout.box()
        warn.label(text="Run validation to compare vs checkpoint", icon="PLAY")
        # Show checkpoint data as reference
        warn.separator(factor=0.3)
        warn.label(text=f"Last checkpoint: {cp['totals'].get('total', '?')} issues  "
                        f"({cp['totals'].get('blockers', '?')} blockers)")
        return

    # ── Compute current ───────────────────────────────────────────────────────
    cur      = _compute_current_results(mc)
    cp_tot   = cp.get("totals", {})
    cur_tot  = cur["totals"]
    cp_objs  = cp.get("objects", {})
    cur_objs = cur["objects"]

    # ── Totals box ────────────────────────────────────────────────────────────
    tot_box = layout.box()
    tot_box.label(text="Summary", icon="LINENUMBERS_ON")
    _draw_delta_row(tot_box, "Total issues",
                    cp_tot.get("total", 0), cur_tot["total"],
                    icon="ERROR", big=True)
    _draw_delta_row(tot_box, "Blockers",
                    cp_tot.get("blockers", 0), cur_tot["blockers"],
                    icon="CANCEL", big=True)

    # ── Per-category ──────────────────────────────────────────────────────────
    cat_box = layout.box()
    cat_box.label(text="By category", icon="COLLAPSEMENU")
    cp_by_cat  = cp_tot.get("by_category", {})
    cur_by_cat = cur_tot["by_category"]

    from .properties import CHECK_CATEGORIES, _CAT_ICONS
    any_cat = False
    for cat, checks in CHECK_CATEGORIES.items():
        cp_v  = cp_by_cat.get(cat, 0)
        cur_v = cur_by_cat.get(cat, 0)
        if cp_v == 0 and cur_v == 0:
            continue
        any_cat = True
        _draw_delta_row(cat_box, cat.capitalize(),
                        cp_v, cur_v,
                        icon=_CAT_ICONS.get(cat, "NONE"))
    if not any_cat:
        r = cat_box.row()
        r.enabled = False
        r.label(text="No active checks with issues", icon="CHECKMARK")

    # ── Per-object ────────────────────────────────────────────────────────────
    obj_box = layout.box()
    obj_box.label(text="By object", icon="OBJECT_DATA")

    all_names = sorted(set(list(cp_objs.keys()) + list(cur_objs.keys())))
    if not all_names:
        r = obj_box.row()
        r.enabled = False
        r.label(text="No objects", icon="CHECKMARK")
    else:
        for name in all_names:
            is_new  = name not in cp_objs
            is_gone = name not in cur_objs
            cp_v    = cp_objs.get(name, {}).get("_total", 0)
            cur_v   = cur_objs.get(name, {}).get("_total", 0)
            short   = name if len(name) <= 24 else name[:21] + "…"
            _draw_delta_row(obj_box, short, cp_v, cur_v,
                            is_new=is_new, is_gone=is_gone)


def draw_hierarchy_block(layout, mc):
    """Pipeline hierarchy validator — collapsible sub-section.

    Renders inside the Naming category box (called from draw_options()).
    *mc* is MeshCheckProperties (WindowManager.mesh_check_props).

    Sections:
      • Header row: collapse toggle · "Hierarchy" label · Scan/Re-scan · X
      • (when expanded) Summary · Issue list · Hierarchy Tree
    """
    from .naming import (
        HierarchyResult,
        _ROLE_ASSET_ROOT,
        _ROLE_ICONS,
        INFO, WARNING, ERROR, SEVERITY_ICON,
    )
    from .manager import MeshCheck

    result: HierarchyResult = MeshCheck.hierarchy_result

    # ── Collapsible header ────────────────────────────────────────────────────
    hdr = layout.row(align=True)
    tria = "TRIA_DOWN" if mc.hierarchy_block_open else "TRIA_RIGHT"
    hdr.prop(mc, "hierarchy_block_open", text="", icon=tria, emboss=False)
    hdr.label(text="Hierarchy", icon="EMPTY_AXIS")

    # Scan / Re-scan + Clear on the right side of the header
    right = hdr.row(align=True)
    right.alignment = "RIGHT"
    if result is None:
        right.operator("asset_checker.scan_hierarchy", text="Scan", icon="PLAY")
    else:
        right.operator("asset_checker.scan_hierarchy", text="", icon="FILE_REFRESH")
        right.operator("asset_checker.clear_hierarchy", text="", icon="X", emboss=False)

    if not mc.hierarchy_block_open:
        # Collapsed — show one-line status badge
        if result is not None:
            badge = layout.row(align=True)
            badge.scale_y = 0.75
            e, w = result.error_count, result.warning_count
            if result.is_clean():
                badge.label(text=f"  Clean  ·  {len(result.asset_roots)} root(s)", icon="CHECKMARK")
            else:
                if e:
                    badge.label(text=f"  {e} error(s)", icon=SEVERITY_ICON[ERROR])
                if w:
                    badge.label(text=f"  {w} warning(s)", icon=SEVERITY_ICON[WARNING])
        return

    # ── Expanded content ──────────────────────────────────────────────────────

    if result is None:
        layout.label(text="Press Scan to validate hierarchy", icon="INFO")
        return

    # Summary row
    e, w = result.error_count, result.warning_count
    n_roots = len(result.asset_roots)

    summ = layout.row(align=True)
    if result.is_clean():
        summ.label(
            text=f"Clean  ·  {n_roots} root(s)  ·  {result.objects_scanned} obj",
            icon="CHECKMARK",
        )
    else:
        if e:
            summ.label(text=f"{e} error(s)", icon=SEVERITY_ICON[ERROR])
        if w:
            summ.label(text=f"{w} warning(s)", icon=SEVERITY_ICON[WARNING])
        right2 = summ.row()
        right2.alignment = "RIGHT"
        right2.label(text=f"{n_roots} root(s)  ·  {result.objects_scanned} obj")

    # ── Issue list ────────────────────────────────────────────────────────────
    if not result.is_clean():
        for sev in (ERROR, WARNING):
            group = [i for i in result.issues if i.severity == sev]
            if not group:
                continue
            sev_col = layout.column(align=True)
            sev_col.alert = (sev == ERROR)
            sev_col.label(text=sev, icon=SEVERITY_ICON[sev])
            for issue in group:
                row = sev_col.row(align=True)
                role_icon = _ROLE_ICONS.get(issue.role, "DOT")
                nm = issue.obj_name
                nm_short = nm if len(nm) <= 20 else nm[:17] + "…"
                msg = (issue.message if len(issue.message) <= 36
                       else issue.message[:33] + "…")
                row.label(text=f"  {nm_short}  —  {msg}", icon=role_icon)
                if nm != "[scene]" and bpy.data.objects.get(nm):
                    op = row.operator(
                        "asset_checker.select_object",
                        text="", icon="RESTRICT_SELECT_OFF", emboss=False,
                    )
                    op.object_name = nm

    # ── Hierarchy Tree (collapsible) ──────────────────────────────────────────
    MAX_TREE_NODES = 150

    tree_hdr = layout.row(align=True)
    tria2 = "TRIA_DOWN" if mc.hierarchy_tree_open else "TRIA_RIGHT"
    tree_hdr.prop(mc, "hierarchy_tree_open", text="", icon=tria2, emboss=False)
    tree_hdr.label(text="Hierarchy Tree", icon="OUTLINER")

    if mc.hierarchy_tree_open:
        if result.objects_scanned > MAX_TREE_NODES:
            layout.label(
                text=f"Tree disabled: {result.objects_scanned} objects (limit {MAX_TREE_NODES})",
                icon="INFO",
            )
        elif not result.asset_roots:
            layout.label(text="No asset roots found", icon="QUESTION")
        else:
            def _draw_subtree(col, name, depth, role):
                obj_issues = result.issues_for(name)
                n_iss = sum(1 for i in obj_issues if i.severity in (WARNING, ERROR))
                node_icon  = _ROLE_ICONS.get(role, "DOT")
                issue_icon = ("ERROR" if any(i.severity == ERROR for i in obj_issues)
                              else ("INFO" if n_iss else "BLANK1"))
                indent = "   " * depth
                label_text = f"{indent}{name}"
                if len(label_text) > 34:
                    label_text = label_text[:31] + "…"
                r = col.row(align=True)
                r.label(text=label_text, icon=node_icon)
                if n_iss:
                    r.label(text="", icon=issue_icon)
                if bpy.data.objects.get(name):
                    op = r.operator(
                        "asset_checker.select_object",
                        text="", icon="RESTRICT_SELECT_OFF", emboss=False,
                    )
                    op.object_name = name
                for child in sorted(result.children_of.get(name, [])):
                    _draw_subtree(col, child, depth + 1,
                                  result.node_roles.get(child, ""))

            tree_col = layout.column(align=True)
            for root_name in result.asset_roots:
                _draw_subtree(tree_col, root_name, 0, _ROLE_ASSET_ROOT)


def _draw_ignore_list_block(layout, mc) -> None:
    """Collapsible 'Ignored Issues' block — shows all per-object ignores.

    Only rendered when at least one tracked object has ignored checks.
    Each row: object name · ignored check names · [X] clear.
    Header: total count · [Clear All].
    """
    from .properties import get_obj_ignore_list, _CHECK_LABELS, _AC_IGNORE_KEY

    # Collect (name, obj, ignored_set) for all tracked objects that have ignores
    objects_with_ignores = []
    for obj in list(MC.objects.keys()):
        try:
            name = obj.name
        except ReferenceError:
            continue
        ignored = get_obj_ignore_list(obj)
        if ignored:
            objects_with_ignores.append((name, obj, ignored))

    if not objects_with_ignores:
        return   # nothing to show — block disappears automatically

    total_count = sum(len(ig) for _, _, ig in objects_with_ignores)

    box = layout.box()

    # ── Header ────────────────────────────────────────────────────────────────
    hdr = box.row(align=True)
    tria = "TRIA_DOWN" if mc.ignore_list_open else "TRIA_RIGHT"
    hdr.prop(mc, "ignore_list_open", text="", icon=tria, emboss=False)
    hdr.label(
        text=f"Ignored Issues  ({total_count})",
        icon="HIDE_ON",
    )
    # Clear-all button sits on the right of the header
    hdr.operator(
        "asset_checker.clear_all_ignores",
        text="", icon="X", emboss=False,
    )

    if not mc.ignore_list_open:
        return

    # ── Per-object rows ───────────────────────────────────────────────────────
    col = box.column(align=True)
    for obj_name, obj, ignored in sorted(objects_with_ignores, key=lambda x: x[0]):
        row = col.row(align=True)

        # Object name
        name_part = row.row(align=True)
        name_part.alignment = "LEFT"
        name_part.label(
            text=f"{obj_name}:",
            icon="OBJECT_DATA",
        )

        # Ignored check labels (human-readable)
        check_labels = [
            _CHECK_LABELS.get(c, c.replace('_', ' ').title())
            for c in sorted(ignored)
        ]
        chips = row.row(align=True)
        chips.enabled = False
        chips.label(text=", ".join(check_labels))

        # Per-object clear button
        op = row.operator(
            "asset_checker.clear_ignore_object",
            text="", icon="X", emboss=False,
        )
        op.obj_name = obj_name

    # Hint at the bottom
    hint = box.row()
    hint.scale_y = 0.7
    hint.enabled = False
    hint.label(
        text="Ignored issues are excluded from the pipeline report",
        icon="INFO",
    )


def draw_naming_audit_block(layout, mc) -> None:
    """Naming Audit block — scene-wide check, collapsible.

    Renders inside the Naming category box (called from draw_options()).
    *mc* is MeshCheckProperties (WindowManager.mesh_check_props).

    Sections:
      • Header row: collapse toggle · "Naming Audit" label · Run/Re-run · X
      • (collapsed) one-line status badge
      • (expanded) error/warning list with click-to-select
    """
    from .naming import NamingAudit, INFO, WARNING, ERROR, SEVERITY_ICON

    if mc is None:
        try:
            mc = bpy.context.window_manager.mesh_check_props
        except Exception:
            return

    # ── Collapsible header ────────────────────────────────────────────────────
    hdr = layout.row(align=True)
    tria = "TRIA_DOWN" if mc.naming_audit_open else "TRIA_RIGHT"
    hdr.prop(mc, "naming_audit_open", text="", icon=tria, emboss=False)
    hdr.label(text="Naming Audit", icon="VIEWZOOM")

    right = hdr.row(align=True)
    right.alignment = "RIGHT"
    if not NamingAudit._ran:
        right.operator("asset_checker.run_naming_audit", text="Run", icon="PLAY")
    else:
        right.operator("asset_checker.run_naming_audit", text="", icon="FILE_REFRESH")
        right.operator("asset_checker.clear_naming_audit", text="", icon="X", emboss=False)

    if not mc.naming_audit_open:
        # Collapsed — one-line status badge
        if NamingAudit._ran:
            badge = layout.row(align=True)
            badge.scale_y = 0.75
            if NamingAudit.is_clean():
                badge.label(text="  Scene naming is clean", icon="CHECKMARK")
            else:
                e, w = NamingAudit.error_count(), NamingAudit.warning_count()
                if e:
                    badge.label(text=f"  {e} error(s)", icon=SEVERITY_ICON[ERROR])
                if w:
                    badge.label(text=f"  {w} warning(s)", icon=SEVERITY_ICON[WARNING])
        return

    # ── Expanded content ──────────────────────────────────────────────────────
    if not NamingAudit._ran:
        layout.label(text="Press Run to scan scene naming", icon="INFO")
        return

    if NamingAudit.is_clean():
        layout.label(text="Scene naming is clean", icon="CHECKMARK")
        return

    e, w = NamingAudit.error_count(), NamingAudit.warning_count()
    summary = layout.row(align=True)
    if e:
        summary.label(text=f"{e} error(s)", icon=SEVERITY_ICON[ERROR])
    if w:
        summary.label(text=f"{w} warning(s)", icon=SEVERITY_ICON[WARNING])

    for sev in (ERROR, WARNING, INFO):
        group = [r for r in NamingAudit._results if r.severity == sev]
        if not group:
            continue
        sev_col = layout.column(align=True)
        sev_col.alert = (sev == ERROR)
        sev_col.label(text=sev, icon=SEVERITY_ICON[sev])
        for result in group:
            row = sev_col.row(align=True)
            nm = result.object_name
            nm_short = nm if len(nm) <= 22 else nm[:19] + "…"
            msg = result.message if len(result.message) <= 30 else result.message[:27] + "…"
            row.label(text=f"   {nm_short}  —  {msg}")
            if result.check == "obj_naming":
                op = row.operator(
                    "asset_checker.select_object",
                    text="", icon="RESTRICT_SELECT_OFF", emboss=False,
                )
                op.object_name = result.object_name


def _draw_scene_units_row(layout, mc) -> None:
    """Scene Units inline check — drawn above the categories box.

    Toggle sits on the left; when enabled, shows OK (green) or issues (red).
    ERROR severity: any deviation from METRIC/METERS/scale_length=1.0.
    """
    from .core import check_scene_units

    row = layout.row(align=True)
    icon = "CHECKBOX_HLT" if mc.scene_units else "CHECKBOX_DEHLT"
    row.prop(mc, "scene_units", icon=icon, emboss=False, text="Scene Units")

    if not mc.scene_units:
        return

    result = check_scene_units()
    status = row.row(align=True)
    status.alignment = "RIGHT"
    if result['ok']:
        status.label(text="METRIC · m · 1.0", icon="CHECKMARK")
    else:
        status.alert = True
        status.label(text="  ·  ".join(result['issues']), icon="ERROR")


class ASSET_CHECKER_PT_Panel(bpy.types.Panel):
    bl_label = "STUKACH"
    bl_idname = "ASSET_CHECKER_PT_Panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "STUKACH"

    @classmethod
    def poll(cls, context):
        return True

    @staticmethod
    def _draw_score_block(layout, mc):
        """Compact asset summary: scope badge · obj count · issue count · per-category."""
        if not MC.objects:
            layout.box().label(text="Awaiting suspects.", icon="GHOST_ENABLED")
            return

        summary = _compute_asset_summary(mc)
        status  = _get_asset_status(mc)

        _st_icon = {
            "ready":    "CHECKMARK",
            "warning":  "INFO",
            "critical": "ERROR",
            "none":     "RADIOBUT_OFF",
        }

        scope = MC._scope
        if scope == "COLLECTION":
            scope_label = MC._scope_collection or "COLLECTION"
        elif scope == "SCENE":
            scope_label = "SCENE"
        else:
            scope_label = "SELECTION"

        box = layout.box()
        box.alert = (status == "critical")

        # ── Row 1: icon · obj count · blocker + warning counts · scope ──────
        row = box.row(align=True)
        n_b = summary["total_blockers"]
        n_w = summary["total_warnings"]
        if n_b and n_w:
            count_text = f"{summary['obj_count']} obj  ·  {n_b} block  {n_w} warn"
        elif n_b:
            count_text = f"{summary['obj_count']} obj  ·  {n_b} blockers"
        elif n_w:
            count_text = f"{summary['obj_count']} obj  ·  {n_w} warnings"
        else:
            count_text = f"{summary['obj_count']} obj  ·  clean"
        row.label(text=count_text, icon=_st_icon.get(status, "RADIOBUT_OFF"))
        badge = row.row()
        badge.alignment = "RIGHT"
        badge.label(text=f"[ {scope_label} ]")

        # ── Row 2: per-category — red = has blockers, yellow = only warnings ─
        cats_row = box.row(align=True)
        cats_row.scale_y = 0.75
        for cat, (cat_b, cat_w) in summary["cat_counts"].items():
            total = cat_b + cat_w
            icon  = "ERROR" if cat_b else ("INFO" if cat_w else "CHECKMARK")
            cats_row.label(text=f"{cat[:4]}: {total}", icon=icon)

    @staticmethod
    def _draw_object_details(ob_box, obj, mc_obj, mc):
        from .properties import get_obj_ignore_list

        # Resolve prefs locally (static method has no access to outer draw() scope)
        addon_name = __name__.split(".")[0]
        try:
            prefs = bpy.context.preferences.addons[addon_name].preferences
        except Exception:
            prefs = None

        verts, edges, faces, tris = mc_obj.stats
        ob_box.label(text=f"V: {verts}  E: {edges}  F: {faces}  T: {tris}")

        stats_row = ob_box.row()
        split = stats_row.split(factor=0.02)
        split.separator()
        c1, c2 = split.column(), split.column()

        ignored_checks = get_obj_ignore_list(obj)

        active_checks = [
            (check, mc_obj._checks.get(check))
            for checks in CHECK_CATEGORIES.values()
            for check in checks
            if getattr(mc, check, False) and mc_obj._checks.get(check) is not None
        ]

        for i, (check, checker) in enumerate(active_checks):
            col = c1 if i % 2 == 0 else c2

            # ── Ignored check row ──────────────────────────────────────────
            if check in ignored_checks:
                row = col.row(align=True)
                # Label side — greyed out
                lbl = row.row(align=True)
                lbl.enabled = False
                lbl.label(
                    text=check.replace('_', ' ').title(),
                    icon="HIDE_ON",
                )
                # Un-ignore button — always enabled
                op = row.operator(
                    "asset_checker.toggle_ignore",
                    text="", icon="HIDE_OFF", emboss=False,
                )
                op.obj_name   = obj.name
                op.check_name = check
                continue

            # ── Normal check row ───────────────────────────────────────────
            count     = _get_check_count(mc_obj, check)
            threshold = _get_threshold(check, prefs)

            if count == 0:
                icon = "CHECKMARK"
            elif count > threshold:
                icon = "ERROR"
            else:
                icon = "INFO"

            mt    = getattr(checker, 'metric_text', '')
            label = mt if mt else f"{check.replace('_', ' ').title()}: {count}"

            row = col.row(align=True)
            row.label(text=label, icon=icon)

            if count > 0:
                # Select-elements button (where applicable)
                if hasattr(checker, 'get_select_data'):
                    element_type, _ = checker.get_select_data()
                    if element_type is not None:
                        op = row.operator(
                            "asset_checker.select_check_elements",
                            text="", icon="RESTRICT_SELECT_OFF", emboss=False,
                        )
                        op.obj_name   = obj.name
                        op.check_name = check

                # Ignore button — only when there's an actual problem to suppress
                op = row.operator(
                    "asset_checker.toggle_ignore",
                    text="", icon="HIDE_ON", emboss=False,
                )
                op.obj_name   = obj.name
                op.check_name = check

    @staticmethod
    def _draw_asset_status(layout, mc):
        """Asset Status block — pipeline gate summary at bottom of panel."""
        status = _get_asset_status(mc)

        box = layout.box()
        box.alert = (status == "critical")

        if status == "none":
            box.label(text="No active checks.", icon="RADIOBUT_OFF")
            return

        if status == "ready":
            box.label(text="ASSET STATUS: PIPELINE READY", icon="CHECKMARK")
            box.label(text="Стукач считает ассет production-ready")
        elif status == "warning":
            box.label(text="ASSET STATUS: WARNING", icon="INFO")
            box.label(text="Стукач советует еще поработать")
        elif status == "critical":
            box.label(text="ASSET STATUS: CRITICAL", icon="ERROR")
            box.label(text="Стукач блокирует publish")

    @staticmethod
    def _draw_hierarchy_block(layout, mc):
        """Deprecated shim — delegates to module-level draw_hierarchy_block()."""
        draw_hierarchy_block(layout, mc)

    @staticmethod
    def _draw_naming_audit(layout, mc=None):
        """Deprecated shim — delegates to module-level draw_naming_audit_block()."""
        draw_naming_audit_block(layout, mc)

    @staticmethod
    def _draw_naming_issues(layout, mc):
        """Severity-grouped 'Invalid Naming' block with click-to-select."""
        from .naming import INFO, WARNING, ERROR, SEVERITY_ICON

        all_results = []
        seen_cols: set = set()   # deduplicate collection issues across tracked objects

        for mc_obj in MC.objects.values():
            if mc.obj_naming:
                checker = mc_obj._checks.get("obj_naming")
                if checker and hasattr(checker, "results"):
                    all_results.extend(checker.results)
            if mc.col_naming:
                checker = mc_obj._checks.get("col_naming")
                if checker and hasattr(checker, "results"):
                    for r in checker.results:
                        if r.object_name not in seen_cols:
                            seen_cols.add(r.object_name)
                            all_results.append(r)

        if not all_results:
            return

        box = layout.box()
        box.label(
            text=f"Invalid Naming  ({len(all_results)} issue{'s' if len(all_results) != 1 else ''})",
            icon="ERROR",
        )

        for sev in (ERROR, WARNING, INFO):
            group = [r for r in all_results if r.severity == sev]
            if not group:
                continue
            sev_col = box.column(align=True)
            sev_col.alert = (sev == ERROR)
            sev_col.label(text=sev, icon=SEVERITY_ICON[sev])
            for result in group:
                row = sev_col.row(align=True)
                msg = result.message if len(result.message) <= 42 else result.message[:39] + "…"
                row.label(text=f"   {result.object_name}  —  {msg}")
                # select button only makes sense for object-level issues
                if result.check == "obj_naming":
                    op = row.operator(
                        "asset_checker.select_object",
                        text="", icon="RESTRICT_SELECT_OFF", emboss=False,
                    )
                    op.object_name = result.object_name

    def draw(self, context):
        layout = self.layout
        mc = context.window_manager.mesh_check_props

        addon_name = __name__.split(".")[0]
        try:
            prefs = context.preferences.addons[addon_name].preferences
        except Exception:
            prefs = None

        # ── Main action button ───────────────────────────────────────────────
        sub = layout.row()
        sub.scale_y = 0.55
        sub.label(text="pipeline snitch system")

        btn_text = "STUKACH ACTIVE" if mc.show_overlay else "RUN STUKACH"
        btn_icon = "RADIOBUT_ON"    if mc.show_overlay else "PLAY"
        run_row = layout.row(align=True)
        run_row.scale_y = 1.3
        run_row.prop(mc, "show_overlay", text=btn_text, toggle=True, icon=btn_icon)
        # Coordinator Mode toggle — small button on the right of Run
        coord_icon = "COMMUNITY"
        run_row.prop(mc, "coordinator_mode",
                     text="", icon=coord_icon, toggle=True)

        # ── Branch: Coordinator Mode ─────────────────────────────────────────
        if mc.coordinator_mode:
            draw_coordinator_panel(layout, mc, context)
            return

        # ── "Settings restored" hint ─────────────────────────────────────────
        if MC._state_restored and not mc.show_overlay:
            hint = layout.row()
            hint.scale_y = 0.75
            hint.label(text="  Settings restored — press Run to revalidate",
                       icon="RECOVER_LAST")

        # ── "Scene stale" hint ───────────────────────────────────────────────
        if MC._scene_stale and mc.show_overlay:
            stale = layout.row()
            stale.scale_y = 0.75
            stale.label(text="  Scene changed — re-run to include new objects",
                        icon="FILE_REFRESH")

        # ── Score block (replaces status bar) ───────────────────────────────
        self._draw_score_block(layout, mc)

        # ── Scope buttons ────────────────────────────────────────────────────
        scope_row = layout.row(align=True)
        scope_row.operator("asset_checker.validate_scene",      text="Scene",      icon="WORLD")
        scope_row.operator("asset_checker.validate_collection", text="Collection", icon="OUTLINER_COLLECTION")
        scope_row.operator("asset_checker.clear_validation",    text="",           icon="X")

        # ── Scene Units check (scene-level, not per-object) ──────────────────
        _draw_scene_units_row(layout, mc)

        box = layout.box()
        box.label(text="Pipeline Checks:", icon="FILE_TEXT")
        mc.draw_options(box)

        if prefs:
            off_row = box.row(align=True)
            off_row.scale_y = 0.8
            off_row.prop(prefs, "faces_offset", text="Face Offset")
            off_row.prop(prefs, "points_offset", text="Point Offset")

        if not MC.objects:
            return

        # ── Collapsible object-list section header ────────────────────────────
        n_obj    = len(MC.objects)
        n_issues = 0
        for _obj, _mc_obj in MC.objects.items():
            try:
                _obj.name  # probe — raises ReferenceError if removed
            except ReferenceError:
                continue
            if _get_object_status(_mc_obj, mc) != "clean":
                n_issues += 1

        sec_box  = layout.box()
        sec_head = sec_box.row(align=True)

        tria_sec = "TRIA_DOWN" if mc.obj_list_open else "TRIA_RIGHT"
        sec_head.prop(mc, "obj_list_open", text="", icon=tria_sec, emboss=False)

        # Label: "Objects  18   ⚠ 3"
        sec_head.label(text="Objects", icon="OBJECT_DATA")
        cnt = sec_head.row()
        cnt.alignment = "RIGHT"
        cnt.label(text=str(n_obj))
        if n_issues:
            cnt.label(text=str(n_issues), icon="ERROR")

        if mc.obj_list_open:
            # ── Pre-compute visible objects (filter) ──────────────────────────
            filter_text = mc.obj_filter_text.lower().strip()
            errors_only = mc.obj_filter_errors_only

            visible = []
            for obj, mc_obj in MC.objects.items():
                try:
                    obj_name = obj.name
                except ReferenceError:
                    continue
                if filter_text and filter_text not in obj_name.lower():
                    continue
                obj_status = _get_object_status(mc_obj, mc)
                if errors_only and obj_status == "clean":
                    continue
                visible.append((obj, mc_obj, obj_name, obj_status))

            # ── Filter bar ────────────────────────────────────────────────────
            filt_row = sec_box.row(align=True)
            filt_row.prop(mc, "obj_filter_text",        text="", icon="VIEWZOOM")
            filt_row.prop(mc, "obj_filter_errors_only", text="Issues only", icon="FILTER", toggle=True)

            count_sub = filt_row.row()
            count_sub.alignment = "RIGHT"
            count_sub.label(text=f"{len(visible)} / {n_obj}")

            def _stat(o):
                try:
                    return bool(o.mesh_check_statistics)
                except ReferenceError:
                    return False

            any_open = any(_stat(o) for o in MC.objects)
            col_text = "Collapse All" if any_open else "Expand All"
            col_icon = "TRIA_RIGHT"   if any_open else "TRIA_DOWN"
            sec_box.operator("asset_checker.collapse_objects", text=col_text, icon=col_icon)

            # ── Object list ───────────────────────────────────────────────────
            _BADGE = {"critical": "ERROR", "warning": "INFO", "clean": "CHECKMARK"}

            from .properties import get_obj_ignore_list

            for obj, mc_obj, obj_name, obj_status in visible:
                try:
                    stat = obj.mesh_check_statistics
                except ReferenceError:
                    continue

                n_ignored = len(get_obj_ignore_list(obj))

                ob_box = sec_box.box()
                r_name = ob_box.row()

                # Adjust split factor when ignore badge is shown
                split_f = 0.78 if (n_ignored and not stat) else 0.88
                split   = r_name.split(factor=split_f)

                left = split.row(align=True)
                left.alignment = "LEFT"
                tria = "TRIA_DOWN" if stat else "TRIA_RIGHT"
                left.prop(obj, "mesh_check_statistics", text=obj_name, icon=tria, emboss=False)

                right = split.row(align=True)
                right.alignment = "RIGHT"

                # Ignore badge — only on collapsed rows so it doesn't duplicate
                # the per-check HIDE icons already visible in the expanded view
                if n_ignored and not stat:
                    ign_badge = right.row(align=True)
                    ign_badge.enabled = False
                    ign_badge.label(text=str(n_ignored), icon="HIDE_ON")

                right.label(text="", icon=_BADGE[obj_status])

                if stat:
                    self._draw_object_details(ob_box, obj, mc_obj, mc)

        # Ignored Issues block — shown only when there are active ignores
        _draw_ignore_list_block(layout, mc)

        # Invalid Naming block — shown when any naming check is active
        if mc.obj_naming or mc.col_naming:
            self._draw_naming_issues(layout, mc)

        # Asset Status — bottom-of-panel pipeline verdict
        self._draw_asset_status(layout, mc)

        # Export block
        if MC.objects:
            try:
                exp_box = layout.box()

                # Report export
                exp_row = exp_box.row(align=True)
                exp_row.label(text="Export:", icon="EXPORT")
                for fmt, lbl in (('JSON', 'JSON'), ('CSV', 'CSV'), ('HTML', 'HTML')):
                    op = exp_row.operator("asset_checker.export_report", text=lbl)
                    op.fmt = fmt

                # Pre-flight export
                pf_row = exp_box.row(align=True)
                pf_row.label(text="Pre-flight:", icon="CHECKMARK")
                op_fbx = pf_row.operator("asset_checker.preflight_export",
                                         text="FBX", icon="EXPORT")
                op_fbx.fmt = 'FBX'
                op_usd = pf_row.operator("asset_checker.preflight_export",
                                         text="USD", icon="EXPORT")
                op_usd.fmt = 'USD'

                # Coordinator checkpoint
                from .properties import _AC_CHECKPOINT_KEY
                cp_exists = bool(context.scene.get(_AC_CHECKPOINT_KEY))
                cp_row = exp_box.row(align=True)
                cp_row.label(text="Checkpoint:", icon="BOOKMARKS")
                cp_row.operator(
                    "asset_checker.save_checkpoint",
                    text="Update" if cp_exists else "Save",
                    icon="FILE_TICK",
                )
                cp_row.operator(
                    "asset_checker.load_checkpoint",
                    text="", icon="IMPORT",
                )
                if cp_exists:
                    cp_row.operator(
                        "asset_checker.clear_checkpoint",
                        text="", icon="X", emboss=False,
                    )
            except Exception:
                pass


class ASSET_CHECKER_PT_UV_Panel(bpy.types.Panel):
    """STUKACH UV-панель в редакторе UV."""

    bl_label = "STUKACH"
    bl_idname = "ASSET_CHECKER_PT_UV_Panel"
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "STUKACH"

    _UV_CHECKS = (
        "uv_single_set", "uv_overlap", "uv_micro_shell",
        "uv_texel_density", "uv_stretch", "uv_padding", "uv_udim_bounds",
    )

    @classmethod
    def poll(cls, context):
        return True

    def draw(self, context):
        layout = self.layout
        mc = context.window_manager.mesh_check_props

        btn_text = "STUKACH ACTIVE" if mc.show_overlay else "RUN STUKACH"
        btn_icon = "RADIOBUT_ON"    if mc.show_overlay else "PLAY"
        row = layout.row(align=True)
        row.scale_y = 1.3
        row.prop(mc, "show_overlay", text=btn_text, toggle=True, icon=btn_icon)

        addon_name = __name__.split(".")[0]
        try:
            prefs = context.preferences.addons[addon_name].preferences
        except Exception:
            prefs = None

        # UV-чеки
        box = layout.box()
        box.label(text="UV Checks", icon="UV")
        col_a = box.column(align=True)
        for check in self._UV_CHECKS:
            r = col_a.row(align=True)
            icon = "CHECKBOX_HLT" if getattr(mc, check, False) else "CHECKBOX_DEHLT"
            r.prop(mc, check, icon=icon, emboss=False,
                   text=check.replace("_", " ").title())
            if prefs and hasattr(prefs, f"{check}_color"):
                c = r.row()
                c.scale_x = 0.15
                c.scale_y = 0.8
                c.alignment = "RIGHT"
                c.prop(prefs, f"{check}_color", text="")

        # Texel Density quick settings (shown when TD check is active)
        if prefs and getattr(mc, "uv_texel_density", False):
            import math as _math
            td_box = layout.box()
            td_box.label(text="Texel Density Settings", icon="UV_DATA")
            row = td_box.row(align=True)
            row.label(text="Tex Size:")
            row.prop(prefs, "uv_td_texture_size", text="")
            row = td_box.row(align=True)
            row.label(text="Target:")
            row.prop(prefs, "uv_td_target", text="")
            row.label(text="px/cm")
            row = td_box.row(align=True)
            row.label(text="Tolerance:")
            row.prop(prefs, "uv_td_tolerance", text="")
            row.label(text="%")

            # ── UV Space + Density summary ──────────────────────────────
            td_box.separator(factor=0.5)

            # Scope toggle: All Objects ↔ Active Object
            scope_row = td_box.row(align=True)
            scope_row.prop(
                mc, "uv_td_scope_active",
                text="Active Object" if mc.uv_td_scope_active else "All Objects",
                icon="OBJECT_DATA" if mc.uv_td_scope_active else "MESH_DATA",
                toggle=True,
            )

            # Gather UV area + world area for chosen scope
            _total_uv    = 0.0
            _total_world = 0.0
            if mc.uv_td_scope_active:
                _active = context.object
                _mc_o   = MC.objects.get(_active) if _active else None
                if _mc_o:
                    _ch = _mc_o._checks.get("uv_texel_density")
                    if _ch:
                        _total_uv    = getattr(_ch, '_uv_area',    0.0)
                        _total_world = getattr(_ch, '_world_area', 0.0)
            else:
                for _mc_o in MC.objects.values():
                    _ch = _mc_o._checks.get("uv_texel_density")
                    if _ch:
                        _total_uv    += getattr(_ch, '_uv_area',    0.0)
                        _total_world += getattr(_ch, '_world_area', 0.0)

            stats_col = td_box.column(align=True)
            stats_col.label(
                text=f"UV Space:  {_total_uv * 100.0:.2f} %",
                icon="UV_DATA",
            )
            if _total_world > 1e-10 and _total_uv > 1e-10:
                _TD_SIZES = {'0': 512, '1': 1024, '2': 2048, '3': 4096}
                _tex_sz = _TD_SIZES.get(prefs.uv_td_texture_size, 2048)
                try:
                    _sl = bpy.context.scene.unit_settings.scale_length or 1.0
                except Exception:
                    _sl = 1.0
                _agg_td = _tex_sz * _math.sqrt(_total_uv) / (_math.sqrt(_total_world) * 100.0 * _sl)
                stats_col.label(text=f"Density:   {_agg_td:.2f} px/cm", icon="GRAPH")
            else:
                stats_col.label(text="Density:   N/A", icon="GRAPH")

        # UV Padding quick settings + UDIM stats (shown when padding check is active)
        if getattr(mc, "uv_padding", False):
            pad_box = layout.box()
            pad_box.label(text="UV Padding Settings", icon="UV_FACESEL")
            if prefs:
                row = pad_box.row(align=True)
                row.label(text="Tex Size:")
                row.prop(prefs, "uv_padding_texture_size", text="")
                row = pad_box.row(align=True)
                row.label(text="Shell (px):")
                row.prop(prefs, "uv_padding_shell_px", text="")
                row = pad_box.row(align=True)
                row.label(text="Tile border (px):")
                row.prop(prefs, "uv_padding_tile_px", text="")

            # ── UDIM Padding Map ─────────────────────────────────────────────
            try:
                from .core import _uv_padding_tile_stats
                _pad_stats = _uv_padding_tile_stats
            except Exception:
                _pad_stats = {}

            if _pad_stats:
                pad_box.separator(factor=0.4)
                # Collapsible header
                hdr = pad_box.row(align=True)
                _tria_p = "TRIA_DOWN" if mc.uv_padding_stats_open else "TRIA_RIGHT"
                hdr.prop(mc, "uv_padding_stats_open",
                         text="", icon=_tria_p, emboss=False)

                # Count total bad tiles for the badge
                _n_bad_tiles = sum(
                    1 for s in _pad_stats.values()
                    if s['bad_shell'] > 0 or s['bad_tile'] > 0
                )
                _hdr_text = f"UDIM Padding Map  ({len(_pad_stats)} tile{'s' if len(_pad_stats) != 1 else ''})"
                hdr.label(text=_hdr_text, icon="TEXTURE")
                if _n_bad_tiles:
                    _hr = hdr.row()
                    _hr.alignment = "RIGHT"
                    _hr.label(text=str(_n_bad_tiles), icon="ERROR")

                if mc.uv_padding_stats_open:
                    _sorted_tiles = sorted(
                        _pad_stats.items(),
                        key=lambda kv: 1001 + kv[0][0] + kv[0][1] * 10,
                    )
                    for (tu, tv), st in _sorted_tiles:
                        _udim_num = 1001 + tu + tv * 10
                        _mb       = st['min_border_px']
                        _ms       = st.get('min_shell_px')    # float or None
                        _n_isl    = st['n_islands']
                        _n_obj    = st['n_objects']
                        _bs       = st['bad_shell']
                        _bt       = st['bad_tile']
                        _any_bad  = _bs > 0 or _bt > 0

                        # ── Per-tile sub-box ─────────────────────────────────
                        tile_box = pad_box.box()

                        # Row 1: UDIM  |  N isl, N obj  |  status icon
                        r1 = tile_box.row(align=True)
                        _udim_icon = "ERROR" if _any_bad else "CHECKMARK"
                        r1.label(text=f"UDIM {_udim_num}", icon=_udim_icon)
                        _info = f"{_n_isl} isl  {_n_obj} obj"
                        r1.label(text=_info)
                        if _any_bad:
                            _viol = []
                            if _bs:
                                _viol.append(f"{_bs} shell")
                            if _bt:
                                _viol.append(f"{_bt} border")
                            _rr = r1.row()
                            _rr.alignment = "RIGHT"
                            _rr.label(text=", ".join(_viol))

                        # Row 2: Shell spacing  |  Border spacing
                        r2 = tile_box.row(align=True)
                        r2.scale_y = 0.85

                        # Shell
                        if _ms is not None:
                            _sh_icon = "ERROR" if _bs > 0 else "CHECKMARK"
                            r2.label(
                                text=f"Shell: {_ms:.2f}px",
                                icon=_sh_icon,
                            )
                        else:
                            # Too dense for exact measurement — show threshold direction
                            _sh_icon = "ERROR" if _bs > 0 else "CHECKMARK"
                            _sh_text = "Shell: <..." if _bs > 0 else "Shell: OK"
                            r2.label(text=_sh_text, icon=_sh_icon)

                        # Border
                        _bd_icon = "ERROR" if _bt > 0 else "CHECKMARK"
                        r2.label(text=f"Border: {_mb:.2f}px", icon=_bd_icon)

        if not MC.objects:
            layout.box().label(text="Awaiting suspects.", icon="GHOST_ENABLED")
            return

        # ── Collapsible object-list section ──────────────────────────────────
        n_obj    = len(MC.objects)
        n_issues = sum(
            1 for mc_obj in MC.objects.values()
            if any(
                getattr(mc, ch, False) and _get_check_count(mc_obj, ch) > 0
                for ch in self._UV_CHECKS
            )
        )

        sec_box  = layout.box()
        sec_head = sec_box.row(align=True)

        tria_sec = "TRIA_DOWN" if mc.uv_obj_list_open else "TRIA_RIGHT"
        sec_head.prop(mc, "uv_obj_list_open", text="", icon=tria_sec, emboss=False)
        sec_head.label(text="Objects", icon="OBJECT_DATA")

        cnt = sec_head.row()
        cnt.alignment = "RIGHT"
        cnt.label(text=str(n_obj))
        if n_issues:
            cnt.label(text=str(n_issues), icon="ERROR")

        if mc.uv_obj_list_open:
            for obj, mc_obj in MC.objects.items():
                try:
                    obj_name = obj.name
                except ReferenceError:
                    continue

                obj_status = _get_object_status(mc_obj, mc)
                _BADGE = {"critical": "ERROR", "warning": "INFO", "clean": "CHECKMARK"}

                ob_box  = sec_box.box()
                ob_head = ob_box.row()
                split   = ob_head.split(factor=0.88)
                split.label(text=obj_name, icon="MESH_DATA")
                right = split.row()
                right.alignment = "RIGHT"
                right.label(text="", icon=_BADGE[obj_status])

                any_active = False
                for check in self._UV_CHECKS:
                    if not getattr(mc, check, False):
                        continue
                    any_active = True
                    checker = mc_obj._checks.get(check)
                    if checker is None:
                        continue
                    count = checker.count
                    threshold = _get_threshold(check, prefs)

                    if count == 0:
                        icon = "CHECKMARK"
                    elif count > threshold:
                        icon = "ERROR"
                    else:
                        icon = "INFO"

                    mt = getattr(checker, 'metric_text', '')
                    label = mt if mt else f"{check.replace('_', ' ').title()}: {count}"
                    ob_box.label(text=label, icon=icon)

                if not any_active:
                    ob_box.label(text="Enable UV checks above", icon="INFO")

        # ── Material → UDIM map ──────────────────────────────────────────────
        # Aggregate across all tracked objects
        _agg_mat_tiles: dict = {}
        for _mc_o in MC.objects.values():
            for _mat, _tiles in getattr(_mc_o, '_mat_udim_map', {}).items():
                if _mat not in _agg_mat_tiles:
                    _agg_mat_tiles[_mat] = set()
                _agg_mat_tiles[_mat].update(_tiles)

        if _agg_mat_tiles:
            mu_box = layout.box()
            mu_box.label(text="Material  →  UDIM", icon="MATERIAL")
            for _mat_name, _tiles in sorted(_agg_mat_tiles.items()):
                _udims = sorted(1001 + tu + tv * 10 for tu, tv in _tiles)
                _udim_str = "  ".join(str(u) for u in _udims)
                _is_sel = mc.mat_udim_selected == _mat_name
                row = mu_box.row(align=True)
                op = row.operator(
                    "asset_checker.highlight_mat_udim",
                    text=_mat_name,
                    icon="RADIOBUT_ON" if _is_sel else "RADIOBUT_OFF",
                    depress=_is_sel,
                )
                op.mat_name = _mat_name
                row.label(text=_udim_str)


def register():
    bpy.utils.register_class(ASSET_CHECKER_PT_Panel)
    bpy.utils.register_class(ASSET_CHECKER_PT_UV_Panel)


def unregister():
    bpy.utils.unregister_class(ASSET_CHECKER_PT_UV_Panel)
    bpy.utils.unregister_class(ASSET_CHECKER_PT_Panel)
