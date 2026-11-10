<#
Instalación nativa en Windows 10/11 o Windows Server 2019/2022 (sin Docker).

Requisito: Python 3.10 o superior instalado desde python.org ("Add python.exe to PATH" marcado).

Desde una consola de PowerShell abierta como administrador, en la carpeta del proyecto:

    Set-ExecutionPolicy -Scope Process Bypass -Force
    .\deploy\install-windows.ps1                 # instala y deja una tarea programada al arrancar
    .\deploy\install-windows.ps1 -Port 8080      # otro puerto (solo en la primera instalación)
    .\deploy\install-windows.ps1 -NoFirewall     # no crea la regla del Firewall de Windows
    .\deploy\install-windows.ps1 -Uninstall      # quita la tarea y la regla (conserva .env y data\)

El servicio se ejecuta desde esta misma carpeta: no la muevas después de instalar.
#>
param(
    [int]$Port = 8066,
    [switch]$NoFirewall,
    [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Task = 'RingProvisioning'
$Rule = 'Ring provisioning'

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'Abre PowerShell como administrador.' }

if ($Uninstall) {
    Stop-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $Task -Confirm:$false -ErrorAction SilentlyContinue
    Remove-NetFirewallRule -DisplayName $Rule -ErrorAction SilentlyContinue
    Write-Host 'Tarea y regla quitadas. Se conservan .env y data\.'
    return
}

# Python: primero el lanzador "py", luego python.exe
$py = $null
if (Get-Command py -ErrorAction SilentlyContinue) { $py = @('py', '-3') }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $py = @('python') }
else { throw 'No encuentro Python. Instálalo desde https://www.python.org/downloads/windows/ y vuelve a ejecutar.' }
$pyExe = $py[0]; $pyArgs = @(); if ($py.Count -gt 1) { $pyArgs = $py[1..($py.Count - 1)] }
& $pyExe @pyArgs -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if ($LASTEXITCODE -ne 0) { throw 'Hace falta Python 3.10 o superior.' }

Write-Host '==> Entorno Python y dependencias'
$venvPy = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    & $pyExe @pyArgs -m venv (Join-Path $Root '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el entorno virtual.' }
}
& $venvPy -m pip install --quiet --disable-pip-version-check -r (Join-Path $Root 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Fallo al instalar las dependencias.' }

Write-Host '==> Configuración'
$envFile = Join-Path $Root '.env'
if (-not (Test-Path $envFile)) {
    & $venvPy (Join-Path $Root 'deploy\init_env.py') $envFile --port $Port
    # El .env guarda contraseñas: solo administradores y SYSTEM
    icacls $envFile /inheritance:r /grant:r 'SYSTEM:F' 'BUILTIN\Administrators:F' | Out-Null
} else {
    Write-Host 'Se conserva el .env existente.'
    $m = Select-String -Path $envFile -Pattern '^PORT=(\d+)' | Select-Object -First 1
    if ($m) { $Port = [int]$m.Matches[0].Groups[1].Value }
}
New-Item -ItemType Directory -Force -Path (Join-Path $Root 'data') | Out-Null

if (-not $NoFirewall) {
    Write-Host "==> Regla del Firewall de Windows (TCP $Port de entrada)"
    Remove-NetFirewallRule -DisplayName $Rule -ErrorAction SilentlyContinue
    New-NetFirewallRule -DisplayName $Rule -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port | Out-Null
}

Write-Host '==> Tarea programada al arrancar el equipo'
$action = New-ScheduledTaskAction -Execute $venvPy -Argument ('"' + (Join-Path $Root 'run.py') + '"') -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $Task -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Stop-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName $Task

Start-Sleep -Seconds 4
$ok = $false
try {
    Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/ui" -TimeoutSec 5 | Out-Null
    $ok = $true
} catch {
    if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 401) { $ok = $true }
}
Write-Host ''
if ($ok) { Write-Host "Instalado y en marcha. Panel: http://<IP de este equipo>:$Port/ui" }
else { Write-Warning "La tarea está creada pero el servicio no responde en el puerto $Port. Prueba a mano: `"$venvPy`" run.py" }
Write-Host "Provisión: http://<IP de este equipo>:$Port/p/<token>/  (el token está en $envFile)"
Write-Host 'Recuerda poner PHONE_ADMIN_PASSWORD y PHONE_USER_PASSWORD en el .env y reiniciar la tarea RingProvisioning.'
