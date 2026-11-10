"""Catálogo de modelos soportados y cómo se provisiona cada uno."""
import re

# kind: yealink = teléfono Yealink (fichero <mac>.cfg)
#       gs_base = base DECT Grandstream (cfg<mac>.xml)
#       gs_handset = terminal DECT Grandstream, va por IPEI a través de su base
MODELS = {
    "T73U": {"kind": "yealink", "linekeys": 3, "label": "Yealink T73U"},
    "T74U": {"kind": "yealink", "linekeys": 8, "label": "Yealink T74U"},
    "T33G": {"kind": "yealink", "linekeys": 4, "label": "Yealink T33G"},
    "T31P": {"kind": "yealink", "linekeys": 2, "label": "Yealink T31P"},
    "T77U": {"kind": "yealink", "linekeys": 8, "label": "Yealink T77U"},
    "DP752": {"kind": "gs_base", "label": "Grandstream DP752 (base DECT)"},
    "DP755": {"kind": "gs_base", "label": "Grandstream DP755 (base DECT)"},
    "DP750": {"kind": "gs_base", "label": "Grandstream DP750 (base DECT)"},
    "DP722": {"kind": "gs_handset", "label": "Grandstream DP722 (terminal DECT)"},
    "DP720": {"kind": "gs_handset", "label": "Grandstream DP720 (terminal DECT)"},
    "DP730": {"kind": "gs_handset", "label": "Grandstream DP730 (terminal DECT)"},
}


def kind_of(model: str) -> str:
    m = (model or "").upper().strip()
    if m in MODELS:
        return MODELS[m]["kind"]
    if re.match(r"^(T|W|CP|VP)\d", m):
        return "yealink"
    if re.match(r"^DP7[5-9]\d", m):
        return "gs_base"
    if re.match(r"^DP7[0-4]\d", m):
        return "gs_handset"
    return "yealink"


def model_from_text(text: str):
    """Detecta el modelo en un texto libre (p.ej. 'YEALINK T73U i CARREGADOR')."""
    t = (text or "").upper()
    m = re.search(r"\b(DP7\d\d)\b", t)
    if m:
        return m.group(1)
    m = re.search(r"\b(T\d{2}[A-Z]?)\b", t)
    if m:
        return m.group(1)
    return None


def model_from_ua(ua: str):
    u = ua or ""
    m = re.search(r"Yealink\s+SIP[-\s]?(\S+)", u, re.I)
    if m:
        return m.group(1).upper()
    m = re.search(r"\b(DP7\d\d)\b", u, re.I)
    if m:
        return m.group(1).upper()
    return None
