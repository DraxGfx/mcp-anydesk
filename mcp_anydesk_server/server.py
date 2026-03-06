"""MCP AnyDesk Server — entry point.

Registers MCP tools conditionally based on preflight dependency checks.
Transport: stdio only (Claude Desktop subprocess).
"""

from __future__ import annotations

import asyncio
import os
import sys

# Windows fix: ProactorEventLoop (default in Python 3.8+) causes
# OSError: [Errno 22] Invalid argument when anyio flushes stdout over
# stdio pipes. SelectorEventLoop handles pipes correctly.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    # Set stdout/stdin to binary mode so MCP stdio transport can write raw bytes
    import msvcrt
    msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
    msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Literal, Optional

from mcp.server.fastmcp import FastMCP

from .config import SCREENSHOT_DEFAULT_SCALE
from .recipes import get_recipe as _get_recipe_impl, list_recipes as _list_recipes_impl
from .startup_checks import PreflightResult, run_preflight

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
preflight: PreflightResult = run_preflight()

# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "AnyDesk MCP Server",
    instructions=(
        "MCP server for remote server administration through a double-hop "
        "AnyDesk connection. Provides keyboard injection, screen reading "
        "(OCR / Base64), screenshot capture, session history, command recipes, "
        "and PII-sanitized output."
    ),
)

# ---------------------------------------------------------------------------
# Session state (singleton, shared across tools)
# ---------------------------------------------------------------------------
from .session_state import session, CommandRecord


# ---------------------------------------------------------------------------
# Tool: get_version  (ALWAYS)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_version() -> dict:
    """Return the MCP server version. Use to verify the correct build is running."""
    from . import __version__
    return {"version": __version__}


# ---------------------------------------------------------------------------
# Tool: get_system_status  (ALWAYS)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_system_status() -> dict:
    """Return the current preflight status of all dependencies.

    Use this at the start of every session to understand which tools are
    available and whether Tesseract OCR is installed.
    """
    from . import __version__
    data = asdict(preflight)
    data["server_version"] = __version__
    data["write_capable"] = preflight.write_capable
    data["read_capable"] = preflight.read_capable
    data["ocr_capable"] = preflight.ocr_capable
    return data


# ---------------------------------------------------------------------------
# Tool: get_session_history  (ALWAYS)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_session_history(last_n: int = 5) -> dict:
    """Return the last N commands executed in this session.

    Output text is truncated to 80 chars in the response for token
    efficiency — full text is preserved in the audit log on disk.

    Args:
        last_n: Number of recent commands to return (default 5).

    Returns:
        Dict with session info and command history.
    """
    return {
        "status": "ok",
        "pinned_hwnd": session.pinned_hwnd,
        "pinned_title": session.pinned_title,
        "ps_version": session.ps_version,
        "sanitizer_bootstrapped": session.sanitizer_bootstrapped,
        "total_steps": session.step_counter,
        "commands": session.get_history(last_n),
    }


# ---------------------------------------------------------------------------
# Tool: log_step_result  (ALWAYS)
# ---------------------------------------------------------------------------
@mcp.tool()
def log_step_result(
    step_number: int,
    success: bool,
    notes: str = "",
) -> dict:
    """Record whether a step succeeded or failed in the audit log.

    Call this after analyzing the output of a command to complete
    the audit record for that step.

    Args:
        step_number: The step number to update.
        success: Whether the command succeeded.
        notes: Optional observations or analysis.

    Returns:
        Dict confirming the log update.
    """
    for cmd in session.commands:
        if cmd.step_number == step_number:
            cmd.success = success
            cmd.notes = notes
            session._write_log_update(cmd)

            # Auto-activate sanitizer on bootstrap confirmation
            # Detects "BOOTSTRAP-OK" in command output or "bootstrap" in notes
            _has_bootstrap_note = notes and "bootstrap" in notes.lower()
            _has_bootstrap_output = (
                cmd.output_text and "BOOTSTRAP-OK" in cmd.output_text
            )
            bootstrap_confirmed = success and (
                _has_bootstrap_note or _has_bootstrap_output
            )
            if bootstrap_confirmed:
                session.sanitizer_bootstrapped = True

            return {
                "status": "ok",
                "step_number": step_number,
                "success": success,
                "note": (
                    "Audit record updated. Sanitizer activated."
                    if bootstrap_confirmed
                    else "Audit record updated."
                ),
            }

    return {
        "status": "error",
        "note": f"Step {step_number} not found in session history.",
    }


