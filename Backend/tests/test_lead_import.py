import io
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import import_api
import lead_import as li
from database import (DNCList, ImportBatch, IndustryProfile, Lead, LeadSource, OutreachMessage,
                      PipelineStage, get_db)
from industries import normalize_industry


# ─── field cleaning ──────────────────────────────────────────────────────────

def test_emails():
    assert li.clean_email("  Dana@Example.COM ") == ("dana@example.com", None)
    assert li.clean_email("mailto:a@b.com, c@d.com")[0] == "a@b.com"
    assert li.clean_email("not an email")[0] is None and li.clean_email("not an email")[1]
    assert li.clean_email("test@site.com")[0] is None                      # placeholder
    assert li.clean_email("") == (None, None)


def test_phones():
    assert li.clean_phone("(512) 555-0142") == ("+15125550142", None)
    assert li.clean_phone("1-512-555-0142 ext. 12") == ("+15125550142", None)
    assert li.clean_phone("+44 20 7946 0958") == ("+442079460958", None)
    assert li.clean_phone("555-0142")[0] is None and li.clean_phone("555-0142")[1]
    assert li.clean_phone("(012) 555-0142")[0] is None                      # area code cannot start with 0


def test_websites():
    assert li.clean_website("www.Example.com/") == ("https://www.example.com", None)
    assert li.clean_website("http://shop.example.com/a/b?utm=1")[0] == "https://shop.example.com/a/b"
    assert li.clean_website("facebook.com/mybiz")[0] is None
    assert li.clean_website("two words")[0] is None
    assert li.website_domain("https://www.Example.com/x") == "example.com"


def test_states_and_city_state_split():
    assert li.clean_state("texas") == "TX" and li.clean_state("tx") == "TX" and li.clean_state("ZZ") is None
    assert li.split_city_state("Austin, TX 78701") == ("Austin", "TX")
    assert li.split_city_state("Austin") == ("Austin", None)


def test_formula_characters_are_stripped_from_text():
    row = li.clean_row({"_row": 2, "business_name": '=HYPERLINK("x")Acme', "email": "a@acme.com"})
    assert not row["data"]["business_name"].startswith("=")


def test_industry_matching():
    assert normalize_industry("Heating & Air Conditioning") == "hvac"
    assert normalize_industry("hvac-companies") == "hvac"
    assert normalize_industry("Dentist") == "dental"
    assert normalize_industry("Smith & Jones Attorneys at Law") == "law-firms"
    assert normalize_industry("Med Spa") == "med-spa"
    assert normalize_industry("bakery") is None


# ─── reading files ───────────────────────────────────────────────────────────

def test_csv_with_bom_and_semicolons_and_pasted_tabs():
    csv_bytes = "﻿Business;Email\nAcme;a@acme.com\n".encode("utf-8")
    assert li.read_table("x.csv", csv_bytes, None) == [["Business", "Email"], ["Acme", "a@acme.com"]]
    assert li.read_table(None, None, "Company\tEmail\nAcme\ta@acme.com") == [["Company", "Email"], ["Acme", "a@acme.com"]]


def test_xlsx_numbers_become_text():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["Business Name", "Phone"])
    ws.append(["Acme", 5125550142])
    buf = io.BytesIO()
    wb.save(buf)
    table = li.read_table("leads.xlsx", buf.getvalue(), None)
    assert table == [["Business Name", "Phone"], ["Acme", "5125550142"]]


def test_empty_and_old_excel_are_refused_politely():
    with pytest.raises(li.ImportProblem):
        li.read_table(None, None, "   ")
    with pytest.raises(li.ImportProblem):
        li.read_table("old.xls", b"x", None)


def test_header_aliases_and_first_last_name():
    headers = ["Company Name", "First Name", "Last Name", "E-mail", "Mobile", "Industry", "Favourite colour"]
    mapping = li.map_headers(headers)
    assert mapping == {0: "business_name", 1: "first_name", 2: "last_name", 3: "email", 4: "phone", 5: "industry"}
    rows = li.rows_to_dicts([headers, ["Acme", "Dana", "Rivera", "d@acme.com", "5125550142", "hvac", "blue"]], mapping)
    assert rows[0]["contact_name"] == "Dana Rivera" and rows[0]["extra"] == {"Favourite colour": "blue"}


