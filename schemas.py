"""
schemas.py — Pydantic request/response schemas
"""

from pydantic import BaseModel, field_validator
from typing import Optional, List
from datetime import datetime


class SessionCreate(BaseModel):
    notes: Optional[str] = None


class SessionUpdate(BaseModel):
    ended_at:     Optional[datetime] = None
    duration_sec: Optional[int]      = None
    avg_score:    Optional[float]    = None
    min_score:    Optional[float]    = None
    max_score:    Optional[float]    = None
    alert_count:  Optional[int]      = None
    notes:        Optional[str]      = None

    @field_validator("avg_score", "min_score", "max_score", mode="before")
    @classmethod
    def clamp_score(cls, v):
        if v is not None:
            return max(0.0, min(100.0, float(v)))
        return v

    @field_validator("duration_sec", "alert_count", mode="before")
    @classmethod
    def non_negative(cls, v):
        if v is not None:
            return max(0, int(v))
        return v


class SessionResponse(BaseModel):
    id:           int
    started_at:   datetime
    ended_at:     Optional[datetime]
    duration_sec: int
    avg_score:    float
    min_score:    float
    max_score:    float
    alert_count:  int
    notes:        Optional[str]

    model_config = {"from_attributes": True}


class EventResponse(BaseModel):
    id:              int
    session_id:      Optional[int]
    posture_score:   int
    head_angle:      float
    shoulder_slope:  float
    gaze_horizontal: float
    gaze_vertical:   float
    head_status:     str
    shoulder_status: str
    eye_status:      str
    brightness:      float
    timestamp:       float
    created_at:      datetime

    model_config = {"from_attributes": True}


# ── Incoming WebSocket frame ─────────────────────────────
class PostureFrame(BaseModel):
    """
    FIX 2: Validates every incoming WebSocket frame.
    Bad/malformed data is rejected before touching the DB.
    """
    session_id:           Optional[int]   = None
    posture_score:        int             = 0
    head_forward_angle:   float           = 0.0
    shoulder_slope:       float           = 0.0
    gaze_horizontal:      float           = 0.0
    gaze_vertical:        float           = 0.0
    head_position_status: str             = "Unknown"
    shoulder_status:      str             = "Unknown"
    eye_contact_status:   str             = "N/A"
    brightness:           float           = 100.0
    timestamp:            float           = 0.0

    @field_validator("posture_score", mode="before")
    @classmethod
    def clamp_score(cls, v):
        return max(0, min(100, int(v or 0)))

    @field_validator("brightness", mode="before")
    @classmethod
    def clamp_brightness(cls, v):
        return max(0.0, min(300.0, float(v or 0)))

    @field_validator("head_forward_angle", "shoulder_slope",
                     "gaze_horizontal", "gaze_vertical", mode="before")
    @classmethod
    def clamp_float(cls, v):
        return round(float(v or 0), 4)
