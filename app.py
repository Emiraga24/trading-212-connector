"""Single-owner Trading 212 bridge. OAuth + PKCE; orders require browser consent.

No broker keys or owner passwords are accepted through MCP tools.
"""
import base64
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template_string, request, session
from werkzeug.security import check_password_hash, generate_password_hash

SCOPE = "portfolio:read orders:prepare"
VERSIONS = {"2025-03-26", "2025-06-18", "2025-11-25"}
PAGE = """<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Trading 212 Verbindung</title>
<style>body{font:17px system-ui;background:#101820;color:#eff5f5;margin:0;padding:24px}
main{max-width:600px;margin:30px auto}input,button{font:inherit;box-sizing:border-box;padding:14px;width:100%;margin:8px 0;border-radius:8px}
button{background:#b4ef71;border:0;color:#17210e}dt{margin-top:16px;color:#aabbc6}dd{margin:4px 0}a{color:#b4ef71}.note{color:#c4ced5}code{overflow-wrap:anywhere}</style>
<main><h1>{{ title }}</h1><p>{{ text }}</p>
{% if order %}<dl><dt>Konto</dt><dd>{{ environment }}</dd><dt>Aktion</dt><dd>{{ 'KAUFEN' if order.side == 'buy' else 'VERKAUFEN' }}</dd>
<dt>Wertpapier</dt><dd>{{ order.name }} — {{ order.ticker }}</dd><dt>ISIN</dt><dd>{{ order.isin }}</dd>
<dt>Stückzahl (keine Geldsumme)</dt><dd>{{ order.quantity }}</dd><dt>Orderart</dt><dd>Marktorder, nur reguläre Handelszeiten</dd>
<dt>Instrumentwährung</dt><dd>{{ order.currency }}</dd></dl>
<p>Der Ausführungspreis ist nicht garantiert. Bei geschlossener Börse kann die Order bis zur Öffnung warten. Kein Stop-Loss wird mitgesendet.</p>{% endif %}
{% if form %}<form method="post"><input type="hidden" name="csrf" value="{{ csrf }}">
<input type="hidden" name="action" value="{{ action }}">
{% if password %}<label>Dein Verbindungs-Passwort (APP_PASSWORD)<input name="password" type="password" autocomplete="current-password" required maxlength="512"></label>{% endif %}
<button type="submit">{{ button }}</button></form>{% endif %}
{% if result %}<p><code>{{ result }}</code></p>{% endif %}
<p class="note">Privater Verbindungsdienst. Kein offizieller Dienst von Trading 212 oder OpenAI.</p></main></html>"""


class BrokerError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward broker credentials to redirected destinations.


