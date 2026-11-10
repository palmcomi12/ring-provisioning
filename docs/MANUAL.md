# Manual de despliegue y operación

Ring provisioning en una máquina virtual de vCenter. Tiempo estimado: 30 minutos.

## 1. Qué se despliega

Una VM pequeña con un único servicio web:

- Escucha en un puerto TCP (8066 por defecto). Por ahí entran los teléfonos (a `/p/<token>/`) y los técnicos (al panel, `/ui`).
- Guarda todo en un fichero SQLite, `data/prov.db`. No hay servidor de base de datos.
- Sale por HTTPS hacia la API del vPBX (`vpbx.me`, puerto 443) para leer la contraseña SIP de cada extensión.

No necesita más componentes. Un proxy inverso con HTTPS delante es opcional y se explica en el apartado 6.

## 2. Qué plataforma elegir

| Opción | Cuándo | Notas |
|---|---|---|
| **VM Debian 12 con instalación nativa** | Caso normal en vCenter | Un script, servicio systemd, sin Docker. Es la que describe este manual |
| VM Linux con Docker | Si ya tenéis un host de contenedores | `docker compose up -d --build` |
| Windows Server | Si no queréis mantener una VM Linux | `deploy\install-windows.ps1`, queda como tarea programada |

No es obligatorio que sea un contenedor ni que sea Debian. La aplicación es Python puro (3.10 a 3.13) y funciona igual en los tres casos.

Recursos de la VM: 1 vCPU, 1 GB de RAM (2 GB si usáis Docker) y 10 GB de disco.

## 3. Crear la VM en vCenter

1. Sube la ISO de Debian 12 (netinst, amd64) a un datastore, o usa la plantilla Linux de la empresa si existe. Con plantilla, clónala y pasa al punto 5.
2. En vSphere Client: botón derecho sobre el clúster o el host, **New Virtual Machine**, **Create a new virtual machine**.
3. Nombre (`ring-provisioning`), carpeta, recurso de cómputo y datastore.
4. Sistema invitado: familia **Linux**, versión **Debian GNU/Linux 12 (64-bit)**. Si vuestra versión de ESXi no la ofrece, elige Debian 11 u «Other Linux (64-bit)»: no afecta al funcionamiento.
5. Hardware:
   - CPU: 1. Memoria: 1 GB.
   - Disco: 10 GB, thin provision.
   - Red: adaptador VMXNET3 en el port group desde el que la van a alcanzar los teléfonos o el proxy inverso.
   - CD/DVD: **Datastore ISO File**, la ISO de Debian, con **Connect At Power On** marcado.
6. Arranca la VM e instala Debian con lo mínimo: sin escritorio, con **SSH server** y **standard system utilities**.
7. Pon IP fija (en el instalador o después en `/etc/network/interfaces`) y apúntala.
8. Entra por SSH e instala las herramientas de VMware y `unzip`:

   ```bash
   sudo apt update && sudo apt install -y open-vm-tools unzip
   ```

9. Haz un snapshot («Debian limpio») antes de seguir. Bórralo cuando el servicio lleve unos días funcionando.

## 4. Instalar el servicio (nativa, recomendada)

Copia el zip a la VM y lanza el instalador:

```bash
scp ring-provisioning-1.0.0.zip usuario@IP_VM:/tmp/
ssh usuario@IP_VM
cd /tmp && unzip ring-provisioning-1.0.0.zip && cd ring-provisioning-1.0.0
sudo ./deploy/install-debian.sh
```

El instalador:

- instala `python3` y `python3-venv` con apt;
- copia el programa a `/opt/ring-provisioning` y crea un entorno Python propio con las dependencias (necesita salida a internet hacia `pypi.org`);
- crea el usuario de sistema `ringprov`, sin shell, que es quien ejecuta el servicio;
- crea `/opt/ring-provisioning/.env` con un **token de provisión** y una **contraseña de panel** aleatorios, y los enseña una sola vez: guárdalos en el gestor de contraseñas;
- crea y arranca el servicio systemd `ring-provisioning`, que también arranca con la máquina.

Opciones: `--port 8080` para otro puerto, `--dir /ruta` para otra carpeta, `--uninstall` para quitar el servicio.

Comprueba:

```bash
systemctl status ring-provisioning
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8066/ui    # debe dar 401 (pide contraseña)
```

### Alternativa: Docker

```bash
cd ring-provisioning-1.0.0
python3 deploy/init_env.py          # crea .env con token y contraseña
docker compose up -d --build
```

Los datos quedan en `./data`. Para cambiar el puerto, `PORT=` en el `.env`.

### Alternativa: Windows Server

