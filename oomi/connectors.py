"""Connected apps: remote MCP servers (Notion, Linear, GitHub, Sentry…) whose tools the bots can use.

* oomi talks MCP itself (Streamable HTTP), so every call to an app goes through the same approval rules as
  any other tool: tools an app marks read-only just run; anything else asks first ("Always allow" works per
  tool). What an app returns counts as untrusted content, like a web page.
* Signing in follows the MCP authorization spec: discover the app's OAuth server, register oomi as a client,
  open the browser with PKCE, and catch the redirect on the app's own local server. Apps that take a token
  instead (GitHub's personal access tokens, say) get a paste box.
* Tokens live in the Keychain. What's connected, and each app's tool list, live in ~/.oomi/connectors.json,
  so starting a bot never waits on the network.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlencode, urlparse

from . import __version__, keychain
from .memory import http_module
from .safety import READ, WRITE, Assessment
from .tools import Tool, ToolResult

PROTOCOL = "2025-06-18"
TIMEOUT = 60

# Apps with a hosted MCP server. auth: "oauth" (sign in in the browser), "token" (paste one), "none", "client"
# (sign in in the browser with an OAuth client the user made themselves, because the app has no automatic
# registration; its "oauth" entry pins the endpoints, scopes and extra parameters to use).
CATALOG = [
    {"id": "notion", "name": "Notion", "category": "Productivity", "auth": "oauth", "url": "https://mcp.notion.com/mcp",
     "desc": "Search, read and update Notion pages and databases", "color": "#111111"},
    {"id": "linear", "name": "Linear", "category": "Productivity", "auth": "oauth", "url": "https://mcp.linear.app/mcp",
     "desc": "Find, create and update Linear issues and projects", "color": "#5e6ad2"},
    {"id": "atlassian", "name": "Atlassian", "category": "Productivity", "auth": "oauth",
     "url": "https://mcp.atlassian.com/v1/mcp", "desc": "Jira issues and Confluence pages", "color": "#1868db"},
    {"id": "gmail", "name": "Gmail", "category": "Communication", "auth": "client",
     "url": "https://gmailmcp.googleapis.com/mcp/v1", "desc": "Search and read email, and write drafts, in Gmail", "color": "#ea4335",
     "oauth": {"authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
               "token_endpoint": "https://oauth2.googleapis.com/token",
               "scope": "https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.compose",
               "extra": {"access_type": "offline", "prompt": "consent"}},
     "client_help": "Google doesn't let oomi sign in on its own, so Gmail uses an OAuth client of yours. In Google Cloud "
                    "(a project in the Google Workspace Developer Preview Program, with the Gmail API and Gmail MCP API "
                    "turned on), create an OAuth client ID of type Web application, add the redirect address below to it, "
                    "then paste its client ID and secret here."},
    {"id": "intercom", "name": "Intercom", "category": "Communication", "auth": "oauth",
     "url": "https://mcp.intercom.com/mcp", "desc": "Search conversations and contacts in Intercom", "color": "#1f8ded"},
    {"id": "github", "name": "GitHub", "category": "Code", "auth": "token", "url": "https://api.githubcopilot.com/mcp/",
     "desc": "Repositories, issues, pull requests and Actions", "color": "#24292f",
     "token_help": "Create a fine-grained personal access token at github.com/settings/personal-access-tokens "
                   "with access to the repositories you want, then paste it here."},
    {"id": "sentry", "name": "Sentry", "category": "Code", "auth": "oauth", "url": "https://mcp.sentry.dev/mcp",
     "desc": "Look into errors, issues and releases in Sentry", "color": "#362d59"},
    {"id": "vercel", "name": "Vercel", "category": "Code", "auth": "oauth", "url": "https://mcp.vercel.com",
     "desc": "Projects, deployments and build logs on Vercel", "color": "#000000"},
    {"id": "supabase", "name": "Supabase", "category": "Code", "auth": "oauth", "url": "https://mcp.supabase.com/mcp",
     "desc": "Supabase projects, tables, SQL and logs", "color": "#3ecf8e"},
    {"id": "cloudflare", "name": "Cloudflare", "category": "Code", "auth": "oauth",
     "url": "https://bindings.mcp.cloudflare.com/mcp", "desc": "Workers, KV, R2 and D1 on Cloudflare", "color": "#f38020"},
    {"id": "neon", "name": "Neon", "category": "Code", "auth": "oauth", "url": "https://mcp.neon.tech/mcp",
     "desc": "Neon Postgres projects, branches and queries", "color": "#00e599"},
    {"id": "huggingface", "name": "Hugging Face", "category": "Code", "auth": "oauth", "url": "https://huggingface.co/mcp",
     "desc": "Search models, datasets, Spaces and papers", "color": "#ffd21e"},
    {"id": "figma", "name": "Figma", "category": "Design", "auth": "none", "url": "http://127.0.0.1:3845/mcp",
     "desc": "Read designs from the Figma desktop app (turn on its MCP server first)", "color": "#a259ff"},
    {"id": "canva", "name": "Canva", "category": "Design", "auth": "oauth", "url": "https://mcp.canva.com/mcp",
     "desc": "Find, create and edit Canva designs", "color": "#00c4cc"},
    {"id": "stripe", "name": "Stripe", "category": "Payments", "auth": "oauth", "url": "https://mcp.stripe.com",
     "desc": "Customers, payments, invoices and Stripe docs", "color": "#635bff"},
    {"id": "paypal", "name": "PayPal", "category": "Payments", "auth": "oauth", "url": "https://mcp.paypal.com/mcp",
     "desc": "Invoices, orders and transactions on PayPal", "color": "#003087"},
    {"id": "posthog", "name": "PostHog", "category": "Data", "auth": "oauth", "url": "https://mcp.posthog.com/mcp",
     "desc": "Product analytics, insights and feature flags", "color": "#f54e00"},
    {"id": "context7", "name": "Context7", "category": "Research", "auth": "none", "url": "https://mcp.context7.com/mcp",
     "desc": "Up-to-date docs and code examples for libraries", "color": "#059669"},
    {"id": "deepwiki", "name": "DeepWiki", "category": "Research", "auth": "none", "url": "https://mcp.deepwiki.com/mcp",
     "desc": "Ask questions about any public GitHub repository", "color": "#2563eb"},
    {"id": "exa", "name": "Exa", "category": "Research", "auth": "none", "url": "https://mcp.exa.ai/mcp",
     "desc": "Web and code search built for AI", "color": "#1f40ed"},
    {"id": "cloudflare-docs", "name": "Cloudflare Docs", "category": "Research", "auth": "none",
     "url": "https://docs.mcp.cloudflare.com/mcp", "desc": "Search Cloudflare's developer documentation", "color": "#f38020"},
]
CATEGORIES = ["Productivity", "Communication", "Code", "Design", "Payments", "Data", "Research"]
ID_RX = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")


class McpError(Exception):
    pass


class AuthNeeded(McpError):
    """The server answered 401: sign in (again). `challenge` is its WWW-Authenticate header."""

    def __init__(self, challenge: str = ""):
        super().__init__("The app asked oomi to sign in again.")
        self.challenge = challenge


class SessionGone(McpError):
    pass


# ---------------------------------------------------------------------------------------------------------
# MCP over Streamable HTTP

class McpClient:
    """Just enough MCP for tools: initialize, tools/list, tools/call. One request at a time per client."""

    def __init__(self, url: str, token: Callable[[], Optional[str]] = lambda: None, http=None):
        self.url, self.token = url, token
        self.http = http or http_module().Client(timeout=TIMEOUT, follow_redirects=True)
        self.session_id: Optional[str] = None
        self.ready = False
        self.seq = 0
        self.lock = threading.Lock()

    def _post(self, msg: dict) -> Optional[dict]:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.ready:
            headers["MCP-Protocol-Version"] = PROTOCOL
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        tok = self.token()
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        try:
            with self.http.stream("POST", self.url, json=msg, headers=headers) as r:
                if r.status_code == 401:
                    raise AuthNeeded(r.headers.get("www-authenticate", ""))
                if r.status_code == 404 and self.session_id:
                    raise SessionGone()
                if r.status_code == 202:
                    return None
                if r.status_code >= 400:
                    r.read()
                    raise McpError(f"The app answered {r.status_code}: {r.text[:300].strip()}")
                if r.headers.get("mcp-session-id"):
                    self.session_id = r.headers["mcp-session-id"]
                if "id" not in msg:
                    return None
                if "text/event-stream" in r.headers.get("content-type", ""):
                    return self._from_stream(r.iter_lines(), msg["id"])
                r.read()
                return r.json()
        except McpError:
            raise
        except Exception as e:  # network, TLS, bad JSON
            raise McpError(f"Couldn't reach the app ({type(e).__name__}: {e}).") from e

    @staticmethod
    def _from_stream(lines, want) -> dict:
        data: list = []
        for line in lines:
            if line.startswith("data:"):
                data.append(line[5:].lstrip())
            elif line == "" and data:
                try:
                    msg = json.loads("\n".join(data))
                except json.JSONDecodeError:
                    msg = None
                data = []
                if isinstance(msg, dict) and msg.get("id") == want and ("result" in msg or "error" in msg):
                    return msg
        if data:
            msg = json.loads("\n".join(data))
            if isinstance(msg, dict) and msg.get("id") == want:
                return msg
        raise McpError("The app closed the connection without answering.")

    def _request(self, method: str, params: dict) -> dict:
        self.seq += 1
        resp = self._post({"jsonrpc": "2.0", "id": self.seq, "method": method, "params": params})
        if not isinstance(resp, dict):
            raise McpError("The app sent no answer.")
        if "error" in resp:
            err = resp["error"] or {}
            raise McpError(str(err.get("message") or err)[:500])
        return resp.get("result") or {}

    def _init(self) -> None:
        self.session_id, self.ready = None, False
        self._request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                     "clientInfo": {"name": "oomi", "version": __version__}})
        self.ready = True
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def request(self, method: str, params: dict) -> dict:
        with self.lock:
            if not self.ready:
                self._init()
            try:
                return self._request(method, params)
            except SessionGone:
                self._init()
                return self._request(method, params)

    def list_tools(self) -> list:
        out, cursor = [], None
        for _ in range(20):
            res = self.request("tools/list", {"cursor": cursor} if cursor else {})
            out += [t for t in res.get("tools", []) if isinstance(t, dict) and t.get("name")]
            cursor = res.get("nextCursor")
            if not cursor:
                break
        return out

    def call_tool(self, name: str, args: dict) -> dict:
        return self.request("tools/call", {"name": name, "arguments": args})

    def reset(self) -> None:
        with self.lock:
            self.ready, self.session_id = False, None


# ---------------------------------------------------------------------------------------------------------
# OAuth (MCP authorization: protected-resource metadata, authorization-server metadata, dynamic registration,
# authorization code with PKCE)

def _get_json(http, url: str) -> Optional[dict]:
    try:
        r = http.get(url, headers={"Accept": "application/json", "MCP-Protocol-Version": PROTOCOL})
        if r.status_code == 200:
            d = r.json()
            return d if isinstance(d, dict) else None
    except Exception:  # noqa: BLE001
        return None
    return None


def _well_known(url: str, name: str) -> list:
    u = urlparse(url)
    origin, path = f"{u.scheme}://{u.netloc}", u.path.rstrip("/")
    return [f"{origin}/.well-known/{name}{path}"] + ([f"{origin}/.well-known/{name}"] if path else [])


def discover(http, url: str, challenge: str = "") -> dict:
    """The authorization server's endpoints for an MCP server, plus the scopes it asks for."""
    m = re.search(r'resource_metadata="([^"]+)"', challenge or "")
    prm = None
    for cand in ([m.group(1)] if m else []) + _well_known(url, "oauth-protected-resource"):
        prm = _get_json(http, cand)
        if prm:
            break
    servers = (prm or {}).get("authorization_servers") or []
    issuer = servers[0] if servers else f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    meta = None
    for cand in _well_known(issuer, "oauth-authorization-server") + _well_known(issuer, "openid-configuration"):
        meta = _get_json(http, cand)
        if meta and meta.get("authorization_endpoint") and meta.get("token_endpoint"):
            break
        meta = None
    if not meta:  # servers from before the metadata documents: the defaults the 2025-03-26 spec named
        base = issuer.rstrip("/")
        meta = {"authorization_endpoint": base + "/authorize", "token_endpoint": base + "/token",
                "registration_endpoint": base + "/register"}
    scopes = (prm or {}).get("scopes_supported") or []
    return {"authorization_endpoint": meta["authorization_endpoint"], "token_endpoint": meta["token_endpoint"],
            "registration_endpoint": meta.get("registration_endpoint"), "scope": " ".join(scopes),
            "resource": (prm or {}).get("resource") or url}