# ---------------------------------------------------------------------------
# Tool: export_session  (ALWAYS)
# ---------------------------------------------------------------------------
@mcp.tool()
def export_session() -> dict:
    """Export the current session as a markdown report file.

    Generates a timestamped .md file in ~/.mcp_anydesk/recordings/
    containing all commands, outputs, and outcomes from this session.

    Returns:
        Dict with status and path to the generated report.
    """
    path = session.export_session_report()
    return {"status": "ok", "path": path, "note": f"Report saved to {path}"}


# ---------------------------------------------------------------------------
# Tool: list_recipes  (ALWAYS)
# ---------------------------------------------------------------------------
@mcp.tool()
def list_recipes() -> dict:
    """List all available pre-built command recipes.

    Recipes are named sequences of PowerShell commands for common tasks
    (health check, VM inventory, network check, etc.).
    Use get_recipe(name) to retrieve the commands for a specific recipe.

    Returns:
        Dict with a list of recipes (name, description, step count).
    """
    return {"status": "ok", "recipes": _list_recipes_impl()}


# ---------------------------------------------------------------------------
# Tool: get_recipe  (ALWAYS)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_recipe(name: str) -> dict:
    """Get a recipe's commands by name. Execute each command sequentially.

    Args:
        name: Recipe name. Use list_recipes() to see available names.

    Returns:
        Dict with the recipe description and list of commands.
    """
    recipe = _get_recipe_impl(name)
    if recipe is None:
        available = [r["name"] for r in _list_recipes_impl()]
        return {
            "status": "error",
            "note": f"Recipe '{name}' not found. Available: {available}",
        }
    return {"status": "ok", **recipe}


# ---------------------------------------------------------------------------
# Tools requiring pywin32
# ---------------------------------------------------------------------------
if preflight.pywin32_available:
    from .window_manager import (
        enumerate_anydesk_windows,
        list_sessions as _list_sessions_impl,
        pin_session as _pin_session_impl,
        select_window,
        switch_session as _switch_session_impl,
    )

    @mcp.tool()
    def select_anydesk_window(hwnd: int = 0) -> dict:
        """List AnyDesk windows or pin one for the session.

        Call without arguments (or hwnd=0) to enumerate available windows.
        Call with a specific hwnd to pin that window as the injection target.

        Args:
            hwnd: Window handle to pin. Pass 0 to list available windows.

        Returns:
            Dict with available windows list or pinned window confirmation.
        """
        if hwnd == 0:
            windows = enumerate_anydesk_windows()
            if not windows:
                return {
                    "status": "error",
                    "note": "No AnyDesk sessions detected. Is AnyDesk running?",
                    "windows": [],
                }
            return {
                "status": "ok",
                "note": (
                    "Call select_anydesk_window with the hwnd of the "
                    "desired window to pin it."
                ),
                "windows": [
                    {"hwnd": h, "title": t, "class_name": c}
                    for h, t, c in windows
                ],
            }

        try:
            result = select_window(hwnd)
            session.pinned_hwnd = result["hwnd"]
            session.pinned_title = result["title"]
            return result
        except ValueError as exc:
            return {"status": "error", "note": str(exc)}

    @mcp.tool()
    def pin_session(hwnd: int, name: str) -> dict:
        """Pin an AnyDesk window with a human-readable name (N4).

        Multiple windows can be pinned simultaneously. The newly pinned
        window becomes the active session for all subsequent tool calls.

        Args:
            hwnd: Window handle to pin (from select_anydesk_window).
            name: Human-readable label (e.g., "DC01", "HyperV-Host").

        Returns:
            Status dict with name, hwnd, and title.
        """
        result = _pin_session_impl(hwnd, name)
        if result.get("status") == "pinned":
            session.pinned_hwnd = result["hwnd"]
            session.pinned_title = result["title"]
        return result

    @mcp.tool()
    def switch_session(name: str) -> dict:
        """Switch the active session to a previously pinned named window (N4).

        Args:
            name: Name of a previously pinned session (use list_sessions).

        Returns:
            Status dict with name, hwnd, and title.
        """
        result = _switch_session_impl(name)
        if result.get("status") == "switched":
            session.pinned_hwnd = result["hwnd"]
            session.pinned_title = result["title"]
        return result

    @mcp.tool()
    def list_sessions() -> dict:
        """List all named sessions with their hwnd, title, and active status.

        Returns:
            Dict with a list of all pinned sessions.
        """
        return {"status": "ok", "sessions": _list_sessions_impl()}


