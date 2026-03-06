"""Shared Win32 input structures and SendInput helpers.

Used by both keyboard_injector and window_manager to avoid code duplication
and import cycles. This module has no dependencies on other project modules.

Structures mirror the Win32 API exactly so sizeof(INPUT) == 40 on x64.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Win32 structures
# ---------------------------------------------------------------------------

class MOUSEINPUT(ctypes.Structure):
    """Largest INPUT union member — required so sizeof(INPUT) == 40 on x64."""
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [
            ("mi", MOUSEINPUT),
            ("ki", KEYBDINPUT),
            ("hi", HARDWAREINPUT),
        ]

    _fields_ = [
        ("type", wintypes.DWORD),
        ("ii", _INPUT),
    ]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

# Keyboard event flags
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008  # Use wScan as primary key identifier

# Mouse event flags
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000  # dx/dy are normalized absolute coords (0-65535)

# MapVirtualKey modes
MAPVK_VK_TO_VSC = 0

# GetSystemMetrics indices
SM_CXSCREEN = 0
SM_CYSCREEN = 1


# ---------------------------------------------------------------------------
# Win32 API bindings (dedicated DLL handle to avoid shared argtypes clobbering)
# ---------------------------------------------------------------------------

_user32 = ctypes.WinDLL("user32", use_last_error=True)

SendInput = _user32.SendInput
SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
SendInput.restype = wintypes.UINT

MapVirtualKeyW = _user32.MapVirtualKeyW
MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
MapVirtualKeyW.restype = wintypes.UINT

VkKeyScanW = _user32.VkKeyScanW
VkKeyScanW.argtypes = [wintypes.WCHAR]
VkKeyScanW.restype = ctypes.c_short

GetSystemMetrics = _user32.GetSystemMetrics
GetSystemMetrics.argtypes = [ctypes.c_int]
GetSystemMetrics.restype = ctypes.c_int


# ---------------------------------------------------------------------------
# sizeof validation (fail fast on wrong struct layout)
# ---------------------------------------------------------------------------

SIZEOF_INPUT = ctypes.sizeof(INPUT)

if SIZEOF_INPUT < 40 and ctypes.sizeof(ctypes.c_void_p) == 8:
    raise RuntimeError(
        f"sizeof(INPUT) = {SIZEOF_INPUT}, expected 40 on 64-bit Python. "
        "The INPUT struct union is missing MOUSEINPUT. "
        "SendInput will silently fail with wrong cbSize."
    )

log.info("sizeof(INPUT) = %d (expected 40 on x64, 28 on x86)", SIZEOF_INPUT)


# ---------------------------------------------------------------------------
# Public helper
# ---------------------------------------------------------------------------

def send_inputs(inputs_array: ctypes.Array) -> int:
    """Call SendInput and return the number of events inserted.

    Args:
        inputs_array: ctypes array of INPUT structs (e.g. ``(INPUT * n)()``)

    Returns:
        Number of events successfully inserted (>= 1).

    Raises:
        RuntimeError: If SendInput returns 0 (Win32 error available via
            ctypes.get_last_error()).
    """
    n = len(inputs_array)
    result = SendInput(n, inputs_array, SIZEOF_INPUT)
    if result == 0:
        error = ctypes.get_last_error()
        raise RuntimeError(
            f"SendInput failed. Inserted 0/{n} events. "
            f"Win32 error: {error}. sizeof(INPUT)={SIZEOF_INPUT}"
        )
    return result
