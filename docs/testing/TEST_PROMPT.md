# MCP AnyDesk Server — Test Prompt

> Pegar este prompt completo en una conversacion de Claude Desktop que tenga el MCP AnyDesk server configurado.
> Se requiere una conexion AnyDesk activa a una maquina Windows remota.
>
> **Ultima actualizacion:** 2026-03-05 (refactor/v3-limpio)

---

## Prompt

```
Necesito que ejecutes una validacion del MCP AnyDesk Server. Sigue cada test exactamente.
Para cada test registra: ID, descripcion, resultado esperado, resultado real, estado (PASS/FAIL/PARTIAL).

Al final genera una tabla markdown con los resultados.

### SETUP
1. Llama get_version y reporta la version. Esperado: 3.5.0 o superior.
2. Llama initialize_session para pinear la ventana AnyDesk.
3. NO hagas bootstrap — todos los tests deben funcionar sin el.

### TEST 1 — Inyeccion simple (sin wrap)
Inyecta con write_to_anydesk(wrap=False):
hostname
Pideme presionar Enter, luego lee el output con read_from_anydesk.
**Esperado:** status="injected", el texto aparece en el remoto, Enter NO presionado.

### TEST 2 — Caracteres especiales
Inyecta con write_to_anydesk(wrap=False):
Write-Host "Path: C:\Users\test | Status: OK {done} = 100%"
Pideme presionar Enter, luego lee el output.
**Esperado:** Todos los caracteres (\ | { } = %) aparecen correctamente sin corrupcion.

### TEST 3 — OCR basico
Lee la pantalla actual con read_from_anydesk(mode="ocr").
**Esperado:** Retorna texto legible con un valor de confianza. No importa el valor exacto.

### TEST 4 — Screenshot
Toma un screenshot con capture_screenshot() (sin return_base64).
**Esperado:** status="ok", size_kb <= 15, saved_to con ruta valida, SIN image_base64 en la respuesta.

### TEST 5 — Base64 lite (sin bootstrap)
Inyecta con write_to_anydesk(base64_output=True, wrap=True):
hostname
Pideme presionar Enter, luego lee con read_from_anydesk(mode="base64").
**Esperado:** Output decodificado + CRC valido. Sin error de [SANITIZER-MISSING].

### TEST 6 — send_cancel
Inyecta un comando largo con write_to_anydesk(wrap=False):
ping 127.0.0.1 -n 100
Pideme presionar Enter. Luego envia send_cancel.
**Esperado:** status="awaiting_operator", ventana AnyDesk enfocada, mensaje pidiendo al operador presionar Ctrl+C manualmente.

### TEST 7 — wrap=True sin bootstrap (fallback)
Inyecta con write_to_anydesk(wrap=True):
Get-Date
**Esperado:** El comando se inyecta como raw (sin wrapper) con un sanitizer_warning
en la respuesta. NO debe devolver error ni bloquear.

### TEST 8 — Validacion de quotes
Inyecta con write_to_anydesk(wrap=False):
Write-Host "test
(comilla doble sin cerrar)
**Esperado:** status="error" con mensaje sobre comillas desbalanceadas. NO debe inyectar.

### TEST 9 — Recipes
1. Llama list_recipes.
2. Llama get_recipe con el nombre del primer recipe disponible.
**Esperado:** Lista de recipes retornada, recipe individual tiene commands y description.

### TEST 10 — Session history + export
1. Llama get_session_history.
2. Llama export_session.
**Esperado:** Historial con los pasos anteriores. Export genera un .md en disco.

### TEST 11 — Bootstrap completo (si los tests anteriores pasan)
1. Llama bootstrap_sanitizer para obtener los 9 pasos.
2. Inyecta cada paso con write_to_anydesk(wrap=False), pideme Enter despues de cada uno.
3. Despues del paso 9, lee con read_from_anydesk y verifica [BOOTSTRAP-OK].
4. Llama log_step_result con success=True y notes="BOOTSTRAP-OK".
5. Ahora inyecta con write_to_anydesk(wrap=True): hostname
**Esperado:** Bootstrap se completa, sanitizer_bootstrapped=True, wrap=True usa el wrapper completo.

### FORMATO DE RESULTADOS. Un archivo MARKDOWN con lo siguiente:

| # | Test | Esperado | Real | Estado |
|---|------|----------|------|--------|
| 1 | Inyeccion simple | injected, texto visible | [resultado] | PASS/FAIL |
| 2 | Caracteres especiales | \ | { } = % correctos | [resultado] | PASS/FAIL |
| 3 | OCR basico | texto legible | [resultado] | PASS/FAIL |
| 4 | Screenshot | JPEG <= 15KB, sin base64 | [resultado] | PASS/FAIL |
| 5 | Base64 lite | decoded + CRC valido | [resultado] | PASS/FAIL |
| 6 | send_cancel | awaiting_operator, foco en AnyDesk | [resultado] | PASS/FAIL |
| 7 | wrap=True fallback | raw + warning | [resultado] | PASS/FAIL |
| 8 | Quotes desbalanceadas | error, no inyecta | [resultado] | PASS/FAIL |
| 9 | Recipes | lista + detalle | [resultado] | PASS/FAIL |
| 10 | History + export | historial + .md | [resultado] | PASS/FAIL |
| 11 | Bootstrap completo | sanitizer activo | [resultado] | PASS/FAIL |

**Resumen:** X/11 passed
```
