# Ring provisioning · Banco de provisión de teléfonos

Servicio web que entrega a cada teléfono su configuración cuando se enchufa. El teléfono recibe la URL por DHCP (opción 66), pide su fichero y el servicio le devuelve la cuenta SIP de la centralita RingPBX/vPBX, con la contraseña leída de la API en ese momento, más teclas, marcaciones rápidas y contraseñas de acceso. El teléfono se reinicia una vez y queda registrado.

Incluye un panel web para dar de alta clientes y teléfonos, importar la hoja de trabajo (Excel), ver qué equipos se han detectado y revisar la actividad.

Modelos: Yealink T73U, T74U, T33G, T31P y T77U; bases Grandstream DP750, DP752 y DP755; terminales DECT DP720, DP722 y DP730.

## Dónde funciona

No depende del contenedor ni del sistema operativo. Es una aplicación Python (FastAPI + SQLite) sin componentes nativos, con cuatro dependencias de `pip`.

| Plataforma | Cómo se instala | Estado |
|---|---|---|
| Debian 12/13, Ubuntu 22.04/24.04 (VM) | `sudo ./deploy/install-debian.sh` (venv + servicio systemd) | Recomendado. Probado en cada cambio (CI) |
| Docker / Docker Compose (Linux) | `docker compose up -d --build` | Probado en cada cambio (CI) |
| Windows 10/11, Windows Server 2019/2022 | `deploy\install-windows.ps1` (venv + tarea programada) | Probado en cada cambio (CI, Windows Server) |
| Cualquier sistema con Python 3.10 a 3.13 | `pip install -r requirements.txt` y `python run.py` | Probado con 3.10 y 3.13 |

Recursos: 1 vCPU, 1 GB de RAM y 10 GB de disco sobran. Necesita salida HTTPS hacia la API del vPBX (`vpbx.me`) y que los teléfonos lleguen a su puerto (8066 por defecto).

Para una VM en vCenter lo más simple es Debian 12 con la instalación nativa. El paso a paso está en [docs/MANUAL.md](docs/MANUAL.md).

## Instalación rápida

Descarga el zip de la [última release](../../releases/latest) o clona el repositorio.

**Debian / Ubuntu (nativa, recomendada)**

```bash
unzip ring-provisioning-*.zip && cd ring-provisioning-*/
sudo ./deploy/install-debian.sh
```

Instala en `/opt/ring-provisioning`, crea el `.env` con un token y una contraseña de panel aleatorios (los muestra una sola vez) y deja el servicio `ring-provisioning` arrancado. Repetir el comando actualiza y conserva `.env` y `data/`.

**Docker**

```bash
python3 deploy/init_env.py        # crea .env con token y contraseña; o: cp .env.example .env
docker compose up -d --build
```

**Windows** (PowerShell como administrador, con Python 3.10+ instalado)

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\deploy\install-windows.ps1
```

**Desde el código, sin instalar nada en el sistema**

```bash
python3 -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python deploy/init_env.py
python run.py
```

Después abre `http://SERVIDOR:8066/ui` y apunta la opción 66 del DHCP a `http://SERVIDOR:8066/p/TOKEN/` (ver [deploy/mikrotik-opcion66.rsc](deploy/mikrotik-opcion66.rsc)).

## Configuración

Todo va en el fichero `.env` (plantilla comentada en [.env.example](.env.example)). Lo imprescindible:

| Variable | Para qué |
|---|---|
| `PROV_TOKEN` | Token de la URL de provisión `/p/<token>/`. Sin él cualquiera que conozca la dirección puede pedir ficheros |
| `PANEL_USER`, `PANEL_PASSWORD` | Acceso al panel |
| `PHONE_ADMIN_PASSWORD`, `PHONE_USER_PASSWORD` | Contraseñas web que se dejan en los teléfonos |
| `PROV_ALLOWED_CIDRS`, `PANEL_ALLOWED_CIDRS` | Redes que pueden provisionar y abrir el panel (vacío = cualquiera) |
| `TRUSTED_PROXIES` | Proxy inverso del que se acepta `X-Forwarded-For` |
| `RING_*` | Datos del hub para el asistente de túneles `/ring` (opcional; sin ellos queda oculto) |

La clave API del vPBX se pega por centralita en el panel y se guarda en `data/prov.db`.

## Datos y secretos

- `data/prov.db` (SQLite) contiene clientes, teléfonos y las claves API. `.env` contiene las contraseñas. Los dos están en `.gitignore` y no deben subirse nunca.
- Copia de seguridad: guarda `data/prov.db` y `.env`.
- Para precargar clientes en una instalación vacía, deja un `data/seed.json` con el formato de [seed.example.json](seed.example.json).

## Pruebas

```bash
python tests/smoke.py
```

Arranca el servicio con una base de datos temporal y un vPBX simulado, y comprueba panel, token, filtro de IP y la entrega completa de una configuración. No toca `.env` ni `data/`.

## Publicar una versión

1. Cambia el número en `VERSION` y súbelo a `main`.
2. Crea la etiqueta: `git tag v1.2.3 && git push origin v1.2.3` (o lanza a mano el workflow *Release* indicando la versión).
3. GitHub Actions pasa las pruebas y publica `ring-provisioning-1.2.3.zip` en *Releases*.

## Estructura

```
app/            servicio (main.py), generadores de configuración y panel (static/)
deploy/         instaladores, creación del .env y comandos de MikroTik
docs/MANUAL.md  manual de despliegue (vCenter) y operación
tests/smoke.py  prueba de humo
run.py          arranque sin Docker
```
