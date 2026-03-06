"""Preflight check helper for install.ps1 — outputs JSON to stdout."""
import json
from mcp_anydesk_server.startup_checks import run_preflight
from mcp_anydesk_server import __version__

r = run_preflight()
print(json.dumps({
    "version": __version__,
    "pywin32": r.pywin32_available,
    "mss": r.mss_available,
    "opencv": r.opencv_available,
    "tesseract": r.tesseract_available,
    "tesseract_version": r.tesseract_version,
    "write_capable": r.write_capable,
    "read_capable": r.read_capable,
    "ocr_capable": r.ocr_capable,
}))
