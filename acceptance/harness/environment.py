#!/usr/bin/env python3
"""Record the exact viewing environment, and list what was NOT tested.

A recommendation is only as good as the environment it was measured in.  This
writes acceptance/observations/environment.json with:

  measured   tools and versions that actually ran here;
  unavailable tools that are missing or cannot launch, with the reason;
  untested   environments a recipient may plausibly use that this run could not
             measure at all.  These are listed explicitly so that nobody reads
             the passing result as covering them.

    python3 acceptance/harness/environment.py
"""
import json
import pathlib
import os
import platform
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
OBS = ROOT / "acceptance" / "observations"
RENDER = OBS / "render" / "viewers.json"


def run(cmd):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
        return (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr) else ""
    except Exception as e:  # noqa: BLE001 - recorded, not raised
        return f"error: {e}"


def main():
    env = {
        "host": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "node": run(["node", "--version"]),
            "go": run([os.environ.get("GO", "go"), "version"]),
            "imagemagick": run(["convert", "-version"]),
        },
        "measured": {},
        "unavailable": {},
        "untested": {},
    }
    if RENDER.exists():
        doc = json.loads(RENDER.read_text())
        for v in doc["viewers"]:
            label = f"{v['name']} {v.get('version', '')}".strip()
            if v["ok"]:
                entry = {"version": v.get("version"), "modes": v.get("embed_modes", ["own", "post"]),
                         "alpha_channel": bool(v.get("alpha"))}
                if v.get("alpha_error"):
                    entry["alpha_note"] = v["alpha_error"]
                env["measured"][v["name"]] = entry
            else:
                reason = (v.get("error") or "").strip()
                env["unavailable"][v["name"]] = {
                    "version": v.get("version"),
                    "missing_libraries": v.get("missing_libraries", []),
                    "reason": (reason.splitlines()[0][:200] if reason else "unknown"),
                }
    else:
        env["unavailable"]["all"] = {"reason": "viewers.json missing; run viewers.mjs first"}

    # Explicitly untested.  Every item below is a real way a recipient might
    # look at the file and none of it was measured in this run.
    env["untested"] = {
        "browsers": [
            "Safari / WebKit（本机 WebKit 缺少 libGLESv2、libx264 等运行库，启动检查直接失败；结果未知）",
            "Microsoft Edge（未安装）",
            "Chrome / Firefox 的移动端（未安装）",
            "Chrome / Firefox 的 GUI 窗口（本机只有无头截图；缩放、显示器色彩配置未覆盖）",
        ],
        "platforms": [
            "macOS 与 Windows 的浏览器与系统级颜色管理（本机为 Linux arm64）",
            "印刷 / PDF 输出路径（未涉及）",
        ],
        "viewers": [
            "Adobe Illustrator / Inkscape / Affinity 等桌面矢量应用（未安装）",
            "Okular / Evince 等文档查看器内嵌 SVG（未安装）",
        ],
        "conditions": [
            "非 1:1 缩放（本实验固定 deviceScaleFactor=1）",
            "HDR / 广色域显示与 ICC 配置（本机强制 sRGB）",
            "打印为纸面（颜色空间转换完全未测）",
        ],
    }
    (OBS / "environment.json").write_text(json.dumps(env, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"measured": sorted(env["measured"]),
                      "unavailable": sorted(env["unavailable"]),
                      "untested_groups": len(env["untested"])}, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
