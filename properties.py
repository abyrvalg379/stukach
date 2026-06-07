# -*- coding:utf-8 -*-
import bpy
import bmesh
import csv
import json
import os
import tempfile
from datetime import datetime
from bpy.types import PropertyGroup
from bpy.props import BoolProperty, EnumProperty, StringProperty, FloatProperty

CHECK_CATEGORIES = {
    "TOPOLOGY":   ("non_manifold", "boundary_edges", "isolated_verts", "duplicate_verts",
                   "face_aspect_ratio",
                   "triangles", "ngons", "poles",
                   "zero_area", "flipped_normals", "z_fighting", "invalid_normals"),
    "TRANSFORMS": ("non_applied_transform", "scale", "origin_at_zero", "modifier_stack",
                   "smooth_by_angle"),
    "SYMMETRY":   ("symmetry_x", "symmetry_y", "symmetry_z"),
    "UV":         ("uv_single_set", "uv_overlap", "uv_micro_shell",
                   "uv_texel_density", "uv_stretch", "uv_padding",
                   "uv_udim_bounds", "uv_material_udim"),
    "NAMING":     ("obj_naming", "col_naming", "mat_numbering"),
    "MATERIALS":  ("mat_suffix", "mat_assignment", "missing_textures"),
    "CLEANUP":    ("unused_data",),
}

_CAT_ICONS = {
    "TOPOLOGY":   "MESH_DATA",
    "TRANSFORMS": "ARROW_LEFTRIGHT",
    "SYMMETRY":   "MOD_MIRROR",
    "UV":         "UV",
    "NAMING":     "OUTLINER_OB_EMPTY",
    "MATERIALS":  "MATERIAL",
    "CLEANUP":    "BRUSH_DATA",
}

# Custom display labels — overrides auto-generated text for specific checks
_CHECK_LABELS: dict = {
    "obj_naming":         "Object Name",
    "col_naming":         "Group Name",
    "z_fighting":         "Z-Fighting",
    "face_aspect_ratio":  "Face Aspect Ratio",
    "uv_material_udim":   "Mat per UDIM",
    "mat_numbering":      "Mat Numbering",
}


def enable_depsgraph_handler(self, context):
    from .manager import MeshCheck
    if self.check_data:
        if context.object is None:
            self.check_data = False
            self.show_overlay = False
            return
        # Always start fresh in SELECTED scope when pressing Run.
        # Scene / Collection scope is an explicit expansion — not a persistent state.
        MeshCheck._scope = "SELECTED"
        MeshCheck._scope_collection = ""
        MeshCheck.reset_mesh_check()
        MeshCheck.set_mode(context.object.mode)
        MeshCheck.add_callback()
    else:
        MeshCheck.remove_callback()


def update_overlay(self, context):
    from .manager import MeshCheck, MeshCheckGPU, UVCheckGPU
    self.check_data = self.show_overlay
    if self.show_overlay:
        MeshCheck._state_restored = False   # settings consumed — clear the banner
        if context.object is None:
            self.show_overlay = False
            return
        MeshCheckGPU.setup_handler()
        UVCheckGPU.setup_handler()
    else:
        MeshCheckGPU.remove_handler()
        UVCheckGPU.remove_handler()


def mc_object_datas_updater(attr):
    def updater(self, context):
        from .manager import MeshCheck
        if getattr(self, attr):
            MeshCheck.update_mc_object_datas(attr)
        return None
    return updater


def update_obj_naming(self, context):
    """Custom updater for obj_naming: clears NamingMarker when check is disabled."""
    from .manager import MeshCheck
    if self.obj_naming:
        MeshCheck.update_mc_object_datas("obj_naming")
        # NamingMarker.update() is called inside update_mc_object_datas
    else:
        try:
            from .naming import NamingMarker
            NamingMarker.clear()
        except Exception:
            pass


class ASSET_CHECKER_OT_select_check_elements(bpy.types.Operator):
    """Switch to Edit Mode and select the mesh elements flagged by this check"""
    bl_idname  = "asset_checker.select_check_elements"
    bl_label   = "Select Issues in Edit Mode"
    bl_options = {'REGISTER', 'UNDO'}

    obj_name:   StringProperty(options={'HIDDEN'})
    check_name: StringProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.mode in {'OBJECT', 'EDIT_MESH'}

    def execute(self, context):
        from .manager import MeshCheck

        obj = bpy.data.objects.get(self.obj_name)
        if obj is None:
            self.report({'WARNING'}, f"Object '{self.obj_name}' not found")
            return {'CANCELLED'}

        mc_obj = MeshCheck.objects.get(obj)
        if mc_obj is None:
            self.report({'WARNING'}, "Object not tracked — run validation first")
            return {'CANCELLED'}

        checker = mc_obj._checks.get(self.check_name)
        if checker is None or checker.count == 0:
            return {'CANCELLED'}

        element_type, indices = checker.get_select_data()
        if element_type is None or not indices:
            self.report({'INFO'}, "No selectable 3D elements for this check")
            return {'CANCELLED'}

        # Make active, enter Edit mode
        bpy.ops.object.select_all(action='DESELECT')
        context.view_layer.objects.active = obj
        obj.select_set(True)
        if context.mode != 'EDIT_MESH':
            bpy.ops.object.mode_set(mode='EDIT')

        bm = bmesh.from_edit_mesh(obj.data)

        # Deselect everything
        for v in bm.verts: v.select = False
        for e in bm.edges: e.select = False
        for f in bm.faces: f.select = False

        bm.select_flush(False)

        if element_type == 'VERT':
            bpy.ops.mesh.select_mode(type='VERT')
            bm.verts.ensure_lookup_table()
            for idx in indices:
                if 0 <= idx < len(bm.verts):
                    bm.verts[idx].select = True
        elif element_type == 'EDGE':
            bpy.ops.mesh.select_mode(type='EDGE')
            bm.edges.ensure_lookup_table()
            for idx in indices:
                if 0 <= idx < len(bm.edges):
                    bm.edges[idx].select = True
        elif element_type == 'FACE':
            bpy.ops.mesh.select_mode(type='FACE')
            bm.faces.ensure_lookup_table()
            for idx in indices:
                if 0 <= idx < len(bm.faces):
                    bm.faces[idx].select = True

        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)

        # Zoom viewport to the selected elements so the user can immediately
        # see where the problem is, especially important for zero-area faces
        # whose markers can be hard to spot manually.
        try:
            bpy.ops.view3d.view_selected()
        except Exception:
            pass

        return {'FINISHED'}


class MESH_CHECK_OT_toggle_category(bpy.types.Operator):
    """Включить / выключить все чеки категории"""
    bl_idname = "mesh_check.toggle_category"
    bl_label = "Toggle Category"
    bl_options = {'REGISTER', 'UNDO'}

    category: StringProperty()

    def execute(self, context):
        mc = context.window_manager.mesh_check_props
        checks = CHECK_CATEGORIES.get(self.category, ())
        any_on = any(getattr(mc, c, False) for c in checks if hasattr(mc, c))
        for c in checks:
            if hasattr(mc, c):
                setattr(mc, c, not any_on)
        return {'FINISHED'}


class ASSET_CHECKER_OT_set_td_target(bpy.types.Operator):
    """Set the Texel Density target value from a preset"""
    bl_idname = "asset_checker.set_td_target"
    bl_label  = "Set TD Target"
    bl_options = {'REGISTER', 'UNDO'}

    td_value: FloatProperty(name="TD Value", default=10.24, min=0.0, max=500.0)

    def execute(self, context):
        addon_name = __name__.split(".")[0]
        try:
            prefs = context.preferences.addons[addon_name].preferences
            prefs.uv_td_target = self.td_value
        except Exception as e:
            self.report({'WARNING'}, f"Could not set TD target: {e}")
            return {'CANCELLED'}
        # Re-run TD check to update counts with new target
        from .manager import MeshCheck
        MeshCheck.update_mc_object_datas("uv_texel_density")
        return {'FINISHED'}


class ASSET_CHECKER_OT_check_naming(bpy.types.Operator):
    """Run naming validation for all tracked objects using current prefix / suffix settings"""
    bl_idname = "asset_checker.check_naming"
    bl_label  = "Check Naming"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        from .manager import MeshCheck
        return bool(MeshCheck.objects)

    def execute(self, context):
        from .manager import MeshCheck
        mc = context.window_manager.mesh_check_props
        # Enable the checks so results are visible in the panel
        mc.obj_naming = True
        mc.col_naming = True
        # Force re-run (set_datas picks up the new prefix/suffix values)
        MeshCheck.update_mc_object_datas("obj_naming")
        MeshCheck.update_mc_object_datas("col_naming")
        return {'FINISHED'}


