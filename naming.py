# -*- coding:utf-8 -*-
"""
Centralised naming validation for Asset Checker.

Rule configuration lives entirely in NAMING_RULES — validation logic
never hard-codes individual names or patterns.

Entry points
------------
    NamingValidator.validate_object(obj)  -> List[ValidationResult]
    NamingMarker.update(objects)          -> None   (outliner collection)
    NamingMarker.clear()                  -> None
    NamingMarker.remove()                 -> None   (full cleanup on unregister)
"""

import re
import bpy
from dataclasses import dataclass
from typing import List

# ── Severity ──────────────────────────────────────────────────────────────────

INFO    = "INFO"
WARNING = "WARNING"
ERROR   = "ERROR"

SEVERITY_ICON = {
    ERROR:   "ERROR",    # red
    WARNING: "INFO",     # advisory (SEQUENCE_COLOR_* removed in Blender 5)
    INFO:    "DOT",      # neutral
}

# Numeric order for sorting / comparison
SEVERITY_ORDER = {INFO: 0, WARNING: 1, ERROR: 2}


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    object_name: str
    check:       str
    severity:    str   # INFO / WARNING / ERROR
    message:     str
    rule:        str = ""

    @property
    def is_blocking(self) -> bool:
        """True for WARNING and ERROR — counts toward check.count."""
        return self.severity in (WARNING, ERROR)


# ── Central naming rules ──────────────────────────────────────────────────────

NAMING_RULES = {
    "object": {
        "lowercase": True,
        "allowed_suffixes": [
            "_grp", "_geo", "_proxy",
            "_hero", "_mid", "_bg",
            "_left", "_right", "_front", "_back", "_top", "_bottom",
        ],
        # All entries MUST be lowercase — comparison is done on name.lower()
        "forbidden_base_names": [
            # ── Blender ──
            "cube", "sphere", "plane", "cylinder", "cone", "torus",
            "suzanne", "beziercircle", "beziercurve", "curve", "nurbs",
            "text", "camera", "light", "sun", "area", "spot",
            "empty", "armature", "lattice", "metaball",
            # ── Maya ──
            "pcube", "psphere", "pcylinder", "pcone", "pplane", "ptorus",
            "polysurface", "group", "locator",
            "nurbscircle", "nurbssphere", "nurbscylinder",
            "nurbscone", "nurbsplane",
            # ── 3ds Max ──
            "box", "teapot", "geosphere",
            "editable_poly", "editable_mesh", "editablepoly", "editablemesh",
            "dummy", "point", "line",
            # ── Generic traps ──
            "object", "mesh", "model", "asset",
        ],
    },
    "collection": {
        "lowercase": True,
        # _grp is no longer hardcoded — configure via col_naming_suffixes in preferences
        "skip_names": {"scene collection", "master collection"},
    },
    "material": {
        "lowercase": True,
        "required_suffix": "_mat",
    },
}


# ── Runtime naming policy (populated from preferences at validation time) ─────
#
# This is the code-level default — all lists empty = no requirement enforced.
# Actual values come from MeshCheckPreferences.naming_prefixes / naming_suffixes
# via get_active_policy().  The dict is keyed by asset-type so it can be extended
# to "material", "collection", "usd_prim" etc. without changing the validator API.

NAMING_POLICY: dict = {
    "object": {
        "required_prefixes": [],
        "required_suffixes": [],
    },
    "collection": {
        "required_prefixes": [],
        "required_suffixes": [],
    },
    # Future domains: "material", "usd_prim", "export_set"
}


def get_active_policy(prefs=None) -> dict:
    """Return the active naming policy merged from code defaults and preferences.

    When *prefs* is None the baseline (empty lists, no requirements) is returned.
    Pass the addon AddonPreferences instance to include user-configured rules.

    The returned dict is keyed by domain ("object", "collection", …).  Each
    domain has "required_prefixes" and "required_suffixes" lists — empty list
    means no requirement for that axis.
    """
    def _read(prop_name: str) -> list:
        return [
            e.value.strip().lower()
            for e in getattr(prefs, prop_name, ())
            if e.value.strip()
        ]

    if prefs is None:
        return {
            "object":     {"required_prefixes": [], "required_suffixes": []},
            "collection": {"required_prefixes": [], "required_suffixes": []},
        }

    return {
        "object": {
            "required_prefixes": _read("naming_prefixes"),
            "required_suffixes": _read("naming_suffixes"),
        },
        "collection": {
            "required_prefixes": _read("col_naming_prefixes"),
            "required_suffixes": _read("col_naming_suffixes"),
        },
    }


