# Asset Checker

Blender аддон-валидатор (N-panel → STUKACH) для проверки ассетов по pipeline-регламенту.
**Blender 5.1+ · Python 3.12 · автор: Maksim Kovalev · версия 1.3.0**

---

## Файлы

| Файл | Роль |
|---|---|
| `core.py` | Классы чекеров + `CHECK_TYPES` + UV-хелперы + `_uv_island_cache` + `_uv_island_membership` |
| `naming.py` | Naming policy engine: `NAMING_RULES`, `NAMING_POLICY`, `get_active_policy`, `NamingValidator`, `NamingMarker`, `NamingAudit`, операторы |
| `manager.py` | `MeshCheckObject` (smart dirty keys), `MeshCheckGPU` (3D draw + batch cache), `UVCheckGPU` (UV editor draw + batch cache), `MeshCheck` |
| `properties.py` | `BoolProperty` на каждый чек, `CHECK_CATEGORIES`, операторы (toggle category, set TD target, check naming, select elements) |
| `preferences.py` | `NamingEntry`, naming policy операторы (8 шт.), `MeshCheckPreferences` (цвета, offsets, naming policy, TD settings, stretch threshold) |
| `ui.py` | N-panel (VIEW\_3D + IMAGE\_EDITOR), статус-бар, `CHECK_THRESHOLDS`, Invalid Naming блок, Naming Audit блок, Coordinator Mode |
| `__init__.py` | Регистрация: NamingEntry → PolicyOps → Prefs → ToggleOp → SetTDOp → CheckNamingOp → Properties → Panels → NamingOps |

---

## Добавить новый чек — 6 шагов

1. `core.py` — класс `: BaseCheck`, реализовать `set_datas`, `get_edges`, `get_points`
   (+ `get_faces` если нужна face-заливка в 3D; + `get_uv_faces`/`get_uv_edges` если нужен UV-оверлей)
   (+ `get_select_data` → `(element_type, indices)` для кнопки Select in Edit Mode)
2. `core.py` — добавить в `CHECK_TYPES`
3. `properties.py` — `BoolProperty(default=False)` в `MeshCheckProperties` + ключ в `CHECK_CATEGORIES`
4. `preferences.py` — `{name}_color` FloatVectorProperty; опциональные настройки чека
5. `ui.py` — порог в `CHECK_THRESHOLDS`; если нужен в UV Editor панели — добавить в `ASSET_CHECKER_PT_UV_Panel._UV_CHECKS`
6. `manager.py` — если face-заливка в 3D: добавить имя в `_FACE_OVERLAY_CHECKS`; если UV-оверлей: добавить в `UVCheckGPU._UV_OVERLAY_CHECKS`; если UV-чек (dirty skip): добавить в `MeshCheckObject._UV_CHECKS`

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
    'uv_material_udim',
})
```

В `MeshCheck.callback` (EDIT mode): `new_mesh_key` и `new_uv_key` вычисляются и сравниваются отдельно, `update_datas` вызывается с точными флагами `uv_changed=uv_ch or topo_ch, topo_changed=topo_ch`.

---

### Batch caching (GPU — критично для производительности)

**3D Viewport (`MeshCheckGPU`):**
- `_batch_cache: dict` — ключ `id(checker)`, инвалидируется при `checker._gpu_dirty = True` или смене `offset`/`pt_offset`
- `_gpu_dirty` выставляет **вызывающий код** после `set_datas()` — сам `set_datas()` его не трогает
- `_FACE_OVERLAY_CHECKS = {'zero_area', 'triangles', 'ngons', 'uv_stretch', 'invalid_normals', 'uv_material_udim'}` — face fill + edge
- `_THICK_LINE_CHECKS = {'non_applied_transform', 'scale'}` — bbox, линия 4px
- **`flipped_normals` исключён** из `_FACE_OVERLAY_CHECKS` — использует Blender built-in Face Orientation overlay

**UV Editor (`UVCheckGPU`):**
- `_batch_cache: dict` — тот же паттерн, ключ `id(checker)`
- Инвалидируется через `checker._uv_gpu_dirty` — флаг независим от `_gpu_dirty`
- `_UV_OVERLAY_CHECKS = {'uv_overlap', 'uv_micro_shell', 'uv_udim_bounds', 'uv_padding', 'uv_stretch', 'uv_material_udim'}`

**UV Editor — требования к координатам:**
- `get_uv_faces()` / `get_uv_edges()` **обязаны** возвращать 3D-координаты `(u, v, 0.0)` — шейдер `UNIFORM_COLOR` всегда ожидает `VEC3`
- Возврат 2D `(u, v)` тюплов → шейдер молча ничего не рисует

**`BaseCheck.__init__`:**
```python
self._gpu_dirty    = True   # 3D viewport batch
self._uv_gpu_dirty = True   # UV editor batch
```

**`gpu.state` при рисовании face fill (3D viewport):**
```python
gpu.state.blend_set("ALPHA")
gpu.state.depth_test_set('NONE')
gpu.state.face_culling_set('NONE')   # обязательно — без него грани с нормалью "от камеры" не видны
cached['face'].draw(shader)
```

### Жизненный цикл

1. Кнопка "RUN STUKACH" → `update_overlay()` → `MeshCheckGPU.setup_handler()` + `UVCheckGPU.setup_handler()` + `MeshCheck.add_callback()`
2. `depsgraph_update_post` → `MeshCheck.callback()` каждое изменение сцены
3. EDIT mode: `_sample_uv_key` + `_mesh_key` определяют что именно изменилось
4. `MeshCheckObject.update_datas(bm, uv_changed=…, topo_changed=…)` → `checker.set_datas()` только для нужных чекеров
5. GPU draw handlers читают `_batch_cache`, рисуют оверлеи

### FlippedNormals — Blender built-in overlay

`flipped_normals` **не рисует собственный GPU-оверлей**. Вместо этого:
- Галка включается → `update_flipped_normals()` в `properties.py` → `space.overlay.show_face_orientation = True` для всех VIEW_3D
- Галка выключается → `show_face_orientation = False`
- Счётчик всё ещё работает через `bmesh.ops.recalc_face_normals` (cache key: verts, edges, faces)
- Визуализация идентична Blender Viewport Overlays → Face Orientation

### MeshCheck hot-reload — стабильные ссылки

`ui.py` импортирует `from . import manager as _manager_mod` на уровне модуля.  
Везде используется `_manager_mod.MeshCheck` (не `MC` alias), что гарантирует актуальную ссылку после горячей перезагрузки.

```python
# ui.py — верхний уровень файла
from . import manager as _manager_mod

