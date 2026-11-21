#!/usr/bin/env python3
"""Counterfactual tamper probe: where does the receiver tool stop being able to tell?

Each probe copies a reception kit into a scratch directory, applies one mutation,
and runs the receiver tool on the copy.  Nothing in drill/kit is modified.

The outcomes are the point of the exercise:

  caught        the tool reaches a different, weaker verdict or flags the change
  undetectable  the kit carries no signature, so a consistent in-package rewrite
                is accepted.  This is an irreducible ambiguity, not a tool bug.

Run:  python3 tools/tamper_probe.py [--case kit/case-a] [--json]
"""

import argparse
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DRILL = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import compare  # noqa: E402


def _copy(case_dir, scratch, name):
    target = os.path.join(scratch, name)
    shutil.copytree(case_dir, target)
    return target


def _load(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _save(path, value):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


def probe_swap_image_only(case_dir, scratch):
    target = _copy(case_dir, scratch, "swap-image-only")
    manifest = _load(os.path.join(target, "manifest.json"))
    images = [entry for entry in manifest["images"]
              if entry.get("format") == "png" and entry.get("provenance") == "service_artifact"]
    other = next(entry for entry in images if not entry["file"].endswith("-r2.png"))
    shutil.copy(os.path.join(target, other["file"]),
                os.path.join(target, manifest["signed_sample"]["file"]))
    return target, ("the substituted image is a real kit artifact, so byte identity "
                    "still matches that other artifact; only the accepted proof now "
                    "points elsewhere")


def probe_forge_proof(case_dir, scratch):
    target = _copy(case_dir, scratch, "forge-image-and-proof")
    manifest = _load(os.path.join(target, "manifest.json"))
    images = [entry for entry in manifest["images"]
              if entry.get("format") == "png" and entry.get("provenance") == "service_artifact"]
    other = next(entry for entry in images if not entry["file"].endswith("-r2.png"))
    signed_path = os.path.join(target, manifest["signed_sample"]["file"])
    shutil.copy(os.path.join(target, other["file"]), signed_path)

    # Rewrite the proof-decision record to name the substituted artifact.
    for relative in manifest["responses"]["included"]:
        record_path = os.path.join(target, relative)
        record = _load(record_path)
        request = record.get("request", {})
        if request.get("method") == "PUT" and str(request.get("path", "")).startswith("/v1/proofs/"):
            record["response"]["body"]["artifact"] = other["artifact"]["id"]
            _save(record_path, record)

    # Keep the manifest fully self-consistent with the substituted bytes.  This is
    # the strongest in-package forgery: bytes, byte digest and pixel digest agree,
    # and the proof record names the substituted artifact.
    import hashlib
    sys.path.insert(0, os.path.join(DRILL, "lib"))
    import pngcodec
    with open(signed_path, "rb") as handle:
        raw = handle.read()
    width, height, rgba = pngcodec.decode(raw)
    manifest["signed_sample"]["sha256"] = hashlib.sha256(raw).hexdigest()
    manifest["signed_sample"]["pixels_sha256"] = pngcodec.pixel_digest(width, height, rgba)
    _save(os.path.join(target, "manifest.json"), manifest)
    return target, ("no signature covers proof records or the manifest, so a fully "
                    "self-consistent rewrite is accepted as provenance")


def probe_pixel_edit(case_dir, scratch):
    target = _copy(case_dir, scratch, "pixel-edit")
    manifest = _load(os.path.join(target, "manifest.json"))
    signed_path = os.path.join(target, manifest["signed_sample"]["file"])
    with open(signed_path, "rb") as handle:
        data = handle.read()
    sys.path.insert(0, os.path.join(DRILL, "lib"))
    import pngcodec
    width, height, rgba = pngcodec.decode(data)
    pixels = bytearray(rgba)
    pixels[0:3] = bytes((pixels[0] ^ 0xFF, pixels[1], pixels[2]))
    with open(signed_path, "wb") as handle:
        handle.write(pngcodec.encode(width, height, bytes(pixels)))
    return target, "one pixel in the signed sample is altered; metadata is left stale"


def probe_derivation_lie(case_dir, scratch):
    target = _copy(case_dir, scratch, "derivation-lie")
    manifest = _load(os.path.join(target, "manifest.json"))
    if not manifest.get("derivations"):
        return None, "case has no derived sample"
    record = manifest["derivations"][0]
    signed_path = os.path.join(target, manifest["signed_sample"]["file"])
    # Replace the derived sample with a visibly different kit artifact while the
    # derivation record still claims pixel identity with its stated source.
    other = next(entry for entry in manifest["images"]
                 if entry.get("format") == "png"
                 and entry["file"] != record["file"])
    shutil.copy(os.path.join(target, other["file"]), signed_path)
    return target, ("derivation record still asserts pixel identity, but the file "
                    "no longer matches its stated source")


PROBES = [
    ("swap_image_only", probe_swap_image_only,
     "caught", "substituting another real artifact leaves the manifest's stored "
               "pixel digest stale, and the accepted proof names a different artifact"),
    ("forge_image_and_proof", probe_forge_proof,
     "undetectable", "no signature covers the proof record or manifest; a fully "
                     "self-consistent rewrite cannot be told apart from truth"),
    ("pixel_edit", probe_pixel_edit,
     "caught", "bytes no longer match the recorded artifact digest"),
    ("derivation_lie", probe_derivation_lie,
     "caught", "recorded derivation is re-verified against the file"),
]


def run(case_dir):
    scratch = tempfile.mkdtemp(prefix="prism-tamper-")
    results = []
    try:
        for name, fn, expectation, why in PROBES:
            target, explanation = fn(case_dir, scratch)
            if target is None:
                results.append({"probe": name, "skipped": True, "reason": explanation})
                continue
            baseline = compare.bind_case(case_dir)
            result = compare.bind_case(target)
            sample = result["signed_sample"]
            inconsistent = (not sample["sha256_matches_manifest"]
                            or not sample["pixels_match_manifest"])
            # A tamper is "caught" when the tool either notices in-package
            # inconsistency or refuses the package's claim.  A verdict change
            # alone is not detection: the package can be rewritten coherently.
            caught = inconsistent or any(
                not check.get("matches")
                for check in result.get("identity_checks", [])
                if check.get("checked")) or result["verdict"] == "indeterminate"
            results.append({
                "probe": name,
                "expected": expectation,
                "detected": caught,
                "in_package_inconsistent": inconsistent,
                "verdict": result["verdict"],
                "source": result.get("source"),
                "verdict_unchanged": result["verdict"] == baseline["verdict"],
                "binding_basis": (result.get("binding") or {}).get("basis"),
                "explanation": explanation,
                "why": why,
                "as_expected": ("caught" if caught else "undetectable") == expectation,
            })
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", default=os.path.join(DRILL, "kit", "case-a"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    results = run(os.path.abspath(args.case))
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return
    for item in results:
        if item.get("skipped"):
            print("%-24s SKIP  %s" % (item["probe"], item["reason"]))
            continue
        print("%-24s %-12s verdict=%-22s source=%s"
              % (item["probe"], "CAUGHT" if item["detected"] else "UNDETECTED",
                 item["verdict"], item["source"]))
        print("%-24s %s" % ("", item["explanation"]))
    if not all(item.get("skipped") or item["as_expected"] for item in results):
        print("probe outcome differs from documented expectation", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