Instala Python 3.10 o superior desde python.org marcando «Add python.exe to PATH». Descomprime el zip en una carpeta definitiva (por ejemplo `C:\ring-provisioning`) y, en PowerShell como administrador:

```powershell
cd C:\ring-provisioning
Set-ExecutionPolicy -Scope Process Bypass -Force
.\deploy\install-windows.ps1
```

Crea el entorno Python, el `.env`, una regla de entrada en el Firewall de Windows y la tarea programada `RingProvisioning`, que arranca con el equipo como SYSTEM. Si hay un antivirus con firewall propio, abre también ahí el puerto.

## 5. Configurar

Edita el `.env` y reinicia el servicio:

```bash
sudo nano /opt/ring-provisioning/.env
sudo systemctl restart ring-provisioning
```

| Variable | Valor |
|---|---|
| `PHONE_ADMIN_PASSWORD`, `PHONE_USER_PASSWORD` | Contraseñas web que se dejarán en los teléfonos. Sin ellas los teléfonos conservan las de fábrica |
| `PROV_TOKEN` | Ya generado. La URL de provisión es `http://SERVIDOR:8066/p/<token>/` |
| `PANEL_USER`, `PANEL_PASSWORD` | Ya generados (`admin` y la contraseña mostrada al instalar) |
| `PROV_ALLOWED_CIDRS` | Redes desde las que se aceptan teléfonos. Ej.: `192.168.10.0/24,203.0.113.10/32`. Vacío acepta cualquiera |
| `PANEL_ALLOWED_CIDRS` | Redes que pueden abrir el panel |
| `TRUSTED_PROXIES` | IP del proxy inverso, si lo hay (apartado 6) |
| `VPBX_API_KEY` | Opcional. Lo habitual es pegar la clave de cada centralita en el panel |
| `RING_*` | Datos del hub OpenVPN para el asistente `/ring`. Si no se definen, el asistente no aparece |

Los valores `RING_*` de la empresa no están en el repositorio. Pégalos en el `.env` desde el fichero privado que acompaña a la entrega.

Para traer los clientes de otra instalación, para el servicio, copia su `data/prov.db` a `/opt/ring-provisioning/data/`, ajusta el propietario (`chown ringprov: prov.db`) y arranca.

## 6. Red y publicación

### Solo dentro de la empresa

Los teléfonos y los técnicos llegan a `http://IP_VM:8066`. Abre TCP 8066 hacia la VM en los firewalls intermedios y limita con `PROV_ALLOWED_CIDRS` y `PANEL_ALLOWED_CIDRS` a vuestras redes.

### Accesible desde internet (provisionar desde cualquier sede)

1. Publica el servicio detrás del proxy inverso o firewall de la empresa, con un nombre DNS y HTTPS. El proxy reenvía a `http://IP_VM:8066`.
2. Pon en `TRUSTED_PROXIES` la IP del proxy (`10.0.0.5/32`, por ejemplo). Así el servicio ve la IP real del teléfono en `X-Forwarded-For`; sin esto todos los accesos parecen venir del proxy y los filtros por IP no sirven.
3. Mantén `PROV_TOKEN` y `PANEL_PASSWORD`. Sin token la provisión queda abierta a cualquiera.
4. Limita `PANEL_ALLOWED_CIDRS` a las IP públicas de la oficina.
5. Limita `PROV_ALLOWED_CIDRS` a las IP públicas conocidas. Para una sede puntual usa la **excepción temporal**: abre `https://SERVIDOR/excepcion`, entra con el usuario del panel y autoriza durante una hora la IP desde la que te conectas, u otra que escribas. Las excepciones viven en memoria y se pierden al reiniciar el servicio.

Con HTTPS, comprueba con un teléfono real de cada modelo que acepta el certificado antes de darlo por bueno. Si un modelo no lo acepta, publica también el puerto HTTP y protégelo con token y filtro de IP.

Quien queda fuera de las redes permitidas o no trae el token recibe un 404, igual que si no hubiera nada.

### Resumen de puertos

| Origen | Destino | Puerto | Para qué |
|---|---|---|---|
| Teléfonos, técnicos o proxy inverso | VM | TCP 8066 | Provisión y panel |
| VM | `vpbx.me` | TCP 443 | API de la centralita |
| VM | `pypi.org`, `files.pythonhosted.org`, repositorios de Debian | TCP 443/80 | Solo al instalar o actualizar |

## 7. DHCP: opción 66

Los teléfonos descubren el servicio por la opción 66 del DHCP. En un MikroTik que ya reparte direcciones bastan dos comandos (sustituye lo que va entre corchetes):

