"""Generadores de configuración.

Yealink (T73U, T74U, T33G...): fichero <mac>.cfg
  - features_cfg: parte editable en el panel (teclas, idioma, hora, códecs...).
  - account_cfg: cuenta SIP, se genera al servir (contraseña desde la API, no se guarda).
Grandstream DECT:
  - gs_base_xml: cfg<mac>.xml de la base (servidor, transporte, códecs, provisión por IPEI).
  - gs_handset_xml: ipei_<IPEI>.xml de cada terminal (su línea SIP).

Criterio RingPBX/vPBX: puerto 7060, transporte TCP, solo PCMA/PCMU, caller ID RPID-PAI-FROM.
"""
from xml.sax.saxutils import escape

KEY_LINE = 15
KEY_SPEED_DIAL = 13
KEY_BLF = 16

# Códecs Yealink que se desactivan (solo quedan PCMA y PCMU). Los que no existan en un modelo se ignoran.
YEALINK_OFF_CODECS = ["g722", "g729", "g726_16", "g726_24", "g726_32", "g726_40", "ilbc",
                      "opus", "g723_53", "g723_63", "amrwb", "g722_1_24", "g722_1_32", "g722_1c_48"]


# ---------------------------------------------------------------- Yealink
def waiting_cfg() -> str:
    """Teléfono sin asignar: que vuelva a preguntar cada minuto."""
    return "\n".join([
        "#!version:1.0.0.1",
        "## OnWork banco: teléfono sin asignar, reintenta cada minuto",
        "static.auto_provision.repeat.enable = 1",
        "static.auto_provision.repeat.minutes = 1",
        "",
    ])


def account_cfg(ext: str, name: str, password: str, domain: str, port: int = 7060) -> str:
    return "\n".join([
        "## Cuenta SIP (generada al vuelo desde la API del vPBX)",
        "account.1.enable = 1",
        f"account.1.label = {ext}",
        f"account.1.display_name = {name or ext}",
        f"account.1.auth_name = {ext}",
        f"account.1.user_name = {ext}",
        f"account.1.password = {password}",
        f"account.1.sip_server.1.address = {domain}",
        f"account.1.sip_server.1.port = {port}",
        "account.1.sip_server.1.transport_type = 1",
        "account.1.sip_server.1.expires = 300",
        "account.1.cid_source = 3",
        "account.1.nat.udp_update_enable = 1",
        "",
    ])


def security_cfg(admin_pwd: str, user_pwd: str) -> str:
    out = []
    if admin_pwd:
        out.append(f"static.security.user_password = admin:{admin_pwd}")
    if user_pwd:
        out.append(f"static.security.user_password = user:{user_pwd}")
    return ("## Acceso al teléfono\n" + "\n".join(out) + "\n") if out else ""


def final_tail(reboot: bool) -> str:
    lines = ["## Fin de provisión en banco", "static.auto_provision.repeat.enable = 0"]
    if reboot:
        # Solo en la primera entrega: reinicia para aplicar todo; las siguientes no.
        lines.append("static.auto_provision.reboot_force.enable = 1")
    else:
        lines.append("static.auto_provision.reboot_force.enable = 0")
    return "\n".join(lines) + "\n"


def bootstrap_cfg(prov_url: str) -> str:
    url = prov_url.strip()
    if url and not url.endswith("/"):
        url += "/"
    return "\n".join([
        "#!version:1.0.0.1",
        "## OnWork banco -> redirige al vPBX del cliente",
        f"static.auto_provision.server.url = {url}",
        "static.auto_provision.dhcp_option.enable = 0",
        "static.auto_provision.pnp_enable = 0",
        "static.auto_provision.power_on = 1",
        "static.auto_provision.reboot_force.enable = 1",
        "",
    ])


