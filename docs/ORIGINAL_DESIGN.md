# MCP AnyDesk Server — Diseño Original y Qué Se Rompió

> Este documento describe el diseño que FUNCIONABA (v2), qué cambió v3,
> y dónde se rompió. Es la referencia para reconstruir el proyecto.

---

## 1. El Concepto Central (Nunca Cambió)

Un servidor MCP que permite a Claude Desktop administrar servidores
Windows remotos a través de doble salto AnyDesk:

```
[Claude Desktop] → [PC Local] → [AnyDesk Hop 1] → [AnyDesk Hop 2] → [Servidor Windows]
```

**Restricciones inamovibles:**
- No se puede instalar agentes en el servidor remoto
- No se puede usar clipboard (se rompe en AnyDesk nested)
- No se puede hacer click con mouse (irrecuperable si falla)
- Toda interacción es por inyección de keystrokes

**El ciclo fundamental de operación:**
```
1. Claude propone un comando
2. Operador aprueba
3. Claude inyecta el comando caracter por caracter (SIN presionar Enter)
4. Operador verifica visualmente y presiona Enter
5. Claude lee el output (OCR o screenshot)
6. Claude analiza y propone el siguiente paso
```

El operador SIEMPRE tiene el control. Claude NUNCA presiona Enter.

---

## 2. v2 — El Diseño Que Funcionaba

### 2.1. Archivos y Responsabilidades

```
server.py              → Registra tools MCP, punto de entrada
config.py              → Constantes (delays, paths, umbrales)
window_manager.py      → Enumerar ventanas AnyDesk, pinear una, manejar foco
keyboard_injector.py   → Inyectar keystrokes caracter por caracter
screen_reader.py       → Leer pantalla con OCR (Tesseract)
screenshot.py          → Capturar pantalla como PNG base64
sanitizer.py           → Regex PII layer 2 (Python-side)
command_templates.py   → Wrapper del sanitizer + script bootstrap
session_state.py       → Historial de comandos + audit log
startup_checks.py      → Verificar dependencias al arrancar
SYSTEM_PROMPT.md       → Instrucciones para Claude
```

### 2.2. Tools MCP Registradas

| Tool | Qué Hace |
|------|----------|
| `get_system_status` | Verifica deps (pywin32, mss, tesseract) |
| `select_anydesk_window` | Lista ventanas AnyDesk o pinea una por hwnd |
| `write_to_anydesk` | Inyecta texto caracter por caracter al window pineado |
| `read_from_anydesk` | Lee pantalla con OCR o decodifica Base64 |
| `capture_screenshot` | Devuelve screenshot como PNG base64 |
| `bootstrap_sanitizer` | Inyecta script de sanitización PII en el remoto |
| `get_session_history` | Devuelve últimos N comandos ejecutados |
| `log_step_result` | Registra éxito/fallo de un paso en audit log |

### 2.3. Flujo de Inyección (keyboard_injector.py v2)

Así de simple era:

```python
def write_to_anydesk(command, keystroke_delay_ms=80, focus_delay_ms=500,
                     wrap=True, base64_output=False):

    # 1. Rechazar si tiene newlines
    if "\n" in command or "\r" in command:
        return error

    # 2. Si wrap=True, envolver con sanitizer wrapper
    if wrap:
        command = wrap_command(command)  # agrega ~120 chars de wrapper

    # 3. Obtener foco de la ventana pineada
    focus_and_verify(focus_delay_ms)   # AttachThreadInput + verify

    # 4. Inyectar caracter por caracter
    for char in command:
        keyboard.type(char)
        time.sleep(delay)

    # 5. Verificar que el foco no se perdió
    verify_focus_post()

    return {"status": "injected", "note": "Enter NOT pressed"}
```

**No hay chunks, no hay re-focus, no hay safety check, no hay cooldowns.**
Solo: focus → type → verify. Punto.

### 2.4. Wrapper del Sanitizer (command_templates.py v2)

Dos funciones:

```python
def wrap_command(command):
    """Envuelve un comando con verificación de sanitizer.
    
    Si el archivo s.ps1 existe en TEMP, lo carga y ejecuta el comando
    a través de la función S() que sanitiza el output.
    Si no existe, devuelve [SANITIZER-MISSING].
    """
    return (
        'if(!(Test-Path "$env:TEMP\\s.ps1")){"[SANITIZER-MISSING]"}'
        'else{iex (Get-Content "$env:TEMP\\s.ps1" -Raw); '
        f"S('{command}')}}}"
    )

def wrap_command_base64(command):
    """Igual que wrap_command pero el output sale en Base64 + CRC."""
    # Similar pero con encoding adicional
```

