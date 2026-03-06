# Setup — MCP AnyDesk Server

## Requisitos

- Windows 10/11 o Windows Server 2019+
- Python 3.11+ (testeado en 3.13)
- AnyDesk instalado con al menos una sesion remota activa
- [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) instalado y en PATH
- Claude Desktop con soporte MCP

## Instalacion

```bash
git clone <repo-url>
cd mcp-anydesk
python -m venv .venv
.venv\Scripts\activate
pip install .
```

Verificar Tesseract en PATH:

```powershell
tesseract --version
```

Si no esta instalado:

```powershell
winget install UB-Mannheim.TesseractOCR
```

O descargar desde: https://github.com/UB-Mannheim/tesseract/wiki

Agregar la carpeta de instalacion al PATH (tipicamente `C:\Program Files\Tesseract-OCR`).

## Configuracion de Claude Desktop

Editar `%APPDATA%\Claude\claude_desktop_config.json`.

Claude Desktop lanza el servidor como subprocess. El servidor necesita correr elevado
(el proceso de Python debe tener permisos de administrador), de lo contrario Windows
UIPI bloquea SendInput hacia el proceso elevado de AnyDesk.

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

Reiniciar Claude Desktop. El servidor debe aparecer disponible en la interfaz de herramientas.

En la conversacion, Claude llamara `initialize_session` para pinear la ventana AnyDesk activa.
