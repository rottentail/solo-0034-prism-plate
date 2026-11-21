#!/usr/bin/env python3
"""Verify the delivered drill: receipts, receiver verdicts, and leak separation.

Checks
------
1. Every file listed in RECEIPT.json still matches its sha256.
2. The receiver tool, run only on kit/<case>/, reproduces the answer's
   receiver_verdict / source / candidate set for all five cases.
3. No reception kit file contains answer-side markers; kit/ and answer/ are
   physically separate directories.

Exit status is non-zero if any check fails.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import compare  # noqa: E402

ANSWER_MARKERS = ("receiver_verdict", "ground_truth", "source_revision",
                  "expected\"", "answer/index")


def sha256_file(path):
    import hashlib
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def check_receipt(root):
    with open(os.path.join(root, "RECEIPT.json"), "r", encoding="utf-8") as handle:
        receipt = json.load(handle)["files"]
    problems = []
    missing = []
    for relative, expected in receipt.items():
        path = os.path.join(root, relative)
        if not os.path.exists(path):
            missing.append(relative)
            continue
        if sha256_file(path) != expected:
            problems.append(relative)
    return {"checked": len(receipt), "mismatched": problems, "missing": missing}


def check_verdicts(root):
    with open(os.path.join(root, "answer", "index.json"), "r", encoding="utf-8") as handle:
        index = json.load(handle)["cases"]
    results = {}
    ok = True
    for key, expected in index.items():
        result = compare.bind_case(os.path.join(root, "kit", key))
        problems = []
        if result["verdict"] != expected["receiver_verdict"]:
            problems.append("verdict %s != %s" % (result["verdict"],
                                                  expected["receiver_verdict"]))
        if expected["receiver_source"] is not None:
            if result.get("source") != expected["receiver_source"]:
                problems.append("source %r != %r" % (result.get("source"),
                                                     expected["receiver_source"]))
        if sorted(result.get("candidates", [])) != sorted(expected["receiver_candidates"]):
            problems.append("candidates %r != %r" % (result.get("candidates"),
                                                     expected["receiver_candidates"]))
        results[key] = {"verdict": result["verdict"], "problems": problems}
        ok = ok and not problems
    return ok, results


def check_separation(root):
    problems = []
    for directory, _, files in os.walk(os.path.join(root, "kit")):
        for filename in files:
            if not filename.endswith((".json", ".md", ".svg")):
                continue
            path = os.path.join(directory, filename)
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
            for marker in ANSWER_MARKERS:
                if marker in text:
                    problems.append({"file": os.path.relpath(path, root),
                                     "marker": marker})
    return problems


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=ROOT,
                        help="delivery root to verify (default: this drill)")
    args = parser.parse_args()
    root = os.path.abspath(args.root)
    report = {}
    report["receipt"] = check_receipt(root)
    ok_verdicts, report["verdicts"] = check_verdicts(root)
    report["leaks"] = check_separation(root)
    failures = []
    if report["receipt"]["mismatched"] or report["receipt"]["missing"]:
        failures.append("receipt mismatch")
    if not ok_verdicts:
        failures.append("verdict mismatch")
    if report["leaks"]:
        failures.append("answer marker found in kit")
    report["ok"] = not failures
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if failures:
        print("FAILED: " + ", ".join(failures), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
