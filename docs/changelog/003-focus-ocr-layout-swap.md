# 003 — Focus antes de OCR + layout swap temporal para inyeccion

**Rama:** `refactor/v3-limpio`
**Fecha:** 2026-03-05

---

## Cambio 1: Focus antes de captura (OCR y screenshot)

**Problema:** `read_from_anydesk` y `capture_screenshot` capturan las coordenadas
de la ventana AnyDesk, pero `mss.grab` captura lo que sea que este visualmente
en esas coordenadas. Si AnyDesk esta detras de otra ventana (ej: YouTube),
se captura la ventana equivocada.

**Fix:** Llamar `focus_and_verify(200)` antes de capturar en `_capture_region()`
(screen_reader.py) y `capture_screenshot()` (screenshot.py). Best-effort: si
el focus falla, la captura procede igual.

**Archivos:** `screen_reader.py`, `screenshot.py`

## Cambio 2: Layout swap temporal durante inyeccion

**Problema:** `VkKeyScanW` mapea caracteres segun el layout de teclado LOCAL
activo. Si el usuario final tiene ES-Peru y el operador desarrollo con EN-US,
caracteres especiales como `|` se envian como `]` porque el VK code difiere
entre layouts.

**Fix:** Antes de inyectar, se activa el layout configurado en
`INJECTION_KEYBOARD_LAYOUT` (default: `"00000409"` = EN-US). Despues de
inyectar (o si falla), se restaura el layout original del usuario.

Flujo:
1. `_swap_layout("00000409")` → guarda handle del layout anterior
2. Inyeccion caracter por caracter
3. `_restore_layout(previous)` → restaura el layout del usuario

El usuario no nota el cambio porque dura solo el tiempo de la inyeccion
(tipicamente < 2 segundos).

**Config:** `INJECTION_KEYBOARD_LAYOUT` en `config.py`. Debe coincidir con
el layout del remoto. Valores comunes:
- `"00000409"` — EN-US (default)
- `"0000080A"` — ES-MX
- `"0000040A"` — ES-ES

**Archivos:** `keyboard_injector.py`, `config.py`
