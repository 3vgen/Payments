import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, tmp_path, password=""):
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.password = password
        env = dict(os.environ, PLATEZHI_HOST="127.0.0.1", PLATEZHI_PORT=str(self.port),
                   PLATEZHI_DB=str(tmp_path / "test.db"), PLATEZHI_PASSWORD=password)
        self.proc = subprocess.Popen([sys.executable, str(ROOT / "server.py")], env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.1).close()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("server did not start")

    def request(self, method, path, body=None, headers=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as r:
                text = r.read().decode()
                return r.status, (json.loads(text) if text and "json" in r.headers.get("Content-Type", "") else text)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def subs(self):
        status, body = self.request("GET", "/api/subs")
        assert status == 200
        return {s["id"]: s for s in body}

    def put(self, sid, **fields):
        status, _ = self.request("PUT", f"/api/subs/{sid}", fields)
        assert status == 204

    def stop(self):
        self.proc.terminate()
        self.proc.wait(5)


@pytest.fixture
def server(tmp_path):
    s = Server(tmp_path)
    yield s
    s.stop()


@pytest.fixture
def server_pw(tmp_path):
    s = Server(tmp_path, password="секрет-123")
    yield s
    s.stop()
