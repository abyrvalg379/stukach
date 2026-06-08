# -*- coding:utf-8 -*-
import bpy
import bmesh
import gpu
import json as _json
from gpu_extras.batch import batch_for_shader
from .core import CHECK_TYPES


def _apply_obj_ignore(checker, obj, name: str) -> None:
    """Set or clear the ``_ignored`` flag on *checker* based on the object's ignore list.

    Always resets the flag first so that un-ignoring a check (removing it from
    the JSON list) is reflected immediately without a separate cleanup pass.

    Hot path: if ``_ac_ignore`` is not set or the check name is not even present
    as a substring, we skip JSON parsing entirely.
    """
    import json as _j
    checker._ignored = False           # always reset so un-ignore works
    raw = obj.get("_ac_ignore", "")
    if not raw or name not in raw:     # fast path — nothing to ignore
        return
    try:
        if name in _j.loads(raw):
            checker._ignored = True
    except Exception:
        pass


class MeshCheckObject:
    MESH_DATAS = ('verts', 'edges', 'faces')

    # Checks whose results depend only on UV data, not 3-D topology.
    # update_datas() skips these when topo changed but UV didn't, and vice-versa.
    _UV_CHECKS = frozenset({
        'uv_overlap', 'uv_padding', 'uv_micro_shell', 'uv_texel_density',
        'uv_stretch', 'uv_single_set', 'uv_udim_bounds',
    })

    # Checks whose results depend on the object's world transform (location /
    # rotation / scale), not on mesh topology or UV data.  They re-run whenever
    # the transform key changes, independently of topo/UV flags.
    _TRANSFORM_CHECKS = frozenset({
        'origin_at_zero', 'non_applied_transform', 'scale',
    })

    def __init__(self, obj):
        self._object = obj
        self._bm_object = None
        self._verts = self._edges = self._faces = self._tris = 0
        self._checks = {name: cls(self) for name, cls in CHECK_TYPES.items()}
        self._mesh_key: tuple = ()   # (n_verts, n_edges, n_faces) — topology dirty flag
        self._uv_key:   tuple = ()   # sampled UV hash — UV-coords dirty flag
        self._transform_key: tuple = ()  # (loc, rot, scale) — transform dirty flag
        self._mat_udim_map: dict = {}
        # Shared KD-tree for SymmetryX / SymmetryY / SymmetryZ (built once per topology change)
        self._sym_kd_key:   tuple = ()
        self._sym_kd_cache  = None   # mathutils.kdtree.KDTree
        self._sym_kd_co           = None   # numpy (n_verts, 3) float32, rebuilt per topology  # mat_name → set of (tile_u, tile_v)
        self._init_object()

    @staticmethod
    def _sample_transform_key(obj) -> tuple:
        """Cheap transform dirty-detector: packs world position + local rot/scale.

        World translation (matrix_world.translation) is used for the location
        component so that moving a parent also triggers re-evaluation of child
        objects (e.g. origin_at_zero on parented meshes).
        Local rotation_euler and scale are kept as-is — their world equivalents
        can be derived from matrix_world but these are sufficient for the
        non_applied_transform / scale checks which only inspect local values.
        """
        loc = obj.matrix_world.translation   # world position of the origin
        rot = obj.rotation_euler
        scl = obj.scale
        return (round(loc.x, 5), round(loc.y, 5), round(loc.z, 5),
                round(rot.x, 5), round(rot.y, 5), round(rot.z, 5),
                round(scl.x, 5), round(scl.y, 5), round(scl.z, 5))

    def _init_object(self):
        bm = self.set_bm_object()
        self._mesh_key      = (len(bm.verts), len(bm.edges), len(bm.faces))
        self._uv_key        = self._sample_uv_key(bm)
        self._transform_key = self._sample_transform_key(self._object)
        self.update_datas(bm)

    @staticmethod
    def _sample_uv_key(bm) -> tuple:
        """Cheap UV dirty-detector: samples UV coords from ~16 faces.

        Returns a tuple that changes whenever UV coordinates change, without
        reading the entire UV array.  False-negative rate is negligible for
        real packing / unwrap operations.
        """
        uv_layer = bm.loops.layers.uv.active
        if not uv_layer:
            return (0,)
        n = len(bm.faces)
        if n == 0:
            return (0,)
        bm.faces.ensure_lookup_table()
        step = max(1, n // 16)
        xsum = 0
        for i in range(0, n, step):
            face = bm.faces[i]
            if face.loops:
                uv = face.loops[0][uv_layer].uv
                xsum ^= hash((round(uv.x, 5), round(uv.y, 5)))
        return (n, uv_layer.name, xsum)

    def set_bm_object(self):
        me = self._object.data
        if self._bm_object and self._bm_object.is_valid and not me.is_editmode:
            try:
                self._bm_object.free()
            except Exception:
                pass
        if me.is_editmode:
            self._bm_object = bmesh.from_edit_mesh(me)
        else:
            bm = bmesh.new()
            bm.from_mesh(me)
            self._bm_object = bm
        return self._bm_object

    def update_datas(self, bm, *, uv_changed: bool = True, topo_changed: bool = True,
                     transform_changed: bool = True):
        mc = bpy.context.window_manager.mesh_check_props

        if topo_changed:
            for d in self.MESH_DATAS:
                setattr(self, f"_{d}", len(getattr(bm, d)))
            # Faster than bm.calc_loop_triangles() which builds 200k+ Python objects
            import numpy as _np
            _me = self._object.data
            _n_polys = len(_me.polygons)
            if _n_polys > 0:
                _pt = _np.empty(_n_polys, dtype=_np.int32)
                _me.polygons.foreach_get("loop_total", _pt)
                self._tris = int((_pt - 2).sum())
            else:
                self._tris = 0
            del _np, _me, _n_polys

        from .core import _uv_island_cache, _uv_membership_cache, build_material_udim_map
        _uv_island_cache.clear()
        _uv_membership_cache.clear()

        # Always rebuild material→UDIM map so the UV panel stays in sync
        # regardless of which checks are active.
        try:
            self._mat_udim_map = build_material_udim_map(self._object, bm)
        except Exception as e:
            print(f"[AssetChecker] mat_udim_map error: {e}")
        UVCheckGPU._mat_highlight_dirty = True

        ran_uv_padding = False
        for name, checker in self._checks.items():
            if not getattr(mc, name, False):
                continue
            is_uv        = name in self._UV_CHECKS
            is_transform = name in self._TRANSFORM_CHECKS
            if is_uv and not uv_changed:
                continue
            if is_transform and not transform_changed:
                continue
            if not is_uv and not is_transform and not topo_changed:
                continue
            try:
                checker.set_datas()
                _apply_obj_ignore(checker, self._object, name)
                checker._gpu_dirty = True
                checker._uv_gpu_dirty = True
                if name == 'uv_padding':
                    ran_uv_padding = True
            except Exception as e:
                print(f"[AssetChecker] Error in {name}: {e}")

        # Re-run global padding after this object's UV data is refreshed
        if ran_uv_padding:
            try:
                MeshCheck._run_global_uv_padding()
            except Exception as e:
                print(f"[AssetChecker] Global UV padding (live) error: {e}")

    @property
    def bm_object(self):
        if not self._bm_object or not self._bm_object.is_valid:
            bm = self.set_bm_object()
            self._mesh_key      = (len(bm.verts), len(bm.edges), len(bm.faces))
            self._uv_key        = self._sample_uv_key(bm)
            self._transform_key = self._sample_transform_key(self._object)
            self.update_datas(bm)
        return self._bm_object

    @property
    def stats(self):
        return self._verts, self._edges, self._faces, self._tris

    def is_updated_datas(self, bm):
        return any(getattr(self, f"_{d}") != len(getattr(bm, d)) for d in self.MESH_DATAS)


class MeshCheckGPU:
    _handler = None
    _shader = None
    _batch_cache: dict = {}

    _FACE_OVERLAY_CHECKS = {'zero_area', 'triangles', 'ngons', 'uv_stretch',
                            'invalid_normals', 'uv_material_udim'}
    # flipped_normals excluded — uses Blender's built-in Face Orientation overlay
    _THICK_LINE_CHECKS   = {'non_applied_transform', 'scale',
                            'modifier_stack', 'origin_at_zero'}

    @classmethod
    def get_shader(cls):
        if cls._shader is None:
            cls._shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        return cls._shader

    @classmethod
    def setup_handler(cls):
        if not cls._handler:
            cls._handler = bpy.types.SpaceView3D.draw_handler_add(
                cls.draw, (), 'WINDOW', 'POST_VIEW'
            )

    @classmethod
    def remove_handler(cls):
        if cls._handler:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(cls._handler, 'WINDOW')
            except Exception:
                pass
            finally:
                cls._handler = None
        cls._batch_cache.clear()

    @classmethod
    def _rebuild_checker_batches(cls, checker, check, prefs, offset, pt_offset):
        shader = cls.get_shader()
        entry = {'offset': offset, 'pt_offset': pt_offset}

        # Ignored checks get empty batches — no overlay, no wasted GPU bandwidth.
        if getattr(checker, '_ignored', False):
            entry['edge'] = entry['face'] = entry['point'] = None
            cls._batch_cache[id(checker)] = entry
            checker._gpu_dirty = False
            return

        coords = checker.get_edges(offset)
        entry['edge'] = (
            batch_for_shader(shader, 'LINES', {"pos": coords}) if coords else None
        )

        if check in cls._FACE_OVERLAY_CHECKS:
            faces, face_idx = checker.get_faces(offset)
            entry['face'] = (
                batch_for_shader(shader, 'TRIS', {"pos": faces}, indices=face_idx)
                if (faces and face_idx) else None
            )
        else:
            entry['face'] = None

        pts = checker.get_points(pt_offset)
        entry['point'] = (
            batch_for_shader(shader, 'POINTS', {"pos": pts}) if pts else None
        )

        cls._batch_cache[id(checker)] = entry
        checker._gpu_dirty = False

    @staticmethod
    def remap_color(prefs, check):
        c = getattr(prefs, f"{check}_color", (1.0, 0.0, 0.0))
        return (*c[:3], getattr(prefs, 'edges_alpha', 1.0))

    @classmethod
    def draw(cls):
        ctx = bpy.context
        if not ctx.object:
            return
        if not ctx.space_data.shading.show_xray:
            gpu.state.depth_test_set('LESS')

        mc = ctx.window_manager.mesh_check_props
        addon_name = __name__.split(".")[0]
        try:
            prefs = ctx.preferences.addons[addon_name].preferences
        except Exception:
            prefs = None
        if not prefs or not MeshCheck.objects:
            gpu.state.depth_test_set('NONE')
            return

        offset = prefs.faces_offset
        pt_offset = prefs.points_offset
        shader = cls.get_shader()
        shader.bind()

        try:
            for check in mc.checker_options:
                if not getattr(mc, check, False):
                    continue
                for mc_obj in MeshCheck.objects.values():
                    checker = mc_obj._checks.get(check)
                    if not checker:
                        continue
                    try:
                        cid = id(checker)
                        cached = cls._batch_cache.get(cid)

                        if (checker._gpu_dirty or cached is None
                                or cached['offset'] != offset
                                or cached['pt_offset'] != pt_offset):
                            cls._rebuild_checker_batches(checker, check, prefs, offset, pt_offset)
                            cached = cls._batch_cache[cid]

                        color = cls.remap_color(prefs, check)

                        if cached['face']:
                            shader.uniform_float("color", (*color[:3], prefs.faces_alpha))
                            gpu.state.blend_set("ALPHA")
                            gpu.state.depth_test_set('NONE')
                            gpu.state.face_culling_set('NONE')
                            cached['face'].draw(shader)

                        if cached['edge']:
                            w = 4.0 if check in cls._THICK_LINE_CHECKS else prefs.edges_width
                            shader.uniform_float("color", color)
                            gpu.state.blend_set("ALPHA")
                            gpu.state.line_width_set(w)
                            # Z-fighting faces can be buried inside a mesh (interior
                            # duplicates hidden by outer skin).  Draw them with NONE
                            # depth test so they are always visible through surfaces.
                            if check == 'z_fighting':
                                gpu.state.depth_test_set('NONE')
                            cached['edge'].draw(shader)
                            if check == 'z_fighting' and not ctx.space_data.shading.show_xray:
                                gpu.state.depth_test_set('LESS')

                        if cached['point']:
                            shader.uniform_float("color", color)
                            gpu.state.point_size_set(prefs.point_size)
                            # Points mark specific problem vertices/centroids and must
                            # always be visible regardless of camera distance.
                            # Switch off depth test so surface geometry never occludes them,
                            # then restore the per-viewport setting for subsequent batches.
                            gpu.state.depth_test_set('NONE')
                            cached['point'].draw(shader)
                            if not ctx.space_data.shading.show_xray:
                                gpu.state.depth_test_set('LESS')

                    except Exception as e:
                        print(f"[AssetChecker] Draw error in {check}: {e}")
        finally:
            # Always restore GPU state so Blender's own rendering is not affected.
            gpu.state.blend_set("NONE")
            gpu.state.depth_test_set('NONE')
            gpu.state.line_width_set(1.0)
            gpu.state.point_size_set(1.0)


class UVCheckGPU:
    """Draw handler для IMAGE_EDITOR — UV-оверлеи оверлапов и микрошеллов."""

    _handler = None
    _shader = None
    _batch_cache: dict = {}
    _UV_OVERLAY_CHECKS = {'uv_overlap', 'uv_micro_shell', 'uv_udim_bounds', 'uv_padding',
                          'uv_stretch', 'uv_material_udim'}

    # Material→UDIM highlight state
    _mat_highlight_batch = None
    _mat_highlight_key   = None   # (mat_name, frozenset of tiles) — rebuild when changed
    _mat_highlight_dirty = False  # set True by update_datas() to force rebuild

    @classmethod
    def get_shader(cls):
        if cls._shader is None:
            cls._shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        return cls._shader

    @classmethod
    def setup_handler(cls):
        if not cls._handler:
            cls._handler = bpy.types.SpaceImageEditor.draw_handler_add(
                cls.draw, (), 'WINDOW', 'POST_VIEW'
            )

    @classmethod
    def remove_handler(cls):
        if cls._handler:
            try:
                bpy.types.SpaceImageEditor.draw_handler_remove(cls._handler, 'WINDOW')
            except Exception:
                pass
            finally:
                cls._handler = None
        cls._batch_cache.clear()
        cls._mat_highlight_batch = None
        cls._mat_highlight_key   = None

    @classmethod
    def _rebuild_checker_uv_batches(cls, checker):
        """Build and cache UV face/edge batches for one checker."""
        if getattr(checker, '_ignored', False):
            cls._batch_cache[id(checker)] = {'face': None, 'edge': None}
            checker._uv_gpu_dirty = False
            return
        shader = cls.get_shader()
        faces, face_idx = checker.get_uv_faces()
        edges = checker.get_uv_edges()
        cls._batch_cache[id(checker)] = {
            'face': (
                batch_for_shader(shader, 'TRIS', {"pos": faces}, indices=face_idx)
                if (faces and face_idx) else None
            ),
            'edge': (
                batch_for_shader(shader, 'LINES', {"pos": edges}) if edges else None
            ),
        }
        checker._uv_gpu_dirty = False

    @classmethod
    def draw(cls):
        ctx = bpy.context
        mc = ctx.window_manager.mesh_check_props
        if not MeshCheck.objects:
            return

        addon_name = __name__.split(".")[0]
        try:
            prefs = ctx.preferences.addons[addon_name].preferences
        except Exception:
            return

        shader = cls.get_shader()
        shader.bind()

        for check in cls._UV_OVERLAY_CHECKS:
            if not getattr(mc, check, False):
                continue
            for mc_obj in MeshCheck.objects.values():
                checker = mc_obj._checks.get(check)
                if not checker:
                    continue

                cid = id(checker)
                if cls._batch_cache.get(cid) is None or checker._uv_gpu_dirty:
                    cls._rebuild_checker_uv_batches(checker)
                cached = cls._batch_cache[cid]

                if checker.count == 0:
                    continue

                color = MeshCheckGPU.remap_color(prefs, check)

                if cached['face']:
                    face_color = (*color[:3], prefs.faces_alpha)
                    shader.uniform_float("color", face_color)
                    gpu.state.blend_set("ALPHA")
                    cached['face'].draw(shader)

                if cached['edge']:
                    shader.uniform_float("color", color)
                    gpu.state.blend_set("ALPHA")
                    gpu.state.line_width_set(prefs.edges_width)
                    cached['edge'].draw(shader)

        gpu.state.blend_set("NONE")
        gpu.state.line_width_set(1.0)

        # ── Material→UDIM highlight ──────────────────────────────────────────
        selected_mat = mc.mat_udim_selected if hasattr(mc, 'mat_udim_selected') else ""
        if selected_mat:
            tiles: set = set()
            for mc_obj in MeshCheck.objects.values():
                tiles.update(mc_obj._mat_udim_map.get(selected_mat, set()))

            if tiles:
                new_key = (selected_mat, frozenset(tiles))
                if cls._mat_highlight_key != new_key or cls._mat_highlight_dirty:
                    coords = []
                    for tile_u, tile_v in tiles:
                        x0, y0 = float(tile_u),       float(tile_v)
                        x1, y1 = float(tile_u) + 1.0, float(tile_v) + 1.0
                        # Rectangle outline as 4 line segments (8 points)
                        coords.extend([
                            (x0, y0, 0.0), (x1, y0, 0.0),
                            (x1, y0, 0.0), (x1, y1, 0.0),
                            (x1, y1, 0.0), (x0, y1, 0.0),
                            (x0, y1, 0.0), (x0, y0, 0.0),
                        ])
                    cls._mat_highlight_batch = batch_for_shader(
                        shader, 'LINES', {"pos": coords})
                    cls._mat_highlight_key   = new_key
                    cls._mat_highlight_dirty = False

                if cls._mat_highlight_batch:
                    shader.uniform_float("color", (1.0, 0.6, 0.0, 1.0))  # orange
                    gpu.state.blend_set("ALPHA")
                    gpu.state.line_width_set(3.0)
                    cls._mat_highlight_batch.draw(shader)
                    gpu.state.line_width_set(1.0)
                    gpu.state.blend_set("NONE")


class MeshCheck:
    _mode = ""
    _scope: str = "SELECTED"          # "SELECTED" | "SCENE" | "COLLECTION"
    _scope_collection: str = ""       # collection name when scope == "COLLECTION"
    objects = {}
    hierarchy_result = None            # HierarchyResult | None — set by scan_hierarchy operator
    _state_restored: bool = False      # True after load_post restores settings; cleared on Run
    _scene_stale:   bool = False       # True when SCENE/COLLECTION has untracked objects

    @staticmethod
    def poll():
        mc = bpy.context.window_manager.mesh_check_props
        return mc.check_data and any(getattr(mc, p, False) for p in mc.checker_options)

    @classmethod
    def reset_mesh_check(cls):
        cls._mode = ""
        cls.objects.clear()
        MeshCheckGPU._batch_cache.clear()
        UVCheckGPU._batch_cache.clear()
        from .core import (_uv_island_cache, _uv_membership_cache,
                           _uv_padding_registry, _uv_padding_tile_stats)
        _uv_island_cache.clear()
        _uv_membership_cache.clear()
        _uv_padding_registry.clear()
        _uv_padding_tile_stats.clear()

    @classmethod
    def add_scene_objects(cls, wm=None):
        """Track every mesh object in the active scene.

        *wm* — optional WindowManager for progress reporting.
        When provided, calls wm.progress_begin / progress_update / progress_end
        so Blender shows OS-level progress (title-bar + taskbar) during heavy scans.
        """
        mesh_objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
        total = len(mesh_objs)
        if wm is not None and total:
            wm.progress_begin(0, total)
        for i, obj in enumerate(mesh_objs):
            if obj not in cls.objects:
                try:
                    cls.objects[obj] = MeshCheckObject(obj)
                except Exception as e:
                    print(f"[AssetChecker] Error adding {obj.name}: {e}")
            if wm is not None:
                wm.progress_update(i + 1)
        if wm is not None and total:
            wm.progress_end()
        try:
            cls._run_inter_object_z_fighting()
        except Exception as e:
            print(f"[AssetChecker] Inter-object Z-fighting error: {e}")
        try:
            cls._run_global_uv_padding()
        except Exception as e:
            print(f"[AssetChecker] Global UV padding error: {e}")

    @classmethod
    def add_collection_objects_from(cls, col, wm=None):
        """Track every mesh object in *col* (includes sub-collections via all_objects).

        *wm* — optional WindowManager for progress reporting.
        """
        mesh_objs = [o for o in col.all_objects if o.type == "MESH"]
        total = len(mesh_objs)
        if wm is not None and total:
            wm.progress_begin(0, total)
        for i, obj in enumerate(mesh_objs):
            if obj not in cls.objects:
                try:
                    cls.objects[obj] = MeshCheckObject(obj)
                except Exception as e:
                    print(f"[AssetChecker] Error adding {obj.name}: {e}")
            if wm is not None:
                wm.progress_update(i + 1)
        if wm is not None and total:
            wm.progress_end()
        try:
            cls._run_inter_object_z_fighting()
        except Exception as e:
            print(f"[AssetChecker] Inter-object Z-fighting error: {e}")
        try:
            cls._run_global_uv_padding()
        except Exception as e:
            print(f"[AssetChecker] Global UV padding error: {e}")

    @classmethod
    def set_mode(cls, s):
        cls._mode = s

    @classmethod
    def add_mesh_check_object(cls):
        for o in bpy.context.selected_objects:
            if o.type == "MESH" and o not in cls.objects:
                cls.objects[o] = MeshCheckObject(o)
        # Re-run inter-object Z-fighting when tracked set changes
        try:
            cls._run_inter_object_z_fighting()
        except Exception as e:
            print(f"[AssetChecker] Inter-object Z-fighting error: {e}")

    @classmethod
    def remove_mesh_check_object(cls, o):
        if o in cls.objects:
            mc_obj = cls.objects[o]
            for checker in mc_obj._checks.values():
                MeshCheckGPU._batch_cache.pop(id(checker), None)
                UVCheckGPU._batch_cache.pop(id(checker), None)
            del cls.objects[o]

    @classmethod
    def reset_mc_objects(cls):
        cls.objects.clear()
        MeshCheckGPU._batch_cache.clear()
        UVCheckGPU._batch_cache.clear()
        cls._repopulate_by_scope()

    @classmethod
    def _purge_stale_callbacks(cls):
        """Remove ALL asset_checker depsgraph handlers — including ones left over
        from previous addon reloads that were never properly unregistered.
        They share the same __qualname__ but are different function objects.
        """
        handlers = bpy.app.handlers.depsgraph_update_post
        stale = [
            h for h in handlers
            if getattr(h, '__qualname__', '') == cls.callback.__qualname__
        ]
        for h in stale:
            try:
                handlers.remove(h)
            except Exception:
                pass

    @classmethod
    def add_callback(cls):
        # Purge any stale callbacks from previous reloads before adding ours.
        cls._purge_stale_callbacks()
        cls._repopulate_by_scope()
        bpy.app.handlers.depsgraph_update_post.append(cls.callback)

    @classmethod
    def _repopulate_by_scope(cls):
        """Re-add objects according to the current validation scope."""
        if cls._scope == "SCENE":
            cls.add_scene_objects()
        elif cls._scope == "COLLECTION" and cls._scope_collection:
            col = bpy.data.collections.get(cls._scope_collection)
            if col:
                cls.add_collection_objects_from(col)
            else:
                cls.add_mesh_check_object()
        else:
            cls.add_mesh_check_object()

    @classmethod
    def remove_callback(cls):
        cls._purge_stale_callbacks()
        cls.reset_mesh_check()

    # Guard for inter-object Z-fighting: skip if total faces exceed this limit
    _INTER_Z_FIGHT_MAX_TOTAL_FACES: int = 500_000

    @classmethod
    def _run_inter_object_z_fighting(cls):
        """Detect Z-fighting between pairs of tracked objects (world space).

        Builds a world-space BVHTree for every tracked mesh, then tests all
        O(n²) pairs.  Only coplanar face pairs (normal dot > 0.99) are flagged.
        Results are injected into each object's ZFighting checker via
        checker.add_inter_results().
        """
        mc = bpy.context.window_manager.mesh_check_props
        if not getattr(mc, 'z_fighting', False):
            return

        from mathutils.bvhtree import BVHTree

        pairs = [(obj, mc_obj) for obj, mc_obj in cls.objects.items()
                 if mc_obj._checks.get('z_fighting') is not None]
        if len(pairs) < 2:
            return

        # Performance guard
        total_faces = sum(len(mc_obj.bm_object.faces) for _, mc_obj in pairs)
        if total_faces > cls._INTER_Z_FIGHT_MAX_TOTAL_FACES:
            return

        # Build world-space BVH for each object
        def _world_bvh(mc_obj):
            bm  = mc_obj.bm_object
            mw  = mc_obj._object.matrix_world
            if not bm.faces:
                return None
            bm.faces.ensure_lookup_table()
            bm.verts.ensure_lookup_table()
            verts_ws       = [mw @ v.co for v in bm.verts]
            face_vert_idx  = [[v.index for v in f.verts] for f in bm.faces]
            try:
                return BVHTree.FromPolygons(verts_ws, face_vert_idx, epsilon=0.0001)
            except Exception:
                return None

        bvh_list = [(obj, mc_obj, _world_bvh(mc_obj)) for obj, mc_obj in pairs]
        eps_normal = 0.99

        for i in range(len(bvh_list)):
            obj_a, mc_a, bvh_a = bvh_list[i]
            if bvh_a is None:
                continue
            checker_a = mc_a._checks['z_fighting']
            bm_a  = mc_a.bm_object
            rot_a = obj_a.matrix_world.to_3x3().normalized()

            for j in range(i + 1, len(bvh_list)):
                obj_b, mc_b, bvh_b = bvh_list[j]
                if bvh_b is None:
                    continue
                checker_b = mc_b._checks['z_fighting']
                bm_b  = mc_b.bm_object
                rot_b = obj_b.matrix_world.to_3x3().normalized()

                try:
                    overlapping = bvh_a.overlap(bvh_b)
                except Exception:
                    continue

                # Same three-stage filter used for intra-object detection:
                # 1. Same winding only (signed dot, not abs) — removes
                #    inner/outer shell pairs and faces at a geometry-
                #    intersection interface pointing in opposite directions.
                # 2. Centroid ≤ 1 mm world-space — removes geometry that
                #    physically passes through another object (the face
                #    centroids are far apart even though BVH volumes overlap).
                sl        = bpy.context.scene.unit_settings.scale_length or 1.0
                threshold = 0.0001 / sl     # 0.1 mm — same gate as intra-object

                mw_a = obj_a.matrix_world
                mw_b = obj_b.matrix_world

                inter_a: set = set()
                inter_b: set = set()
                for idx_a, idx_b in overlapping:
                    n_a = (rot_a @ bm_a.faces[idx_a].normal).normalized()
                    n_b = (rot_b @ bm_b.faces[idx_b].normal).normalized()
                    # Stage 1 — same winding
                    if n_a.dot(n_b) <= eps_normal:
                        continue
                    # Stage 2 — centroids within 1 mm (world space)
                    ca = mw_a @ bm_a.faces[idx_a].calc_center_median()
                    cb = mw_b @ bm_b.faces[idx_b].calc_center_median()
                    if (ca - cb).length > threshold:
                        continue
                    inter_a.add(idx_a)
                    inter_b.add(idx_b)

                if inter_a:
                    checker_a.add_inter_results(inter_a, obj_b.name)
                if inter_b:
                    checker_b.add_inter_results(inter_b, obj_a.name)

    @classmethod
    def _run_global_uv_padding(cls) -> None:
        """Cross-object UV padding check — Phase 2.

        Called after all per-object uv_padding set_datas() calls complete.
        Reads prefs, then delegates to core.run_global_uv_padding().
        """
        try:
            mc = bpy.context.window_manager.mesh_check_props
        except Exception:
            return
        if not getattr(mc, 'uv_padding', False):
            return
        from .core import run_global_uv_padding, UVPaddingCheck
        addon_name = __name__.split(".")[0]
        try:
            prefs    = bpy.context.preferences.addons[addon_name].preferences
            tex_size = UVPaddingCheck._PAD_TEX_SIZES.get(
                getattr(prefs, 'uv_padding_texture_size', '3'), 4096)
            shell_px = prefs.uv_padding_shell_px
            tile_px  = prefs.uv_padding_tile_px
        except Exception:
            tex_size, shell_px, tile_px = 4096, 16, 8
        run_global_uv_padding(tex_size=tex_size, shell_px=shell_px, tile_px=tile_px)

    @classmethod
    def update_mc_object_datas(cls, name):
        for mc_obj in cls.objects.values():
            checker = mc_obj._checks.get(name)
            if checker:
                try:
                    checker.set_datas()
                    _apply_obj_ignore(checker, mc_obj._object, name)
                    checker._gpu_dirty = True
                    checker._uv_gpu_dirty = True
                except Exception as e:
                    print(f"[AssetChecker] Update error in {name}: {e}")

        # Inter-object Z-fighting (runs after all intra checks complete)
        if name == "z_fighting":
            try:
                cls._run_inter_object_z_fighting()
            except Exception as e:
                print(f"[AssetChecker] Inter-object Z-fighting error: {e}")

        # Cross-object UV padding (runs after all per-object set_datas complete)
        if name == "uv_padding":
            try:
                cls._run_global_uv_padding()
            except Exception as e:
                print(f"[AssetChecker] Global UV padding error: {e}")

        # Sync the outliner quarantine collection after naming check
        if name == "obj_naming":
            try:
                from .naming import NamingMarker
                mc = bpy.context.window_manager.mesh_check_props
                if getattr(mc, "obj_naming", False):
                    problem_objs = [
                        obj for obj, mc_obj in cls.objects.items()
                        if mc_obj._checks.get("obj_naming")
                        and mc_obj._checks["obj_naming"].count > 0
                    ]
                    NamingMarker.update(problem_objs)
            except Exception as e:
                print(f"[AssetChecker] NamingMarker update error: {e}")

    @staticmethod
    def callback(scene):
        ctx = bpy.context
        if not ctx.object:
            ctx.window_manager.mesh_check_props.check_data = False
            return
        m = ctx.object.mode
        if m != MeshCheck._mode:
            MeshCheck.set_mode(m)
            MeshCheck.reset_mc_objects()
        if m == "OBJECT":
            if MeshCheck._scope == "SELECTED":
                # Track only selected objects; auto-remove when deselected
                if any(o.type == "MESH" and o not in MeshCheck.objects
                       for o in ctx.selected_objects):
                    MeshCheck.add_mesh_check_object()
                for o in list(MeshCheck.objects.keys()):
                    try:
                        if not o.select_get():
                            MeshCheck.remove_mesh_check_object(o)
                    except ReferenceError:
                        MeshCheck.remove_mesh_check_object(o)
            else:
                # SCENE / COLLECTION: keep all tracked objects; only purge deleted ones
                for o in list(MeshCheck.objects.keys()):
                    try:
                        o.select_get()  # raises ReferenceError if the object was deleted
                    except ReferenceError:
                        MeshCheck.remove_mesh_check_object(o)

                # Stale detection: compare expected mesh count vs currently tracked
                try:
                    if MeshCheck._scope == "SCENE":
                        expected = sum(1 for o in ctx.scene.objects if o.type == "MESH")
                    else:
                        col = bpy.data.collections.get(MeshCheck._scope_collection)
                        expected = sum(1 for o in col.all_objects
                                       if o.type == "MESH") if col else 0
                    MeshCheck._scene_stale = (expected != len(MeshCheck.objects))
                except Exception:
                    pass

            # Transform dirty check — re-run origin/rotation/scale checks when
            # the object's location/rotation/scale changes without topology change.
            for o, mc_obj in MeshCheck.objects.items():
                try:
                    new_tk = MeshCheckObject._sample_transform_key(o)
                    if new_tk != mc_obj._transform_key:
                        mc_obj._transform_key = new_tk
                        mc_obj.update_datas(
                            mc_obj.bm_object,
                            uv_changed=False,
                            topo_changed=False,
                            transform_changed=True,
                        )
                except Exception as e:
                    print(f"[AssetChecker] transform dirty check {o.name}: {e}")

        elif m == "EDIT" and MeshCheck.poll():
            deps = ctx.evaluated_depsgraph_get()
            for o, mc_obj in MeshCheck.objects.items():
                bm = mc_obj.bm_object
                for u in deps.updates:
                    if u.id.original != o:
                        continue
                    if not u.is_updated_geometry:
                        break
                    new_mesh_key = (len(bm.verts), len(bm.edges), len(bm.faces))
                    new_uv_key   = MeshCheckObject._sample_uv_key(bm)
                    topo_ch = new_mesh_key != mc_obj._mesh_key
                    uv_ch   = new_uv_key   != mc_obj._uv_key
                    if topo_ch:
                        mc_obj._mesh_key = new_mesh_key
                    if uv_ch:
                        mc_obj._uv_key = new_uv_key
                    if topo_ch or uv_ch:
                        mc_obj.update_datas(
                            bm,
                            uv_changed=uv_ch or topo_ch,
                            topo_changed=topo_ch,
                        )
                    break


# ── Session state persistence ─────────────────────────────────────────────────
# Check-enable flags + scope are serialised to scene["_ac_state"] on every save
# and restored on load_post.  Validation *results* are never persisted — the user
# just presses Run again; the check selection is already pre-filled.

_AC_STATE_KEY = "_ac_state"

# BoolProperty / StringProperty identifiers to include in the snapshot
_AC_CHECK_PROPS: frozenset = frozenset({
    'non_manifold', 'boundary_edges', 'isolated_verts', 'triangles', 'ngons',
    'poles', 'zero_area', 'flipped_normals', 'z_fighting', 'invalid_normals',
    'non_applied_transform', 'scale', 'origin_at_zero', 'modifier_stack',
    'symmetry_x', 'symmetry_y', 'symmetry_z',
    'uv_single_set', 'uv_overlap', 'uv_micro_shell', 'uv_texel_density',
    'uv_stretch', 'uv_padding', 'uv_udim_bounds',
    'obj_naming', 'col_naming',
    'mat_suffix', 'mat_assignment', 'missing_textures',
    'unused_data',
})
_AC_UI_PROPS: frozenset = frozenset({
    'cat_topology_open', 'cat_transforms_open', 'cat_symmetry_open',
    'cat_uv_open', 'cat_naming_open', 'cat_materials_open',
    'cat_cleanup_open',
    'obj_list_open', 'uv_td_scope_active',
    'hierarchy_block_open',
    'obj_required_prefix', 'obj_required_suffix',
    'col_required_prefix', 'col_required_suffix',
})
_AC_ALL_PROPS: frozenset = _AC_CHECK_PROPS | _AC_UI_PROPS


@bpy.app.handlers.persistent
def _ac_save_pre(*args):
    """Serialize check-enable flags + scope to scene["_ac_state"] before save."""
    try:
        scene = getattr(bpy.context, 'scene', None)
        if scene is None:
            return
        wm = getattr(bpy.context, 'window_manager', None)
        if wm is None or not hasattr(wm, 'mesh_check_props'):
            return
        mc = wm.mesh_check_props

        state: dict = {}
        for key in _AC_ALL_PROPS:
            try:
                state[key] = getattr(mc, key)
            except Exception:
                pass

        # Scope is intentionally NOT persisted — it always resets to SELECTED on Run.
        scene[_AC_STATE_KEY] = _json.dumps(state, ensure_ascii=False)
    except Exception as e:
        print(f"[AssetChecker] save_pre error: {e}")


@bpy.app.handlers.persistent
def _ac_load_post(*args):
    """Restore check-enable flags + scope from scene["_ac_state"] after load."""
    try:
        scene = getattr(bpy.context, 'scene', None)
        if scene is None:
            return
        raw = scene.get(_AC_STATE_KEY)
        if not raw:
            return

        state = _json.loads(raw)

        wm = getattr(bpy.context, 'window_manager', None)
        if wm is None or not hasattr(wm, 'mesh_check_props'):
            return
        mc = wm.mesh_check_props

        restored = 0
        for key, val in state.items():
            if key.startswith('__') or key not in _AC_ALL_PROPS:
                continue
            try:
                setattr(mc, key, val)
                restored += 1
            except Exception:
                pass

        MeshCheck._state_restored = True
        print(f"[AssetChecker] Settings restored from '{scene.name}' ({restored} props).")
    except Exception as e:
        print(f"[AssetChecker] load_post error: {e}")


def register_state_handlers() -> None:
    """Register save_pre / load_post handlers (idempotent)."""
    if _ac_save_pre not in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(_ac_save_pre)
    if _ac_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_ac_load_post)


def unregister_state_handlers() -> None:
    """Remove save_pre / load_post handlers (idempotent)."""
    if _ac_save_pre in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(_ac_save_pre)
    if _ac_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_ac_load_post)
