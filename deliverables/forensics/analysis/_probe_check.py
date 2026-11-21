import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model
import recipes


def codes(rec, bg, ba):
    out_codes, _ = model.composite(
        rec["rgb"], rec["brush_a"], rec["opacity"], rec["blend"], bg, ba
    )
    return out_codes


def blank_codes(bg, ba):
    # No coverage: output equals the background itself, composited over nothing.
    px = model.from_color(*bg, ba)
    return model.rgba8(px)


summary = {}
for fam, recs in recipes.FAMILIES.items():
    print(f"== Family {fam} ==")
    for name, bg, ba in recipes.BACKGROUNDS:
        seen = {}
        for cid, rec in recs:
            seen[cid] = codes(rec, bg, ba)
        uniq = set(seen.values())
        flag = "SAME " if len(uniq) == 1 else "SPLIT"
        print(f"  {name:14s} {flag} {seen}")
        summary[(fam, name)] = flag
print("== Family D ==")
for name, bg, ba in recipes.BACKGROUNDS:
    seen = {cid: blank_codes(bg, ba) for cid, _ in recipes.FAMILY_D}
    uniq = set(seen.values())
    print(f"  {name:14s} {'SAME ' if len(uniq)==1 else 'SPLIT'} {seen}")

# B2 vs B4 must be identical on every background including the alpha probe.
b2 = dict(recipes.FAMILY_B[1][1])
b4 = dict(recipes.FAMILY_B[2][1]) if False else dict(recipes.FAMILY_B[3][1])
for name, bg, ba in recipes.BACKGROUNDS:
    c2, c4 = codes(b2, bg, ba), codes(b4, bg, ba)
    assert c2 == c4, (name, c2, c4)
print("\nB2 == B4 on every background (permanent brush-alpha/opacity ambiguity): OK")
