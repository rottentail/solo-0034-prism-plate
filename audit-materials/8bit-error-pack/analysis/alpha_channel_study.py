#!/usr/bin/env python3
"""Alpha-channel encoding facts observed on the real RGBA production PNG.

Two contracts are verified directly against ``alpha-channel-probe.png``:

1. clearing rule — a pixel whose *float* alpha is exactly zero encodes as
   (0,0,0,0); pixels with a positive float alpha that merely *rounds* to
   alpha code 0 keep their straight RGB codes.  That asymmetry is why code
   (R,G,B,0) with nonzero RGB appears in the artifact and must not be
   mistaken for a corruption;
2. sub-code alpha — alpha values inside one half-step of zero are
   indistinguishable from zero after 8-bit encoding even though their RGB
   channels survive.  This is the alpha-lattice counterpart of the colour
   1/510 bound and must be judged after compositing, not on the raw code.
"""

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
PACK = HERE.parent
sys.path.insert(0, str(PACK))

from reference import composite as comp
from reference import pngio

PNG_DIR = PACK / "png"
DATA = PACK / "data"


def main():
    decoded = pngio.read_png(str(PNG_DIR / "alpha-channel-probe.png"))
    assert decoded["color_type"] == 6, "expected an RGBA PNG"
    pixels = decoded["pixels"]

    cleared = subcode_alpha = positive_alpha = 0
    subcode_examples = []
    for y, row in enumerate(pixels):
        for x, (r8, g8, b8, a8) in enumerate(row):
            if a8 == 0 and (r8, g8, b8) == (0, 0, 0):
                cleared += 1
            elif a8 == 0:
                subcode_alpha += 1
                if len(subcode_examples) < 5:
                    subcode_examples.append(
                        {"x": x, "y": y, "rgba": [r8, g8, b8, a8]})
            else:
                positive_alpha += 1

    # Demonstrate that a sub-code-alpha pixel is visually inert over an
    # opaque backdrop: alpha code 0 means zero coverage once decoded.
    backdrop = comp.Straight(0.5, 0.5, 0.5, 1.0)
    if subcode_examples:
        r8, g8, b8, a8 = subcode_examples[0]["rgba"]
        decoded_px = comp.Premul.from_straight(
            comp.Straight(r8 / 255.0, g8 / 255.0, b8 / 255.0, a8 / 255.0))
        flat = comp.flatten_over_backdrop(decoded_px, backdrop)
        inert = (abs(flat.r - 0.5) < 1e-15
                 and abs(flat.g - 0.5) < 1e-15
                 and abs(flat.b - 0.5) < 1e-15)
    else:
        inert = True

    result = {
        "artifact": "alpha-channel-probe.png",
        "color_type": 6,
        "pixels_float_alpha_exactly_zero_cleared": cleared,
        "pixels_positive_float_alpha_rounded_to_code_zero": subcode_alpha,
        "pixels_with_positive_alpha_code": positive_alpha,
        "subcode_alpha_examples": subcode_examples,
        "subcode_pixel_visually_inert_over_backdrop": inert,
        "rules": [
            "float alpha == 0 -> RGBA (0,0,0,0) (RGB cleared)",
            "float alpha > 0 but round(a*255) == 0 -> RGB retained, "
            "alpha code 0; visually zero coverage once decoded",
            "never compare straight RGB of alpha-0 pixels; flatten first "
            "or skip RGB entirely",
        ],
    }
    DATA.mkdir(exist_ok=True)
    (DATA / "alpha-channel-facts.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
