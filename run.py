#!/usr/bin/env python3
"""Arranque sin Docker (Linux, Windows o macOS):  python run.py

Lee el .env de esta carpeta (o el indicado en ENV_FILE). Variables: HOST (0.0.0.0) y PORT (8066).
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "app")
sys.path.insert(0, APP)

import envfile  # noqa: E402,F401  (carga el .env)


def main():
    try:
        import uvicorn
    except ImportError:
        sys.exit("Faltan dependencias. Ejecuta: python -m pip install -r requirements.txt")
    uvicorn.run("main:app", app_dir=APP, host=os.environ.get("HOST", "0.0.0.0"),
                port=int(os.environ.get("PORT", "8066")), proxy_headers=True)


if __name__ == "__main__":
    main()
