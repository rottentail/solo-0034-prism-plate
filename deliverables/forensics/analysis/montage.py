"""Build per-family montage grids from the real rendered PNGs.

Rows are candidates, columns are observation backgrounds, in the exact order of
recipes.FAMILIES / recipes.BACKGROUNDS. The mid-gray column (the original
single-background observation) gets a highlighted frame so the eye starts where
the ambiguity lives. A companion JSON maps every tile to its source PNG.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import pngio
import pngwrite
import recipes

TILE = 96
GAP = 6
MARGIN = 10
FRAME = 3
HIGHLIGHT = (217, 119, 6)    # amber frame on the original-observation column
GRID = (235, 235, 238)
TEXTFREE = True


def load_rgba(path):
    w, h, px = pngio.read_png(path)
    rows = []
    for y in range(h):
        row = bytearray()
        for x in range(w):
            row.extend(px(x, y))
        rows.append(row)
    return w, h, rows


def main():
    manifest = json.load(open(os.path.join(OUT, "render-manifest.json"),
                              encoding="utf-8"))
    by_key = {(m["candidate"], m["background"]): m for m in manifest}
    montage_dir = os.path.join(OUT, "renders", "montages")
    os.makedirs(montage_dir, exist_ok=True)
    index = {}

    family_members = {f: [c for c, _ in items]
                      for f, items in recipes.FAMILIES.items()}
    family_members["D"] = [c for c, _ in recipes.FAMILY_D]

    for fam, members in family_members.items():
        nrows, ncols = len(members), len(recipes.BACKGROUNDS)
        width = MARGIN * 2 + ncols * TILE + (ncols - 1) * GAP
        height = MARGIN * 2 + nrows * TILE + (nrows - 1) * GAP
        bg = bytes(GRID + (255,))  # opaque RGBA quad, aligned to 4-byte pixels
        canvas = [bytearray(bg * width) for _ in range(height)]
        tile_map = []
        for r, cid in enumerate(members):
            for c, (bg_name, _, _) in enumerate(recipes.BACKGROUNDS):
                entry = by_key[(cid, bg_name)]
                _, _, rows = load_rgba(os.path.join(OUT, entry["png"]))
                x0 = MARGIN + c * (TILE + GAP)
                y0 = MARGIN + r * (TILE + GAP)
                for ty, tile_row in enumerate(rows):
                    canvas[y0 + ty][x0 * 4:(x0 + TILE) * 4] = tile_row
                if c == 0:  # mid-gray = the original single-background view
                    for fy in range(y0 - FRAME, y0 + TILE + FRAME):
                        for fx in range(x0 - FRAME, x0 + TILE + FRAME):
                            inside = (y0 <= fy < y0 + TILE
                                      and x0 <= fx < x0 + TILE)
                            if inside:
                                continue
                            off = fx * 4
                            for ch, v in enumerate(HIGHLIGHT):
                                canvas[fy][off + ch] = v
                            canvas[fy][off + 3] = 255
                tile_map.append({"row": r, "column": c, "candidate": cid,
                                 "background": bg_name,
                                 "source_png": entry["png"],
                                 "artifact_sha256": entry["artifact_sha256"]})
        out_path = os.path.join(montage_dir, f"family-{fam}-grid.png")
        pngwrite.write_png(out_path, width, height, canvas)
        index[f"family-{fam}"] = {
            "montage": os.path.relpath(out_path, OUT),
            "rows": members,
            "columns": [b[0] for b in recipes.BACKGROUNDS],
            "highlighted_column": "bg-midgray (original single background)",
            "tiles": tile_map,
        }
    with open(os.path.join(montage_dir, "montage-index.json"), "w",
              encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=2)
    print(json.dumps({"montages": len(index)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