# ─── row status ──────────────────────────────────────────────────────────────

def make(db, rows, default=None):
    table = [["business_name", "email", "website", "phone", "industry", "city", "state"]] + rows
    return li.build_preview(db, table, use_ai=False, default_industry=default)


def test_statuses_duplicates_and_dnc(db):
    db.add(Lead(name="Old Co", email="old@oldco.com", website="https://oldco.com", source=LeadSource.MANUAL))
    db.add(DNCList(email="stop@blocked.com", reason="asked"))
    db.add(DNCList(domain="banned.com"))
    db.commit()

    out = make(db, [
        ["Fresh HVAC", "new@fresh.com", "fresh.com", "(512) 555-0142", "hvac", "Austin", "TX"],       # 2 ready
        ["Fresh Again", "new@fresh.com", "", "", "hvac", "", ""],                                    # 3 same email in file
        ["Old Name", "old@oldco.com", "", "", "dental", "", ""],                                     # 4 already in DB
        ["Blocked", "stop@blocked.com", "", "", "dental", "", ""],                                   # 5 DNC email
        ["Banned Site", "", "www.banned.com", "", "dental", "", ""],                                 # 6 DNC domain
        ["", "x@y.com", "", "", "", "", ""],                                                         # 7 no name
        ["No Contact", "", "", "", "", "", ""],                                                      # 8 nothing to reach
        ["Site Only", "", "siteonly.com", "", "plumbing", "Denver", "Colorado"],                     # 9 ready, no email
    ])
    status = {r["row"]: r["status"] for r in out["rows"]}
    assert status == {2: "ready", 3: "duplicate", 4: "duplicate", 5: "dnc", 6: "dnc", 7: "invalid", 8: "invalid", 9: "ready"}
    assert out["summary"] == {"total": 8, "ready": 2, "duplicate": 2, "dnc": 2, "invalid": 2, "no_email": 1}
    site_only = out["rows"][-1]
    assert site_only["data"]["state"] == "CO" and any("No email" in w for w in site_only["warnings"])


def test_default_industry_fills_only_missing(db):
    out = make(db, [["A Co", "a@a.com", "", "", "", "", ""], ["B Co", "b@b.com", "", "", "hvac", "", ""]], default="Dental")
    assert [r["data"]["industry"] for r in out["rows"]] == ["dental", "hvac"]


# ─── Groq help (never trusted blindly) ───────────────────────────────────────

def test_ai_tags_industry_but_ignores_invalid_answers(db, monkeypatch):
    monkeypatch.setattr(li, "ai_available", lambda: True)
    import utils.groq_helper as groq

    def fake(system, user, max_tokens=1000, temperature=0.3):
        return '```json\n[{"i": 2, "industry": "dental"}, {"i": 3, "industry": "made-up"}]\n```'

    monkeypatch.setattr(groq, "_call_groq", fake)
    table = [["business_name", "email"], ["Bright Smiles", "a@bright.com"], ["Mystery Inc", "b@mystery.com"]]
    out = li.build_preview(db, table, use_ai=True)
    by_row = {r["row"]: r for r in out["rows"]}
    assert by_row[2]["data"]["industry"] == "dental" and by_row[2]["ai_fixed"] == ["industry"]
    assert by_row[3]["data"]["industry"] is None
    assert "tagged the industry of 1 of 2" in " ".join(out["notes"])


def test_ai_off_never_calls_groq(db, monkeypatch):
    import utils.groq_helper as groq
    monkeypatch.setattr(groq, "_call_groq", lambda *a, **k: pytest.fail("Groq must not be called"))
    li.build_preview(db, [["business_name", "email"], ["Acme", "a@acme.com"]], use_ai=False)