El bootstrap era un SCRIPT MULTILINEA guardado en `BOOTSTRAP_SCRIPT`:

```python
BOOTSTRAP_SCRIPT = r"""[Console]::OutputEncoding = ...
Set-Content -Path "$env:TEMP\s.ps1" -Encoding UTF8 -Value @'
function S($c){
  try {
    $r = iex $c | Out-String
    $r = $r -replace '..regex..','[IP-X]'
    ...más regex...
    $r.TrimEnd()
  } catch {
    "[ERROR] $($_.Exception.Message)"
  }
}
'@"""
```

**Problema conocido:** Este script multilinea no se puede inyectar
con `write_to_anydesk` porque rechaza newlines. En v2 esto se hacía
de forma manual (el operador pegaba el script). Nunca se automatizó
correctamente en v2.

### 2.5. Foco de Ventana (window_manager.py v2)

```python
def focus_and_verify(focus_delay_ms):
    # 1. Verificar que hay ventana pineada
    # 2. AttachThreadInput (bypass foreground lock de Windows)
    # 3. BringWindowToTop + SetForegroundWindow
    # 4. Detach thread input (en finally)
    # 5. Sleep delay
    # 6. Verificar que el foco está en la ventana correcta
```

Esto funcionó para comandos cortos (< 30 chars). Para comandos
largos con wrap=True (~150+ chars), el foco se perdía porque
la inyección tomaba ~16 segundos.

### 2.6. Screenshots (screenshot.py v2)

```python
def capture_screenshot(region_x=0, region_y=0, region_w=0, region_h=0,
                       scale_percent=100):
    # 1. Obtener rect de ventana pineada
    # 2. Capturar con mss
    # 3. Resize si scale_percent < 100
    # 4. Encode a PNG
    # 5. Base64
    # 6. Return {"image_base64": "...", "width": w, "height": h}
```

**Problema:** PNG a escala 100% genera ~200-400KB de base64.
Claude Desktop no puede procesar responses tan grandes y muestra
"conversación demasiado larga" después de 1-2 screenshots.

### 2.7. Sesión y Bootstrap (server.py v2)

El flujo de inicio era MANUAL, paso por paso:

```
1. Claude llama get_system_status → verifica deps
2. Claude llama select_anydesk_window(0) → lista ventanas
3. Claude llama select_anydesk_window(hwnd) → pinea una
4. Operador hace bootstrap manualmente (pega script en terminal)
5. Claude llama write_to_anydesk("hostname", wrap=False) → verifica
6. Sesión lista para trabajar
```

El bootstrap_sanitizer tool existía pero inyectaba el script
multilinea directamente, lo cual fallaba porque write_to_anydesk
rechaza newlines. En la práctica, el operador lo hacía manual.

### 2.8. Resultados de Testing v2

| Test | Resultado | Nota |
|------|-----------|------|
| hostname, wrap=False | ✅ | Funciona perfecto |
| hostname, OCR | ✅ | Confianza 0.34 (baja pero funcional) |
| capture_screenshot | ✅ | Funciona, pero PNG muy grande |
| get_session_history | ✅ | OK |
| Get-Date, wrap=True | ❌ | Focus drift (16s inyección) |
| Get-Date, wrap=False | ✅ | Funciona porque es corto |

**Conclusión v2:** Funciona para comandos cortos sin wrap.
El wrap largo y el bootstrap multilinea son los 2 problemas.

---

## 3. Lo Que v3 Intentó Arreglar

### 3.1. Problemas Reales de v2

1. **Focus drift** — wrap=True genera ~150 chars, toma ~16s, foco se pierde
2. **Bootstrap bloqueado** — script multilinea no pasa por write_to_anydesk
3. **Screenshots enormes** — PNG base64 mata el context de Claude
4. **OCR poco confiable** — 0.34 de confianza, mucho ruido

### 3.2. Lo Que v3 Cambió

