import base64

SUB = {"name": "Яндекс Плюс", "amount": 399, "currency": "RUB", "cycle": "month",
       "nextDate": "2026-10-17", "anchorDay": 17, "category": "Сервисы и подписки"}


def test_page_served(server):
    status, body = server.request("GET", "/")
    assert status == 200 and "<title>Мои платежи</title>" in body


def test_empty_list(server):
    assert server.subs() == {}


def test_create_read_update_delete(server):
    server.put("a1", **SUB)
    assert server.subs()["a1"]["name"] == "Яндекс Плюс"
    server.put("a1", **dict(SUB, amount=499))
    subs = server.subs()
    assert len(subs) == 1 and subs["a1"]["amount"] == 499
    assert server.request("DELETE", "/api/subs/a1")[0] == 204
    assert server.subs() == {}


def test_id_in_body_is_ignored(server):
    server.put("a1", **dict(SUB, id="other"))
    assert list(server.subs()) == ["a1"]


def test_persists_across_restart(tmp_path):
    from conftest import Server
    s = Server(tmp_path)
    s.put("a1", **SUB)
    s.stop()
    s2 = Server(tmp_path)
    try:
        assert "a1" in s2.subs()
    finally:
        s2.stop()


def test_rejects_bad_input(server):
    assert server.request("PUT", "/api/subs/a1", raw=b"{not json")[0] == 400
    assert server.request("PUT", "/api/subs/a1", [1, 2])[0] == 400
    assert server.request("PUT", "/api/subs/a1", dict(SUB, name="  "))[0] == 400
    assert server.request("PUT", "/api/subs/a1", raw=b"x" * (70 * 1024))[0] == 400
    assert server.request("PUT", "/api/subs/bad%20id", SUB)[0] == 404
    assert server.request("PUT", "/api/subs/../x", SUB)[0] == 404
    assert server.request("GET", "/nope")[0] == 404
    assert server.subs() == {}


def auth(pw):
    return {"Authorization": "Basic " + base64.b64encode(f"me:{pw}".encode()).decode()}


def test_password_required(server_pw):
    assert server_pw.request("GET", "/")[0] == 401
    assert server_pw.request("GET", "/api/subs")[0] == 401
    assert server_pw.request("GET", "/", headers=auth("wrong"))[0] == 401
    assert server_pw.request("GET", "/", headers=auth("секрет-123"))[0] == 200
    assert server_pw.request("PUT", "/api/subs/a1", SUB)[0] == 401


# ---------- caching / traffic ----------

def raw_get(server, path, headers=None):
    import urllib.request, urllib.error
    req = urllib.request.Request(server.base + path, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_list_etag_304_and_invalidation(server):
    server.put("a1", **SUB)
    status, h, body = raw_get(server, "/api/subs")
    tag = h["ETag"]
    assert status == 200 and tag
    status, _, body = raw_get(server, "/api/subs", {"If-None-Match": tag})
    assert status == 304 and body == b""
    server.put("a1", **dict(SUB, amount=1))  # a write must change the ETag
    status, h2, _ = raw_get(server, "/api/subs", {"If-None-Match": tag})
    assert status == 200 and h2["ETag"] != tag
    assert server.request("DELETE", "/api/subs/a1")[0] == 204
    status, h3, body = raw_get(server, "/api/subs", {"If-None-Match": h2["ETag"]})
    assert status == 200 and body == b"[]"


def test_page_gzip_and_304(server):
    import gzip
    status, h, body = raw_get(server, "/", {"Accept-Encoding": "gzip"})
    assert status == 200 and h["Content-Encoding"] == "gzip"
    html = gzip.decompress(body)
    assert b"<title>" in html and len(body) < len(html) / 2
    status, h, plain = raw_get(server, "/")
    assert "Content-Encoding" not in h and plain == html
    assert raw_get(server, "/", {"If-None-Match": h["ETag"]})[0] == 304
