#Requires -Version 5.1
<#
.SYNOPSIS
    Setup para MCP AnyDesk Server.

.DESCRIPTION
    Verifica Python >= 3.11, crea el entorno virtual, instala el paquete
    con todas sus dependencias, verifica Tesseract OCR y parchea
    claude_desktop_config.json.

.PARAMETER SkipClaudeConfig
    Si se especifica, omite el parcheo de claude_desktop_config.json.

.EXAMPLE
    .\setup.ps1
    .\setup.ps1 -SkipClaudeConfig
#>

param(
    [switch]$SkipClaudeConfig
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot      = $PSScriptRoot
$VenvDir          = Join-Path $ProjectRoot ".venv"
$VenvPython       = Join-Path $VenvDir "Scripts\python.exe"
$McpAnydesk       = Join-Path $VenvDir "Scripts\mcp-anydesk.exe"
$ClaudeConfigPath = Join-Path $env:APPDATA "Claude\claude_desktop_config.json"

# ---------------------------------------------------------------------------
# Helpers de output
# ---------------------------------------------------------------------------
function Write-Step { param($msg) Write-Host "  >> $msg" -ForegroundColor Cyan }
function Write-OK   { param($msg) Write-Host "  OK  $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "  !!  $msg" -ForegroundColor Yellow }
function Write-Fail { param($msg) Write-Host "  XX  $msg" -ForegroundColor Red }

Write-Host ""
Write-Host "=== MCP AnyDesk Server - Setup ===" -ForegroundColor White
Write-Host ""

# ---------------------------------------------------------------------------
# 1. Python >= 3.11
# ---------------------------------------------------------------------------
Write-Step "Verificando Python..."
try {
    $pyver = & python --version 2>&1
    if ($pyver -match "Python (\d+)\.(\d+)") {
        $major = [int]$Matches[1]
        $minor = [int]$Matches[2]
        if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 11)) {
            Write-Fail "Python $major.$minor encontrado. Se requiere 3.11+."
            Write-Warn "Descargar desde: https://www.python.org/downloads/"
            exit 1
        }
        Write-OK "$pyver"
    } else {
        Write-Fail "No se pudo determinar la version de Python."
        exit 1
    }
} catch {
    Write-Fail "Python no encontrado en PATH."
    Write-Warn "Descargar desde: https://www.python.org/downloads/"
    exit 1
}

# ---------------------------------------------------------------------------
# 2. Entorno virtual
# ---------------------------------------------------------------------------
Write-Step "Preparando entorno virtual..."
if (Test-Path $VenvPython) {
    Write-OK "Entorno virtual ya existe: $VenvDir"
} else {
    python -m venv $VenvDir
    if (-not (Test-Path $VenvPython)) {
        Write-Fail "No se pudo crear el entorno virtual en $VenvDir"
        exit 1
    }
    Write-OK "Entorno virtual creado: $VenvDir"
}

# ---------------------------------------------------------------------------
# 3. Instalar paquete (pip install .)
# ---------------------------------------------------------------------------
Write-Step "Instalando MCP AnyDesk Server (puede tardar un momento)..."
& $VenvPython -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) { Write-Fail "pip upgrade fallo."; exit 1 }

& $VenvPython -m pip install $ProjectRoot --quiet
if ($LASTEXITCODE -ne 0) { Write-Fail "pip install fallo."; exit 1 }
Write-OK "Paquete instalado"

# pywin32 requiere un paso de post-instalacion para registrar sus DLLs
Write-Step "Ejecutando post-instalacion de pywin32..."
$postInstall = Join-Path $VenvDir "Scripts\pywin32_postinstall.py"
if (Test-Path $postInstall) {
    & $VenvPython $postInstall -install 2>&1 | Out-Null
    Write-OK "pywin32 post-instalacion completada"
} else {
    Write-Warn "pywin32_postinstall.py no encontrado (puede no ser necesario en esta version)"
}

