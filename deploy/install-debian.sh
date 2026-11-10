#!/usr/bin/env bash
# Instalación nativa (sin Docker) en Debian 12/13 o Ubuntu 22.04/24.04 con systemd.
#
#   sudo ./deploy/install-debian.sh              instala o actualiza en /opt/ring-provisioning
#   sudo ./deploy/install-debian.sh --port 8080  cambia el puerto (solo en la primera instalación)
#   sudo ./deploy/install-debian.sh --uninstall  quita el servicio (conserva .env y data/)
#
# Es idempotente: al repetirlo actualiza el código y las dependencias y conserva .env y data/.
set -euo pipefail

DEST=/opt/ring-provisioning
SVC=ring-provisioning
RUN_USER=ringprov
PORT=8066
UNINSTALL=0

while [ $# -gt 0 ]; do
  case "$1" in
    --dir) DEST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "Opción desconocida: $1" >&2; exit 2 ;;
  esac
done

[ "$(id -u)" -eq 0 ] || { echo "Ejecuta con sudo o como root." >&2; exit 1; }
SRC="$(cd "$(dirname "$0")/.." && pwd)"
HAS_SYSTEMD=0; [ -d /run/systemd/system ] && HAS_SYSTEMD=1

if [ "$UNINSTALL" -eq 1 ]; then
  if [ "$HAS_SYSTEMD" -eq 1 ]; then
    systemctl disable --now "$SVC" 2>/dev/null || true
    rm -f "/etc/systemd/system/$SVC.service"
    systemctl daemon-reload
  fi
  echo "Servicio quitado. Se conservan $DEST/.env y $DEST/data (bórralos a mano si ya no hacen falta)."
  exit 0
fi

echo "==> Paquetes del sistema"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv ca-certificates >/dev/null

python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    sys.exit("Hace falta Python 3.10 o superior (este sistema trae %d.%d)." % sys.version_info[:2])
PY

echo "==> Usuario de servicio y carpeta $DEST"
id "$RUN_USER" >/dev/null 2>&1 || useradd --system --home-dir "$DEST" --shell /usr/sbin/nologin "$RUN_USER"
mkdir -p "$DEST/data"
if [ "$SRC" != "$DEST" ]; then
  rm -rf "$DEST/app.new"
  cp -r "$SRC/app" "$DEST/app.new"
  rm -rf "$DEST/app.old"
  [ -d "$DEST/app" ] && mv "$DEST/app" "$DEST/app.old"
  mv "$DEST/app.new" "$DEST/app"
  rm -rf "$DEST/app.old"
  mkdir -p "$DEST/deploy"
  cp "$SRC/requirements.txt" "$SRC/run.py" "$SRC/VERSION" "$SRC/.env.example" "$DEST/"
  cp "$SRC/deploy/init_env.py" "$DEST/deploy/"
fi
find "$DEST/app" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true

echo "==> Entorno Python y dependencias"
[ -x "$DEST/.venv/bin/python" ] || python3 -m venv "$DEST/.venv"
"$DEST/.venv/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade pip
"$DEST/.venv/bin/python" -m pip install --quiet --disable-pip-version-check -r "$DEST/requirements.txt"

echo "==> Configuración"
NEW_ENV=0
if [ ! -f "$DEST/.env" ]; then
  NEW_ENV=1
  python3 "$DEST/deploy/init_env.py" "$DEST/.env" --port "$PORT"
else
  echo "Se conserva el $DEST/.env existente."
  PORT="$(sed -n 's/^PORT=\([0-9][0-9]*\).*/\1/p' "$DEST/.env" | head -n1)"; PORT="${PORT:-8066}"
fi
chown -R "$RUN_USER":"$RUN_USER" "$DEST/data" "$DEST/.env"
chmod 600 "$DEST/.env"
chmod 750 "$DEST/data"

if [ "$HAS_SYSTEMD" -eq 1 ]; then
  echo "==> Servicio systemd"
  cat > "/etc/systemd/system/$SVC.service" <<UNIT
[Unit]
Description=Ring provisioning (banco de provision de telefonos)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_USER
WorkingDirectory=$DEST
Environment=ENV_FILE=$DEST/.env
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=$DEST/.venv/bin/python $DEST/run.py
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=$DEST/data

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  systemctl enable "$SVC" >/dev/null 2>&1
  systemctl restart "$SVC"
  sleep 2
  systemctl --no-pager --lines=0 status "$SVC" || true
else
  echo "Este sistema no usa systemd: no se crea el servicio."
  echo "Arranque manual:  ENV_FILE=$DEST/.env $DEST/.venv/bin/python $DEST/run.py"
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "Instalado en $DEST (versión $(cat "$DEST/VERSION"))."
echo "  Panel:      http://${IP:-IP_DEL_SERVIDOR}:$PORT/ui"
echo "  Provisión:  http://${IP:-IP_DEL_SERVIDOR}:$PORT/p/<token>/   (el token está en $DEST/.env)"
if [ "$NEW_ENV" -eq 1 ]; then
  echo "  Edita $DEST/.env para poner las contraseñas de los teléfonos y reinicia: systemctl restart $SVC"
fi
echo "  Registro:   journalctl -u $SVC -f"