def register(http, meta: dict, redirect_uri: str) -> dict:
    if not meta.get("registration_endpoint"):
        raise McpError("This app doesn't let new apps sign in on their own. Try a token, if it offers one.")
    r = http.post(meta["registration_endpoint"], json={
        "client_name": "oomi", "redirect_uris": [redirect_uri], "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"], "token_endpoint_auth_method": "none"})
    if r.status_code not in (200, 201):
        raise McpError(f"The app refused to register oomi ({r.status_code}): {r.text[:200]}")
    d = r.json()
    return {"client_id": d["client_id"], "client_secret": d.get("client_secret")}


def _token_request(http, endpoint: str, form: dict) -> dict:
    r = http.post(endpoint, data=form, headers={"Accept": "application/json"})
    if r.status_code != 200:
        raise McpError(f"Signing in failed ({r.status_code}): {r.text[:200]}")
    d = r.json()
    if not d.get("access_token"):
        raise McpError("The app didn't send an access token.")
    return d


@dataclass
class Pending:
    app: dict
    meta: dict
    client: dict
    verifier: str
    redirect_uri: str
    started: float


# ---------------------------------------------------------------------------------------------------------
# What's connected

class ConnectorStore:
    """connectors.json + Keychain tokens + one MCP client per app."""

    def __init__(self, home: Path, secrets_get=None, secrets_put=None, secrets_del=None, http=None):
        self.path = Path(home) / "connectors.json"
        self.get_secret = secrets_get or keychain.get_secret
        self.put_secret = secrets_put or keychain.store_secret
        self.del_secret = secrets_del or keychain.delete_secret
        self.http = http
        self.pending: dict = {}
        self.clients: dict = {}
        self.lock = threading.RLock()

    def _http(self):
        if self.http is None:
            self.http = http_module().Client(timeout=TIMEOUT, follow_redirects=True)
        return self.http

    # -- state ---------------------------------------------------------------------------------------------
    def load(self) -> dict:
        try:
            d = json.loads(self.path.read_text())
            return d if isinstance(d, dict) and isinstance(d.get("apps"), dict) else {"apps": {}}
        except (OSError, json.JSONDecodeError):
            return {"apps": {}}

    def save(self, d: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=1))
        tmp.replace(self.path)

    def connected(self) -> dict:
        return self.load()["apps"]

    def catalog(self) -> list:
        apps = self.connected()
        out = []
        for c in CATALOG:
            a = apps.get(c["id"])
            out.append({**{k: v for k, v in c.items() if k != "oauth"}, "connected": bool(a),
                        "tools": len(a["tools"]) if a else 0})
        for a in apps.values():
            if a.get("custom"):
                out.append({k: a.get(k) for k in ("id", "name", "category", "auth", "url", "desc", "color", "custom")}
                           | {"connected": True, "tools": len(a["tools"])})
        return out

    # -- tokens ----------------------------------------------------------------------------------------------
    def _secret(self, app_id: str) -> dict:
        raw = self.get_secret(f"app:{app_id}")
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}

    def _token(self, app_id: str) -> Optional[str]:
        s = self._secret(app_id)
        if s.get("expires_at") and s["expires_at"] - 60 < time.time() and s.get("refresh_token"):
            s = self._refresh(app_id, s) or s
        return s.get("access_token")

    def _refresh(self, app_id: str, s: dict) -> Optional[dict]:
        if not (s.get("refresh_token") and s.get("token_endpoint")):
            return None
        form = {"grant_type": "refresh_token", "refresh_token": s["refresh_token"], "client_id": s["client_id"]}
        if s.get("client_secret"):
            form["client_secret"] = s["client_secret"]
        if s.get("resource"):
            form["resource"] = s["resource"]
        try:
            d = _token_request(self._http(), s["token_endpoint"], form)
        except McpError:
            return None
        s = {**s, "access_token": d["access_token"], "refresh_token": d.get("refresh_token") or s["refresh_token"],
             "expires_at": time.time() + float(d["expires_in"]) if d.get("expires_in") else None}
        self.put_secret(f"app:{app_id}", json.dumps(s))
        return s

    def client(self, app_id: str) -> McpClient:
        with self.lock:
            c = self.clients.get(app_id)
            app = self.connected().get(app_id)
            if not app:
                raise McpError("That app isn't connected any more.")
            if c is None or c.url != app["url"]:
                c = self.clients[app_id] = McpClient(app["url"], lambda: self._token(app_id), self._http())
            return c

    def call(self, app_id: str, tool: str, args: dict) -> dict:
        c = self.client(app_id)
        try:
            return c.call_tool(tool, args)
        except AuthNeeded:
            s = self._secret(app_id)
            if not self._refresh(app_id, s):
                raise McpError("The app's sign-in has expired. Reconnect it in Settings → Apps.")
            c.reset()
            return c.call_tool(tool, args)

    # -- connecting --------------------------------------------------------------------------------------------
    def find(self, app_id: str) -> dict:
        for c in CATALOG:
            if c["id"] == app_id:
                return dict(c)
        a = self.connected().get(app_id)
        if a and a.get("custom"):
            return {k: a[k] for k in ("id", "name", "category", "auth", "url", "desc", "color", "custom") if k in a}
        raise McpError(f"No app called {app_id}.")

    def custom(self, name: str, url: str) -> dict:
        name = " ".join(str(name or "").split())[:40]
        u = urlparse(str(url or "").strip())
        local = u.hostname in ("127.0.0.1", "localhost")
        if not name or u.scheme not in ("https", "http") or (u.scheme == "http" and not local) or not u.netloc:
            raise McpError("Give it a name and the server's https:// address (http only for this Mac).")
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "app"
        app_id, n = base, 2
        known = {c["id"] for c in CATALOG} | set(self.connected())
        while app_id in known:
            app_id, n = f"{base}-{n}", n + 1
        return {"id": app_id, "name": name, "category": "Custom", "auth": "oauth", "url": u.geturl(),
                "desc": u.netloc, "color": "#555555", "custom": True}

    def connect(self, app: dict, redirect_uri: str, token: str = "", client: Optional[dict] = None) -> dict:
        """Try the app. Returns {"connected": app} when it works now, or {"auth_url": …} to sign in first.
        client: {"client_id", "client_secret"} for an app that signs in with the user's own OAuth client."""
        if token:
            return {"connected": self._finish(app, {"access_token": token.strip()})}
        if app.get("auth") == "client":
            cid = str((client or {}).get("client_id") or "").strip()
            if not cid:
                raise McpError(f"{app['name']} needs the client ID of your OAuth client.")
            own = {"client_id": cid, "client_secret": str((client or {}).get("client_secret") or "").strip() or None}
            return {"auth_url": self._start_oauth(app, redirect_uri, "", own)}
        probe = McpClient(app["url"], http=self._http())
        try:
            probe._init()
        except AuthNeeded as e:
            if app.get("auth") == "token":
                raise McpError(f"{app['name']} needs a token.")
            return {"auth_url": self._start_oauth(app, redirect_uri, e.challenge)}
        return {"connected": self._finish(app, {})}

    def _start_oauth(self, app: dict, redirect_uri: str, challenge: str, client: Optional[dict] = None) -> str:
        http = self._http()
        meta = {"resource": "", **app["oauth"]} if app.get("oauth") else discover(http, app["url"], challenge)
        client = client or register(http, meta, redirect_uri)
        verifier = secrets.token_urlsafe(48)
        state = secrets.token_urlsafe(24)
        with self.lock:
            now = time.time()
            self.pending = {k: v for k, v in self.pending.items() if now - v.started < 900}
            self.pending[state] = Pending(app, meta, client, verifier, redirect_uri, now)
        q = {"response_type": "code", "client_id": client["client_id"], "redirect_uri": redirect_uri, "state": state,
             "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("="),
             "code_challenge_method": "S256"}
        if meta.get("resource"):
            q["resource"] = meta["resource"]
        if meta.get("scope"):
            q["scope"] = meta["scope"]
        q.update(meta.get("extra") or {})
        sep = "&" if "?" in meta["authorization_endpoint"] else "?"
        return meta["authorization_endpoint"] + sep + urlencode(q)

    def finish_oauth(self, state: str, code: str) -> dict:
        with self.lock:
            p = self.pending.pop(state, None)
        if p is None or time.time() - p.started > 900:
            raise McpError("That sign-in link is old or wasn't started here. Try connecting again.")
        form = {"grant_type": "authorization_code", "code": code, "redirect_uri": p.redirect_uri,
                "client_id": p.client["client_id"], "code_verifier": p.verifier}
        if p.meta.get("resource"):
            form["resource"] = p.meta["resource"]
        if p.client.get("client_secret"):
            form["client_secret"] = p.client["client_secret"]
        d = _token_request(self._http(), p.meta["token_endpoint"], form)
        return self._finish(p.app, {
            "access_token": d["access_token"], "refresh_token": d.get("refresh_token"),
            "expires_at": time.time() + float(d["expires_in"]) if d.get("expires_in") else None,
            "token_endpoint": p.meta["token_endpoint"], "resource": p.meta.get("resource") or "", **p.client})

    def _finish(self, app: dict, secret: dict) -> dict:
        """Store the token, list the app's tools (which proves it works) and remember them."""
        if secret:
            if not self.put_secret(f"app:{app['id']}", json.dumps(secret)):
                raise McpError("Couldn't save the sign-in to the Keychain.")
        c = McpClient(app["url"], lambda: secret.get("access_token"), self._http())
        try:
            tools = c.list_tools()
        except AuthNeeded:
            if secret:
                self.del_secret(f"app:{app['id']}")
            raise McpError(f"{app['name']} didn't accept that sign-in.")
        rec = {**{k: app.get(k) for k in ("id", "name", "category", "auth", "url", "desc", "color", "custom")},
               "connected_at": time.time(),
               "tools": [{"name": t["name"], "description": str(t.get("description") or "")[:1500],
                          "input_schema": t.get("inputSchema") if isinstance(t.get("inputSchema"), dict) else {},
                          "read_only": bool((t.get("annotations") or {}).get("readOnlyHint"))} for t in tools]}
        with self.lock:
            d = self.load()
            d["apps"][app["id"]] = rec
            self.save(d)
            self.clients.pop(app["id"], None)
        return rec

    def refresh_tools(self, app_id: str) -> dict:
        app = self.connected().get(app_id)
        if not app:
            raise McpError("That app isn't connected.")
        tools = self.client(app_id).list_tools()
        with self.lock:
            d = self.load()
            d["apps"][app_id]["tools"] = [{"name": t["name"], "description": str(t.get("description") or "")[:1500],
                                           "input_schema": t.get("inputSchema") if isinstance(t.get("inputSchema"), dict) else {},
                                           "read_only": bool((t.get("annotations") or {}).get("readOnlyHint"))} for t in tools]
            self.save(d)
            return d["apps"][app_id]

    def disconnect(self, app_id: str) -> bool:
        with self.lock:
            d = self.load()
            gone = d["apps"].pop(app_id, None)
            self.save(d)
            self.clients.pop(app_id, None)
        self.del_secret(f"app:{app_id}")
        return gone is not None

    # -- tools for a bot ---------------------------------------------------------------------------------------
    def tools(self, off=()) -> list:
        out, used = [], set()
        for app in self.connected().values():
            if app["id"] in off:
                continue
            for t in app["tools"]:
                tool = AppTool(self, app, t)
                if tool.name not in used:
                    used.add(tool.name)
                    out.append(tool)
        return out


