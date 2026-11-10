# Cambios mínimos en un MikroTik que ya da DHCP a los teléfonos: opción 66 hacia el servicio.
# Sustituye lo que va entre corchetes (corchetes incluidos):
#   [URL_PROVISION]  URL del servicio, sin barra final.  Ej.: https://prov.ejemplo.com  o  http://192.168.10.20:8066
#   [TOKEN]          valor de PROV_TOKEN del .env
#   [RED_LAN]/[MASCARA]  red del DHCP de los teléfonos.  Ej.: 192.168.10.0/24
# Las comillas simples dentro de las dobles son necesarias: indican a RouterOS que el valor es texto.
/ip dhcp-server option add name=prov66 code=66 value="'[URL_PROVISION]/p/[TOKEN]/'"
/ip dhcp-server network set [find address=[RED_LAN]/[MASCARA]] dhcp-option=prov66

# Para deshacerlo:
# /ip dhcp-server network set [find address=[RED_LAN]/[MASCARA]] dhcp-option=""
# /ip dhcp-server option remove [find name=prov66]