# ---------------------------------------------------------------------------
# Tools requiring write capability
# ---------------------------------------------------------------------------
if preflight.write_capable:
    import time as _time
    from .command_templates import bootstrap_steps
    from .keyboard_injector import (
        send_cancel as _cancel_impl,
        write_to_anydesk as _write_impl,
    )
    from .window_manager import enumerate_anydesk_windows, pin_session as _pin_session_impl

    @mcp.tool()
    def initialize_session(window_index: int = 0) -> dict:
        """Pin the AnyDesk window and report system status.

        Does NOT inject bootstrap — sanitizer is OFF by default.
        After this call, tell the operator: "Session ready. Say 'bootstrap'
        to enable PII sanitization, or I'll work without it."

        Args:
            window_index: Which window to pin when multiple AnyDesk windows
                are found (0 = first window).

        Returns:
            Consolidated status with system info, pinned window, and
            a reminder that bootstrap_sanitizer must be called separately.
        """
        system_info = {
            "write_capable": preflight.write_capable,
            "read_capable": preflight.read_capable,
            "ocr_capable": preflight.ocr_capable,
        }

        # Enumerate and pin window
        windows = enumerate_anydesk_windows()
        if not windows:
            return {
                "status": "error",
                "system_status": system_info,
                "window": None,
                "note": "No AnyDesk sessions found. Open AnyDesk and connect first.",
            }

        idx = min(window_index, len(windows) - 1)
        hwnd, title, _ = windows[idx]

        # Use pin_session to register in both legacy _pinned_hwnd and
        # named _sessions dict, so list_sessions() shows this session.
        pin_result = _pin_session_impl(hwnd, "default")
        if pin_result.get("status") == "error":
            return {
                "status": "error",
                "system_status": system_info,
                "window": None,
                "note": f"Failed to pin window {hwnd}: {pin_result.get('note')}",
            }
        session.pinned_hwnd = hwnd
        session.pinned_title = title

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        return {
            "status": "ok",
            "system_status": system_info,
            "window": {
                "hwnd": hwnd,
                "title": title,
                "total_windows_found": len(windows),
            },
            "sanitizer": "NOT ACTIVE — call bootstrap_sanitizer when ready",
            "session_id": ts,
            "note": (
                "Session ready. Sanitizer is OFF — commands work normally without it. "
                "Say 'bootstrap' to enable PII redaction, or just continue."
            ),
        }

    @mcp.tool()
    def write_to_anydesk(
        command: str,
        keystroke_delay_ms: int = 80,
        focus_delay_ms: int = 500,
        wrap: bool = True,
        base64_output: bool = False,
        force_dangerous: bool = False,
    ) -> dict:
        """Inject keystrokes into the pinned AnyDesk window.

        Text is typed character-by-character via KEYEVENTF_UNICODE
        (layout-independent — works with any local keyboard layout).
        Enter is NEVER pressed — the operator must press Enter manually
        after verifying the injected text.

        Commands are wrapped with the sanitizer check by default (wrap=True).
        Set wrap=False only for bootstrap, PS version detection, or non-PS contexts.

        Args:
            command: Text to inject (single line, no newlines).
            keystroke_delay_ms: Delay between characters in ms (min 30).
            focus_delay_ms: Delay after focusing window before typing.
            wrap: If True, auto-wrap with sanitizer check.
            base64_output: If True, use base64 output wrapper (wrap must be True).
                Works without bootstrap (lite wrapper, no PII sanitization).
            force_dangerous: Unused — kept for API compatibility.

        Returns:
            Dict with status, char_count, original_command, wrapped,
            elapsed_ms, and note.
        """
        _sanitizer_warning: str | None = None
        _sanitizer_active = session.sanitizer_bootstrapped

        # Without bootstrap: standard wrap is disabled (S() doesn't exist),
        # but base64 lite wrapper works without it.
        if wrap and not _sanitizer_active and not base64_output:
            wrap = False
            _sanitizer_warning = (
                "Sanitizer not active — injected without PII sanitization. "
                "Call bootstrap_sanitizer to enable."
            )

        result = _write_impl(
            command,
            keystroke_delay_ms,
            focus_delay_ms,
            wrap=wrap,
            base64_output=base64_output,
            force_dangerous=force_dangerous,
            sanitizer_available=_sanitizer_active,
        )

        # Record in session state
        step = session.next_step()
        record = CommandRecord(
            step_number=step,
            timestamp=datetime.now(timezone.utc).isoformat(),
            command_original=result.get("original_command", command),
            command_injected=command if not result.get("wrapped") else "(wrapped)",
            was_wrapped=result.get("wrapped", False),
            char_count=result.get("char_count", 0),
            injection_elapsed_ms=result.get("elapsed_ms", 0),
        )
        session.add_command(record)

        result["step_number"] = step
        if _sanitizer_warning:
            result["sanitizer_warning"] = _sanitizer_warning
        return result

    @mcp.tool()
    def send_cancel() -> dict:
        """Focus AnyDesk and ask the operator to press Ctrl+C manually.

        AnyDesk does NOT forward programmatic Ctrl+C as a real interrupt
        signal. Only a physical Ctrl+C from the operator's keyboard works.

        This tool focuses the AnyDesk window so the operator can press
        Ctrl+C immediately without clicking first.

        Use when the operator says 'abort' or 'cancel', the terminal is
        unresponsive, or a command appears hung.

        Returns:
            Dict with status and instructions.
        """
        return _cancel_impl()

    @mcp.tool()
    def bootstrap_sanitizer() -> dict:
        """Return the ordered list of bootstrap commands to inject (v3.1).

        Does NOT inject anything. Returns 9 short commands (each < 120 chars)
        that the LLM must inject one at a time via write_to_anydesk(wrap=False),
        asking the operator to press Enter after each step.

        After the last step, call read_from_anydesk to verify [BOOTSTRAP-OK]
        appears, then call log_step_result to mark it successful.
        session.sanitizer_bootstrapped is set to True only when the operator
        confirms [BOOTSTRAP-OK] via log_step_result.

        Cooldown: rejects if called within 30s of the previous attempt
        (prevents double-bootstrap from LLM retries).

        Returns:
            Dict with status, total_steps, and list of step dicts
            (step, description, command).
        """
        _COOLDOWN_S = 30
        now = _time.monotonic()
        if session.last_bootstrap_attempt is not None:
            elapsed = now - session.last_bootstrap_attempt
            if elapsed < _COOLDOWN_S:
                remaining = int(_COOLDOWN_S - elapsed)
                return {
                    "status": "cooldown",
                    "note": (
                        f"Bootstrap called {int(elapsed)}s ago. "
                        f"Wait {remaining}s before retrying to avoid "
                        "double-injection. If the previous attempt is still "
                        "in progress, wait for the operator to confirm."
                    ),
                }

        session.last_bootstrap_attempt = now
        session.bootstrap_steps_completed = 0
        steps = bootstrap_steps()
        return {
            "status": "ready",
            "total_steps": len(steps),
            "steps": steps,
            "note": (
                "Inject each step in order with write_to_anydesk(wrap=False). "
                "Ask the operator to press Enter after each step. "
                "After step 9, call read_from_anydesk and verify [BOOTSTRAP-OK]. "
                "Then call log_step_result to confirm. Do NOT skip steps."
            ),
        }