# ── NamingValidator ───────────────────────────────────────────────────────────

class NamingValidator:
    """
    Validates Blender object names against NAMING_RULES.

    All regex patterns are compiled once at class level — zero per-call
    compilation overhead.  The forbidden-name frozenset is also built once.
    """

    # ── Class-level cache (initialised on first call) ──────────────────────
    _forbidden_set: frozenset = None

    # Blender auto-numbers duplicates with ".NNN" suffix
    _pat_blender_num = re.compile(r"\.\d+$")
    # Maya / 3ds Max append bare digits: pCube1, Box003
    _pat_trailing_digits = re.compile(r"\d+$")
    # Characters that are problematic in file-system paths / pipelines
    _pat_forbidden_chars = re.compile(r'[\s/\\:*?"<>|]')

    # ── Initialisation ──────────────────────────────────────────────────────
    @classmethod
    def _ensure_cache(cls) -> None:
        if cls._forbidden_set is not None:
            return
        cls._forbidden_set = frozenset(
            NAMING_RULES["object"]["forbidden_base_names"]
        )

    # ── Internal helpers ────────────────────────────────────────────────────
    @classmethod
    def _strip_numbering(cls, name_lower: str) -> str:
        """
        Return the bare base name by stripping Blender /.NNN/ and trailing
        digits so that 'Cube.001' → 'cube' and 'pCube1' → 'pcube'.
        Only used for the forbidden-base-name comparison.
        """
        s = cls._pat_blender_num.sub("", name_lower)
        s = cls._pat_trailing_digits.sub("", s)
        return s.rstrip("_")

    # ── Public API ──────────────────────────────────────────────────────────
    @classmethod
    def validate_object(cls, obj, policy: dict = None) -> List[ValidationResult]:
        """Return a list of ValidationResult for obj.  May be empty (= clean).

        *policy* – result of get_active_policy().  When None (or empty lists),
        only the immutable code-level rules are applied and prefix/suffix
        requirements are skipped.
        """
        cls._ensure_cache()
        name: str = obj.name
        results: List[ValidationResult] = []

        # ── ERROR: empty / whitespace-only name ─────────────────────────────
        if not name.strip():
            return [ValidationResult(
                object_name=name, check="obj_naming",
                severity=ERROR, message="Empty object name",
                rule="empty_name",
            )]

        name_lower = name.lower()
        rules = NAMING_RULES["object"]

        # ── ERROR: forbidden filesystem characters or spaces ────────────────
        if cls._pat_forbidden_chars.search(name):
            results.append(ValidationResult(
                object_name=name, check="obj_naming",
                severity=ERROR,
                message=f"Forbidden characters or spaces in '{name}'",
                rule="forbidden_chars",
            ))

        # ── ERROR: Blender duplicate-numbering suffix (applies to any name) ──
        # e.g. "myobject.001" means Blender auto-renamed it due to a conflict.
        if cls._pat_blender_num.search(name):
            results.append(ValidationResult(
                object_name=name, check="obj_naming",
                severity=ERROR,
                message=f"Blender auto-numbering (name conflict): '{name}'",
                rule="blender_numbering",
            ))

        # ── ERROR: default DCC-generated base name ──────────────────────────
        # Strip all numbering first so "Cube.001" and "pCube1" both reduce
        # to their forbidden base.  "cube_hero" stays "cube_hero" → OK.
        base = cls._strip_numbering(name_lower)
        if base in cls._forbidden_set or name_lower in cls._forbidden_set:
            results.append(ValidationResult(
                object_name=name, check="obj_naming",
                severity=ERROR,
                message=f"Default DCC-generated name: '{name}'",
                rule="forbidden_base_name",
            ))

        # ── WARNING: uppercase letters ───────────────────────────────────────
        if rules.get("lowercase") and name != name_lower:
            results.append(ValidationResult(
                object_name=name, check="obj_naming",
                severity=WARNING,
                message=f"Name must be lowercase: '{name}'",
                rule="lowercase",
            ))

        # ── Policy-driven prefix / suffix checks ─────────────────────────────
        obj_policy = (policy or {}).get("object", {})
        req_prefixes: list = obj_policy.get("required_prefixes", [])
        req_suffixes: list = obj_policy.get("required_suffixes", [])

        # WARNING: configured prefixes present but name matches none
        if req_prefixes:
            if not any(name_lower.startswith(p) for p in req_prefixes):
                short = ", ".join(req_prefixes[:3])
                ellipsis = "…" if len(req_prefixes) > 3 else ""
                results.append(ValidationResult(
                    object_name=name, check="obj_naming",
                    severity=WARNING,
                    message=f"Missing required prefix ({short}{ellipsis})",
                    rule="missing_prefix",
                ))

        # WARNING: configured suffixes present but name matches none.
        # INFO fallback: no policy → use NAMING_RULES allowed_suffixes.
        if req_suffixes:
            if not any(name_lower.endswith(s) for s in req_suffixes):
                short = ", ".join(req_suffixes[:3])
                ellipsis = "…" if len(req_suffixes) > 3 else ""
                results.append(ValidationResult(
                    object_name=name, check="obj_naming",
                    severity=WARNING,
                    message=f"Missing required suffix ({short}{ellipsis})",
                    rule="missing_suffix",
                ))
        else:
            allowed = rules.get("allowed_suffixes", [])
            if allowed and not any(name_lower.endswith(s) for s in allowed):
                short = ", ".join(allowed[:3])
                results.append(ValidationResult(
                    object_name=name, check="obj_naming",
                    severity=INFO,
                    message=f"No recommended suffix ({short}…)",
                    rule="no_suffix",
                ))

        return results

    # ── Collection validation ────────────────────────────────────────────────
    @classmethod
    def validate_collection(cls, col, policy: dict = None) -> List[ValidationResult]:
        """Return naming issues for a Blender collection.  May be empty (= clean).

        Applies the same immutable checks as validate_object (chars, numbering,
        case) plus policy-driven prefix / suffix checks from the "collection"
        domain.  There is no forbidden-base-name list for collections.
        """
        name: str = col.name
        results: List[ValidationResult] = []

        if not name.strip():
            return [ValidationResult(
                object_name=name, check="col_naming",
                severity=ERROR, message="Empty collection name",
                rule="empty_name",
            )]

        name_lower = name.lower()

        # ERROR: forbidden filesystem characters
        if cls._pat_forbidden_chars.search(name):
            results.append(ValidationResult(
                object_name=name, check="col_naming",
                severity=ERROR,
                message=f"Forbidden characters in '{name}'",
                rule="forbidden_chars",
            ))

        # ERROR: Blender auto-numbering conflict
        if cls._pat_blender_num.search(name):
            results.append(ValidationResult(
                object_name=name, check="col_naming",
                severity=ERROR,
                message=f"Blender auto-numbering: '{name}'",
                rule="blender_numbering",
            ))

        # WARNING: uppercase letters
        if name != name_lower:
            results.append(ValidationResult(
                object_name=name, check="col_naming",
                severity=WARNING,
                message=f"Name must be lowercase: '{name}'",
                rule="lowercase",
            ))

        # Policy-driven prefix / suffix (configurable, no hardcoded _grp)
        col_policy = (policy or {}).get("collection", {})
        req_prefixes: list = col_policy.get("required_prefixes", [])
        req_suffixes: list = col_policy.get("required_suffixes", [])

        if req_prefixes:
            if not any(name_lower.startswith(p) for p in req_prefixes):
                short = ", ".join(req_prefixes[:3])
                ellipsis = "…" if len(req_prefixes) > 3 else ""
                results.append(ValidationResult(
                    object_name=name, check="col_naming",
                    severity=WARNING,
                    message=f"Missing required prefix ({short}{ellipsis})",
                    rule="missing_prefix",
                ))

        if req_suffixes:
            if not any(name_lower.endswith(s) for s in req_suffixes):
                short = ", ".join(req_suffixes[:3])
                ellipsis = "…" if len(req_suffixes) > 3 else ""
                results.append(ValidationResult(
                    object_name=name, check="col_naming",
                    severity=WARNING,
                    message=f"Missing required suffix ({short}{ellipsis})",
                    rule="missing_suffix",
                ))

        return results