| Cambio | Intención | Resultado |
|--------|-----------|-----------|
| Chunked injection (30 chars + re-focus) | Evitar focus drift | Complejidad añadida, inyección se raya |
| EncodedCommand bootstrap | Bypass multilinea PS5 | 800-1000 chars → loops infinitos |
| Bootstrap split en 9 pasos | Reducir tamaño de inyección | Funciona pero 9 ciclos inject→Enter |
| initialize_session todo-en-uno | Reducir setup a 1 call | Incluía bootstrap → loops → se separó |
| safety.py blocklist | Bloquear Remove-VM etc server-side | OK pero agrega check en cada inyección |
| JPEG + caps de tamaño | Reducir screenshots | Cap no funciona (33KB en vez de 15KB) |
| wrap=True valida bootstrap | Proteger contra wrapper sin sanitizer | BLOQUEA en vez de fallback a raw |
| sanitizer_bootstrapped flag | Trackear si bootstrap se hizo | Nunca se actualiza a True |
| Named sessions | Multi-servidor | Agrega complejidad |
| Recipes | Comandos predefinidos | Útil pero no crítico |
| Diagnostic mode | Saltar aprobación read-only | Solo cambio de prompt |
| Wrapper comprimido (gc alias) | Menos chars inyectados | OK en principio |
| Adaptive OCR | Retry con zoom | Complejidad en screen_reader |

### 3.3. Dónde Se Rompió

**Bug 1 — Inyección se raya:**
El chunked injection añade lógica de re-focus entre cada chunk
de 30 caracteres. Si cualquier re-focus falla, reporta partial
injection. El LLM ve el error y reintenta, lo que genera basura
en la terminal. La complejidad del chunking introdujo más puntos
de fallo que los que resolvió.

**Bug 2 — Bootstrap causa loops:**
initialize_session incluía bootstrap internamente. El bootstrap
(EncodedCommand ~800 chars) tardaba ~80 segundos. Claude Desktop
hacía timeout, el LLM reintentaba, se inyectaba otro bootstrap
encima. Loop infinito de basura. Se separó después, pero el daño
ya estaba hecho en el diseño.

**Bug 3 — sanitizer_bootstrapped nunca es True:**
El flag `session.sanitizer_bootstrapped` debería ponerse True
después de un bootstrap exitoso. Pero log_step_result no tiene
la lógica para detectar "BOOTSTRAP-OK" en las notes y actualizar
el flag. Resultado: wrap=True queda permanentemente bloqueado.

**Bug 4 — wrap=True bloquea en vez de funcionar:**
Si sanitizer_bootstrapped=False y wrap=True, v3 devuelve error
y NO inyecta nada. El diseño esperado era: inyectar raw con warning.
Pero se implementó como bloqueo duro.

**Bug 5 — Cap de screenshots no funciona:**
SCREENSHOT_MAX_BASE64_KB=15 existe en config pero el loop de
reducción de quality no se implementó (o el cálculo bytes vs
base64 está mal). Screenshots siguen siendo ~33KB.

**Bug 6 — scale_percent=100 malogra la imagen:**
Algún resize se aplica cuando no debería (scale=100 debería
significar "no resize"), generando dimensiones inválidas.

---

## 4. Inventario de Estado Actual

Lo que FUNCIONA ahora:
- get_system_status ✅
- select_anydesk_window ✅
- write_to_anydesk wrap=False (comandos cortos) ✅
- read_from_anydesk OCR (baja confianza) ✅
- capture_screenshot (pero demasiado grande) ⚠️
- get_session_history ✅
- Blocklist de comandos destructivos ✅
- send_cancel (Ctrl+C) ✅
- Bootstrap en 9 pasos manuales ✅

Lo que NO funciona:
- write_to_anydesk wrap=True ❌ (bloquea por sanitizer_bootstrapped=False)
- sanitizer_bootstrapped flag ❌ (nunca se actualiza)
- Cap de tamaño en screenshots ❌ (33KB en vez de 15KB)
- scale_percent=100 ❌ (imagen corrupta)
- base64_output mode ❌ (cascadea del wrap=True bloqueado)

---

## 5. Principios de Diseño Que Deben Preservarse

### 5.1. Simplicidad ante todo
El inyector v2 era ~40 líneas de código. Funcionaba para comandos
cortos. La complejidad de chunks, re-focus, safety checks, cooldowns
añadió ~150 líneas y más bugs de los que resolvió.

### 5.2. Fallo visible, no silencioso
Si algo falla, Claude debe ver un error claro con qué pasó y cuánto
se inyectó. No reintentar automáticamente. No recuperar silenciosamente.

### 5.3. Operador siempre en control
- Enter siempre manual
- Bootstrap siempre opcional (operador decide)
- Comandos destructivos siempre requieren confirmación
- wrap=True es un convenience, nunca un requisito