def features_cfg(phone: dict, blf: list, speed_dials: list, max_keys: int = 0) -> str:
    """phone: ext, name, model. blf: [{ext,name}]. speed_dials: [{label,number}]."""
    out = [
        "#!version:1.0.0.1",
        f"## {phone['ext']} - {phone.get('name') or ''} ({phone.get('model') or 'Yealink'})",
        "",
        "## Idioma y hora",
        "lang.gui = Spanish",
        "static.lang.wui = Spanish",
        "local_time.time_zone = +1",
        "local_time.time_zone_name = Spain(Madrid)",
        "local_time.ntp_server1 = es.pool.ntp.org",
        "local_time.summer_time = 2",
        "local_time.time_format = 1",
        "local_time.date_format = 5",
        "",
        "## Funciones",
        "features.dnd.allow = 0",
        "features.pickup.group_pickup_enable = 1",
        "features.pickup.group_pickup_code = *8",
        "",
        "## Códecs: solo PCMA y PCMU",
        "account.1.codec.pcma.enable = 1",
        "account.1.codec.pcma.priority = 1",
        "account.1.codec.pcmu.enable = 1",
        "account.1.codec.pcmu.priority = 2",
    ] + [f"account.1.codec.{c}.enable = 0" for c in YEALINK_OFF_CODECS] + [
        "",
        "## Teclas",
        "linekey.1.type = 15",
        "linekey.1.line = 1",
        f"linekey.1.label = {phone['ext']}",
    ]
    k = 2
    for s in blf:
        if s["ext"] == phone["ext"]:
            continue
        out += [f"linekey.{k}.type = {KEY_BLF}", f"linekey.{k}.line = 1",
                f"linekey.{k}.value = {s['ext']}", f"linekey.{k}.label = {s.get('name') or s['ext']}"]
        k += 1
    for sd in speed_dials:
        num = str(sd.get("number", "")).replace(" ", "")
        if not num:
            continue
        out += [f"linekey.{k}.type = {KEY_SPEED_DIAL}", f"linekey.{k}.line = 1",
                f"linekey.{k}.value = {num}", f"linekey.{k}.label = {sd.get('label') or num}"]
        k += 1
    if max_keys and k - 1 > max_keys:
        out.append(f"## Aviso: {k - 1} teclas; el {phone.get('model')} muestra {max_keys} por página (el resto va en páginas siguientes si el modelo lo admite)")
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------- Grandstream DECT
def gs_base_defaults(domain: str, port: int, admin_pwd: str, user_pwd: str) -> dict:
    """P-values de la base DP75x. Por verificar con la primera unidad (ver documento)."""
    p = {
        "P47": f"{domain}:{port}",   # Perfil 1: servidor SIP
        "P130": "1",                 # Perfil 1: transporte TCP
        "P57": "8", "P58": "0",      # Códecs: PCMA, PCMU
        "P59": "8", "P60": "8", "P61": "8", "P62": "8",
        "P27014": "1",               # Enable Handset Config (provisión por IPEI)
        "P212": "1",                 # Provisión por HTTP
        "P64": "CET-1CEST-2,M3.5.0,M10.5.0/3",  # Zona horaria Madrid
        "P30": "es.pool.ntp.org",
    }
    if admin_pwd:
        p["P2"] = admin_pwd
    if user_pwd:
        p["P196"] = user_pwd
    return p


def parse_pvalues(text: str) -> dict:
    """Líneas 'P123 = valor' del panel -> dict. Ignora comentarios."""
    out = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k.upper().startswith("P") and k[1:].isdigit():
            out["P" + k[1:]] = v.strip()
    return out


def gs_base_xml(mac: str, pvalues: dict) -> str:
    items = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in pvalues.items())
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<gs_provision version="1"><mac>{mac}</mac><config version="1">{items}</config></gs_provision>\n')


def gs_handset_xml(ipei: str, hs_name: str, ext: str, display: str, password: str, profile: str) -> str:
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<hs_config>'
            f"<ipei>{escape(ipei)}</ipei><hs_name>{escape((hs_name or ext)[:10])}</hs_name>"
            "<sip><line>"
            f"<display_name>{escape(display or ext)}</display_name>"
            f"<profile_name>{escape(profile)}</profile_name>"
            f"<user_id>{escape(ext)}</user_id><auth_id>{escape(ext)}</auth_id>"
            f"<password>{escape(password)}</password><ring_type>DID</ring_type>"
            "</line></sip></hs_config>\n")
