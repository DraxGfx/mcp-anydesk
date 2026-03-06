# Tips de uso — MCP AnyDesk Server

## Split mode: foco despues de inyeccion

Cuando usas Claude Desktop y AnyDesk en split mode (lado a lado), despues
de que el MCP inyecta un comando el **foco queda en AnyDesk**. Esto es
intencional — permite presionar Enter manualmente para confirmar la ejecucion.

**Consecuencia:** si escribes "ok" en Claude Desktop sin hacer clic primero
en su ventana, las teclas van al remoto (AnyDesk tiene el foco).

**Solucion:** haz clic en la ventana de Claude Desktop antes de escribir
tu confirmacion. En split mode es un clic rapido al otro lado.

No hay fix en codigo para esto porque:
1. El foco DEBE estar en AnyDesk despues de inyectar (para que Enter funcione)
2. Windows envia keystrokes a la ventana con foco
3. Si devolviéramos el foco a Claude Desktop automaticamente, no podrias
   presionar Enter en el remoto

## Verificar version del MCP

Usa la herramienta `get_version` en Claude Desktop para confirmar que
version del MCP esta corriendo. Util para verificar que no estas usando
una version en cache despues de un `pip install .`.

Si la version no coincide con la esperada, reinicia Claude Desktop.
