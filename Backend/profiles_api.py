"""
profiles_api.py — REST endpoints for Industry Profiles
------------------------------------------------------
Profiles are archived, never deleted.

    GET    /profiles                  list (add ?include_archived=true)
    POST   /profiles                  create
    GET    /profiles/{id}             one profile
    PUT    /profiles/{id}             edit (bumps version)
    POST   /profiles/{id}/duplicate   copy
    POST   /profiles/{id}/archive     hide from pickers
    POST   /profiles/{id}/restore     bring back
    GET    /profiles/{id}/export      download as JSON
    POST   /profiles/import           create from an exported JSON
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

import profile_service as svc
from database import get_db
from profile_schema import ProfileIn, ProfileOut

router = APIRouter(prefix="/profiles", tags=["Profiles"])


class DuplicateRequest(BaseModel):
    name: Optional[str] = None


def _call(fn, *args, **kwargs):
    """Run a service call and turn its errors into clean HTTP errors."""
    try:
        return fn(*args, **kwargs)
    except svc.ProfileNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except svc.ProfileConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except (ValueError, ValidationError) as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("", response_model=list[ProfileOut])
def list_profiles(include_archived: bool = False, db: Session = Depends(get_db)):
    return [svc.to_out(db, p) for p in svc.list_profiles(db, include_archived)]


@router.post("", response_model=ProfileOut, status_code=201)
def create_profile(body: ProfileIn, db: Session = Depends(get_db)):
    return svc.to_out(db, _call(svc.create_profile, db, body))


# Declared before "/{profile_id}" so "import" is not read as an id.
@router.post("/import", response_model=ProfileOut, status_code=201)
def import_profile(payload: dict, db: Session = Depends(get_db)):
    return svc.to_out(db, _call(svc.import_profile, db, payload))


@router.get("/{profile_id}", response_model=ProfileOut)
def get_profile(profile_id: int, db: Session = Depends(get_db)):
    return svc.to_out(db, _call(svc.get_profile, db, profile_id))


@router.put("/{profile_id}", response_model=ProfileOut)
def update_profile(profile_id: int, body: ProfileIn, db: Session = Depends(get_db)):
    return svc.to_out(db, _call(svc.update_profile, db, profile_id, body))


@router.post("/{profile_id}/duplicate", response_model=ProfileOut, status_code=201)
def duplicate_profile(profile_id: int, body: DuplicateRequest = DuplicateRequest(),
                      db: Session = Depends(get_db)):
    return svc.to_out(db, _call(svc.duplicate_profile, db, profile_id, body.name))


@router.post("/{profile_id}/archive", response_model=ProfileOut)
def archive_profile(profile_id: int, db: Session = Depends(get_db)):
    return svc.to_out(db, _call(svc.set_archived, db, profile_id, True))


@router.post("/{profile_id}/restore", response_model=ProfileOut)
def restore_profile(profile_id: int, db: Session = Depends(get_db)):
    return svc.to_out(db, _call(svc.set_archived, db, profile_id, False))


@router.get("/{profile_id}/export")
def export_profile(profile_id: int, db: Session = Depends(get_db)):
    return _call(svc.export_profile, db, profile_id)
