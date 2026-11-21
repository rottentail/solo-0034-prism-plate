#!/usr/bin/env python3
"""Build the offline sign-off provenance drill from a real prismplate session.

Pipeline
--------
1. Start a real prismplate HTTP service on an ephemeral loopback port.
2. For every case, create the plate, append revisions, render PNG/SVG, open and
   decide proofs, and seal a bundle where the case calls for one.
3. Record every request/response verbatim in origin/ (binary bodies are stored
   as files and referenced by digest).
4. Derive the reception kit from the recorded facts: partial source excerpts,
   selected PNG/SVG, scattered response records, labelled encoding copies.
5. Derive the answer set and per-case arguments from the same recorded facts,
   with measured pixel relations rather than hand-written claims.
6. Run the receiver-side tool against each kit case and refuse to finish if its
   verdict differs from the answer set.

The production service is never modified.  The only build inputs are
examples-like scene documents written against the public prism-plate-v1
contract.
"""

import argparse
import base64
import copy
import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DRILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(DRILL, "lib"))

import canonical  # noqa: E402
import pngcodec  # noqa: E402
import scenes  # noqa: E402
from service import Service  # noqa: E402

THUMB_FACTORS = (4, 8)
STABLE_HEADERS = ("etag", "x-scene-digest", "content-type", "content-security-policy",
                  "x-content-type-options")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def write_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return sha256(data)


def write_json(path, value):
    data = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n").encode("utf-8")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return sha256(data)


def read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


class Recorder:
    """Calls the real service and keeps a complete transcript."""

    def __init__(self, service):
        self.service = service
        self.records = []

    def call(self, case, name, method, path, payload=None, status=200,
             binary=False, raw_body=False, note=""):
        reply = self.service.call(method, path, payload)
        if reply.status != status:
            raise RuntimeError("%s %s %s: expected %d, observed %d: %r"
                               % (case, name, path, status, reply.status, reply.body[:300]))
        record = {
            "case": case,
            "name": name,
            "request": {"method": method, "path": path, "body": payload},
            "response": {
                "status": reply.status,
                "headers": {k: v for k, v in reply.headers.items()},
            },
            "note": note,
        }
        if binary:
            record["response"]["body_sha256"] = sha256(reply.body)
            record["response"]["body_bytes"] = len(reply.body)
            record["response"]["body_omitted"] = "binary body stored in origin/artifacts"
            body = reply.body
        elif raw_body:
            record["response"]["body"] = json.loads(reply.body.decode("utf-8"))
            record["response"]["body_sha256"] = sha256(reply.body)
            body = reply.body
        else:
            record["response"]["body"] = json.loads(reply.body.decode("utf-8"))
            body = record["response"]["body"]
        self.records.append(record)
        return reply, body

    def transcript(self):
        return [copy.deepcopy(r) for r in self.records]


