"""Byte-compatible reproduction of the service's scene digest and artifact id.

The service hashes the *re-marshalled* Document struct with Go's encoding/json.
Field order and `omitempty` are part of that contract, so this module mirrors the
struct layout in internal/scene/scene.go rather than dumping the input dict.
"""

import hashlib
import json

# Float formatting equivalent to Go's encoding/json for the value ranges used by
# prism-plate documents (all coordinates and channels are small finite numbers).
def _number(value):
    if isinstance(value, bool):
        raise TypeError("bool is not a scene number")
    if isinstance(value, int):
        return str(value)
    if value == int(value) and abs(value) < 1e21:
        return str(int(value))
    text = repr(float(value))
    if text.endswith(".0"):
        text = text[:-2]
    return text


_ESCAPES = {
    '"': '\\"', "\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t",
    "\b": "\\b", "\f": "\\f",
}


def _string(value):
    out = ['"']
    for ch in value:
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif ch == "<":
            out.append("\\u003c")
        elif ch == ">":
            out.append("\\u003e")
        elif ch == "&":
            out.append("\\u0026")
        elif ch == " ":
            out.append("\\u2028")
        elif ch == " ":
            out.append("\\u2029")
        elif ord(ch) < 0x20:
            out.append("\\u%04x" % ord(ch))
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _emit(value):
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, (int, float)):
        return _number(value)
    if isinstance(value, list):
        return "[" + ",".join(_emit(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(_string(k) + ":" + _emit(v) for k, v in value.items()) + "}"
    raise TypeError(type(value))


def _color(c):
    return {"r": c["r"], "g": c["g"], "b": c["b"], "a": c["a"]}


def _point(p):
    return {"x": p["x"], "y": p["y"]}


def _op(o):
    value = {"kind": o["kind"]}
    if o.get("points"):
        value["points"] = [_point(p) for p in o["points"]]
    return value


def _brush(b):
    value = {
        "kind": b["kind"],
        "color": _color(b["color"]),
        "from": _point(b["from"]),
        "to": _point(b["to"]),
    }
    if b.get("stops"):
        value["stops"] = [{"at": s["at"], "color": _color(s["color"])} for s in b["stops"]]
    return value


def _shape(s):
    return {
        "id": s["id"],
        "path": [_op(o) for o in s["path"]],
        "fill_rule": s["fill_rule"],
        "brush": _brush(s["brush"]),
        "transform": list(s["transform"]),
    }


def _layer(layer):
    value = {"id": layer["id"], "opacity": layer["opacity"], "blend": layer["blend"]}
    if layer.get("clip"):
        value["clip"] = [_op(o) for o in layer["clip"]]
    if layer.get("mask"):
        value["mask"] = [_op(o) for o in layer["mask"]]
    value["shapes"] = [_shape(s) for s in layer["shapes"]]
    return value


def canonical_document(document):
    """Return exactly the bytes Go's json.Marshal emits for scene.Document."""
    value = {
        "width": document["width"],
        "height": document["height"],
        "background": _color(document["background"]),
        "layers": [_layer(layer) for layer in document["layers"]],
    }
    return _emit(value).encode("utf-8")


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def scene_digest(document):
    return sha256_hex(canonical_document(document))


def artifact_id(plate, revision, scene_digest_value, fmt, byte_digest):
    material = "%s:%d:%s:%s:%s" % (plate, revision, scene_digest_value, fmt, byte_digest)
    return sha256_hex(material.encode("utf-8"))


def bundle_digest(plate_object):
    """bundle.Seal marshals the entire Plate struct, then hashes those bytes."""
    return sha256_hex(_emit(_plate(plate_object)).encode("utf-8"))


def _time(value):
    return value  # RFC3339Nano strings are preserved verbatim


def _plate(p):
    revisions = []
    for r in p["revisions"]:
        revisions.append({
            "number": r["number"],
            "digest": r["digest"],
            "document": r["document"],
            "created": r["created"],
        })
    return {"id": p["id"], "title": p["title"], "state": p["state"], "revisions": revisions}
