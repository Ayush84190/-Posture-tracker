"""
routes/stats.py
===============
FIX 5: Response caching (30s) — stats query won't hammer DB on every refresh
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
import time

from database import get_db
import models

router = APIRouter()

# Simple in-memory cache
_cache: dict = {}
CACHE_TTL = 30  # seconds


def _cached(key: str, fn, *args):
    now = time.time()
    if key in _cache and now - _cache[key]["ts"] < CACHE_TTL:
        return _cache[key]["data"]
    result = fn(*args)
    _cache[key] = {"ts": now, "data": result}
    return result


def _build_overview(db: Session) -> dict:
    total_sessions = db.query(func.count(models.PostureSession.id)).scalar() or 0
    total_minutes  = db.query(func.sum(models.PostureSession.duration_sec)).scalar() or 0
    avg_score      = db.query(func.avg(models.PostureEvent.posture_score)).scalar() or 0
    best_score     = db.query(func.max(models.PostureEvent.posture_score)).scalar() or 0
    worst_score    = db.query(func.min(
        models.PostureEvent.posture_score)).filter(
        models.PostureEvent.posture_score > 0
    ).scalar() or 0
    total_alerts   = db.query(func.sum(models.PostureSession.alert_count)).scalar() or 0

    trend_rows = db.query(
        models.PostureSession.id,
        models.PostureSession.started_at,
        models.PostureSession.avg_score,
    ).order_by(
        models.PostureSession.started_at.desc()
    ).limit(20).all()

    score_trend = [
        {
            "session": r.id,
            "date":    str(r.started_at)[:10],
            "score":   round(float(r.avg_score or 0), 1),
        }
        for r in reversed(trend_rows)
    ]

    return {
        "total_sessions": total_sessions,
        "total_minutes":  round(float(total_minutes or 0) / 60, 1),
        "avg_score":      round(float(avg_score), 1),
        "best_score":     float(best_score),
        "worst_score":    float(worst_score),
        "total_alerts":   int(total_alerts or 0),
        "score_trend":    score_trend,
    }


def _build_hourly(db: Session) -> list:
    rows = db.query(
        func.strftime("%H", models.PostureEvent.created_at).label("hour"),
        func.avg(models.PostureEvent.posture_score).label("avg_score"),
        func.count(models.PostureEvent.id).label("count"),
    ).group_by("hour").order_by("hour").all()

    return [
        {
            "hour":      int(r.hour),
            "avg_score": round(float(r.avg_score), 1),
            "count":     r.count,
        }
        for r in rows
    ]


@router.get("/overview")
def overview(db: Session = Depends(get_db)):
    return _cached("overview", _build_overview, db)


@router.get("/hourly")
def hourly(db: Session = Depends(get_db)):
    return _cached("hourly", _build_hourly, db)
