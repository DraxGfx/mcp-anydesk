"""Keyboard injection via SendInput KEYEVENTF_UNICODE for AnyDesk.

Simple flow: focus_and_verify -> type each char via _send_char -> verify_focus_post.

All characters are sent via KEYEVENTF_UNICODE (wVk=0, wScan=Unicode codepoint).
This is completely layout-independent — works regardless of the local keyboard
layout (EN-US, ES-PE, etc.). No VkKeyScanW mapping needed for character injection.

Ctrl+C (send_cancel) cannot be sent programmatically through AnyDesk —
it focuses the window so the operator can press Ctrl+C manually.
"""

from __future__ import annotations

import ctypes
import logging
import time

from .command_templates import wrap_command, wrap_command_base64
from .config import (
    CANCEL_FOCUS_DELAY_MS,
    DEFAULT_FOCUS_DELAY_MS,
    DEFAULT_KEYSTROKE_DELAY_MS,
    MIN_KEYSTROKE_DELAY_MS,
)
from .win32_input import (
    INPUT,
    INPUT_KEYBOARD,
    KEYEVENTF_KEYUP,
    KEYEVENTF_UNICODE,
    SIZEOF_INPUT,
    SendInput as _SendInput,
)
from .window_manager import focus_and_verify, get_pinned_hwnd, verify_focus_post

log = logging.getLogger(__name__)

def _send_char(char: str) -> None:
    """Send a single character via KEYEVENTF_UNICODE (layout-independent).

    Uses wVk=0, wScan=<unicode codepoint>, dwFlags=KEYEVENTF_UNICODE.
    This sends the character directly by its Unicode value, bypassing
    the local keyboard layout entirely. Works regardless of whether
    the local machine has EN-US, ES-PE, or any other layout active.

    Args:
        char: Single character to type.

    Raises:
        RuntimeError: If SendInput fails.
    """
    code = ord(char)
    inputs = (INPUT * 2)()
    inputs[0].type = INPUT_KEYBOARD
    inputs[0].ii.ki.wVk = 0
    inputs[0].ii.ki.wScan = code
    inputs[0].ii.ki.dwFlags = KEYEVENTF_UNICODE
    inputs[1].type = INPUT_KEYBOARD
    inputs[1].ii.ki.wVk = 0
    inputs[1].ii.ki.wScan = code
    inputs[1].ii.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP

    result = _SendInput(2, inputs, SIZEOF_INPUT)
    if result == 0:
        error = ctypes.get_last_error()
        raise RuntimeError(
            f"SendInput UNICODE failed for {char!r} (U+{code:04X}). "
            f"Win32 error: {error}. sizeof(INPUT)={SIZEOF_INPUT}"
        )


def _check_quote_balance(text: str) -> str | None:
    """Check for unbalanced quotes that would leave PS in >> continuation.

    Removes PS-escaped quote pairs ('' and `") before counting, so
    intentionally escaped quotes don't trigger false positives.

    Returns an error message if unbalanced, None if OK.
    """
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


