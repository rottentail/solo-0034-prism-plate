#!/usr/bin/env python3
"""Write SHA-256 checksums of every delivered source, artifact and result file."""
import hashlib
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
GROUPS = {
    "sources_scenes": "scenes",
    "rendered_png": "artifacts",
    "independent_reference": "reference",
    "results": "results",
}


def sha256(path):
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def main():
    out = {}
    for group, dirname in GROUPS.items():
        base = ROOT / dirname
        entries = {}
        for p in sorted(base.rglob("*")):
            if p.is_file():
                entries[str(p.relative_to(ROOT))] = sha256(p)
        out[group] = entries
    out["scripts"] = {
        p.name: sha256(p)
        for p in sorted(ROOT.glob("*.py"))
    }
    (ROOT / "results" / "checksums.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2))
    total = sum(len(v) for v in out.values())
    print(f"wrote results/checksums.json covering {total} files")


if __name__ == "__main__":
    main()
