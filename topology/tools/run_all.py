#!/usr/bin/env python3
"""Regenerate every deliverable in order, then verify the outputs.

Usage:
    GO=/path/to/go python3 -I topology/tools/run_all.py

Fails fast if the Go toolchain is missing; the analysis-only parts can be run
individually (see topology/README.md).
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent


def run(*args, env=None):
    print("$", " ".join(str(a) for a in args), flush=True)
    subprocess.run([sys.executable, "-I", *map(str, args)], cwd=ROOT, check=True,
                   env=env)


def main() -> int:
    run(HERE / "build_cases.py")
    run(HERE / "render_cases.py")
    run(HERE / "verify.py")
    run(HERE / "selfcheck.py")
    run(HERE / "pixel_probe.py")
    run(HERE / "annotate.py")
    png = ROOT / "annotations" / "png"
    if png.exists():
        shutil.rmtree(png)
    png.mkdir()
    if shutil.which("convert"):
        for svg in sorted((ROOT / "annotations").glob("case-*.svg")):
            subprocess.run(["convert", "-background", "white", "-density", "150",
                            str(svg), str(png / (svg.stem + ".png"))],
                           check=False, capture_output=True)
    run(HERE / "report.py")
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