def build_case(recorder, case):
    plate = case["plate"]
    title = case["title"]
    documents = {}
    revision_notes = {}

    first = case["revisions"][0]
    documents[1] = first[1]()
    revision_notes[1] = first[2]
    recorder.call(case["key"], "create-plate", "POST", "/v1/plates",
                  {"id": plate, "title": title, "document": documents[1]}, 201)

    for number, (_, factory, note) in enumerate(case["revisions"][1:], start=2):
        documents[number] = factory()
        revision_notes[number] = note
        recorder.call(case["key"], "revise-r%d" % number, "PUT", "/v1/plates/" + plate,
                      {"expected_revision": number - 1, "document": documents[number]})

    reply, plate_body = recorder.call(case["key"], "get-plate", "GET",
                                      "/v1/plates/" + plate)
    if len(plate_body["revisions"]) != len(case["revisions"]):
        raise RuntimeError("%s: revision count mismatch" % case["key"])
    for revision in plate_body["revisions"]:
        expected = canonical.scene_digest(revision["document"])
        if expected != revision["digest"]:
            raise RuntimeError("%s: service digest not reproducible at revision %d"
                               % (case["key"], revision["number"]))
        documents[revision["number"]] = revision["document"]

    artifacts = {}
    for name, revision, fmt in case["renders"]:
        reply, meta = recorder.call(case["key"], "render-" + name, "POST",
                                    "/v1/plates/%s/render?revision=%d&format=%s"
                                    % (plate, revision, fmt), status=201)
        reply, data = recorder.call(case["key"], "get-artifact-" + name, "GET",
                                    "/v1/artifacts/" + meta["id"], binary=True)
        if sha256(data) != meta["digest"]:
            raise RuntimeError("%s: artifact bytes do not match digest" % case["key"])
        artifacts[name] = {
            "meta": meta,
            "data": data,
            "headers": {k.lower(): v for k, v in reply.headers.items()},
            "revision": revision,
            "format": fmt,
        }

    proofs = {}
    if case["proof"]:
        specification = case["proof"]
        target = None
        for name, artifact in artifacts.items():
            if (artifact["revision"] == specification["revision"]
                    and artifact["format"] == specification["format"]):
                target = (name, artifact)
                break
        if target is None:
            raise RuntimeError("%s: proof target artifact is not rendered" % case["key"])
        name, artifact = target
        recorder.call(case["key"], "proof-open", "POST", "/v1/proofs",
                      {"id": specification["id"], "artifact": artifact["meta"]["id"]}, 201)
        reply, decision = recorder.call(case["key"], "proof-decide", "PUT",
                                        "/v1/proofs/" + specification["id"],
                                        {"state": specification["state"],
                                         "note": specification["note"]})
        proofs[specification["id"]] = {
            "artifact_name": name,
            "artifact": artifact["meta"]["id"],
            "state": decision["state"],
            "note": decision["note"],
        }

    bundle = None
    bundle_digest = None
    if case["kit_bundle"]:
        reply, bundle = recorder.call(case["key"], "bundle", "GET",
                                      "/v1/plates/%s/bundle" % plate, raw_body=True)
        envelope = json.loads(bundle.decode("utf-8"))
        bundle_digest = envelope["digest"]

    extra_plates = []
    for extra in case.get("extra_plates", []):
        created = recorder.call(case["key"], "create-" + extra["id"], "POST",
                                "/v1/plates",
                                {"id": extra["id"], "title": extra["title"],
                                 "document": extra["document"]()}, 201)[1]
        reply, meta = recorder.call(case["key"], "render-" + extra["render_name"], "POST",
                                    "/v1/plates/%s/render?revision=1&format=png" % extra["id"],
                                    status=201)
        reply, data = recorder.call(case["key"], "get-artifact-" + extra["render_name"],
                                    "GET", "/v1/artifacts/" + meta["id"], binary=True)
        if sha256(data) != meta["digest"]:
            raise RuntimeError("%s: lookalike bytes do not match digest" % case["key"])
        extra_plates.append({
            "id": extra["id"],
            "title": extra["title"],
            "render_name": extra["render_name"],
            "kit_file": extra["kit_file"],
            "meta": meta,
            "data": data,
            "plate": created,
        })

    return {
        "case": case,
        "documents": documents,
        "revision_notes": revision_notes,
        "plate": plate_body,
        "artifacts": artifacts,
        "proofs": proofs,
        "bundle": bundle,
        "bundle_digest": bundle_digest,
        "extra_plates": extra_plates,
    }


# ----------------------------------------------------------------- measurment

def image_facts(data):
    width, height, rgba = pngcodec.decode(data)
    return {
        "width": width,
        "height": height,
        "rgba": rgba,
        "pixels_sha256": pngcodec.pixel_digest(width, height, rgba),
        "sha256": sha256(data),
        "bytes": len(data),
        "thumbnails": {},
    }


def measure_case(state):
    case = state["case"]
    facts = {}
    for name, artifact in state["artifacts"].items():
        if artifact["format"] != "png":
            facts[name] = {"format": "svg", "sha256": sha256(artifact["data"]),
                           "bytes": len(artifact["data"])}
            continue
        facts[name] = image_facts(artifact["data"])
        for factor in THUMB_FACTORS:
            tw, th, pixels = pngcodec.thumbnail(facts[name]["width"], facts[name]["height"],
                                                facts[name]["rgba"], factor)
            facts[name]["thumbnails"][str(factor)] = {
                "width": tw, "height": th,
                "sha256": pngcodec.pixel_digest(tw, th, pixels),
                "rgba": pixels,
            }

    relations = []
    names = [name for name, value in facts.items() if value.get("format") != "svg"]
    for i, left in enumerate(names):
        for right in names[i + 1:]:
            a = state["artifacts"][left]
            b = state["artifacts"][right]
            if a["meta"]["id"] == b["meta"]["id"]:
                continue
            diff = pngcodec.compare(
                (facts[left]["width"], facts[left]["height"], facts[left]["rgba"]),
                (facts[right]["width"], facts[right]["height"], facts[right]["rgba"]))
            relation = {
                "left": left,
                "right": right,
                "left_revision": a["revision"],
                "right_revision": b["revision"],
                "bytes_equal": facts[left]["sha256"] == facts[right]["sha256"],
                "pixels_equal": diff["diff_pixels"] == 0,
                "diff_pixels": diff["diff_pixels"],
                "bbox": diff["bbox"],
                "max_channel_delta": diff["max_channel_delta"],
                "pairs": diff["pairs"],
                "thumbnails_equal": {},
            }
            for factor in THUMB_FACTORS:
                relation["thumbnails_equal"][str(factor)] = (
                    facts[left]["thumbnails"][str(factor)]["sha256"]
                    == facts[right]["thumbnails"][str(factor)]["sha256"])
            relations.append(relation)
    return facts, relations


