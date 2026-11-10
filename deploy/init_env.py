#!/usr/bin/env python3
"""Crea el .env a partir de .env.example y genera el token de provisión y la contraseña del panel.

Uso:  python deploy/init_env.py [ruta_del_.env] [--port 8066]
Si el .env ya existe no lo toca. Solo usa la biblioteca estándar.
"""
import os
import re
import secrets
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    args = [a for a in sys.argv[1:]]
    port = None
    if "--port" in args:
        i = args.index("--port")
        port = args[i + 1]
        del args[i:i + 2]
    dest = args[0] if args else os.path.join(ROOT, ".env")
    if os.path.exists(dest):
        print(f"Ya existe {dest}: no se modifica.")
        return 0
    with open(os.path.join(ROOT, ".env.example"), encoding="utf-8") as f:
        txt = f.read()
    token = secrets.token_hex(16)
    panel = secrets.token_urlsafe(12)
    txt = re.sub(r"(?m)^PROV_TOKEN=.*$", "PROV_TOKEN=" + token, txt)
    txt = re.sub(r"(?m)^PANEL_PASSWORD=.*$", "PANEL_PASSWORD=" + panel, txt)
    if port:
        txt = re.sub(r"(?m)^PORT=.*$", "PORT=" + str(int(port)), txt)
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(txt)
    print(f"Creado {dest}")
    print("  Usuario del panel:    admin")
    print(f"  Contraseña del panel: {panel}")
    print(f"  Ruta de provisión:    /p/{token}/")
    print("Guárdalos en el gestor de contraseñas. Faltan por rellenar PHONE_ADMIN_PASSWORD y PHONE_USER_PASSWORD.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
