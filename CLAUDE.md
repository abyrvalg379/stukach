# Asset Checker

Blender аддон-валидатор (N-panel → Asset Checker) для проверки ассетов по pipeline-регламенту.
**Blender 4.5+ · Python 3.12 · автор: Maksim Kovalev · версия 1.2.3**

---

## Файлы

| Файл | Роль |
|---|---|
| `core.py` | Классы чекеров + `CHECK_TYPES` + UV-хелперы + `_uv_island_cache` + `_uv_island_membership` |
| `naming.py` | Naming policy engine: `NAMING_RULES`, `NAMING_POLICY`, `get_active_policy`, `NamingValidator`, `NamingMarker`, `NamingAudit`, операторы |
| `manager.py` | `MeshCheckObject` (smart dirty keys), `MeshCheckGPU` (3D draw + batch cache), `UVCheckGPU` (UV editor draw + batch cache), `MeshCheck` |
| `properties.py` | `BoolProperty` на каждый чек, `CHECK_CATEGORIES`, операторы (toggle category, set TD target, check naming) |
| `preferences.py` | `NamingEntry`, naming policy операторы (8 шт.), `MeshCheckPreferences` (цвета, offsets, naming policy, TD settings, stretch threshold) |
| `ui.py` | N-panel (VIEW\_3D + IMAGE\_EDITOR), статус-бар, `CHECK_THRESHOLDS`, Invalid Naming блок, Naming Audit блок |
| `__init__.py` | Регистрация: NamingEntry → PolicyOps → Prefs → ToggleOp → SetTDOp → CheckNamingOp → Properties → Panels → NamingOps |

---

## Добавить новый чек — 6 шагов

1. `core.py` — класс `: BaseCheck`, реализовать `set_datas`, `get_edges`, `get_points`
   (+ `get_faces` если нужна face-заливка; + `get_uv_faces`/`get_uv_edges` если нужен UV-оверлей)
2. `core.py` — добавить в `CHECK_TYPES`
3. `properties.py` — `BoolProperty(default=False)` в `MeshCheckProperties` + ключ в `CHECK_CATEGORIES`
4. `preferences.py` — `{name}_color` FloatVectorProperty; опциональные настройки чека
5. `ui.py` — порог в `CHECK_THRESHOLDS`
6. `manager.py` — если face-заливка: добавить имя в `_FACE_OVERLAY_CHECKS`; если UV-чек: добавить в `MeshCheckObject._UV_CHECKS`

---

## Архитектура

### Smart dirty detection (MeshCheckObject)

`MeshCheckObject` хранит два независимых dirty ключа:

```python
_mesh_key: tuple = ()   # (n_verts, n_edges, n_faces) — topology dirty
_uv_key:   tuple = ()   # (n_faces, layer_name, xsum)  — UV coords dirty
```