def relation(state, left, right):
    facts, relations = state["measurements"]
    for item in relations:
        if {item["left"], item["right"]} == {left, right}:
            return item
    raise KeyError((left, right))


def _jsonable_fact(fact):
    value = {k: v for k, v in fact.items() if k not in ("rgba", "thumbnails")}
    if "thumbnails" in fact:
        value["thumbnails"] = {
            factor: {"width": thumb["width"], "height": thumb["height"],
                     "sha256": thumb["sha256"]}
            for factor, thumb in fact["thumbnails"].items()
        }
    return value


def _jsonable_relations(relations):
    rows = []
    for item in relations:
        row = {k: v for k, v in item.items() if k != "pairs"}
        row["pairs"] = [{"x": p[0], "y": p[1], "left": list(p[2]), "right": list(p[3])}
                        for p in item.get("pairs", [])]
        rows.append(row)
    return rows


# ----------------------------------------------------------------------- kit

def filter_headers(headers):
    return {k: v for k, v in headers.items() if k in STABLE_HEADERS}


def build_kit(root, state):
    case = state["case"]
    key = case["key"]
    base = os.path.join(root, "kit", key)
    manifest = {
        "case": key,
        "plate": {"id": state["plate"]["id"], "title": state["plate"]["title"]},
        "package": "real prismplate session excerpt",
        "warning": ("本包是真实流程的节选。未纳入的响应、源稿或成品不代表被删除或篡改；"
                    "未纳入的材料必须在结论中列为未排除项。"),
        "source_excerpt": None,
        "images": [],
        "signed_sample": None,
        "responses": {"included": [], "withheld": []},
        "derivations": [],
    }

    included_revisions = list(case["kit_docs"])
    omitted = [number for number in sorted(state["documents"])
               if number not in included_revisions]
    excerpt = {
        "excerpt": bool(omitted),
        "kind": "derived_excerpt_of_GET_plate_response",
        "source_response": "get-plate",
        "included_revisions": included_revisions,
        "omitted_revision_numbers": omitted,
        "note": ("节选仅从 GET /v1/plates 的真实响应中保留上述稿本；"
                 "省略的稿本内容未纳入本包，不构成对源稿的删除或篡改。"),
        "plate": {
            "id": state["plate"]["id"],
            "title": state["plate"]["title"],
            "state": state["plate"]["state"],
            "revisions": [revision for revision in state["plate"]["revisions"]
                          if revision["number"] in included_revisions],
        },
    }
    write_json(os.path.join(base, "sources", "plate-excerpt.json"), excerpt)
    manifest["source_excerpt"] = {
        "file": "sources/plate-excerpt.json",
        "sha256": sha256(json.dumps(excerpt, ensure_ascii=False, indent=2).encode() + b"\n"),
        "included_revisions": included_revisions,
        "omitted_revision_numbers": omitted,
        "omission_is_not_tampering": True,
    }

    # original artifacts requested for the kit
    artifact_files = {}
    for name in case["kit_artifacts"]:
        artifact = state["artifacts"][name]
        extension = ".png" if artifact["format"] == "png" else ".svg"
        relative = "artifacts/%s-r%d%s" % (state["plate"]["id"], artifact["revision"], extension)
        digest = write_bytes(os.path.join(base, relative), artifact["data"])
        artifact_files[name] = relative
        entry = {
            "file": relative,
            "format": artifact["format"],
            "sha256": digest,
            "artifact": {
                "id": artifact["meta"]["id"],
                "revision": artifact["revision"],
                "scene_digest": artifact["meta"]["scene_digest"],
                "byte_digest": artifact["meta"]["digest"],
            },
            "provenance": "service_artifact",
        }
        if artifact["format"] == "png":
            fact = state["measurements"][0][name]
            entry.update({
                "width": fact["width"], "height": fact["height"],
                "pixels_sha256": fact["pixels_sha256"],
                "thumbnail": {"factor": 4,
                              "sha256": fact["thumbnails"]["4"]["sha256"]},
            })
        manifest["images"].append(entry)

    for revision in case["kit_svg"]:
        match = [(name, artifact) for name, artifact in state["artifacts"].items()
                 if artifact["revision"] == revision and artifact["format"] == "svg"]
        if not match:
            raise RuntimeError("%s: no SVG render recorded for revision %d"
                               % (case["key"], revision))
        name, artifact = match[0]
        relative = "vectors/%s-r%d.svg" % (state["plate"]["id"], artifact["revision"])
        digest = write_bytes(os.path.join(base, relative), artifact["data"])
        manifest["images"].append({
            "file": relative,
            "format": "svg",
            "sha256": digest,
            "artifact": {
                "id": artifact["meta"]["id"],
                "revision": artifact["revision"],
                "scene_digest": artifact["meta"]["scene_digest"],
                "byte_digest": artifact["meta"]["digest"],
            },
            "provenance": "service_artifact",
            "note": "SVG 是矢量校样，不是像素成品；不得用它替代 PNG 做逐像素认定。",
        })

    # signed sample
    signed_spec = case["signed"]
    signed_source = state["artifacts"][signed_spec["artifact"]]
    signed_fact = state["measurements"][0][signed_spec["artifact"]]
    signed_relative = "signed/" + signed_spec["name"]
    if signed_spec["derived"]:
        derived_bytes = pngcodec.encode(signed_fact["width"], signed_fact["height"],
                                        signed_fact["rgba"])
        write_bytes(os.path.join(base, signed_relative), derived_bytes)
        derivation = {
            "file": signed_relative,
            "is_service_artifact": False,
            "kind": signed_spec["derived"]["kind"],
            "source_artifact": {
                "id": signed_source["meta"]["id"],
                "revision": signed_source["revision"],
                "file": artifact_files[signed_spec["artifact"]],
                "sha256": signed_source["meta"]["digest"],
            },
            "transform": ("按原始尺寸解码源成品为 RGBA8，逐像素保持不变，"
                          "再以 PNG 重新编码；仅改变容器编码，不改变像素。"),
            "tool": "drill/lib/pngcodec.py",
            "verification": {
                "pixels_equal_to_source": True,
                "source_pixels_sha256": signed_fact["pixels_sha256"],
                "derived_sha256": sha256(derived_bytes),
            },
            "warning": ("派生记录是自述性说明，不带密码学签名；它解释字节差，"
                        "本身不能替代校样或交接记录来认定来源。"),
        }
        write_json(os.path.join(base, "derivations", "signed-sample.derivation.json"),
                   derivation)
        manifest["derivations"].append(derivation)
        manifest["signed_sample"] = {
            "file": signed_relative,
            "format": "png",
            "sha256": sha256(derived_bytes),
            "pixels_sha256": signed_fact["pixels_sha256"],
            "kind": "labelled_reencoding",
            "artifact_metadata": "见派生记录；样本本身不是服务成品。",
        }
    else:
        write_bytes(os.path.join(base, signed_relative), signed_source["data"])
        manifest["signed_sample"] = {
            "file": signed_relative,
            "format": "png",
            "sha256": sha256(signed_source["data"]),
            "pixels_sha256": signed_fact["pixels_sha256"],
            "kind": "received_sample",
            "artifact_metadata": ("见同目录响应记录" if case["key"] != "case-e"
                                  else "本包未提供该样本的成品元数据"),
        }

    # response records
    included = set(case["kit_responses"])
    for record in state["records"]:
        name = record["name"]
        target = {
            "case": key,
            "record": name,
            "request": record["request"],
            "response": {
                "status": record["response"]["status"],
                "headers": filter_headers(record["response"].get("headers", {})),
            },
            "capture_note": ("响应记录为真实会话的摘录；易变的 Date/Transfer-Encoding 头未保留。"),
        }
        body = record["response"].get("body")
        if name == "bundle":
            # The sealed bytes are preserved verbatim in sources/plate-bundle.json;
            # this parsed copy must not be mistaken for the byte-exact body.
            target["response"]["body_file"] = "sources/plate-bundle.json"
            target["response"]["body_note"] = "解析副本；字节精确内容见 body_file。"
        elif body is not None:
            target["response"]["body"] = body
        if "body_sha256" in record["response"]:
            target["response"]["body_sha256"] = record["response"]["body_sha256"]
            if "body_bytes" in record["response"]:
                target["response"]["body_bytes"] = record["response"]["body_bytes"]
            target["response"]["body_omitted"] = (record["response"].get("body_omitted")
                                                  or "二进制成品单独存放于 artifacts/ 或 signed/。")
        if name in included:
            write_json(os.path.join(base, "responses", name + ".json"), target)
            manifest["responses"]["included"].append("responses/%s.json" % name)
        else:
            # Only the fact that a call happened is disclosed.  Request bodies can
            # contain omitted source documents, so they must not enter the kit.
            manifest["responses"]["withheld"].append({
                "record": name,
                "request": {"method": record["request"]["method"],
                            "path": record["request"]["path"]},
                "body_included": False,
                "reason": ("真实会话中执行过，但请求体与响应体均未纳入本包；"
                           "此处只登记调用存在，不提供其中任何稿本内容。"),
            })

    if case["kit_bundle"]:
        write_bytes(os.path.join(base, "sources", "plate-bundle.json"), state["bundle"])
        manifest["bundle"] = {
            "file": "sources/plate-bundle.json",
            "sha256": sha256(state["bundle"]),
            "note": "封包只含稿本历史，不含成品与校样；封包摘要可校验包内谱系一致性。",
        }

    # thumbnail exhibits
    if case["kit_thumbnail_of"]:
        os.makedirs(os.path.join(base, "thumbnails"), exist_ok=True)
        exhibits = {"signed": signed_fact}
        if "png-r1" in state["measurements"][0] and case["key"] in ("case-d", "case-e"):
            exhibits["r1"] = state["measurements"][0]["png-r1"]
        for label, fact in exhibits.items():
            tw, th, pixels = pngcodec.thumbnail(fact["width"], fact["height"],
                                                fact["rgba"], 4)
            relative = "thumbnails/%s-4x.png" % label
            write_bytes(os.path.join(base, relative), pngcodec.encode(tw, th, pixels))
            manifest.setdefault("thumbnail_exhibits", []).append({
                "file": relative,
                "label": label,
                "factor": 4,
                "pixels_sha256": pngcodec.pixel_digest(tw, th, pixels),
                "note": "缩略图只用于筛查；相同缩略图不构成同一来源的证据。",
            })

    # look-alike plate rendered by the real service, never the received sample
    for extra in state.get("extra_plates", []):
        relative = extra["kit_file"]
        digest = write_bytes(os.path.join(base, relative), extra["data"])
        fact = image_facts(extra["data"])
        comparison = pngcodec.compare(
            (signed_fact["width"], signed_fact["height"], signed_fact["rgba"]),
            (fact["width"], fact["height"], fact["rgba"]))
        manifest.setdefault("lookalikes", []).append({
            "file": relative,
            "is_received_sample": False,
            "is_service_artifact": True,
            "plate": extra["id"],
            "title": extra["title"],
            "artifact": {
                "id": extra["meta"]["id"],
                "revision": extra["meta"]["revision"],
                "scene_digest": extra["meta"]["scene_digest"],
                "byte_digest": extra["meta"]["digest"],
            },
            "sha256": digest,
            "pixels_sha256": fact["pixels_sha256"],
            "pixels_equal_to_signed": comparison["diff_pixels"] == 0,
            "diff_pixels": comparison["diff_pixels"],
            "warning": ("这是另一版面的真实重渲染图，与受签样本没有谱系关系；"
                        "肉眼最接近或像素相同都不等于来源。"),
        })

    manifest["worksheet"] = "WORKSHEET.md"
    write_json(os.path.join(base, "manifest.json"), manifest)
    state["signed_file_sha256"] = manifest["signed_sample"]["sha256"]
    write_worksheet(base, case, manifest)
    return manifest