### 5.4. Tools simples, una responsabilidad
Cada tool hace UNA cosa. initialize_session no debe hacer bootstrap.
write_to_anydesk no debe decidir si bloquear por falta de sanitizer.

### 5.5. El response debe ser pequeño
Si un tool response es > 20KB, Claude Desktop no lo procesa bien.
Screenshots DEBEN tener un cap funcional.

---

## 6. Dependencias y Entorno

```
Python:        3.13.2 (requiere WindowsSelectorEventLoopPolicy fix)
MCP SDK:       mcp[cli] (FastMCP, transport stdio)
pywin32:       para window management (SetForegroundWindow, EnumWindows)
pynput:        para keyboard injection (Controller.type)
mss:           para screen capture
opencv-python: para image processing (resize, encode)
pytesseract:   para OCR
Tesseract:     5.5.0 (instalado aparte, debe estar en PATH)
```

**Fix crítico Python 3.13:**
```python
# En server.py, ANTES de cualquier import de asyncio:
import sys
if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
```

**Config Claude Desktop:**
```json
{
  "mcpServers": {
    "anydesk": {
      "command": "C:\\dev\\mcp_anydesk_server\\.venv\\Scripts\\python.exe",
      "args": ["-m", "mcp_anydesk_server"]
    }
  }
}
```

---

## 7. Referencia de Código v2 Funcional

### keyboard_injector.py (v2 — el que funcionaba)

```python
"""Keyboard injection into the pinned AnyDesk window."""

from __future__ import annotations
import time
from pynput.keyboard import Controller as KbController
from command_templates import wrap_command, wrap_command_base64
from config import (DEFAULT_KEYSTROKE_DELAY_MS, MIN_KEYSTROKE_DELAY_MS,
                    DEFAULT_FOCUS_DELAY_MS)
from window_manager import focus_and_verify, verify_focus_post

_keyboard = KbController()

def write_to_anydesk(command, keystroke_delay_ms=DEFAULT_KEYSTROKE_DELAY_MS,
                     focus_delay_ms=DEFAULT_FOCUS_DELAY_MS,
                     wrap=True, base64_output=False):
    original_command = command

    # Rechazar multilinea
    if any(ch in command for ch in ("\n", "\r")):
        return {"status": "error", "char_count": 0,
                "original_command": original_command, "wrapped": False,
                "elapsed_ms": 0,
                "note": "Multi-line input rejected."}

    # Limpiar stray sequences
    command = command.replace("\n", "").replace("\r", "")
    command = command.replace("`n", "").replace("`r", "")

    if not command:
        return {"status": "error", "char_count": 0,
                "original_command": original_command, "wrapped": False,
                "elapsed_ms": 0, "note": "Empty command."}

    # Auto-wrap
    was_wrapped = False
    if wrap:
        if base64_output:
            command = wrap_command_base64(command)
        else:
            command = wrap_command(command)
        was_wrapped = True

    # Enforce minimum delay
    keystroke_delay_ms = max(keystroke_delay_ms, MIN_KEYSTROKE_DELAY_MS)
    delay_s = keystroke_delay_ms / 1000.0

    # Focus
    try:
        focus_and_verify(focus_delay_ms)
    except RuntimeError as exc:
        return {"status": "error", "char_count": 0,
                "original_command": original_command,
                "wrapped": was_wrapped, "elapsed_ms": 0,
                "note": str(exc)}

    # Inject
    start = time.perf_counter()
    for char in command:
        _keyboard.type(char)
        time.sleep(delay_s)
    elapsed_ms = int((time.perf_counter() - start) * 1000)

    # Post-verify
    try:
        verify_focus_post()
    except RuntimeError as exc:
        return {"status": "error", "char_count": len(command),
                "original_command": original_command,
                "wrapped": was_wrapped, "elapsed_ms": elapsed_ms,
                "note": f"Focus drifted: {exc}"}

    return {"status": "injected", "char_count": len(command),
            "original_command": original_command,
            "wrapped": was_wrapped, "elapsed_ms": elapsed_ms,
            "note": "Enter NOT pressed — awaiting operator confirmation"}
```

### command_templates.py (v2)