def test_ai_maps_unknown_columns_without_overwriting_known_ones(db, monkeypatch):
    monkeypatch.setattr(li, "ai_available", lambda: True)
    import utils.groq_helper as groq
    monkeypatch.setattr(groq, "_call_groq", lambda *a, **k: json.dumps(
        [{"i": 1, "field": "email"}, {"i": 2, "field": "phone"}, {"i": 3, "field": "bogus"}]))
    table = [["business_name", "Contact Mail", "Ring", "Misc"], ["Acme", "a@acme.com", "5125550142", "x"]]
    out = li.build_preview(db, table, use_ai=True)
    assert out["mapping"] == {"0": "business_name", "1": "email", "2": "phone"}


def test_free_text_extraction_drops_values_not_in_the_text(db, monkeypatch):
    monkeypatch.setattr(li, "ai_available", lambda: True)
    import utils.groq_helper as groq
    monkeypatch.setattr(groq, "_call_groq", lambda *a, **k: json.dumps([
        {"business_name": "Acme Heating", "email": "dana@acme.com", "phone": "512-555-0142", "website": "acme.com"},
        {"business_name": "Acme Heating", "email": "invented@acme.com", "phone": "999-999-9999", "website": "other.com"},
    ]))
    text = "Acme Heating - Dana - dana@acme.com - 512-555-0142 - acme.com"
    rows = li.ai_extract_rows(text)
    assert rows[0]["email"] == "dana@acme.com" and rows[0]["phone"] == "512-555-0142"
    assert rows[1]["email"] == "" and rows[1]["phone"] == "" and rows[1]["website"] == ""


def test_text_without_headings_needs_the_ai(db, monkeypatch):
    monkeypatch.setattr(li, "ai_available", lambda: False)
    with pytest.raises(li.ImportProblem):
        li.build_preview(db, [["Acme Heating", "dana@acme.com"]], text="Acme Heating, dana@acme.com")


# ─── saving ──────────────────────────────────────────────────────────────────

def test_save_creates_leads_with_geo_industry_and_greeting_name(seeded_db):
    db = seeded_db
    db.add(IndustryProfile(name="HVAC", slug="hvac", config={}, is_active=True))
    db.commit()
    out = li.build_preview(db, [
        ["business_name", "contact_name", "email", "phone", "website", "industry", "city", "state"],
        ["Fresh HVAC", "Dana Rivera", "dana@fresh.com", "512-555-0142", "fresh.com", "HVAC", "Austin", "TX"],
        ["Dup", "", "dana@fresh.com", "", "", "", "", ""],
    ], use_ai=False)
    batch = li.save_leads(db, out["rows"], batch_name="Test", source="pasted text", default_industry=None,
                          total_rows=2, ai_used=False)
    assert (batch.imported, batch.duplicates) == (1, 1)

    lead = db.query(Lead).one()
    assert lead.name == "Fresh HVAC" and lead.email == "dana@fresh.com" and lead.phone == "+15125550142"
    assert lead.niche == "HVAC" and lead.source == LeadSource.MANUAL and lead.import_batch_id == batch.id
    assert lead.country == "United States" and lead.timezone == "America/Chicago"
    assert lead.linkedin_name == "Dana Rivera" and not lead.linkedin_found
    assert lead.profile_id == db.query(IndustryProfile).filter_by(slug="hvac").one().id
    assert lead.attributes["email_is_role"] is False


# ─── endpoints ───────────────────────────────────────────────────────────────

@pytest.fixture
def api(seeded_db):
    app = FastAPI()
    app.include_router(import_api.router)
    app.dependency_overrides[get_db] = lambda: seeded_db
    return TestClient(app), seeded_db


CSV = (b"business_name,email,website,industry,city,state\n"
       b"Fresh HVAC,dana@fresh.com,fresh.com,hvac,Austin,TX\n"
       b"Bright Dental,hi@bright.com,bright.com,dental,Denver,CO\n")


def as_commit_rows(body):
    return [{**r["data"], "row": r["row"]} for r in body["rows"]]