def write_worksheet(base, case, manifest):
    lines = [
        "# 取证工作表：%s" % case["key"],
        "",
        "版面：`%s`" % case["plate"],
        "",
        "## 1. 清单",
        "",
        "- [ ] 列出包内每个文件的 sha256、PNG 像素摘要、缩略图摘要。",
        "- [ ] 列出 included / withheld 响应；说明省略不等于篡改。",
        "- [ ] 对每个编码副本核对派生记录，并确认其不是服务成品。",
        "",
        "## 2. 比对",
        "",
        "- [ ] 先做全分辨率逐像素比对，记录差异像素数、包围盒和最大通道差。",
        "- [ ] 单独记录 4x 缩略图是否相同，并说明它为什么不能单独用于认定。",
        "- [ ] 把每个候选稿本的 scene_digest 与样本可得的 X-Scene-Digest 比较。",
        "",
        "## 3. 结论（三选一）",
        "",
        "- [ ] 足以认定签样来源：写明稿本号、绑定证据和排除理由。",
        "- [ ] 只能列出候选：写明候选集合、未能排除的未纳入稿本。",
        "- [ ] 现有材料无法判断：写明已排除项与缺少的证据。",
        "",
        "结论：____________________",
        "",
        "证据：____________________",
        "",
        "缺失证据与不可消除的歧义：____________________",
        "",
    ]
    write_bytes(os.path.join(base, "WORKSHEET.md"),
                ("\n".join(lines)).encode("utf-8"))


