"""AnyDesk window enumeration, selection, and focus management.

v3 changes (N4):
- Named sessions dict: multiple windows can be pinned simultaneously.
- pin_session(hwnd, name): pin with a human-readable label.
- switch_session(name): change the active session by name.
- list_sessions(): enumerate all named sessions.
- get_pinned_hwnd() now returns the active named session's hwnd, with
  fallback to the legacy single-pin var for backward compatibility.
"""

from __future__ import annotations

import time

import ctypes

import win32api  # type: ignore[import-untyped]
import win32gui  # type: ignore[import-untyped]
import win32con  # type: ignore[import-untyped]
import win32process  # type: ignore[import-untyped]

from .config import DEFAULT_FOCUS_DELAY_MS


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
_pinned_hwnd: int | None = None                  # Legacy single-pin (v1/v2 compat)
_sessions: dict[str, dict] = {}                  # name -> {"hwnd": int, "title": str}
_active_session: str | None = None               # Currently active named session


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------
def enumerate_anydesk_windows() -> list[tuple[int, str, str]]:
    """Find all visible windows whose class or title contains 'AnyDesk'.

    Returns:
        List of (hwnd, title, class_name) tuples.
    """
    windows: list[tuple[int, str, str]] = []

    def _callback(hwnd: int, _extra: object) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        class_name = win32gui.GetClassName(hwnd)
        # Match by AnyDesk window class (most reliable)
        is_anydesk_class = "anydesk" in class_name.lower()
        # Match by title only if "AnyDesk" appears as a standalone word
        # (not as part of a path like "mcp_anydesk_server")
        import re
        is_anydesk_title = bool(re.search(r'(?<![_\w])AnyDesk(?![_\w])', title))
        if is_anydesk_class or is_anydesk_title:
            windows.append((hwnd, title, class_name))

    win32gui.EnumWindows(_callback, None)
    return windows


# ---------------------------------------------------------------------------
# Selection / pinning
# ---------------------------------------------------------------------------
def select_window(hwnd: int) -> dict:
    """Pin a specific window handle for the session.

    Args:
        hwnd: The window handle to pin.

    Returns:
        Status dict with pinned hwnd and title.

    Raises:
        ValueError: If the hwnd is not a valid visible window.
    """
    global _pinned_hwnd

    if not win32gui.IsWindow(hwnd):
        raise ValueError(f"hwnd {hwnd} is not a valid window.")
    if not win32gui.IsWindowVisible(hwnd):
        raise ValueError(f"hwnd {hwnd} is not visible.")

    title = win32gui.GetWindowText(hwnd)
    _pinned_hwnd = hwnd
    return {"status": "pinned", "hwnd": hwnd, "title": title}


def get_active_hwnd() -> int | None:
    """Return the hwnd for the currently active session.

    Checks named sessions first; falls back to the legacy single-pin var.
    """
    if _active_session and _active_session in _sessions:
        return _sessions[_active_session]["hwnd"]
    return _pinned_hwnd


def get_pinned_hwnd() -> int | None:
    """Return the currently active hwnd, or None if nothing is pinned.

    Backward-compatible wrapper around get_active_hwnd().
    """
    return get_active_hwnd()


def pin_session(hwnd: int, name: str) -> dict:
    """Pin a window with a human-readable name (N4).

    Multiple windows can be pinned simultaneously under different names.
    The newly pinned window becomes the active session.

    Args:
        hwnd: Window handle to pin.
        name: Human-readable label (e.g., "DC01", "HyperV-Host").

    Returns:
        Status dict with name, hwnd, and title.
    """
    global _active_session, _pinned_hwnd

    if not win32gui.IsWindow(hwnd):
        return {"status": "error", "note": f"hwnd {hwnd} is not a valid window."}
    if not win32gui.IsWindowVisible(hwnd):
        return {"status": "error", "note": f"hwnd {hwnd} is not visible."}

    title = win32gui.GetWindowText(hwnd)
    _sessions[name] = {"hwnd": hwnd, "title": title}
    _active_session = name
    _pinned_hwnd = hwnd  # Keep legacy var in sync
    return {"status": "pinned", "name": name, "hwnd": hwnd, "title": title}


def switch_session(name: str) -> dict:
    """Switch the active session to a previously pinned named window (N4).

    Args:
        name: Name of a previously pinned session.

    Returns:
        Status dict with name, hwnd, and title, or error if not found.
    """
    global _active_session, _pinned_hwnd

    if name not in _sessions:
        available = list(_sessions.keys())
        return {
            "status": "error",
            "note": f"Session '{name}' not found. Available: {available}",
        }

    _active_session = name
    _pinned_hwnd = _sessions[name]["hwnd"]  # Keep legacy var in sync
    return {"status": "switched", "name": name, **_sessions[name]}


def list_sessions() -> list[dict]:
    """Return all named sessions with their hwnd, title, and active status.

    Returns:
        List of session dicts (name, hwnd, title, active).
    """
    return [
        {
            "name": name,
            "hwnd": info["hwnd"],
            "title": info["title"],
            "active": name == _active_session,
        }
        for name, info in _sessions.items()
    ]


# ---------------------------------------------------------------------------
# Focus management
# ---------------------------------------------------------------------------
def focus_and_verify(focus_delay_ms: int = DEFAULT_FOCUS_DELAY_MS) -> None:
    """Bring the pinned window to the foreground and verify focus.

    Uses AttachThreadInput to bypass Windows' foreground lock restriction,
    which prevents background processes from stealing focus.

    Raises:
        RuntimeError: If no window is pinned or focus verification fails.
    """
    active_hwnd = get_active_hwnd()
    if active_hwnd is None:
        raise RuntimeError(
            "No AnyDesk window pinned. Use select_anydesk_window first."
        )

    if not win32gui.IsWindow(active_hwnd):
        raise RuntimeError(
            f"Pinned hwnd {active_hwnd} is no longer valid. "
            "The AnyDesk session may have been closed."
        )

    # Get foreground window's thread
    fg_hwnd = win32gui.GetForegroundWindow()
    fg_thread = win32process.GetWindowThreadProcessId(fg_hwnd)[0]
    our_thread = win32api.GetCurrentThreadId()

    attached = False
    try:
        # Attach our thread to the foreground thread's input queue
        if fg_thread != our_thread:
            ctypes.windll.user32.AttachThreadInput(our_thread, fg_thread, True)
            attached = True

        # Restore if minimized
        if win32gui.IsIconic(active_hwnd):
            win32gui.ShowWindow(active_hwnd, win32con.SW_RESTORE)

        win32gui.BringWindowToTop(active_hwnd)
        win32gui.SetForegroundWindow(active_hwnd)

    finally:
        if attached:
            ctypes.windll.user32.AttachThreadInput(our_thread, fg_thread, False)

    time.sleep(focus_delay_ms / 1000.0)

    foreground = win32gui.GetForegroundWindow()
    if foreground != active_hwnd:
        raise RuntimeError(
            "Focus lost, injection aborted. "
            f"Expected hwnd {active_hwnd}, got {foreground}."
        )


def verify_focus_post() -> None:
    """Post-injection focus verification.

    Raises:
        RuntimeError: If focus has drifted away from the active window.
    """
    active_hwnd = get_active_hwnd()
    if active_hwnd is None:
        return

    foreground = win32gui.GetForegroundWindow()
    if foreground != active_hwnd:
        raise RuntimeError(
            "Focus drifted during injection. "
            f"Expected hwnd {active_hwnd}, got {foreground}. "
            "Keystrokes may have been sent to the wrong window."
        )
