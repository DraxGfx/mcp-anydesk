# MCP AnyDesk Server

Servidor MCP para administración remota de servidores Windows a través de
sesiones AnyDesk anidadas (Local → AnyDesk → AnyDesk → Servidor). Diseñado
para entornos donde no se puede instalar agentes ni herramientas adicionales
en el servidor remoto.

---

## Características

- **Inyección de comandos PowerShell por keystroke** — chunked (30 chars/chunk)
  con re-verificación de foco entre chunks. Usa VK_PACKET (KEYEVENTF_UNICODE)
  para independencia total del layout de teclado
- **Lectura de output por OCR** — Tesseract con preprocesado OpenCV, retry
  adaptativo 4× con zoom cuando la confianza es baja, detección de output
  truncado
- **Modo Base64** — wrapper lite (sin bootstrap) y full (con PII sanitization).
  Incluye validación CRC SHA256
- **Screenshots comprimidos** — JPEG con cap garantizado de 15 KB (reducción
  automática de calidad + resize de emergencia)
- **Sanitización PII opcional** — dual-layer: PowerShell (s.ps1 en el remoto) +
  Python post-OCR. Redacta IPs RFC1918, emails, cuentas DOMAIN\user, SIDs
- **Validación de quotes** — detecta comillas desbalanceadas antes de inyectar
  para prevenir el estado `>>` de continuación de PowerShell
- **Sesiones nombradas** — múltiples ventanas AnyDesk pinneadas simultáneamente
  (pin_session / switch_session / list_sessions)
- **Recipes predefinidos** — secuencias de comandos para health check, inventario
  de VMs, red, servicios, eventos de error, VMware
- **Auditoría completa** — historial en memoria + log append-only en disco +
  export a markdown
- Compatible con **Hyper-V** y **VMware PowerCLI**

---

## Requisitos

- Windows 10/11 o Windows Server 2019+
- Python 3.11+ (testeado en 3.13)
- AnyDesk instalado y con al menos una sesión activa
- [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) instalado y
  en PATH
- Claude Desktop con soporte MCP

---

## Instalación

### Opción A — Setup automático (recomendado)

#### 1. Clonar el repositorio

```bash
git clone <repo-url>
cd mcp-anydesk-server
```

#### 2. Instalar Tesseract OCR

Descargar e instalar desde:
https://github.com/UB-Mannheim/tesseract/wiki

Agregar la carpeta de instalación al PATH del sistema
(típicamente `C:\Program Files\Tesseract-OCR`).

#### 3. Ejecutar el instalador

Doble clic en `install.bat` — o desde PowerShell:

```powershell
.\setup.ps1
```

El script verifica Python, crea el entorno virtual, instala el paquete con
todas sus dependencias y parchea `claude_desktop_config.json` automáticamente.

Para omitir el parcheo automático de Claude Desktop:

```powershell
.\setup.ps1 -SkipClaudeConfig
```

#### 4. Reiniciar Claude Desktop

Cerrar y reabrir Claude Desktop. El servidor aparecerá disponible
en la interfaz de herramientas.

---

### Opción B — Instalación con pip

```bash
git clone <repo-url>
cd mcp-anydesk-server
python -m venv .venv
.venv\Scripts\activate
pip install .
```

Verificar Tesseract en PATH:

```powershell
tesseract --version
```

Configurar Claude Desktop — editar `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "anydesk": {
      "command": "C:\\path\\to\\mcp-anydesk-server\\.venv\\Scripts\\python.exe",
      "args": ["-m", "mcp_anydesk_server"]
    }
  }
}
```

Reiniciar Claude Desktop.

---

## Configuración

Los defaults están en `mcp_anydesk_server/config.py`. Los más relevantes:

| Constante | Default | Descripción |
|-----------|---------|-------------|
| `SCREENSHOT_JPEG_QUALITY` | 35 | Calidad JPEG inicial (1-100) |
| `SCREENSHOT_DEFAULT_SCALE` | 25 | Escala de captura (%) |
| `SCREENSHOT_MAX_WIDTH` | 640 | Ancho máximo en píxeles |
| `SCREENSHOT_MAX_BASE64_KB` | 15 | Límite de tamaño de imagen |
| `CHUNK_SIZE` | 30 | Caracteres por chunk de inyección |
| `DEFAULT_KEYSTROKE_DELAY_MS` | 80 | Delay entre teclas (ms) |
| `DEFAULT_FOCUS_DELAY_MS` | 500 | Delay tras enfocar ventana (ms) |
| `OCR_DENOISE_H` | 13 | Fuerza de denoising OCR |
| `AUDIT_LOG_DIR` | `~/.mcp_anydesk/logs` | Directorio de logs |

