"""
models.py
=========
SQLAlchemy ORM models.
Tables: posture_sessions, posture_events
"""

from sqlalchemy import Column, Integer, Float, String, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


class PostureSession(Base):
    __tablename__ = "posture_sessions"

    id           = Column(Integer, primary_key=True, index=True)
    started_at   = Column(DateTime(timezone=True), server_default=func.now())
    ended_at     = Column(DateTime(timezone=True), nullable=True)
    duration_sec = Column(Integer, default=0)
    avg_score    = Column(Float,   default=0.0)
    min_score    = Column(Float,   default=0.0)
    max_score    = Column(Float,   default=0.0)
    alert_count  = Column(Integer, default=0)
    notes        = Column(String,  nullable=True)

    events = relationship(
        "PostureEvent",
        back_populates="session",
        cascade="all, delete-orphan",  # delete events when session deleted
    )


class PostureEvent(Base):
    __tablename__ = "posture_events"

    id               = Column(Integer, primary_key=True, index=True)
    session_id       = Column(Integer, ForeignKey("posture_sessions.id", ondelete="CASCADE"), nullable=True)
    posture_score    = Column(Integer, default=0)
    head_angle       = Column(Float,   default=0.0)
    shoulder_slope   = Column(Float,   default=0.0)
    gaze_horizontal  = Column(Float,   default=0.0)
    gaze_vertical    = Column(Float,   default=0.0)
    head_status      = Column(String,  default="Unknown")
    shoulder_status  = Column(String,  default="Unknown")
    eye_status       = Column(String,  default="N/A")
    brightness       = Column(Float,   default=100.0)
    timestamp        = Column(Float,   default=0.0)
    created_at       = Column(DateTime(timezone=True), server_default=func.now())

    session = relationship("PostureSession", back_populates="events")

    # Index for fast cleanup queries
    __table_args__ = (
        Index("ix_events_created_at", "created_at"),
        Index("ix_events_session_id", "session_id"),
    )
