# 004 — KEYEVENTF_UNICODE injection (layout-independent)

**Rama:** `refactor/v3-limpio`
**Fecha:** 2026-03-05

---

## Problema

`_send_char` usaba VK codes reales via `VkKeyScanW`, que mapea caracteres
segun el layout de teclado LOCAL. Si el operador tiene ES-Peru y el remoto
tiene EN-US, caracteres especiales como `|` se envian como `]`.

Se intento resolver con `ActivateKeyboardLayout` temporal (cambio 003),
pero AnyDesk PROPAGA el cambio de layout al remoto, corrompiendo la salida:
`Write'Host [PathÑ CÑ}Users}test ] StatusÑ OK ¨done* ¿ 100%[`

## Solucion

Reemplazar VK codes con `KEYEVENTF_UNICODE` para TODOS los caracteres:
- `wVk = 0`
- `wScan = ord(char)` (Unicode codepoint)
- `dwFlags = KEYEVENTF_UNICODE`

Esto envia el caracter por su valor Unicode, sin depender del layout local.

## Cambios

**`keyboard_injector.py`:**
- `_send_char` ahora usa KEYEVENTF_UNICODE para todos los caracteres
- Eliminado: `_swap_layout`, `_restore_layout`, `_LoadKeyboardLayoutW`,
  `_ActivateKeyboardLayout`, `_GetKeyboardLayout`, `KLF_ACTIVATE`
- Eliminado: import de `VkKeyScanW`
- `_send_vk_combo` (Ctrl+C) sigue usando VK codes reales (necesario para modifiers)

**`config.py`:**
- Eliminado: `INJECTION_KEYBOARD_LAYOUT`

## Riesgo

KEYEVENTF_UNICODE fue reportado como ignorado por AnyDesk en v3.2, pero
nunca fue probado correctamente con el struct layout corregido (sizeof=40).
Esta es la prueba definitiva.

Si AnyDesk no captura UNICODE events, el fallback seria volver a VK codes
y documentar que el operador debe usar el mismo layout que el remoto.
