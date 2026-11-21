#!/usr/bin/env python3
"""One command to regenerate every deliverable and re-check every claim."""
from __future__ import annotations

import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent


def step(name, script):
    print(f"[{name}]")
    subprocess.run([sys.executable, str(HERE / script)], check=True)


def main():
    step("render", "build.py")
    step("measure", "analyze.py")
    step("criteria", "policy.py")
    step("tables", "report_tables.py")
    step("sheets", "annotate.py")
    step("verify", "verify.py")
    print("done")


if __name__ == "__main__":
    main()
