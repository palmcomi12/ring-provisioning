"""OnWork · Banco de provisión de teléfonos (Yealink + Grandstream DECT) para centralitas RingPBX/vPBX."""
import base64
import ipaddress
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from pydantic import BaseModel

import envfile  # noqa: F401  (carga el .env antes de leer la configuración)
import fulla
import llm
import vpbx
from generator import (account_cfg, bootstrap_cfg, features_cfg, final_tail, gs_base_defaults, gs_base_xml,
                       gs_handset_xml, parse_pvalues, security_cfg, waiting_cfg)
from models import MODELS, kind_of, model_from_ua

# ---------------- Configuración (variables de entorno / .env) ----------------
E = os.environ.get
DB_PATH = E("DB_PATH") or os.path.join(envfile.ROOT, "data", "prov.db")
# Datos iniciales opcionales: solo se cargan si la base de datos está vacía.
SEED_FILE = E("SEED_FILE") or os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "seed.json")
STATIC = os.path.join(os.path.dirname(__file__), "static")
GLOBAL_API_KEY = E("VPBX_API_KEY", "").strip()
PHONE_ADMIN_PASSWORD = E("PHONE_ADMIN_PASSWORD", "")
PHONE_USER_PASSWORD = E("PHONE_USER_PASSWORD", "")
GS_PROFILE_NAME = E("GS_PROFILE_NAME", "profile1")
PROV_TOKEN = E("PROV_TOKEN", "").strip()            # si está, la URL de provisión es /p/<token>/
PANEL_USER = E("PANEL_USER", "admin")
PANEL_PASSWORD = E("PANEL_PASSWORD", "")            # si está, el panel pide usuario/contraseña


def _cidrs(v):
    out = []
    for x in (v or "").replace(";", ",").split(","):
        x = x.strip()
        if x:
            out.append(ipaddress.ip_network(x, strict=False))
    return out


PROV_ALLOWED = _cidrs(E("PROV_ALLOWED_CIDRS"))      # vacío = cualquiera
PANEL_ALLOWED = _cidrs(E("PANEL_ALLOWED_CIDRS"))
TRUSTED_PROXIES = _cidrs(E("TRUSTED_PROXIES", "127.0.0.1/32,172.16.0.0/12"))

# Asistente RING (/ring): solo se activa si se definen los datos del hub OpenVPN.
RING_CFG = {
    "hubName": E("RING_HUB_NAME", "HUB").strip() or "HUB",
    "hubHost": E("RING_HUB_HOST", "").strip(),
    "hubIp": E("RING_HUB_IP", "").strip(),
    "tunNet": E("RING_TUN_NET", "").strip().rstrip("."),
    "mgmtIps": [x.strip() for x in E("RING_MGMT_IPS", "").replace(";", ",").split(",") if x.strip()],
    "monitorIp": E("RING_MONITOR_IP", "").strip(),
    "hubAcl": E("RING_HUB_ACL", "GESTION").strip() or "GESTION",
    "hubSnmpChain": E("RING_HUB_SNMP_CHAIN", "dstnat").strip() or "dstnat",
    "winboxBase": int(E("RING_WINBOX_BASE", "8300") or 8300),
    "snmpBase": int(E("RING_SNMP_BASE", "50000") or 50000),
}
RING_ENABLED = bool(RING_CFG["hubHost"] and RING_CFG["hubIp"] and RING_CFG["tunNet"])

app = FastAPI(title="OnWork provision bench", docs_url=None, redoc_url=None)

COMMON_RE = re.compile(r"^y0{9}\w*\.cfg$")
YL_RE = re.compile(r"^([0-9a-f]{12})\.cfg$")
GS_BASE_RE = re.compile(r"^cfg([0-9a-f]{12})(\.xml)?$")
GS_HS_RE = re.compile(r"^[a-z_\-]*?([0-9a-f]{10})\.xml$")
MAC_RE = re.compile(r"([0-9a-fA-F]{2}[:\-]?){5}[0-9a-fA-F]{2}")


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def norm_id(v: Optional[str]) -> str:
    return re.sub(r"[^0-9a-f]", "", (v or "").lower())


# ---------------- Base de datos ----------------
def db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


def _add_cols(c, table, cols):
    have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
    for col, ddl in cols:
        if col not in have:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


