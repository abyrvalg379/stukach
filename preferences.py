# -*- coding:utf-8 -*-
import bpy
from bpy.types import AddonPreferences, PropertyGroup
from bpy.props import FloatVectorProperty, FloatProperty, StringProperty, CollectionProperty, IntProperty, EnumProperty


# ── Naming policy entry (one prefix or suffix) ────────────────────────────────

class NamingEntry(PropertyGroup):
    """Single configurable naming prefix or suffix."""
    value: StringProperty(name="", default="")


# ── Naming policy operators ───────────────────────────────────────────────────

class ASSET_CHECKER_OT_naming_add_prefix(bpy.types.Operator):
    """Add a required object name prefix"""
    bl_idname = "asset_checker.naming_add_prefix"
    bl_label = "Add Prefix"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        prefs = context.preferences.addons[__name__.rsplit(".", 1)[0]].preferences
        prefs.naming_prefixes.add()
        return {'FINISHED'}


class ASSET_CHECKER_OT_naming_remove_prefix(bpy.types.Operator):
    """Remove the selected required prefix"""
    bl_idname = "asset_checker.naming_remove_prefix"
    bl_label = "Remove Prefix"
    bl_options = {'REGISTER', 'UNDO'}

    index: IntProperty()

    def execute(self, context):
        prefs = context.preferences.addons[__name__.rsplit(".", 1)[0]].preferences
        if 0 <= self.index < len(prefs.naming_prefixes):
            prefs.naming_prefixes.remove(self.index)
        return {'FINISHED'}


class ASSET_CHECKER_OT_naming_add_suffix(bpy.types.Operator):
    """Add a required object name suffix"""
    bl_idname = "asset_checker.naming_add_suffix"
    bl_label = "Add Suffix"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        prefs = context.preferences.addons[__name__.rsplit(".", 1)[0]].preferences
        prefs.naming_suffixes.add()
        return {'FINISHED'}


class ASSET_CHECKER_OT_naming_remove_suffix(bpy.types.Operator):
    """Remove the selected required suffix"""
    bl_idname = "asset_checker.naming_remove_suffix"
    bl_label = "Remove Suffix"
    bl_options = {'REGISTER', 'UNDO'}

    index: IntProperty()

    def execute(self, context):
        prefs = context.preferences.addons[__name__.rsplit(".", 1)[0]].preferences
        if 0 <= self.index < len(prefs.naming_suffixes):
            prefs.naming_suffixes.remove(self.index)
        return {'FINISHED'}


class ASSET_CHECKER_OT_col_naming_add_prefix(bpy.types.Operator):
    """Add a required collection name prefix"""
    bl_idname = "asset_checker.col_naming_add_prefix"
    bl_label = "Add Prefix"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        prefs = context.preferences.addons[__name__.rsplit(".", 1)[0]].preferences
        prefs.col_naming_prefixes.add()
        return {'FINISHED'}


class ASSET_CHECKER_OT_col_naming_remove_prefix(bpy.types.Operator):
    """Remove the selected required collection prefix"""
    bl_idname = "asset_checker.col_naming_remove_prefix"
    bl_label = "Remove Prefix"
    bl_options = {'REGISTER', 'UNDO'}

    index: IntProperty()

    def execute(self, context):
        prefs = context.preferences.addons[__name__.rsplit(".", 1)[0]].preferences
        if 0 <= self.index < len(prefs.col_naming_prefixes):
            prefs.col_naming_prefixes.remove(self.index)
        return {'FINISHED'}


class ASSET_CHECKER_OT_col_naming_add_suffix(bpy.types.Operator):
    """Add a required collection name suffix"""
    bl_idname = "asset_checker.col_naming_add_suffix"
    bl_label = "Add Suffix"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        prefs = context.preferences.addons[__name__.rsplit(".", 1)[0]].preferences
        prefs.col_naming_suffixes.add()
        return {'FINISHED'}


class ASSET_CHECKER_OT_col_naming_remove_suffix(bpy.types.Operator):
    """Remove the selected required collection suffix"""
    bl_idname = "asset_checker.col_naming_remove_suffix"
    bl_label = "Remove Suffix"
    bl_options = {'REGISTER', 'UNDO'}

    index: IntProperty()

    def execute(self, context):
        prefs = context.preferences.addons[__name__.rsplit(".", 1)[0]].preferences
        if 0 <= self.index < len(prefs.col_naming_suffixes):
            prefs.col_naming_suffixes.remove(self.index)
        return {'FINISHED'}