```routeros
/ip dhcp-server option add name=prov66 code=66 value="'[URL_PROVISION]/p/[TOKEN]/'"
/ip dhcp-server network set [find address=[RED_LAN]/[MASCARA]] dhcp-option=prov66
```

`[URL_PROVISION]` es `http://IP_VM:8066` o el nombre público con HTTPS, sin barra final. Están también en `deploy/mikrotik-opcion66.rsc`, con los comandos para deshacerlo.

## 8. Uso diario

1. Abre `http://SERVIDOR:8066/ui`.
2. Carga el cliente: **Importar fulla de treball** (Excel) o pega la clave API de su centralita para traer las extensiones.
3. Revisa teclas y marcaciones rápidas.
4. Enchufa los teléfonos a la red con la opción 66. Los que tienen la MAC dada de alta se configuran solos. Los demás aparecen en **Detectados**: elige a qué extensión corresponden y asígnalos.
5. El teléfono se reinicia una vez y queda registrado. El panel muestra el estado y la última vez que pidió su fichero.

Si un teléfono se asignó a la extensión equivocada: corrige o vacía su MAC en el equipo del panel y reinicia el teléfono. Al arrancar vuelve a pedir su fichero y, si su MAC ya no está asignada, sale otra vez en Detectados.

## 9. Mantenimiento

| Tarea | Instalación nativa | Docker |
|---|---|---|
| Ver registro | `journalctl -u ring-provisioning -f` | `docker compose logs -f` |
| Reiniciar | `sudo systemctl restart ring-provisioning` | `docker compose restart` |
| Parar | `sudo systemctl stop ring-provisioning` | `docker compose down` |
| Actualizar | Descomprime la versión nueva y repite `sudo ./deploy/install-debian.sh` | Sustituye los ficheros y `docker compose up -d --build` |
| Desinstalar | `sudo ./deploy/install-debian.sh --uninstall` | `docker compose down` |

En Docker el código va dentro de la imagen: tras cambiar ficheros hay que reconstruir con `--build`; un `restart` no basta.

**Copia de seguridad.** Lo único que hay que guardar es `data/prov.db` y `.env`. Con la copia de VM habitual de vCenter queda cubierto. Para una copia suelta:

```bash
sudo systemctl stop ring-provisioning
sudo cp /opt/ring-provisioning/data/prov.db /opt/ring-provisioning/.env /ruta/de/copias/
sudo systemctl start ring-provisioning
```

**Restaurar.** Instala en una VM nueva, para el servicio, copia `prov.db` y `.env` a su sitio y arranca.

**Empezar de cero.** Para el servicio y borra `data/prov.db`.

## 10. Seguridad

- `data/prov.db` guarda las claves API de las centralitas y `.env` las contraseñas. Solo los lee el usuario del servicio (permisos 600/750). No los copies a carpetas compartidas ni a repositorios.
- El panel usa autenticación básica: publícalo siempre por HTTPS si sale de la red interna.
- La vista previa del panel oculta las contraseñas. Los ficheros que reciben los teléfonos sí las llevan: por eso el token y el filtro de IP.
- El registro del servicio (journal) incluye las rutas pedidas, token incluido. Trátalo como dato interno.
- Cambiar el token: edita `PROV_TOKEN`, reinicia y actualiza la opción 66 de los routers.

## 11. Problemas frecuentes

| Síntoma | Causa y solución |
|---|---|
| El panel devuelve 403 | Tu IP no está en `PANEL_ALLOWED_CIDRS`, o falta `TRUSTED_PROXIES` y el servicio ve la IP del proxy |
| El teléfono no aparece en Actividad | No le llega la opción 66, o un firewall corta el puerto. Prueba la URL desde un PC de esa red |
| Actividad muestra «bloqueado (IP)» | La IP pública de esa sede no está en `PROV_ALLOWED_CIDRS`. Añádela o usa `/excepcion` |
| Estado «error: sin clave API» | Falta la clave API de la centralita en la ficha del cliente |
| Estado «error: HTTP 401…» o «Sin conexión con el vPBX» | Clave API incorrecta, o la VM no tiene salida HTTPS a `vpbx.me` |
| Todas las IP de Actividad son la del proxy | Falta `TRUSTED_PROXIES` |
| El servicio no arranca | `journalctl -u ring-provisioning -n 50`. Lo habitual es un `.env` con una red mal escrita o el puerto ocupado |
| No aparece «Asistente RING» | Faltan `RING_HUB_HOST`, `RING_HUB_IP` o `RING_TUN_NET` en el `.env` |

Prueba completa sin teléfonos: `python tests/smoke.py` desde la carpeta descomprimida, con el Python del entorno (`/opt/ring-provisioning/.venv/bin/python`).