# ---------------------------------------------------------------------------
# 4. Tesseract OCR
# ---------------------------------------------------------------------------
Write-Step "Verificando Tesseract OCR..."
$tessCmd = Get-Command tesseract -ErrorAction SilentlyContinue
if ($tessCmd) {
    $tessVer = & tesseract --version 2>&1 | Select-Object -First 1
    Write-OK "$tessVer"
} else {
    Write-Warn "Tesseract no encontrado en PATH."
    Write-Warn "Descargar desde: https://github.com/UB-Mannheim/tesseract/wiki"
    Write-Warn "Tras instalar, agregar su carpeta al PATH del sistema y reiniciar."
    Write-Warn "Sin Tesseract: read_from_anydesk y check_health no funcionaran."
}

# ---------------------------------------------------------------------------
# 5. Parchear claude_desktop_config.json
# ---------------------------------------------------------------------------
if (-not $SkipClaudeConfig) {
    Write-Step "Parcheando claude_desktop_config.json..."

    $mcpEntry = [PSCustomObject]@{
        command = $VenvPython
        args    = @("-m", "mcp_anydesk_server")
    }

    # Leer config existente o crear una nueva
    if (Test-Path $ClaudeConfigPath) {
        try {
            $raw = Get-Content $ClaudeConfigPath -Raw -Encoding UTF8
            $cfg = $raw | ConvertFrom-Json
        } catch {
            Write-Warn "No se pudo parsear el JSON existente. Se creara uno nuevo."
            $cfg = [PSCustomObject]@{}
        }
    } else {
        $claudeDir = Split-Path $ClaudeConfigPath
        if (-not (Test-Path $claudeDir)) {
            New-Item -ItemType Directory -Path $claudeDir -Force | Out-Null
        }
        $cfg = [PSCustomObject]@{}
    }

    # Asegurar que existe la clave mcpServers
    if (-not (Get-Member -InputObject $cfg -Name "mcpServers" -MemberType NoteProperty -ErrorAction SilentlyContinue)) {
        $cfg | Add-Member -MemberType NoteProperty -Name "mcpServers" -Value ([PSCustomObject]@{})
    }

    # Agregar o sobreescribir la entrada "anydesk"
    if (Get-Member -InputObject $cfg.mcpServers -Name "anydesk" -MemberType NoteProperty -ErrorAction SilentlyContinue) {
        $cfg.mcpServers.anydesk = $mcpEntry
    } else {
        $cfg.mcpServers | Add-Member -MemberType NoteProperty -Name "anydesk" -Value $mcpEntry
    }

    $cfg | ConvertTo-Json -Depth 10 | Set-Content $ClaudeConfigPath -Encoding UTF8
    Write-OK "Config guardada: $ClaudeConfigPath"
} else {
    Write-Warn "Parcheo de Claude Desktop omitido (-SkipClaudeConfig)"
    Write-Warn "Agregar manualmente a claude_desktop_config.json:"
    Write-Host ""
    Write-Host '    "mcpServers": {'                                     -ForegroundColor DarkGray
    Write-Host '      "anydesk": {'                                      -ForegroundColor DarkGray
    Write-Host ('        "command": "' + $VenvPython + '",')             -ForegroundColor DarkGray
    Write-Host '        "args": ["-m", "mcp_anydesk_server"]'            -ForegroundColor DarkGray
    Write-Host '      }'                                                  -ForegroundColor DarkGray
    Write-Host '    }'                                                    -ForegroundColor DarkGray
    Write-Host ""
}

# ---------------------------------------------------------------------------
# Resumen
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== Setup completo ===" -ForegroundColor Green
Write-Host ""
Write-Host "  Python:     $VenvPython"
Write-Host "  Comando:    mcp-anydesk (o: python -m mcp_anydesk_server)"
if (-not $SkipClaudeConfig) {
    Write-Host "  Config:     $ClaudeConfigPath"
}
Write-Host ""
Write-Host "  Proximo paso: Reiniciar Claude Desktop." -ForegroundColor White
Write-Host ""
