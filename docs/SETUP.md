# Setup — MCP AnyDesk Server

## Instalacion rapida

```powershell
git clone <repo-url>
cd mcp-anydesk
powershell -ExecutionPolicy Bypass -File install.ps1
```

El instalador:
1. Verifica Python 3.11+
2. Crea el entorno virtual (.venv)
3. Instala todas las dependencias
4. Verifica Tesseract OCR (necesario para lectura de pantalla)
5. Ejecuta preflight check (muestra estado de cada componente)
6. Configura Claude Desktop automaticamente

Al finalizar, reinicia Claude Desktop.

## Requisitos

- Windows 10/11 o Windows Server 2019+
- Python 3.11+ (testeado en 3.13)
- AnyDesk instalado con al menos una sesion remota activa
- Claude Desktop con soporte MCP
- [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) (opcional pero recomendado)

### Tesseract OCR

Sin Tesseract, la inyeccion de teclado y screenshots funcionan, pero la lectura
de pantalla (OCR) no estara disponible.

Instalar:

```powershell
winget install UB-Mannheim.TesseractOCR
```

O descargar desde: https://github.com/UB-Mannheim/tesseract/wiki

Agregar la carpeta de instalacion al PATH (tipicamente `C:\Program Files\Tesseract-OCR`).

## Instalacion manual

Si prefieres no usar el instalador:

```bash
python -m venv .venv
.venv\Scripts\activate
.venv\Scripts\pip.exe install -e .
```

Editar `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "anydesk": {
      "command": "C:\\ruta\\a\\mcp-anydesk\\.venv\\Scripts\\python.exe",
      "args": ["-m", "mcp_anydesk_server"]
    }
  }
}
```

> Reemplazar `C:\\ruta\\a\\mcp-anydesk` con la ruta real del proyecto.

## Verificacion

1. Reiniciar Claude Desktop
2. Llamar `get_version` — debe retornar la version instalada
3. Llamar `get_system_status` — muestra el estado de cada dependencia
4. Llamar `initialize_session` para pinear la ventana AnyDesk activa

## System prompt

Copiar el contenido de `docs/SYSTEM_PROMPT.md` como system prompt en Claude Desktop
para que el modelo siga las reglas de seguridad y el flujo de trabajo correcto.
