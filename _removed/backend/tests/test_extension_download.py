import io
import json
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

import extension_api


def client():
    app = FastAPI()
    app.include_router(extension_api.router)
    return TestClient(app)


def test_the_zip_holds_the_extension_in_one_folder_without_tests():
    r = client().get("/extension/download")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert 'filename="leads-scraper-whatsapp-extension.zip"' in r.headers["content-disposition"]

    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert {n.split("/")[0] for n in names} == {"WhatsApp_Extension"}          # unzips into a single folder
    for needed in ("manifest.json", "background.js", "content.js", "page-bridge.js", "popup.html", "shared.js"):
        assert f"WhatsApp_Extension/{needed}" in names
    assert not any("/tests/" in n for n in names)

    manifest = json.loads(zipfile.ZipFile(io.BytesIO(r.content)).read("WhatsApp_Extension/manifest.json"))
    assert manifest["manifest_version"] == 3


def test_a_server_without_the_files_answers_clearly(monkeypatch, tmp_path):
    monkeypatch.setattr(extension_api, "EXTENSION_DIR", str(tmp_path))
    assert client().get("/extension/download").status_code == 404
