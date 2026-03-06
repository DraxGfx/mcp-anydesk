# 002 — Fix inyeccion: VK codes reales en vez de SCANCODE

**Rama:** `refactor/v3-limpio`
**Fecha:** 2026-03-05
**Objetivo:** Restaurar la inyeccion de keystrokes que AnyDesk captura correctamente.

---

## Problema

`_send_char()` en `keyboard_injector.py` enviaba keystrokes con:
- `wVk=0, dwFlags=KEYEVENTF_SCANCODE`

AnyDesk ignoraba estos eventos silenciosamente. `SendInput` reportaba exito
(`status=injected`, chars contados correctamente) pero NADA aparecia en la
pantalla remota. Sin errores, sin warnings — fallo completamente silencioso.

## Causa raiz

v3.4 cambio el metodo de inyeccion de VK codes reales (como usaba pynput en v2)
a KEYEVENTF_SCANCODE con `wVk=0`. La teoria era que AnyDesk leeria el scan code.
En la practica, AnyDesk necesita un VK code real (`wVk != 0`) para capturar
el evento como keystroke genuino.

## Historial de metodos de inyeccion

| Version | Metodo | wVk | dwFlags | Resultado |
|---------|--------|-----|---------|-----------|
| v2 | pynput Controller.type() | VK real | 0 | FUNCIONA |
| v3.2 | SendInput KEYEVENTF_UNICODE | 0 | KEYEVENTF_UNICODE | AnyDesk ignora VK_PACKET |
| v3.4 | SendInput KEYEVENTF_SCANCODE | 0 | KEYEVENTF_SCANCODE | AnyDesk ignora (wVk=0) |
| **v3-limpio** | **SendInput VK real** | **VK real** | **0** | **FUNCIONA** |

## Fix aplicado

En `keyboard_injector.py`, funcion `_send_char()`:

```python
# ANTES (v3.4 — roto):
events.append((0, scan, KEYEVENTF_SCANCODE))
events.append((0, scan, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP))

# DESPUES (v3-limpio — funciona):
events.append((vk, scan, 0))
events.append((vk, scan, KEYEVENTF_KEYUP))
```

Los modifiers (Shift/Ctrl/Alt) ya usaban VK mode — solo el caracter principal
estaba mal. El fallback KEYEVENTF_UNICODE para caracteres no mapeables por
VkKeyScanW se mantiene sin cambios.

## Leccion critica

**AnyDesk requiere `wVk` con un VK code real para capturar keystrokes.**
SendInput puede reportar exito (return > 0) incluso cuando AnyDesk descarta
los eventos. No hay forma de detectar este fallo desde el lado del emisor.
Solo la verificacion visual en la pantalla remota confirma que la inyeccion
llego.

## Archivos modificados

- `keyboard_injector.py` — `_send_char()` usa VK reales, eliminado import de `KEYEVENTF_SCANCODE`
- Reinstalado paquete con `pip install .`