# -------------------------------------------------------------------- answer

def build_answer(root, states):
    answer_cases = {}
    for state in states:
        case = state["case"]
        facts, relations = state["measurements"]
        expected = case["expected"]
        signed_name = case["signed"]["artifact"]
        signed_fact = facts[signed_name]
        signed_artifact = state["artifacts"][signed_name]

        candidate_details = []
        for name, artifact in state["artifacts"].items():
            if artifact["format"] != "png":
                continue
            if artifact["meta"]["id"] == signed_artifact["meta"]["id"]:
                continue
            rel = relation(state, signed_name, name)
            candidate_details.append({
                "revision": artifact["revision"],
                "artifact": name,
                "pixels_equal": rel["pixels_equal"],
                "bytes_equal": rel["bytes_equal"],
                "diff_pixels": rel["diff_pixels"],
                "bbox": rel["bbox"],
                "max_channel_delta": rel["max_channel_delta"],
                "thumbnail_4x_equal": rel["thumbnails_equal"]["4"],
                "thumbnail_8x_equal": rel["thumbnails_equal"]["8"],
            })

        ground_truth = {
            "signed_sample_origin": ("service artifact %s of %s revision %d"
                                     % (signed_name, case["plate"], signed_artifact["revision"])),
            "source_revision": signed_artifact["revision"],
            "source_artifact": signed_name,
            "source_bytes_sha256": signed_artifact["meta"]["digest"],
            "source_scene_digest": signed_artifact["meta"]["scene_digest"],
            "source_pixels_sha256": signed_fact["pixels_sha256"],
        }
        if case["signed"]["derived"]:
            ground_truth["signed_sample_origin"] = (
                "labelled %s copy of service artifact %s of %s revision %d"
                % (case["signed"]["derived"]["kind"], signed_name, case["plate"],
                   signed_artifact["revision"]))
            ground_truth["derivation_note"] = ("样本是带派生记录的编码副本；"
                                               "像素与源成品一致，字节不同。")

        omitted = [number for number in sorted(state["documents"])
                   if number not in case["kit_docs"]]
        lookalike_relations = []
        for extra in state.get("extra_plates", []):
            extra_fact = image_facts(extra["data"])
            comparison = pngcodec.compare(
                (signed_fact["width"], signed_fact["height"], signed_fact["rgba"]),
                (extra_fact["width"], extra_fact["height"], extra_fact["rgba"]))
            lookalike_relations.append({
                "plate": extra["id"],
                "file": extra["kit_file"],
                "pixels_equal": comparison["diff_pixels"] == 0,
                "bytes_equal": extra["meta"]["digest"] == signed_artifact["meta"]["digest"],
                "diff_pixels": comparison["diff_pixels"],
            })
        entry = {
            "case": case["key"],
            "plate": case["plate"],
            "receiver_verdict": expected["verdict"],
            "receiver_source": expected["source"],
            "receiver_candidates": expected["candidates"],
            "receiver_excluded": expected["excluded"],
            "receiver_unresolved_withheld": (
                ["%s revision %d (material withheld)" % (case["plate"], number)
                 for number in omitted] if expected["verdict"] == "candidates_only"
                else []),
            "ground_truth": ground_truth,
            "ambiguity": expected["ambiguity"],
            "missing_evidence": expected["missing_evidence"],
            "measurements": {
                "signed_sample": {
                    "file": "signed/" + case["signed"]["name"],
                    "sha256": signed_fact["sha256"],
                    "pixels_sha256": signed_fact["pixels_sha256"],
                    "thumbnail_4x_sha256": signed_fact["thumbnails"]["4"]["sha256"],
                },
                "candidate_pixel_relations": candidate_details,
                "excluded_revisions": expected["excluded"],
                "ground_truth_notes": expected.get("ground_truth_notes", []),
                "omitted_revision_numbers": omitted,
                "lookalike_relations": lookalike_relations,
            },
            "replay": {
                "kit": "kit/%s" % case["key"],
                "bind": ("python3 tools/compare.py bind --case kit/%s --json"
                         % case["key"]),
            },
        }
        answer_cases[case["key"]] = entry

    # Run the receiver tool first, then write arguments with its measured output,
    # so the written guarantees cannot drift from what the kit actually supports.
    sys.path.insert(0, os.path.join(DRILL, "tools"))
    import compare as receiver_tool
    verified = {}
    for state in states:
        key = state["case"]["key"]
        verified[key] = receiver_tool.bind_case(os.path.join(root, "kit", key))
    for state in states:
        key = state["case"]["key"]
        write_argument(root, state, answer_cases[key], state["measurements"][0],
                       state["measurements"][1], verified[key])
    for key, entry in answer_cases.items():
        entry["receiver_guarantees"] = verified[key].get("guarantees", [])
        entry["receiver_established_on"] = verified[key].get("established_on")
    write_json(os.path.join(root, "answer", "index.json"), {
        "description": ("逐例答案。receiver_* 字段是仅凭接收包可以正当得出的结论；"
                        "ground_truth 是原始事实中记录的样本出处，接收者看不到。"),
        "cases": answer_cases,
    })
    return answer_cases