# ── NamingMarker ──────────────────────────────────────────────────────────────

class NamingMarker:
    """
    Marks problematic objects in the Outliner via a dedicated collection.

    Objects are LINKED (not moved) — their original collections are intact.
    The quarantine collection is hidden from renders automatically.

    Lifecycle
    ---------
    update(objs) — called after obj_naming check runs
    clear()      — called when obj_naming check is disabled
    remove()     — called on addon unregister (full cleanup)
    """

    COLLECTION_NAME = "_AC_Issues"
    COLOR_TAG       = "COLOR_01"   # Red tag in Blender outliner

    @classmethod
    def update(cls, problem_objects) -> None:
        """Sync the quarantine collection with the current problem set."""
        col = cls._get_or_create()
        if col is None:
            return
        existing = set(col.objects)
        target   = set(problem_objects)
        for obj in existing - target:
            col.objects.unlink(obj)
        for obj in target - existing:
            try:
                col.objects.link(obj)
            except Exception:
                pass  # obj already linked or invalid

    @classmethod
    def clear(cls) -> None:
        """Remove all objects from the quarantine collection (keep collection)."""
        col = bpy.data.collections.get(cls.COLLECTION_NAME)
        if col:
            for obj in list(col.objects):
                col.objects.unlink(obj)

    @classmethod
    def remove(cls) -> None:
        """Full removal: unlink from all scenes and delete the collection."""
        col = bpy.data.collections.get(cls.COLLECTION_NAME)
        if not col:
            return
        for scene in bpy.data.scenes:
            try:
                scene.collection.children.unlink(col)
            except Exception:
                pass
        bpy.data.collections.remove(col)

    @classmethod
    def _get_or_create(cls):
        col = bpy.data.collections.get(cls.COLLECTION_NAME)
        if not col:
            try:
                col = bpy.data.collections.new(cls.COLLECTION_NAME)
                col.color_tag   = cls.COLOR_TAG
                col.hide_render = True
                bpy.context.scene.collection.children.link(col)
            except Exception as exc:
                print(f"[AssetChecker] NamingMarker._get_or_create: {exc}")
                return None
        return col


