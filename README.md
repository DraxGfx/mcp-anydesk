# MCP AnyDesk Server

Servidor MCP para administracion remota de servidores Windows a traves de
sesiones AnyDesk anidadas (Local -> AnyDesk -> AnyDesk -> Servidor).

Inyecta comandos PowerShell como keystrokes y lee la salida via OCR.
Diseñado para entornos donde no se puede instalar agentes en el servidor remoto.

**Claude nunca presiona Enter.** El operador siempre confirma manualmente.

---

## Instalacion rapida

```powershell
git clone https://github.com/DraxGfx/mcp-anydesk.git
cd mcp-anydesk
powershell -ExecutionPolicy Bypass -File install.ps1
```

El instalador verifica Python, crea el entorno virtual, instala dependencias,
corre preflight check y configura Claude Desktop automaticamente.

Al finalizar, reinicia Claude Desktop.

---

## Instalacion manual

```powershell
git clone https://github.com/DraxGfx/mcp-anydesk.git
cd mcp-anydesk
python -m venv .venv
.venv\Scripts\pip.exe install -e .
```

### Tesseract OCR (opcional, necesario para lectura de pantalla)

```powershell
winget install UB-Mannheim.TesseractOCR
```

O descargar desde: https://github.com/UB-Mannheim/tesseract/wiki

### Configurar Claude Desktop

Editar `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "anydesk": {
      "command": "C:\\dev\\mcp-anydesk\\.venv\\Scripts\\python.exe",
      "args": ["-m", "mcp_anydesk_server"]
    }
  }
}
```

> Reemplazar `C:\\dev\\mcp-anydesk` con la ruta real del proyecto.

Reiniciar Claude Desktop.

---

## Caracteristicas

- **Inyeccion via KEYEVENTF_UNICODE** — layout-independent, funciona con cualquier
  teclado (EN-US, ES-PE, etc.). Caracter por caracter via SendInput
- **Lectura OCR** — Tesseract + OpenCV con retry adaptativo y zoom 4x
- **Modo Base64** — wrapper lite (sin bootstrap) y full (con PII sanitization)
  con validacion CRC SHA256
- **Screenshots JPEG** — cap de 15KB, guardados a disco (sin base64 en response
  para evitar lag en Claude Desktop)
- **Sanitizacion PII opcional** — dual-layer: PowerShell remoto + Python post-OCR.
  Redacta IPs, emails, DOMAIN\user, SIDs
- **Validacion de quotes** — detecta comillas desbalanceadas antes de inyectar
- **Sesiones nombradas** — multiples ventanas AnyDesk pinneadas simultaneamente
- **Recipes** — secuencias predefinidas para health check, VMs, red, servicios
- **Auditoria** — historial en memoria + log en disco + export a markdown
- Compatible con **Hyper-V** y **VMware PowerCLI**

---

## Uso

1. Abrir AnyDesk y conectar a la sesion remota
2. Abrir Claude Desktop
3. Claude llama `initialize_session` — pinnea la ventana AnyDesk
4. (Opcional) Decir "bootstrap" — activa sanitizacion PII en 9 pasos
5. Trabajar: Claude inyecta comandos, el operador presiona Enter, Claude lee el output

### Tools disponibles (18)

| Tool | Descripcion |
|------|-------------|
| `get_version` | Version del MCP server |
| `get_system_status` | Estado de dependencias y capacidades |
| `initialize_session` | Pinnear ventana AnyDesk |
| `write_to_anydesk` | Inyectar keystrokes (UNICODE) |
| `read_from_anydesk` | Leer pantalla (OCR o Base64) |
| `capture_screenshot` | Screenshot JPEG guardado a disco |
| `send_cancel` | Enfocar AnyDesk para Ctrl+C manual |
| `bootstrap_sanitizer` | Obtener los 9 pasos de bootstrap |
| `log_step_result` | Registrar resultado de un paso |
| `get_session_history` | Historial de comandos |
| `export_session` | Exportar sesion a markdown |
| `check_health` | Verificar estado de la sesion |
| `list_recipes` | Listar recetas disponibles |
| `get_recipe` | Obtener comandos de una receta |
| `select_anydesk_window` | Listar/seleccionar ventanas |
| `pin_session` | Pinnear ventana con nombre |
| `switch_session` | Cambiar sesion activa |
| `list_sessions` | Listar sesiones pinneadas |

---

## Limitaciones conocidas

- **Ctrl+C programatico no funciona** — AnyDesk no reenvía Ctrl+C de SendInput
  como señal de interrupcion. `send_cancel` enfoca la ventana para que el operador
  presione Ctrl+C manualmente.
- **OCR no es perfecto** — caracteres pueden leerse mal en resoluciones bajas.
  Usar `capture_screenshot` o modo Base64 para output critico.
- **Focus drift** — no mover el mouse ni cambiar de ventana durante la inyeccion.

---

## Seguridad

- **No auto-Enter**: ninguna herramienta presiona Enter
- **Validacion de quotes**: comillas desbalanceadas se detectan antes de inyectar
- **Sanitizacion PII dual-layer**: PowerShell remoto + Python post-OCR
- **Audit log append-only**: cada comando queda registrado en disco
- **Sin clipboard**: inyeccion siempre por keystrokes

---

## Documentacion

| Archivo | Contenido |
|---------|-----------|
| `docs/SETUP.md` | Guia de instalacion detallada |
| `docs/SYSTEM_PROMPT.md` | System prompt para Claude Desktop |
| `docs/TIPS.md` | Tips de uso (split mode, version check) |
| `docs/testing/TEST_PROMPT.md` | Prompt de validacion (11 tests) |
| `docs/changelog/` | Historial de cambios por sesion |

---

## Licencia

MIT
