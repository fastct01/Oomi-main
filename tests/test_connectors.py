import base64
import hashlib
import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from oomi.connectors import AppTool, ConnectorStore, McpError
from oomi.safety import READ, WRITE, decide


class FakeApp:
    """A tiny MCP server with OAuth: protected-resource metadata, AS metadata, registration, PKCE, refresh."""

    def __init__(self, open_access=False):
        self.open_access = open_access
        self.tokens = set()
        self.codes = {}
        self.sessions = set()
        self.calls = []
        app = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, data, status=200, headers=None):
                body = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/.well-known/oauth-protected-resource/mcp":
                    return self._json({"resource": app.base + "/mcp", "authorization_servers": [app.base],
                                       "scopes_supported": ["read", "write"]})
                if self.path == "/.well-known/oauth-authorization-server":
                    return self._json({"authorization_endpoint": app.base + "/authorize",
                                       "token_endpoint": app.base + "/token", "registration_endpoint": app.base + "/register"})
                self._json({}, 404)

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if self.path == "/register":
                    return self._json({"client_id": "client-1"}, 201)
                if self.path == "/token":
                    form = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
                    if form["grant_type"] == "authorization_code":
                        challenge = app.codes.pop(form["code"], None)
                        digest = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).decode().rstrip("=")
                        if challenge != digest or form.get("resource") != app.base + "/mcp":
                            return self._json({"error": "invalid_grant"}, 400)
                        tok = "tok-1"
                    elif form["grant_type"] == "refresh_token" and form["refresh_token"] == "refresh-1":
                        tok = "tok-2"
                    else:
                        return self._json({"error": "invalid_grant"}, 400)
                    app.tokens.add(tok)
                    return self._json({"access_token": tok, "refresh_token": "refresh-1", "expires_in": 3600})
                if self.path != "/mcp":
                    return self._json({}, 404)
                auth = self.headers.get("Authorization", "")
                if not app.open_access and auth[7:] not in app.tokens:
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", f'Bearer resource_metadata="{app.base}/.well-known/oauth-protected-resource/mcp"')
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                msg = json.loads(raw)
                if msg["method"] == "initialize":
                    sid = f"s{len(app.sessions) + 1}"
                    app.sessions.add(sid)
                    return self._json({"jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": "2025-06-18",
                                       "capabilities": {"tools": {}}, "serverInfo": {"name": "fake"}}}, headers={"Mcp-Session-Id": sid})
                if self.headers.get("Mcp-Session-Id") not in app.sessions:
                    return self._json({}, 404)
                if "id" not in msg:
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if msg["method"] == "tools/list":
                    result = {"tools": [
                        {"name": "search", "description": "Search pages", "annotations": {"readOnlyHint": True},
                         "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}},
                        {"name": "create-page", "description": "Create a page",
                         "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}}}}]}
                    # answered as a server-sent event stream, with a notification first
                    body = ("event: message\ndata: " + json.dumps({"jsonrpc": "2.0", "method": "notifications/progress"}) + "\n\n"
                            + "event: message\ndata: " + json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\n\n").encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if msg["method"] == "tools/call":
                    app.calls.append((auth, msg["params"]))
                    p = msg["params"]
                    return self._json({"jsonrpc": "2.0", "id": msg["id"], "result": {
                        "content": [{"type": "text", "text": f"{p['name']}: {json.dumps(p['arguments'])}"}]}})
                self._json({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "no such method"}})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def authorize(self, auth_url: str) -> tuple:
        """What the browser and the app's sign-in page do: approve, then redirect with a code."""
        q = {k: v[0] for k, v in parse_qs(urlparse(auth_url).query).items()}
        assert q["code_challenge_method"] == "S256" and q["scope"] == "read write" and q["resource"] == self.base + "/mcp"
        self.codes["code-1"] = q["code_challenge"]
        return q["state"], "code-1", q["redirect_uri"]


@pytest.fixture
def fake():
    f = FakeApp()
    yield f
    f.httpd.shutdown()


def memory_store(home):
    vault = {}
    return ConnectorStore(home, vault.get, lambda k, v: vault.__setitem__(k, v) or True, lambda k: vault.pop(k, None)), vault


def test_signing_in_to_an_app_and_using_its_tools(tmp_path, fake):
    store, vault = memory_store(tmp_path)
    app = store.custom("Wiki", fake.base + "/mcp")
    res = store.connect(app, "http://127.0.0.1:9/oauth/callback")
    state, code, redirect = fake.authorize(res["auth_url"])
    assert redirect == "http://127.0.0.1:9/oauth/callback"
    rec = store.finish_oauth(state, code)
    assert [t["name"] for t in rec["tools"]] == ["search", "create-page"] and rec["tools"][0]["read_only"]
    assert json.loads(vault["app:wiki"])["access_token"] == "tok-1"
    with pytest.raises(McpError):
        store.finish_oauth(state, code)  # a state works once

    tools = {t.name: t for t in store.tools()}
    search, create = tools["wiki__search"], tools["wiki__create-page"]
    ctx = SimpleNamespace(taint=SimpleNamespace(active=False))
    a = search.assess({"q": "x"}, ctx)
    assert a.level == READ and decide(a, "project", False, True, set()) == ("allow", "")
    a = create.assess({"title": "x"}, ctx)
    assert a.level == WRITE and decide(a, "project", False, True, set())[0] == "ask"
    assert decide(a, "project", False, True, {a.approval_key}) == ("allow", "")  # "Always allow" for this tool
    assert decide(a, "read-only", False, True, set())[0] == "deny"
    ctx.taint.active = True
    assert decide(search.assess({"q": "x"}, ctx), "project", True, True, set())[0] == "ask"

    out = search.run({"q": "roadmap"}, None)
    assert out.text == 'search: {"q": "roadmap"}' and out.taint and not out.is_error
    assert fake.calls[-1][0] == "Bearer tok-1"

    # the token stops working: refreshed once, then the call goes through
    fake.tokens.discard("tok-1")
    assert not create.run({"title": "Plan"}, None).is_error
    assert fake.calls[-1][0] == "Bearer tok-2" and json.loads(vault["app:wiki"])["access_token"] == "tok-2"

    assert store.tools(off={"wiki"}) == []
    assert store.disconnect("wiki") and store.tools() == [] and "app:wiki" not in vault


def test_gmail_signs_in_with_the_users_own_oauth_client(tmp_path, fake):
    from oomi.connectors import CATALOG
    store, vault = memory_store(tmp_path)
    gmail = store.find("gmail")
    assert gmail["auth"] == "client" and gmail["url"] == "https://gmailmcp.googleapis.com/mcp/v1"
    assert "oauth" not in next(a for a in store.catalog() if a["id"] == "gmail")  # endpoints stay server-side
    with pytest.raises(McpError, match="client ID"):
        store.connect(gmail, "http://127.0.0.1:47821/oauth/callback")
    q = parse_qs(urlparse(store.connect(gmail, "http://127.0.0.1:47821/oauth/callback",
                                        client={"client_id": "abc.apps.googleusercontent.com", "client_secret": "s3"})["auth_url"]).query)
    assert q["client_id"] == ["abc.apps.googleusercontent.com"] and q["access_type"] == ["offline"] and q["prompt"] == ["consent"]
    assert set(q["scope"][0].split()) == {"https://www.googleapis.com/auth/gmail.readonly",
                                          "https://www.googleapis.com/auth/gmail.compose"}
    assert "resource" not in q and q["redirect_uri"] == ["http://127.0.0.1:47821/oauth/callback"]
    # the same flow against the fake app: the user's client, no registration, secret kept for refreshing
    app = {**next(c for c in CATALOG if c["id"] == "gmail"), "id": "mail", "url": fake.base + "/mcp",
           "oauth": {"authorization_endpoint": fake.base + "/authorize", "token_endpoint": fake.base + "/token",
                     "scope": "read write", "resource": fake.base + "/mcp"}}
    res = store.connect(app, "http://127.0.0.1:9/oauth/callback", client={"client_id": "mine", "client_secret": "shh"})
    state, code, _ = fake.authorize(res["auth_url"])
    store.finish_oauth(state, code)
    saved = json.loads(vault["app:mail"])
    assert saved["client_id"] == "mine" and saved["client_secret"] == "shh" and saved["access_token"] == "tok-1"


def test_apps_without_sign_in_and_with_a_token(tmp_path, fake):
    store, vault = memory_store(tmp_path)
    fake.open_access = True
    res = store.connect(store.custom("Docs", fake.base + "/mcp"), "http://127.0.0.1:9/oauth/callback")
    assert res["connected"]["id"] == "docs" and "app:docs" not in vault
    fake.open_access = False
    fake.tokens.add("pat-123")
    res = store.connect(store.custom("Code", fake.base + "/mcp"), "x", token="pat-123")
    assert res["connected"]["id"] == "code" and json.loads(vault["app:code"]) == {"access_token": "pat-123"}
    with pytest.raises(McpError):
        store.connect(store.custom("Bad", fake.base + "/mcp"), "x", token="wrong")
    assert "app:bad" not in vault and "bad" not in store.connected()
    with pytest.raises(McpError):
        store.custom("Plain", "http://example.com/mcp")  # http only for this Mac
    names = {a["id"]: a["connected"] for a in store.catalog()}
    assert names["notion"] is False and names["docs"] is True and names["code"] is True


def test_tool_names_fit_the_api(tmp_path):
    store, _ = memory_store(tmp_path)
    t = AppTool(store, {"id": "cloudflare-docs", "name": "Cloudflare Docs"},
                {"name": "search.docs/v2 " + "x" * 80, "input_schema": {}})
    assert t.name.startswith("cloudflare_docs__search_docs_v2_") and len(t.name) == 64
    assert t.schema == {"type": "object", "properties": {}}


def test_the_window_connects_apps_and_bots_can_leave_them_out(tmp_path, fake, monkeypatch):
    import oomi.gui.server as srv

    vault = {}
    monkeypatch.setattr("oomi.keychain.get_secret", vault.get)
    monkeypatch.setattr("oomi.keychain.store_secret", lambda k, v: vault.__setitem__(k, v) or True)
    monkeypatch.setattr("oomi.keychain.delete_secret", lambda k: vault.pop(k, None))
    monkeypatch.setenv("OOMI_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "user"))  # what Path.home() reads on Windows
    (tmp_path / "user").mkdir()
    server = srv.Server(srv.App(home=tmp_path / "home", llm_factory=lambda key: None)).start()
    server.app.no_browser = True
    base = f"http://127.0.0.1:{server.port}"

    def call(method, path, body=None):
        req = urllib.request.Request(f"{base}/api/{path}", data=json.dumps(body).encode() if body is not None else None,
                                     method=method, headers={"X-Oomi-Token": server.app.token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    try:
        code, apps = call("GET", "apps")
        assert code == 200 and apps["connected"] == 0 and any(a["id"] == "notion" for a in apps["apps"])
        code, out = call("POST", "apps/custom", {"name": "Wiki", "url": fake.base + "/mcp"})
        assert code == 200 and out["auth_url"]
        state, auth_code, redirect = fake.authorize(out["auth_url"])
        assert redirect == f"{base}/oauth/callback"
        with urllib.request.urlopen(f"{redirect}?state={state}&code={auth_code}") as r:
            assert b"Connected" in r.read()
        code, apps = call("GET", "apps")
        assert apps["connected"] == 1 and next(a for a in apps["apps"] if a["id"] == "wiki")["tools"] == 2
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(f"{redirect}?state=forged&code=x")

        code, bot = call("POST", "bots", {"name": "Notes", "trust": "project"})
        code, st = call("PATCH", f"bots/{bot['id']}", {"apps_off": ["wiki"]})
        assert code == 200 and st["apps_off"] == ["wiki"]
        assert call("PATCH", f"bots/{bot['id']}", {"apps_off": "wiki"})[0] == 400
        assert call("POST", "apps/nope/connect", {})[0] == 400
        code, apps = call("POST", "apps/wiki/disconnect", {})
        assert code == 200 and apps["connected"] == 0 and not vault
    finally:
        server.stop()


def test_a_bot_asks_before_acting_in_an_app_and_then_reads_freely(env, fake):
    from conftest import resp, text, tool

    store, _ = memory_store(env.home)
    res = store.connect(store.custom("Wiki", fake.base + "/mcp"), "http://127.0.0.1:9/oauth/callback")
    store.finish_oauth(*fake.authorize(res["auth_url"])[:2])
    agent = env.make([resp(tool("wiki__search", {"q": "plan"})),
                      resp(tool("wiki__create-page", {"title": "Plan"})),
                      resp(text("done"))], answers=[("always", "")])
    agent.set_app_tools(store.tools())
    assert {"wiki__search", "wiki__create-page"} <= {d["name"] for d in agent.tool_defs}
    agent.run("make a plan page")
    assert [a["tool"] for a in env.ui.asked] == ["wiki__create-page"]
    assert "acts in Wiki" in env.ui.asked[0]["reason"]
    assert [c[1]["name"] for c in fake.calls] == ["search", "create-page"]
    assert agent.taint.active  # what the app sent back is untrusted
    agent.set_app_tools([])
    assert "wiki__search" not in agent.tools