# ── NamingAudit — scene-wide pipeline gate check ─────────────────────────────

class NamingAudit:
    """
    Scene-wide naming audit — explicit pipeline gate check.

    Unlike the live per-object checks (obj_naming / col_naming) that run only
    on selected MESH objects, NamingAudit scans ALL objects and ALL collections
    in the current scene on demand.

    Results persist as a class variable until the next run() or clear().

    Lifecycle
    ---------
    run(policy)  — populate _results from the active scene
    clear()      — discard results
    """

    _results: list = []
    _ran:     bool = False

    @classmethod
    def run(cls, policy: dict = None) -> None:
        results = []

        # All objects regardless of type
        for obj in bpy.data.objects:
            results.extend(NamingValidator.validate_object(obj, policy=policy))

        # Collections — skip scene root (by reference) and well-known system names
        skip_lower = NAMING_RULES["collection"].get("skip_names", set())
        try:
            scene_root = bpy.context.scene.collection
        except Exception:
            scene_root = None
        for col in bpy.data.collections:
            if col is scene_root:
                continue
            if col.name.lower() in skip_lower:
                continue
            results.extend(NamingValidator.validate_collection(col, policy=policy))

        cls._results = results
        cls._ran = True

    @classmethod
    def clear(cls) -> None:
        cls._results = []
        cls._ran = False

    @classmethod
    def error_count(cls) -> int:
        return sum(1 for r in cls._results if r.severity == ERROR)

    @classmethod
    def warning_count(cls) -> int:
        return sum(1 for r in cls._results if r.severity == WARNING)

    @classmethod
    def is_clean(cls) -> bool:
        return cls._ran and not any(r.severity in (WARNING, ERROR) for r in cls._results)


# ── Operators ─────────────────────────────────────────────────────────────────

class ASSET_CHECKER_OT_run_naming_audit(bpy.types.Operator):
    """Run a full scene naming audit against the active naming policy"""
    bl_idname  = "asset_checker.run_naming_audit"
    bl_label   = "Run Naming Audit"
    bl_options = {'REGISTER'}

    def execute(self, context):
        addon_name = __name__.split(".")[0]
        try:
            prefs  = context.preferences.addons[addon_name].preferences
            policy = get_active_policy(prefs)
        except Exception:
            policy = get_active_policy(None)
        NamingAudit.run(policy=policy)
        blocking = NamingAudit.error_count() + NamingAudit.warning_count()
        if blocking:
            self.report({'WARNING'}, f"Audit: {blocking} issue(s) in {len(NamingAudit._results)} result(s)")
        else:
            self.report({'INFO'}, "Audit: scene naming is clean")
        return {'FINISHED'}


