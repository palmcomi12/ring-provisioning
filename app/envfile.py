"""Carga un fichero .env sencillo (CLAVE=valor) sin dependencias.

Se importa antes que el resto de módulos. No pisa variables que ya estén en el entorno,
así que con Docker (env_file) o systemd (EnvironmentFile) no cambia nada.
Orden de búsqueda: variable ENV_FILE, y si no, el .env de la carpeta del proyecto.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(path=None):
    path = path or os.environ.get("ENV_FILE") or os.path.join(ROOT, ".env")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            if k and k not in os.environ:
                os.environ[k] = v
    return path


load()
