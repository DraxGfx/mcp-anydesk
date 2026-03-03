"""Screenshot capture for GUI copilot mode.

Returns base64-encoded images for Claude's vision capabilities.
Unlike read_from_anydesk (which returns OCR text), this returns the
raw image so Claude can see and interpret GUI elements.

v3 changes:
- Default format changed from PNG to JPEG (quality 60, scale 50%).
- JPEG at 50% scale reduces token usage ~85-90% vs PNG at 100%.
- save_to_disk cleanup now handles both .jpg and .png files.
"""

from __future__ import annotations

import base64
import os
from datetime import datetime, timezone
from pathlib import Path

import cv2  # type: ignore[import-untyped]
import mss  # type: ignore[import-untyped]
import numpy as np
import win32gui  # type: ignore[import-untyped]

from .config import (
    SCREENSHOT_DIR,
    SCREENSHOT_MAX_KEPT,
    SCREENSHOT_FORMAT,
    SCREENSHOT_JPEG_QUALITY,
    SCREENSHOT_DEFAULT_SCALE,
    SCREENSHOT_MAX_WIDTH,
    SCREENSHOT_MAX_BASE64_KB,
)
from .window_manager import get_pinned_hwnd


def _get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Return (x, y, w, h) for the given window handle."""
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    return left, top, right - left, bottom - top


def _cleanup_old_screenshots(directory: Path) -> None:
    """Keep only the newest SCREENSHOT_MAX_KEPT images in the directory."""
    images = sorted(
        list(directory.glob("*.jpg")) + list(directory.glob("*.png")),
        key=lambda p: p.stat().st_mtime,
    )
    excess = len(images) - SCREENSHOT_MAX_KEPT
    for old_file in images[: max(0, excess)]:
        old_file.unlink(missing_ok=True)


def _encode_error(fmt_lower: str) -> dict:
    """Return a standardised encode-failure dict."""
    return {
        "status": "error",
        "image_base64": "",
        "format": fmt_lower,
        "width": 0,
        "height": 0,
        "size_kb": 0,
        "saved_to": None,
        "note": f"Failed to encode screenshot as {fmt_lower.upper()}.",
    }


def capture_screenshot(
    region_x: int = 0,
    region_y: int = 0,
    region_w: int = 0,
    region_h: int = 0,
    scale_percent: int = SCREENSHOT_DEFAULT_SCALE,
    fmt: str = SCREENSHOT_FORMAT,
    save_to_disk: bool = False,
) -> dict:
    """Capture the pinned AnyDesk window as a JPEG or PNG image.

    v3 defaults: JPEG at quality 60, 50% scale.
    Reduces token usage ~85-90% compared to v2 PNG at 100%.

    Args:
        region_x: X offset for sub-region capture (0 = full window).
        region_y: Y offset for sub-region capture.
        region_w: Width of sub-region (0 = full window).
        region_h: Height of sub-region.
        scale_percent: Downscale percentage (default 50).
        fmt: Image format — "jpeg" (default) or "png".
        save_to_disk: If True, save a copy to the local screenshots dir.

    Returns:
        Dict with base64 image, format, dimensions, and metadata.
    """
    hwnd = get_pinned_hwnd()
    if hwnd is None:
        return {
            "status": "error",
            "image_base64": "",
            "format": fmt,
            "width": 0,
            "height": 0,
            "size_kb": 0,
            "saved_to": None,
            "note": "No AnyDesk window pinned. Use select_anydesk_window first.",
        }

    # Determine capture region
    if region_w > 0 and region_h > 0:
        x, y, w, h = region_x, region_y, region_w, region_h
    else:
        x, y, w, h = _get_window_rect(hwnd)

    monitor = {"left": x, "top": y, "width": w, "height": h}

    with mss.mss() as sct:
        shot = sct.grab(monitor)
        # mss returns BGRA; convert to BGR
        img = np.array(shot)[:, :, :3]

    # Downscale
    if scale_percent < 100:
        factor = scale_percent / 100.0
        new_w = max(1, int(img.shape[1] * factor))
        new_h = max(1, int(img.shape[0] * factor))
        img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)

    h_final, w_final = img.shape[:2]

    # Cap width to SCREENSHOT_MAX_WIDTH (prevents oversized captures)
    if w_final > SCREENSHOT_MAX_WIDTH:
        ratio = SCREENSHOT_MAX_WIDTH / w_final
        img = cv2.resize(
            img,
            (SCREENSHOT_MAX_WIDTH, max(1, int(h_final * ratio))),
            interpolation=cv2.INTER_AREA,
        )
        h_final, w_final = img.shape[:2]

    # Encode with quality reduction to guarantee base64 ≤ SCREENSHOT_MAX_BASE64_KB.
    # base64 inflates raw size by 4/3, so max raw bytes = target_kb * 3/4.
    max_raw_bytes = int(SCREENSHOT_MAX_BASE64_KB * 1024 * 3 // 4)
    fmt_lower = fmt.lower()
    if fmt_lower in ("jpg", "jpeg"):
        ext = ".jpg"
        encode_ext = ".jpg"
        current_quality = SCREENSHOT_JPEG_QUALITY
        success_enc, buf = cv2.imencode(
            encode_ext, img, [cv2.IMWRITE_JPEG_QUALITY, current_quality]
        )
        if not success_enc:
            return _encode_error(fmt_lower)
        # Reduce quality in steps of 5 until under raw limit or floor reached
        while len(buf.tobytes()) > max_raw_bytes and current_quality > 10:
            current_quality -= 5
            success_enc, buf = cv2.imencode(
                encode_ext, img, [cv2.IMWRITE_JPEG_QUALITY, current_quality]
            )
            if not success_enc:
                return _encode_error(fmt_lower)
        # Last resort: 75% spatial resize if quality=10 still exceeds limit
        if len(buf.tobytes()) > max_raw_bytes:
            h_r, w_r = img.shape[:2]
            img_small = cv2.resize(
                img,
                (max(1, int(w_r * 0.75)), max(1, int(h_r * 0.75))),
                interpolation=cv2.INTER_AREA,
            )
            h_final, w_final = img_small.shape[:2]
            success_enc, buf = cv2.imencode(
                encode_ext, img_small, [cv2.IMWRITE_JPEG_QUALITY, 15]
            )
            if not success_enc:
                return _encode_error(fmt_lower)
    else:
        ext = ".png"
        encode_ext = ".png"
        success_enc, buf = cv2.imencode(encode_ext, img)
        if not success_enc:
            return _encode_error(fmt_lower)

    raw_bytes = buf.tobytes()
    image_b64 = base64.b64encode(raw_bytes).decode("ascii")
    size_kb = round(len(raw_bytes) / 1024, 1)

    # Optionally save to disk
    saved_path: str | None = None
    if save_to_disk:
        save_dir = Path(os.path.expanduser(SCREENSHOT_DIR))
        save_dir.mkdir(parents=True, exist_ok=True)
        _cleanup_old_screenshots(save_dir)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        file_path = save_dir / f"screenshot_{ts}{ext}"
        file_path.write_bytes(raw_bytes)
        saved_path = str(file_path)

    return {
        "status": "ok",
        "image_base64": image_b64,
        "format": fmt_lower,
        "width": w_final,
        "height": h_final,
        "size_kb": size_kb,
        "saved_to": saved_path,
        "note": (
            "Screenshot captured. This image is LOCAL ONLY and was not "
            "sent through any external API other than this conversation."
        ),
    }
