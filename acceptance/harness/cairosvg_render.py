#!/usr/bin/env python3
"""Render one Prism Plate SVG with cairosvg at 1:1, optionally over a page colour."""
import argparse
import cairosvg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--svg", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--background")
    args = ap.parse_args()
    kw = {}
    if args.background:
        kw["background_color"] = args.background
    cairosvg.svg2png(url=args.svg, write_to=args.out, **kw)


if __name__ == "__main__":
    main()
