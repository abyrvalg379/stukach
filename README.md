# STUKACH

Blender аддон-валидатор ассетов по pipeline-регламенту.

**Blender 5.1+ · Python 3.12 · автор: Maksim Kovalev**

---

## Установка

1. Скопировать папку `STUKACH` в `scripts/addons/`
2. Preferences → Add-ons → найти **STUKACH** → включить
3. N-Panel → вкладка **STUKACH**

## Категории чеков

| Категория | Чеки |
|---|---|
| TOPOLOGY | Non Manifold, Boundary Edges, Isolated Verts, Duplicate Verts, Face Aspect Ratio, Triangles, Ngons, Poles, Zero Area, Flipped Normals, Z-Fighting, Invalid Normals |
| TRANSFORMS | Non Applied Transform, Scale, Origin at Zero, Modifier Stack |
| SYMMETRY | Symmetry X / Y / Z |
| UV | Single Set, Overlap, Micro Shell, Texel Density, Stretch, Padding, UDIM Bounds, Uv Material Udim |
| NAMING | Obj Naming, Col Naming, Mat Numbering |
| MATERIALS | Mat Suffix, Mat Assignment, Missing Textures |
| CLEANUP | Unused Data |