```python
"""PowerShell command templates."""

from config import BOOTSTRAP_FILENAME, SANITIZER_SENTINEL

BOOTSTRAP_SCRIPT = r"""[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Content -Path "$env:TEMP\{filename}" -Encoding UTF8 -Value @'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
function S($c){{
  try {{
    $r = iex $c | Out-String
    $r = $r -replace '(?<!\d)(10\.\d{{1,3}}\.\d{{1,3}}\.\d{{1,3}})(?!\d)','[IP-X]'
    $r = $r -replace '(?<!\d)(172\.(1[6-9]|2\d|3[01])\.\d{{1,3}}\.\d{{1,3}})(?!\d)','[IP-X]'
    $r = $r -replace '(?<!\d)(192\.168\.\d{{1,3}}\.\d{{1,3}})(?!\d)','[IP-X]'
    $r = $r -replace '(?i)fe80:[0-9a-f:]+(%\w+)?','[IPv6-X]'
    $r = $r -replace '[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{{2,}}','[EMAIL-X]'
    $r = $r -replace '(?i)[A-Z0-9_-]+\\[A-Z0-9_.-]+','[ACCT-X]'
    $r = $r -replace '(?i)[a-z0-9._-]+@[a-z0-9.-]+\.local','[UPN-X]'
    $r = $r -replace 'S-1-\d+-\d+(-\d+){{1,}}','[SID-X]'
    $r = $r -replace '([0-9A-Fa-f]{{2}}[:-]){{5}}[0-9A-Fa-f]{{2}}','[MAC-X]'
    $r = $r -replace '\\\\[A-Za-z0-9_.-]+\\','\\[SRV-X]\'
    $r.TrimEnd()
  }} catch {{
    "[ERROR] $($_.Exception.Message)"
  }}
}}
'@""".format(filename=BOOTSTRAP_FILENAME)

_COMMAND_WRAPPER = (
    'if(!(Test-Path "$env:TEMP\\{filename}")){{"{sentinel}"}}'
    'else{{iex (Get-Content "$env:TEMP\\{filename}" -Raw); '
    "S('{command}')}}"
)

def bootstrap():
    return BOOTSTRAP_SCRIPT

def wrap_command(command):
    escaped = command.replace("'", "''")
    return _COMMAND_WRAPPER.format(
        filename=BOOTSTRAP_FILENAME,
        sentinel=SANITIZER_SENTINEL,
        command=escaped,
    )
```

### screenshot.py (v2)

```python
"""Screenshot capture — PNG base64."""

def capture_screenshot(region_x=0, region_y=0, region_w=0, region_h=0,
                       scale_percent=100):
    hwnd = get_pinned_hwnd()
    # Determine region (full window or sub-region)
    # Capture with mss
    # BGRA → BGR
    # Resize if scale_percent < 100
    # Encode to PNG
    # Base64
    # Return dict with image_base64, width, height
```

---

## 8. Qué Debe Hacer Un "v3 Limpio"

### Arreglar solo lo que está roto, no reescribir todo:

**Fix 1 — Screenshots funcionales:**
- JPEG en vez de PNG (calidad 35%)
- Scale default 25%
- Cap máximo de ancho: 640px
- Cap máximo de base64: 15KB (loop de reducción de quality)
- scale_percent=100 NO debe hacer resize innecesario

**Fix 2 — Bootstrap funcional:**
- Split en pasos cortos (cada uno < 120 chars)
- Cada paso es un Set-Content/Add-Content separado
- bootstrap_sanitizer retorna la LISTA de pasos (no inyecta)
- El LLM inyecta cada paso individualmente
- Último paso verifica con Test-Path → [BOOTSTRAP-OK]
- Bootstrap es 100% opcional, operador decide

**Fix 3 — wrap=True nunca bloquea:**
- Si sanitizer NO está activo: inyectar raw + warning
- Si sanitizer SÍ está activo: inyectar wrapped
- NUNCA devolver error por falta de sanitizer

**Fix 4 — sanitizer_bootstrapped se actualiza:**
- En log_step_result: si notes contiene "BOOTSTRAP" y success=True
  → session.sanitizer_bootstrapped = True

**Fix 5 — Focus drift (el problema original):**
- Opción conservadora: NO chunk. Dejar la inyección simple de v2.
  El wrapper comprimido (~80 chars en vez de ~120) reduce el tiempo.
  Un comando wrapped de ~100 chars toma ~8 segundos — aceptable.
- Si 8 segundos sigue causando drift: ENTONCES implementar chunks
  como mejora incremental, no como reescritura.

**Lo que SÍ vale de v3:**
- safety.py (blocklist de destructivos) — funciona, útil, mantener
- send_cancel (Ctrl+C) — funciona, mantener
- initialize_session (SIN bootstrap) — mantener
- Recipes — útil, mantener
- Named sessions — útil, mantener
- Audit log — funciona, mantener
