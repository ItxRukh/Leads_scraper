"""
import_api.py — The Import page's endpoints
-------------------------------------------
    GET    /imports/industries   the industries a lead can be tagged with
    GET    /imports/sample.csv   a template with the columns we use
    POST   /imports/preview      read a file or pasted text, clean it, flag duplicates (saves nothing)
    POST   /imports/commit       save the good rows as leads under one batch
    GET    /imports              earlier imports
    POST   /imports/{id}/audit   check the websites of the batch's leads and score them (runs in the background)
    GET    /imports/{id}/audit-status   how far the audit is, and how many leads are Hot / Warm / Cold
    DELETE /imports/{id}         undo an import, only while none of its leads has been contacted
"""

import json
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

import lead_import as li
from database import (CallLog, EmailLog, ImportBatch, InboundMessage, Lead, LeadContact, LeadTier, OutreachMessage,
                       OutreachQueueEntry, OutreachStepLog, PipelineStage, SyncSessionLocal, WhatsAppLog, get_db)
from industries import industry_options, normalize_industry

router = APIRouter(prefix="/imports", tags=["Import"])

_UNDOABLE_STAGES = {PipelineStage.SCRAPED, PipelineStage.AUDITED, PipelineStage.LINKEDIN_ENRICHED, PipelineStage.EMAIL_VERIFIED}


@router.get("/industries")
def industries():
    return industry_options()


