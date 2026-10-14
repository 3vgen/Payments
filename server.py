#!/usr/bin/env python3
"""Мои платежи: a small self-hosted server for tracking subscriptions and recurring payments.

Standard library only. Serves static/index.html and a JSON API backed by SQLite:
    GET    /api/subs        list all payments
    PUT    /api/subs/<id>   create or replace a payment
    DELETE /api/subs/<id>   delete a payment

Settings (environment variables):
    PLATEZHI_HOST      address to listen on (default 127.0.0.1; 0.0.0.0 opens it to the network)
    PLATEZHI_PORT      port (default 8095)
    PLATEZHI_DB        path to the SQLite file (default ./data/platezhi.db)
    PLATEZHI_PASSWORD  if set, the page asks for this password (HTTP Basic auth, any username)
"""
import base64
import gzip
import hashlib
import hmac
import json
import os
import re
import sqlite3
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "static" / "index.html"
HOST = os.environ.get("PLATEZHI_HOST", "127.0.0.1")
PORT = int(os.environ.get("PLATEZHI_PORT", "8095"))
DB_PATH = Path(os.environ.get("PLATEZHI_DB", ROOT / "data" / "platezhi.db"))
PASSWORD = os.environ.get("PLATEZHI_PASSWORD", "")
MAX_BODY = 64 * 1024
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

DB_PATH.parent.mkdir(parents=True, exist_ok=True)
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.execute("PRAGMA journal_mode=WAL")
_conn.execute("CREATE TABLE IF NOT EXISTS subs (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
_conn.commit()
_lock = threading.Lock()


_cache = None  # (json bytes, etag) of the current list; dropped on every write


def etag(data):
    return '"' + hashlib.blake2b(data, digest_size=8).hexdigest() + '"'


def list_subs_json():
    global _cache
    with _lock:
        if _cache is None:
            rows = _conn.execute("SELECT id, body FROM subs").fetchall()
            data = json.dumps([dict(json.loads(body), id=sid) for sid, body in rows], ensure_ascii=False).encode()
            _cache = (data, etag(data))
        return _cache


# the page is read and compressed once at startup (restart the service after editing it)
INDEX_BYTES = INDEX.read_bytes()
INDEX_GZ = gzip.compress(INDEX_BYTES, 9)
INDEX_ETAG = etag(INDEX_BYTES)


def save_sub(sid, body):
    body.pop("id", None)
    with _lock:
        _conn.execute(
            "INSERT INTO subs (id, body) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET body = excluded.body",
            (sid, json.dumps(body, ensure_ascii=False)),
        )
        _conn.commit()
        _invalidate()


def delete_sub(sid):
    with _lock:
        _conn.execute("DELETE FROM subs WHERE id = ?", (sid,))
        _conn.commit()
        _invalidate()


def _invalidate():
    global _cache
    _cache = None


class Handler(BaseHTTPRequestHandler):
    server_version = "platezhi/1.0"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {fmt % args}", flush=True)

    def _send(self, status, payload=None, ctype="application/json; charset=utf-8", extra=None):
        data = b""
        if payload is not None:
            data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        if data:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if data and self.command != "HEAD":
            self.wfile.write(data)

    def _authorized(self):
        if not PASSWORD:
            return True
        header = self.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                _, _, given = base64.b64decode(header[6:]).decode().partition(":")
            except Exception:
                return False
            return hmac.compare_digest(given.encode(), PASSWORD.encode())
        return False

    def _route(self):
        if not self._authorized():
            self._send(HTTPStatus.UNAUTHORIZED, {"error": "auth required"},
                       extra={"WWW-Authenticate": 'Basic realm="platezhi", charset="UTF-8"'})
            return None
        return self.path.split("?", 1)[0]

    def do_GET(self):
        path = self._route()
        if path is None:
            return
        if path in ("/", "/index.html"):
            if self._not_modified(INDEX_ETAG):
                return
            gz = "gzip" in self.headers.get("Accept-Encoding", "")
            extra = {"ETag": INDEX_ETAG, "Vary": "Accept-Encoding"}
            if gz:
                extra["Content-Encoding"] = "gzip"
            self._send(HTTPStatus.OK, INDEX_GZ if gz else INDEX_BYTES, "text/html; charset=utf-8", extra)
        elif path == "/api/subs":
            data, tag = list_subs_json()
            if not self._not_modified(tag):
                self._send(HTTPStatus.OK, data, extra={"ETag": tag})
        else:
            self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

    do_HEAD = do_GET

    def _not_modified(self, tag):
        if self.headers.get("If-None-Match") == tag:
            self._send(HTTPStatus.NOT_MODIFIED, extra={"ETag": tag})
            return True
        return False

    def _sub_id(self, path):
        if not path.startswith("/api/subs/"):
            return None
        sid = path[len("/api/subs/"):]
        return sid if ID_RE.match(sid) else None

    def do_PUT(self):
        path = self._route()
        if path is None:
            return
        sid = self._sub_id(path)
        if not sid:
            return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return self._send(HTTPStatus.BAD_REQUEST, {"error": "bad body size"})
        try:
            body = json.loads(self.rfile.read(length))
        except ValueError:
            return self._send(HTTPStatus.BAD_REQUEST, {"error": "invalid json"})
        if not isinstance(body, dict) or not str(body.get("name", "")).strip():
            return self._send(HTTPStatus.BAD_REQUEST, {"error": "name required"})
        save_sub(sid, body)
        self._send(HTTPStatus.NO_CONTENT)

    def do_DELETE(self):
        path = self._route()
        if path is None:
            return
        sid = self._sub_id(path)
        if not sid:
            return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
        delete_sub(sid)
        self._send(HTTPStatus.NO_CONTENT)


if __name__ == "__main__":
    print(f"Мои платежи: http://{HOST}:{PORT}  (db: {DB_PATH}, password: {'on' if PASSWORD else 'off'})", flush=True)
    if HOST not in ("127.0.0.1", "localhost", "::1") and not PASSWORD:
        print("Warning: listening on the network without PLATEZHI_PASSWORD; anyone who can reach the port sees your payments.", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
