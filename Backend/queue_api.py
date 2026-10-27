"""
queue_api.py — REST endpoints for the Outreach Queue
----------------------------------------------------
    GET  /outreach-queue                    list (?status=queued|paused|removed &tier= &profile_id= &search= &due=true)
    GET  /outreach-queue/plan               what is due now per channel, with spaced release times
    GET  /outreach-queue/schedule-settings  sending rules shared by all profiles
    PUT  /outreach-queue/schedule-settings  change them
    POST /outreach-queue/sync               queue every qualifying lead not queued yet
    POST /outreach-queue/{lead_id}/add      add a lead by hand (or bring a removed one back)
    POST /outreach-queue/{lead_id}/pause
    POST /outreach-queue/{lead_id}/resume
    POST /outreach-queue/{lead_id}/remove
    POST /outreach-queue/{lead_id}/done     the current step was carried out; plan the next
    POST /outreach-queue/{lead_id}/skip     skip the current step
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

import queue_service as q
import scheduling
from database import get_db

router = APIRouter(prefix="/outreach-queue", tags=["Outreach Queue"])


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("")
def list_queue(
    status: str = Query(q.QUEUED, pattern="^(queued|paused|removed)$"),
    tier: Optional[str] = Query(None, pattern="^(hot|warm|cold)$"),
    profile_id: Optional[int] = None,
    search: Optional[str] = None,
    due: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return q.list_queue(db, status=status, tier=tier, profile_id=profile_id, search=search,
                        due=due, limit=limit, offset=offset)


@router.get("/plan")
def plan(db: Session = Depends(get_db)):
    return scheduling.build_send_plan(db)


@router.get("/schedule-settings")
def get_schedule_settings():
    return scheduling.load_settings()


@router.put("/schedule-settings")
def put_schedule_settings(body: scheduling.ScheduleSettings):
    return scheduling.save_settings(body)


@router.post("/sync")
def sync(profile_id: Optional[int] = None, db: Session = Depends(get_db)):
    return {"added": q.sync_queue(db, profile_id)}


@router.post("/{lead_id}/add")
def add(lead_id: int, db: Session = Depends(get_db)):
    entry = _call(q.add_manually, db, lead_id)
    return {"lead_id": entry.lead_id, "status": entry.status}


@router.post("/{lead_id}/pause")
def pause(lead_id: int, db: Session = Depends(get_db)):
    return {"lead_id": lead_id, "status": _call(q.set_status, db, lead_id, q.PAUSED).status}


@router.post("/{lead_id}/resume")
def resume(lead_id: int, db: Session = Depends(get_db)):
    return {"lead_id": lead_id, "status": _call(q.set_status, db, lead_id, q.QUEUED).status}


@router.post("/{lead_id}/remove")
def remove(lead_id: int, db: Session = Depends(get_db)):
    return {"lead_id": lead_id, "status": _call(q.set_status, db, lead_id, q.REMOVED, "Removed by admin").status}


@router.post("/{lead_id}/done")
def done(lead_id: int, db: Session = Depends(get_db)):
    entry = _call(scheduling.complete_step, db, lead_id)
    return {"lead_id": lead_id, "next_step": entry.next_step_key, "sequence_status": entry.sequence_status}


@router.post("/{lead_id}/skip")
def skip(lead_id: int, db: Session = Depends(get_db)):
    entry = _call(scheduling.skip_step, db, lead_id)
    return {"lead_id": lead_id, "next_step": entry.next_step_key, "sequence_status": entry.sequence_status}
