# -*- coding:utf-8 -*-
# Metadata is defined in blender_manifest.toml (Blender 4.2+ extension standard).

import bpy
from bpy.props import BoolProperty, PointerProperty

if "bpy" in locals():
    # Hot-reload: ensure submodules are in sys.modules, then reload in dependency order
    import importlib, sys as _sys
    from . import core, preferences, properties, ui, naming, manager
    for _key, _mod in (
        ("STUKACH.core", core), ("STUKACH.naming", naming),
        ("STUKACH.preferences", preferences), ("STUKACH.properties", properties),
        ("STUKACH.ui", ui), ("STUKACH.manager", manager),
    ):
        _sys.modules[_key] = _mod
        importlib.reload(_mod)

from . import preferences, properties, ui, naming

classes = (
    # NamingEntry must precede MeshCheckPreferences (CollectionProperty type dependency)
    preferences.NamingEntry,
    # Object naming policy operators
    preferences.ASSET_CHECKER_OT_naming_add_prefix,
    preferences.ASSET_CHECKER_OT_naming_remove_prefix,
    preferences.ASSET_CHECKER_OT_naming_add_suffix,
    preferences.ASSET_CHECKER_OT_naming_remove_suffix,
    # Collection naming policy operators
    preferences.ASSET_CHECKER_OT_col_naming_add_prefix,
    preferences.ASSET_CHECKER_OT_col_naming_remove_prefix,
    preferences.ASSET_CHECKER_OT_col_naming_add_suffix,
    preferences.ASSET_CHECKER_OT_col_naming_remove_suffix,
    preferences.MeshCheckPreferences,
    properties.MESH_CHECK_OT_toggle_category,
    properties.ASSET_CHECKER_OT_select_check_elements,
    properties.ASSET_CHECKER_OT_set_td_target,
    properties.ASSET_CHECKER_OT_check_naming,
    properties.ASSET_CHECKER_OT_highlight_mat_udim,
    properties.ASSET_CHECKER_OT_collapse_objects,
    properties.ASSET_CHECKER_OT_fix_transforms,
    properties.ASSET_CHECKER_OT_fix_scale,
    properties.ASSET_CHECKER_OT_fix_origin,
    properties.ASSET_CHECKER_OT_fix_modifier_stack,
    properties.ASSET_CHECKER_OT_fix_normals,
    properties.ASSET_CHECKER_OT_fix_merge_by_distance,
    properties.ASSET_CHECKER_OT_fix_naming,
    properties.ASSET_CHECKER_OT_fix_unused_data,
    properties.ASSET_CHECKER_OT_fix_mat_suffix,
    properties.ASSET_CHECKER_OT_fix_category,
    properties.ASSET_CHECKER_OT_export_report,
    properties.ASSET_CHECKER_OT_preflight_export,
    properties.ASSET_CHECKER_OT_save_checkpoint,
    properties.ASSET_CHECKER_OT_clear_checkpoint,
    properties.ASSET_CHECKER_OT_load_checkpoint,
    properties.ASSET_CHECKER_OT_toggle_ignore,
    properties.ASSET_CHECKER_OT_clear_ignore_object,
    properties.ASSET_CHECKER_OT_clear_all_ignores,
    properties.ASSET_CHECKER_OT_validate_scene,
    properties.ASSET_CHECKER_OT_validate_collection,
    properties.ASSET_CHECKER_OT_clear_validation,
    properties.MeshCheckProperties,
    ui.ASSET_CHECKER_PT_Panel,
    ui.ASSET_CHECKER_PT_UV_Panel,
    naming.ASSET_CHECKER_OT_select_object,
    naming.ASSET_CHECKER_OT_run_naming_audit,
    naming.ASSET_CHECKER_OT_clear_naming_audit,
    naming.ASSET_CHECKER_OT_scan_hierarchy,
    naming.ASSET_CHECKER_OT_clear_hierarchy,
)


def register():
    for cls in classes:
        try:
            if hasattr(bpy.types, cls.__name__):
                bpy.utils.unregister_class(getattr(bpy.types, cls.__name__))
            bpy.utils.register_class(cls)
        except Exception as e:
            print(f"[AssetChecker] Warning: {e}")

    bpy.types.WindowManager.mesh_check_props = PointerProperty(
        type=properties.MeshCheckProperties)

    bpy.types.Object.mesh_check_statistics = BoolProperty(
        name="Toggle Visibility",
        default=False)

    from .manager import register_state_handlers
    register_state_handlers()


def unregister():
    try:
        from .manager import unregister_state_handlers
        unregister_state_handlers()
    except Exception as e:
        print(f"[AssetChecker] unregister_state_handlers: {e}")

    try:
        from .manager import MeshCheck, MeshCheckGPU, UVCheckGPU
    except Exception as e:
        print(f"[AssetChecker] unregister: manager unavailable ({e})")
        for cls in reversed(classes):
            try:
                bpy.utils.unregister_class(cls)
            except Exception:
                pass
        return

    if hasattr(bpy.app.handlers, 'depsgraph_update_post'):
        if MeshCheck.callback in bpy.app.handlers.depsgraph_update_post:
            try:
                bpy.app.handlers.depsgraph_update_post.remove(MeshCheck.callback)
            except Exception:
                pass

    if MeshCheckGPU._handler:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(MeshCheckGPU._handler, 'WINDOW')
            MeshCheckGPU._handler = None
        except Exception:
            pass

    if UVCheckGPU._handler:
        try:
            bpy.types.SpaceImageEditor.draw_handler_remove(UVCheckGPU._handler, 'WINDOW')
            UVCheckGPU._handler = None
        except Exception:
            pass

    try:
        from .naming import NamingMarker, NamingAudit
        NamingMarker.remove()
        NamingAudit.clear()
    except Exception:
        pass

    for obj in list(MeshCheck.objects.keys()):
        mc = MeshCheck.objects.get(obj)
        if mc and getattr(mc, '_bm_object', None) and mc._bm_object.is_valid:
            try:
                mc._bm_object.free()
            except Exception:
                pass
    MeshCheck.objects.clear()

    try:
        del bpy.types.Object.mesh_check_statistics
    except Exception:
        pass
    try:
        del bpy.types.WindowManager.mesh_check_props
    except Exception:
        pass

    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass


if __name__ == "__main__":
    register()
