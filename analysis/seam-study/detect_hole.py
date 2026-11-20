#!/usr/bin/env python3
"""Detect the inner-contour hole in one rendered seam plate PNG.

A "hole pixel" is a pixel where the outer silhouette cannot explain any
partial alpha (all four 2x2 samples inside the outer contour) yet at least one
sample falls inside the inner contour, so the inner contour is what reduced
alpha. This separates "the hole is visible here" from "alpha is partial because
the outer edge cuts the pixel".

Usage: python3 detect_hole.py samples/offset-0.25.png
The horizontal offset is taken from the file name (offset-<dx>.png or
nohole-<dx>.png).
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import analyze  # noqa: E402


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    path = pathlib.Path(sys.argv[1])
    dx = float(path.stem.split("-")[-1])
    _, rows = analyze.read_png(path)
    found = analyze.hole_pixels(rows, dx)
    print(f"{path}: {len(found)} hole pixels (offset {dx:g})")
    for px in found:
        print(f"  ({px['x']},{px['y']}) alpha={px['a']}")


if __name__ == "__main__":
    main()
