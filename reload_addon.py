# Run this in Blender's Python console or via MCP to hot-reload STUKACH
import bpy, sys

bpy.ops.preferences.addon_disable(module="STUKACH")

to_del = [k for k in sys.modules if k == 'STUKACH' or k.startswith('STUKACH.')]
for k in to_del:
    del sys.modules[k]

bpy.ops.preferences.addon_enable(module="STUKACH")
print("[STUKACH] Reloaded OK")