def build_relation_table(state):
    _, relations = state["measurements"]
    rows = []
    for item in relations:
        rows.append(
            "| R%d `%s` | R%d `%s` | %s | %s | %s | %s | %s |"
            % (item["left_revision"], item["left"], item["right_revision"], item["right"],
               "是" if item["bytes_equal"] else "否",
               "是" if item["pixels_equal"] else "否",
               item["diff_pixels"] if item["diff_pixels"] is not None else "尺寸不同",
               item["bbox"] if item["bbox"] else "—",
               "是" if item["thumbnails_equal"]["4"] else "否"))
    return "\n".join(rows)


def write_argument(root, state, entry, facts, relations, verified=None):
    case = state["case"]
    verified = verified or {}
    lines = [
        "# %s：%s" % (case["key"], case["title"]),
        "",
        "版面 `%s`。接收包：`kit/%s`。原始事实：`origin/%s/`。"
        % (case["plate"], case["key"], case["key"]),
        "",
        "## 接收包能支持什么",
        "",
        "- **结论**：`%s`" % entry["receiver_verdict"],
        "- **可认定来源**：%s" % (entry["receiver_source"] or "无"),
        "- **候选**：%s" % ("、".join(entry["receiver_candidates"]) or "无"),
    ]
    if entry["receiver_unresolved_withheld"]:
        lines.append("- **未纳入、无法排除**：%s"
                     % "、".join(entry["receiver_unresolved_withheld"]))
    for item in verified.get("guarantees", []):
        lines.append("- **保证**：%s" % item)
    lines += ["", "## 测量事实（由真实服务渲染结果计算）", "",
              "| 左 | 右 | 字节相同 | 像素相同 | 差异像素 | 包围盒 | 4x 缩略图相同 |",
              "|---|---|---|---|---|---|---|",
              build_relation_table(state), ""]

    lookalikes = entry["measurements"].get("lookalike_relations", [])
    if lookalikes:
        lines += ["### 外部重渲染对照", "",
                  "| 外部版面 | 文件 | 像素相同 | 字节相同 | 差异像素 |",
                  "|---|---|---|---|---|"]
        for item in lookalikes:
            lines.append("| `%s` | `%s` | %s | %s | %s |"
                         % (item["plate"], item["file"],
                            "是" if item["pixels_equal"] else "否",
                            "是" if item["bytes_equal"] else "否",
                            item["diff_pixels"]))
        lines += ["", ("外部图即使像素相同，也不等于来源：它没有被任何校样或谱系"
                       "绑定到接收样本。"), ""]

    lines += ["## 排除与不可消除的歧义", ""]
    for item in entry["receiver_excluded"]:
        lines.append("- 排除 %s：%s" % (item["candidate"], item["reason"]))
    for note in entry["measurements"].get("ground_truth_notes", []):
        lines.append("- 原始事实备注（接收者看不到）：%s" % note)
    for item in entry["ambiguity"]:
        lines.append("- 歧义：%s" % item)
    if not entry["ambiguity"]:
        lines.append("- 无额外歧义记录。")
    lines.append("")
    lines.append("## 缺少什么证据")
    if entry["missing_evidence"]:
        for item in entry["missing_evidence"]:
            lines.append("- %s" % item)
    else:
        lines.append("- 在本例范围内没有发现会改变结论的缺证。")
    lines += ["", "## 原始事实", "",
              "- 样本出处：%s" % entry["ground_truth"]["signed_sample_origin"],
              "- 源成品字节摘要：`%s`" % entry["ground_truth"]["source_bytes_sha256"],
              "- 源成品 scene_digest：`%s`" % entry["ground_truth"]["source_scene_digest"],
              "- 像素摘要：`%s`" % entry["ground_truth"]["source_pixels_sha256"],
              "- 接收包中样本文件摘要：`%s`"
              % state["signed_file_sha256"],
              "", "## 重跑", "",
              "```sh",
              "python3 tools/compare.py bind --case kit/%s --json" % case["key"],
              "python3 tools/compare.py diff --case kit/%s --left signed/%s --right artifacts/%s-r%d.png"
              % (case["key"], case["signed"]["name"], case["plate"],
                 state["artifacts"][case["signed"]["artifact"]]["revision"]),
              "```",
              ""]
    write_bytes(os.path.join(root, "answer", "arguments", case["key"] + ".md"),
                ("\n".join(lines)).encode("utf-8"))


