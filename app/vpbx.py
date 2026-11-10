"""Cliente mínimo de la API del vPBX (https://vpbx.me/api, cabecera X-Api-Key)."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

# VPBX_API_URL permite apuntar a otra instancia del vPBX (o a un simulador en las pruebas)
BASE = (os.environ.get("VPBX_API_URL") or "https://vpbx.me/api").rstrip("/")
SECRET_HINTS = ("pass", "secret", "pwd", "token", "key")


class VpbxError(Exception):
    pass


def _get(api_key: str, path: str):
    req = urllib.request.Request(
        BASE + path,
        headers={"X-Api-Key": api_key, "Accept": "application/json", "User-Agent": "ring-provisioning"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        raise VpbxError(f"HTTP {e.code} en {path}") from None
    except urllib.error.URLError as e:
        raise VpbxError(f"Sin conexión con el vPBX: {e.reason}") from None


def find_extension(api_key: str, ext: str) -> dict:
    data = _get(api_key, f"/extension/findbyusername/{urllib.parse.quote(ext)}")
    ext_id = data.get("extensionId") if isinstance(data, dict) else data
    if not ext_id:
        raise VpbxError(f"La extensión {ext} no existe en el vPBX")
    detail = _get(api_key, f"/extension/{ext_id}")
    if not isinstance(detail, dict):
        raise VpbxError("Respuesta inesperada del vPBX")
    return detail


def list_extensions(api_key: str) -> list:
    """Extensiones de la centralita: [{ext, name, domain, type}] (sin secretos)."""
    data = _get(api_key, "/extension")
    out = []
    for e in data if isinstance(data, list) else []:
        if not isinstance(e, dict) or not e.get("username"):
            continue
        out.append({"ext": str(e["username"]), "name": e.get("name") or "",
                    "domain": e.get("domain") or "", "type": e.get("type") or ""})
    return sorted(out, key=lambda x: x["ext"])


def sip_password(detail: dict):
    """Busca la contraseña SIP en la respuesta (el nombre del campo no está documentado)."""
    for k in ("sipPassword", "password", "secret", "sipSecret", "authPassword"):
        v = detail.get(k)
        if isinstance(v, str) and v:
            return v
    for k, v in detail.items():
        if isinstance(v, str) and v and "pass" in k.lower():
            return v
    return None


def mask(obj):
    """Copia de la respuesta con los secretos ocultos, para diagnóstico."""
    if isinstance(obj, dict):
        return {k: ("•••• (%d car.)" % len(v) if isinstance(v, str) and v and any(h in k.lower() for h in SECRET_HINTS) else mask(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask(x) for x in obj]
    return obj


def _shape(obj):
    """Resumen de una respuesta: tipo, nº de elementos y campos."""
    if isinstance(obj, list):
        first = obj[0] if obj else None
        return {"tipo": "lista", "elementos": len(obj), "campos": sorted(first.keys()) if isinstance(first, dict) else None,
                "ejemplo": mask(first) if isinstance(first, dict) else None}
    if isinstance(obj, dict):
        return {"tipo": "objeto", "campos": sorted(obj.keys()), "valor": mask(obj)}
    return {"tipo": type(obj).__name__}


def probe(api_key: str, ext: str) -> dict:
    out = {}
    for path in ("/extension", f"/extension/findbyusername/{ext}", "/pbx", "/agent", "/voiceengine"):
        try:
            out[path] = {"ok": True, **_shape(_get(api_key, path))}
        except VpbxError as e:
            out[path] = {"ok": False, "error": str(e)}
    return out


def diagnose(api_key: str, ext: str) -> dict:
    detail = find_extension(api_key, ext)
    return {"campos": sorted(detail.keys()), "password_encontrada": bool(sip_password(detail)), "respuesta": mask(detail)}
