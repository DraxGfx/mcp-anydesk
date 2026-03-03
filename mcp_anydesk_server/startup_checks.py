"""Preflight validation — checks all dependencies at server startup."""

from __future__ import annotations

import shutil
import subprocess
import re
from dataclasses import dataclass, field


TESSERACT_INSTALL_INSTRUCTIONS = """\
Tesseract OCR not found in PATH.

Installation on Windows:
1. Download from: https://github.com/UB-Mannheim/tesseract/wiki
   (use the 64-bit .exe installer)
2. During installation, check "Add to PATH"
3. Verify with: tesseract --version

Or via winget:
  winget install UB-Mannheim.TesseractOCR

Or via choco:
  choco install tesseract

Restart the MCP server after installing."""


@dataclass
class PreflightResult:
    tesseract_available: bool = False
    tesseract_version: str | None = None
    tesseract_instructions: str | None = None
    opencv_available: bool = False
    pywin32_available: bool = False
    pynput_available: bool = False
    mss_available: bool = False
    anydesk_windows: list[tuple] = field(default_factory=list)

    @property
    def write_capable(self) -> bool:
        return self.pywin32_available and self.pynput_available

    @property
    def read_capable(self) -> bool:
        return self.pywin32_available and self.mss_available

    @property
    def ocr_capable(self) -> bool:
        return self.read_capable and self.tesseract_available


def _check_import(module_name: str) -> bool:
    """Try to import a module, return True if successful."""
    try:
        __import__(module_name)
        return True
    except ImportError:
        return False


def _check_tesseract() -> tuple[bool, str | None, str | None]:
    """Check Tesseract binary availability and version.

    Returns:
        (available, version_string, install_instructions_or_None)
    """
    path = shutil.which("tesseract")
    if path is None:
        return False, None, TESSERACT_INSTALL_INSTRUCTIONS

    try:
        result = subprocess.run(
            ["tesseract", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # tesseract --version prints to stderr on most platforms
        output = result.stdout + result.stderr
        match = re.search(r"(\d+\.\d+\.\d+)", output)
        if match:
            version = match.group(1)
            major = int(version.split(".")[0])
            if major >= 5:
                return True, version, None
            else:
                return True, version, (
                    f"Tesseract {version} detected (< 5.0). "
                    "LSTM engine requires >= 5.0 — OCR accuracy may be degraded."
                )
        return True, "unknown", "Could not parse Tesseract version."
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False, None, TESSERACT_INSTALL_INSTRUCTIONS


def _find_anydesk_windows() -> list[tuple]:
    """Enumerate visible AnyDesk windows. Returns [(hwnd, title), ...].

    Non-blocking: returns empty list if pywin32 is unavailable.
    """
    try:
        import win32gui  # type: ignore[import-untyped]
    except ImportError:
        return []

    windows: list[tuple] = []

    def _enum_callback(hwnd: int, _extra: object) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        class_name = win32gui.GetClassName(hwnd)
        import re
        is_anydesk_class = "anydesk" in class_name.lower()
        is_anydesk_title = bool(re.search(r'(?<![_\w])AnyDesk(?![_\w])', title))
        if is_anydesk_class or is_anydesk_title:
            windows.append((hwnd, title))

    win32gui.EnumWindows(_enum_callback, None)
    return windows


def run_preflight() -> PreflightResult:
    """Execute all preflight checks and return the result."""
    tess_available, tess_version, tess_instructions = _check_tesseract()

    result = PreflightResult(
        tesseract_available=tess_available,
        tesseract_version=tess_version,
        tesseract_instructions=tess_instructions,
        opencv_available=_check_import("cv2"),
        pywin32_available=_check_import("win32gui"),
        pynput_available=_check_import("pynput"),
        mss_available=_check_import("mss"),
    )

    if result.pywin32_available:
        result.anydesk_windows = _find_anydesk_windows()

    return result
