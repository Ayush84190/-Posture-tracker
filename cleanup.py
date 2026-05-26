"""
cleanup.py
==========
FIX 3: Auto-close orphaned sessions (tracker crashed without saving end time)
FIX 4: Prune posture_events older than 30 days (DB stays small forever)

Runs automatically:
  - Orphan fix  → on every backend startup
  - Data pruning → every 24 hours in background thread
"""

import threading
import time
import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from database import SessionLocal
import models

logger = logging.getLogger(__name__)

PRUNE_DAYS    = 30    # delete events older than this
STALE_HOURS   = 2     # sessions open longer than this are orphaned


def fix_orphaned_sessions(db: Session) -> int:
    """
    Close any session that has no ended_at and was started more than
    STALE_HOURS ago. These are sessions where the tracker crashed.
    Returns the number of sessions fixed.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=STALE_HOURS)

    orphans = db.query(models.PostureSession).filter(
        models.PostureSession.ended_at == None,          # noqa: E711
        models.PostureSession.started_at < cutoff,
    ).all()

    for session in orphans:
        # Calculate real duration from its events if any
        events = db.query(models.PostureEvent).filter(
            models.PostureEvent.session_id == session.id
        ).all()

        if events:
            scores   = [e.posture_score for e in events if e.posture_score > 0]
            avg      = round(sum(scores) / len(scores), 1) if scores else 0.0
            duration = int(events[-1].timestamp - events[0].timestamp) if len(events) > 1 else 0
            session.avg_score    = avg
            session.min_score    = min(scores) if scores else 0.0
            session.max_score    = max(scores) if scores else 0.0
            session.duration_sec = duration

        session.ended_at = datetime.now(timezone.utc)
        logger.info(f"Auto-closed orphaned session #{session.id}")

    if orphans:
        db.commit()

    return len(orphans)


def prune_old_events(db: Session) -> int:
    """
    Delete posture_events older than PRUNE_DAYS.
    Keeps the DB from growing forever.
    Returns number of rows deleted.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=PRUNE_DAYS)

    deleted = db.query(models.PostureEvent).filter(
        models.PostureEvent.created_at < cutoff
    ).delete(synchronize_session=False)

    db.commit()

    if deleted:
        logger.info(f"Pruned {deleted} old posture events (>{PRUNE_DAYS} days)")

    return deleted


def run_startup_cleanup():
    """Call this once when the backend starts."""
    db = SessionLocal()
    try:
        fixed   = fix_orphaned_sessions(db)
        pruned  = prune_old_events(db)
        logger.info(f"Startup cleanup: {fixed} orphaned sessions fixed, {pruned} old events pruned")
    except Exception as exc:
        logger.error(f"Startup cleanup error: {exc}")
    finally:
        db.close()


def _daily_prune_loop():
    """Background thread that prunes every 24 hours."""
    while True:
        time.sleep(86400)  # 24 hours
        db = SessionLocal()
        try:
            prune_old_events(db)
        except Exception as exc:
            logger.error(f"Daily prune error: {exc}")
        finally:
            db.close()


def start_background_cleanup():
    """Start the 24-hour background cleanup thread."""
    t = threading.Thread(target=_daily_prune_loop, daemon=True, name="CleanupThread")
    t.start()
    logger.info("Background cleanup thread started (runs every 24h)")