def write_to_anydesk(
    command: str,
    keystroke_delay_ms: int = DEFAULT_KEYSTROKE_DELAY_MS,
    focus_delay_ms: int = DEFAULT_FOCUS_DELAY_MS,
    wrap: bool = True,
    base64_output: bool = False,
    force_dangerous: bool = False,
    sanitizer_available: bool = True,
) -> dict:
    """Focus the pinned AnyDesk window and inject text character by character.

    Simple flow: focus -> type each char with delay -> verify focus.

    Args:
        command: Text to inject. Must NOT contain newlines.
        keystroke_delay_ms: Delay between each character (ms). Floor: 30ms.
        focus_delay_ms: Delay after focusing window before typing (ms).
        wrap: If True, auto-wrap with sanitizer check before injecting.
        base64_output: If True, use base64 wrapper instead of standard.
            Only effective when wrap=True.
        force_dangerous: Unused — kept for API compatibility.

    Returns:
        Dict with status, char_count, original_command, wrapped,
        elapsed_ms, and note.
    """
    original_command = command

    if base64_output and not wrap:
        return {
            "status": "error",
            "char_count": 0,
            "original_command": original_command,
            "wrapped": False,
            "elapsed_ms": 0,
            "note": (
                "base64_output=True requires wrap=True. Base64 encoding "
                "is applied via the command wrapper — it cannot work "
                "without wrapping. Set wrap=True or remove base64_output."
            ),
        }

    if any(ch in command for ch in ("\n", "\r")):
        return {
            "status": "error",
            "char_count": 0,
            "original_command": original_command,
            "wrapped": False,
            "elapsed_ms": 0,
            "note": (
                "Multi-line input rejected. Commands containing newlines "
                "must be split into separate steps. Remove all \\n and \\r "
                "characters."
            ),
        }

    command = command.replace("\n", "").replace("\r", "")
    command = command.replace("`n", "").replace("`r", "")

    if not command:
        return {
            "status": "error",
            "char_count": 0,
            "original_command": original_command,
            "wrapped": False,
            "elapsed_ms": 0,
            "note": "Empty command after sanitization.",
        }

    was_wrapped = False
    if wrap:
        if base64_output:
            command = wrap_command_base64(command, sanitizer_available=sanitizer_available)
        else:
            command = wrap_command(command)
        was_wrapped = True

    quote_error = _check_quote_balance(command)
    if quote_error:
        return {
            "status": "error",
            "char_count": len(command),
            "original_command": original_command,
            "wrapped": was_wrapped,
            "elapsed_ms": 0,
            "note": quote_error,
        }

    keystroke_delay_ms = max(keystroke_delay_ms, MIN_KEYSTROKE_DELAY_MS)
    delay_s = keystroke_delay_ms / 1000.0

    # 1. Focus
    try:
        focus_and_verify(focus_delay_ms)
    except RuntimeError as exc:
        return {
            "status": "error",
            "char_count": 0,
            "original_command": original_command,
            "wrapped": was_wrapped,
            "elapsed_ms": 0,
            "note": str(exc),
        }

    # 2. Inject character by character (KEYEVENTF_UNICODE — layout-independent)
    start = time.perf_counter()
    chars_injected = 0
    try:
        for char in command:
            _send_char(char)
            chars_injected += 1
            time.sleep(delay_s)
    except RuntimeError as exc:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return {
            "status": "error",
            "char_count": len(command),
            "original_command": original_command,
            "wrapped": was_wrapped,
            "elapsed_ms": elapsed_ms,
            "note": (
                f"Injection failed after {chars_injected}/{len(command)} chars: {exc}"
            ),
        }
    elapsed_ms = int((time.perf_counter() - start) * 1000)

    # 3. Post-verify focus
    try:
        verify_focus_post()
    except RuntimeError as exc:
        return {
            "status": "error",
            "char_count": len(command),
            "original_command": original_command,
            "wrapped": was_wrapped,
            "elapsed_ms": elapsed_ms,
            "note": f"Focus drifted: {exc}",
        }

    return {
        "status": "injected",
        "char_count": len(command),
        "original_command": original_command,
        "wrapped": was_wrapped,
        "elapsed_ms": elapsed_ms,
        "note": "Enter NOT pressed — awaiting operator confirmation",
    }


def send_cancel() -> dict:
    """Request the operator to press Ctrl+C on the remote console.

    AnyDesk does NOT forward programmatic Ctrl+C (SendInput) as a real
    console interrupt signal — it renders it as literal text. Only a
    physical Ctrl+C from the operator's keyboard generates the
    CTRL_C_EVENT that stops running processes.

    This tool focuses the AnyDesk window so the operator can immediately
    press Ctrl+C without needing to click on it first.

    Returns:
        Dict with status and instructions for the operator.
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

    return {
        "status": "awaiting_operator",
        "note": (
            "AnyDesk window focused. OPERATOR: press Ctrl+C NOW to cancel "
            "the running command. Programmatic Ctrl+C cannot interrupt "
            "processes through AnyDesk — only physical keyboard input works."
        ),
    }