class ASSET_CHECKER_OT_highlight_mat_udim(bpy.types.Operator):
    """Highlight UDIM tiles used by this material in the UV Editor (click again to deselect)"""
    bl_idname  = "asset_checker.highlight_mat_udim"
    bl_label   = "Highlight Material UDIMs"
    bl_options = {'REGISTER'}

    mat_name: StringProperty(options={'HIDDEN'})

    def execute(self, context):
        mc = context.window_manager.mesh_check_props
        # Toggle: clicking the same material deselects it
        mc.mat_udim_selected = "" if mc.mat_udim_selected == self.mat_name else self.mat_name
        # Redraw all UV editors
        for area in context.screen.areas:
            if area.type == 'IMAGE_EDITOR':
                area.tag_redraw()
        return {'FINISHED'}


# ── Auto-fix helpers ─────────────────────────────────────────────────────────

# Maps check_name → fix operator bl_idname.
# Used by draw_options() to show a Fix button when issues are found.
_FIX_OPERATORS: dict = {
    "non_applied_transform": "asset_checker.fix_transforms",
    "scale":                 "asset_checker.fix_scale",
    "origin_at_zero":        "asset_checker.fix_origin",
    "modifier_stack":        "asset_checker.fix_modifier_stack",
    "flipped_normals":       "asset_checker.fix_normals",
    "isolated_verts":        "asset_checker.fix_merge_by_distance",
    "obj_naming":            "asset_checker.fix_naming",
    "mat_suffix":            "asset_checker.fix_mat_suffix",
}


def _problem_objects(check_name):
    """Yield (obj, mc_obj) pairs where *check_name* has count > 0."""
    from .manager import MeshCheck
    for obj, mc_obj in MeshCheck.objects.items():
        ch = mc_obj._checks.get(check_name)
        if ch and ch.count > 0:
            yield obj, mc_obj


def _ensure_accessible(context, obj) -> tuple:
    """Make *obj* active, visible, and selectable for fix operators.

    Returns a state tuple to pass to _restore_accessible().
    Works correctly in SCENE / COLLECTION scope where objects may not
    be selected (select_get() == False) or may be hidden in the viewport.
    """
    state = (obj.hide_viewport, obj.hide_select)
    obj.hide_viewport = False
    obj.hide_select   = False
    try:
        context.view_layer.objects.active = obj
        obj.select_set(True)
    except Exception:
        pass
    return state


def _restore_accessible(obj, state: tuple) -> None:
    """Restore visibility / select state saved by _ensure_accessible()."""
    try:
        obj.select_set(False)
    except Exception:
        pass
    obj.hide_viewport, obj.hide_select = state


def _ensure_visible(obj) -> tuple:
    """Lighter variant: make *obj* visible only (for EDIT-mode fix operators).

    Returns state tuple for _restore_visible().
    """
    state = (obj.hide_viewport, obj.hide_select)
    obj.hide_viewport = False
    obj.hide_select   = False
    return state


def _restore_visible(obj, state: tuple) -> None:
    """Restore visibility state saved by _ensure_visible()."""
    obj.hide_viewport, obj.hide_select = state


# ── Fix: Apply Rotation ───────────────────────────────────────────────────────
class ASSET_CHECKER_OT_fix_transforms(bpy.types.Operator):
    """Apply rotation to all tracked objects with non-applied rotation"""
    bl_idname  = "asset_checker.fix_transforms"
    bl_label   = "Fix: Apply Rotation"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from .manager import MeshCheck
        fixed = 0
        for obj, _ in list(_problem_objects("non_applied_transform")):
            state = _ensure_accessible(context, obj)
            try:
                with context.temp_override(active_object=obj,
                                           selected_objects=[obj],
                                           selected_editable_objects=[obj]):
                    bpy.ops.object.transform_apply(rotation=True)
                fixed += 1
            except Exception as e:
                print(f"[AssetChecker] fix_transforms {obj.name}: {e}")
            finally:
                _restore_accessible(obj, state)
        MeshCheck.update_mc_object_datas("non_applied_transform")
        self.report({'INFO'}, f"Applied rotation to {fixed} object(s)")
        return {'FINISHED'}


# ── Fix: Apply Scale ──────────────────────────────────────────────────────────
class ASSET_CHECKER_OT_fix_scale(bpy.types.Operator):
    """Apply scale to all tracked objects with non-unit scale"""
    bl_idname  = "asset_checker.fix_scale"
    bl_label   = "Fix: Apply Scale"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from .manager import MeshCheck
        fixed = 0
        for obj, _ in list(_problem_objects("scale")):
            state = _ensure_accessible(context, obj)
            try:
                with context.temp_override(active_object=obj,
                                           selected_objects=[obj],
                                           selected_editable_objects=[obj]):
                    bpy.ops.object.transform_apply(scale=True)
                fixed += 1
            except Exception as e:
                print(f"[AssetChecker] fix_scale {obj.name}: {e}")
            finally:
                _restore_accessible(obj, state)
        MeshCheck.update_mc_object_datas("scale")
        self.report({'INFO'}, f"Applied scale to {fixed} object(s)")
        return {'FINISHED'}


# ── Fix: Recalculate Normals ──────────────────────────────────────────────────
class ASSET_CHECKER_OT_fix_normals(bpy.types.Operator):
    """Recalculate normals outside for all tracked objects with flipped normals"""
    bl_idname  = "asset_checker.fix_normals"
    bl_label   = "Fix: Recalculate Normals"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from .manager import MeshCheck
        fixed = 0
        prev_active = context.view_layer.objects.active

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        for obj, _ in list(_problem_objects("flipped_normals")):
            state = _ensure_visible(obj)
            try:
                context.view_layer.objects.active = obj
                obj.select_set(True)
                bpy.ops.object.mode_set(mode='EDIT')
                bpy.ops.mesh.select_all(action='SELECT')
                bpy.ops.mesh.normals_make_consistent(inside=False)
                bpy.ops.object.mode_set(mode='OBJECT')
                obj.select_set(False)
                fixed += 1
            except Exception as e:
                print(f"[AssetChecker] fix_normals {obj.name}: {e}")
                try:
                    bpy.ops.object.mode_set(mode='OBJECT')
                except Exception:
                    pass
            finally:
                _restore_visible(obj, state)

        try:
            if prev_active:
                context.view_layer.objects.active = prev_active
        except Exception:
            pass

        MeshCheck.update_mc_object_datas("flipped_normals")
        self.report({'INFO'}, f"Recalculated normals on {fixed} object(s)")
        return {'FINISHED'}


# ── Fix: Merge by Distance ────────────────────────────────────────────────────
class ASSET_CHECKER_OT_fix_merge_by_distance(bpy.types.Operator):
    """Merge vertices by distance to remove isolated verts and near-duplicates"""
    bl_idname  = "asset_checker.fix_merge_by_distance"
    bl_label   = "Fix: Merge by Distance"
    bl_options = {'REGISTER', 'UNDO'}

    threshold: FloatProperty(
        name="Merge Distance",
        default=0.0001, min=0.0, max=1.0,
        description="Maximum distance between vertices to merge",
    )

    def execute(self, context):
        from .manager import MeshCheck
        fixed = 0
        prev_active = context.view_layer.objects.active

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        for obj, _ in list(_problem_objects("isolated_verts")):
            state = _ensure_visible(obj)
            try:
                context.view_layer.objects.active = obj
                obj.select_set(True)
                bpy.ops.object.mode_set(mode='EDIT')
                bpy.ops.mesh.select_all(action='SELECT')
                bpy.ops.mesh.remove_doubles(threshold=self.threshold)
                bpy.ops.object.mode_set(mode='OBJECT')
                obj.select_set(False)
                fixed += 1
            except Exception as e:
                print(f"[AssetChecker] fix_merge {obj.name}: {e}")
                try:
                    bpy.ops.object.mode_set(mode='OBJECT')
                except Exception:
                    pass
            finally:
                _restore_visible(obj, state)

        try:
            if prev_active:
                context.view_layer.objects.active = prev_active
        except Exception:
            pass

        MeshCheck.update_mc_object_datas("isolated_verts")
        self.report({'INFO'}, f"Merged by distance on {fixed} object(s)")
        return {'FINISHED'}