class ASSET_CHECKER_OT_clear_naming_audit(bpy.types.Operator):
    """Clear the naming audit results"""
    bl_idname  = "asset_checker.clear_naming_audit"
    bl_label   = "Clear Audit Results"
    bl_options = {'REGISTER'}

    def execute(self, context):
        NamingAudit.clear()
        return {'FINISHED'}


# ── Operator: select & frame object ──────────────────────────────────────────

class ASSET_CHECKER_OT_select_object(bpy.types.Operator):
    """Select and frame this object in the 3D viewport"""
    bl_idname  = "asset_checker.select_object"
    bl_label   = "Select Object"
    bl_options = {'REGISTER', 'UNDO'}

    object_name: bpy.props.StringProperty()

    def execute(self, context):
        obj = bpy.data.objects.get(self.object_name)
        if not obj:
            self.report({'WARNING'}, f"Object '{self.object_name}' not found")
            return {'CANCELLED'}

        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        context.view_layer.objects.active = obj

        # Frame the object in any available 3D viewport
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                for region in area.regions:
                    if region.type == 'WINDOW':
                        with context.temp_override(area=area, region=region):
                            bpy.ops.view3d.view_selected()
                        break
                break

        return {'FINISHED'}


# ── HierarchyValidator ────────────────────────────────────────────────────────
#
# Pipeline hierarchy rules (Empty-based, Maya-compatible):
#
#   asset_root_grp                    EMPTY · no parent · ends _grp
#     functional_layer_grp            EMPTY · name_base in whitelist
#       part_group_grp                EMPTY · ends _grp
#         part_name                   MESH  · == parent_base
#         part_name_01                MESH  · parent_base + _NN
#       mesh_name                     MESH  · direct under functional layer
#     static_grp                      another functional layer
#       mesh_name                     MESH
#
# All EMPTYs must end with "_grp".
# All names must be lowercase, no .NNN Blender numbering, no DCC defaults.

import re as _re

# ── Role constants ─────────────────────────────────────────────────────────────

_ROLE_ASSET_ROOT       = "asset_root"        # EMPTY, top-level (no parent in scene)
_ROLE_FUNCTIONAL_LAYER = "functional_layer"  # EMPTY, child of asset_root
_ROLE_PART_GROUP       = "part_group"        # EMPTY, child of functional_layer / part_group
_ROLE_MESH_UNDER_GROUP = "mesh_group"        # MESH, child of part_group
_ROLE_MESH_DIRECT      = "mesh_direct"       # MESH, child of functional_layer / asset_root
_ROLE_ORPHAN_EMPTY     = "orphan_empty"      # EMPTY not reachable from any asset root
_ROLE_ORPHAN_MESH      = "orphan_mesh"       # MESH not reachable from any asset root
_ROLE_SCENE            = "scene"             # pseudo-role for scene-level issues

_ROLE_ICONS: dict = {
    _ROLE_ASSET_ROOT:       "EMPTY_AXIS",
    _ROLE_FUNCTIONAL_LAYER: "GROUP",
    _ROLE_PART_GROUP:       "OUTLINER_OB_EMPTY",
    _ROLE_MESH_UNDER_GROUP: "MESH_DATA",
    _ROLE_MESH_DIRECT:      "MESH_DATA",
    _ROLE_ORPHAN_EMPTY:     "QUESTION",
    _ROLE_ORPHAN_MESH:      "QUESTION",
    _ROLE_SCENE:            "WORLD",
}

_ROLE_LABELS: dict = {
    _ROLE_ASSET_ROOT:       "Asset Root",
    _ROLE_FUNCTIONAL_LAYER: "Functional Layer",
    _ROLE_PART_GROUP:       "Part Group",
    _ROLE_MESH_UNDER_GROUP: "Mesh (grouped)",
    _ROLE_MESH_DIRECT:      "Mesh (direct)",
    _ROLE_ORPHAN_EMPTY:     "Orphan Empty",
    _ROLE_ORPHAN_MESH:      "Orphan Mesh",
}


