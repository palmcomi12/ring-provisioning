#!/usr/bin/env python3
"""Prueba de humo: arranca el servicio con una base de datos temporal y comprueba lo esencial.

    python tests/smoke.py

Solo usa la biblioteca estándar (más las dependencias del propio servicio). No toca .env ni data/.
"""
import base64
import http.server
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN = "tokendeprueba0123"
USER, PWD = "admin", "clave-de-prueba"
AUTH = {"Authorization": "Basic " + base64.b64encode(f"{USER}:{PWD}".encode()).decode()}
YEALINK_UA = "Yealink SIP-T73U 185.86.0.20 00:15:65:aa:bb:cc"


SIP_PWD = "ClaveSipDePrueba42"


class FakeVpbx(http.server.BaseHTTPRequestHandler):
    """Simula lo mínimo de la API del vPBX: buscar la extensión 2001 y devolver su contraseña SIP."""

    def do_GET(self):
        if self.headers.get("X-Api-Key") != "clave-api-de-prueba":
            self.send_response(401); self.end_headers(); return
        if self.path == "/extension/findbyusername/2001":
            out = {"extensionId": 7}
        elif self.path == "/extension/7":
            out = {"username": "2001", "name": "Recepción", "password": SIP_PWD}
        else:
            self.send_response(404); self.end_headers(); return
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(base, path, method="GET", body=None, headers=None):
    h = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


class Server:
    def __init__(self, extra_env=None):
        self.tmp = tempfile.TemporaryDirectory()
        self.port = free_port()
        env = {k: v for k, v in os.environ.items() if not k.startswith(("RING_", "PROV_", "PANEL_", "PHONE_", "VPBX_", "LLM_"))}
        env.update({"ENV_FILE": os.path.join(self.tmp.name, "no-existe.env"), "DB_PATH": os.path.join(self.tmp.name, "prov.db"),
                    "HOST": "127.0.0.1", "PORT": str(self.port), "PROV_TOKEN": TOKEN, "PANEL_USER": USER,
                    "PANEL_PASSWORD": PWD, "PYTHONDONTWRITEBYTECODE": "1"})
        env.update(extra_env or {})
        self.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "run.py")], env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.base = f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        for _ in range(100):
            if self.proc.poll() is not None:
                raise RuntimeError("El servicio no arranca:\n" + self.proc.stdout.read().decode("utf-8", "replace"))
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.3).close()
                return self
            except OSError:
                time.sleep(0.2)
        raise RuntimeError("El servicio no abre el puerto")

    def __exit__(self, *a):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.proc.stdout.close()
        try:
            self.tmp.cleanup()
        except OSError:
            pass


CHECKS = []


