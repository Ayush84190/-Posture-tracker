"""
main.py
=======
FastAPI backend — all 5 fixes applied:

  FIX 1: Connection pooling + WAL mode         → database.py
  FIX 2: Input validation on WebSocket frames  → schemas.PostureFrame
  FIX 3: Frame batching (write every 5s)       → _batch_writer()
  FIX 4: Auto session cleanup on startup       → cleanup.run_startup_cleanup()
  FIX 5: Data pruning every 24h               → cleanup.start_background_cleanup()

Run:
    pip install -r requirements.txt
    uvicorn main:app --reload --port 8000
"""

import json
import time
import asyncio
import logging
from contextlib import asynccontextmanager
from collections import deque

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
from sqlalchemy.orm import Session

from database import engine, get_db
import models
from schemas import PostureFrame
from websocket_manager import ConnectionManager
from cleanup import run_startup_cleanup, start_background_cleanup
import sessions
import events
import stats

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("posture.main")


# ── STARTUP / SHUTDOWN ───────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables
    models.Base.metadata.create_all(bind=engine)
    logger.info("Database tables ready")

    # FIX 4: Fix any sessions left open from crashes
    run_startup_cleanup()

    # FIX 5: Start 24-hour background pruning thread
    start_background_cleanup()

    # Start batch writer inside lifespan (replaces deprecated on_event)
    asyncio.create_task(_batch_writer())
    logger.info("Batch writer started")

    yield
    logger.info("Shutting down")


# ── APP ──────────────────────────────────────────────────────────────
app = FastAPI(
    title="PostureAI Backend",
    version="2.0.0",
    description="Posture tracking API with WebSocket live stream",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],     # personal use — open is fine
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sessions.router, prefix="/api/sessions", tags=["sessions"])
app.include_router(events.router,   prefix="/api/events",   tags=["events"])
app.include_router(stats.router,    prefix="/api/stats",    tags=["stats"])

manager = ConnectionManager()


# ── FIX 3: BATCH WRITER ─────────────────────────────────────────────
# Frames arrive ~30/sec. Writing every frame = ~2M DB rows/day.
# Instead: collect frames in memory, flush to DB every 5 seconds.

_frame_buffer: deque = deque(maxlen=500)   # cap at 500 frames in memory
_BATCH_INTERVAL = 5.0                       # seconds between DB writes


async def _batch_writer():
    """Background task that flushes buffered frames to DB every 5 seconds."""
    while True:
        await asyncio.sleep(_BATCH_INTERVAL)
        if not _frame_buffer:
            continue

        # Drain the buffer
        batch = []
        while _frame_buffer:
            batch.append(_frame_buffer.popleft())

        # Write in one transaction
        db = None
        try:
            from database import SessionLocal
            db = SessionLocal()
            db.bulk_insert_mappings(models.PostureEvent, [
                {
                    "session_id":      f.session_id,
                    "posture_score":   f.posture_score,
                    "head_angle":      f.head_forward_angle,
                    "shoulder_slope":  f.shoulder_slope,
                    "gaze_horizontal": f.gaze_horizontal,
                    "gaze_vertical":   f.gaze_vertical,
                    "head_status":     f.head_position_status,
                    "shoulder_status": f.shoulder_status,
                    "eye_status":      f.eye_contact_status,
                    "brightness":      f.brightness,
                    "timestamp":       f.timestamp,
                }
                for f in batch
            ])
            db.commit()
            logger.debug(f"Batch wrote {len(batch)} frames to DB")
        except Exception as exc:
            logger.error(f"Batch write error: {exc}")
            if db:
                db.rollback()
        finally:
            if db:
                db.close()


# ── HEALTH ───────────────────────────────────────────────────────────
@app.get("/api/health", tags=["health"])
def health():
    return {
        "status":    "ok",
        "timestamp": time.time(),
        "version":   "2.0.0",
        "buffered_frames": len(_frame_buffer),
    }


# ── WEBSOCKET ────────────────────────────────────────────────────────
@app.websocket("/ws/posture")
async def posture_ws(websocket: WebSocket):
    """
    Desktop tracker connects here.
    - FIX 2: Each frame is validated with PostureFrame schema
    - FIX 3: Valid frames go to buffer, not directly to DB
    - Bad JSON / bad data → logged and skipped, never crashes
    """
    await manager.connect(websocket)
    logger.info("Tracker connected via WebSocket")

    try:
        while True:
            raw = await websocket.receive_text()

            # ── FIX 2: Validate incoming frame ──────────────────────
            try:
                frame = PostureFrame.model_validate_json(raw)
            except ValidationError as e:
                logger.warning(f"Invalid frame rejected: {e.error_count()} errors")
                continue
            except json.JSONDecodeError:
                logger.warning("Malformed JSON from tracker — skipped")
                continue

            # ── FIX 3: Buffer frame (don't write to DB yet) ──────────
            _frame_buffer.append(frame)

            # Broadcast to all browser dashboards immediately
            await manager.broadcast(raw)

    except WebSocketDisconnect:
        manager.disconnect(websocket)
        logger.info("Tracker disconnected")
    except Exception as exc:
        logger.error(f"WebSocket error: {exc}")
        manager.disconnect(websocket)