# ── HierarchyIssue ─────────────────────────────────────────────────────────────

@dataclass
class HierarchyIssue:
    obj_name: str
    severity: str    # INFO / WARNING / ERROR
    rule:     str    # machine-readable key for grouping / filtering
    message:  str
    role:     str = ""  # detected role of this node


# ── HierarchyResult ────────────────────────────────────────────────────────────

@dataclass
class HierarchyResult:
    """Immutable snapshot of a single hierarchy scan."""
    issues:          list   # List[HierarchyIssue]
    asset_roots:     list   # List[str] — names of detected asset roots
    node_roles:      dict   # {obj_name: role_str}
    children_of:     dict   # {obj_name: [child_name, ...]} — for tree rendering
    objects_scanned: int

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == WARNING)

    @property
    def blocking_count(self) -> int:
        return sum(1 for i in self.issues if i.severity in (WARNING, ERROR))

    def issues_for(self, obj_name: str) -> list:
        return [i for i in self.issues if i.obj_name == obj_name]

    def is_clean(self) -> bool:
        return not any(i.severity in (WARNING, ERROR) for i in self.issues)


# ── HierarchyValidator ─────────────────────────────────────────────────────────

class HierarchyValidator:
    """
    Validates the pipeline-oriented object hierarchy in the active scene.

    Use HierarchyValidator.scan_scene(prefs=prefs) to produce a HierarchyResult.
    The validator is stateless — all state lives in the returned HierarchyResult.
    """

    DEFAULT_FUNCTIONAL_LAYERS: frozenset = frozenset({
        'animated', 'static', 'proxy', 'rig', 'fx', 'geo',
        'ctrl', 'export', 'ref', 'template', 'lod',
        'collision', 'shadow', 'physics', 'render', 'sim',
        'hi', 'lo', 'mid', 'cache', 'cloth', 'hair', 'fluid',
    })

    # Reuse compiled patterns from NamingValidator (set after NamingValidator defined)
    _pat_blender_num     = NamingValidator._pat_blender_num
    _pat_forbidden_chars = NamingValidator._pat_forbidden_chars
    _pat_trailing_digits = NamingValidator._pat_trailing_digits

    # ── Public API ─────────────────────────────────────────────────────────────

    @classmethod
    def scan_scene(cls, prefs=None) -> HierarchyResult:
        """Scan active scene objects, classify roles, validate each node.

        Returns a HierarchyResult. Never raises — all exceptions are caught
        internally so the panel stays stable even on corrupt scene state.
        """
        # Build functional layer whitelist: built-ins + user-configured entries
        layer_names: set = set(cls.DEFAULT_FUNCTIONAL_LAYERS)
        if prefs is not None:
            for entry in getattr(prefs, 'hierarchy_layer_names', ()):
                v = entry.value.strip().lower()
                if v:
                    layer_names.add(v)

        try:
            scene_objects = list(bpy.context.scene.objects)
        except Exception:
            return HierarchyResult(
                issues=[], asset_roots=[], node_roles={},
                children_of={}, objects_scanned=0,
            )

        # Ensure NamingValidator forbidden-name cache is warm
        NamingValidator._ensure_cache()

        # ── Phase 1: classify roles via BFS ──────────────────────────────────
        node_roles: dict  = {}   # {obj_name: role}
        scene_obj_set     = set(scene_objects)

        # Build parent→children index (scene-local only)
        _children_raw: dict = {}   # {obj: [child_obj, ...]}
        for obj in scene_objects:
            p = obj.parent
            if p is not None and p in scene_obj_set:
                _children_raw.setdefault(p, []).append(obj)

        # Find asset roots: EMPTY without a parent inside the scene
        asset_root_objs: list = []
        for obj in scene_objects:
            if obj.type != 'EMPTY':
                continue
            if obj.parent is not None and obj.parent in scene_obj_set:
                continue
            asset_root_objs.append(obj)
            node_roles[obj.name] = _ROLE_ASSET_ROOT

        # BFS from each root; propagate role context downwards
        #   queue item: (obj, parent_role)
        queue: list = []
        for root in asset_root_objs:
            for child in _children_raw.get(root, []):
                queue.append((child, _ROLE_ASSET_ROOT))

        while queue:
            obj, parent_role = queue.pop(0)
            name = obj.name
            if name in node_roles:
                continue  # already classified (multi-parented edge case)

            if obj.type == 'EMPTY':
                if parent_role == _ROLE_ASSET_ROOT:
                    role = _ROLE_FUNCTIONAL_LAYER
                else:
                    # EMPTY under functional_layer or deeper → part_group
                    role = _ROLE_PART_GROUP
            elif obj.type == 'MESH':
                if parent_role == _ROLE_PART_GROUP:
                    role = _ROLE_MESH_UNDER_GROUP
                else:
                    # MESH directly under functional_layer or asset_root
                    role = _ROLE_MESH_DIRECT
            else:
                # ARMATURE, CAMERA, LIGHT, … — not a hierarchy concern
                role = _ROLE_ORPHAN_MESH

            node_roles[name] = role
            for child in _children_raw.get(obj, []):
                queue.append((child, role))

        # Anything not reached by BFS → orphan
        for obj in scene_objects:
            if obj.name not in node_roles:
                node_roles[obj.name] = (
                    _ROLE_ORPHAN_EMPTY if obj.type == 'EMPTY'
                    else _ROLE_ORPHAN_MESH
                )

        # ── Build children_of (name-keyed, for UI tree rendering) ─────────────
        children_of: dict = {}
        for obj in scene_objects:
            obj_name = obj.name
            if obj_name not in children_of:
                children_of[obj_name] = []
            p = obj.parent
            if p is not None and p in scene_obj_set:
                children_of.setdefault(p.name, []).append(obj_name)

        # ── Phase 2: generate issues ───────────────────────────────────────────
        issues: list = []

        # Scene-level checks
        if len(asset_root_objs) == 0:
            issues.append(HierarchyIssue(
                obj_name="[scene]", severity=WARNING,
                rule="no_asset_root",
                message="No asset root found — expected one EMPTY without parent",
                role=_ROLE_SCENE,
            ))
        elif len(asset_root_objs) > 1:
            names_sample = ", ".join(o.name for o in asset_root_objs[:3])
            extra = f" +{len(asset_root_objs) - 3} more" if len(asset_root_objs) > 3 else ""
            issues.append(HierarchyIssue(
                obj_name="[scene]", severity=WARNING,
                rule="multiple_asset_roots",
                message=f"Multiple asset roots: {names_sample}{extra}",
                role=_ROLE_SCENE,
            ))

        # Per-object validation
        for obj in scene_objects:
            role = node_roles.get(obj.name, _ROLE_ORPHAN_MESH)
            issues.extend(cls._validate_node(obj, role, layer_names))

        return HierarchyResult(
            issues=issues,
            asset_roots=[o.name for o in asset_root_objs],
            node_roles=node_roles,
            children_of=children_of,
            objects_scanned=len(scene_objects),
        )

    # ── Node validator ─────────────────────────────────────────────────────────

    @classmethod
    def _validate_node(cls, obj, role: str, layer_names: set) -> list:
        """Return HierarchyIssue list for a single object."""
        issues  = []
        name    = obj.name
        name_lo = name.lower()

        # ── Common rules — all object types ──────────────────────────────────

        # ERROR: forbidden filesystem characters / spaces
        if cls._pat_forbidden_chars.search(name):
            issues.append(HierarchyIssue(
                obj_name=name, severity=ERROR,
                rule="forbidden_chars",
                message=f"Forbidden characters or spaces: '{name}'",
                role=role,
            ))

        # ERROR: Blender auto-numbering (.001 suffix = name conflict)
        if cls._pat_blender_num.search(name):
            issues.append(HierarchyIssue(
                obj_name=name, severity=ERROR,
                rule="blender_numbering",
                message=f"Blender numbering conflict: '{name}'",
                role=role,
            ))

        # ERROR: default DCC-generated base name
        base = cls._pat_trailing_digits.sub(
            "", cls._pat_blender_num.sub("", name_lo)
        ).rstrip("_")
        if base in NamingValidator._forbidden_set or name_lo in NamingValidator._forbidden_set:
            issues.append(HierarchyIssue(
                obj_name=name, severity=ERROR,
                rule="forbidden_base_name",
                message=f"Default DCC name: '{name}'",
                role=role,
            ))

        # WARNING: uppercase letters
        if name != name_lo:
            issues.append(HierarchyIssue(
                obj_name=name, severity=WARNING,
                rule="lowercase",
                message=f"Name must be lowercase: '{name}'",
                role=role,
            ))

        # ── EMPTY-specific rules ──────────────────────────────────────────────
        if obj.type == 'EMPTY':
            # ERROR: every Empty in the hierarchy must end with _grp
            if not name_lo.endswith('_grp'):
                issues.append(HierarchyIssue(
                    obj_name=name, severity=ERROR,
                    rule="missing_grp_suffix",
                    message=f"Empty must end with '_grp': '{name}'",
                    role=role,
                ))

            # WARNING: functional layer name not in whitelist
            if role == _ROLE_FUNCTIONAL_LAYER:
                layer_base = name_lo[:-4] if name_lo.endswith('_grp') else name_lo
                if layer_base not in layer_names:
                    known_sorted = sorted(layer_names)[:5]
                    issues.append(HierarchyIssue(
                        obj_name=name, severity=WARNING,
                        rule="unknown_functional_layer",
                        message=(
                            f"'{layer_base}' not in functional layer whitelist "
                            f"({', '.join(known_sorted)}…)"
                        ),
                        role=role,
                    ))

            # WARNING: orphan Empty (not connected to any asset root)
            if role == _ROLE_ORPHAN_EMPTY:
                issues.append(HierarchyIssue(
                    obj_name=name, severity=WARNING,
                    rule="orphan_empty",
                    message=f"Empty not connected to any asset root: '{name}'",
                    role=role,
                ))

        # ── MESH-specific rules ───────────────────────────────────────────────
        elif obj.type == 'MESH':
            # WARNING: orphan Mesh (not connected to any asset root)
            if role == _ROLE_ORPHAN_MESH:
                issues.append(HierarchyIssue(
                    obj_name=name, severity=WARNING,
                    rule="orphan_mesh",
                    message=f"Mesh not connected to any asset root: '{name}'",
                    role=role,
                ))

            # WARNING: mesh name doesn't match parent group base name
            if role == _ROLE_MESH_UNDER_GROUP and obj.parent is not None:
                parent_lo = obj.parent.name.lower()
                # strip _grp to get the shared base
                parent_base = parent_lo[:-4] if parent_lo.endswith('_grp') else parent_lo
                # Valid: parent_base  OR  parent_base_<digits>
                pat = _re.compile(
                    r'^' + _re.escape(parent_base) + r'(_\d+)?$'
                )
                if not pat.match(name_lo):
                    issues.append(HierarchyIssue(
                        obj_name=name, severity=WARNING,
                        rule="parent_mismatch",
                        message=(
                            f"Expected '{parent_base}' or '{parent_base}_NN', "
                            f"got '{name}'"
                        ),
                        role=role,
                    ))

        return issues


