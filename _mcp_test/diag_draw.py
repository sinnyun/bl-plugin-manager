import bpy
import io
import json
import sys

res = {"ok": True}

# 找到 VIEW_3D 的 UI region，打开侧边栏并切到“插件库”类别，确保我的面板会被绘制
target_area = None
for w in bpy.context.window_manager.windows:
    for a in w.screen.areas:
        if a.type == "VIEW_3D":
            for sp in a.spaces:
                if sp.type == "VIEW_3D":
                    sp.show_region_ui = True
            for r in a.regions:
                if r.type == "UI":
                    try:
                        r.active_panel_category = "插件库"
                        res["category_set"] = r.active_panel_category
                    except Exception as e:
                        res["category_err"] = repr(e)
            target_area = a

res["found_view3d"] = target_area is not None

# 捕获重绘期间 stdout/stderr（Blender 打印 Python traceback 走 sys.stderr）
buf = io.StringIO()
old_out, old_err = sys.stdout, sys.stderr
sys.stdout = buf
sys.stderr = buf
try:
    # 强制完整重绘，触发各面板 draw()
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=3)
    res["redraw"] = "done"
except Exception as e:
    res["redraw"] = f"EXC {e!r}"
finally:
    sys.stdout = old_out
    sys.stderr = old_err

text = buf.getvalue()
res["captured_len"] = len(text)
res["has_traceback"] = "Traceback" in text
# 只保留含错误信息的行
lines = [ln for ln in text.splitlines()
         if any(k in ln for k in ("Traceback", "Error", "error", "PM_PT", "ui.py", "Exception"))]
res["error_lines"] = lines[:40]

# 当前活动类别的面板是否包含我的面板
res["active_category"] = res.get("category_set")

print(json.dumps(res, ensure_ascii=False, default=str))
