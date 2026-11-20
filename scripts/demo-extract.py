#!/usr/bin/env python3
"""Build verifiable partitive-branch evidence against an ephemeral service.

Regenerates preview JSON and actual rendered PNGs under var/demo using only the
standard library. The source plate is never modified; every derived plate can
be cross-checked against its preview document and content digest.
"""
import json, pathlib, signal, socket, subprocess, tempfile, time, urllib.request, urllib.error

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "var" / "demo"


def call(method, url, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=35) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="prism-demo-") as tmp:
        base = pathlib.Path(tmp)
        binary = base / "prismplate"
        subprocess.run(["go", "build", "-o", str(binary), "./cmd/prismplate"], cwd=ROOT, check=True)
        sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
        url = f"http://127.0.0.1:{port}"
        log = (base / "service.log").open("w")
        proc = subprocess.Popen([str(binary), "-listen", f"127.0.0.1:{port}", "-data", str(base / "state")], stdout=log, stderr=log)
        try:
            for _ in range(100):
                if proc.poll() is not None:
                    raise RuntimeError("service exited")
                try:
                    urllib.request.urlopen(url + "/healthz", timeout=1).close()
                    break
                except OSError:
                    time.sleep(0.05)
            seed = json.loads((ROOT / "examples" / "amber-proof.json").read_text())
            call("POST", url + "/v1/plates", seed)

            def render(plate, name):
                _, raw = call("POST", f"{url}/v1/plates/{plate}/render?revision=1&format=png")
                art = json.loads(raw)
                _, png = call("GET", f"{url}/v1/artifacts/{art['id']}")
                target = OUT / name
                target.write_bytes(png)
                (OUT / (name + ".digest")).write_text(art["digest"] + "\n")
                assert png[:8] == b"\x89PNG\r\n\x1a\n"
                return art["digest"]

            digest_source = render("amber-plate", "01-original-rev1.png")

            preview_plan = {
                "revision": 1, "placement": "original",
                "selection": [{"layer": "cyan-glaze", "shape": "cyan-window"}],
                "canvas": {"width": 160, "height": 120, "background": {"r": 1, "g": 1, "b": 1, "a": 1}},
            }
            _, raw = call("POST", url + "/v1/plates/amber-plate/branch/preview", preview_plan)
            preview = json.loads(raw)
            (OUT / "02-preview-cyan-on-white.json").write_text(json.dumps(preview, ensure_ascii=False, indent=2))
            commit = dict(preview_plan, id="cyan-extract", title="青色单形抽出（白底原位）")
            _, raw = call("POST", url + "/v1/plates/amber-plate/branch", commit)
            branch_resp = json.loads(raw)
            (OUT / "02-branch-response.json").write_text(json.dumps(branch_resp, ensure_ascii=False, indent=2))
            digest_white = render("cyan-extract", "02-extract-cyan-original-position.png")

            dark = {
                "revision": 1, "placement": "original",
                "selection": [{"layer": "cyan-glaze", "shape": "cyan-window"}],
                "canvas": {"width": 160, "height": 120, "background": {"r": 0.04, "g": 0.055, "b": 0.09, "a": 1}},
                "id": "cyan-dark", "title": "青色单形抽出（深底原位）",
            }
            call("POST", url + "/v1/plates/amber-plate/branch", dark)
            digest_dark = render("cyan-dark", "04-extract-cyan-dark-original.png")

            fit = {
                "revision": 1, "placement": "fit",
                "selection": [{"layer": "cyan-glaze", "shape": "cyan-window"}],
                "canvas": {"width": 128, "height": 128, "background": {"r": 0.04, "g": 0.055, "b": 0.09, "a": 1}},
                "id": "cyan-fit", "title": "青色单形抽出（深底贴合）",
            }
            call("POST", url + "/v1/plates/amber-plate/branch", fit)
            digest_fit = render("cyan-fit", "05-extract-cyan-fit.png")

            amber_fit = {
                "revision": 1, "placement": "fit",
                "selection": [{"layer": "amber-sheet"}],
                "canvas": {"width": 96, "height": 96},
                "id": "amber-fit", "title": "琥珀图层贴合版",
            }
            call("POST", url + "/v1/plates/amber-plate/branch", amber_fit)
            digest_amber_fit = render("amber-fit", "03-fit-amber-96.png")

            call("POST", url + "/v1/plates/amber-plate/branch", {"revision": 1, "id": "full-fork", "title": "整稿分支"})
            digest_full = render("full-fork", "06-full-branch.png")
            assert digest_full == digest_source, "full branch must render byte-identical PNG"

            # Source plate remains frozen at one revision.
            _, raw = call("GET", url + "/v1/plates/amber-plate")
            assert len(json.loads(raw)["revisions"]) == 1

            summary = {
                "source_png_digest": digest_source,
                "full_branch_matches_source": digest_full == digest_source,
                "extract_white_background_digest": digest_white,
                "extract_dark_background_digest": digest_dark,
                "extract_fit_digest": digest_fit,
                "amber_fit_digest": digest_amber_fit,
                "preview_notice_codes": [n["code"] for n in preview["notices"]],
            }
            (OUT / "00-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
            print(json.dumps({"result": "written", "dir": str(OUT), **summary}, ensure_ascii=False, indent=2))
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=45)
            log.close()


if __name__ == "__main__":
    main()