# ── Operators ──────────────────────────────────────────────────────────────────

class ASSET_CHECKER_OT_scan_hierarchy(bpy.types.Operator):
    """Scan the scene hierarchy against pipeline naming conventions"""
    bl_idname  = "asset_checker.scan_hierarchy"
    bl_label   = "Scan Hierarchy"
    bl_options = {'REGISTER'}

    def execute(self, context):
        addon_name = __name__.split(".")[0]
        try:
            prefs = context.preferences.addons[addon_name].preferences
        except Exception:
            prefs = None

        from .manager import MeshCheck
        result = HierarchyValidator.scan_scene(prefs=prefs)
        MeshCheck.hierarchy_result = result

        if result.is_clean():
            self.report(
                {'INFO'},
                f"Hierarchy: clean  ({result.objects_scanned} objects scanned)",
            )
        else:
            self.report(
                {'WARNING'},
                f"Hierarchy: {result.blocking_count} issue(s)  "
                f"({result.objects_scanned} objects scanned)",
            )
        return {'FINISHED'}


class ASSET_CHECKER_OT_clear_hierarchy(bpy.types.Operator):
    """Clear the hierarchy scan results"""
    bl_idname  = "asset_checker.clear_hierarchy"
    bl_label   = "Clear Hierarchy"
    bl_options = {'REGISTER'}

    def execute(self, context):
        from .manager import MeshCheck
        MeshCheck.hierarchy_result = None
        return {'FINISHED'}