# ── Fix: Auto-rename objects ──────────────────────────────────────────────────
class ASSET_CHECKER_OT_fix_naming(bpy.types.Operator):
    """Auto-fix object names: lowercase, strip forbidden chars, apply prefix/suffix"""
    bl_idname  = "asset_checker.fix_naming"
    bl_label   = "Fix: Auto-rename"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        import re
        from .manager import MeshCheck
        mc     = context.window_manager.mesh_check_props
        prefix = mc.obj_required_prefix.strip()
        suffix = mc.obj_required_suffix.strip()
        fixed  = 0

        for obj, _ in list(_problem_objects("obj_naming")):
            name = obj.name
            name = name.lower()
            name = re.sub(r'[^a-z0-9_]', '_', name)
            name = re.sub(r'_+', '_', name).strip('_') or "unnamed"
            if prefix and not name.startswith(prefix):
                name = prefix + name
            if suffix and not name.endswith(suffix):
                name = name + suffix
            if name != obj.name:
                obj.name = name
                fixed += 1

        MeshCheck.update_mc_object_datas("obj_naming")
        self.report({'INFO'}, f"Renamed {fixed} object(s)")
        return {'FINISHED'}


# ── Fix: Apply Location (origin to zero) ─────────────────────────────────────
class ASSET_CHECKER_OT_fix_origin(bpy.types.Operator):
    """Apply location to all tracked objects whose origin is not at world zero.
    Bakes the current world-space position into the mesh vertices so the object
    stays in place visually while obj.location resets to (0, 0, 0)."""
    bl_idname  = "asset_checker.fix_origin"
    bl_label   = "Fix: Apply Location"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from .manager import MeshCheck
        fixed = 0
        for obj, _ in list(_problem_objects("origin_at_zero")):
            state = _ensure_accessible(context, obj)
            try:
                with context.temp_override(active_object=obj,
                                           selected_objects=[obj],
                                           selected_editable_objects=[obj]):
                    bpy.ops.object.transform_apply(location=True)
                fixed += 1
            except Exception as e:
                print(f"[AssetChecker] fix_origin {obj.name}: {e}")
            finally:
                _restore_accessible(obj, state)
        MeshCheck.update_mc_object_datas("origin_at_zero")
        self.report({'INFO'}, f"Applied location to {fixed} object(s)")
        return {'FINISHED'}


# ── Fix: Apply All Modifiers ──────────────────────────────────────────────────
class ASSET_CHECKER_OT_fix_modifier_stack(bpy.types.Operator):
    """Apply all non-Armature modifiers on all tracked objects with modifier issues"""
    bl_idname  = "asset_checker.fix_modifier_stack"
    bl_label   = "Fix: Apply Modifiers"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from .manager import MeshCheck
        fixed = 0
        prev_active = context.view_layer.objects.active

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        for obj, _ in list(_problem_objects("modifier_stack")):
            state = _ensure_accessible(context, obj)
            try:
                mods_to_apply = [m.name for m in obj.modifiers
                                 if m.type not in {'ARMATURE'}]
                for mod_name in mods_to_apply:
                    if mod_name not in obj.modifiers:
                        continue
                    with context.temp_override(active_object=obj,
                                               selected_objects=[obj],
                                               selected_editable_objects=[obj]):
                        bpy.ops.object.modifier_apply(modifier=mod_name)
                fixed += 1
            except Exception as e:
                print(f"[AssetChecker] fix_modifier_stack {obj.name}: {e}")
            finally:
                _restore_accessible(obj, state)

        try:
            if prev_active:
                context.view_layer.objects.active = prev_active
        except Exception:
            pass

        MeshCheck.update_mc_object_datas("modifier_stack")
        self.report({'INFO'}, f"Applied modifiers on {fixed} object(s)")
        return {'FINISHED'}


# ── Fix: Add _mat suffix ──────────────────────────────────────────────────────
class ASSET_CHECKER_OT_fix_mat_suffix(bpy.types.Operator):
    """Add _mat suffix to all materials that are missing it"""
    bl_idname  = "asset_checker.fix_mat_suffix"
    bl_label   = "Fix: Add _mat Suffix"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        fixed     = 0
        seen_mats: set = set()
        for obj, _ in list(_problem_objects("mat_suffix")):
            for slot in obj.material_slots:
                mat = slot.material
                if mat and id(mat) not in seen_mats and not mat.name.endswith("_mat"):
                    mat.name = mat.name + "_mat"
                    seen_mats.add(id(mat))
                    fixed += 1

        from .manager import MeshCheck
        MeshCheck.update_mc_object_datas("mat_suffix")
        self.report({'INFO'}, f"Added _mat suffix to {fixed} material(s)")
        return {'FINISHED'}


# ── Collapse / Expand all objects in the list ────────────────────────────────
class ASSET_CHECKER_OT_collapse_objects(bpy.types.Operator):
    """Collapse all expanded objects in the list (click again to expand all)"""
    bl_idname  = "asset_checker.collapse_objects"
    bl_label   = "Collapse / Expand All"
    bl_options = {'REGISTER'}

    def execute(self, context):
        from .manager import MeshCheck
        objects = list(MeshCheck.objects.keys())
        if not objects:
            return {'CANCELLED'}
        # If any object is expanded → collapse all; otherwise expand all
        def _stat(o):
            try:
                return bool(o.mesh_check_statistics)
            except ReferenceError:
                return False

        any_open = any(_stat(o) for o in objects)
        for o in objects:
            try:
                o.mesh_check_statistics = not any_open
            except Exception:
                pass
        return {'FINISHED'}


# ── Fix: All fixable checks in a category ────────────────────────────────────
class ASSET_CHECKER_OT_fix_category(bpy.types.Operator):
    """Run all available auto-fixes for this category"""
    bl_idname  = "asset_checker.fix_category"
    bl_label   = "Fix Category"
    bl_options = {'REGISTER', 'UNDO'}

    category: StringProperty(options={'HIDDEN'})

    def execute(self, context):
        from .manager import MeshCheck
        mc     = context.window_manager.mesh_check_props
        checks = CHECK_CATEGORIES.get(self.category, ())
        ran    = 0

        for check in checks:
            fix_idname = _FIX_OPERATORS.get(check)
            if not fix_idname or not getattr(mc, check, False):
                continue
            has_issues = any(
                mc_obj._checks.get(check) and mc_obj._checks[check].count > 0
                for mc_obj in MeshCheck.objects.values()
            )
            if not has_issues:
                continue
            try:
                # e.g. "asset_checker.fix_scale" → bpy.ops.asset_checker.fix_scale()
                mod, op = fix_idname.split(".", 1)
                getattr(getattr(bpy.ops, mod), op)()
                ran += 1
            except Exception as e:
                print(f"[AssetChecker] fix_category {check}: {e}")

        self.report({'INFO'}, f"Ran {ran} fix(es) in {self.category}")
        return {'FINISHED'}


# ── Export Report ─────────────────────────────────────────────────────────────

def _get_check_count_for_export(mc_obj, check_name: str) -> int:
    """Return count for a single check on a single MeshCheckObject (0 if absent)."""
    ch = mc_obj._checks.get(check_name)
    return ch.count if ch else 0


def _get_check_detail(mc_obj, check_name: str) -> str:
    """Return metric_text if available, otherwise empty string."""
    ch = mc_obj._checks.get(check_name)
    if ch is None:
        return ""
    return getattr(ch, "metric_text", "") or ""


