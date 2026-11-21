"""Minimal HTTP client for a locally launched prismplate service.

The drill never fabricates responses: every scene, artifact byte string, proof
decision and bundle in the reception kit and in the answer set is obtained from
this real service process.  The service is started on an ephemeral loopback port
against a temporary data root and stopped on exit.
"""

import contextlib
import json
import os
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


class Response:
    def __init__(self, status, body, headers):
        self.status = status
        self.body = body
        self.headers = {k.lower(): v for k, v in headers.items()}

    def json(self):
        return json.loads(self.body.decode("utf-8"))

    @property
    def text(self):
        return self.body.decode("utf-8")


class Service:
    def __init__(self, binary, root, port=None):
        self.binary = binary
        self.root = root
        self.port = port or _free_port()
        self.base = "http://127.0.0.1:%d" % self.port
        self.proc = None
        self.log = None

    def start(self, timeout=10.0):
        os.makedirs(self.root, exist_ok=True)
        self.log = open(os.path.join(self.root, "service.log"), "w")
        self.proc = subprocess.Popen(
            [self.binary, "-listen", "127.0.0.1:%d" % self.port, "-data",
             os.path.join(self.root, "data")],
            stdout=self.log, stderr=self.log)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("service exited during startup")
            try:
                urllib.request.urlopen(self.base + "/healthz", timeout=1).close()
                return self
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("service did not become ready")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=45)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if self.log:
            self.log.close()
            self.log = None

    def call(self, method, path, payload=None, timeout=40):
        body = None
        headers = {"Accept": "*/*"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=body,
                                         method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as reply:
                return Response(reply.status, reply.read(), dict(reply.headers))
        except urllib.error.HTTPError as error:
            return Response(error.code, error.read(), dict(error.headers))

    def expect(self, method, path, payload=None, status=200):
        reply = self.call(method, path, payload)
        if reply.status != status:
            raise RuntimeError("%s %s: expected %d, observed %d: %r"
                               % (method, path, status, reply.status, reply.body[:300]))
        return reply

    # convenience wrappers -------------------------------------------------
    def create(self, plate_id, title, document):
        return self.expect("POST", "/v1/plates",
                           {"id": plate_id, "title": title, "document": document},
                           status=201)

    def revise(self, plate_id, expected, document):
        return self.expect("PUT", "/v1/plates/" + plate_id,
                           {"expected_revision": expected, "document": document})

    def render(self, plate_id, revision, fmt):
        return self.expect("POST", "/v1/plates/%s/render?revision=%d&format=%s"
                           % (plate_id, revision, fmt), status=201)

    def artifact(self, artifact_id):
        return self.expect("GET", "/v1/artifacts/" + artifact_id)

    def open_proof(self, proof_id, artifact_id):
        return self.expect("POST", "/v1/proofs",
                           {"id": proof_id, "artifact": artifact_id}, status=201)

    def decide_proof(self, proof_id, state, note):
        return self.expect("PUT", "/v1/proofs/" + proof_id,
                           {"state": state, "note": note})

    def bundle(self, plate_id):
        return self.expect("GET", "/v1/plates/%s/bundle" % plate_id)


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@contextlib.contextmanager
def running(binary, root=None, keep=False):
    root = root or tempfile.mkdtemp(prefix="prism-drill-")
    service = Service(binary, root)
    service.start()
    try:
        yield service
    finally:
        service.stop()
        if not keep and root.startswith(tempfile.gettempdir()):
            subprocess.run(["rm", "-rf", root], check=False)
