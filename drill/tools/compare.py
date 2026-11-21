#!/usr/bin/env python3
"""Receiver-side evidence tool for the offline sign-off provenance drill.

It reads only a reception kit directory.  It never opens answer/ or origin/.
Use it to inventory hashes, compare two images at full resolution, verify
labelled derivations, and produce the three-way verdict:

    provenance_established | candidates_only | indeterminate

Usage:
    python3 tools/compare.py inspect --case kit/case-a
    python3 tools/compare.py diff --case kit/case-a \
        --left signed/signed-sample.png --right artifacts/press-alpha-r1.png
    python3 tools/compare.py bind --case kit/case-a --json
"""

import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "lib"))

import pngcodec  # noqa: E402
import canonical  # noqa: E402


def sha256_file(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def verify_artifact_identity(entry, plate_id):
    """Recompute the service artifact id from published fields.

    id = sha256(plate + ":" + revision + ":" + scene_digest + ":" + format + ":" + byte_digest)
    A receiver can check this without any private key; it proves the metadata is
    self-consistent, not that the scene was authored by a particular person.
    """
    artifact = entry.get("artifact") or {}
    required = ("id", "revision", "scene_digest", "byte_digest")
    if not all(artifact.get(key) for key in required):
        return {"checked": False, "reason": "metadata incomplete"}
    recomputed = canonical.artifact_id(plate_id, artifact["revision"],
                                       artifact["scene_digest"], entry["format"],
                                       artifact["byte_digest"])
    return {"checked": True, "matches": recomputed == artifact["id"],
            "recomputed": recomputed, "recorded": artifact["id"]}


def verify_bundle(path):
    """Recompute the sealed bundle digest over the re-marshalled plate object."""
    with open(path, "r", encoding="utf-8") as handle:
        envelope = json.load(handle)
    if envelope.get("schema") != "prism-plate-bundle-v1":
        return {"checked": False, "reason": "unexpected schema"}
    recomputed = canonical.bundle_digest(envelope["plate"])
    return {"checked": True, "matches": recomputed == envelope["digest"],
            "recomputed": recomputed, "recorded": envelope["digest"]}


def load_manifest(case_dir):
    with open(os.path.join(case_dir, "manifest.json"), "r", encoding="utf-8") as handle:
        return json.load(handle)


def image_at(case_dir, relative):
    with open(os.path.join(case_dir, relative), "rb") as handle:
        data = handle.read()
    width, height, rgba = pngcodec.decode(data)
    return {
        "path": relative,
        "bytes_sha256": hashlib.sha256(data).hexdigest(),
        "width": width,
        "height": height,
        "rgba": rgba,
        "pixels_sha256": pngcodec.pixel_digest(width, height, rgba),
    }


def inventory(case_dir):
    manifest = load_manifest(case_dir)
    entries = []
    for root, _, files in os.walk(case_dir):
        for filename in sorted(files):
            path = os.path.join(root, filename)
            relative = os.path.relpath(path, case_dir)
            item = {"file": relative, "sha256": sha256_file(path),
                    "bytes": os.path.getsize(path)}
            if filename.lower().endswith(".png"):
                fact = image_at(case_dir, relative)
                item.update({
                    "width": fact["width"], "height": fact["height"],
                    "pixels_sha256": fact["pixels_sha256"],
                })
                for factor in (4, 8):
                    if fact["width"] % factor == 0 and fact["height"] % factor == 0:
                        tw, th, pixels = pngcodec.thumbnail(
                            fact["width"], fact["height"], fact["rgba"], factor)
                        item["thumbnail_%dx_sha256" % factor] = pngcodec.pixel_digest(
                            tw, th, pixels)
            entries.append(item)
    return {"case": manifest["case"], "manifest": manifest, "files": entries}


def diff(case_dir, left, right):
    a = image_at(case_dir, left)
    b = image_at(case_dir, right)
    result = pngcodec.compare((a["width"], a["height"], a["rgba"]),
                              (b["width"], b["height"], b["rgba"]))
    thumbnails = {}
    for factor in (4, 8):
        if a["width"] % factor == 0 and a["height"] % factor == 0:
            ta = pngcodec.thumbnail(a["width"], a["height"], a["rgba"], factor)
            tb = pngcodec.thumbnail(b["width"], b["height"], b["rgba"], factor)
            thumbnails[str(factor)] = (
                pngcodec.pixel_digest(*ta) == pngcodec.pixel_digest(*tb))
    return {
        "left": left, "right": right,
        "left_pixels_sha256": a["pixels_sha256"],
        "right_pixels_sha256": b["pixels_sha256"],
        "bytes_equal": a["bytes_sha256"] == b["bytes_sha256"],
        "pixels_equal": result["diff_pixels"] == 0,
        "diff_pixels": result["diff_pixels"],
        "bbox": result["bbox"],
        "max_channel_delta": result["max_channel_delta"],
        "thumbnail_equal": thumbnails,
        "pairs": [{"x": p[0], "y": p[1], "left": list(p[2]), "right": list(p[3])}
                  for p in result["pairs"]],
    }


def _proof_artifacts(case_dir, manifest):
    accepted = set()
    for relative in manifest["responses"]["included"]:
        if not relative.endswith(".json"):
            continue
        with open(os.path.join(case_dir, relative), "r", encoding="utf-8") as handle:
            record = json.load(handle)
        request = record.get("request", {})
        if request.get("method") == "PUT" and str(request.get("path", "")).startswith("/v1/proofs/"):
            body = request.get("body") or {}
            response_body = record.get("response", {}).get("body") or {}
            if body.get("state") == "accepted" and response_body.get("state") == "accepted":
                accepted.add(response_body.get("artifact"))
    return accepted


def _artifact_metadata(case_dir, manifest):
    """Map artifact id -> metadata drawn only from records inside the kit."""
    metadata = {}
    for entry in manifest.get("images", []):
        if entry.get("provenance") == "service_artifact" and entry.get("artifact"):
            metadata[entry["artifact"]["id"]] = {
                "id": entry["artifact"]["id"],
                "revision": entry["artifact"]["revision"],
                "scene_digest": entry["artifact"]["scene_digest"],
                "byte_digest": entry["artifact"]["byte_digest"],
                "file": entry["file"],
            }
    for relative in manifest["responses"]["included"]:
        if not relative.endswith(".json"):
            continue
        with open(os.path.join(case_dir, relative), "r", encoding="utf-8") as handle:
            record = json.load(handle)
        body = record.get("response", {}).get("body")
        request = record.get("request", {})
        if request.get("method") == "POST" and "render" in str(request.get("path", "")):
            if isinstance(body, dict) and body.get("id"):
                metadata[body["id"]] = {
                    "id": body["id"],
                    "revision": body.get("revision"),
                    "scene_digest": body.get("scene_digest"),
                    "byte_digest": body.get("digest"),
                    "file": None,
                }
    return metadata


def bind_case(case_dir):
    manifest = load_manifest(case_dir)
    included_revisions = manifest["source_excerpt"]["included_revisions"]
    omitted = manifest["source_excerpt"]["omitted_revision_numbers"]
    signed = image_at(case_dir, manifest["signed_sample"]["file"])
    metadata = _artifact_metadata(case_dir, manifest)
    accepted = _proof_artifacts(case_dir, manifest)

    # Verify every labelled derivation inside the kit.
    derivations = []
    for record in manifest.get("derivations", []):
        source_file = record["source_artifact"]["file"]
        source = image_at(case_dir, source_file)
        derived = image_at(case_dir, record["file"])
        same = (source["width"], source["height"], source["rgba"]) == (
            derived["width"], derived["height"], derived["rgba"])
        derivations.append({
            "file": record["file"],
            "source": source_file,
            "pixels_equal_to_source": same,
            "source_artifact_id": record["source_artifact"]["id"],
            "source_byte_digest": record["source_artifact"]["sha256"],
            "signed_byte_digest": derived["bytes_sha256"],
        })

    # Bind the received sample to an artifact id using bytes/metadata/derivation.
    binding = None
    if manifest["signed_sample"]["kind"] == "received_sample":
        for artifact_id, info in metadata.items():
            if info.get("byte_digest") == signed["bytes_sha256"]:
                binding = {"artifact": artifact_id, "basis": "byte identity with recorded artifact",
                           "revision": info.get("revision")}
                break
    else:
        for record in derivations:
            if record["file"] != manifest["signed_sample"]["file"]:
                continue
            if not record["pixels_equal_to_source"]:
                binding = {"artifact": None, "basis": "derivation record failed verification",
                           "revision": None}
                break
            source_info = metadata.get(record["source_artifact_id"])
            if source_info and source_info.get("byte_digest") == record["source_byte_digest"]:
                # The derivation record is self-asserted: it explains a byte
                # difference but does not authenticate the hand-off.
                identical = sorted(
                    name for name, info in metadata.items()
                    if name != record["source_artifact_id"]
                    and info.get("byte_digest") == record["source_byte_digest"])
                binding = {"artifact": record["source_artifact_id"],
                           "basis": "self_asserted_labelled_derivation",
                           "revision": source_info.get("revision"),
                           "byte_identical_artifacts_in_kit": identical}
            else:
                binding = {"artifact": None,
                           "basis": "derivation source artifact is not present with metadata",
                           "revision": None}
            break

    # Compare the sample with every full-resolution candidate present in the kit.
    candidates = []
    for entry in manifest.get("images", []):
        if entry.get("format") != "png" or entry.get("provenance") != "service_artifact":
            continue
        fact = image_at(case_dir, entry["file"])
        comparison = pngcodec.compare((signed["width"], signed["height"], signed["rgba"]),
                                      (fact["width"], fact["height"], fact["rgba"]))
        candidate = {
            "revision": entry["artifact"]["revision"],
            "artifact": entry["artifact"]["id"],
            "file": entry["file"],
            "pixels_equal": comparison["diff_pixels"] == 0,
            "bytes_equal": fact["bytes_sha256"] == signed["bytes_sha256"],
            "diff_pixels": comparison["diff_pixels"],
            "bbox": comparison["bbox"],
            "max_channel_delta": comparison["max_channel_delta"],
        }
        for factor in (4, 8):
            if fact["width"] % factor == 0 and signed["width"] % factor == 0:
                ta = pngcodec.thumbnail(signed["width"], signed["height"], signed["rgba"], factor)
                tb = pngcodec.thumbnail(fact["width"], fact["height"], fact["rgba"], factor)
                candidate["thumbnail_%dx_equal" % factor] = (
                    pngcodec.pixel_digest(*ta) == pngcodec.pixel_digest(*tb))
        candidates.append(candidate)

    matching = [c for c in candidates if c["pixels_equal"]]
    matched_candidates = ["%s revision %d" % (manifest["plate"]["id"], c["revision"])
                          for c in matching]
    accepted_binding = binding is not None and binding.get("artifact") in accepted
    established_on = None
    guarantees = []
    unbound_pixel_twins = [item for item in manifest.get("lookalikes", [])
                           if item.get("pixels_equal_to_signed")]

    if accepted_binding and len(matching) == 1 and not omitted:
        verdict = "provenance_established"
        source = matched_candidates[0]
        established_on = ("an accepted proof binds this artifact id; the received "
                          "sample equals that artifact byte-for-byte; exactly one "
                          "kit candidate is pixel-identical; the kit declares no "
                          "omitted revision of this plate")
        guarantees = [
            "成品级绑定：样本就是校样已接受的那一件成品（按成品 id 与字节摘要）。",
            "同版面内唯一：包内其余稿本的成品均与样本存在全分辨率像素差。",
        ]
    elif accepted_binding and len(matching) == 1 and omitted:
        # A revision that is not in the kit could be pixel-identical too.  Without
        # it, the strongest supportable claim is a candidate set, not a source.
        verdict = "candidates_only"
        source = None
    elif binding is not None and binding.get("artifact") and matching:
        verdict = "candidates_only"
        source = None
    else:
        verdict = "indeterminate"
        source = None

    # Even a proven artifact binding does not make the pixels unique across all
    # possible plates.  Record that limit instead of overstating the result.
    pixel_unique = not unbound_pixel_twins
    for item in unbound_pixel_twins:
        guarantees.append(
            "像素级唯一性未成立：包外 `%s`（%s）与样本像素相同，"
            "说明相同像素也可由另一版面产生。" % (item["file"], item["plate"]))
    if verdict == "provenance_established" and unbound_pixel_twins:
        # Same pixels from an unbound plate do not by themselves defeat a
        # byte-level artifact binding; they do defeat any claim of pixel
        # uniqueness.  Keep the verdict, state the limit precisely.
        guarantees.append(
            "结论是“样本就是被接受的该成品”，不是“只有它能产生这些像素”。")

    withheld_entries = []
    if verdict == "candidates_only":
        for number in omitted:
            withheld_entries.append({
                "revision": number,
                "status": "withheld",
                "note": ("该稿本在真实版面中存在，但源稿、成品或响应未纳入本包；"
                         "无法做像素比对，因此不能排除，也不能列为已匹配候选。"),
            })
    elif verdict == "provenance_established":
        for number in omitted:
            withheld_entries.append({
                "revision": number,
                "status": "withheld",
                "note": "未纳入本包；结论不依赖它，但缺证仍应登记。",
            })

    excluded = []
    for candidate in candidates:
        if candidate["pixels_equal"]:
            continue
        reason = ("全分辨率像素差 %d 像素，包围盒 %s，最大通道差 %d。"
                  % (candidate["diff_pixels"], candidate["bbox"],
                     candidate["max_channel_delta"]))
        if candidate.get("thumbnail_4x_equal"):
            reason += " 4x 缩略图虽相同，但缩略图不是身份证据。"
        excluded.append({"candidate": "%s revision %d"
                         % (manifest["plate"]["id"], candidate["revision"]),
                         "reason": reason})

    identity_checks = [
        dict(entry=entry["file"],
             **verify_artifact_identity(entry, manifest["plate"]["id"]))
        for entry in manifest.get("images", [])
        if entry.get("provenance") == "service_artifact"
    ]
    bundle_check = None
    if manifest.get("bundle"):
        bundle_check = verify_bundle(os.path.join(case_dir, manifest["bundle"]["file"]))

    lookalike_checks = []
    for entry in manifest.get("lookalikes", []):
        fact = image_at(case_dir, entry["file"])
        comparison = pngcodec.compare((signed["width"], signed["height"], signed["rgba"]),
                                      (fact["width"], fact["height"], fact["rgba"]))
        lookalike_checks.append({
            "file": entry["file"],
            "plate": entry["plate"],
            "is_received_sample": entry["is_received_sample"],
            "pixels_equal_to_signed": comparison["diff_pixels"] == 0,
            "diff_pixels": comparison["diff_pixels"],
            "note": ("相似度不是来源证据；该图没有绑定到受签样本的谱系或校样。"),
        })

    return {
        "case": manifest["case"],
        "verdict": verdict,
        "established_on": established_on,
        "guarantees": guarantees,
        "source": source,
        "candidates": matched_candidates,
        "excluded": excluded,
        "withheld_unresolved": withheld_entries,
        "signed_sample": {
            "file": manifest["signed_sample"]["file"],
            "kind": manifest["signed_sample"]["kind"],
            "sha256": signed["bytes_sha256"],
            "recorded_sha256": manifest["signed_sample"].get("sha256"),
            "sha256_matches_manifest": (manifest["signed_sample"].get("sha256")
                                        == signed["bytes_sha256"]),
            "pixels_sha256": signed["pixels_sha256"],
            "recorded_pixels_sha256": manifest["signed_sample"].get("pixels_sha256"),
            "pixels_match_manifest": (manifest["signed_sample"].get("pixels_sha256")
                                      == signed["pixels_sha256"]),
        },
        "binding": binding,
        "accepted_proofs": sorted(x for x in accepted if x),
        "derivations": derivations,
        "identity_checks": identity_checks,
        "bundle_check": bundle_check,
        "lookalike_checks": lookalike_checks,
        "included_revisions": included_revisions,
        "omitted_revisions": omitted,
        "thumbnail_note": ("相同缩略图只说明粗略外观接近；结论必须基于全分辨率逐像素比对。"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["inspect", "diff", "bind"])
    parser.add_argument("--case", required=True)
    parser.add_argument("--left")
    parser.add_argument("--right")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.command == "inspect":
        result = inventory(args.case)
    elif args.command == "diff":
        if not args.left or not args.right:
            parser.error("diff needs --left and --right")
        result = diff(args.case, args.left, args.right)
    else:
        result = bind_case(args.case)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "inspect":
        print("case %s: %d files" % (result["case"], len(result["files"])))
        for item in result["files"]:
            extra = ""
            if "pixels_sha256" in item:
                extra = "  pixels=%s" % item["pixels_sha256"][:16]
            print("  %-52s %s%s" % (item["file"], item["sha256"][:16], extra))
    elif args.command == "diff":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("case:     %s" % result["case"])
        print("verdict:  %s" % result["verdict"])
        if result.get("established_on"):
            print("basis:    %s" % result["established_on"])
        for item in result.get("guarantees", []):
            print("guarantee: %s" % item)
        print("source:   %s" % (result["source"] or "-"))
        print("candidates (pixel-matched): %s" % (", ".join(result["candidates"]) or "-"))
        for item in result["excluded"]:
            print("excluded %s: %s" % (item["candidate"], item["reason"]))
        for item in result["withheld_unresolved"]:
            print("withheld %s: %s" % (item["revision"], item["note"]))
        print("binding:  %s" % json.dumps(result["binding"], ensure_ascii=False))
        for check in result["identity_checks"]:
            if not check.get("checked"):
                print("identity %s: not checked (%s)" % (check["entry"], check["reason"]))
            elif not check.get("matches"):
                print("identity %s: MISMATCH" % check["entry"])
        if result["bundle_check"]:
            print("bundle:   %s" % ("digest verified" if result["bundle_check"].get("matches")
                                    else "digest MISMATCH"))
        for item in result["lookalike_checks"]:
            print("lookalike %s: pixels_equal=%s diff_pixels=%s (%s)"
                  % (item["file"], item["pixels_equal_to_signed"],
                     item["diff_pixels"], item["note"]))


if __name__ == "__main__":
    main()
