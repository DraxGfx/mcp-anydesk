# 001 — Refactor v3 Limpio

**Rama:** `refactor/v3-limpio`
**Fecha:** 2026-03-05
**Objetivo:** Volver a la simplicidad de inyeccion v2, conservando las herramientas de v3 que funcionan.

---

## Cambios realizados

### `keyboard_injector.py` — Simplificacion del inyector
- **Eliminado:** chunked injection (`_inject_chunk`, `_refocus_with_retry`, loop de chunks)
- **Eliminado:** imports de `CHUNK_SIZE`, `CHUNK_REFOCUS_DELAY_MS`, `CHUNK_MAX_RETRIES`
- **Eliminado:** import no usado `send_inputs`
- **Eliminado:** campos `chunks_used` y `chars_injected` de los response dicts
- **Restaurado:** flujo simple v2: `focus_and_verify` -> `for char: _send_char(char) + sleep` -> `verify_focus_post`
- **Conservado:** `_send_char` (scan codes), `_send_vk_combo` (Ctrl+C), `_check_quote_balance`, `send_cancel`

### `config.py` — Limpieza de constantes
- **Eliminado:** `CHUNK_SIZE`, `CHUNK_REFOCUS_DELAY_MS`, `CHUNK_MAX_RETRIES`

### `server.py` — Fix sanitizer flag + limpieza
- **Mejorado:** `log_step_result` ahora detecta `"BOOTSTRAP-OK"` en `cmd.output_text` ademas de `"bootstrap"` en notes para activar `sanitizer_bootstrapped = True`
- **Verificado:** wrap=True sin sanitizer ya hacia fallback correcto a raw + warning (no bloquea)
- **Limpiado:** docstring del modulo y del tool `write_to_anydesk`

### `command_templates.py` — Limpieza de docstring
- Eliminada referencia obsoleta a `CHUNK_SIZE`

### `screenshot.py` — Sin cambios
- El loop de reduccion de calidad JPEG y el cap de 15KB ya estaban implementados correctamente

### `docs/` — Reorganizacion
- `docs/specs/` — specs historicas (v1, v2, v3)
- `docs/testing/` — resultados de pruebas y test prompts
- `docs/changelog/` — registro de cambios por sesion de trabajo

---

## No tocados (conservados de v3)
- `send_cancel` (Ctrl+C), `initialize_session`, named sessions, recipes
- Audit log, `_check_quote_balance`, bootstrap en 9 pasos
- `win32_input.py`, `window_manager.py`, `screen_reader.py`, `sanitizer.py`

## Bugs referenciados (ORIGINAL_DESIGN.md)
| Bug | Estado |
|-----|--------|
| Bug 1 — Inyeccion se raya (chunks) | Corregido: chunks eliminados |
| Bug 3 — sanitizer_bootstrapped nunca es True | Corregido: deteccion mejorada en log_step_result |
| Bug 4 — wrap=True bloquea | Verificado: ya funcionaba con fallback |
| Bug 5 — Cap de screenshots no funciona | Verificado: ya implementado correctamente |
| Bug 2 — Bootstrap causa loops | No aplica: bootstrap ya separado de initialize_session |
| Bug 6 — scale_percent=100 malogra imagen | Verificado: no se reproduce |
