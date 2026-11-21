#!/usr/bin/env python3
"""Tie-point divergence between independent transfer implementations.

The production service (Go) and this audit's independent reference (Python)
implement the identical IEC sRGB formula, but their respective libm ``pow``
routines can differ by one unit in the last place (ULP).  At ordinary
values that is invisible; at an exact half-code boundary (k+0.5)/255 one
ULP flips the rounded code to the adjacent integer.

This script consumes the CSV emitted by the standalone Go probe
(``analysis/gotieprobe``), compares it with the Python transfer at every
half-code boundary, and records every divergence.  All divergences are
exactly one code, which keeps them inside the declared 1-code quantisation
envelope — but it proves that bit-identical cross-implementation output
cannot be promised at tie points without pinning the libm, and it shows why
the audit judges *envelopes* rather than exact equality.
"""

import csv
import json
import math
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
PACK = HERE.parent
sys.path.insert(0, str(PACK))

from reference import srgb

DATA = PACK / "data"


def main(go_bin: str):
    proc = subprocess.run([go_bin, "run", "."], cwd=HERE / "gotieprobe",
                          capture_output=True, text=True, check=True)
    go_codes = {}
    for row in csv.DictReader(proc.stdout.splitlines()):
        go_codes[int(row["half_code"])] = int(row["rounded_code"])

    divergences = []
    for k in range(255):
        v = (k + 0.5) / 255.0
        rt = srgb.encode(srgb.decode(v))
        py_code = (0 if rt <= 0.0 else
                   255 if rt >= 1.0 else math.floor(rt * 255.0 + 0.5))
        go_code = go_codes[k]
        if py_code != go_code:
            divergences.append({
                "half_code_k": k,
                "python_code": py_code,
                "go_code": go_code,
                "abs_code_delta": abs(py_code - go_code),
                "python_roundtrip_x255": rt * 255.0,
            })

    result = {
        "boundaries_tested": 255,
        "diverging_boundaries": len(divergences),
        "max_abs_code_delta": max(
            (d["abs_code_delta"] for d in divergences), default=0),
        "cause": "1-ULP libm pow difference at exact half-code ties; the "
                 "two implementations use the same IEC formula",
        "inside_one_code_envelope": all(
            d["abs_code_delta"] <= 1 for d in divergences),
        "consequence": "cross-implementation bit equality is not promised "
                       "at ties; judge against the 1-code envelope, and use "
                       "flattened perceptual metrics for low-alpha pixels",
        "divergences": divergences,
    }
    DATA.mkdir(exist_ok=True)
    (DATA / "tie-divergence.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items()
                      if k != "divergences"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import os
    main(os.environ.get("GO_BIN", "go"))