`_sample_uv_key(bm)` — дешёвый детектор изменений UV: XOR-хеш координат из ~16 граней (step = max(1, n//16)), без полного чтения UV-массива. False-negative rate пренебрежимо мал для реальных операций pack/unwrap.

`update_datas(bm, *, uv_changed=True, topo_changed=True)` — умный skip:
- UV-чеки (`_UV_CHECKS`) пропускаются если `not uv_changed`
- Topology-чеки пропускаются если `not topo_changed`

```python
_UV_CHECKS = frozenset({
    'uv_overlap', 'uv_padding', 'uv_micro_shell', 'uv_texel_density',
    'uv_stretch', 'uv_single_set', 'uv_udim_ready', 'uv_udim_bounds',
})
```

В `MeshCheck.callback` (EDIT mode): `new_mesh_key` и `new_uv_key` вычисляются и сравниваются отдельно, `update_datas` вызывается с точными флагами `uv_changed=uv_ch or topo_ch, topo_changed=topo_ch`.

---

### Batch caching (GPU — критично для производительности)

**3D Viewport (`MeshCheckGPU`):**
- `_batch_cache: dict` — ключ `id(checker)`, инвалидируется при `checker._gpu_dirty = True` или смене `offset`/`pt_offset`
- `_gpu_dirty` выставляет **вызывающий код** после `set_datas()` — сам `set_datas()` его не трогает
- `_FACE_OVERLAY_CHECKS = {'flipped_normals', 'zero_area', 'triangles', 'ngons', 'uv_stretch'}` — face fill + edge
- `_THICK_LINE_CHECKS = {'non_applied_transform', 'scale'}` — bbox, линия 4px

**UV Editor (`UVCheckGPU`):**
- `_batch_cache: dict` — тот же паттерн, ключ `id(checker)`
- Инвалидируется через `checker._uv_gpu_dirty` — флаг независим от `_gpu_dirty`
- `_UV_OVERLAY_CHECKS = {'uv_overlap', 'uv_micro_shell', 'uv_udim_bounds'}`

**`BaseCheck.__init__`:**
```python
self._gpu_dirty    = True   # 3D viewport batch
self._uv_gpu_dirty = True   # UV editor batch
```

### Жизненный цикл

1. Кнопка "Run Validation" → `update_overlay()` → `MeshCheckGPU.setup_handler()` + `UVCheckGPU.setup_handler()` + `MeshCheck.add_callback()`
2. `depsgraph_update_post` → `MeshCheck.callback()` каждое изменение сцены
3. EDIT mode: `_sample_uv_key` + `_mesh_key` определяют что именно изменилось
4. `MeshCheckObject.update_datas(bm, uv_changed=…, topo_changed=…)` → `checker.set_datas()` только для нужных чекеров
5. GPU draw handlers читают `_batch_cache`, рисуют оверлеи

### FlippedNormals — topology cache key

Хранит `_cache_key: tuple = (verts, edges, faces)`. Если топология не изменилась — `bm.copy()` + `recalc_face_normals` пропускаются. Критично на 100k–500k+ меш.

### Depsgraph callback — O(n) по tracked objects

OBJECT mode: `try: o.select_get() except ReferenceError` — удалённые объекты бросают `ReferenceError`.

### World-space координаты — обязательный паттерн

```python
p = wm @ v.co
coords.append((p.x + v.normal.x * _offset, p.y + v.normal.y * _offset, p.z + v.normal.z * _offset))
```
Никогда `(wm @ v.co)[0]` трижды. Bbox — явные `(x, y, z)` туплы, не Vector.

### Performance — обязательные правила

- **Никогда** не создавать `mathutils.Vector` в per-vertex / per-triangle циклах
- Использовать `foreach_get` + flat float lists + raw Python float arithmetic
- Все `set_datas()` защищены: `if not me.uv_layers.active: return`
- `_uv_island_cache` — module-level dict, используется `UVUDIMBounds`; очищается в `update_datas()` и `reset_mesh_check()`
- `UVPaddingCheck` использует отдельную `_uv_island_membership()` без shared кеша

---

## UV-алгоритмы (core.py)

### Константы (guards и пороги)

```python
_MICRO_SHELL_UV_AREA          = 1e-12    # per-triangle fallback — truly degenerate UV only
_MICRO_SHELL_ISLAND_AREA      = 1e-5     # per-island threshold ≈ 6px×6px @ 2048
_UV_OVERLAP_MAX_TRIS          = 80_000   # overlap guard — skip on very dense meshes
_UV_ISLAND_MAX_POLYS          = 15_000   # shared cache island detection (_detect_uv_islands)
_UV_MICRO_SHELL_MAX_POLYS     = 100_000  # micro-shell island detection guard (higher limit)
_UV_STRETCH_MAX_POLYS         = 30_000   # angle walk guard
_UV_PADDING_MAX_POLYS         = 50_000   # independent island detection (_uv_island_membership)
_UV_STRETCH_DEFAULT_THRESHOLD = 0.5      # radians ≈ 28°
_uv_island_cache: dict        = {}       # module-level shared cache
```

### Overlap detection

**Pipeline (4 стадии):**

1. **Island membership** — `_uv_island_membership(me, _UV_PADDING_MAX_POLYS)` строит `tri_island[]`.
   Треугольники одного острова не могут перекрываться → исключаются на стадии 2.
   Устраняет >99% кандидатов на packed UV.

2. **2D grid broad-phase** — `_uv_grid_candidates(tri_uvs, tri_poly_buf, grid_size=128, tri_island=...)`.
   Строит spatial hash 128×128; эмитирует только inter-island, inter-polygon пары.
   **BVHTree убран** — он делает 3D volume intersection, всегда возвращает 0 для coplanar (z=0) треугольников.

3. **AABB pre-filter** — отбрасывает ~85% оставшихся grid-кандидатов без дорогих тестов.
   Вычисляется вместе с `tri_uvs` в одном цикле (`tri_aabb` list).

4. **Exact test** — `_uv_tris_truly_overlap(t1, t2)` — три случая:
   - **Vertex-in-triangle** (строгий, не включает границу)
   - **Centroid check** — стек идентичных дубликатов
   - **Edge-edge intersection** — `_seg_intersect_2d(p1,p2,p3,p4)` через cross product; строгое пересечение (eps < t, u < 1-eps), НЕ включает общие вершины

**Производительность** (замеры на реальных asset-ах):

| Mesh | Полигоны | Кандидатов grid | После island-filter | Exact-тестов | Время |
|------|----------|-----------------|---------------------|--------------|-------|
| bg   | 61       | 749             | <100                | 0            | ~8 ms |
| f52  | 1 291    | 134 764         | 0                   | 0            | ~20 ms |
| e2   | 4 482    | 159 971         | 57                  | 57           | ~54 ms |
| f2   | 10 000   | 658 813         | 0                   | 0            | ~140 ms |

Guard: `_UV_OVERLAP_MAX_TRIS = 80_000` — пропускается на очень плотных мешах.

**Примечание**: intra-island self-intersections (вручную сложенный остров) не обнаруживаются — покрываются только inter-island overlaps (типичная pipeline-проблема).

### Island detection

`_detect_uv_islands(me)` — Union-Find с path compression по UV-рёбрам (half-edge canonical keys).
Guard: `_UV_ISLAND_MAX_POLYS = 15_000`.
Кешируется в `_uv_island_cache` по `(me.as_pointer(), n_loops)` — один расчёт на цикл обновления.

`_uv_island_membership(me, max_polys, bm=None)` — отдельная (без кэша) версия того же алгоритма.
Возвращает `(poly_to_island, flat_uvs, poly_start, poly_total)` или `None`.
- `UVPaddingCheck`: guard `_UV_PADDING_MAX_POLYS = 50_000`
- `UVMicroShellCheck`: guard `_UV_MICRO_SHELL_MAX_POLYS = 100_000` — выше, чтобы покрывать плотные ассеты

Fallback когда mesh > guard: `_MICRO_SHELL_UV_AREA = 1e-12` (только вырожденные UV, без ложных срабатываний).

### Texel Density (px/cm)

Формула (источник: Texel Density Checker addon + ZenUV — идентичны):
```
TD (px/cm) = tex_size × √uv_area / (√world_area_m² × 100 × scale_length)
```

Реализация в `UVTexelDensity`:
- `_TD_TEX_SIZES = {'0': 512, '1': 1024, '2': 2048, '3': 4096}` — class attribute
- `tex_size` из `prefs.uv_td_texture_size` (default `'2'` = 2048)
- `scale_length` из `bpy.context.scene.unit_settings.scale_length`
- `_count = 1` если `target_td > 0` и отклонение > `uv_td_tolerance`% (default 20%)
- `metric_text` → `"TD: 10.24 px/cm"` или `"TD: 10.24 / 20.48 px/cm"` (с target)
- world_area вычисляется через raw cross product без Vector объектов

### UV Stretch

Алгоритм из ZenUV `StretchMap.calc_distortion_fac`:
```python
mesh_angle = loop.calc_angle()       # 3D угол при вершине
cos_a = dot(uv_vec0, uv_vec1) / (|uv_vec0| × |uv_vec1|)
uv_angle = acos(cos_a)
distorted = abs(mesh_angle - uv_angle) > threshold
```

Реализация в `UVStretch`:
- Threshold из `prefs.uv_stretch_threshold` (default 0.5 rad ≈ 28°)
- `_count` = число stretched граней
- Face overlay (заливка) — добавлен в `_FACE_OVERLAY_CHECKS`
- Guard: `_UV_STRETCH_MAX_POLYS = 30_000`
- Fan-triangulation для face overlay (world-space coords)

---

## CHECK_CATEGORIES (properties.py)

```python
CHECK_CATEGORIES = {
    "TOPOLOGY":   ("non_manifold", "triangles", "ngons", "poles", "zero_area", "flipped_normals", "intersections"),
    "TRANSFORMS": ("non_applied_transform", "scale"),
    "SYMMETRY":   ("symmetry_x", "symmetry_y", "symmetry_z"),
    "UV":         ("uv_single_set", "uv_udim_ready", "uv_overlap", "uv_micro_shell",
                   "uv_texel_density", "uv_stretch", "uv_padding", "uv_udim_bounds"),
    "NAMING":     ("obj_naming", "col_naming"),
    "MATERIALS":  ("mat_suffix", "mat_assignment"),
}
```

---

## Статус чекеров

### Работают корректно

| Чек | Механизм |
|---|---|
| `non_manifold` | `e.is_manifold` |
| `triangles`, `ngons` | face edge count, triangulate для ngon-заливки |
| `poles` | link\_edges count (3=N, 5=E, >5=more, 0=isolated) |
| `zero_area` | `f.calc_area() < 1e-10` |
| `flipped_normals` | bmesh copy + `recalc_face_normals` + dot; cache key (verts, edges, faces) |
| `intersections` | `BVHTree.overlap()` + normal dot filter |
| `non_applied_transform` | rotation\_euler != 0 |
| `scale` | `any(abs(s - 1.0) > 0.001)` |
| `symmetry_x/y/z` | KD-Tree mirror lookup |
| `obj_naming` | `NamingValidator.validate_object(obj, policy)` + severity system |
| `col_naming` | `NamingValidator.validate_collection(col, policy)` — configurable |
| `mat_suffix` | `endswith("_mat")` |
| `mat_assignment` | пустые слоты |
| `uv_single_set` | count UV layers == 1 |
| `uv_udim_ready` | UV в диапазоне [0..10] × [0..10] |
| `uv_overlap` | island-filter + 2D grid hash (`_uv_grid_candidates`) + AABB pre-filter + `_uv_tris_truly_overlap`; ~54ms @ 4k polys |
| `uv_micro_shell` | island-based: `_uv_island_membership` → сумма UV-area per island < `_MICRO_SHELL_ISLAND_AREA=1e-5`; fallback per-tri < 1e-7 |
| `uv_texel_density` | TD в px/cm с target + tolerance; `metric_text` |
| `uv_stretch` | ZenUV angle-diff алгоритм; threshold из preferences |
| `uv_padding` | KD-tree shell-to-shell (default 16px) + аналитический tile-border (default 8px); `metric_text` разбивает по типу |
| `uv_udim_bounds` | UV-острова выходят за пределы UDIM-тайлов |

### Не реализованы (волна 2)

`origin_at_zero`, `scene_units`, face aspect ratio

---

## Пороги (CHECK_THRESHOLDS в ui.py)

- **0** (любой count > 0 = red): все, кроме ↓
- **50** yellow: `triangles`
- **10** yellow: `ngons`
- **20** yellow: `poles`

---

## Preferences — структура MeshCheckPreferences

```python
# Offsets / sizes
edges_width, faces_offset, edges_alpha, faces_alpha, point_size, points_offset

# UV Padding (новое)
uv_padding_shell_px: IntProperty(default=16, min=1, max=256)   # между шеллами
uv_padding_tile_px:  IntProperty(default=8,  min=1, max=128)   # до границы тайла
# Пороги в UV-пространстве = px / tex_size (масштабируются от uv_td_texture_size)

# UV Stretch
uv_stretch_threshold: FloatProperty(default=0.5, min=0.05, max=3.14)  # radians

# Texel Density
uv_td_texture_size:  EnumProperty(items=('0'=512,'1'=1024,'2'=2048,'3'=4096), default='2')
uv_td_target:        FloatProperty(default=0.0)   # 0 = informational only
uv_td_tolerance:     FloatProperty(default=20.0)  # percent

# Цвета — один FloatVectorProperty(size=3, subtype="COLOR") на каждый чек
# Naming Policy — CollectionProperty(type=NamingEntry) × 4
naming_prefixes, naming_suffixes, col_naming_prefixes, col_naming_suffixes
```

Регистрационный порядок: `NamingEntry` **до** `MeshCheckPreferences`.

---

## Операторы (properties.py)

| Класс | bl_idname | Назначение |
|---|---|---|
| `MESH_CHECK_OT_toggle_category` | `mesh_check.toggle_category` | Вкл/выкл все чеки категории |
| `ASSET_CHECKER_OT_set_td_target` | `asset_checker.set_td_target` | Пресет Target TD из UV-панели |
| `ASSET_CHECKER_OT_check_naming` | `asset_checker.check_naming` | Запустить naming-валидацию вручную |

`ASSET_CHECKER_OT_set_td_target` принимает `td_value: FloatProperty`, записывает в `prefs.uv_td_target`, вызывает `MeshCheck.update_mc_object_datas("uv_texel_density")`.

---

## UV Panel (IMAGE_EDITOR) — TD quick settings

Отображается когда `uv_texel_density` активен:
- Dropdown: Texture Size (из prefs)
- Field: Target TD + единица "px/cm"
- Field: Tolerance %
- Preset buttons: **20.48 / 10.24 / 5.12 / 2.56** px/cm → вызывают `asset_checker.set_td_target`

---

## Naming policy engine (`naming.py`)

### Severity matrix

| Нарушение | Severity | Rule |
|---|---|---|
| DCC default name (Cube, pCube1…) | ERROR | `forbidden_base_name` |
| Forbidden chars / spaces | ERROR | `forbidden_chars` |
| Blender `.001` auto-numbering | ERROR | `blender_numbering` |
| Пустое имя | ERROR | `empty_name` |
| Uppercase | WARNING | `lowercase` |
| Prefix не совпал (если policy задан) | WARNING | `missing_prefix` |
| Suffix не совпал (если policy задан) | WARNING | `missing_suffix` |
| Нет суффикса (policy не задан) | INFO | `no_suffix` |

`count` = WARNING + ERROR. INFO — только в панели.

### Severity icons

```python
SEVERITY_ICON = {
    ERROR:   "SEQUENCE_COLOR_01",   # красный
    WARNING: "SEQUENCE_COLOR_02",   # оранжевый
    INFO:    "SEQUENCE_COLOR_03",   # жёлтый
}
```

### NamingMarker / NamingAudit

- `NamingMarker` — коллекция `_AC_Issues` в outliner, объекты линкуются, не перемещаются
- `NamingAudit` — сканирует весь `bpy.data` по запросу; операторы `run_naming_audit`, `clear_naming_audit`

### Inline policy fields (MeshCheckProperties)

```python
obj_required_prefix, obj_required_suffix   # объединяются с prefs в set_datas()
col_required_prefix, col_required_suffix
```

---

## UI — неизменяемые правила

- Вкладка: **Asset Checker** (VIEW\_3D + IMAGE\_EDITOR)
- Категории: **TOPOLOGY / TRANSFORMS / SYMMETRY / UV / NAMING / MATERIALS**
- Toggle-кнопка в заголовке категории; цветовой свотч у каждого чека
- Статус-иконки: grey / green / yellow / red
- "Invalid Naming" — внизу панели при `mc.obj_naming or mc.col_naming`; `seen_cols` set для дедупликации коллекций
- "Naming Audit" — всегда в конце; компактный до Run, развёрнутый после
- Все `default=False`; `faces_offset` = `points_offset` = 0.03

---

## Dev-workflow: полный перезагрузчик аддона в Blender

`addon_disable` / `addon_enable` **не** удаляют модули из `sys.modules` — старые классы остаются.
После обновления файлов нужно вручную чистить кеш:

```python
import bpy, sys
bpy.ops.preferences.addon_disable(module="asset_checker")
for k in [k for k in sys.modules if k == 'asset_checker' or k.startswith('asset_checker.')]:
    del sys.modules[k]
bpy.ops.preferences.addon_enable(module="asset_checker")
```

Также: удалять `__pycache__/core.cpython-*.pyc` при изменении `core.py`.

---

## Pipeline-правила

**Топология:** квады; ngons запрещены на hard-surface; tris — вне deform/subdiv зон; нет self-intersections

**Трансформы:** Scale=1, Rotation=0; pivot в нуле

**Нейминг:** lowercase English; configurable через preferences + inline panel fields

**UV:** UDIM, один UV-сет; padding ≥ 16px (4K); нет overlap, micro-shells, stretch, visible seams

**Материалы:** слот с материалом; имя → `_mat`; нет `Material.001`