@router.get("/sample.csv")
def sample():
    return PlainTextResponse(li.sample_csv(), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="leads_sample.csv"'})


@router.post("/preview")
async def preview(
    file: Optional[UploadFile] = File(None),
    text: Optional[str] = Form(None),
    mapping: Optional[str] = Form(None),            # JSON {"0": "email", "3": "ignore"}: the person's own column choices
    default_industry: Optional[str] = Form(None),
    use_ai: bool = Form(True),
    db: Session = Depends(get_db),
):
    if default_industry and not normalize_industry(default_industry):
        raise HTTPException(422, "Unknown industry")

    override = None
    if mapping:
        try:
            allowed = set(li.FIELDS) | {"first_name", "last_name", "notes"}
            override = {int(i): f for i, f in json.loads(mapping).items() if f in allowed}
        except (ValueError, AttributeError):
            raise HTTPException(422, "The column mapping is not valid")

    try:
        if file is not None:
            content = await file.read()
            table = li.read_table(file.filename, content, None)
            raw_text = None if (file.filename or "").lower().endswith((".xlsx", ".xlsm")) else \
                content.decode("utf-8-sig", errors="replace")
        else:
            table = li.read_table(None, None, text)
            raw_text = text
        result = li.build_preview(db, table, text=raw_text, mapping_override=override,
                                  default_industry=default_industry, use_ai=use_ai)
    except li.ImportProblem as e:
        raise HTTPException(422, str(e))

    result["source"] = f"file: {file.filename}" if file is not None else "pasted text"
    return result


class CommitRow(BaseModel):
    row: Optional[int] = None
    business_name: str = ""
    contact_name: Optional[str] = None
    contact_title: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    industry: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None
    extra: dict = Field(default_factory=dict)
    ai_fixed: list[str] = Field(default_factory=list)


class CommitBody(BaseModel):
    batch_name: str = Field(min_length=1, max_length=200)
    source: str = "pasted text"
    default_industry: Optional[str] = None
    total_rows: int = 0
    ai_used: bool = False
    skip_no_email: bool = False
    rows: list[CommitRow] = Field(max_length=li.MAX_ROWS)


@router.post("/commit")
def commit(body: CommitBody, db: Session = Depends(get_db)):
    """
    The rows come back from the browser, so they are cleaned and checked against the database again here:
    nothing is trusted just because the preview said it was fine, and leads added in between are caught.
    """
    raw_rows = [{**r.model_dump(exclude={"extra", "ai_fixed", "notes", "row"}), "_row": r.row or n,
                 "extra": r.extra, "notes": r.notes} for n, r in enumerate(body.rows, start=1)]
    results = [li.clean_row(r, body.default_industry) for r in raw_rows]
    for result, source_row in zip(results, body.rows):
        result["ai_fixed"] = [f for f in source_row.ai_fixed if f in ("industry", "state")]
    li.classify(results, db)

    skipped = 0
    if body.skip_no_email:
        for r in results:
            if r["status"] == "ready" and not r["data"]["email"]:
                r["status"] = "skipped"
                skipped += 1

    batch = li.save_leads(db, results, batch_name=body.batch_name, source=body.source,
                          default_industry=body.default_industry, total_rows=body.total_rows or len(results),
                          ai_used=body.ai_used)
    return {"batch_id": batch.id, "imported": batch.imported, "duplicates": batch.duplicates,
            "on_dnc": batch.on_dnc, "invalid": batch.invalid, "skipped": skipped}


@router.get("")
def list_batches(db: Session = Depends(get_db)):
    counts = dict(db.query(Lead.import_batch_id, func.count(Lead.id))
                  .filter(Lead.import_batch_id.isnot(None)).group_by(Lead.import_batch_id).all())
    audited = dict(db.query(Lead.import_batch_id, func.count(Lead.id))
                   .filter(Lead.import_batch_id.isnot(None), Lead.audited_at.isnot(None)).group_by(Lead.import_batch_id).all())
    batches = db.query(ImportBatch).order_by(ImportBatch.id.desc()).limit(50).all()
    return [{"id": b.id, "name": b.name, "source": b.source, "imported": b.imported, "leads_now": counts.get(b.id, 0),
             "audited": audited.get(b.id, 0),
             "duplicates": b.duplicates, "on_dnc": b.on_dnc, "invalid": b.invalid, "total_rows": b.total_rows,
             "ai_used": bool(b.ai_used), "default_industry": b.default_industry,
             "created_at": b.created_at.isoformat() + "Z" if b.created_at else None} for b in batches]


def audit_batch(batch_id: int, db: Optional[Session] = None) -> None:
    """Audits every not-yet-audited lead of a batch, one after another. One failing lead does not stop the rest."""
    from scripts.auditor import audit_lead

    own_session = db is None
    db = db or SyncSessionLocal()
    try:
        ids = [i for (i,) in db.query(Lead.id).filter(Lead.import_batch_id == batch_id, Lead.audited_at.is_(None))]
        logger.info(f"[import] auditing {len(ids)} lead(s) of batch {batch_id}")
        for lead_id in ids:
            try:
                audit_lead(db.get(Lead, lead_id), db)
            except Exception as e:                                 # a bad website must not stop the others
                logger.error(f"[import] audit failed for lead {lead_id}: {e}")
                db.rollback()
    finally:
        if own_session:
            db.close()


@router.post("/{batch_id}/audit", status_code=202)
def start_audit(batch_id: int, background: BackgroundTasks, db: Session = Depends(get_db)):
    if not db.get(ImportBatch, batch_id):
        raise HTTPException(404, "Import not found")
    pending = db.query(func.count(Lead.id)).filter(Lead.import_batch_id == batch_id, Lead.audited_at.is_(None)).scalar()
    if not pending:
        return {"started": False, "pending": 0}
    background.add_task(audit_batch, batch_id)
    return {"started": True, "pending": pending}


@router.get("/{batch_id}/audit-status")
def audit_status(batch_id: int, db: Session = Depends(get_db)):
    if not db.get(ImportBatch, batch_id):
        raise HTTPException(404, "Import not found")
    leads = db.query(Lead.audited_at, Lead.tier, Lead.score_incomplete).filter(Lead.import_batch_id == batch_id).all()
    audited = [row for row in leads if row.audited_at]
    tiers = [row.tier for row in audited]
    return {"total": len(leads), "audited": len(audited),
            "hot": tiers.count(LeadTier.HOT), "warm": tiers.count(LeadTier.WARM), "cold": tiers.count(LeadTier.COLD),
            "incomplete": sum(1 for row in audited if row.score_incomplete)}


@router.delete("/{batch_id}")
def undo(batch_id: int, db: Session = Depends(get_db)):
    batch = db.get(ImportBatch, batch_id)
    if not batch:
        raise HTTPException(404, "Import not found")

    leads = db.query(Lead).filter(Lead.import_batch_id == batch_id).all()
    ids = [lead.id for lead in leads]

    contacted = (
        any(lead.pipeline_stage not in _UNDOABLE_STAGES or lead.do_not_contact for lead in leads)
        or (ids and any(db.query(m.id).filter(m.lead_id.in_(ids)).first() for m in (EmailLog, CallLog, WhatsAppLog, InboundMessage)))
        or (ids and db.query(OutreachMessage.id).filter(OutreachMessage.lead_id.in_(ids),
                                                        OutreachMessage.status.in_(["approved", "claimed", "sent"])).first())
    )
    if contacted:
        raise HTTPException(409, "Some of these leads have already been contacted or changed, so the import cannot be undone.")

    if ids:
        for model in (OutreachStepLog, OutreachMessage, OutreachQueueEntry, LeadContact):
            db.query(model).filter(model.lead_id.in_(ids)).delete(synchronize_session=False)
        db.query(Lead).filter(Lead.id.in_(ids)).delete(synchronize_session=False)
    db.delete(batch)
    db.commit()
    return {"deleted_leads": len(ids)}
