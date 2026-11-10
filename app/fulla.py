"""Lectura de la "fulla de treball" (Excel del comercial) -> clientes/teléfonos/teclas.

Lectura determinista de la plantilla de RING. Lo que no se pueda leer con reglas
se puede mandar a la IA (llm.py) desde el panel.
"""
import io
import re
import unicodedata

from models import kind_of, model_from_text

SKIP_TERMINALS = ("SENSE TERMINAL", "NOMES MOBIL", "NOMÉS MÒBIL")


def _norm(s) -> str:
    s = "" if s is None else str(s)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().upper()


def load_grid(data: bytes):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    ws = wb.worksheets[0]
    grid = {}
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is not None and str(cell.value).strip() != "":
                grid[(cell.row, cell.column)] = cell.value
    return grid


def grid_text(grid) -> str:
    """Texto plano de la hoja (para la IA)."""
    from openpyxl.utils import get_column_letter
    return "\n".join(f"{get_column_letter(c)}{r}: {v}" for (r, c), v in sorted(grid.items()))


def _find(grid, pred):
    for (r, c), v in sorted(grid.items()):
        if pred(_norm(v)):
            return r, c
    return None


def _right_value(grid, r, c, max_cols=12):
    for cc in range(c + 1, c + max_cols):
        v = grid.get((r, cc))
        if v is not None:
            return v
    return None


def parse_speed_dials(text: str):
    """'1 - Nombre Apellido 600000000 2 – Otro ...' -> marcaciones rápidas y BLF internas."""
    sd, blf = [], []
    for m in re.finditer(r"(\d{1,2})\s*[-–]\s*([^\d\n]+?)\s*:?\s*((?:\+?34)?\d{3,12})(?=\s|$)", text or ""):
        label = m.group(2).strip(" :-–")
        num = re.sub(r"^\+?34(?=\d{9}$)", "", m.group(3))
        if not label:
            continue
        if len(num) <= 4:
            blf.append({"ext": num, "name": label})
        else:
            sd.append({"label": label[:20], "number": num})
    return sd, blf


def parse(data: bytes) -> dict:
    grid = load_grid(data)
    res = {"client": None, "phones": [], "speed_dials": [], "blf_extra": [], "notes": [], "ambiguous": False}

    pos = _find(grid, lambda s: s == "NOM CLIENT")
    if pos:
        res["client"] = str(_right_value(grid, *pos) or "").strip() or None

    hdr = _find(grid, lambda s: s == "EXTENSIO SIP")
    if not hdr:
        res["notes"].append("No encuentro la tabla 'DETALL EXTENSIONS' (columna EXTENSIÓ SIP).")
        res["ambiguous"] = True
    else:
        hr = hdr[0]
        cols = {}
        for (r, c), v in grid.items():
            if r == hr:
                cols[_norm(v)] = c
        c_ext = hdr[1]
        c_name = cols.get("NOM")
        c_dep = cols.get("DEPARTAMENT")
        c_term = cols.get("TIPUS DE TERMINAL")
        c_base = next((c for k, c in cols.items() if k.startswith("BASE IP")), None)
        empty = 0
        r = hr + 1
        while empty < 3 and r < hr + 200:
            ext = grid.get((r, c_ext))
            if ext is None:
                empty += 1
                r += 1
                continue
            empty = 0
            ext = str(ext).strip().split(".")[0]
            name = str(grid.get((r, c_name)) or "").strip() if c_name else ""
            dep = str(grid.get((r, c_dep)) or "").strip() if c_dep else ""
            term = str(grid.get((r, c_term)) or "").strip() if c_term else ""
            base = str(grid.get((r, c_base)) or "").strip() if c_base else ""
            if any(s in _norm(term) for s in SKIP_TERMINALS) or not term:
                res["notes"].append(f"{ext}: sin terminal ({term or 'vacío'}), no se crea teléfono.")
            else:
                model = model_from_text(term)
                if not model:
                    res["notes"].append(f"{ext}: no reconozco el terminal «{term}».")
                    res["ambiguous"] = True
                else:
                    k = kind_of(model)
                    res["phones"].append({"ext": "" if k == "gs_base" else ext, "name": name or dep, "model": model,
                                          "kind": k, "base_hint": base})
            if base and "DP7" in _norm(base):
                bm = model_from_text(base)
                if bm and not any(p["kind"] == "gs_base" and p.get("base_hint") == base for p in res["phones"]):
                    res["phones"].append({"ext": "", "name": f"Base {base}", "model": bm,
                                          "kind": "gs_base", "base_hint": base})
            r += 1

    blf_pos = _find(grid, lambda s: s.startswith("TECLES BLF"))
    if blf_pos:
        r0, c0 = blf_pos
        text = None
        for rr in range(r0 + 1, r0 + 6):
            v = grid.get((rr, c0))
            if v:
                text = str(v)
                break
        if text:
            sd, blf = parse_speed_dials(text)
            res["speed_dials"], res["blf_extra"] = sd, blf
            if not sd and not blf:
                res["notes"].append("Hay texto de teclas BLF pero no lo entiendo.")
                res["ambiguous"] = True

    if not res["phones"]:
        res["ambiguous"] = True
    return res
