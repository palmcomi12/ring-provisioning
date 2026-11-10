"""Interpretación con IA de hojas o peticiones ambiguas.

Proveedor según variables de entorno:
  LLM_PROVIDER=anthropic  + ANTHROPIC_API_KEY (+ LLM_MODEL, por defecto claude-haiku-5-5)
  LLM_PROVIDER=ollama     + OLLAMA_URL (p.ej. http://servidor:11434) (+ LLM_MODEL, por defecto qwen2.5:14b)
Sin LLM_PROVIDER la función no está disponible y el panel lo indica.
"""
import json
import os
import urllib.request

PROVIDER = os.environ.get("LLM_PROVIDER", "").strip().lower()

SCHEMA = """Devuelve SOLO un JSON con esta forma:
{"client": "nombre del cliente o null",
 "phones": [{"ext": "2001", "name": "Nombre", "model": "T73U|T74U|T33G|DP752|DP722|...", "base_hint": "base DECT a la que pertenece o ''"}],
 "speed_dials": [{"label": "Nombre", "number": "600000000"}],
 "blf_extra": [{"ext": "2002", "name": "Nombre"}],
 "notes": ["dudas o cosas que no has podido interpretar"]}
Reglas: solo extensiones con terminal físico (no 'SENSE TERMINAL' ni 'NOMÉS MÒBIL'); las bases DECT van con ext "";
los números móviles/fijos externos son speed_dials; las extensiones internas que pidan vigilar son blf_extra; no inventes datos."""


def available() -> bool:
    if PROVIDER == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    if PROVIDER == "ollama":
        return bool(os.environ.get("OLLAMA_URL"))
    return False


def _post(url, payload, headers):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


def interpret(sheet_text: str, hint: str = "") -> dict:
    prompt = ("Eres un técnico de telefonía IP de OnWork. Interpreta esta hoja de trabajo de una centralita "
              "y extrae los teléfonos a provisionar.\n" + SCHEMA +
              (f"\nIndicaciones del técnico: {hint}" if hint else "") + "\n\nHOJA:\n" + sheet_text[:60000])
    if PROVIDER == "anthropic":
        r = _post("https://api.anthropic.com/v1/messages",
                  {"model": os.environ.get("LLM_MODEL", "claude-haiku-5-5"), "max_tokens": 4000,
                   "messages": [{"role": "user", "content": prompt}]},
                  {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"})
        text = "".join(b.get("text", "") for b in r.get("content", []))
    elif PROVIDER == "ollama":
        r = _post(os.environ["OLLAMA_URL"].rstrip("/") + "/api/chat",
                  {"model": os.environ.get("LLM_MODEL", "qwen2.5:14b"), "stream": False, "format": "json",
                   "messages": [{"role": "user", "content": prompt}]}, {})
        text = r.get("message", {}).get("content", "")
    else:
        raise RuntimeError("IA no configurada (LLM_PROVIDER)")
    start, end = text.find("{"), text.rfind("}")
    data = json.loads(text[start:end + 1])
    data.setdefault("notes", []).insert(0, "Interpretado con IA: revisa antes de importar.")
    return data
