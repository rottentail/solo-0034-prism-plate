import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import pngio

idx = json.load(open(os.path.join(OUT, "renders/montages/montage-index.json"),
                     encoding="utf-8"))
fails = 0
checked = 0
for fam_key, info in idx.items():
    mont = pngio.read_png(os.path.join(OUT, info["montage"]))
    mw, mh, mpx = mont
    for tile in info["tiles"]:
        sw, sh, spx = pngio.read_png(os.path.join(OUT, tile["source_png"]))
        x0 = 10 + tile["column"] * (96 + 6)
        y0 = 10 + tile["row"] * (96 + 6)
        for y in range(6, 90, 7):
            for x in range(6, 90, 7):
                if mpx(x0 + x, y0 + y) != spx(x, y):
                    fails += 1
                    print("MISMATCH", fam_key, tile["candidate"],
                          tile["background"], x, y,
                          mpx(x0 + x, y0 + y), spx(x, y))
                checked += 1
print(json.dumps({"tiles": sum(len(i["tiles"]) for i in idx.values()),
                  "pixels_checked": checked, "mismatches": fails}))
assert fails == 0