def check(name, cond, detail=""):
    CHECKS.append((name, bool(cond)))
    print(("  ok   " if cond else "  FALLO ") + name + ("" if cond else f"  -> {detail}"))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    print("Servicio básico")
    with Server() as s:
        st, _ = call(s.base, "/ui")
        check("el panel pide contraseña (401)", st == 401, st)
        st, body = call(s.base, "/ui", headers=AUTH)
        check("el panel carga con contraseña", st == 200 and "<html" in body.lower(), st)
        st, body = call(s.base, "/api/info", headers=AUTH)
        info = json.loads(body) if st == 200 else {}
        check("/api/info responde y oculta el token", st == 200 and TOKEN not in body and info.get("seguridad", {}).get("token_provision"), body[:200])
        check("asistente RING desactivado sin configurar", info.get("ring") is False)
        st, _ = call(s.base, "/ring", headers=AUTH)
        check("/ring devuelve 404 sin configurar", st == 404, st)

        st, body = call(s.base, "/api/clients", "POST", {"name": "CLIENTE PRUEBA", "sip_domain": "prueba.example.com"}, AUTH)
        cid = json.loads(body).get("id") if st == 200 else None
        check("crear cliente", cid, body[:200])
        st, body = call(s.base, "/api/phones", "POST", {"client_id": cid, "ext": "2001", "name": "Recepción", "model": "T73U",
                                                         "mac": "00:15:65:AA:BB:CC"}, AUTH)
        check("crear teléfono con MAC", st == 200, body[:200])

        ua = {"User-Agent": YEALINK_UA}
        st, body = call(s.base, f"/p/{TOKEN}/001565aabbcc.cfg", headers=ua)
        check("sin clave API el teléfono no recibe cuenta (404)", st == 404, f"{st} {body[:120]}")
        st, body = call(s.base, f"/api/phones?client_id={cid}", headers=AUTH)
        check("el panel explica el motivo (sin clave API)", st == 200 and "sin clave API" in body, body[:200])
        st, _ = call(s.base, "/p/token-malo/001565aabbcc.cfg", headers=ua)
        check("token incorrecto -> 404", st == 404, st)
        st, _ = call(s.base, "/001565aabbcc.cfg", headers=ua)
        check("sin token -> 404", st == 404, st)
        st, body = call(s.base, f"/p/{TOKEN}/001565ddeeff.cfg", headers={"User-Agent": "Yealink SIP-T33G 124.86.0.40 00:15:65:dd:ee:ff"})
        check("MAC desconocida recibe la config de espera", st == 200 and "auto_provision" in body, f"{st} {body[:120]}")
        st, body = call(s.base, "/api/detected", headers=AUTH)
        check("la MAC desconocida aparece en Detectados", st == 200 and "001565ddeeff" in body.lower(), body[:200])
        st, body = call(s.base, "/api/excepcion", headers=AUTH)
        check("/api/excepcion devuelve la IP de origen", st == 200 and json.loads(body).get("tu_ip") == "127.0.0.1", body[:200])

    print("Provisión completa contra un vPBX simulado")
    fake = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeVpbx)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    with Server({"VPBX_API_URL": f"http://127.0.0.1:{fake.server_address[1]}", "VPBX_API_KEY": "clave-api-de-prueba",
                 "PHONE_ADMIN_PASSWORD": "AdminTelefono1"}) as s:
        st, body = call(s.base, "/api/clients", "POST", {"name": "CLIENTE PRUEBA", "sip_domain": "prueba.example.com"}, AUTH)
        cid = json.loads(body).get("id")
        st, body = call(s.base, "/api/phones", "POST", {"client_id": cid, "ext": "2001", "name": "Recepción", "model": "T73U",
                                                         "mac": "001565aabbcc"}, AUTH)
        pid = json.loads(body).get("id") if st == 200 else None
        st, body = call(s.base, f"/p/{TOKEN}/001565aabbcc.cfg", headers={"User-Agent": YEALINK_UA})
        check("el teléfono recibe la cuenta SIP con la contraseña de la API",
              st == 200 and body.startswith("#!version") and SIP_PWD in body and "prueba.example.com" in body, f"{st} {body[:160]}")
        check("y las contraseñas del teléfono", "AdminTelefono1" in body)
        st, body = call(s.base, f"/api/phones?client_id={cid}", headers=AUTH)
        check("el panel lo marca como configurado", st == 200 and "configurado (API)" in body, body[:200])
        if pid:
            st, body = call(s.base, f"/api/phones/{pid}/preview", headers=AUTH)
            check("la vista previa del panel no enseña contraseñas", st == 200 and SIP_PWD not in body and "AdminTelefono1" not in body, body[:200])
    fake.shutdown()

    print("Asistente RING configurado y filtro de IP")
    ring = {"RING_HUB_NAME": "HUB-PRUEBA", "RING_HUB_HOST": "vpn.example.com", "RING_HUB_IP": "203.0.113.1",
            "RING_TUN_NET": "10.99.0", "RING_MGMT_IPS": "203.0.113.10", "PROV_ALLOWED_CIDRS": "203.0.113.0/24"}
    with Server(ring) as s:
        st, body = call(s.base, "/ring", headers=AUTH)
        check("/ring carga con los datos del hub", st == 200 and "vpn.example.com" in body and "/*RING_CFG*/{}" not in body, st)
        st, _ = call(s.base, f"/p/{TOKEN}/001565aabbcc.cfg", headers={"User-Agent": YEALINK_UA})
        check("provisión desde una IP no permitida -> 404", st == 404, st)

    bad = [n for n, ok in CHECKS if not ok]
    print(f"\n{len(CHECKS) - len(bad)}/{len(CHECKS)} comprobaciones correctas")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