def _uv_padding_settings_update(self, context):
    """Re-run cross-object padding check when any threshold setting changes.
    Called automatically by Blender when uv_padding_shell_px / tile_px /
    texture_size is edited in the N-panel or Preferences."""
    try:
        from .manager import MeshCheck
        MeshCheck._run_global_uv_padding()
        scr = getattr(context, 'screen', None)
        if scr:
            for area in scr.areas:
                if area.type in ('VIEW_3D', 'IMAGE_EDITOR'):
                    area.tag_redraw()
    except Exception:
        pass


class MeshCheckPreferences(AddonPreferences):
    bl_idname = __name__.rsplit(".", 1)[0]

    edges_width:   FloatProperty(name="Edges Width",  default=2.0,  min=1.0, max=10.0, subtype="PIXEL")
    faces_offset:  FloatProperty(name="Faces Offset", default=0.03, min=0.0, max=5.0,  precision=3)
    edges_alpha:   FloatProperty(name="Edges Alpha",  default=1.0,  min=0.0, max=1.0,  precision=3)
    faces_alpha:   FloatProperty(name="Faces Alpha",  default=0.4,  min=0.0, max=1.0,  precision=3)
    point_size:    FloatProperty(name="Vertex Size",  default=10.0, min=0.1, max=20.0, subtype="PIXEL")
    points_offset: FloatProperty(name="Points Offset",default=0.03, min=0.0, max=5.0,  precision=3)

    # TOPOLOGY
    non_manifold_color:         FloatVectorProperty(name="Non manifold",         default=(0.02, 1.0,  0.02), min=0.0, max=1.0, size=3, subtype="COLOR")
    boundary_edges_color:       FloatVectorProperty(name="Boundary edges",       default=(1.0,  0.5,  0.0),  min=0.0, max=1.0, size=3, subtype="COLOR")
    isolated_verts_color:       FloatVectorProperty(name="Isolated vertices",    default=(1.0,  1.0,  0.0),  min=0.0, max=1.0, size=3, subtype="COLOR")
    duplicate_verts_color:      FloatVectorProperty(name="Duplicate vertices",   default=(1.0,  0.6,  0.0),  min=0.0, max=1.0, size=3, subtype="COLOR")
    triangles_color:            FloatVectorProperty(name="Triangles",            default=(0.7,  0.7,  0.02), min=0.0, max=1.0, size=3, subtype="COLOR")
    ngons_color:                FloatVectorProperty(name="Ngons",                default=(0.7,  0.02, 0.02), min=0.0, max=1.0, size=3, subtype="COLOR")
    zero_area_color:            FloatVectorProperty(name="Zero-area",            default=(1.0,  0.0,  1.0),  min=0.0, max=1.0, size=3, subtype="COLOR")
    flipped_normals_color:      FloatVectorProperty(name="Flipped normals",      default=(1.0,  0.3,  0.0),  min=0.0, max=1.0, size=3, subtype="COLOR")
    z_fighting_color:           FloatVectorProperty(name="Z-Fighting",           default=(1.0,  0.0,  0.0),  min=0.0, max=1.0, size=3, subtype="COLOR")
    invalid_normals_color:      FloatVectorProperty(name="Invalid normals",      default=(0.0,  0.8,  1.0),  min=0.0, max=1.0, size=3, subtype="COLOR")

    # POLES
    poles_color:           FloatVectorProperty(name="Poles",           default=(0.25, 0.4,  1.0),  min=0.0, max=1.0, size=3, subtype="COLOR")

    # TRANSFORMS
    non_applied_transform_color: FloatVectorProperty(name="Rotation issue",    default=(1.0, 0.0, 0.0), min=0.0, max=1.0, size=3, subtype="COLOR")
    scale_color:                 FloatVectorProperty(name="Scale issue",        default=(1.0, 0.4, 0.0), min=0.0, max=1.0, size=3, subtype="COLOR")
    origin_at_zero_color:        FloatVectorProperty(name="Origin not at zero", default=(1.0, 0.8, 0.0), min=0.0, max=1.0, size=3, subtype="COLOR")
    modifier_stack_color:        FloatVectorProperty(name="Modifier stack",     default=(0.6, 0.0, 1.0), min=0.0, max=1.0, size=3, subtype="COLOR")
    face_aspect_ratio_threshold: FloatProperty(
        name="Aspect Ratio Threshold",
        description="Quads with ratio longer_side/shorter_side above this value are flagged",
        default=6.0, min=1.5, max=50.0, precision=1,
    )
    face_aspect_ratio_color:     FloatVectorProperty(name="Face Aspect Ratio",  default=(1.0, 0.85, 0.0), min=0.0, max=1.0, size=3, subtype="COLOR")

