"""
routes/events.py
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import List

from database import get_db
import models, schemas

router = APIRouter()


@router.get("/recent", response_model=List[schemas.EventResponse])
def recent_events(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    return db.query(models.PostureEvent).order_by(
        models.PostureEvent.created_at.desc()
    ).limit(limit).all()


@router.get("/session/{session_id}", response_model=List[schemas.EventResponse])
def events_for_session(
    session_id: int,
    limit: int = Query(default=500, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    return db.query(models.PostureEvent).filter(
        models.PostureEvent.session_id == session_id
    ).order_by(
        models.PostureEvent.created_at.asc()
    ).limit(limit).all()