def _MC():
    return _manager_mod.MeshCheck
```

Все `draw()` методы панелей используют `_manager_mod.MeshCheck` напрямую — **не** module-level alias.

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
_UV_STRETCH_MAX_POLYS         = 300_000  # angle walk guard (поднят: fuselage 106k polys)
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

Guard: `_UV_OVERLAP_MAX_TRIS = 80_000` — пропускается на очень плотных мешах.

### Island detection

`_detect_uv_islands(me)` — Union-Find с path compression по UV-рёбрам (half-edge canonical keys).
Guard: `_UV_ISLAND_MAX_POLYS = 15_000`.
Кешируется в `_uv_island_cache` по `(me.as_pointer(), n_loops)` — один расчёт на цикл обновления.

`_uv_island_membership(me, max_polys, bm=None)` — отдельная (без кэша) версия того же алгоритма.

### Texel Density (px/cm)

```
TD (px/cm) = tex_size × √uv_area / (√world_area_m² × 100 × scale_length)
```

### UV Stretch

Алгоритм из ZenUV `StretchMap.calc_distortion_fac`:
```python
mesh_angle = loop.calc_angle()
cos_a = dot(uv_vec0, uv_vec1) / (|uv_vec0| × |uv_vec1|)
uv_angle = acos(cos_a)
distorted = abs(mesh_angle - uv_angle) > threshold
```

### UV Material UDIM (`uv_material_udim`)

**Алгоритм:**
1. `_uv_island_membership` строит острова (guard: `_MAX_POLYS = 50_000`)
2. Для каждого острова: голосование по UDIM-тайлу (floor(u_avg), floor(v_avg))
3. Группировка по тайлу → тайлы с > 1 материала → `bad_tiles`
4. Для каждого bad_tile: определяется **доминантный материал** (max face count)
5. Грани миноритарных материалов → `_minority_uv_tris` (UV-оверлей в IMAGE_EDITOR)
6. Все грани bad_tiles → `_bad_face_indices` (3D face fill + select)

**Оверлеи:**
- 3D viewport: `get_faces()` — заливка всех проблемных полигонов (`_FACE_OVERLAY_CHECKS`)
- UV Editor: `get_uv_faces()` — только UV-треугольники миноритарного материала (`_UV_OVERLAY_CHECKS`)
- `get_select_data()` → `('FACE', _bad_face_indices)`

---

## CHECK_CATEGORIES (properties.py)

```python
CHECK_CATEGORIES = {
    "TOPOLOGY":   ("non_manifold", "boundary_edges", "isolated_verts", "duplicate_verts",
                   "face_aspect_ratio", "triangles", "ngons", "poles", "zero_area",
                   "flipped_normals", "z_fighting", "invalid_normals"),
    "TRANSFORMS": ("non_applied_transform", "scale", "origin_at_zero", "modifier_stack"),
    "SYMMETRY":   ("symmetry_x", "symmetry_y", "symmetry_z"),
    "UV":         ("uv_single_set", "uv_overlap", "uv_micro_shell", "uv_texel_density",
                   "uv_stretch", "uv_padding", "uv_udim_bounds", "uv_material_udim"),
    "NAMING":     ("obj_naming", "col_naming", "mat_numbering"),
    "MATERIALS":  ("mat_suffix", "mat_assignment", "missing_textures"),
    "CLEANUP":    ("unused_data",),
}
```

---

## Статус чекеров

### Работают корректно

| Чек | Механизм |
|---|---|
| `non_manifold` | T-junction edges (link_faces > 2) — BLOCKER; wire edges (link_faces == 0) — visualise only |
| `triangles`, `ngons` | face edge count, fan-triangulation для GPU-заливки |
| `poles` | link\_edges count (3=N, 5=E, >5=more, 0=isolated) |
| `zero_area` | `f.calc_area() < 1e-10` |
| `flipped_normals` | `bmesh.ops.recalc_face_normals` (счётчик) + Blender Face Orientation overlay (визуализация); toggle через `update_flipped_normals` → `space.overlay.show_face_orientation` |
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
| `uv_overlap` | island-filter + 2D grid hash + AABB pre-filter + `_uv_tris_truly_overlap`; ~54ms @ 4k polys |
| `uv_micro_shell` | island-based: `_uv_island_membership` → сумма UV-area per island < `_MICRO_SHELL_ISLAND_AREA=1e-5` |
| `uv_texel_density` | TD в px/cm с target + tolerance; `metric_text` |
| `uv_stretch` | ZenUV angle-diff алгоритм; threshold из preferences; guard 300k polys; face fill в 3D + UV overlay |
| `uv_padding` | KD-tree shell-to-shell (default 16px) + аналитический tile-border (default 8px) |
| `uv_udim_bounds` | UV-острова выходят за пределы UDIM-тайлов; guard 500k polys |
| `uv_material_udim` | Смешанные материалы на одном UDIM-тайле; доминантный vs миноритарный мат; face fill в 3D + UV overlay миноритарного мата |

---

## UV Editor панель (IMAGE_EDITOR) — `ASSET_CHECKER_PT_UV_Panel`

```python
_UV_CHECKS = (
    "uv_single_set", "uv_overlap", "uv_micro_shell",
    "uv_texel_density", "uv_stretch", "uv_padding", "uv_udim_bounds",
    "uv_material_udim",
)
```

Блок **Material → UDIM** ниже списка чекеров: агрегирует `_mat_udim_map` всех tracked объектов, кликабельные строки материал → список UDIM-тайлов. Клик → `mat_udim_selected` → `UVCheckGPU` рисует оранжевый прямоугольник вокруг тайлов материала.

---

## Select in Edit Mode (properties.py)

`ASSET_CHECKER_OT_select_check_elements` (`asset_checker.select_check_elements`):
- Параметры: `obj_name: StringProperty`, `check_name: StringProperty`
- Переходит в Edit Mode, снимает все выделения, выделяет элементы из `checker.get_select_data()`
- `get_select_data()` возвращает `(element_type, indices)` где `element_type` ∈ `{'VERT', 'EDGE', 'FACE'}`
- Центрирует viewport на выделении (`view3d.view_selected`)
- Кнопка в UI: иконка `EDITMODE_HLT`, левее кнопки Ignore, с `row.separator(factor=0.5)` между ними
- Отображается только если `count > 0` и `element_type is not None`

---

## Coordinator Mode (ui.py)

`draw_coordinator_panel(layout, mc, context)` — отдельная функция, вызывается из `draw()` когда `mc.coordinator_mode = True`.

- Фильтрует чеки по severity: показывает только `{'BLOCKER', 'WARNING'}`, INFO скрыт
- `mc.draw_options(checks_box, severity_filter={'BLOCKER', 'WARNING'})` — параметр `severity_filter` в `draw_options()`
- Категории где все чеки — INFO (например Symmetry) скрываются полностью
- Asset Status badge вверху: CRITICAL / REVIEW / READY / "Run validation first"
- Export/Pre-flight/Checkpoint блок внизу (идентичен основному виду)

---

## Export / Pre-flight / Checkpoint (ui.py)

Блок всегда виден внизу панели (после Asset Status и объектного списка):
- **Export** (JSON/CSV/HTML) — `asset_checker.export_report`, задизейблен если `not MC.objects`
- **Pre-flight** (FBX/USD) — `asset_checker.preflight_export`, задизейблен если `not MC.objects`
- **Checkpoint** (Save/Update/Load/Clear) — всегда активен, хранится в `scene[_AC_CHECKPOINT_KEY]`

Дублируется в `draw_coordinator_panel`.

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

# UV Padding
uv_padding_shell_px: IntProperty(default=16, min=1, max=256)
uv_padding_tile_px:  IntProperty(default=8,  min=1, max=128)

# UV Stretch
uv_stretch_threshold: FloatProperty(default=0.5, min=0.05, max=3.14)  # radians

# Texel Density
uv_td_texture_size:  EnumProperty(items=('0'=512,'1'=1024,'2'=2048,'3'=4096), default='2')
uv_td_target:        FloatProperty(default=0.0)
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
| `ASSET_CHECKER_OT_select_check_elements` | `asset_checker.select_check_elements` | Выделить проблемные элементы в Edit Mode |
| `ASSET_CHECKER_OT_set_td_target` | `asset_checker.set_td_target` | Пресет Target TD из UV-панели |
| `ASSET_CHECKER_OT_check_naming` | `asset_checker.check_naming` | Запустить naming-валидацию вручную |
| `ASSET_CHECKER_OT_export_report` | `asset_checker.export_report` | Экспорт отчёта JSON/CSV/HTML |
| `ASSET_CHECKER_OT_preflight_export` | `asset_checker.preflight_export` | Pre-flight + экспорт FBX/USD |
| `ASSET_CHECKER_OT_save_checkpoint` | `asset_checker.save_checkpoint` | Сохранить checkpoint в scene data |
| `ASSET_CHECKER_OT_load_checkpoint` | `asset_checker.load_checkpoint` | Загрузить checkpoint |

---

## UI — неизменяемые правила

- Вкладка: **STUKACH** (VIEW\_3D + IMAGE\_EDITOR)
- Категории: **TOPOLOGY / TRANSFORMS / SYMMETRY / UV / NAMING / MATERIALS / CLEANUP**
- Toggle-кнопка в заголовке категории; цветовой свотч у каждого чека
- Статус-иконки: grey / green / yellow / red
- Все категории свёрнуты по умолчанию (`default=False`)
- Export/Pre-flight/Checkpoint — в самом низу панели, после Asset Status
- Offset fields — компактная строка внизу Pipeline Checks box

---

## Dev-workflow: полный перезагрузчик аддона в Blender

`addon_disable` / `addon_enable` **не** удаляют модули из `sys.modules` — старые классы остаются.

```python
import bpy, sys
bpy.ops.preferences.addon_disable(module="STUKACH")
for k in [k for k in sys.modules if k == 'STUKACH' or k.startswith('STUKACH.')]:
    del sys.modules[k]
bpy.ops.preferences.addon_enable(module="STUKACH")
```

**Junction (Desktop → AppData):** файлы аддона хранятся в `Desktop/STUKACH`, AppData линкован через Directory Junction:
```
mklink /J "C:\Users\...\Blender\5.1\scripts\addons\STUKACH" "C:\Users\...\Desktop\STUKACH"
```
Если junction сломан — пересоздать командой выше.

---

## Pipeline-правила

**Топология:** квады; ngons запрещены на hard-surface; tris — вне deform/subdiv зон; нет self-intersections

**Трансформы:** Scale=1, Rotation=0; pivot в нуле

**Нейминг:** lowercase English; configurable через preferences + inline panel fields

**UV:** UDIM, один UV-сет; padding ≥ 16px (4K); нет overlap, micro-shells, stretch, visible seams; 1 материал на UDIM-тайл

**Материалы:** слот с материалом; имя → `_mat`; нет `Material.001`