# SYMMETRY
    symmetry_x_color: FloatVectorProperty(name="Symmetry X", default=(1.0,  0.15, 0.15), min=0.0, max=1.0, size=3, subtype="COLOR")
    symmetry_y_color: FloatVectorProperty(name="Symmetry Y", default=(0.15, 1.0,  0.15), min=0.0, max=1.0, size=3, subtype="COLOR")
    symmetry_z_color: FloatVectorProperty(name="Symmetry Z", default=(0.15, 0.4,  1.0),  min=0.0, max=1.0, size=3, subtype="COLOR")

    # UV
    uv_single_set_color:    FloatVectorProperty(name="UV Single Set",   default=(0.0, 0.5, 1.0),  min=0.0, max=1.0, size=3, subtype="COLOR")
    uv_overlap_color:       FloatVectorProperty(name="UV Overlap",      default=(1.0, 0.2, 0.0),  min=0.0, max=1.0, size=3, subtype="COLOR")
    uv_micro_shell_color:   FloatVectorProperty(name="UV Micro-shell",  default=(1.0, 0.0, 0.8),  min=0.0, max=1.0, size=3, subtype="COLOR")
    uv_texel_density_color: FloatVectorProperty(name="Texel Density",   default=(0.3, 0.9, 0.5),  min=0.0, max=1.0, size=3, subtype="COLOR")
    uv_stretch_color:       FloatVectorProperty(name="UV Stretch",      default=(1.0, 0.5, 0.0),  min=0.0, max=1.0, size=3, subtype="COLOR")
    uv_padding_color:       FloatVectorProperty(name="UV Padding",      default=(1.0, 0.6, 0.0),  min=0.0, max=1.0, size=3, subtype="COLOR")
    uv_udim_bounds_color:   FloatVectorProperty(name="UDIM Bounds",     default=(0.7, 0.2, 1.0),  min=0.0, max=1.0, size=3, subtype="COLOR")
    uv_material_udim_color: FloatVectorProperty(name="Uv Material Udim",   default=(1.0, 0.2, 0.6),  min=0.0, max=1.0, size=3, subtype="COLOR")

    # NAMING colors
    obj_naming_color:  FloatVectorProperty(name="Naming issue",  default=(1.0, 0.5, 0.0), min=0.0, max=1.0, size=3, subtype="COLOR")
    col_naming_color:  FloatVectorProperty(name="Col naming",    default=(0.5, 0.5, 0.5), min=0.0, max=1.0, size=3, subtype="COLOR")
    mat_numbering_color: FloatVectorProperty(name="Mat numbering", default=(1.0, 0.4, 0.1), min=0.0, max=1.0, size=3, subtype="COLOR")

    # NAMING POLICY — object prefixes / suffixes
    naming_prefixes: CollectionProperty(type=NamingEntry, name="Object Required Prefixes")
    naming_suffixes: CollectionProperty(type=NamingEntry, name="Object Required Suffixes")
    # NAMING POLICY — collection prefixes / suffixes
    col_naming_prefixes: CollectionProperty(type=NamingEntry, name="Collection Required Prefixes")
    col_naming_suffixes: CollectionProperty(type=NamingEntry, name="Collection Required Suffixes")

    # UV PADDING settings
    uv_padding_texture_size: EnumProperty(
        name="Padding Texture Size",
        description="Reference texture size for UV padding threshold calculation. "
                    "Must match the texture size used during UV packing (e.g. UVPackmaster Texture Size)",
        items=(('0', '512 px',  ''), ('1', '1024 px', ''),
               ('2', '2048 px', ''), ('3', '4096 px', '')),
        default='3',  # 4096 — UVPackmaster default
        update=_uv_padding_settings_update,
    )
    uv_padding_shell_px: IntProperty(
        name="Shell Padding",
        description="Minimum required padding between UV islands in pixels "
                    "(scaled to UV space using the Padding Texture Size setting)",
        default=16, min=1, max=256,
        update=_uv_padding_settings_update,
    )
    uv_padding_tile_px: IntProperty(
        name="Tile Border Padding",
        description="Minimum required padding from UV island edges to UDIM tile borders "
                    "in pixels (scaled to UV space using the Padding Texture Size setting)",
        default=8, min=1, max=128,
        update=_uv_padding_settings_update,
    )

    # UV STRETCH settings
    uv_stretch_threshold: FloatProperty(
        name="Stretch Threshold",
        description="Maximum allowed angle deviation between 3D and UV space (radians). "
                    "Lower = more strict. Default 0.5 rad ≈ 28°",
        default=0.5, min=0.05, max=3.14, precision=2,
    )

    # TEXEL DENSITY settings
    uv_td_texture_size: EnumProperty(
        name="Texture Size",
        description="Reference texture size for texel density calculation",
        items=(('0', '512 px',  ''), ('1', '1024 px', ''),
               ('2', '2048 px', ''), ('3', '4096 px', '')),
        default='3',
    )
    uv_td_target: FloatProperty(
        name="Target TD",
        description="Target texel density in px/cm (0 = informational only, no flagging)",
        default=0.0, min=0.0, max=500.0, precision=2,
    )
    uv_td_tolerance: FloatProperty(
        name="Tolerance %",
        description="Allowed deviation from target TD in percent (e.g. 20 = ±20%)",
        default=20.0, min=1.0, max=100.0, precision=1,
    )

    # MATERIALS
    mat_suffix_color:        FloatVectorProperty(name="Mat suffix",        default=(0.5, 0.5, 0.5), min=0.0, max=1.0, size=3, subtype="COLOR")
    mat_assignment_color:    FloatVectorProperty(name="Mat assign",        default=(0.5, 0.5, 0.5), min=0.0, max=1.0, size=3, subtype="COLOR")
    missing_textures_color:  FloatVectorProperty(name="Missing textures",  default=(1.0, 0.2, 0.2), min=0.0, max=1.0, size=3, subtype="COLOR")

    # CLEANUP
    unused_data_color: FloatVectorProperty(name="Unused Data", default=(0.6, 0.4, 0.1), min=0.0, max=1.0, size=3, subtype="COLOR")

    # CHECK THRESHOLDS — count ≤ threshold → yellow dot; count > threshold → red dot
    # Only the three checks that make sense to tune are exposed here.
    threshold_triangles: IntProperty(
        name="Triangles",
        description="Counts up to this value show a yellow dot; above — red.\n"
                    "Set to 0 to flag any triangle as red immediately",
        default=50, min=0, max=10_000,
    )
    threshold_ngons: IntProperty(
        name="Ngons",
        description="Counts up to this value show a yellow dot; above — red.\n"
                    "Set to 0 to flag any ngon as red immediately",
        default=10, min=0, max=10_000,
    )
    threshold_poles: IntProperty(
        name="Poles",
        description="Counts up to this value show a yellow dot; above — red.\n"
                    "Set to 0 to flag any pole as red immediately",
        default=20, min=0, max=10_000,
    )

    def draw(self, context):
        layout = self.layout

        box = layout.box()
        box.label(text="Faces / Edges", icon="FACESEL")
        box.prop(self, "edges_width")
        box.prop(self, "faces_offset")
        box.prop(self, "edges_alpha")
        box.prop(self, "faces_alpha")

        box = layout.box()
        box.label(text="Points", icon="VERTEXSEL")
        box.prop(self, "point_size")
        box.prop(self, "points_offset")

        # ── Naming Policy ──────────────────────────────────────────────────
        box = layout.box()
        box.label(text="Naming Policy", icon="FILE_TEXT")

        def _draw_policy_domain(parent, domain_label, entries_prefix, entries_suffix,
                                op_add_pre, op_rm_pre, op_add_suf, op_rm_suf):
            sub = parent.box()
            sub.label(text=domain_label, icon="DOT")
            split = sub.split(factor=0.5)

            c = split.column(align=True)
            c.label(text="Prefixes:")
            for i, entry in enumerate(entries_prefix):
                row = c.row(align=True)
                row.prop(entry, "value", text="")
                op = row.operator(op_rm_pre, text="", icon="X", emboss=False)
                op.index = i
            c.operator(op_add_pre, text="Add", icon="ADD")

            c = split.column(align=True)
            c.label(text="Suffixes:")
            for i, entry in enumerate(entries_suffix):
                row = c.row(align=True)
                row.prop(entry, "value", text="")
                op = row.operator(op_rm_suf, text="", icon="X", emboss=False)
                op.index = i
            c.operator(op_add_suf, text="Add", icon="ADD")

        _draw_policy_domain(
            box, "Objects",
            self.naming_prefixes,    self.naming_suffixes,
            "asset_checker.naming_add_prefix",     "asset_checker.naming_remove_prefix",
            "asset_checker.naming_add_suffix",     "asset_checker.naming_remove_suffix",
        )
        _draw_policy_domain(
            box, "Collections",
            self.col_naming_prefixes, self.col_naming_suffixes,
            "asset_checker.col_naming_add_prefix",  "asset_checker.col_naming_remove_prefix",
            "asset_checker.col_naming_add_suffix",  "asset_checker.col_naming_remove_suffix",
        )

        hint = box.row()
        hint.enabled = False
        hint.label(text="Empty list = no requirement enforced", icon="INFO")

        # ── Texel Density settings ─────────────────────────────────────────
        box = layout.box()
        box.label(text="Texel Density", icon="UV")
        row = box.row(align=True)
        row.label(text="Texture Size:")
        row.prop(self, "uv_td_texture_size", text="")
        row = box.row(align=True)
        row.label(text="Target TD (px/cm):")
        row.prop(self, "uv_td_target", text="")
        row = box.row(align=True)
        row.label(text="Tolerance:")
        row.prop(self, "uv_td_tolerance", text="")
        hint = box.row()
        hint.enabled = False
        hint.label(text="Target TD = 0 → display only, no error flagging", icon="INFO")

        # ── UV Padding settings ────────────────────────────────────────────
        box = layout.box()
        box.label(text="UV Padding", icon="UV_FACESEL")
        row = box.row(align=True)
        row.label(text="Shell padding (px):")
        row.prop(self, "uv_padding_shell_px", text="")
        row = box.row(align=True)
        row.label(text="Tile border (px):")
        row.prop(self, "uv_padding_tile_px", text="")
        hint = box.row()
        hint.enabled = False
        hint.label(text="Pixel thresholds scale with Texture Size setting", icon="INFO")

        # ── UV Stretch settings ────────────────────────────────────────────
        box = layout.box()
        box.label(text="UV Stretch", icon="UV_DATA")
        row = box.row(align=True)
        row.label(text="Threshold (rad):")
        row.prop(self, "uv_stretch_threshold", text="")
        hint = box.row()
        hint.enabled = False
        hint.label(text="Angle diff > threshold → face flagged as stretched", icon="INFO")

        # ── Face Aspect Ratio ─────────────────────────────────────────────
        box = layout.box()
        box.label(text="Face Aspect Ratio", icon="MESH_GRID")
        row = box.row(align=True)
        row.label(text="Threshold (ratio):")
        row.prop(self, "face_aspect_ratio_threshold", text="")
        hint = box.row()
        hint.enabled = False
        hint.label(text="Quads above this ratio are flagged (default 6:1)", icon="INFO")

        # ── Check Thresholds ───────────────────────────────────────────────
        box = layout.box()
        box.label(text="Check Thresholds", icon="SETTINGS")
        hint = box.row()
        hint.enabled = False
        hint.label(text="Count ≤ threshold → yellow  ·  above → red  ·  0 = always red",
                   icon="INFO")
        col = box.column(align=True)
        for attr, label in (
            ("threshold_triangles", "Triangles"),
            ("threshold_ngons",     "Ngons"),
            ("threshold_poles",     "Poles"),
        ):
            row = col.row(align=True)
            row.label(text=label + ":")
            row.prop(self, attr, text="")

        # ── Pipeline check colors ──────────────────────────────────────────
        box = layout.box()
        box.label(text="Pipeline check colors", icon="MODIFIER")
        for attr in (
            "triangles_color", "ngons_color", "non_manifold_color", "boundary_edges_color",
            "isolated_verts_color", "duplicate_verts_color", "poles_color", "zero_area_color",
            "flipped_normals_color", "z_fighting_color", "invalid_normals_color",
            "non_applied_transform_color", "scale_color", "origin_at_zero_color",
            "modifier_stack_color",
            "face_aspect_ratio_color",
            "symmetry_x_color", "symmetry_y_color", "symmetry_z_color",
            "uv_single_set_color",
            "uv_overlap_color", "uv_micro_shell_color",
            "uv_texel_density_color", "uv_stretch_color", "uv_padding_color",
            "uv_udim_bounds_color", "uv_material_udim_color",
            "obj_naming_color", "col_naming_color", "mat_numbering_color",
            "mat_suffix_color", "mat_assignment_color", "missing_textures_color",
            "unused_data_color",
        ):
            prop_def = self.bl_rna.properties.get(attr)
            label = prop_def.name if prop_def else attr
            row = box.row(align=True)
            split = row.split(factor=0.55)
            split.label(text=label + ":")
            split.prop(self, attr, text="")
