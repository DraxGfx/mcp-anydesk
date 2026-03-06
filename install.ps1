# MCP AnyDesk Server - Instalador
# Ejecutar: powershell -ExecutionPolicy Bypass -File install.ps1

$ErrorActionPreference = 'Stop'

Write-Host ''
Write-Host '=== MCP AnyDesk Server - Instalador ===' -ForegroundColor Cyan
Write-Host ''

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$venvDir = Join-Path $projectDir '.venv'
$venvPython = Join-Path $venvDir 'Scripts\python.exe'
$venvPip = Join-Path $venvDir 'Scripts\pip.exe'
$configPath = Join-Path $env:APPDATA 'Claude\claude_desktop_config.json'

# ---------------------------------------------------------------
# 1. Verificar Python
# ---------------------------------------------------------------
Write-Host '[1/6] Verificando Python...' -ForegroundColor Yellow
try {
    $pyVersion = python --version 2>&1
    Write-Host "  OK: $pyVersion" -ForegroundColor Green
} catch {
    Write-Host '  ERROR: Python no encontrado. Instala Python 3.11+ desde python.org' -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------
# 2. Crear venv
# ---------------------------------------------------------------
Write-Host '[2/6] Creando entorno virtual...' -ForegroundColor Yellow
if (Test-Path $venvPython) {
    Write-Host '  OK: .venv ya existe' -ForegroundColor Green
} else {
    python -m venv $venvDir
    Write-Host '  OK: .venv creado' -ForegroundColor Green
}

# ---------------------------------------------------------------
# 3. Instalar dependencias
# ---------------------------------------------------------------
Write-Host '[3/6] Instalando dependencias...' -ForegroundColor Yellow
& $venvPip install -e $projectDir --quiet 2>&1 | Out-Null
Write-Host '  OK: Dependencias instaladas' -ForegroundColor Green

# ---------------------------------------------------------------
# 4. Verificar Tesseract OCR
# ---------------------------------------------------------------
Write-Host '[4/6] Verificando Tesseract OCR...' -ForegroundColor Yellow
$tesseract = Get-Command tesseract -ErrorAction SilentlyContinue
if ($tesseract) {
    Write-Host '  OK: Tesseract encontrado' -ForegroundColor Green
} else {
    Write-Host '  ADVERTENCIA: Tesseract no encontrado.' -ForegroundColor Red
    Write-Host '  Sin Tesseract, OCR no estara disponible.' -ForegroundColor Yellow
    Write-Host '  Instalar con: winget install UB-Mannheim.TesseractOCR' -ForegroundColor White
    Write-Host ''
}

# ---------------------------------------------------------------
# 5. Verificar dependencias con preflight
# ---------------------------------------------------------------
Write-Host '[5/6] Ejecutando preflight check...' -ForegroundColor Yellow
$preflightScript = Join-Path $projectDir 'install_preflight.py'
$preflightOutput = & $venvPython $preflightScript 2>$null

try {
    $pf = $preflightOutput | ConvertFrom-Json
    Write-Host "  Server version:  $($pf.version)" -ForegroundColor White

    $items = @(
        @{Name='pywin32 (inyeccion)'; OK=$pf.pywin32},
        @{Name='mss (captura)'; OK=$pf.mss},
        @{Name='opencv (procesamiento)'; OK=$pf.opencv},
        @{Name='tesseract (OCR)'; OK=$pf.tesseract}
    )
    foreach ($item in $items) {
        if ($item.OK) {
            Write-Host "  $($item.Name): OK" -ForegroundColor Green
        } else {
            Write-Host "  $($item.Name): NO DISPONIBLE" -ForegroundColor Red
        }
    }
    Write-Host ''
    if ($pf.write_capable) {
        Write-Host '  Inyeccion de teclado: DISPONIBLE' -ForegroundColor Green
    } else {
        Write-Host '  Inyeccion de teclado: NO DISPONIBLE' -ForegroundColor Red
    }
    if ($pf.ocr_capable) {
        Write-Host '  Lectura OCR: DISPONIBLE' -ForegroundColor Green
    } elseif ($pf.read_capable) {
        Write-Host '  Lectura OCR: PARCIAL (falta Tesseract)' -ForegroundColor Yellow
    } else {
        Write-Host '  Lectura OCR: NO DISPONIBLE' -ForegroundColor Red
    }
} catch {
    Write-Host '  ERROR ejecutando preflight. Revisa la instalacion.' -ForegroundColor Red
}

# ---------------------------------------------------------------
# 6. Configurar Claude Desktop
# ---------------------------------------------------------------
Write-Host ''
Write-Host '[6/6] Configurando Claude Desktop...' -ForegroundColor Yellow

$serverEntry = @{
    command = $venvPython
    args = @('-m', 'mcp_anydesk_server')
}

if (Test-Path $configPath) {
    try {
        $config = Get-Content $configPath -Raw | ConvertFrom-Json

        if (-not $config.mcpServers) {
            $config | Add-Member -NotePropertyName 'mcpServers' -NotePropertyValue @{}
        }

        if ($config.mcpServers.anydesk) {
            Write-Host '  anydesk ya existe en la configuracion.' -ForegroundColor Yellow
            $overwrite = Read-Host '  Sobrescribir? (s/N)'
            if ($overwrite -eq 's' -or $overwrite -eq 'S') {
                $config.mcpServers.anydesk = $serverEntry
                $config | ConvertTo-Json -Depth 10 | Set-Content $configPath -Encoding UTF8
                Write-Host '  OK: Configuracion actualizada' -ForegroundColor Green
            } else {
                Write-Host '  Configuracion no modificada.' -ForegroundColor Yellow
            }
        } else {
            $config.mcpServers | Add-Member -NotePropertyName 'anydesk' -NotePropertyValue $serverEntry
            $config | ConvertTo-Json -Depth 10 | Set-Content $configPath -Encoding UTF8
            Write-Host '  OK: anydesk agregado a Claude Desktop' -ForegroundColor Green
        }
    } catch {
        Write-Host '  ERROR leyendo configuracion. Revisa docs/SETUP.md para config manual.' -ForegroundColor Red
    }
} else {
    $newConfig = @{
        mcpServers = @{
            anydesk = $serverEntry
        }
    }
    $configDir = Split-Path $configPath
    if (-not (Test-Path $configDir)) {
        New-Item -ItemType Directory -Path $configDir -Force | Out-Null
    }
    $newConfig | ConvertTo-Json -Depth 10 | Set-Content $configPath -Encoding UTF8
    Write-Host "  OK: Configuracion creada en $configPath" -ForegroundColor Green
}

# ---------------------------------------------------------------
# Resumen
# ---------------------------------------------------------------
Write-Host ''
Write-Host '=== Instalacion completa ===' -ForegroundColor Cyan
Write-Host ''
Write-Host '  1. Reinicia Claude Desktop' -ForegroundColor White
Write-Host '  2. Abre una sesion AnyDesk con una maquina remota' -ForegroundColor White
Write-Host '  3. En Claude Desktop, pide: initialize_session' -ForegroundColor White
Write-Host ''
Write-Host '  Docs: docs/SETUP.md | System prompt: docs/SYSTEM_PROMPT.md' -ForegroundColor Gray
Write-Host ''
