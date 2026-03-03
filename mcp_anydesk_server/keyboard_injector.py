"""Keyboard injection with chunked re-focus for long commands.

Uses Win32 SendInput with KEYEVENTF_UNICODE (VK_PACKET) for character
injection. This sends Unicode codepoints directly, bypassing the OS
keyboard layout entirely. Fixes EN-US vs ES-LA mismatch: characters
like backslash, pipe, braces, slash, equals, colon are injected
correctly regardless of layout on either side.

pynput is only used for modifier combos (Ctrl+C in send_cancel).

v3 change: injection is split into CHUNK_SIZE-char chunks. Between chunks,
focus is re-verified and re-acquired. This prevents Windows from dropping
the foreground lock during long injections (150+ chars at 80ms = 12-16s).

v3.2 change: replaced pynput.keyboard.Controller.type() with SendInput
KEYEVENTF_UNICODE to eliminate keyboard layout dependency.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import time

from pynput.keyboard import Controller as KbController, Key  # type: ignore[import-untyped]

from .command_templates import wrap_command, wrap_command_base64
from .config import (
    CANCEL_FOCUS_DELAY_MS,
    CHUNK_MAX_RETRIES,
    CHUNK_REFOCUS_DELAY_MS,
    CHUNK_SIZE,
    DEFAULT_FOCUS_DELAY_MS,
    DEFAULT_KEYSTROKE_DELAY_MS,
    MIN_KEYSTROKE_DELAY_MS,
)
from .window_manager import focus_and_verify, get_pinned_hwnd, verify_focus_post


# ---------------------------------------------------------------------------
# Win32 SendInput structures for KEYEVENTF_UNICODE
# ---------------------------------------------------------------------------
INPUT_KEYBOARD = 1
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP = 0x0002


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]

    _fields_ = [
        ("type", wintypes.DWORD),
        ("ii", _INPUT),
    ]


_SendInput = ctypes.windll.user32.SendInput
_SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
_SendInput.restype = wintypes.UINT

# pynput only for Ctrl+C (modifier combo — not affected by layout)
_keyboard = KbController()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _send_unicode_char(char: str) -> None:
    """Send a single Unicode character via SendInput VK_PACKET.

    Bypasses the keyboard layout completely — the character's Unicode
    codepoint is sent directly. AnyDesk receives it as WM_CHAR,
    independent of local or remote keyboard layout.
    """
    code = ord(char)
    inputs = (INPUT * 2)()

    # Key down
    inputs[0].type = INPUT_KEYBOARD
    inputs[0].ii.ki.wVk = 0
    inputs[0].ii.ki.wScan = code
    inputs[0].ii.ki.dwFlags = KEYEVENTF_UNICODE

    # Key up
    inputs[1].type = INPUT_KEYBOARD
    inputs[1].ii.ki.wVk = 0
    inputs[1].ii.ki.wScan = code
    inputs[1].ii.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP

    _SendInput(2, inputs, ctypes.sizeof(INPUT))


def _inject_chunk(text: str, delay_s: float) -> int:
    """Type every character in text via VK_PACKET with delay_s between each.

    Returns the number of characters typed.
    """
    for char in text:
        _send_unicode_char(char)
        time.sleep(delay_s)
    return len(text)


def _check_quote_balance(text: str) -> str | None:
    """Check for unbalanced quotes that would leave PS in >> continuation.

    Removes PS-escaped quote pairs ('' and `") before counting, so
    intentionally escaped quotes don't trigger false positives.

    Returns an error message if unbalanced, None if OK.
    """
    # Strip PS-escaped pairs so they don't affect the count
    cleaned = text.replace("''", "").replace('`"', "")

    single = cleaned.count("'")
    double = cleaned.count('"')

    issues: list[str] = []
    if single % 2 != 0:
        issues.append("single quotes (')")
    if double % 2 != 0:
        issues.append('double quotes (")')

    if issues:
        return (
            f"Unbalanced {' and '.join(issues)} detected. "
            "PowerShell will enter >> continuation mode, requiring "
            "Ctrl+C to recover. Fix quote pairing before injecting."
        )
    return None


def _refocus_with_retry(focus_delay_ms: int) -> bool:
    """Attempt to re-acquire foreground focus up to CHUNK_MAX_RETRIES times.

    Returns True if focus was successfully acquired, False otherwise.
    """
    for attempt in range(CHUNK_MAX_RETRIES + 1):
        try:
            focus_and_verify(focus_delay_ms)
            return True
        except RuntimeError:
            if attempt < CHUNK_MAX_RETRIES:
                time.sleep(CHUNK_REFOCUS_DELAY_MS / 1000.0)
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def write_to_anydesk(
    command: str,
    keystroke_delay_ms: int = DEFAULT_KEYSTROKE_DELAY_MS,
    focus_delay_ms: int = DEFAULT_FOCUS_DELAY_MS,
    wrap: bool = True,
    base64_output: bool = False,
    force_dangerous: bool = False,
    sanitizer_available: bool = True,
) -> dict:
    """Focus the pinned AnyDesk window and inject keystrokes in chunks.

    Commands are split into chunks of CHUNK_SIZE characters. Between each
    chunk, focus is re-verified and re-acquired if needed. This prevents
    Windows foreground lock loss during long injections.

    Args:
        command: Text to inject. Must NOT contain newlines.
        keystroke_delay_ms: Delay between each character (ms). Floor: 30ms.
        focus_delay_ms: Delay after focusing window before typing (ms).
        wrap: If True, auto-wrap with sanitizer check before injecting.
        base64_output: If True, use base64 wrapper instead of standard.
            Only effective when wrap=True.
        force_dangerous: Unused — kept for API compatibility.

    Returns:
        Dict with status, char_count, chars_injected, original_command,
        wrapped, elapsed_ms, chunks_used, and note.
    """
    original_command = command

    # --- Reject base64 without wrap ---
    if base64_output and not wrap:
        return {
            "status": "error",
            "char_count": 0,
            "chars_injected": 0,
            "original_command": original_command,
            "wrapped": False,
            "elapsed_ms": 0,
            "chunks_used": 0,
            "note": (
                "base64_output=True requires wrap=True. Base64 encoding "
                "is applied via the command wrapper — it cannot work "
                "without wrapping. Set wrap=True or remove base64_output."
            ),
        }

    # --- Reject multi-line input ---
    if any(ch in command for ch in ("\n", "\r")):
        return {
            "status": "error",
            "char_count": 0,
            "chars_injected": 0,
            "original_command": original_command,
            "wrapped": False,
            "elapsed_ms": 0,
            "chunks_used": 0,
            "note": (
                "Multi-line input rejected. Commands containing newlines "
                "must be split into separate steps. Remove all \\n and \\r "
                "characters."
            ),
        }

    # --- Strip any stray newline-equivalent sequences ---
    command = command.replace("\n", "").replace("\r", "")
    command = command.replace("`n", "").replace("`r", "")

    if not command:
        return {
            "status": "error",
            "char_count": 0,
            "chars_injected": 0,
            "original_command": original_command,
            "wrapped": False,
            "elapsed_ms": 0,
            "chunks_used": 0,
            "note": "Empty command after sanitization.",
        }

    # --- Auto-wrap with sanitizer check ---
    was_wrapped = False
    if wrap:
        if base64_output:
            command = wrap_command_base64(command, sanitizer_available=sanitizer_available)
        else:
            command = wrap_command(command)
        was_wrapped = True

    # --- Quote balance check (prevents PS >> continuation) ---
    quote_error = _check_quote_balance(command)
    if quote_error:
        return {
            "status": "error",
            "char_count": len(command),
            "chars_injected": 0,
            "original_command": original_command,
            "wrapped": was_wrapped,
            "elapsed_ms": 0,
            "chunks_used": 0,
            "note": quote_error,
        }

    # --- Enforce minimum delay ---
    keystroke_delay_ms = max(keystroke_delay_ms, MIN_KEYSTROKE_DELAY_MS)
    delay_s = keystroke_delay_ms / 1000.0

    # Split into chunks
    chunks = [
        command[i : i + CHUNK_SIZE]
        for i in range(0, len(command), CHUNK_SIZE)
    ]
    total_chars = len(command)
    chars_injected = 0
    start = time.perf_counter()

    for chunk_index, chunk in enumerate(chunks):
        # Re-acquire focus before every chunk (includes initial focus)
        if not _refocus_with_retry(focus_delay_ms):
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            return {
                "status": "error",
                "char_count": total_chars,
                "chars_injected": chars_injected,
                "original_command": original_command,
                "wrapped": was_wrapped,
                "elapsed_ms": elapsed_ms,
                "chunks_used": chunk_index,
                "note": (
                    f"Focus lost at chunk {chunk_index + 1}/{len(chunks)} "
                    f"after injecting {chars_injected}/{total_chars} characters. "
                    "Re-pin the window and retry."
                ),
            }

        chars_injected += _inject_chunk(chunk, delay_s)

    elapsed_ms = int((time.perf_counter() - start) * 1000)

    # --- Post-injection focus verification ---
    try:
        verify_focus_post()
    except RuntimeError as exc:
        return {
            "status": "error",
            "char_count": total_chars,
            "chars_injected": chars_injected,
            "original_command": original_command,
            "wrapped": was_wrapped,
            "elapsed_ms": elapsed_ms,
            "chunks_used": len(chunks),
            "note": (
                f"Keystrokes were injected but focus drifted at the end: {exc}. "
                "Some characters may have gone to the wrong window."
            ),
        }

    return {
        "status": "injected",
        "char_count": total_chars,
        "chars_injected": chars_injected,
        "original_command": original_command,
        "wrapped": was_wrapped,
        "elapsed_ms": elapsed_ms,
        "chunks_used": len(chunks),
        "note": "Enter NOT pressed — awaiting operator confirmation",
    }


def send_cancel() -> dict:
    """Send Ctrl+C to the pinned AnyDesk window to abort a running command.

    Use when the operator says 'abort', 'cancel', or the command appears hung.

    Returns:
        Dict with status and note.
    """
    hwnd = get_pinned_hwnd()
    if hwnd is None:
        return {
            "status": "error",
            "note": "No AnyDesk window pinned. Use select_anydesk_window first.",
        }

    try:
        focus_and_verify(CANCEL_FOCUS_DELAY_MS)
    except RuntimeError as exc:
        return {"status": "error", "note": str(exc)}

    with _keyboard.pressed(Key.ctrl):
        _keyboard.press("c")
        _keyboard.release("c")

    return {
        "status": "sent",
        "note": "Ctrl+C sent to the remote console.",
    }
