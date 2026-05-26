"""
routes/sessions.py
==================
FIX 5: Pagination validation (can't request 10,000 rows)
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List

from database import get_db
import models, schemas

router = APIRouter()


@router.post("/", response_model=schemas.SessionResponse)
def create_session(payload: schemas.SessionCreate, db: Session = Depends(get_db)):
    session = models.PostureSession(notes=payload.notes)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


@router.get("/", response_model=List[schemas.SessionResponse])
def list_sessions(
    skip:  int = Query(default=0,  ge=0),
    limit: int = Query(default=50, ge=1, le=200),   # max 200 per request
    db: Session = Depends(get_db),
):
    return db.query(models.PostureSession).order_by(
        models.PostureSession.started_at.desc()
    ).offset(skip).limit(limit).all()


@router.get("/{session_id}", response_model=schemas.SessionResponse)
def get_session(session_id: int, db: Session = Depends(get_db)):
    s = db.query(models.PostureSession).filter(
        models.PostureSession.id == session_id
    ).first()
    if not s:
        raise HTTPException(404, "Session not found")
    return s


@router.patch("/{session_id}", response_model=schemas.SessionResponse)
def update_session(
    session_id: int,
    payload: schemas.SessionUpdate,
    db: Session = Depends(get_db),
):
    s = db.query(models.PostureSession).filter(
        models.PostureSession.id == session_id
    ).first()
    if not s:
        raise HTTPException(404, "Session not found")
    for field, val in payload.model_dump(exclude_none=True).items():
        setattr(s, field, val)
    db.commit()
    db.refresh(s)
    return s


@router.delete("/{session_id}")
def delete_session(session_id: int, db: Session = Depends(get_db)):
    s = db.query(models.PostureSession).filter(
        models.PostureSession.id == session_id
    ).first()
    if not s:
        raise HTTPException(404, "Session not found")
    db.delete(s)
    db.commit()
    return {"deleted": session_id}