def test_preview_then_commit_then_undo(api):
    client, db = api
    pv = client.post("/imports/preview", files={"file": ("leads.csv", CSV, "text/csv")}, data={"use_ai": "false"})
    assert pv.status_code == 200
    body = pv.json()
    assert body["summary"]["ready"] == 2 and body["source"] == "file: leads.csv"
    assert db.query(Lead).count() == 0                                        # preview saves nothing

    commit = client.post("/imports/commit", json={
        "batch_name": "First", "source": body["source"], "total_rows": 2, "rows": as_commit_rows(body)})
    assert commit.status_code == 200 and commit.json()["imported"] == 2
    batch_id = commit.json()["batch_id"]
    assert db.query(Lead).count() == 2

    # the same rows again are all duplicates now: nothing is saved twice
    again = client.post("/imports/commit", json={"batch_name": "Again", "rows": as_commit_rows(body)}).json()
    assert again["imported"] == 0 and again["duplicates"] == 2

    listing = client.get("/imports").json()
    assert [b["name"] for b in listing] == ["Again", "First"] and listing[1]["leads_now"] == 2

    assert client.delete(f"/imports/{batch_id}").json() == {"deleted_leads": 2}
    assert db.query(Lead).count() == 0 and db.get(ImportBatch, batch_id) is None


def test_commit_skip_no_email_and_server_side_rechecks(api):
    client, db = api
    rows = [{"business_name": "Has Email", "email": "a@a.com", "industry": "hvac"},
            {"business_name": "Site Only", "website": "siteonly.com", "industry": "hvac"},
            {"business_name": "Bad Email", "email": "nope", "industry": "hvac"}]               # no way to reach: refused here too
    res = client.post("/imports/commit", json={"batch_name": "x", "rows": rows, "skip_no_email": True}).json()
    assert res["imported"] == 1 and res["skipped"] == 1 and res["invalid"] == 1


def test_undo_is_refused_once_a_lead_was_contacted(api):
    client, db = api
    client.post("/imports/commit", json={"batch_name": "x", "rows": [{"business_name": "A", "email": "a@a.com"}]})
    lead = db.query(Lead).one()
    lead.pipeline_stage = PipelineStage.EMAIL_SENT
    db.commit()
    batch = db.query(ImportBatch).one()
    assert client.delete(f"/imports/{batch.id}").status_code == 409
    assert db.query(Lead).count() == 1


def test_undo_removes_unsent_drafts_with_the_leads(api):
    client, db = api
    client.post("/imports/commit", json={"batch_name": "x", "rows": [{"business_name": "A", "email": "a@a.com"}]})
    lead = db.query(Lead).one()
    db.add(OutreachMessage(lead_id=lead.id, channel="email", step_key="email_1", subject="Hi", body="Hello", status="draft"))
    db.commit()
    assert client.delete(f"/imports/{db.query(ImportBatch).one().id}").status_code == 200
    assert db.query(OutreachMessage).count() == 0


def test_bad_input_gives_clear_errors(api):
    client, _ = api
    assert client.post("/imports/preview", data={"text": "   "}).status_code == 422
    assert client.post("/imports/preview", data={"text": "a,b\n1,2", "default_industry": "zzz"}).status_code == 422
    assert client.post("/imports/preview", data={"text": "business_name,email\nA,a@a.com", "mapping": "{bad"}).status_code == 422
    assert client.delete("/imports/999").status_code == 404


def test_sample_and_industries_endpoints(api):
    client, _ = api
    sample = client.get("/imports/sample.csv")
    assert sample.headers["content-type"].startswith("text/csv")
    assert sample.text.splitlines()[0] == "business_name,contact_name,contact_title,email,phone,website,industry,city,state"
    assert {"key": "hvac", "label": "HVAC"} in client.get("/imports/industries").json()
    # the sample must itself import cleanly
    pv = client.post("/imports/preview", files={"file": ("s.csv", sample.content, "text/csv")}, data={"use_ai": "false"}).json()
    assert pv["summary"]["invalid"] == 0 and pv["summary"]["ready"] == 4