---

## Uso

### Flujo básico

1. Abrir AnyDesk y conectar a la sesión remota
2. Abrir Claude Desktop
3. Claude llama `initialize_session` → pinna la ventana AnyDesk activa
4. (Opcional) Decir "bootstrap" → Claude activa sanitización PII en 9 pasos
5. Trabajar: Claude inyecta comandos, el operador presiona Enter, Claude lee el output

### Regla fundamental

**Claude nunca presiona Enter.** El operador siempre confirma manualmente cada
comando antes de ejecutarlo. Esto es intencional.

### Sanitización PII

Por defecto está desactivada. Para activarla decir "bootstrap" al inicio de la
sesión. Claude inyectará 9 comandos que crean un script PowerShell en el remoto
que redacta IPs, emails, cuentas y SIDs antes de devolver el output.

---

## Arquitectura

```
Claude Desktop
     │
     │ MCP stdio
     ▼
┌─────────────────────────────────────────────────────┐
│ mcp_anydesk_server/  (paquete Python, 17 tools)     │
│  ├── server.py           entry point FastMCP        │
│  ├── session_state.py    historial + audit log      │
│  ├── keyboard_injector.py  chunked keystroke VK_PKT │
│  ├── screen_reader.py    OCR + base64 decode        │
│  ├── screenshot.py       JPEG capture               │
│  ├── window_manager.py   named sessions             │
│  ├── command_templates.py  wrap + bootstrap cmds     │
│  ├── sanitizer.py        PII redaction (post-OCR)   │
│  ├── recipes.py          secuencias predefinidas     │
│  ├── config.py           constantes configurables   │
│  └── startup_checks.py   preflight                  │
└─────────────────────────────────────────────────────┘
     │ pywin32 / pynput / mss / cv2 / pytesseract
     ▼
AnyDesk (local)  →  AnyDesk (remoto)  →  Servidor Windows
```

---

## Seguridad

- **No auto-Enter**: ninguna herramienta presiona Enter. El operador siempre
  confirma antes de ejecutar.
- **Validación de quotes**: las comillas desbalanceadas se detectan antes de
  la inyección, previniendo el estado `>>` de PowerShell.
- **Sanitización PII dual-layer**: el script PowerShell en el remoto redacta
  datos sensibles antes de que lleguen al OCR; Python redacta lo que OCR pueda
  haber captado de todas formas.
- **Audit log append-only**: cada comando inyectado queda registrado en disco
  con timestamp, resultado y notas del operador.
- **Sin clipboard**: la inyección es siempre por keystrokes. El portapapeles
  no funciona de forma fiable en sesiones AnyDesk anidadas.

---

## Troubleshooting

**`OSError: [Errno 22]` al iniciar en Python 3.13 + Windows**
Resuelto internamente con `WindowsSelectorEventLoopPolicy`. Si aparece,
verificar que `server.py` tiene el bloque de política al inicio.

**Tesseract no encontrado**
Agregar `C:\Program Files\Tesseract-OCR` (o la ruta de instalación) a PATH
y reiniciar la terminal.

**No se detecta ventana AnyDesk**
Verificar que AnyDesk tiene al menos una sesión remota activa (no solo la
aplicación abierta). Usar `select_anydesk_window()` sin argumentos para
listar ventanas disponibles.

**Focus drift — caracteres van a la ventana equivocada**
Aumentar `DEFAULT_FOCUS_DELAY_MS` y/o `CHUNK_REFOCUS_DELAY_MS` en `config.py`.
Asegurarse de no mover el mouse ni cambiar de ventana durante la inyección.

**OCR con confianza baja (< 0.4)**
`read_from_anydesk` reintenta automáticamente con zoom 4×. Si persiste, usar
`capture_screenshot` para ver el estado visual y diagnosticar manualmente.
Considerar aumentar el tamaño del terminal remoto o usar Consolas 14pt+.

**Screenshots grandes (> 15 KB)**
El loop de reducción de calidad debería garantizar el límite. Si persiste,
reducir `SCREENSHOT_DEFAULT_SCALE` o `SCREENSHOT_MAX_WIDTH` en `config.py`.

**`[SANITIZER-MISSING]` en el output**
El script `s.ps1` no existe en el remoto o fue eliminado. Decir "bootstrap"
para re-ejecutar la secuencia de 9 pasos.
