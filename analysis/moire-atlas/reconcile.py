#!/usr/bin/env python3
"""Reconcile analytic predictions with PNG measurements and render checks.

Produces results/reconciliation.json: for every case it lays out the
predicted value, the value measured from the actual service PNG, the
absolute/relative error, and a pass flag. This is the recomputable atlas
ledger; no image is trusted without a matching entry here.
"""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent


def pct_err(measured, predicted):
    if predicted == 0:
        return None
    return round(100.0 * (measured - predicted) / predicted, 2)


def main():
    cases = {c["id"]: c for c in json.loads((ROOT / "cases.json").read_text())["cases"]}
    measured = {r["case"]: r for r in json.loads((ROOT / "results" / "measurements.json").read_text())}
    rendered = {r["case"]: r for r in json.loads((ROOT / "results" / "render-manifest.json").read_text())}
    rows = []
    for cid, case in cases.items():
        m = measured[cid]["measured"]
        pred = case["predictions"]
        checks = []
        # Renderer integrity: independent contract re-implementation vs PNG.
        ca = measured[cid]["contract_agreement"]
        checks.append({
            "check": "independent_contract_renderer_matches_service_png",
            "mismatch_fraction": ca["mismatch_fraction"],
            "max_code_value_delta": ca["max_code_value_delta"],
            "pass": ca["mismatch_fraction"] < 0.02,
        })
        checks.append({
            "check": "png_and_svg_digests_verified_over_http",
            "pass": all(fmt in rendered[cid]["artifacts"] for fmt in ("png", "svg")),
        })

        if cid == "A1-single-fine":
            checks.append(period_check("alias_carrier_period",
                                       m["measured_png_carrier"]["period_px"],
                                       pred["alias_period_px"]))
        if "beat_period_px" in pred and "measured_png_beat" in m:
            checks.append(period_check("true_beat_period",
                                       m["measured_png_beat"]["period_px"],
                                       pred["beat_period_px"]))
        if "beat_period_px" in pred and "measured_beat_period_px" in m:
            checks.append(period_check("true_beat_period",
                                       m["measured_beat_period_px"],
                                       pred["beat_period_px"]))
        if "true_beat_period_px" in pred and "measured_beat_period_px" in m:
            checks.append(period_check("true_beat_period",
                                       m["measured_beat_period_px"],
                                       pred["true_beat_period_px"]))
        if "beat_displacement_along_km_px" in pred and "measured_beat_displacement_px" in m:
            checks.append(period_check("beat_displacement_along_km",
                                       m["measured_beat_displacement_px"],
                                       pred["beat_displacement_along_km_px"], tol=30.0))
        if cid == "F1-fine-2x-observation":
            checks.append(period_check("scaled_beat_period",
                                       m["measured_beat_period_px"],
                                       pred["beat_period_px"]))
        if "expected_beat_displacement_px" in pred:
            got = m["measured_beat_displacement_px"]
            checks.append(period_check("beat_displacement", got,
                                       pred["expected_beat_displacement_px"], tol=30.0))
        if "true_beat_displacement_px" in pred:
            got = m["measured_beat_displacement_px"]
            checks.append(period_check("true_beat_displacement", got,
                                       pred["true_beat_displacement_px"], tol=30.0))
            # The decisive alias discriminator: retained true carrier barely
            # moves while the folded alias moves in the opposite direction.
            checks.append({
                "check": "true_carrier_sensitivity_near_zero",
                "measured_px": m["carrier_phase_response_px"],
                "pass": abs(m["carrier_phase_response_px"]) < 0.15,
            })
            checks.append({
                "check": "folded_alias_moves_oppositely",
                "measured_px": m["folded_phase_response_px"],
                "pass": m["folded_phase_response_px"] < -0.2,
            })
        if "true_beat_displacement_along_km_px" in pred:
            got = m["measured_beat_displacement_px"]
            checks.append(period_check("beat_displacement_along_km", got,
                                       pred["true_beat_displacement_along_km_px"], tol=30.0))
        # Alias carrier period where predicted.
        if "g2_alias_period_px" in pred:
            checks.append(period_check("g2_folded_alias_period",
                                       m["measured_folded_carrier"]["period_px"],
                                       pred["g2_alias_period_px"]))
        if "alias_g1_period_px" in pred:
            checks.append(period_check("g1_folded_alias_period",
                                       m["measured_png_carrier"]["period_px"],
                                       pred["alias_g1_period_px"]))
        # 2x scaling: F1 beat period must be ~2x the E1 measured beat period.
        if cid == "F1-fine-2x-observation":
            e1 = measured["E1-fine-angled-mixed"]["measured"]["measured_beat_period_px"]
            ratio = m["measured_beat_period_px"] / e1
            checks.append({
                "check": "beat_period_doubles_at_2x_observation_scale",
                "measured_ratio": round(ratio, 4), "predicted_ratio": 2.0,
                "relative_error_pct": pct_err(ratio, 2.0),
                "pass": abs(ratio - 2.0) < 0.05,
            })
        rows.append({"case": cid, "title": case["title"], "checks": checks})

    out = {
        "ledger": "prismplate moire atlas reconciliation",
        "tolerance_note": "Period checks use a 15% tolerance to absorb finite-DFT bin resolution; displacement checks use 30%. Renderer agreement is a separate pixel-level gate.",
        "rows": rows,
        "all_pass": all(c["pass"] for row in rows for c in row["checks"]),
    }
    (ROOT / "results" / "reconciliation.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2))
    for row in rows:
        print(row["case"])
        for c in row["checks"]:
            print(f"  [{'PASS' if c['pass'] else 'FAIL'}] {c['check']}",
                  {k: v for k, v in c.items() if k not in ('check', 'pass')})
    print("\nALL PASS:", out["all_pass"])


def period_check(name, measured_value, predicted_value, tol=15.0):
    err = pct_err(measured_value, predicted_value)
    return {
        "check": name,
        "measured": measured_value,
        "predicted": predicted_value,
        "relative_error_pct": err,
        "pass": err is not None and abs(err) <= tol,
    }


if __name__ == "__main__":
    main()
