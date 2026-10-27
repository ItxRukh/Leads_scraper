"""
extension_api.py — Download the WhatsApp Chrome extension as a zip
------------------------------------------------------------------
    GET /extension/download

Chrome does not let a website install an extension, so the admin panel offers the zip and the
steps to load it (unzip, chrome://extensions, Developer mode, Load unpacked). The tests folder
is left out.
"""

import io
import os
import zipfile

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

router = APIRouter(prefix="/extension", tags=["Extension"])

EXTENSION_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "WhatsApp_Extension")
_SKIP_DIRS = {"tests", "__pycache__", "node_modules"}
FILE_NAME = "leads-scraper-whatsapp-extension.zip"


def build_zip(directory: str = EXTENSION_DIR) -> bytes:
    """The extension's files in a zip, inside one top-level folder so unzipping makes a clean folder."""
    buffer = io.BytesIO()
    root_name = os.path.basename(directory.rstrip("\\/"))
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for folder, dirs, files in os.walk(directory):
            dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
            for name in sorted(files):
                full = os.path.join(folder, name)
                archive.write(full, os.path.join(root_name, os.path.relpath(full, directory)))
    return buffer.getvalue()


@router.get("/download")
def download():
    if not os.path.isfile(os.path.join(EXTENSION_DIR, "manifest.json")):
        raise HTTPException(status_code=404, detail="The extension files are not on this server")
    return Response(content=build_zip(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{FILE_NAME}"'})