class ASSET_CHECKER_OT_export_report(bpy.types.Operator):
    """Export STUKACH validation results to JSON, CSV, or HTML"""
    bl_idname  = "asset_checker.export_report"
    bl_label   = "Export Report"
    bl_options = {'REGISTER'}

    fmt: EnumProperty(
        name="Format",
        items=[
            ('JSON', "JSON", "Machine-readable, ideal for Shotgrid / Ftrack / pipeline tools"),
            ('CSV',  "CSV",  "Spreadsheet / task-tracker format"),
            ('HTML', "HTML", "Dark-theme human-readable report, open in any browser"),
        ],
        default='HTML',
    )

    # File dialog properties
    filepath: StringProperty(
        subtype='FILE_PATH',
        default="",
        description="Output file path for the report",
    )
    filter_glob: StringProperty(
        default="*.html;*.json;*.csv",
        options={'HIDDEN'},
    )

    # ── Internal helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _auto_path(ext: str) -> str:
        """Return default output path next to the .blend (or temp dir if unsaved)."""
        blend_path = bpy.data.filepath
        if blend_path:
            base = os.path.splitext(blend_path)[0]
            return f"{base}_report.{ext}"
        tmp = tempfile.gettempdir()
        return os.path.join(tmp, f"stukach_report.{ext}")

    # ── invoke: open file-save dialog ─────────────────────────────────────────

    def invoke(self, context, event):
        from .manager import MeshCheck
        if not MeshCheck.objects:
            self.report({'WARNING'}, "No validated objects — run STUKACH first")
            return {'CANCELLED'}

        ext = self.fmt.lower()

        # Pre-fill path and restrict browser to matching extension
        if not self.filepath or not self.filepath.lower().endswith(f".{ext}"):
            self.filepath = self._auto_path(ext)
        self.filter_glob = f"*.{ext}"

        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    @staticmethod
    def _build_report(context) -> dict:
        """Assemble a structured report dict from MeshCheck.objects."""
        from .manager import MeshCheck
        from .ui import CHECK_SEVERITY, _get_asset_status, _compute_asset_summary

        mc = context.window_manager.mesh_check_props

        # Summary
        summary_data = _compute_asset_summary(mc)
        status_str   = _get_asset_status(mc).upper()

        objects_list = []
        for obj, mc_obj in MeshCheck.objects.items():
            try:
                obj_name = obj.name
            except ReferenceError:
                continue
            checks_list = []
            for cat_name, cat_checks in CHECK_CATEGORIES.items():
                for check in cat_checks:
                    if not getattr(mc, check, False):
                        continue
                    count = _get_check_count_for_export(mc_obj, check)
                    if count == 0:
                        continue
                    detail = _get_check_detail(mc_obj, check)
                    checks_list.append({
                        "category": cat_name,
                        "check":    check,
                        "severity": CHECK_SEVERITY.get(check, "WARNING"),
                        "count":    count,
                        "detail":   detail,
                    })
            objects_list.append({
                "name":   obj_name,
                "checks": checks_list,
            })

        return {
            "tool":    "STUKACH · Pipeline Snitch System",
            "version": "1.2.3",
            "scene":   context.scene.name,
            "file":    bpy.data.filepath or "(unsaved)",
            "date":    datetime.now().isoformat(timespec='seconds'),
            "scope":   MeshCheck._scope,
            "summary": {
                "status":       status_str,
                "objects":      summary_data["obj_count"],
                "blockers":     summary_data["total_blockers"],
                "warnings":     summary_data["total_warnings"],
                "total_issues": summary_data["total_issues"],
            },
            "objects": objects_list,
        }

    # ── Writers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _write_json(report: dict, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

    @staticmethod
    def _write_csv(report: dict, path: str) -> None:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["scene", "file", "date", "scope",
                        "status", "objects", "blockers", "warnings"])
            s = report["summary"]
            w.writerow([report["scene"], report["file"], report["date"], report["scope"],
                        s["status"], s["objects"], s["blockers"], s["warnings"]])
            w.writerow([])
            w.writerow(["object", "category", "check", "severity", "count", "detail"])
            for obj in report["objects"]:
                for ch in obj["checks"]:
                    w.writerow([
                        obj["name"],
                        ch["category"],
                        ch["check"],
                        ch["severity"],
                        ch["count"],
                        ch.get("detail", ""),
                    ])

    @staticmethod
    def _write_html(report: dict, path: str) -> None:
        s = report["summary"]
        status_color = {"CRITICAL": "#e84040", "WARNING": "#e8a040", "READY": "#40c070"}.get(
            s["status"], "#aaaaaa"
        )

        rows_html = ""
        for obj in report["objects"]:
            for ch in obj["checks"]:
                sev_color = "#e84040" if ch["severity"] == "BLOCKER" else "#e8a040"
                rows_html += (
                    f"<tr>"
                    f"<td>{obj['name']}</td>"
                    f"<td>{ch['category']}</td>"
                    f"<td>{ch['check'].replace('_', ' ')}</td>"
                    f"<td style='color:{sev_color};font-weight:bold'>{ch['severity']}</td>"
                    f"<td style='text-align:center'>{ch['count']}</td>"
                    f"<td>{ch.get('detail','')}</td>"
                    f"</tr>\n"
                )
        if not rows_html:
            rows_html = "<tr><td colspan='6' style='color:#40c070;text-align:center'>No issues found — pipeline clean ✓</td></tr>"

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>STUKACH Report – {report['scene']}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#1a1a1a;color:#d0d0d0;font-family:'Segoe UI',Arial,sans-serif;font-size:13px;padding:24px}}
  h1{{color:#ffffff;font-size:22px;margin-bottom:4px}}
  .subtitle{{color:#888;font-size:11px;margin-bottom:20px}}
  .meta{{display:flex;gap:24px;margin-bottom:20px;flex-wrap:wrap}}
  .meta div{{background:#252525;border:1px solid #333;border-radius:6px;padding:8px 14px}}
  .meta .label{{color:#888;font-size:10px;text-transform:uppercase;letter-spacing:.5px}}
  .meta .value{{color:#fff;font-size:15px;font-weight:bold;margin-top:2px}}
  .status{{color:{status_color};font-size:18px;font-weight:bold}}
  table{{width:100%;border-collapse:collapse;margin-top:12px}}
  th{{background:#2a2a2a;color:#aaa;font-size:11px;text-transform:uppercase;
      letter-spacing:.5px;padding:8px 10px;text-align:left;border-bottom:2px solid #333}}
  td{{padding:7px 10px;border-bottom:1px solid #2a2a2a;vertical-align:top}}
  tr:hover td{{background:#232323}}
  .footer{{margin-top:16px;color:#555;font-size:10px;text-align:right}}
</style>
</head>
<body>
<h1>STUKACH · Pipeline Snitch Report</h1>
<div class="subtitle">{report['tool']} v{report['version']}</div>

<div class="meta">
  <div><div class="label">Status</div><div class="value status">{s['status']}</div></div>
  <div><div class="label">Scene</div><div class="value">{report['scene']}</div></div>
  <div><div class="label">Scope</div><div class="value">{report['scope']}</div></div>
  <div><div class="label">Objects</div><div class="value">{s['objects']}</div></div>
  <div><div class="label">Blockers</div><div class="value" style="color:#e84040">{s['blockers']}</div></div>
  <div><div class="label">Warnings</div><div class="value" style="color:#e8a040">{s['warnings']}</div></div>
  <div><div class="label">Date</div><div class="value">{report['date']}</div></div>
</div>

<table>
<thead>
  <tr>
    <th>Object</th><th>Category</th><th>Check</th>
    <th>Severity</th><th>Count</th><th>Detail</th>
  </tr>
</thead>
<tbody>
{rows_html}
</tbody>
</table>

<div class="footer">
  File: {report['file']}<br>
  Generated by STUKACH · Pipeline Snitch System
</div>
</body>
</html>"""
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)

    # ── execute ────────────────────────────────────────────────────────────────

    def execute(self, context):
        from .manager import MeshCheck
        if not MeshCheck.objects:
            self.report({'WARNING'}, "No validated objects — run STUKACH first")
            return {'CANCELLED'}

        # Use filepath from file dialog; fall back to auto-path if called directly
        ext  = self.fmt.lower()
        path = self.filepath if self.filepath else self._auto_path(ext)

        # Ensure correct extension when user typed a path without one
        if not path.lower().endswith(f".{ext}"):
            path = f"{os.path.splitext(path)[0]}.{ext}"

        report = self._build_report(context)

        try:
            if self.fmt == 'JSON':
                self._write_json(report, path)
            elif self.fmt == 'CSV':
                self._write_csv(report, path)
            else:
                self._write_html(report, path)
        except Exception as e:
            self.report({'ERROR'}, f"Export failed: {e}")
            return {'CANCELLED'}

        self.report({'INFO'}, f"Saved: {path}")
        return {'FINISHED'}


# ── Batch / Scene-wide validation operators ───────────────────────────────────
class ASSET_CHECKER_OT_validate_scene(bpy.types.Operator):
    """Validate all mesh objects in the current scene"""
    bl_idname  = "asset_checker.validate_scene"
    bl_label   = "Validate Scene"
    bl_options = {'REGISTER'}

    def execute(self, context):
        from .manager import MeshCheck, MeshCheckGPU, UVCheckGPU
        wm = context.window_manager
        mc = wm.mesh_check_props
        if not mc.show_overlay:
            mc.show_overlay = True
        # Always ensure GPU handlers are up (covers post-reload where _handler=None
        # but show_overlay was already True so update_overlay never fired)
        MeshCheckGPU.setup_handler()
        UVCheckGPU.setup_handler()
        MeshCheck._scope = "SCENE"
        MeshCheck._scope_collection = ""
        MeshCheck._scene_stale = False
        MeshCheck.objects.clear()
        MeshCheckGPU._batch_cache.clear()
        UVCheckGPU._batch_cache.clear()

        context.window.cursor_set('WAIT')
        try:
            MeshCheck.add_scene_objects(wm=wm)
        finally:
            context.window.cursor_set('DEFAULT')

        return {'FINISHED'}


class ASSET_CHECKER_OT_validate_collection(bpy.types.Operator):
    """Validate all mesh objects in the active collection"""
    bl_idname  = "asset_checker.validate_collection"
    bl_label   = "Validate Collection"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return context.collection is not None

    def execute(self, context):
        from .manager import MeshCheck, MeshCheckGPU, UVCheckGPU
        wm  = context.window_manager
        mc  = wm.mesh_check_props
        col = context.collection
        if not mc.show_overlay:
            mc.show_overlay = True
        MeshCheckGPU.setup_handler()
        UVCheckGPU.setup_handler()
        MeshCheck._scope = "COLLECTION"
        MeshCheck._scope_collection = col.name
        MeshCheck._scene_stale = False
        MeshCheck.objects.clear()
        MeshCheckGPU._batch_cache.clear()
        UVCheckGPU._batch_cache.clear()

        context.window.cursor_set('WAIT')
        try:
            MeshCheck.add_collection_objects_from(col, wm=wm)
        finally:
            context.window.cursor_set('DEFAULT')

        return {'FINISHED'}


class ASSET_CHECKER_OT_clear_validation(bpy.types.Operator):
    """Clear all validated objects and switch back to Selection mode"""
    bl_idname  = "asset_checker.clear_validation"
    bl_label   = "Clear"
    bl_options = {'REGISTER'}

    def execute(self, context):
        from .manager import MeshCheck
        MeshCheck._scope = "SELECTED"
        MeshCheck._scope_collection = ""
        MeshCheck.reset_mesh_check()
        return {'FINISHED'}


# ── Fix: Remove unused data ───────────────────────────────────────────────────

class ASSET_CHECKER_OT_fix_unused_data(bpy.types.Operator):
    """Remove empty vertex groups detected by the Unused Data check.
    Custom attributes are listed but NOT auto-deleted — review them manually."""
    bl_idname  = "asset_checker.fix_unused_data"
    bl_label   = "Fix: Remove Unused Data"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from .manager import MeshCheck
        removed_vgroups = 0
        attr_report     = []

        for obj, mc_obj in list(_problem_objects("unused_data")):
            checker = mc_obj._checks.get("unused_data")
            if not checker:
                continue

            # Remove empty vertex groups
            for vg_name in list(checker._empty_vgroups):
                vg = obj.vertex_groups.get(vg_name)
                if vg:
                    obj.vertex_groups.remove(vg)
                    removed_vgroups += 1

            # Report custom attrs — do NOT auto-delete
            if checker._custom_attrs:
                attr_report.append(
                    f"{obj.name}: {', '.join(checker._custom_attrs)}"
                )

        MeshCheck.update_mc_object_datas("unused_data")

        msg = f"Removed {removed_vgroups} empty vertex group(s)."
        if attr_report:
            msg += f"  Custom attrs (manual review): {'; '.join(attr_report)}"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


# ── Pre-flight Export ─────────────────────────────────────────────────────────

class ASSET_CHECKER_OT_preflight_export(bpy.types.Operator):
    """Run STUKACH pre-flight check, then open the export dialog.
    CRITICAL issues block export; warnings require confirmation."""
    bl_idname  = "asset_checker.preflight_export"
    bl_label   = "Pre-flight Export"
    bl_options = {'REGISTER'}

    fmt: EnumProperty(
        name="Format",
        items=[('FBX', 'FBX',          'Export as FBX (.fbx)'),
               ('USD', 'USD (USDC)',   'Export as Universal Scene Description')],
        default='FBX',
    )

    # Per-invocation state (Python instance attrs, not RNA — intentional)
    _blocker_count: int = 0
    _warning_count: int = 0
    _is_blocked:   bool = False
    _issues:       list = []   # [(severity, display_label, n_objects), ...]

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _gather_issues(mc) -> list:
        """Return sorted list of (severity, label, n_objects) for all active checks
        that have at least one issue across tracked objects."""
        from .manager import MeshCheck
        from .ui import CHECK_SEVERITY

        tally: dict = {}   # check_name → n objects affected
        for obj, mc_obj in MeshCheck.objects.items():
            try:
                obj.name  # ReferenceError if deleted
            except ReferenceError:
                continue
            for check, checker in mc_obj._checks.items():
                if not getattr(mc, check, False):
                    continue
                if checker.count > 0:
                    tally[check] = tally.get(check, 0) + 1

        issues = []
        for check, n_objs in tally.items():
            sev   = CHECK_SEVERITY.get(check, 'WARNING')
            label = _CHECK_LABELS.get(check, check.replace('_', ' ').title())
            issues.append((sev, label, n_objs))

        # BLOCKER first, then WARNING; alphabetical within group
        issues.sort(key=lambda x: (0 if x[0] == 'BLOCKER' else 1, x[1]))
        return issues

    def _refresh(self, mc) -> None:
        self._issues        = self._gather_issues(mc)
        self._blocker_count = sum(1 for sev, _, _ in self._issues if sev == 'BLOCKER')
        self._warning_count = sum(1 for sev, _, _ in self._issues if sev != 'BLOCKER')
        self._is_blocked    = bool(self._blocker_count)

    # ── Blender operator methods ──────────────────────────────────────────────

    def invoke(self, context, event):
        from .manager import MeshCheck
        mc = context.window_manager.mesh_check_props

        if not MeshCheck.objects:
            self.report({'WARNING'}, "No validation results — run STUKACH first")
            return {'CANCELLED'}

        self._refresh(mc)

        if self._is_blocked:
            # Popup only — no OK/Cancel, no execute() call
            return context.window_manager.invoke_popup(self, width=390)
        if self._warning_count:
            # Dialog with OK (→ execute) / Cancel
            return context.window_manager.invoke_props_dialog(self, width=390)

        # All clean — open export immediately
        return self._open_export(context)

    def draw(self, context):
        layout = self.layout

        # Header row
        hdr = layout.row()
        if self._is_blocked:
            hdr.alert = True
            hdr.label(
                text=f"Export blocked — {self._blocker_count} critical issue(s)",
                icon="CANCEL",
            )
        else:
            hdr.label(
                text=f"{self._warning_count} warning(s) — export anyway?",
                icon="ERROR",
            )

        # Issue list
        if self._issues:
            box = layout.box()
            col = box.column(align=True)
            for sev, label, n_objs in self._issues:
                row = col.row()
                icon  = "CANCEL" if sev == "BLOCKER" else "DOT"
                noun  = "object" if n_objs == 1 else "objects"
                row.label(text=f"{label}:  {n_objs} {noun}", icon=icon)

        # Footer hint for blocked state
        if self._is_blocked:
            layout.separator(factor=0.5)
            foot = layout.row()
            foot.enabled = False
            foot.label(text="Fix all critical issues before exporting", icon="INFO")

    def execute(self, context):
        # Re-check in case scene changed while dialog was open
        mc = context.window_manager.mesh_check_props
        self._refresh(mc)
        if self._is_blocked:
            self.report(
                {'ERROR'},
                f"Export blocked — {self._blocker_count} critical issue(s) remain",
            )
            return {'CANCELLED'}
        return self._open_export(context)

    def _open_export(self, context):
        try:
            if self.fmt == 'FBX':
                bpy.ops.export_scene.fbx('INVOKE_DEFAULT')
            else:
                bpy.ops.wm.usd_export('INVOKE_DEFAULT')
        except Exception as e:
            self.report({'ERROR'}, f"Export dialog failed: {e}")
            return {'CANCELLED'}
        return {'FINISHED'}


# ── Ignore List helpers ───────────────────────────────────────────────────────

_AC_IGNORE_KEY = "_ac_ignore"


def get_obj_ignore_list(obj) -> set:
    """Return the set of check names permanently ignored on *obj*.

    Reads from the ``_ac_ignore`` custom property (JSON list).
    Returns an empty set when nothing is ignored.
    """
    raw = obj.get(_AC_IGNORE_KEY, "")
    if not raw:
        return set()
    try:
        return set(json.loads(raw))
    except Exception:
        return set()


def set_obj_ignore_list(obj, ignore_set: set) -> None:
    """Persist *ignore_set* as a custom property on *obj*.

    Passing an empty set removes the property entirely
    (keeps Custom Properties panel tidy).
    """
    if ignore_set:
        obj[_AC_IGNORE_KEY] = json.dumps(sorted(ignore_set), ensure_ascii=False)
    elif _AC_IGNORE_KEY in obj:
        del obj[_AC_IGNORE_KEY]


class ASSET_CHECKER_OT_toggle_ignore(bpy.types.Operator):
    """Permanently ignore / un-ignore this check result on this object.
    The decision is stored in the object's custom properties and survives save/reload."""
    bl_idname  = "asset_checker.toggle_ignore"
    bl_label   = "Ignore / Un-ignore Check"
    bl_options = {'REGISTER', 'UNDO'}

    obj_name:   StringProperty(options={'HIDDEN'})
    check_name: StringProperty(options={'HIDDEN'})

    def execute(self, context):
        obj = bpy.data.objects.get(self.obj_name)
        if obj is None:
            self.report({'WARNING'}, f"Object '{self.obj_name}' not found")
            return {'CANCELLED'}

        ignored = get_obj_ignore_list(obj)
        adding  = self.check_name not in ignored

        if adding:
            ignored.add(self.check_name)
        else:
            ignored.discard(self.check_name)

        set_obj_ignore_list(obj, ignored)

        # Re-run the affected check so the overlay + count update immediately
        from .manager import MeshCheck
        MeshCheck.update_mc_object_datas(self.check_name)

        verb = "Ignored" if adding else "Restored"
        self.report({'INFO'}, f"{verb}: {self.check_name} on '{obj.name}'")
        return {'FINISHED'}


class ASSET_CHECKER_OT_clear_ignore_object(bpy.types.Operator):
    """Remove all ignored checks from this object and re-run them."""
    bl_idname  = "asset_checker.clear_ignore_object"
    bl_label   = "Clear Ignores (Object)"
    bl_options = {'REGISTER', 'UNDO'}

    obj_name: StringProperty(options={'HIDDEN'})

    def execute(self, context):
        obj = bpy.data.objects.get(self.obj_name)
        if obj is None:
            return {'CANCELLED'}

        ignored = get_obj_ignore_list(obj)
        if not ignored:
            return {'CANCELLED'}

        set_obj_ignore_list(obj, set())

        # Re-run all previously ignored checks
        from .manager import MeshCheck
        for check_name in ignored:
            MeshCheck.update_mc_object_datas(check_name)

        self.report({'INFO'}, f"Cleared {len(ignored)} ignored check(s) on '{obj.name}'")
        return {'FINISHED'}


class ASSET_CHECKER_OT_clear_all_ignores(bpy.types.Operator):
    """Remove ALL ignored checks from ALL tracked objects."""
    bl_idname  = "asset_checker.clear_all_ignores"
    bl_label   = "Clear All Ignores"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from .manager import MeshCheck

        cleared:    set = set()
        total_objs: int = 0

        for obj in list(MeshCheck.objects.keys()):
            try:
                ignored = get_obj_ignore_list(obj)
            except ReferenceError:
                continue
            if not ignored:
                continue
            cleared.update(ignored)
            set_obj_ignore_list(obj, set())
            total_objs += 1

        for check_name in cleared:
            MeshCheck.update_mc_object_datas(check_name)

        self.report({'INFO'}, f"Cleared ignores on {total_objs} object(s)")
        return {'FINISHED'}


# ── Coordinator Mode helpers ──────────────────────────────────────────────────

_AC_CHECKPOINT_KEY = "_ac_checkpoint"


def _compute_current_results(mc) -> dict:
    """Snapshot current validation results into a serialisable dict.

    Returns:
        {
          "objects":  {name: {check: count, "_total": N}},
          "totals":   {"total": N, "blockers": N, "by_category": {cat: N}},
        }
    """
    from .manager import MeshCheck
    from .ui import CHECK_SEVERITY

    obj_results: dict = {}
    for obj, mc_obj in MeshCheck.objects.items():
        try:
            name = obj.name
        except ReferenceError:
            continue
        obj_data: dict = {}
        total = 0
        for check, chk in mc_obj._checks.items():
            if getattr(mc, check, False) and chk.count > 0:
                obj_data[check] = chk.count
                total += chk.count
        obj_data["_total"] = total
        obj_results[name] = obj_data

    # Per-category totals
    by_category: dict = {}
    for cat, checks in CHECK_CATEGORIES.items():
        by_category[cat] = sum(
            obj_results[nm].get(chk, 0)
            for nm in obj_results
            for chk in checks
        )

    total    = sum(d.get("_total", 0) for d in obj_results.values())
    blockers = sum(
        cnt
        for d in obj_results.values()
        for chk, cnt in d.items()
        if chk != "_total" and CHECK_SEVERITY.get(chk) == "BLOCKER"
    )

    return {
        "objects": obj_results,
        "totals": {
            "total":       total,
            "blockers":    blockers,
            "by_category": by_category,
        },
    }


class ASSET_CHECKER_OT_save_checkpoint(bpy.types.Operator):
    """Save current validation results as a coordinator checkpoint.
    The checkpoint is stored inside the .blend file and survives save/reload."""
    bl_idname  = "asset_checker.save_checkpoint"
    bl_label   = "Save Checkpoint"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        from .manager import MeshCheck
        return bool(MeshCheck.objects)

    def execute(self, context):
        mc   = context.window_manager.mesh_check_props
        data = _compute_current_results(mc)
        data["timestamp"] = datetime.now().strftime("%Y-%m-%d  %H:%M")
        context.scene[_AC_CHECKPOINT_KEY] = json.dumps(data, ensure_ascii=False)
        n_obj    = len(data["objects"])
        n_issues = data["totals"]["total"]
        self.report({'INFO'},
                    f"Checkpoint saved — {n_obj} object(s), {n_issues} issue(s)")
        return {'FINISHED'}


class ASSET_CHECKER_OT_clear_checkpoint(bpy.types.Operator):
    """Remove the saved coordinator checkpoint from this .blend file."""
    bl_idname  = "asset_checker.clear_checkpoint"
    bl_label   = "Clear Checkpoint"
    bl_options = {'REGISTER'}

    def execute(self, context):
        if _AC_CHECKPOINT_KEY in context.scene:
            del context.scene[_AC_CHECKPOINT_KEY]
        self.report({'INFO'}, "Checkpoint cleared")
        return {'FINISHED'}


class ASSET_CHECKER_OT_load_checkpoint(bpy.types.Operator):
    """Load a checkpoint from a STUKACH JSON report file.

    Use this in FBX-based pipelines: the artist exports a JSON report
    alongside the FBX, the coordinator loads it here as the comparison baseline.
    Accepts both checkpoint JSON and full validation report JSON formats."""
    bl_idname   = "asset_checker.load_checkpoint"
    bl_label    = "Load Checkpoint from File"
    bl_options  = {'REGISTER'}

    filepath:    StringProperty(subtype='FILE_PATH', options={'HIDDEN'})
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})

    def invoke(self, context, event):
        # Pre-fill directory from current blend file location
        blend = bpy.data.filepath
        if blend:
            import os
            self.filepath = os.path.dirname(blend) + os.sep
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            self.report({'ERROR'}, f"Cannot read file: {e}")
            return {'CANCELLED'}

        cp = self._to_checkpoint_fmt(data)
        if cp is None:
            self.report({'ERROR'}, "Unrecognised JSON format — expected STUKACH report or checkpoint")
            return {'CANCELLED'}

        context.scene[_AC_CHECKPOINT_KEY] = json.dumps(cp, ensure_ascii=False)
        n_obj    = len(cp['objects'])
        n_issues = cp['totals']['total']
        self.report({'INFO'},
                    f"Checkpoint loaded — {n_obj} object(s), {n_issues} issue(s)  [{cp['timestamp']}]")
        return {'FINISHED'}

    @staticmethod
    def _to_checkpoint_fmt(data: dict):
        """Convert STUKACH JSON to internal checkpoint format.

        Handles two inputs:
        1. Checkpoint JSON  — objects is a dict {name: {check: count}}
        2. Report JSON      — objects is a list [{name, checks:[{check, count}]}]

        Returns checkpoint dict or None if the format is unrecognised.
        """
        from .ui import CHECK_SEVERITY

        # ── Already a checkpoint (dict-style objects) ────────────────────────
        if (isinstance(data.get("objects"), dict)
                and "totals" in data):
            return data

        # ── Report format (list-style objects) ───────────────────────────────
        obj_list = data.get("objects")
        if not isinstance(obj_list, list):
            return None

        obj_results: dict = {}
        by_category: dict = {cat: 0 for cat in CHECK_CATEGORIES}
        total    = 0
        blockers = 0

        for obj_data in obj_list:
            name = obj_data.get("name", "")
            if not name:
                continue
            chk_dict: dict = {}
            obj_total = 0
            for chk in obj_data.get("checks", []):
                check_name = chk.get("check", "")
                count      = int(chk.get("count", 0))
                cat        = chk.get("category", "")
                if not check_name or count == 0:
                    continue
                chk_dict[check_name] = count
                obj_total += count
                total     += count
                if CHECK_SEVERITY.get(check_name) == "BLOCKER":
                    blockers += count
                if cat in by_category:
                    by_category[cat] += count
            chk_dict["_total"] = obj_total
            obj_results[name]  = chk_dict

        # Normalise timestamp (ISO "2026-05-21T10:30:00" → "2026-05-21  10:30")
        ts = data.get("date", data.get("timestamp", ""))
        if "T" in ts:
            date_part, time_part = ts.split("T", 1)
            ts = f"{date_part}  {time_part[:5]}"   # "YYYY-MM-DD  HH:MM"
        if not ts:
            ts = datetime.now().strftime("%Y-%m-%d  %H:%M")

        return {
            "timestamp": ts,
            "_source":   "report",          # loaded from JSON report, not live session
            "objects":   obj_results,
            "totals": {
                "total":       total,
                "blockers":    blockers,
                "by_category": by_category,
            },
        }


# ─────────────────────────────────────────────────────────────────────────────

class MeshCheckProperties(PropertyGroup):
    check_data:   BoolProperty(name="Check Data",   default=False, update=enable_depsgraph_handler)
    show_overlay: BoolProperty(name="Show Overlay", default=False, update=update_overlay)

    # Coordinator Mode toggle
    coordinator_mode: BoolProperty(
        name="Coordinator Mode",
        default=False,
        description="Switch to coordinator view — compare current results vs saved checkpoint",
    )

    # TOPOLOGY
    non_manifold:        BoolProperty(name="Non-manifold",            default=False, update=mc_object_datas_updater("non_manifold"))
    boundary_edges:      BoolProperty(name="Boundary Edges",          default=False, update=mc_object_datas_updater("boundary_edges"),
                                      description="Open mesh borders (edges with exactly one adjacent face)")
    isolated_verts:      BoolProperty(name="Isolated Vertices",       default=False, update=mc_object_datas_updater("isolated_verts"),
                                      description="Vertices not connected to any edge")
    duplicate_verts:     BoolProperty(name="Duplicate Vertices",      default=False, update=mc_object_datas_updater("duplicate_verts"),
                                      description="Overlapping vertices within 0.1 mm — would merge on Merge by Distance")
    face_aspect_ratio:   BoolProperty(name="Face Aspect Ratio",       default=False, update=mc_object_datas_updater("face_aspect_ratio"),
                                      description="Quads with aspect ratio exceeding threshold (default 6:1) — causes stretching artifacts under subdivision")
    triangles:           BoolProperty(name="Triangles",               default=False, update=mc_object_datas_updater("triangles"))
    ngons:               BoolProperty(name="Ngons",                   default=False, update=mc_object_datas_updater("ngons"))
    poles:               BoolProperty(name="Poles",                   default=False, update=mc_object_datas_updater("poles"))
    zero_area:           BoolProperty(name="Zero-area faces",         default=False, update=mc_object_datas_updater("zero_area"))
    flipped_normals:     BoolProperty(name="Flipped normals",         default=False, update=mc_object_datas_updater("flipped_normals"))
    z_fighting:          BoolProperty(name="Z-Fighting",              default=False, update=mc_object_datas_updater("z_fighting"),
                                      description="Coplanar face overlap within the mesh and between tracked objects")
    invalid_normals:      BoolProperty(name="Invalid Normals",        default=False, update=mc_object_datas_updater("invalid_normals"),
                                       description="Custom split normals that are zero-length or flipped vs. face geometry (midpoly-safe)")

    # TRANSFORMS
    non_applied_transform: BoolProperty(name="Non-applied rotation", default=False, update=mc_object_datas_updater("non_applied_transform"))
    scale:                 BoolProperty(name="Scale (not 1.0)",      default=False, update=mc_object_datas_updater("scale"))
    origin_at_zero:        BoolProperty(name="Origin not at zero",   default=False, update=mc_object_datas_updater("origin_at_zero"),
                                        description="Object pivot point is not at world origin (0, 0, 0)")
    modifier_stack:        BoolProperty(name="Modifier Stack",       default=False, update=mc_object_datas_updater("modifier_stack"),
                                        description="Unapplied modifiers present on object (pipeline non-whitelisted)")
    smooth_by_angle:       BoolProperty(name="Smooth by Angle",      default=False, update=mc_object_datas_updater("smooth_by_angle"),
                                        description="Smooth by Angle GN modifier: must be present with angle 180°")

    # SYMMETRY
    symmetry_x: BoolProperty(name="Symmetry X", default=False, update=mc_object_datas_updater("symmetry_x"))
    symmetry_y: BoolProperty(name="Symmetry Y", default=False, update=mc_object_datas_updater("symmetry_y"))
    symmetry_z: BoolProperty(name="Symmetry Z", default=False, update=mc_object_datas_updater("symmetry_z"))

    # UV
    uv_single_set:    BoolProperty(name="Single UV Set",        default=False, update=mc_object_datas_updater("uv_single_set"))
    uv_overlap:       BoolProperty(name="UV Overlap",           default=False, update=mc_object_datas_updater("uv_overlap"))
    uv_micro_shell:   BoolProperty(name="UV Micro-shells",      default=False, update=mc_object_datas_updater("uv_micro_shell"))
    uv_texel_density: BoolProperty(name="Texel Density",        default=False, update=mc_object_datas_updater("uv_texel_density"))
    uv_stretch:       BoolProperty(name="UV Stretch",           default=False, update=mc_object_datas_updater("uv_stretch"))
    uv_padding:       BoolProperty(name="UV Padding",           default=False, update=mc_object_datas_updater("uv_padding"))
    uv_udim_bounds:   BoolProperty(name="UDIM Bounds",          default=False, update=mc_object_datas_updater("uv_udim_bounds"))
    uv_material_udim: BoolProperty(name="Mat per UDIM",         default=False, update=mc_object_datas_updater("uv_material_udim"),
                                   description="Each UDIM tile must contain shells from one material only (регламент: 1 UDIM = 1 material group)")

    # NAMING
    obj_naming:    BoolProperty(name="Object Name",   default=False, update=update_obj_naming)
    col_naming:    BoolProperty(name="Group Name",    default=False, update=mc_object_datas_updater("col_naming"))
    mat_numbering: BoolProperty(name="Mat Numbering", default=False, update=mc_object_datas_updater("mat_numbering"),
                                description="Material names must not contain Blender auto-numbering (.001, .002 ...)")

    # Inline naming policy fields — combined with prefs at validation time
    obj_required_prefix: StringProperty(name="Prefix", default="",
                                        description="Required object name prefix (e.g. 'sm_')")
    obj_required_suffix: StringProperty(name="Suffix", default="",
                                        description="Required object name suffix (e.g. '_geo')")
    col_required_prefix: StringProperty(name="Prefix", default="",
                                        description="Required group name prefix (e.g. 'grp_')")
    col_required_suffix: StringProperty(name="Suffix", default="",
                                        description="Required group name suffix (e.g. '_grp')")

    # TD scope toggle — controls UV Space / Density summary in UV panel
    uv_td_scope_active: BoolProperty(
        name="Active Object Only",
        default=False,
        description="Show UV Space and Density for the active object only (off = all validated objects)",
    )

    # MATERIALS
    mat_suffix:       BoolProperty(name="Material Suffix (_mat)", default=False, update=mc_object_datas_updater("mat_suffix"))
    mat_assignment:   BoolProperty(name="Material Assignment",    default=False, update=mc_object_datas_updater("mat_assignment"))
    missing_textures: BoolProperty(name="Missing Textures",       default=False, update=mc_object_datas_updater("missing_textures"),
                                   description="Detect missing texture files referenced in material node trees")

    # SCENE-LEVEL check (not per-object — drawn at top of panel)
    scene_units: BoolProperty(
        name="Scene Units",
        default=False,
        description="Scene must use METRIC / METERS with scale_length = 1.0",
    )

    # Category collapsed state — False = collapsed by default for compact startup
    cat_topology_open:   BoolProperty(name="Topology",   default=False)
    cat_transforms_open: BoolProperty(name="Transforms", default=False)
    cat_symmetry_open:   BoolProperty(name="Symmetry",   default=False)
    cat_uv_open:         BoolProperty(name="UV",         default=False)
    cat_naming_open:     BoolProperty(name="Naming",     default=False)
    cat_materials_open:  BoolProperty(name="Materials",  default=False)

    # Object list section — collapsed by default to keep the panel clean
    obj_list_open: BoolProperty(
        name="Object List",
        default=False,
        description="Show / hide the per-object details list",
    )
    uv_obj_list_open: BoolProperty(
        name="UV Object List",
        default=False,
        description="Show / hide the per-object UV results list in the UV Editor",
    )

    # UDIM Padding Map — collapse toggle in UV panel
    uv_padding_stats_open: BoolProperty(
        name="UDIM Padding Map",
        default=True,
        description="Expand / collapse the per-UDIM padding statistics block",
    )

    # Object list filter
    obj_filter_text: StringProperty(
        name="Search",
        default="",
        description="Filter objects by name",
    )
    obj_filter_errors_only: BoolProperty(
        name="Issues Only",
        default=False,
        description="Show only objects that have at least one active issue",
    )

    # Material → UDIM highlight selection (UV panel)
    mat_udim_selected: StringProperty(
        name="Selected Material",
        default="",
        description="Material currently highlighted in the UV editor (click to toggle)",
    )

    # CLEANUP
    unused_data: BoolProperty(
        name="Unused Data",
        default=False,
        update=mc_object_datas_updater("unused_data"),
        description="Empty vertex groups and leftover custom mesh attributes",
    )

    # Category collapse — Cleanup
    cat_cleanup_open: BoolProperty(name="Cleanup", default=True)

    # Hierarchy block — section collapse toggle
    hierarchy_block_open: BoolProperty(
        name="Hierarchy",
        default=False,
        description="Expand / collapse the Hierarchy validator block",
    )
    # Hierarchy block — tree section toggle
    hierarchy_tree_open: BoolProperty(
        name="Hierarchy Tree",
        default=False,
        description="Show object hierarchy tree",
    )

    # Naming Audit block — section collapse toggle
    naming_audit_open: BoolProperty(
        name="Naming Audit",
        default=False,
        description="Expand / collapse the Naming Audit block",
    )

    # Ignore List section toggle
    ignore_list_open: BoolProperty(
        name="Ignore List",
        default=False,
        description="Show / hide ignored checks across all tracked objects",
    )

    checker_options = tuple(item for sublist in CHECK_CATEGORIES.values() for item in sublist)

    def draw_options(self, layout, severity_filter=None):
        """Draw the check grid.

        severity_filter – if given, only show checks whose CHECK_SEVERITY level
        is in the set.  E.g. {'BLOCKER', 'WARNING'} for Coordinator Mode.
        Categories where all checks are filtered out are hidden entirely.
        """
        from .manager import MeshCheck
        from .ui import CHECK_SEVERITY  # lazy import to avoid circular
        addon_name = __name__.split(".")[0]
        try:
            addon_prefs = bpy.context.preferences.addons[addon_name].preferences
        except Exception:
            addon_prefs = None

        for cat_name, checks in CHECK_CATEGORIES.items():
            # Apply severity filter — skip categories where nothing passes
            if severity_filter:
                visible_checks = [c for c in checks
                                  if CHECK_SEVERITY.get(c, 'INFO') in severity_filter]
                if not visible_checks:
                    continue
            else:
                visible_checks = list(checks)

            open_prop = f"cat_{cat_name.lower()}_open"
            is_open   = getattr(self, open_prop, True)

            box = layout.box()

            # ── Collapsible header ─────────────────────────────────────────
            header = box.row(align=True)
            header.prop(
                self, open_prop,
                text="",
                icon="TRIA_DOWN" if is_open else "TRIA_RIGHT",
                emboss=False,
            )
            header.label(
                text=cat_name.replace("_", " ").title(),
                icon=_CAT_ICONS.get(cat_name, "DOT"),
            )

            # Fix button — only when at least one fixable check in the category has issues
            if MeshCheck.objects:
                cat_has_fix = any(
                    _FIX_OPERATORS.get(c)
                    and getattr(self, c, False)
                    and any(
                        mc_obj._checks.get(c) and mc_obj._checks[c].count > 0
                        for mc_obj in MeshCheck.objects.values()
                    )
                    for c in visible_checks
                )
                if cat_has_fix:
                    fix_op = header.operator(
                        "asset_checker.fix_category",
                        text="Fix",
                        icon="TOOL_SETTINGS",
                    )
                    fix_op.category = cat_name

            any_on = any(getattr(self, c, False) for c in visible_checks if hasattr(self, c))
            op = header.operator(
                "mesh_check.toggle_category",
                text="",
                icon="CHECKBOX_HLT" if any_on else "CHECKBOX_DEHLT",
                emboss=False,
            )
            op.category = cat_name

            if not is_open:
                continue

            # ── Check grid ─────────────────────────────────────────────────
            row = box.row(align=True)
            col_1 = row.column()
            col_2 = row.column()

            for i, check in enumerate(visible_checks):
                col = col_1 if i % 2 == 0 else col_2
                r = col.row(align=True)
                icon = "CHECKBOX_HLT" if getattr(self, check, False) else "CHECKBOX_DEHLT"
                label = _CHECK_LABELS.get(check, check.replace("_", " ").title())
                r.prop(self, check, icon=icon, emboss=False, text=label)

                if addon_prefs and hasattr(addon_prefs, f"{check}_color"):
                    c = r.row()
                    c.scale_x = 0.15
                    c.scale_y = 0.8
                    c.alignment = "RIGHT"
                    c.prop(addon_prefs, f"{check}_color", text="")

            # ── Inline CLEANUP actions ─────────────────────────────────────
            if cat_name == "CLEANUP":
                box.separator(factor=0.3)
                col = box.column(align=True)
                col.operator(
                    "asset_checker.fix_unused_data",
                    text="Remove Empty Vertex Groups",
                    icon="GROUP_VERTEX",
                )
                col.operator(
                    "object.material_slot_remove_unused",
                    text="Remove Unused Material Slots",
                    icon="MATERIAL",
                )
                col.separator(factor=0.5)
                op = col.operator(
                    "outliner.orphans_purge",
                    text="Purge Orphan Data-Blocks",
                    icon="ORPHAN_DATA",
                )
                op.do_local_ids = True
                op.do_linked_ids = False
                op.do_recursive  = True

            # ── Inline naming policy fields ────────────────────────────────
            if cat_name == "NAMING":
                box.separator(factor=0.5)
                split = box.row(align=False)

                obj_col = split.column(align=True)
                obj_col.label(text="Objects:", icon="OBJECT_DATA")
                obj_col.prop(self, "obj_required_prefix", text="Prefix")
                obj_col.prop(self, "obj_required_suffix", text="Suffix")

                grp_col = split.column(align=True)
                grp_col.label(text="Groups:", icon="OUTLINER_COLLECTION")
                grp_col.prop(self, "col_required_prefix", text="Prefix")
                grp_col.prop(self, "col_required_suffix", text="Suffix")

                box.operator(
                    "asset_checker.check_naming",
                    text="Check Naming",
                    icon="VIEWZOOM",
                )

                # ── Hierarchy validator sub-section ────────────────────────
                box.separator(factor=0.3)
                try:
                    from .ui import draw_hierarchy_block
                    draw_hierarchy_block(box, self)
                except Exception as _he:
                    print(f"[AssetChecker] hierarchy block draw error: {_he}")

                # ── Naming Audit sub-section ────────────────────────────────
                box.separator(factor=0.3)
                try:
                    from .ui import draw_naming_audit_block
                    draw_naming_audit_block(box, self)
                except Exception as _ne:
                    print(f"[AssetChecker] naming audit block draw error: {_ne}")