# ------------------------------------------------------------------- receipts

def build_receipts(root):
    receipt = {}
    skip = {"RECEIPT.json"}
    for directory, dirnames, files in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in {"__pycache__", ".git"})
        for filename in sorted(files):
            if filename in skip or filename.endswith(".pyc"):
                continue
            path = os.path.join(directory, filename)
            relative = os.path.relpath(path, root)
            with open(path, "rb") as handle:
                receipt[relative] = sha256(handle.read())
    write_json(os.path.join(root, "RECEIPT.json"), {
        "description": ("交付物逐文件 sha256（不含 RECEIPT.json 自身）。"
                        "重跑后时间戳类内容会变，像素与场景摘要不变。"),
        "files": receipt,
    })


# ----------------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True, help="prismplate service binary")
    parser.add_argument("--out", default=DRILL, help="delivery root")
    parser.add_argument("--keep-temp", action="store_true")
    args = parser.parse_args()

    out = os.path.abspath(args.out)
    for section in ("kit", "origin", "answer"):
        shutil.rmtree(os.path.join(out, section), ignore_errors=True)
    os.makedirs(os.path.join(out, "kit"), exist_ok=True)
    os.makedirs(os.path.join(out, "origin"), exist_ok=True)
    os.makedirs(os.path.join(out, "answer"), exist_ok=True)

    root = tempfile.mkdtemp(prefix="prism-drill-build-")
    service = Service(args.binary, root)
    service.start()
    recorder = Recorder(service)
    states = []
    try:
        for case in scenes.CASES:
            state = build_case(recorder, case)
            state["records"] = [r for r in recorder.records if r["case"] == case["key"]]
            state["measurements"] = measure_case(state)
            states.append(state)
    finally:
        service.stop()

    data_root = os.path.join(root, "data")
    snapshot = os.path.join(data_root, "state.json")
    if not os.path.exists(snapshot):
        raise RuntimeError("service did not persist a snapshot")
    shutil.copy2(snapshot, os.path.join(out, "origin", "state.json"))

    # origin artefacts and source documents
    for state in states:
        key = state["case"]["key"]
        for number, document in state["documents"].items():
            write_json(os.path.join(out, "origin", "scenes", key,
                                    "revision-%d.json" % number), document)
        for name, artifact in state["artifacts"].items():
            extension = ".png" if artifact["format"] == "png" else ".svg"
            write_bytes(os.path.join(out, "origin", "artifacts", key,
                                     "%s-r%d%s" % (name, artifact["revision"], extension)),
                        artifact["data"])
        write_json(os.path.join(out, "origin", "cases", key + ".json"), {
            "case": key,
            "plate": state["case"]["plate"],
            "revision_notes": {str(k): v for k, v in state["revision_notes"].items()},
            "proofs": state["proofs"],
            "bundle_digest": state["bundle_digest"],
            "artifacts": {
                name: {
                    "meta": artifact["meta"],
                    "format": artifact["format"],
                    "sha256": sha256(artifact["data"]),
                    "pixels_sha256": (state["measurements"][0][name]["pixels_sha256"]
                                      if artifact["format"] == "png" else None),
                }
                for name, artifact in state["artifacts"].items()
            },
            "measurements": {
                "facts": {name: _jsonable_fact(fact)
                          for name, fact in state["measurements"][0].items()},
                "relations": _jsonable_relations(state["measurements"][1]),
            },
        })

    with open(os.path.join(out, "origin", "transcript.jsonl"), "w", encoding="utf-8") as handle:
        for record in recorder.transcript():
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=False) + "\n")

    # kit
    for state in states:
        build_kit(out, state)

    # answer
    build_answer(out, states)

    # build metadata
    with open(args.binary, "rb") as handle:
        binary_digest = sha256(handle.read())
    write_json(os.path.join(out, "origin", "build.json"), {
        "run_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "service_binary_sha256": binary_digest,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "case_order": [case["key"] for case in scenes.CASES],
        "determinism_note": ("场景摘要、成品 id、成品字节与像素摘要可重跑复现；"
                             "created 时间与封包摘要依赖会话时间，重跑会变。"),
    })

    build_receipts(out)
    shutil.rmtree(root, ignore_errors=True)

    # self-check with the receiver-side tool
    sys.path.insert(0, os.path.join(DRILL, "tools"))
    import compare
    failures = []
    for state in states:
        case = state["case"]
        result = compare.bind_case(os.path.join(out, "kit", case["key"]))
        expected = case["expected"]
        if result["verdict"] != expected["verdict"]:
            failures.append("%s verdict %s != %s"
                            % (case["key"], result["verdict"], expected["verdict"]))
        if expected["verdict"] == "provenance_established":
            if result.get("source") != expected["source"]:
                failures.append("%s source %r != %r"
                                % (case["key"], result.get("source"), expected["source"]))
        if expected["verdict"] == "candidates_only":
            got = sorted(result.get("candidates", []))
            want = sorted(expected["candidates"])
            if got != want:
                failures.append("%s candidates %r != %r" % (case["key"], got, want))
    if failures:
        for failure in failures:
            print("SELF-CHECK FAILED:", failure, file=sys.stderr)
        sys.exit(1)

    print(json.dumps({
        "built": out,
        "cases": len(states),
        "service_binary_sha256": binary_digest,
        "self_check": "passed",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
