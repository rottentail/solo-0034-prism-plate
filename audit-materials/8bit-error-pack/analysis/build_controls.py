#!/usr/bin/env python3
"""Control artifacts: 16-bit output and exact-decimal transfer reference.

Two controls back the attribution of errors to specific stages:

1. 16-bit PNG control — the identical float pixels encoded to a 65535-step
   lattice instead of 255.  Pixel differences that vanish here are purely
   8-bit lattice effects.
2. Decimal transfer reference — the sRGB transfer evaluated at 50-digit
   precision gives the continuous (non-quantised) reference for the
   float-round-trip stage, isolating binary64 rounding inside the transfer
   from the later lattice rounding.
"""

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
PACK = HERE.parent
sys.path.insert(0, str(PACK))

from decimal import Decimal

from reference import composite as comp
from reference import png16
from reference import raster
from reference import srgb

DATA = PACK / "data"
PNG_DIR = PACK / "png"
STEM = "quant-probe"


def build_16bit_control():
    doc = json.loads((PACK / "sources" / (STEM + ".json")).read_text())[
        "document"]
    grid = raster.render_float(doc, 2)
    pixels16 = []
    max_8bit_step = 0
    for row in grid:
        out_row = []
        for px in row:
            if px.a <= 0.0:
                out_row.append((0, 0, 0, 0))
                continue
            st = px.unpremul()
            vals16 = (png16.encode16(st.r), png16.encode16(st.g),
                      png16.encode16(st.b), png16.encode16(min(1.0, px.a)))
            out_row.append(vals16)
            # Remaining lattice error measured in 8-bit code equivalents.
            for v in (st.r, st.g, st.b):
                residual = abs(v * 65535.0 - round(v * 65535.0)) / 257.0
                max_8bit_step = max(max_8bit_step, residual)
        pixels16.append(out_row)
    out = PNG_DIR / (STEM + ".16bit-control.png")
    png16.write_rgba16_png(str(out), doc["width"], doc["height"], pixels16)
    return {
        "artifact": out.name,
        "bit_depth": 16,
        "max_remaining_lattice_error_in_8bit_code_units": max_8bit_step,
        "note": "same float pixels as the 8-bit production input; only the "
                "output lattice changed",
    }


def decimal_transfer_probe():
    """Compare binary64 vs 50-digit transfer at the 256 code midpoints."""
    worst_encode = 0.0
    worst_at = 0.0
    samples = []
    for code in range(256):
        c = Decimal(code) / Decimal(255)
        exact = srgb.encode_exact(c)
        binary = Decimal(repr(srgb.encode(float(c))))
        gap = abs(exact - binary)
        if gap > worst_encode:
            worst_encode, worst_at = gap, code
        if code in (0, 1, 2, 127, 128, 254, 255):
            samples.append({
                "code": code,
                "decimal_linear": float(exact),
                "binary64_linear": float(binary),
                "abs_gap": float(gap),
            })
    # And the decode direction at representative linear-light values.
    decode_rows = []
    for lin in ("0.001", "0.0031308", "0.184507", "0.5", "1.0"):
        d = Decimal(lin)
        exact = srgb.decode_exact(d)
        binary = Decimal(repr(srgb.decode(float(d))))
        decode_rows.append({
            "linear_input": lin,
            "decimal_srgb": float(exact),
            "binary64_srgb": float(binary),
            "abs_gap": float(abs(exact - binary)),
        })
    return {
        "worst_encode_gap_srgb_units": float(worst_encode),
        "worst_at_code": worst_at,
        "encode_samples": samples,
        "decode_samples": decode_rows,
        "note": "gaps are binary64 rounding inside the transfer itself; "
                "the 8-bit lattice gap (1/510) is 2-3 orders larger",
    }


def main():
    DATA.mkdir(exist_ok=True)
    out = {
        "16bit_control": build_16bit_control(),
        "decimal_transfer": decimal_transfer_probe(),
    }
    (DATA / "controls.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