def init():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    with db() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS clients(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
              prov_url TEXT NOT NULL DEFAULT '', speed_dials TEXT NOT NULL DEFAULT '[]');
            CREATE TABLE IF NOT EXISTS phones(id INTEGER PRIMARY KEY,
              client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
              mac TEXT UNIQUE, ext TEXT NOT NULL DEFAULT '', name TEXT DEFAULT '', model TEXT DEFAULT 'T73U',
              config TEXT DEFAULT '', published INTEGER DEFAULT 0, status TEXT DEFAULT 'pendiente',
              last_seen TEXT, last_ip TEXT, user_agent TEXT);
            CREATE TABLE IF NOT EXISTS detected(mac TEXT PRIMARY KEY, model TEXT, user_agent TEXT, ip TEXT,
              last_seen TEXT, hits INTEGER DEFAULT 1);
            CREATE TABLE IF NOT EXISTS log(id INTEGER PRIMARY KEY, ts TEXT, ip TEXT, path TEXT, mac TEXT,
              ua TEXT, result TEXT);
        """)
        _add_cols(c, "clients", [("api_key", "TEXT NOT NULL DEFAULT ''"), ("sip_domain", "TEXT NOT NULL DEFAULT ''"),
                                 ("sip_port", "INTEGER NOT NULL DEFAULT 7060"), ("auto_assign", "INTEGER NOT NULL DEFAULT 0"),
                                 ("blf_all", "INTEGER NOT NULL DEFAULT 1"), ("blf_extra", "TEXT NOT NULL DEFAULT '[]'")])
        _add_cols(c, "phones", [("ipei", "TEXT"), ("base_id", "INTEGER"), ("reboot_done", "INTEGER NOT NULL DEFAULT 0")])
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS phones_ipei ON phones(ipei) WHERE ipei IS NOT NULL")
        empty = c.execute("SELECT COUNT(*) FROM clients").fetchone()[0] == 0
    if empty and os.path.exists(SEED_FILE):
        with open(SEED_FILE, encoding="utf-8") as f:
            import_data(json.load(f))


def upsert_client(c, name, **kw) -> int:
    row = c.execute("SELECT id FROM clients WHERE name=?", (name,)).fetchone()
    if row:
        cid = row[0]
    else:
        cid = c.execute("INSERT INTO clients(name) VALUES(?)", (name,)).lastrowid
    sets = {k: v for k, v in kw.items() if v not in (None, "")}
    for k in ("speed_dials", "blf_extra"):
        if k in sets and not isinstance(sets[k], str):
            sets[k] = json.dumps(sets[k], ensure_ascii=False)
    if sets:
        c.execute(f"UPDATE clients SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", (*sets.values(), cid))
    return cid


def import_data(data: dict) -> dict:
    """data: {clients:[{name, sip_domain?, speed_dials?, blf_extra?, phones:[{ext,name,model,base_hint?}]}]}"""
    created = 0
    with db() as c:
        for cl in data.get("clients", []):
            dom = (cl.get("sip_domain") or "").strip()
            same = c.execute("SELECT name FROM clients WHERE sip_domain=? AND sip_domain<>''", (dom,)).fetchone() if dom else None
            if same:  # misma centralita ya detectada por la clave API: se completa ese cliente
                cl = {**cl, "name": same["name"]}
            cid = upsert_client(c, cl["name"], prov_url=cl.get("prov_url"), sip_domain=cl.get("sip_domain"),
                                sip_port=cl.get("sip_port"), speed_dials=cl.get("speed_dials") or None,
                                blf_extra=cl.get("blf_extra") or None)
            bases = {}
            for p in cl.get("phones", []):
                k = kind_of(p.get("model"))
                if k == "gs_base":
                    hint = p.get("base_hint") or p.get("name") or "Base"
                    ex = c.execute("SELECT id FROM phones WHERE client_id=? AND model=? AND name=?",
                                   (cid, p["model"], p.get("name") or hint)).fetchone()
                    bases[hint] = ex[0] if ex else c.execute(
                        "INSERT INTO phones(client_id, ext, name, model) VALUES(?,?,?,?)",
                        (cid, "", p.get("name") or hint, p["model"])).lastrowid
                    created += 0 if ex else 1
            for p in cl.get("phones", []):
                k = kind_of(p.get("model"))
                if k == "gs_base" or not p.get("ext"):
                    continue
                if c.execute("SELECT 1 FROM phones WHERE client_id=? AND ext=?", (cid, str(p["ext"]))).fetchone():
                    continue
                base_id = None
                if k == "gs_handset":
                    base_id = bases.get(p.get("base_hint")) or (next(iter(bases.values())) if bases else None)
                c.execute("INSERT INTO phones(client_id, mac, ipei, ext, name, model, base_id) VALUES(?,?,?,?,?,?,?)",
                          (cid, norm_id(p.get("mac")) or None, norm_id(p.get("ipei")) or None, str(p["ext"]),
                           p.get("name", ""), p.get("model", "T73U"), base_id))
                created += 1
    with db() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM phones WHERE config='' OR config IS NULL")]
    for pid in ids:
        regenerate(pid)
    return {"creados": created}


def regenerate(phone_id: int) -> str:
    with db() as c:
        p = c.execute("SELECT * FROM phones WHERE id=?", (phone_id,)).fetchone()
        if not p:
            raise HTTPException(404, "teléfono no encontrado")
        cl = c.execute("SELECT * FROM clients WHERE id=?", (p["client_id"],)).fetchone()
        k = kind_of(p["model"])
        if k == "yealink":
            blf = []
            if cl["blf_all"]:
                blf = [dict(r) for r in c.execute(
                    "SELECT ext, name FROM phones WHERE client_id=? AND ext<>'' ORDER BY ext", (p["client_id"],))]
            for x in json.loads(cl["blf_extra"] or "[]"):
                if not any(b["ext"] == x.get("ext") for b in blf):
                    blf.append(x)
            cfg = features_cfg(dict(p), blf, json.loads(cl["speed_dials"] or "[]"),
                               MODELS.get((p["model"] or "").upper(), {}).get("linekeys", 0))
        elif k == "gs_base":
            d = gs_base_defaults(cl["sip_domain"] or "<dominio>", cl["sip_port"] or 7060, "", "")
            cfg = ("# Base DECT Grandstream. Valores que se envían por defecto (se pueden sobrescribir abajo):\n"
                   + "\n".join(f"#   {k2} = {v}" for k2, v in d.items())
                   + "\n#   P2/P196 = contraseñas admin/user del .env\n# Añade o sobrescribe P-values aquí (P123 = valor):\n")
        else:
            cfg = ("# Terminal DECT Grandstream: la base pide ipei_<IPEI>.xml y se le envía su línea SIP\n"
                   "# (extensión, nombre y contraseña desde la API del vPBX). Necesita IPEI y base asignadas.\n")
        c.execute("UPDATE phones SET config=? WHERE id=?", (cfg, phone_id))
    return cfg


init()


# ---------------- Seguridad: IPs permitidas, token y contraseña de panel ----------------
def client_ip(request: Request) -> str:
    host = request.client.host if request.client else ""
    try:
        hip = ipaddress.ip_address(host)
    except ValueError:
        return host
    if any(hip in n for n in TRUSTED_PROXIES):
        xff = [x.strip() for x in request.headers.get("x-forwarded-for", "").split(",") if x.strip()]
        for cand in reversed(xff):
            try:
                if not any(ipaddress.ip_address(cand) in n for n in TRUSTED_PROXIES):
                    return cand
            except ValueError:
                continue
        if request.headers.get("x-real-ip"):
            return request.headers["x-real-ip"]
    return host


TEMP_PROV = {}  # IP pública -> caducidad (epoch); excepciones temporales creadas desde /excepcion
OPEN_PANEL = ("/excepcion", "/api/excepcion")


def temp_ok(ip: str) -> bool:
    import time
    t = TEMP_PROV.get(ip)
    if t and t > time.time():
        return True
    TEMP_PROV.pop(ip, None)
    return False


def ip_allowed(ip: str, nets) -> bool:
    if not nets:
        return True
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(a in n for n in nets)


def is_panel(path: str) -> bool:
    return path in ("/", "/ui", "/ring") or path in OPEN_PANEL or path.startswith("/api/")


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    ip = client_ip(request)
    request.state.ip = ip
    if is_panel(path):
        if path in OPEN_PANEL:
            if not PANEL_PASSWORD:
                return PlainTextResponse("Forbidden", status_code=403)
        elif not ip_allowed(ip, PANEL_ALLOWED):
            return PlainTextResponse("Forbidden", status_code=403)
        if PANEL_PASSWORD:
            ok = False
            auth = request.headers.get("authorization", "")
            if auth.lower().startswith("basic "):
                try:
                    u, _, pw = base64.b64decode(auth[6:]).decode().partition(":")
                    ok = secrets.compare_digest(u, PANEL_USER) and secrets.compare_digest(pw, PANEL_PASSWORD)
                except Exception:
                    ok = False
            if not ok:
                return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="OnWork banco"'})
        return await call_next(request)
    # Provisión: fuera de la lista de IPs o sin token -> 404 silencioso
    if not ip_allowed(ip, PROV_ALLOWED) and not temp_ok(ip):
        _log(ip, path, "", request.headers.get("user-agent", ""), "bloqueado (IP)")
        return PlainTextResponse("Not Found", status_code=404)
    return await call_next(request)


# ---------------- Modelos API ----------------
class ClientIn(BaseModel):
    name: str
    prov_url: str = ""
    speed_dials: list[dict] = []
    sip_domain: str = ""
    sip_port: int = 7060
    api_key: Optional[str] = None
    auto_assign: bool = False
    blf_all: bool = True
    blf_extra: list[dict] = []


class PhoneIn(BaseModel):
    client_id: int
    ext: str = ""
    name: str = ""
    model: str = "T73U"
    mac: Optional[str] = None
    ipei: Optional[str] = None
    base_id: Optional[int] = None
    config: Optional[str] = None
    published: Optional[bool] = None


class AssignIn(BaseModel):
    phone_id: int


# ---------------- Panel ----------------
@app.get("/")
def root():
    return RedirectResponse("/ui")


@app.get("/ui")
def ui():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/ring")
def ring():
    if not RING_ENABLED:
        raise HTTPException(404, "Asistente RING sin configurar (variables RING_HUB_HOST, RING_HUB_IP y RING_TUN_NET)")
    with open(os.path.join(STATIC, "ring.html"), encoding="utf-8") as f:
        html = f.read()
    cfg = json.dumps(RING_CFG, ensure_ascii=False).replace("</", "<\\/")
    return Response(html.replace("/*RING_CFG*/{}", cfg, 1), media_type="text/html; charset=utf-8")


class ExcIn(BaseModel):
    ip: str = ""
    quitar: bool = False


@app.get("/excepcion")
def excepcion_page():
    return FileResponse(os.path.join(STATIC, "excepcion.html"))


@app.get("/api/excepcion")
def excepcion_list(request: Request):
    import time
    now_t = time.time()
    act = [{"ip": k, "min": int((v - now_t) / 60) + 1} for k, v in TEMP_PROV.items() if v > now_t]
    return {"tu_ip": request.state.ip, "activas": act}


@app.post("/api/excepcion")
def excepcion_add(body: ExcIn, request: Request):
    import time
    ip = (body.ip or request.state.ip).strip()
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        raise HTTPException(400, "IP no válida")
    if not a.is_global:
        raise HTTPException(400, "Pon la IP pública del sitio, no una privada")
    if body.quitar:
        TEMP_PROV.pop(ip, None)
        _log(request.state.ip, "/api/excepcion", ip, "", "excepción quitada")
    else:
        TEMP_PROV[ip] = time.time() + 3600
        _log(request.state.ip, "/api/excepcion", ip, "", "excepción 1 h")
    return excepcion_list(request)


@app.get("/api/info")
def info():
    return {"models": {k: v["label"] for k, v in MODELS.items()}, "llm": llm.available(), "now": now(), "ring": RING_ENABLED,
            "prov_path": f"/p/{'•' * 6}/" if PROV_TOKEN else "/",
            "seguridad": {"token_provision": bool(PROV_TOKEN), "ips_provision": [str(n) for n in PROV_ALLOWED],
                          "panel_con_password": bool(PANEL_PASSWORD), "ips_panel": [str(n) for n in PANEL_ALLOWED]},
            "global_api_key": bool(GLOBAL_API_KEY)}


@app.get("/api/clients")
def list_clients():
    with db() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM clients ORDER BY name")]
    for r in rows:
        r["speed_dials"] = json.loads(r["speed_dials"] or "[]")
        r["blf_extra"] = json.loads(r["blf_extra"] or "[]")
        r["has_api_key"] = bool(r.pop("api_key", ""))
        r["global_api_key"] = bool(GLOBAL_API_KEY)
    return rows


@app.post("/api/clients")
def create_client(body: ClientIn):
    with db() as c:
        cid = c.execute("INSERT INTO clients(name, prov_url, sip_domain, sip_port) VALUES(?,?,?,?)",
                        (body.name, body.prov_url, body.sip_domain, body.sip_port)).lastrowid
    update_client(cid, body)
    return {"id": cid}


@app.put("/api/clients/{cid}")
def update_client(cid: int, body: ClientIn):
    with db() as c:
        c.execute("UPDATE clients SET name=?, prov_url=?, speed_dials=?, sip_domain=?, sip_port=?, auto_assign=?, "
                  "blf_all=?, blf_extra=? WHERE id=?",
                  (body.name, body.prov_url, json.dumps(body.speed_dials, ensure_ascii=False), body.sip_domain.strip(),
                   body.sip_port, int(body.auto_assign), int(body.blf_all),
                   json.dumps(body.blf_extra, ensure_ascii=False), cid))
        if body.auto_assign:
            c.execute("UPDATE clients SET auto_assign=0 WHERE id<>?", (cid,))
        if body.api_key:
            c.execute("UPDATE clients SET api_key=? WHERE id=?",
                      ("" if body.api_key.strip() == "-" else body.api_key.strip(), cid))
    return {"ok": True}


@app.delete("/api/clients/{cid}")
def delete_client(cid: int):
    with db() as c:
        c.execute("DELETE FROM clients WHERE id=?", (cid,))
    return {"ok": True}


def _key_for(cid: int) -> str:
    with db() as c:
        cl = c.execute("SELECT api_key FROM clients WHERE id=?", (cid,)).fetchone()
    return (cl["api_key"] if cl else "") or GLOBAL_API_KEY


@app.get("/api/clients/{cid}/apitest")
def api_test(cid: int, ext: str):
    key = _key_for(cid)
    if not key:
        raise HTTPException(400, "No hay clave API (ni del cliente ni global)")
    try:
        return vpbx.diagnose(key, ext)
    except vpbx.VpbxError as e:
        raise HTTPException(502, str(e))


@app.post("/api/clients/{cid}/import-vpbx")
def import_vpbx(cid: int, model: str = "T73U"):
    """Crea un teléfono por cada extensión SIP de la centralita que aún no exista."""
    key = _key_for(cid)
    if not key:
        raise HTTPException(400, "No hay clave API para este cliente")
    try:
        exts = vpbx.list_extensions(key)
    except vpbx.VpbxError as e:
        raise HTTPException(502, str(e))
    created = 0
    with db() as c:
        cl = c.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone()
        if exts and not cl["sip_domain"] and exts[0]["domain"]:
            c.execute("UPDATE clients SET sip_domain=? WHERE id=?", (exts[0]["domain"], cid))
        for e in exts:
            if e["type"] and e["type"].upper() != "SIP":
                continue
            row = c.execute("SELECT id, name FROM phones WHERE client_id=? AND ext=?", (cid, e["ext"])).fetchone()
            if row:
                if not row["name"] and e["name"]:
                    c.execute("UPDATE phones SET name=? WHERE id=?", (e["name"], row["id"]))
                continue
            c.execute("INSERT INTO phones(client_id, ext, name, model) VALUES(?,?,?,?)", (cid, e["ext"], e["name"], model))
            created += 1
    for pid in [r[0] for r in db().execute("SELECT id FROM phones WHERE client_id=?", (cid,))]:
        regenerate(pid)
    return {"extensiones": len(exts), "creados": created}


class DiscoverIn(BaseModel):
    api_key: str
    model: str = "T73U"


@app.post("/api/vpbx/discover")
def vpbx_discover(body: DiscoverIn):
    """Pegar la clave API: detecta la centralita (dominio), crea o reutiliza el cliente y trae sus extensiones."""
    key = body.api_key.strip()
    if not key:
        raise HTTPException(400, "Falta la clave API")
    try:
        exts = vpbx.list_extensions(key)
    except vpbx.VpbxError as e:
        raise HTTPException(502, f"La clave no funciona: {e}")
    if not exts:
        raise HTTPException(404, "La centralita no tiene extensiones")
    doms = [e["domain"] for e in exts if e["domain"]]
    domain = max(set(doms), key=doms.count) if doms else ""
    with db() as c:
        row = c.execute("SELECT id, name FROM clients WHERE sip_domain=? AND sip_domain<>''", (domain,)).fetchone()
        if row:
            cid, name = row["id"], row["name"]
        else:
            base = name = (domain.split(".")[0] if domain else "Centralita").upper()
            i = 2
            while c.execute("SELECT 1 FROM clients WHERE name=?", (name,)).fetchone():
                name, i = f"{base} ({i})", i + 1
            cid = c.execute("INSERT INTO clients(name, sip_domain) VALUES(?,?)", (name, domain)).lastrowid
        c.execute("UPDATE clients SET api_key=? WHERE id=?", (key, cid))
    r = import_vpbx(cid, body.model)
    return {"id": cid, "name": name, "domain": domain, "creados": r["creados"],
            "extensiones": [{"ext": e["ext"], "name": e["name"], "type": e["type"]} for e in exts]}


@app.post("/api/import/fulla")
async def import_fulla(file: UploadFile = File(...), ai: bool = Form(False), hint: str = Form("")):
    """Lee la fulla de treball y devuelve una propuesta (no guarda nada)."""
    data = await file.read()
    try:
        res = fulla.parse(data)
    except Exception as e:
        raise HTTPException(400, f"No puedo leer el Excel: {e}")
    if ai:
        if not llm.available():
            raise HTTPException(400, "La IA no está configurada (LLM_PROVIDER en .env)")
        try:
            res = {**llm.interpret(fulla.grid_text(fulla.load_grid(data)), hint), "ambiguous": False}
        except Exception as e:
            raise HTTPException(502, f"Error de la IA: {e}")
    res["llm_disponible"] = llm.available()
    return res


@app.post("/api/import")
def api_import(data: dict):
    return import_data(data)


@app.get("/api/phones")
def list_phones(client_id: Optional[int] = None):
    q, a = "SELECT * FROM phones", ()
    if client_id:
        q, a = q + " WHERE client_id=?", (client_id,)
    with db() as c:
        rows = [dict(r) for r in c.execute(q + " ORDER BY (ext=''), ext, id", a)]
    for r in rows:
        r["kind"] = kind_of(r["model"])
    return rows


@app.post("/api/phones")
def create_phone(body: PhoneIn):
    with db() as c:
        pid = c.execute("INSERT INTO phones(client_id, mac, ipei, ext, name, model, base_id) VALUES(?,?,?,?,?,?,?)",
                        (body.client_id, norm_id(body.mac) or None, norm_id(body.ipei) or None, body.ext, body.name,
                         body.model, body.base_id)).lastrowid
    regenerate(pid)
    return {"id": pid}


@app.put("/api/phones/{pid}")
def update_phone(pid: int, body: PhoneIn):
    mac, ipei = norm_id(body.mac) or None, norm_id(body.ipei) or None
    if mac and len(mac) != 12:
        raise HTTPException(400, "MAC inválida (12 hex)")
    if ipei and len(ipei) != 10:
        raise HTTPException(400, "IPEI inválido (10 hex)")
    with db() as c:
        old = c.execute("SELECT mac, ipei, model FROM phones WHERE id=?", (pid,)).fetchone()
        for col, val in (("mac", mac), ("ipei", ipei)):
            if val:
                d = c.execute(f"SELECT p.ext, p.name, cl.name AS cname FROM phones p JOIN clients cl ON cl.id=p.client_id "
                              f"WHERE p.{col}=? AND p.id<>?", (val, pid)).fetchone()
                if d:
                    raise HTTPException(409, f"Esa {'MAC' if col == 'mac' else 'IPEI'} ya está en «{d['cname']}», "
                                             f"equipo {d['ext'] or '(base)'} {d['name'] or ''}".strip())
        try:
            c.execute("UPDATE phones SET client_id=?, mac=?, ipei=?, ext=?, name=?, model=?, base_id=? WHERE id=?",
                      (body.client_id, mac, ipei, body.ext, body.name, body.model, body.base_id, pid))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Esa MAC/IPEI ya está asignada a otro equipo")
        if old and (old["mac"] != mac or old["ipei"] != ipei):
            c.execute("UPDATE phones SET reboot_done=0, status='pendiente' WHERE id=?", (pid,))
        if body.config is not None:
            c.execute("UPDATE phones SET config=? WHERE id=?", (body.config, pid))
        if body.published is not None:
            c.execute("UPDATE phones SET published=? WHERE id=?", (int(body.published), pid))
        for ident in (mac, ipei):
            if ident:
                c.execute("DELETE FROM detected WHERE mac=?", (ident,))
    if old and kind_of(old["model"]) != kind_of(body.model):
        regenerate(pid)
    return {"ok": True}


@app.get("/api/phones/{pid}/preview")
def preview_phone(pid: int):
    """Lo que recibirá el equipo, con las contraseñas tapadas."""
    p = _phone_by("id", pid)
    if not p:
        raise HTTPException(404, "teléfono no encontrado")
    k, M = kind_of(p["model"]), "••••••"
    dom, port = p["sip_domain"] or "<falta dominio SIP>", p["sip_port"] or 7060
    adm, usr = (M if PHONE_ADMIN_PASSWORD else ""), (M if PHONE_USER_PASSWORD else "")
    if k == "yealink":
        feats = (p["config"] or "").replace("#!version:1.0.0.1", "", 1).strip("\n")
        txt = ("#!version:1.0.0.1\n" + account_cfg(p["ext"], p["name"], M, dom, port) + "\n" + feats + "\n\n"
               + security_cfg(adm, usr) + "\n" + final_tail(not p["reboot_done"]))
    elif k == "gs_base":
        pv = gs_base_defaults(dom, port, adm, usr)
        pv.update(parse_pvalues(p["config"]))
        txt = gs_base_xml(p["mac"] or "<falta MAC>", pv)
    else:
        txt = gs_handset_xml(p["ipei"] or "<falta IPEI>", p["name"], p["ext"], p["name"], M, GS_PROFILE_NAME)
    return {"config": txt}


@app.post("/api/phones/{pid}/generate")
def generate_phone(pid: int):
    return {"config": regenerate(pid)}


@app.delete("/api/phones/{pid}")
def delete_phone(pid: int):
    with db() as c:
        c.execute("DELETE FROM phones WHERE id=?", (pid,))
    return {"ok": True}


@app.get("/api/detected")
def list_detected():
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM detected ORDER BY last_seen DESC")]


@app.post("/api/detected/{ident}/assign")
def assign_detected(ident: str, body: AssignIn):
    ident = norm_id(ident)
    col = "ipei" if len(ident) == 10 else "mac"
    with db() as c:
        d = c.execute("SELECT * FROM detected WHERE mac=?", (ident,)).fetchone()
        try:
            c.execute(f"UPDATE phones SET {col}=?, reboot_done=0, model=COALESCE(?, model) WHERE id=?",
                      (ident, d["model"] if d and col == "mac" else None, body.phone_id))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Ya está asignado a otro equipo")
        c.execute("DELETE FROM detected WHERE mac=?", (ident,))
    return {"ok": True}


@app.delete("/api/detected/{ident}")
def delete_detected(ident: str):
    with db() as c:
        c.execute("DELETE FROM detected WHERE mac=?", (norm_id(ident),))
    return {"ok": True}


@app.get("/api/log")
def get_log(limit: int = 100, client_id: Optional[int] = None):
    q = ("SELECT l.*, p.ext, p.name AS pname, p.client_id FROM log l "
         "LEFT JOIN phones p ON l.mac<>'' AND (p.mac=l.mac OR p.ipei=l.mac)")
    a = ()
    if client_id:
        q, a = q + " WHERE p.client_id=?", (client_id,)
    with db() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY l.id DESC LIMIT ?", a + (limit,))]


@app.get("/api/vpbx/probe")
def vpbx_probe(ext: str = "2001"):
    if not GLOBAL_API_KEY:
        raise HTTPException(400, "Falta VPBX_API_KEY en .env")
    return vpbx.probe(GLOBAL_API_KEY, ext)


# ---------------- Provisión ----------------
def _log(ip, path, ident, ua, result):
    with db() as c:
        c.execute("INSERT INTO log(ts, ip, path, mac, ua, result) VALUES(?,?,?,?,?,?)", (now(), ip, path, ident, ua, result))
        c.execute("DELETE FROM log WHERE id < (SELECT MAX(id) - 2000 FROM log)")


def _phone_by(col, ident):
    with db() as c:
        return c.execute("SELECT p.*, cl.prov_url, cl.api_key, cl.sip_domain, cl.sip_port FROM phones p "
                         f"JOIN clients cl ON cl.id=p.client_id WHERE p.{col}=?", (ident,)).fetchone()


def _detect_or_autoassign(ident, kind, model, ua, ip):
    """MAC/IPEI desconocido: lo asigna al cliente con asignación automática o lo deja en Detectados."""
    col = "ipei" if kind == "gs_handset" else "mac"
    with db() as c:
        cands = c.execute(f"SELECT p.id, p.model FROM phones p JOIN clients cl ON cl.id=p.client_id "
                          f"WHERE cl.auto_assign=1 AND p.{col} IS NULL ORDER BY (p.ext=''), p.ext, p.id").fetchall()
        free = next((r for r in cands if kind_of(r["model"]) == kind), None)
        if free:
            c.execute(f"UPDATE phones SET {col}=?, reboot_done=0, model=COALESCE(?, model) WHERE id=?",
                      (ident, model if col == "mac" else None, free["id"]))
            c.execute("DELETE FROM detected WHERE mac=?", (ident,))
            return True
        c.execute("INSERT INTO detected(mac, model, user_agent, ip, last_seen) VALUES(?,?,?,?,?) "
                  "ON CONFLICT(mac) DO UPDATE SET hits=hits+1, last_seen=excluded.last_seen, ip=excluded.ip, "
                  "user_agent=excluded.user_agent, model=COALESCE(excluded.model, model)",
                  (ident, model or ("DECT (IPEI)" if col == "ipei" else None), ua, ip, now()))
    return False


def _sip_password(p):
    key = p["api_key"] or GLOBAL_API_KEY
    if not key:
        raise vpbx.VpbxError("sin clave API")
    if not p["sip_domain"]:
        raise vpbx.VpbxError("cliente sin dominio SIP")
    pwd = vpbx.sip_password(vpbx.find_extension(key, p["ext"]))
    if not pwd:
        raise vpbx.VpbxError("la API no devuelve la contraseña")
    return pwd

def _vpbx_cfg(prov_url, fname, ua):
    """Descarga el fichero que sirve la provisión nativa del vPBX (lleva la contraseña SIP)."""
    import urllib.request
    url = prov_url.strip()
    if not url.endswith("/"):
        url += "/"
    req = urllib.request.Request(url + fname, headers={"User-Agent": ua or "Yealink"})
    with urllib.request.urlopen(req, timeout=8) as r:
        txt = r.read().decode("utf-8", "replace")
    if "account.1" not in txt:
        raise vpbx.VpbxError("el vPBX no devuelve cuenta")
    return txt.replace("#!version:1.0.0.1", "", 1).strip("\n")


def _status(pid, status, ip, ua, reboot_done=None):
    with db() as c:
        c.execute("UPDATE phones SET status=?, last_seen=?, last_ip=?, user_agent=? WHERE id=?", (status, now(), ip, ua, pid))
        if reboot_done is not None:
            c.execute("UPDATE phones SET reboot_done=? WHERE id=?", (int(reboot_done), pid))


@app.get("/{filename:path}")
def provision(filename: str, request: Request):
    ip, ua = request.state.ip, request.headers.get("user-agent", "")
    path = filename
    if PROV_TOKEN:
        pref = f"p/{PROV_TOKEN}/"
        if not path.startswith(pref):
            _log(ip, "(sin token)", "", ua, "404")
            raise HTTPException(404)
        path = path[len(pref):]
    name = path.rsplit("/", 1)[-1].lower()
    ua_mac_m = MAC_RE.search(ua)
    ua_model = model_from_ua(ua)
    shown = name

    if COMMON_RE.match(name):
        _log(ip, shown, norm_id(ua_mac_m.group(0)) if ua_mac_m else "", ua, "común")
        return PlainTextResponse("#!version:1.0.0.1\n")

    # --- Yealink <mac>.cfg
    m = YL_RE.match(name)
    if m:
        mac = m.group(1)
        p = _phone_by("mac", mac)
        if not p and _detect_or_autoassign(mac, "yealink", ua_model, ua, ip):
            p = _phone_by("mac", mac)
        if not p:
            _log(ip, shown, mac, ua, "desconocido (reintenta cada minuto)")
            return PlainTextResponse(waiting_cfg())
        try:
            pwd = _sip_password(p)
            feats = (p["config"] or "").replace("#!version:1.0.0.1", "", 1).strip("\n")
            reboot = not p["reboot_done"]
            body = ("#!version:1.0.0.1\n" + account_cfg(p["ext"], p["name"], pwd, p["sip_domain"], p["sip_port"] or 7060)
                    + "\n" + feats + "\n\n" + security_cfg(PHONE_ADMIN_PASSWORD, PHONE_USER_PASSWORD) + "\n" + final_tail(reboot))
            st = "configurado (API)" + (" · reinicio enviado" if reboot else "")
            _status(p["id"], st, ip, ua, reboot_done=True)
            _log(ip, shown, mac, ua, st)
            return PlainTextResponse(body)
        except vpbx.VpbxError as e:
            if p["prov_url"]:
                try:
                    base = _vpbx_cfg(p["prov_url"], name, ua)
                    feats = (p["config"] or "").replace("#!version:1.0.0.1", "", 1).strip("\n")
                    reboot = not p["reboot_done"]
                    body = ("#!version:1.0.0.1\n## Base: provisión del vPBX\n" + base + "\n\n## Banco OnWork\n" + feats
                            + "\n\n" + security_cfg(PHONE_ADMIN_PASSWORD, PHONE_USER_PASSWORD) + "\n" + final_tail(reboot))
                    st = "configurado (vPBX + banco)" + (" · reinicio enviado" if reboot else "")
                    _status(p["id"], st, ip, ua, reboot_done=True)
                    _log(ip, shown, mac, ua, st)
                    return PlainTextResponse(body)
                except Exception as e2:
                    e = f"{e}; vPBX: {e2}"
                _status(p["id"], f"API: {e} → redirigido", ip, ua)
                _log(ip, shown, mac, ua, "redirigido")
                return PlainTextResponse(bootstrap_cfg(p["prov_url"]))
            _status(p["id"], f"error: {e}", ip, ua)
            _log(ip, shown, mac, ua, f"error: {e}")
            raise HTTPException(404)

    # --- Base DECT Grandstream cfg<mac>.xml
    m = GS_BASE_RE.match(name)
    if m:
        mac = m.group(1)
        p = _phone_by("mac", mac)
        if not p and _detect_or_autoassign(mac, "gs_base", ua_model or "DP752", ua, ip):
            p = _phone_by("mac", mac)
        if not p:
            _log(ip, shown, mac, ua, "desconocido")
            raise HTTPException(404)
        pv = gs_base_defaults(p["sip_domain"], p["sip_port"] or 7060, PHONE_ADMIN_PASSWORD, PHONE_USER_PASSWORD)
        pv.update(parse_pvalues(p["config"]))
        _status(p["id"], "configurado (base)", ip, ua)
        _log(ip, shown, mac, ua, "configurado (base)")
        return Response(gs_base_xml(mac, pv), media_type="application/xml")

    # --- Terminal DECT por IPEI (lo pide la base)
    m = GS_HS_RE.match(name)
    if m:
        ipei = m.group(1)
        p = _phone_by("ipei", ipei)
        if not p and _detect_or_autoassign(ipei, "gs_handset", None, ua, ip):
            p = _phone_by("ipei", ipei)
        if not p:
            _log(ip, shown, ipei, ua, "terminal desconocido")
            raise HTTPException(404)
        try:
            pwd = _sip_password(p)
        except vpbx.VpbxError as e:
            _status(p["id"], f"error: {e}", ip, ua)
            _log(ip, shown, ipei, ua, f"error: {e}")
            raise HTTPException(404)
        _status(p["id"], "configurado (terminal)", ip, ua)
        _log(ip, shown, ipei, ua, "configurado (terminal)")
        return Response(gs_handset_xml(ipei, p["name"], p["ext"], p["name"], pwd, GS_PROFILE_NAME),
                        media_type="application/xml")

    _log(ip, shown, "", ua, "404")
    raise HTTPException(404)
