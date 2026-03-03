"""Screen reading from the pinned AnyDesk window.

Captures the window region via mss, then extracts text using either
OCR (Tesseract with OpenCV preprocessing) or Base64 decoding with
SHA256 checksum validation.

v3 changes:
- _strip_garbled_lines(): removes lines >50% non-alphanumeric when
  overall confidence < 0.5 (filters video-compression noise).
- Adaptive OCR retry: if confidence < 0.4, re-attempt with 4× zoom
  on the console region and keep the better result.
- _check_truncation(): warns if output doesn't end with PS/CMD prompt
  (N5 output pagination detection).
- check_session_alive(): OCR the title bar for disconnect keywords (N6).
"""

from __future__ import annotations

import base64
import hashlib
import re
from typing import Literal, Optional

import cv2  # type: ignore[import-untyped]
import mss  # type: ignore[import-untyped]
import numpy as np
import win32gui  # type: ignore[import-untyped]

from .config import (
    ANYDESK_DISCONNECT_PATTERNS,
    BASE64_MAX_RETRIES,
    CONSOLE_BG_THRESHOLD,
    CONSOLE_MIN_AREA_RATIO,
    DEFAULT_OCR_OEM,
    DEFAULT_OCR_PSM,
    OCR_DENOISE_H,
    OCR_SHARPEN_AFTER_DENOISE,
    OCR_UPSCALE_FACTOR,
)
from .sanitizer import sanitize
from .startup_checks import TESSERACT_INSTALL_INSTRUCTIONS
from .window_manager import get_pinned_hwnd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Return (x, y, w, h) for the given window handle."""
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    return left, top, right - left, bottom - top


def _capture_region(
    region: Optional[tuple[int, int, int, int]] = None,
) -> np.ndarray:
    """Capture a screen region with mss and return a BGR numpy array.

    Args:
        region: (x, y, w, h) override. If None, captures the pinned window.

    Raises:
        RuntimeError: If no window is pinned or capture fails.
    """
    hwnd = get_pinned_hwnd()
    if hwnd is None:
        raise RuntimeError(
            "No AnyDesk window pinned. Use select_anydesk_window first."
        )

    if region is None:
        x, y, w, h = _get_window_rect(hwnd)
    else:
        x, y, w, h = region

    monitor = {"left": x, "top": y, "width": w, "height": h}

    with mss.mss() as sct:
        shot = sct.grab(monitor)
        # mss returns BGRA; convert to BGR numpy array
        img = np.array(shot)[:, :, :3]

    return img


def _auto_crop_console(img: np.ndarray) -> np.ndarray:
    """Detect and crop to the console/terminal region (dark background).

    Uses contour detection to find the largest dark rectangle in the
    captured window, filtering out taskbar, desktop icons, and other
    non-console areas that pollute OCR.

    Returns the cropped region, or the original image if no suitable
    dark region is found.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    total_area = img.shape[0] * img.shape[1]

    # Threshold: pixels darker than CONSOLE_BG_THRESHOLD → white (console bg)
    _, mask = cv2.threshold(gray, CONSOLE_BG_THRESHOLD, 255, cv2.THRESH_BINARY_INV)

    # Find contours of dark regions
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return img

    # Find the largest dark rectangle that meets the minimum area ratio
    best_rect: tuple[int, int, int, int] | None = None
    best_area = 0

    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = w * h
        if area > best_area and area >= total_area * CONSOLE_MIN_AREA_RATIO:
            best_area = area
            best_rect = (x, y, w, h)

    if best_rect is None:
        return img

    x, y, w, h = best_rect
    return img[y : y + h, x : x + w]