class Broker:
    def __init__(self, key, secret, environment):
        self.base = {"live": "https://live.trading212.com/api/v0", "demo": "https://demo.trading212.com/api/v0"}[environment]
        self.auth = "Basic " + base64.b64encode((key + ":" + secret).encode()).decode()
        self.opener = urllib.request.build_opener(NoRedirect)
        self.cache = {}
        self.lock = threading.Lock()

    def call(self, path, payload=None):
        if path not in {"/equity/account/summary", "/equity/positions", "/equity/orders", "/equity/metadata/instruments", "/equity/orders/market"}:
            raise BrokerError("Nicht unterstützter API-Pfad.")
        if payload is not None and path != "/equity/orders/market":
            raise BrokerError("Nicht unterstützte Schreibaktion.")
        with self.lock:
            cached = self.cache.get(path)
            ttl = 3600 if path.endswith("instruments") else 6
            if payload is None and cached and time.time() - cached[0] < ttl:
                return cached[1]
            req = urllib.request.Request(self.base + path,
                data=json.dumps(payload, allow_nan=False).encode() if payload is not None else None,
                headers={"Authorization": self.auth, "Content-Type": "application/json", "Accept": "application/json"},
                method="POST" if payload is not None else "GET")
            try:
                with self.opener.open(req, timeout=20) as response:
                    result = json.loads(response.read(8_000_000))
            except urllib.error.HTTPError as exc:
                status = exc.code
                exc.close()
                raise BrokerError("Trading 212 meldet HTTP %s. Nicht automatisch wiederholen; Konto in der Trading-212-App prüfen." % status) from None
            except (OSError, ValueError, urllib.error.URLError):
                raise BrokerError("Antwort nicht sicher empfangen. Vor einer weiteren Order das Konto in der Trading-212-App prüfen.") from None
            if payload is None:
                self.cache[path] = (time.time(), result)
            else:
                self.cache.clear()
            return result


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def create_app(config=None, broker=None):
    cfg = dict(os.environ)
    cfg.update(config or {})
    base = cfg.get("PUBLIC_BASE_URL", cfg.get("RENDER_EXTERNAL_URL", "")).rstrip("/")
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username:
        raise RuntimeError("PUBLIC_BASE_URL oder RENDER_EXTERNAL_URL muss eine reine HTTPS-Serveradresse sein.")
    for name in ("APP_PASSWORD", "OAUTH_CLIENT_SECRET", "SESSION_SECRET"):
        if len(cfg.get(name, "")) < 32:
            raise RuntimeError(name + " benötigt mindestens 32 Zeichen.")
    if len({cfg[n] for n in ("APP_PASSWORD", "OAUTH_CLIENT_SECRET", "SESSION_SECRET")}) != 3:
        raise RuntimeError("Die drei Geheimnisse müssen unterschiedlich sein.")
    for name in ("T212_API_KEY", "T212_API_SECRET", "OAUTH_REDIRECT_URI"):
        if not cfg.get(name):
            raise RuntimeError(name + " fehlt.")
    callback = cfg["OAUTH_REDIRECT_URI"]
    cb = urllib.parse.urlsplit(callback)
    if cb.scheme != "https" or cb.hostname not in {"chatgpt.com", "chat.openai.com"} or cb.username or cb.fragment or cb.port not in (None, 443):
        raise RuntimeError("OAUTH_REDIRECT_URI muss die exakte HTTPS-Rückrufadresse von ChatGPT sein.")
    environment = cfg.get("T212_ENV", "live")
    if environment not in {"live", "demo"}:
        raise RuntimeError("T212_ENV muss live oder demo sein.")
    trading = cfg.get("ENABLE_TRADING", "false").lower() == "true"
    dbpath = cfg.get("STATE_DB", "/var/data/connector.sqlite")
    Path(dbpath).parent.mkdir(parents=True, exist_ok=True)
    app = Flask(__name__)
    app.config.update(SECRET_KEY=cfg["SESSION_SECRET"], MAX_CONTENT_LENGTH=32_768,
                      SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_NAME="__Host-t212", SESSION_COOKIE_PATH="/")
    password_hash = generate_password_hash(cfg["APP_PASSWORD"])
    client_id = "trading212-chatgpt"
    resource = base + "/mcp"
    epoch=digest("|".join(cfg[n] for n in ("APP_PASSWORD","OAUTH_CLIENT_SECRET","SESSION_SECRET","T212_API_KEY","T212_API_SECRET"))+"|"+environment)
    backend = broker or Broker(cfg["T212_API_KEY"], cfg["T212_API_SECRET"], environment)
    login_attempts = deque()
    login_lock = threading.Lock()

    def db():
        conn = sqlite3.connect(dbpath, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS records (kind TEXT NOT NULL, key TEXT NOT NULL, data TEXT NOT NULL,
            expires REAL NOT NULL, PRIMARY KEY(kind,key));
        CREATE TABLE IF NOT EXISTS tickets (id TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT NOT NULL,
            expires REAL NOT NULL, result TEXT);
        """)
    os.chmod(dbpath, 0o600)

    def put(kind, key, data, lifetime):
        data=dict(data,_epoch=epoch)
        with db() as conn:
            conn.execute("DELETE FROM records WHERE expires < ?", (time.time(),))
            conn.execute("INSERT INTO records VALUES (?,?,?,?)", (kind, digest(key), json.dumps(data), time.time()+lifetime))

    def get(kind, key, consume=False):
        with db() as conn:
            if consume:
                conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM records WHERE kind=? AND key=? AND expires>?", (kind,digest(key),time.time())).fetchone()
            if row and consume:
                conn.execute("DELETE FROM records WHERE kind=? AND key=?", (kind,digest(key)))
            data=json.loads(row["data"]) if row else None
            return data if data and data.get("_epoch")==epoch else None

    def csrf():
        token = secrets.token_urlsafe(32)
        session["csrf"] = token
        return token

    def check_csrf():
        expected = session.pop("csrf", "")
        if not expected or not hmac.compare_digest(expected, request.form.get("csrf", "")):
            abort(403)
        if request.headers.get("Origin") != base:
            abort(403)

    def check_password():
        with login_lock:
            while login_attempts and login_attempts[0] < time.time()-300:
                login_attempts.popleft()
            if len(login_attempts) >= 15:
                abort(429)
            login_attempts.append(time.time())
        return check_password_hash(password_hash, request.form.get("password", ""))

    def page(title, text, **kw):
        return render_template_string(PAGE, title=title, text=text, environment=environment.upper(), **kw)

    @app.before_request
    def guard_origin():
        origin = request.headers.get("Origin")
        if origin and origin not in {base, "https://chatgpt.com", "https://chat.openai.com"}:
            abort(403)

    @app.after_request
    def headers(response):
        response.headers.update({"Cache-Control":"no-store", "Pragma":"no-cache", "X-Content-Type-Options":"nosniff",
            "Referrer-Policy":"no-referrer", "X-Frame-Options":"DENY", "Strict-Transport-Security":"max-age=31536000",
            "Content-Security-Policy":"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"})
        return response

    @app.get("/")
    def home():
        return page("Trading-212-Verbindung", "Dienst erreichbar. Verbinde ChatGPT über die Serveradresse mit /mcp. Zugangsdaten werden ausschließlich in Render hinterlegt.")

    @app.get("/health")
    def health():
        return jsonify(status="ok", version="1.0.0")

    @app.get("/.well-known/oauth-protected-resource")
    @app.get("/.well-known/oauth-protected-resource/mcp")
    def resource_metadata():
        return jsonify(resource=resource, authorization_servers=[base], scopes_supported=SCOPE.split(), bearer_methods_supported=["header"])

    @app.get("/.well-known/oauth-authorization-server")
    def oauth_metadata():
        return jsonify(issuer=base, authorization_endpoint=base+"/authorize", token_endpoint=base+"/token",
            response_types_supported=["code"], grant_types_supported=["authorization_code", "refresh_token"],
            code_challenge_methods_supported=["S256"], scopes_supported=SCOPE.split(),
            token_endpoint_auth_methods_supported=["client_secret_post", "client_secret_basic"])

    @app.route("/authorize", methods=["GET", "POST"])
    def authorize():
        if request.method == "GET":
            q=request.args
            if (q.get("client_id") != client_id or q.get("redirect_uri") != callback or q.get("response_type") != "code"
                or q.get("code_challenge_method") != "S256" or not re.fullmatch(r"[A-Za-z0-9_-]{43}",q.get("code_challenge",""))
                or q.get("resource") != resource or not q.get("state") or len(q["state"])>2048):
                return jsonify(error="invalid_request"),400
            scopes=q.get("scope",SCOPE).split()
            if not scopes or not set(scopes)<=set(SCOPE.split()):
                return jsonify(error="invalid_scope"),400
            flow=secrets.token_urlsafe(32)
            put("flow",flow,dict(client_id=client_id, redirect_uri=callback, challenge=q["code_challenge"],
                state=q["state"],scope=" ".join(scopes),resource=resource),600)
            session["flow"]=flow
            return page("ChatGPT verbinden", "Du erlaubst ChatGPT, Kontodaten abzurufen und Orderentwürfe anzulegen. Jede echte Order bestätigst du separat in deinem Browser.",
                form=True,password=True,csrf=csrf(),action="authorize",button="Anmelden und Verbindung erlauben")
        check_csrf()
        if not check_password():
            return page("Anmeldung fehlgeschlagen", "Bitte die Verbindung in ChatGPT erneut öffnen."),403
        data=get("flow",session.pop("flow",""),consume=True)
        if not data:
            return jsonify(error="expired_request"),400
        code=secrets.token_urlsafe(32)
        put("code",code,data,120)
        separator="&" if "?" in callback else "?"
        return redirect(callback+separator+urllib.parse.urlencode({"code":code,"state":data["state"]}),303)

    def authenticated_client():
        auth=request.authorization
        if auth and auth.type.lower()=="basic":
            cid, secret=auth.username or "",auth.password or ""
        else:
            cid, secret=request.form.get("client_id",""),request.form.get("client_secret","")
        return cid==client_id and hmac.compare_digest(secret.encode(),cfg["OAUTH_CLIENT_SECRET"].encode())

    @app.post("/token")
    def token():
        if not authenticated_client():
            return jsonify(error="invalid_client"),401
        q=request.form
        if q.get("resource") not in (None,resource):
            return jsonify(error="invalid_target"),400
        grant=q.get("grant_type")
        if grant=="authorization_code":
            data=get("code",q.get("code",""),consume=True)
            verifier=q.get("code_verifier","")
            challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            if not data or q.get("redirect_uri")!=data["redirect_uri"] or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}",verifier) or not hmac.compare_digest(challenge,data["challenge"]):
                return jsonify(error="invalid_grant"),400
        elif grant=="refresh_token":
            data=get("refresh",q.get("refresh_token",""),consume=True)
            if not data:
                return jsonify(error="invalid_grant"),400
        else:
            return jsonify(error="unsupported_grant_type"),400
        data={"scope":data["scope"],"resource":resource,"client_id":client_id}
        access,refresh=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
        put("access",access,data,3600)
        put("refresh",refresh,data,7*86400)
        return jsonify(access_token=access,refresh_token=refresh,token_type="Bearer",expires_in=3600,scope=data["scope"])

    def tool(name,description,props=None,required=None,prepare=False):
        return {"name":name,"description":description,"inputSchema":{"type":"object","properties":props or {},"required":required or [],"additionalProperties":False},
            "annotations":{"readOnlyHint":not prepare,"destructiveHint":False,"idempotentHint":not prepare,"openWorldHint":True}}

    tools=[tool("account_summary","Liest Kontostand und Kontowährung."),tool("positions","Liest bestehende Positionen."),
        tool("pending_orders","Liest ausstehende Broker-Orders."),tool("search_instruments","Findet exakte Trading-212-Ticker nach Name, ISIN oder Ticker.",{"query":{"type":"string","minLength":2,"maxLength":80}},["query"]),
        tool("prepare_market_order","Erstellt nur einen Orderentwurf und einen Link. Der Kontoinhaber muss sich im Browser anmelden und die exakte Order persönlich bestätigen. Kein automatischer Auftrag.",
             {"ticker":{"type":"string"},"side":{"type":"string","enum":["buy","sell"]},"quantity":{"type":"number","exclusiveMinimum":0}},["ticker","side","quantity"],True)]

    def prepare(args):
        if not trading:
            raise ValueError("Orderfunktion ist deaktiviert. ENABLE_TRADING muss in Render bewusst auf true gesetzt werden.")
        ticker,side,qty=args.get("ticker"),args.get("side"),args.get("quantity")
        if not isinstance(ticker,str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,60}",ticker) or side not in {"buy","sell"}:
            raise ValueError("Exakten Trading-212-Ticker und buy/sell angeben.")
        if isinstance(qty,bool) or not isinstance(qty,(float,int)) or not math.isfinite(qty) or not 0<qty<=1_000_000:
            raise ValueError("Ungültige Stückzahl.")
        matches=[x for x in backend.call("/equity/metadata/instruments") if x.get("ticker")==ticker]
        if len(matches)!=1 or matches[0].get("type") not in {"STOCK","ETF"}:
            raise ValueError("Kein unterstütztes Aktien-/ETF-Instrument mit diesem exakten Ticker gefunden.")
        ins=matches[0]
        data=dict(ticker=ticker,side=side,quantity=qty,name=ins.get("name",ticker),isin=ins.get("isin",""),currency=ins.get("currencyCode",""),_epoch=epoch)
        ident=secrets.token_urlsafe(24)
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # A pending or uncertain identical order must not silently be duplicated.
            prior=conn.execute("SELECT data FROM tickets WHERE status IN ('sending','unknown') OR (status='pending' AND expires>?)",(time.time(),)).fetchall()
            if any(json.loads(p["data"])["ticker"]==ticker for p in prior):
                raise ValueError("Für diesen Ticker besteht ein offener oder ungeklärter Entwurf. Zuerst in Trading 212 prüfen bzw. den Entwurf ablaufen lassen.")
            conn.execute("INSERT INTO tickets VALUES (?,?,?, ?,NULL)",(ident,json.dumps(data),"pending",time.time()+600))
        return {"status":"awaiting_owner_confirmation","order":{k:v for k,v in data.items() if k!="_epoch"},"environment":environment,"expires_in_seconds":600,
            "confirmation_url":base+"/orders/"+ident,"notice":"Noch keine Broker-Order. Kontoinhaber muss selbst im Browser bestätigen."}

    @app.route("/mcp",methods=["POST","GET","DELETE"])
    def mcp():
        auth=request.headers.get("Authorization","")
        token_data=get("access",auth[7:]) if auth.startswith("Bearer ") else None
        if not token_data or token_data.get("resource")!=resource:
            response=jsonify(error="unauthorized")
            response.status_code=401
            response.headers["WWW-Authenticate"]='Bearer resource_metadata="'+base+'/.well-known/oauth-protected-resource/mcp", scope="'+SCOPE+'"'
            return response
        if request.method!="POST":
            return "",405
        msg=request.get_json(silent=True)
        if not isinstance(msg,dict) or msg.get("jsonrpc")!="2.0":
            return jsonify(error="invalid_request"),400
        ident=msg.get("id");method=msg.get("method");params=msg.get("params") or {}
        if not isinstance(params,dict):
            return jsonify(jsonrpc="2.0",id=ident,error={"code":-32602,"message":"Invalid params"})
        version=request.headers.get("MCP-Protocol-Version")
        if version and version not in VERSIONS:
            return jsonify(error="unsupported_protocol_version",supported=sorted(VERSIONS)),400
        if "id" not in msg:
            return "",202
        def reply(result):
            return jsonify(jsonrpc="2.0",id=ident,result=result)
        if method=="initialize":
            v=params.get("protocolVersion")
            return reply({"protocolVersion":v if v in VERSIONS else "2025-11-25","capabilities":{"tools":{"listChanged":False}},"serverInfo":{"name":"trading212-personal","version":"1.0.0"}})
        if method=="ping":
            return reply({})
        if method=="tools/list":
            return reply({"tools":tools})
        if method!="tools/call":
            return jsonify(jsonrpc="2.0",id=ident,error={"code":-32601,"message":"Method not found"})
        name=params.get("name");args=params.get("arguments") or {}
        try:
            definition=next((t for t in tools if t["name"]==name),None)
            if not definition or not isinstance(args,dict) or set(args)-set(definition["inputSchema"]["properties"]) or not set(definition["inputSchema"]["required"])<=set(args):
                raise ValueError("Unbekanntes Tool oder ungültige Argumente.")
            needed="orders:prepare" if name=="prepare_market_order" else "portfolio:read"
            if needed not in token_data["scope"].split():
                raise ValueError("Scope fehlt.")
            if name=="prepare_market_order":
                result=prepare(args)
            elif name=="search_instruments":
                q=args["query"]
                if not isinstance(q,str) or not 2<=len(q)<=80:
                    raise ValueError("Suchbegriff muss 2–80 Zeichen haben.")
                result=[x for x in backend.call("/equity/metadata/instruments") if q.lower() in json.dumps({k:x.get(k) for k in ['ticker','name','isin']}).lower()][:20]
            else:
                path={"account_summary":"/equity/account/summary","positions":"/equity/positions","pending_orders":"/equity/orders"}[name]
                result=backend.call(path)
            return reply({"content":[{"type":"text","text":json.dumps(result,ensure_ascii=False)}],"isError":False})
        except (ValueError,BrokerError) as exc:
            return reply({"content":[{"type":"text","text":str(exc)}],"isError":True})

    @app.route("/orders/<ident>",methods=["GET","POST"])
    def order_page(ident):
        with db() as conn:
            row=conn.execute("SELECT * FROM tickets WHERE id=?",(ident,)).fetchone()
        if not row:
            abort(404)
        if json.loads(row["data"]).get("_epoch")!=epoch:
            return page("Entwurf ungültig","Die Verbindungs- oder Kontoeinstellungen haben sich geändert. Dieser Entwurf kann nicht ausgeführt werden."),410
        if request.method=="GET":
            return page("Order ansehen", "Melde dich persönlich an, um den Entwurf zu prüfen. Das Öffnen dieses Links führt keinen Trade aus.",
                form=True,password=True,csrf=csrf(),action="view",button="Anmelden und Order prüfen")
        check_csrf()
        if request.form.get("action")=="view":
            if not check_password():
                return page("Anmeldung fehlgeschlagen","Öffne den Link erneut."),403
            if row["status"]!="pending" or row["expires"]<=time.time():
                return page("Orderstatus", "Status: "+row["status"]+". Bei Unklarheit direkt in Trading 212 prüfen; nicht erneut absenden.")
            session["order_auth"]={"id":ident,"expires":time.time()+120}
            return page("Order verbindlich bestätigen", "Prüfe Konto, Richtung und Stückzahl. Dieser Klick sendet genau diese Marktorder an Trading 212.",
                order=json.loads(row["data"]),form=True,password=False,csrf=csrf(),action="confirm",button="Diese Order jetzt an Trading 212 senden")
        approval=session.pop("order_auth",{})
        if request.form.get("action")!="confirm" or approval.get("id")!=ident or approval.get("expires",0)<=time.time() or not trading:
            abort(403)
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            claimed=conn.execute("UPDATE tickets SET status='sending' WHERE id=? AND status='pending' AND expires>?",(ident,time.time())).rowcount
        if claimed!=1:
            return page("Nicht erneut gesendet","Der Entwurf wurde bereits verarbeitet oder ist abgelaufen. Prüfe Trading 212."),409
        data=json.loads(row["data"])
        payload={"ticker":data["ticker"],"quantity":data["quantity"]*(1 if data["side"]=="buy" else -1),"extendedHours":False}
        try:
            result=backend.call("/equity/orders/market",payload)
            status="submitted"
            message="Broker hat geantwortet. Prüfe den Ausführungsstatus in Trading 212; angenommen bedeutet noch nicht ausgeführt."
            safe_result={k:result.get(k) for k in ("id","status","ticker","quantity","filledQuantity")}
        except BrokerError as exc:
            status="unknown";message=str(exc);safe_result={"message":message}
        with db() as conn:
            conn.execute("UPDATE tickets SET status=?,result=? WHERE id=?",(status,json.dumps(safe_result),ident))
        return page("Orderstatus",message,result=json.dumps(safe_result,ensure_ascii=False))

    return app