# ---------------------------------------------------------------------------
# Tools requiring read capability
# ---------------------------------------------------------------------------
if preflight.read_capable:
    from .screen_reader import (
        check_session_alive as _check_session_alive,
        read_from_anydesk as _read_impl,
    )
    from .screenshot import capture_screenshot as _screenshot_impl

    @mcp.tool()
    def read_from_anydesk(
        mode: Literal["ocr", "base64"] = "ocr",
        region_x: int = 0,
        region_y: int = 0,
        region_w: int = 0,
        region_h: int = 0,
    ) -> dict:
        """Capture the AnyDesk window and extract text via OCR or Base64.

        OCR mode: Tesseract with OpenCV preprocessing. Auto-retries with 4×
        zoom if confidence < 0.4. Garbled lines stripped when confidence < 0.5.
        Auto-crops to console region. Warns if output appears truncated.

        Base64 mode: Decodes Base64-encoded output with SHA256 checksum
        validation. Use for critical output (hashes, GUIDs, dense tables).

        All output is PII-sanitized before being returned.

        Args:
            mode: "ocr" or "base64".
            region_x: Optional capture region X offset (0 = full window).
            region_y: Optional capture region Y offset.
            region_w: Optional capture region width (0 = full window).
            region_h: Optional capture region height.

        Returns:
            Dict with text, confidence, mode_used, sanitized_fields, warnings.
        """
        region: Optional[tuple[int, int, int, int]] = None
        if region_w > 0 and region_h > 0:
            region = (region_x, region_y, region_w, region_h)

        result = _read_impl(
            mode=mode,
            region=region,
            ocr_available=preflight.ocr_capable,
        )

        # Update last command in session with output
        session.update_last_command(
            operator_confirmed=True,
            output_text=result.get("text", ""),
            output_confidence=result.get("confidence"),
            sanitized_counts=result.get("sanitized_fields"),
        )

        return result

    @mcp.tool()
    def capture_screenshot(
        region_x: int = 0,
        region_y: int = 0,
        region_w: int = 0,
        region_h: int = 0,
        scale_percent: int = SCREENSHOT_DEFAULT_SCALE,
        return_base64: bool = False,
    ) -> dict:
        """Take a JPEG screenshot of the pinned AnyDesk window.

        Saves to disk by default and returns metadata (path, dimensions, size).
        Set return_base64=True to include the image in the response (WARNING:
        this adds ~7KB of base64 text that takes 2-3 min to process in Claude Desktop).

        Use to understand GUI contexts (Hyper-V Manager, Server Manager,
        vSphere), verify visual state, or when OCR confidence is too low.

        NEVER reproduce or describe specific PII visible in screenshots.
        Describe context ("I see Hyper-V Manager with 3 VMs"), not data.

        Args:
            region_x: X offset for sub-region (0 = full window).
            region_y: Y offset for sub-region.
            region_w: Width of sub-region (0 = full window).
            region_h: Height of sub-region.
            scale_percent: Downscale factor (default 25). Lower = fewer tokens.
            return_base64: If True, include image_base64 in the response.
                Default False — saves to disk only to avoid slow processing.

        Returns:
            Dict with dimensions, size_kb, saved_to path, and optionally image_base64.
        """
        result = _screenshot_impl(
            region_x=region_x,
            region_y=region_y,
            region_w=region_w,
            region_h=region_h,
            scale_percent=scale_percent,
            save_to_disk=True,
        )
        if not return_base64 and result.get("status") == "ok":
            result.pop("image_base64", None)
        return result

    @mcp.tool()
    def check_health() -> dict:
        """Verify the window, session, and sanitizer are still healthy.

        Call approximately every 5 interactions to detect silent disconnects
        or window invalidation before attempting injection.

        Returns:
            Dict with status (ok/warning/degraded) and per-component check results.
            - ok: all functional checks pass (injection will work)
            - warning: functional checks pass but sanitizer is inactive (operator choice)
            - degraded: one or more functional checks failed (injection may fail)
        """
        hwnd = session.pinned_hwnd

        # Functional checks (affect whether injection works)
        functional: dict[str, bool] = {
            "window_pinned": hwnd is not None,
        }

        try:
            import win32gui as _win32gui
            functional["window_valid"] = hwnd is not None and bool(
                _win32gui.IsWindow(hwnd)
            )
        except ImportError:
            functional["window_valid"] = hwnd is not None

        # Dead session detection (N6): OCR title bar for disconnect keywords
        if functional.get("window_valid"):
            alive = _check_session_alive()
            functional["session_connected"] = alive["status"] == "connected"
        else:
            functional["session_connected"] = False

        # Optional checks (operator choice, not a functional problem)
        optional: dict[str, bool] = {
            "sanitizer_bootstrapped": session.sanitizer_bootstrapped,
        }

        functional_ok = all(functional.values())
        sanitizer_active = optional["sanitizer_bootstrapped"]

        if functional_ok and sanitizer_active:
            status = "ok"
            note = "All systems go."
        elif functional_ok and not sanitizer_active:
            status = "ok"
            note = (
                "Injection ready. Sanitizer inactive (optional). "
                "Say 'bootstrap' to enable PII redaction."
            )
        else:
            status = "degraded"
            failed = [k for k, v in functional.items() if not v]
            note = f"Functional checks failed: {', '.join(failed)}. Review before continuing."

        return {
            "status": status,
            "checks": {**functional, **optional},
            "pinned_title": session.pinned_title,
            "note": note,
        }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    mcp.run(transport="stdio")