class AppTool(Tool):
    """One tool of a connected app, as the model sees it: `<app>__<tool>`."""

    def __init__(self, store: ConnectorStore, app: dict, t: dict):
        self.store, self.app, self.remote = store, app, t["name"]
        prefix = app["id"].replace("-", "_") + "__"
        self.name = (prefix + re.sub(r"[^A-Za-z0-9_-]", "_", t["name"]))[:64]
        self.description = f"[{app['name']}] {t.get('description') or t['name']}"
        schema = dict(t.get("input_schema") or {})
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        self.schema = schema
        self.read_only = t.get("read_only", False)

    def summary(self, inp: dict) -> str:
        args = json.dumps(inp, ensure_ascii=False)
        return f"{self.app['name']} · {self.remote}" + (f" {args[:600]}" if inp else "")

    def assess(self, inp: dict, ctx) -> Assessment:
        key = f"app:{self.app['id']}:{self.remote}"
        if self.read_only and not ctx.taint.active:
            return Assessment(READ, self.summary(inp), approval_key=key)
        why = (f"sends data to {self.app['name']} while untrusted content is in the conversation"
               if self.read_only else f"acts in {self.app['name']} ({self.remote})")
        return Assessment(WRITE, self.summary(inp), approval_key=key, external=why)

    def run(self, inp: dict, ctx, approved: bool = False) -> ToolResult:
        try:
            res = self.store.call(self.app["id"], self.remote, inp)
        except McpError as e:
            return ToolResult(f"{self.app['name']}: {e}", True)
        parts = []
        for c in res.get("content") or []:
            kind = c.get("type")
            if kind == "text":
                parts.append(c.get("text", ""))
            elif kind == "resource":
                r = c.get("resource") or {}
                parts.append(r.get("text") or f"[resource {r.get('uri', '')}]")
            elif kind == "resource_link":
                parts.append(f"[link {c.get('name') or ''} {c.get('uri', '')}]")
            else:
                parts.append(f"[{kind} omitted]")
        if not any(p.strip() for p in parts) and res.get("structuredContent") is not None:
            parts.append(json.dumps(res["structuredContent"], ensure_ascii=False))
        return ToolResult("\n".join(parts) or "(no output)", bool(res.get("isError")),
                          taint=f"{self.app['name']} ({self.remote})")