def _preprocess_for_ocr(img: np.ndarray) -> np.ndarray:
    """OpenCV preprocessing pipeline for double-compressed video text.

    1. Grayscale
    2. Invert — Tesseract performs better on dark-text-on-light-background
    3. Adaptive thresholding (Gaussian) — handles compression gradients
    4. Denoising — removes H.264/H.265 mosquito noise
    5. 3x upscale with INTER_CUBIC — improves Tesseract accuracy

    v3.2 changes: invert colors (terminal is light-on-dark), blockSize
    11→17, C 2→3, denoising h 10→18, upscale 2→3x.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Invert: terminal is light text on dark bg; Tesseract needs dark on light
    gray = cv2.bitwise_not(gray)

    thresh = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=17,
        C=3,
    )

    denoised = cv2.fastNlMeansDenoising(thresh, h=OCR_DENOISE_H)

    # Sharpen to recover edges softened by denoising
    if OCR_SHARPEN_AFTER_DENOISE:
        sharpen_kernel = np.array(
            [[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32
        )
        denoised = cv2.filter2D(denoised, -1, sharpen_kernel)

    # Morphological closing — reconnects broken character strokes
    close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    denoised = cv2.morphologyEx(denoised, cv2.MORPH_CLOSE, close_kernel)

    h, w = denoised.shape[:2]
    upscaled = cv2.resize(
        denoised,
        (w * OCR_UPSCALE_FACTOR, h * OCR_UPSCALE_FACTOR),
        interpolation=cv2.INTER_CUBIC,
    )

    return upscaled


# ---------------------------------------------------------------------------
# OCR mode
# ---------------------------------------------------------------------------

def _ocr_extract(img: np.ndarray) -> tuple[str, float, list[str]]:
    """Run Tesseract OCR on a preprocessed image.

    Returns:
        (text, confidence_0_to_1, warnings)
    """
    import pytesseract  # type: ignore[import-untyped]

    config = f"--psm {DEFAULT_OCR_PSM} --oem {DEFAULT_OCR_OEM}"

    # Get detailed data for confidence calculation
    data = pytesseract.image_to_data(
        img, config=config, output_type=pytesseract.Output.DICT
    )

    text = pytesseract.image_to_string(img, config=config)

    # Calculate average confidence (exclude -1 entries = non-text)
    confidences = [
        int(c) for c in data["conf"] if int(c) >= 0
    ]
    avg_confidence = (
        sum(confidences) / len(confidences) / 100.0
        if confidences
        else 0.0
    )

    # Warn on low-confidence regions
    warnings: list[str] = []
    if avg_confidence < 0.6:
        # Find which lines have low confidence
        low_lines: list[int] = []
        line_confs: dict[int, list[int]] = {}
        for i, conf_val in enumerate(data["conf"]):
            conf_int = int(conf_val)
            if conf_int < 0:
                continue
            line_num = data["line_num"][i]
            line_confs.setdefault(line_num, []).append(conf_int)

        for line_num, confs in line_confs.items():
            if sum(confs) / len(confs) < 60:
                low_lines.append(line_num)

        if low_lines:
            if len(low_lines) == 1:
                warnings.append(f"Low OCR confidence on line {low_lines[0]}")
            else:
                start, end = low_lines[0], low_lines[-1]
                warnings.append(f"Low OCR confidence on lines {start}-{end}")

    text = text.strip()

    # Strip garbled lines on low-confidence reads
    text, garbled_count = _strip_garbled_lines(text, avg_confidence)
    if garbled_count:
        warnings.append(
            f"Stripped {garbled_count} garbled line(s) (>50% non-alphanumeric)."
        )

    return text, avg_confidence, warnings


# ---------------------------------------------------------------------------
# Base64 mode
# ---------------------------------------------------------------------------

_BASE64_CHARSET_FIXES = str.maketrans({
    "O": "0",
    "l": "1",
    "I": "1",
    "|": "1",
})

_BASE64_PATTERN = re.compile(r"[A-Za-z0-9+/=\s]+")
_CRC_PATTERN = re.compile(r"\[CRC:([0-9A-Fa-f]{8})\]")

# Matches a PS or CMD prompt at end of output:  "PS C:\foo>" or "C:\Users\>"
_PROMPT_PATTERN = re.compile(
    r"(?:PS\s+)?[A-Za-z]:\\[^\n]*>\s*$",
    re.MULTILINE,
)


def _strip_garbled_lines(text: str, confidence: float) -> tuple[str, int]:
    """Remove lines with >50% non-alphanumeric characters when confidence < 0.5.

    Video-compression artifacts often produce lines of symbols (╗║╔═…) that
    confuse downstream parsing. This filter is only active on low-confidence
    reads to avoid stripping intentional punctuation on clean captures.

    Returns:
        (cleaned_text, number_of_lines_stripped)
    """
    if confidence >= 0.5:
        return text, 0

    lines = text.splitlines()
    clean: list[str] = []
    stripped = 0

    for line in lines:
        stripped_line = line.strip()
        if not stripped_line:
            clean.append(line)
            continue
        alnum = sum(1 for c in stripped_line if c.isalnum())
        if alnum / len(stripped_line) < 0.5:
            stripped += 1
        else:
            clean.append(line)

    return "\n".join(clean), stripped


def _check_truncation(text: str) -> str | None:
    """Return a warning if output doesn't end with a PS or CMD prompt.

    Heuristic for N5 output pagination detection: if the last non-blank
    line doesn't match a typical prompt (PS C:\\…> or C:\\…>), the output
    was likely truncated by the console scroll buffer.

    Returns the warning string, or None if a prompt is detected.
    """
    if not text.strip():
        return None
    if _PROMPT_PATTERN.search(text):
        return None
    return (
        "Output may be truncated — no PS/CMD prompt detected at end. "
        "If the command produced many lines, retry with '| Select-Object -First 20'."
    )


def _base64_extract(img: np.ndarray) -> tuple[str, float, list[str]]:
    """OCR a Base64-encoded block with SHA256 checksum validation.

    Returns:
        (decoded_text, confidence_1_or_0, warnings)
    """
    import pytesseract  # type: ignore[import-untyped]

    config = f"--psm {DEFAULT_OCR_PSM} --oem {DEFAULT_OCR_OEM}"
    raw = pytesseract.image_to_string(img, config=config).strip()

    # Extract CRC
    crc_match = _CRC_PATTERN.search(raw)
    if crc_match is None:
        return "", 0.0, ["No [CRC:xxxxxxxx] found in captured text."]

    expected_crc = crc_match.group(1).upper()

    # Extract Base64 payload (everything before the CRC tag)
    payload_text = raw[: crc_match.start()].strip()

    # Keep only valid Base64 characters
    b64_chars = "".join(_BASE64_PATTERN.findall(payload_text))
    b64_chars = b64_chars.replace(" ", "").replace("\n", "").replace("\r", "")

    # Apply charset correction heuristics
    b64_corrected = b64_chars.translate(_BASE64_CHARSET_FIXES)

    # Decode
    try:
        decoded_bytes = base64.b64decode(b64_corrected)
    except Exception:
        return "", 0.0, ["Base64 decode failed after charset correction."]

    # Validate checksum
    actual_crc = (
        hashlib.sha256(decoded_bytes).hexdigest()[:8].upper()
    )

    if actual_crc != expected_crc:
        return "", 0.0, [
            f"CRC mismatch: expected {expected_crc}, got {actual_crc}."
        ]

    decoded_text = decoded_bytes.decode("utf-8", errors="replace")
    return decoded_text, 1.0, []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_from_anydesk(
    mode: Literal["ocr", "base64"] = "ocr",
    region: Optional[tuple[int, int, int, int]] = None,
    *,
    ocr_available: bool = True,
) -> dict:
    """Capture the AnyDesk window and extract text.

    Args:
        mode: "ocr" for Tesseract OCR, "base64" for Base64+CRC decoding.
        region: Optional (x, y, w, h) override for capture area.
        ocr_available: Set to False when Tesseract is not installed
            (base64-only mode).

    Returns:
        Dict with text, confidence, mode_used, sanitized_fields, warnings.
    """
    # --- Gate: OCR not available ---
    if mode == "ocr" and not ocr_available:
        return {
            "text": "",
            "confidence": 0.0,
            "mode_used": "none",
            "sanitized_fields": {},
            "warnings": [
                "OCR mode unavailable — Tesseract is not installed.",
                TESSERACT_INSTALL_INSTRUCTIONS,
                'Retry with mode="base64" if the remote output is Base64-encoded.',
            ],
        }

    # --- Capture ---
    try:
        img = _capture_region(region)
    except RuntimeError as exc:
        return {
            "text": "",
            "confidence": 0.0,
            "mode_used": mode,
            "sanitized_fields": {},
            "warnings": [str(exc)],
        }

    # --- Auto-crop to console region (OCR mode, no explicit region) ---
    if mode == "ocr" and region is None:
        img = _auto_crop_console(img)

    # --- Preprocess ---
    processed = _preprocess_for_ocr(img)

    # --- Extract ---
    if mode == "ocr":
        text, confidence, warnings = _ocr_extract(processed)

        # Adaptive retry: zoom 4× if confidence is below threshold
        if confidence < 0.55:
            zoomed = cv2.resize(
                img,
                (img.shape[1] * 4, img.shape[0] * 4),
                interpolation=cv2.INTER_CUBIC,
            )
            processed_zoom = _preprocess_for_ocr(zoomed)
            text_z, conf_z, warns_z = _ocr_extract(processed_zoom)
            if conf_z > confidence:
                text, confidence, warnings = text_z, conf_z, warns_z
                warnings.append(
                    f"Adaptive OCR: confidence improved to {confidence:.2f} with 4× zoom."
                )
            else:
                warnings.append(
                    f"Adaptive OCR retry did not improve confidence "
                    f"({conf_z:.2f} ≤ {confidence:.2f})."
                )

        # Pagination detection (N5)
        trunc_warn = _check_truncation(text)
        if trunc_warn:
            warnings.append(trunc_warn)

    else:
        # Base64 mode with retry
        text = ""
        confidence = 0.0
        warnings: list[str] = []

        for attempt in range(1 + BASE64_MAX_RETRIES):
            text, confidence, warnings = _base64_extract(processed)
            if confidence > 0.0:
                break
            # Re-capture for retry
            if attempt < BASE64_MAX_RETRIES:
                try:
                    img = _capture_region(region)
                    processed = _preprocess_for_ocr(img)
                except RuntimeError as exc:
                    warnings.append(f"Re-capture failed: {exc}")
                    break

        if confidence == 0.0 and mode == "base64":
            warnings.append(
                "Base64 decoding failed after all retries. "
                "Please read the output manually."
            )

    # --- Sanitize (Layer 2) ---
    result = sanitize(text)

    return {
        "text": result.text,
        "confidence": confidence,
        "mode_used": mode,
        "sanitized_fields": result.counts,
        "warnings": warnings,
    }


def _quick_ocr(region: tuple[int, int, int, int]) -> str:
    """Capture a region and run a minimal OCR pass without preprocessing.

    Used for dead session detection where keyword presence matters more
    than accuracy. Suppresses all exceptions — returns "" on any failure.

    Args:
        region: (x, y, w, h) screen coordinates to capture.

    Returns:
        Lowercase OCR text, or "" on failure.
    """
    try:
        import pytesseract  # type: ignore[import-untyped]

        img = _capture_region(region)
        config = f"--psm {DEFAULT_OCR_PSM} --oem {DEFAULT_OCR_OEM}"
        return pytesseract.image_to_string(img, config=config).lower()
    except Exception:
        return ""


def check_session_alive() -> dict:
    """Check whether the AnyDesk session appears connected (N6).

    Captures the top 50 pixels of the pinned window (title bar area) and
    runs a quick OCR pass to detect disconnect keywords from
    ANYDESK_DISCONNECT_PATTERNS ("connecting", "session interrupted", etc.).

    Returns:
        Dict with status ("connected", "disconnected", or "error") and note.
    """
    hwnd = get_pinned_hwnd()
    if hwnd is None:
        return {
            "status": "error",
            "note": "No AnyDesk window pinned.",
        }

    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        w = right - left
        h = bottom - top
        region = (left, top, w, min(50, h))
    except Exception as exc:
        return {"status": "error", "note": f"Could not get window rect: {exc}"}

    text = _quick_ocr(region)

    for pattern in ANYDESK_DISCONNECT_PATTERNS:
        if pattern in text:
            return {
                "status": "disconnected",
                "detected": pattern,
                "note": (
                    "AnyDesk session appears disconnected. "
                    "Reconnect before continuing."
                ),
            }

    return {"status": "connected", "note": "Session appears active."}
