"""
posture_tracker.py
==================
Complete Posture Tracker with:
- Real-time posture detection (MediaPipe)
- Voice alerts (pyttsx3 for Windows, 'say' for Mac)
- Backend sync via WebSocket
- SMART REMINDER: Screen flash + in-app notification
  after 5-7 minutes of sustained poor posture
- No spam, no frustration

Controls:
  ESC = quit
  V   = toggle voice
  S   = show live stats
"""

import cv2
import mediapipe as mp
import numpy as np
import time
import threading
import logging
import urllib.request
import queue
import json
import requests
import asyncio
import websockets
import platform
import subprocess
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional, Tuple, List

logger = logging.getLogger(__name__)

BACKEND_URL    = "http://localhost:8000"
BACKEND_WS_URL = "ws://localhost:8000/ws/posture"
SYNC_ENABLED   = True   # Set False to run fully offline

_HERE = Path(__file__).resolve().parent

_MODELS = {
    "face_landmarker.task": (
        "https://storage.googleapis.com/mediapipe-models/"
        "face_landmarker/face_landmarker/float16/1/face_landmarker.task"
    ),
    "pose_landmarker_lite.task": (
        "https://storage.googleapis.com/mediapipe-models/"
        "pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task"
    ),
}

_POSE_CONNECTIONS = [
    (0,1),(1,2),(2,3),(3,7),(0,4),(4,5),(5,6),(6,8),
    (9,10),(11,12),(11,13),(13,15),(15,17),(15,19),(15,21),
    (17,19),(12,14),(14,16),(16,18),(16,20),(16,22),(18,20),
    (11,23),(12,24),(23,24),(23,25),(24,26),(25,27),(26,28),
    (27,29),(28,30),(29,31),(30,32),(27,31),(28,32),
]
_LEFT_EYE_CONN = [
    (33,7),(7,163),(163,144),(144,145),(145,153),(153,154),
    (154,155),(155,133),(33,246),(246,161),(161,160),(160,159),
    (159,158),(158,157),(157,173),(173,133),
]
_RIGHT_EYE_CONN = [
    (362,382),(382,381),(381,380),(380,374),(374,373),(373,390),
    (390,249),(249,263),(362,398),(398,384),(384,385),(385,386),
    (386,387),(387,388),(388,466),(466,263),
]


def _progress_hook(block_num, block_size, total_size):
    if total_size > 0:
        pct = min(100, block_num * block_size * 100 // total_size)
        if pct % 20 == 0:
            logger.info(f"  Downloading ... {pct}%")


def _ensure_models() -> Tuple[str, str]:
    for filename, url in _MODELS.items():
        dest = _HERE / filename
        if dest.exists() and dest.stat().st_size > 100_000:
            continue
        logger.info(f"Downloading {filename} ...")
        try:
            urllib.request.urlretrieve(url, dest, reporthook=_progress_hook)
        except Exception as exc:
            if dest.exists():
                dest.unlink()
            raise RuntimeError(f"Download failed: {exc}") from exc
    return str(_HERE / "face_landmarker.task"), str(_HERE / "pose_landmarker_lite.task")


def _status_color(status: str) -> Tuple[int, int, int]:
    return {
        "Good":    (0, 220, 0),
        "Warning": (0, 165, 255),
        "Alert":   (0, 0, 255),
    }.get(status, (160, 160, 160))


# ---------------------------------------------------------------------------
# Smart Posture Reminder — screen flash + in-app notification
# ---------------------------------------------------------------------------

class PostureReminder:
    """
    Tracks sustained poor posture and triggers:
      1. Screen flash (2 times, red overlay)
      2. In-app notification banner on the camera window

    Rules:
      - Only triggers after TRIGGER_MINUTES of continuous poor posture
      - 5-minute cooldown between reminders (won't spam)
      - Posture good = resets the timer immediately
    """

    TRIGGER_MINUTES  = 5      # wait this long before first reminder
    COOLDOWN_SECONDS = 300    # minimum gap between reminders (5 min)
    FLASH_COUNT      = 2      # number of flashes
    FLASH_DURATION   = 0.25   # seconds each flash is ON
    FLASH_GAP        = 0.25   # seconds between flashes

    def __init__(self):
        self.poor_start:       Optional[float] = None
        self.current_streak:   float = 0.0
        self.last_reminder:    float = 0.0

        # Flash state
        self.flash_active:     bool  = False
        self.flash_count_done: int   = 0
        self.flash_on:         bool  = False
        self.flash_next_event: float = 0.0

        # Notification banner state
        self.notif_active:     bool  = False
        self.notif_end:        float = 0.0
        self.notif_message:    str   = ""

    def update(self, posture_score: int, now: float) -> bool:
        """
        Call every frame. Returns True when reminder just triggered.
        """
        is_poor = posture_score < 50 and posture_score > 0

        # ── Update flash animation ─────────────────────────────────
        if self.flash_active:
            if now >= self.flash_next_event:
                if self.flash_on:
                    # Turn flash OFF
                    self.flash_on = False
                    self.flash_count_done += 1
                    if self.flash_count_done >= self.FLASH_COUNT:
                        self.flash_active = False   # done flashing
                    else:
                        self.flash_next_event = now + self.FLASH_GAP
                else:
                    # Turn flash ON
                    self.flash_on = True
                    self.flash_next_event = now + self.FLASH_DURATION

        # ── Update notification banner ─────────────────────────────
        if self.notif_active and now > self.notif_end:
            self.notif_active = False

        # ── Track poor posture streak ──────────────────────────────
        if is_poor:
            if self.poor_start is None:
                self.poor_start = now
            self.current_streak = now - self.poor_start
        else:
            # Good posture — reset
            self.poor_start     = None
            self.current_streak = 0.0
            return False

        # ── Check if reminder should fire ─────────────────────────
        trigger_seconds = self.TRIGGER_MINUTES * 60
        if (self.current_streak >= trigger_seconds and
                now - self.last_reminder >= self.COOLDOWN_SECONDS):

            self.last_reminder = now
            minutes = int(self.current_streak // 60)

            # Start flash sequence
            self.flash_active     = True
            self.flash_on         = True
            self.flash_count_done = 0
            self.flash_next_event = now + self.FLASH_DURATION

            # Show in-app notification for 6 seconds
            self.notif_active  = True
            self.notif_end     = now + 6.0
            self.notif_message = (
                f"! POSTURE ALERT !  {minutes}m of poor posture"
                "  ->  Sit straight now!"
            )

            return True

        return False

    def get_streak_seconds(self) -> int:
        return int(self.current_streak)


# ---------------------------------------------------------------------------
# Voice Alert Engine — Windows + macOS compatible
# ---------------------------------------------------------------------------

class VoiceAlert:
    COOLDOWN_SEC = 12
    RATE_WIN     = 150
    RATE_MAC     = 175

    def __init__(self, enabled: bool = True):
        self.enabled       = enabled
        self._last_spoken: dict        = {}
        self._q:           queue.Queue = queue.Queue(maxsize=2)
        self._is_windows   = platform.system() == "Windows"
        self._engine       = None
        self._ready        = False
        threading.Thread(
            target=self._worker, daemon=True, name="VoiceThread"
        ).start()

    def speak(self, message: str):
        if not self.enabled:
            return
        now = time.time()
        if now - self._last_spoken.get(message, 0) < self.COOLDOWN_SEC:
            return
        self._last_spoken[message] = now
        try:
            self._q.put_nowait(message)
        except queue.Full:
            pass

    def _init_pyttsx3(self):
        try:
            import pyttsx3
            self._engine = pyttsx3.init()
            self._engine.setProperty('rate', self.RATE_WIN)
            self._engine.setProperty('volume', 1.0)
            voices = self._engine.getProperty('voices')
            for v in voices:
                if 'zira' in v.name.lower() or 'david' in v.name.lower():
                    self._engine.setProperty('voice', v.id)
                    break
            self._ready = True
            logger.info("Voice: pyttsx3 (Windows)")
        except ImportError:
            logger.warning("pyttsx3 not found. Run: pip install pyttsx3")
        except Exception as exc:
            logger.warning(f"Voice init failed: {exc}")

    def _worker(self):
        if self._is_windows:
            self._init_pyttsx3()
        else:
            self._ready = True
            logger.info("Voice: macOS say")

        while True:
            msg = self._q.get()
            if not self.enabled:
                continue
            try:
                if self._is_windows:
                    if self._ready and self._engine:
                        self._engine.say(msg)
                        self._engine.runAndWait()
                    else:
                        print(f"[VOICE] {msg}")
                else:
                    subprocess.run(
                        ["say", "-r", str(self.RATE_MAC), msg],
                        timeout=10, check=False,
                    )
            except Exception as exc:
                logger.debug(f"Voice error: {exc}")
                print(f"[VOICE] {msg}")


# ---------------------------------------------------------------------------
# Backend Sync (WebSocket)
# ---------------------------------------------------------------------------

class BackendSync:
    def __init__(self, ws_url: str, enabled: bool = True):
        self.ws_url      = ws_url
        self.enabled     = enabled
        self._q:         queue.Queue = queue.Queue(maxsize=10)
        self.session_id: Optional[int] = None
        self.connected   = False
        if enabled:
            threading.Thread(
                target=self._run, daemon=True, name="SyncThread"
            ).start()

    def send(self, data: dict):
        if not self.enabled:
            return
        try:
            self._q.put_nowait(data)
        except queue.Full:
            pass

    def create_session(self) -> Optional[int]:
        if not self.enabled:
            return None
        try:
            r = requests.post(f"{BACKEND_URL}/api/sessions/", json={}, timeout=3)
            if r.status_code == 200:
                self.session_id = r.json()["id"]
                logger.info(f"Session #{self.session_id} created")
                return self.session_id
        except Exception:
            logger.warning("Backend not reachable — offline mode")
        return None

    def close_session(self, avg_score: float, duration_sec: int, alert_count: int):
        if not self.enabled or not self.session_id:
            return
        try:
            requests.patch(
                f"{BACKEND_URL}/api/sessions/{self.session_id}",
                json={"duration_sec": duration_sec,
                      "avg_score": round(avg_score, 1),
                      "alert_count": alert_count},
                timeout=3,
            )
        except Exception:
            pass

    def _run(self):
        asyncio.run(self._async_loop())

    async def _async_loop(self):
        while True:
            try:
                async with websockets.connect(
                    self.ws_url, ping_interval=20, ping_timeout=10
                ) as ws:
                    self.connected = True
                    logger.info("WebSocket connected")
                    while True:
                        try:
                            data = self._q.get(timeout=0.05)
                            if self.session_id:
                                data["session_id"] = self.session_id
                            await ws.send(json.dumps(data))
                        except queue.Empty:
                            await asyncio.sleep(0.01)
            except Exception:
                self.connected = False
                await asyncio.sleep(3)


# ---------------------------------------------------------------------------
# PostureData
# ---------------------------------------------------------------------------

@dataclass
class PostureData:
    head_forward_angle:   float = 0.0
    shoulder_slope:       float = 0.0
    gaze_horizontal:      float = 0.0
    gaze_vertical:        float = 0.0
    face_detected:        bool  = False
    shoulders_detected:   bool  = False
    iris_detected:        bool  = False
    head_position_status: str   = "Unknown"
    eye_contact_status:   str   = "N/A"
    shoulder_status:      str   = "Unknown"
    posture_score:        int   = 0
    frame_width:          int   = 640
    frame_height:         int   = 480
    brightness:           float = 100.0
    timestamp:            float = 0.0
    error_hint:           str   = ""
    poor_posture_streak:  int   = 0


# ---------------------------------------------------------------------------
# PostureTracker
# ---------------------------------------------------------------------------

class PostureTracker:

    _LEFT_EAR       = 7
    _RIGHT_EAR      = 8
    _LEFT_SHOULDER  = 11
    _RIGHT_SHOULDER = 12
    _L_EYE_OUTER    = 33
    _L_EYE_INNER    = 133
    _R_EYE_INNER    = 362
    _R_EYE_OUTER    = 263
    _L_IRIS         = 468
    _R_IRIS         = 473

    HEAD_WARN_DEG    = 10.0
    HEAD_ALERT_DEG   = 20.0
    GAZE_WARN_MAG    = 0.18
    GAZE_ALERT_MAG   = 0.30
    SHLD_WARN_DEG    = 5.0
    SHLD_ALERT_DEG   = 10.0
    LOW_LIGHT_THRESH = 20

    def __init__(self, voice_enabled: bool = True, sync_enabled: bool = True):
        self._face_detector  = None
        self._pose_detector  = None
        self._cap: Optional[cv2.VideoCapture] = None
        self._running        = False
        self._thread: Optional[threading.Thread] = None
        self._lock           = threading.Lock()
        self._current_data   = PostureData()
        self.error_message:  str = ""
        self.last_annotated_frame: Optional[np.ndarray] = None
        self._calib_samples: List[float] = []
        self._baseline_angle: Optional[float] = None
        self.is_calibrated:  bool = False
        self._fps_times:     List[float] = []
        self._fps:           float = 0.0
        self._last_pose_result = None
        self._last_face_result = None

        # Session stats
        self._session_start: float = time.time()
        self._scores:        List[int] = []
        self._alert_count:   int  = 0
        self._min_score:     int  = 100
        self._max_score:     int  = 0

        self.voice    = VoiceAlert(enabled=voice_enabled)
        self.sync     = BackendSync(BACKEND_WS_URL, enabled=sync_enabled)
        self.reminder = PostureReminder()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> bool:
        try:
            face_model, pose_model = _ensure_models()

            self._cap = cv2.VideoCapture(0)
            if not self._cap.isOpened():
                self.error_message = "Cannot open webcam."
                return False

            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            self._cap.set(cv2.CAP_PROP_FPS, 30)
            self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision as mp_vision

            self._face_detector = mp_vision.FaceLandmarker.create_from_options(
                mp_vision.FaceLandmarkerOptions(
                    base_options=mp_python.BaseOptions(model_asset_path=face_model),
                    running_mode=mp_vision.RunningMode.IMAGE,
                    num_faces=1,
                    output_face_blendshapes=False,
                    output_facial_transformation_matrixes=False,
                )
            )
            self._pose_detector = mp_vision.PoseLandmarker.create_from_options(
                mp_vision.PoseLandmarkerOptions(
                    base_options=mp_python.BaseOptions(model_asset_path=pose_model),
                    running_mode=mp_vision.RunningMode.IMAGE,
                    num_poses=1,
                )
            )
            self.sync.create_session()
            self._session_start = time.time()
            self.voice.speak("Posture tracker started. I will remind you after 5 minutes of poor posture.")
            return True

        except Exception as exc:
            self.error_message = f"Init error: {exc}"
            logger.exception("Init failed")
            return False

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._tracking_loop, name="PostureTrackerThread", daemon=True
        )
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
        self._finalize_session()
        self._release()

    def _release(self):
        if self._cap:
            self._cap.release()
            self._cap = None
        for attr in ("_face_detector", "_pose_detector"):
            det = getattr(self, attr, None)
            if det:
                try:
                    det.close()
                except Exception:
                    pass
                setattr(self, attr, None)

    def _finalize_session(self):
        duration = int(time.time() - self._session_start)
        avg = round(sum(self._scores) / len(self._scores), 1) if self._scores else 0.0
        self.sync.close_session(avg, duration, self._alert_count)
        print(f"\n{'='*50}")
        print(f"  SESSION SUMMARY")
        print(f"{'='*50}")
        print(f"  Duration   : {duration // 60}m {duration % 60}s")
        print(f"  Avg Score  : {avg}/100")
        print(f"  Best Score : {self._max_score}/100")
        print(f"  Worst Score: {self._min_score if self._min_score < 100 else 0}/100")
        print(f"  Alerts     : {self._alert_count}")
        print(f"{'='*50}\n")

    # ------------------------------------------------------------------
    # Data access
    # ------------------------------------------------------------------

    def get_current_data(self) -> PostureData:
        with self._lock:
            d = self._current_data
            return PostureData(**{k: getattr(d, k) for k in d.__dataclass_fields__})

    def start_calibration(self):
        self._calib_samples = []
        self.is_calibrated  = False
        print("Calibration restarted — sit upright")

    # ------------------------------------------------------------------
    # Tracking loop
    # ------------------------------------------------------------------

    def _tracking_loop(self):
        consecutive_fail = 0

        while self._running:
            try:
                ret, frame = self._cap.read()
                if not ret or frame is None:
                    consecutive_fail += 1
                    if consecutive_fail > 20:
                        self.error_message = "Webcam stopped responding."
                        break
                    time.sleep(0.05)
                    continue

                consecutive_fail = 0
                frame = cv2.flip(frame, 1)

                now = time.time()
                self._fps_times.append(now)
                self._fps_times = [t for t in self._fps_times if now - t < 1.0]
                self._fps = float(len(self._fps_times))

                brightness = float(np.mean(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)))
                if brightness < self.LOW_LIGHT_THRESH:
                    data = PostureData(
                        brightness=brightness, timestamp=now,
                        error_hint="Low light — improve lighting",
                    )
                    with self._lock:
                        self._current_data = data
                    time.sleep(0.1)
                    continue

                rgb      = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

                face_result = self._face_detector.detect(mp_image)
                pose_result = self._pose_detector.detect(mp_image)

                self._last_pose_result = pose_result
                self._last_face_result = face_result

                data = self._analyse(face_result, pose_result, frame.shape, brightness)
                self._maybe_calibrate(data)
                self._trigger_voice(data)

                # ── SMART REMINDER ────────────────────────────────────────
                triggered = self.reminder.update(data.posture_score, now)
                if triggered:
                    streak_min = self.reminder.get_streak_seconds() // 60
                    self.voice.speak(
                        f"Posture alert! You have had poor posture for "
                        f"{streak_min} minutes. Please sit up straight now."
                    )
                    self._alert_count += 1
                    print(f"[REMINDER] Poor posture for {streak_min}m — flash + notification triggered")

                data.poor_posture_streak = self.reminder.get_streak_seconds()

                if data.posture_score > 0:
                    self._scores.append(data.posture_score)
                    self._min_score = min(self._min_score, data.posture_score)
                    self._max_score = max(self._max_score, data.posture_score)

                self.sync.send(asdict(data))
                self.last_annotated_frame = self._draw_debug(frame, data)

                with self._lock:
                    self._current_data = data

            except Exception as exc:
                logger.exception(f"Loop error: {exc}")
                time.sleep(0.1)

    # ------------------------------------------------------------------
    # Voice triggers (immediate, per-frame alerts)
    # ------------------------------------------------------------------

    def _trigger_voice(self, data: PostureData):
        if data.head_position_status == "Alert":
            self.voice.speak("Head too far forward. Sit up straight.")
        elif data.head_position_status == "Warning":
            self.voice.speak("Watch your head position.")
        if data.shoulder_status == "Alert":
            self.voice.speak("Shoulders are uneven. Straighten up.")
        elif data.shoulder_status == "Warning":
            self.voice.speak("Check your shoulder alignment.")
        if data.eye_contact_status == "Alert":
            self.voice.speak("Eyes drifting. Look at the screen.")
        if 0 < data.posture_score < 35:
            self.voice.speak("Poor posture detected. Please correct your position.")

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def _analyse(self, face_result, pose_result, shape, brightness: float) -> PostureData:
        h, w = shape[:2]
        data = PostureData(frame_width=w, frame_height=h,
                           brightness=brightness, timestamp=time.time())

        if pose_result.pose_landmarks:
            lm = pose_result.pose_landmarks[0]
            data.shoulders_detected = True
            ls = (lm[self._LEFT_SHOULDER].x  * w, lm[self._LEFT_SHOULDER].y  * h)
            rs = (lm[self._RIGHT_SHOULDER].x * w, lm[self._RIGHT_SHOULDER].y * h)
            le = (lm[self._LEFT_EAR].x  * w,      lm[self._LEFT_EAR].y  * h)
            re = (lm[self._RIGHT_EAR].x * w,      lm[self._RIGHT_EAR].y * h)

            shoulder_cx    = (ls[0] + rs[0]) / 2.0
            ear_cx         = (le[0] + re[0]) / 2.0
            shoulder_width = max(abs(ls[0] - rs[0]), 1.0)
            raw_offset     = (shoulder_cx - ear_cx) / shoulder_width
            data.head_forward_angle = float(np.degrees(np.arctan(raw_offset * 1.5)))

            dy = ls[1] - rs[1]
            dx = ls[0] - rs[0]
            data.shoulder_slope = abs(float(np.degrees(np.arctan2(dy, dx))))

            fa = abs(data.head_forward_angle)
            data.head_position_status = (
                "Good"    if fa < self.HEAD_WARN_DEG  else
                "Warning" if fa < self.HEAD_ALERT_DEG else "Alert"
            )
            sl = data.shoulder_slope
            data.shoulder_status = (
                "Good"    if sl < self.SHLD_WARN_DEG  else
                "Warning" if sl < self.SHLD_ALERT_DEG else "Alert"
            )
        else:
            data.head_position_status = "No body detected"
            data.shoulder_status      = "No body detected"

        if face_result.face_landmarks:
            data.face_detected = True
            fm = face_result.face_landmarks[0]
            if len(fm) > self._R_IRIS:
                data.iris_detected = True
                gh, gv = self._compute_gaze(fm, w, h)
                data.gaze_horizontal = gh
                data.gaze_vertical   = gv
                mag = float(np.hypot(gh, gv))
                data.eye_contact_status = (
                    "Good"    if mag < self.GAZE_WARN_MAG  else
                    "Warning" if mag < self.GAZE_ALERT_MAG else "Alert"
                )
            else:
                data.eye_contact_status = "N/A"
        else:
            data.face_detected      = False
            data.eye_contact_status = "N/A"
            data.error_hint = (
                "Face not visible" if data.shoulders_detected
                else "No face/body detected"
            )

        data.posture_score = self._score(data)
        return data

    def _compute_gaze(self, fm, w: int, h: int) -> Tuple[float, float]:
        def _g(x1, y1, x2, y2, ix, iy):
            cx = (x1 + x2) / 2.0
            cy = (y1 + y2) / 2.0
            ew = max(abs(x2 - x1), 1.0)
            return (ix - cx) / ew, (iy - cy) / (ew * 0.4)
        lh, lv = _g(fm[self._L_EYE_OUTER].x*w, fm[self._L_EYE_OUTER].y*h,
                    fm[self._L_EYE_INNER].x*w, fm[self._L_EYE_INNER].y*h,
                    fm[self._L_IRIS].x*w,       fm[self._L_IRIS].y*h)
        rh, rv = _g(fm[self._R_EYE_INNER].x*w, fm[self._R_EYE_INNER].y*h,
                    fm[self._R_EYE_OUTER].x*w, fm[self._R_EYE_OUTER].y*h,
                    fm[self._R_IRIS].x*w,       fm[self._R_IRIS].y*h)
        return (lh + rh) / 2.0, (lv + rv) / 2.0

    def _score(self, data: PostureData) -> int:
        if not data.face_detected and not data.shoulders_detected:
            return 0
        score = 100
        fa = abs(data.head_forward_angle)
        if   fa >= self.HEAD_ALERT_DEG: score -= 30
        elif fa >= self.HEAD_WARN_DEG:  score -= 15
        sl = data.shoulder_slope
        if   sl >= self.SHLD_ALERT_DEG: score -= 20
        elif sl >= self.SHLD_WARN_DEG:  score -= 10
        if data.iris_detected:
            mag = float(np.hypot(data.gaze_horizontal, data.gaze_vertical))
            if   mag >= self.GAZE_ALERT_MAG: score -= 25
            elif mag >= self.GAZE_WARN_MAG:  score -= 12
        return max(0, min(100, score))

    # ------------------------------------------------------------------
    # Calibration
    # ------------------------------------------------------------------

    def _maybe_calibrate(self, data: PostureData):
        if self.is_calibrated or not data.shoulders_detected:
            return
        self._calib_samples.append(data.head_forward_angle)
        if len(self._calib_samples) >= 60:
            self._baseline_angle = float(np.median(self._calib_samples))
            self.is_calibrated   = True
            self.voice.speak("Calibration complete. You are good to go.")
            logger.info(f"Calibrated. Baseline: {self._baseline_angle:.2f} deg")

    # ------------------------------------------------------------------
    # Draw overlay
    # ------------------------------------------------------------------

    def _draw_debug(self, frame: np.ndarray, data: PostureData) -> np.ndarray:
        out = frame.copy()
        h_px, w_px = out.shape[:2]

        # ── SCREEN FLASH — red overlay ────────────────────────────
        if self.reminder.flash_active and self.reminder.flash_on:
            flash_overlay = np.zeros_like(out)
            flash_overlay[:] = (0, 0, 255)   # full red
            cv2.addWeighted(flash_overlay, 0.6, out, 0.4, 0, out)

        # ── Pose skeleton ─────────────────────────────────────────
        pr = self._last_pose_result
        if pr and pr.pose_landmarks:
            lms = pr.pose_landmarks[0]
            pts = [(int(lm.x * w_px), int(lm.y * h_px)) for lm in lms]
            for a, b in _POSE_CONNECTIONS:
                if a < len(pts) and b < len(pts):
                    cv2.line(out, pts[a], pts[b], (255, 255, 255), 2)
            for pt in pts:
                cv2.circle(out, pt, 3, (0, 255, 0), -1)

        # ── Eye contours ──────────────────────────────────────────
        fr = self._last_face_result
        if fr and fr.face_landmarks:
            flms = fr.face_landmarks[0]
            fpts = [(int(lm.x * w_px), int(lm.y * h_px)) for lm in flms]
            for conn in (_LEFT_EYE_CONN, _RIGHT_EYE_CONN):
                for a, b in conn:
                    if a < len(fpts) and b < len(fpts):
                        cv2.line(out, fpts[a], fpts[b], (255, 200, 0), 1)

        # ── Dark info panel ───────────────────────────────────────
        overlay = out.copy()
        cv2.rectangle(overlay, (0, 0), (265, 285), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.45, out, 0.55, 0, out)

        # ── Score bar ─────────────────────────────────────────────
        sc = (
            (0, 220, 0)   if data.posture_score >= 70 else
            (0, 165, 255) if data.posture_score >= 40 else
            (0, 0, 255)
        )
        cv2.rectangle(out, (10, 10), (255, 26), (60, 60, 60), -1)
        bar_w = int(245 * data.posture_score / 100)
        cv2.rectangle(out, (10, 10), (10 + bar_w, 26), sc, -1)
        cv2.putText(out, f"Score: {data.posture_score}/100",
                    (10, 46), cv2.FONT_HERSHEY_SIMPLEX, 0.65, sc, 2)

        # ── Status rows ───────────────────────────────────────────
        y = 72
        for label, status in [
            ("Head",      data.head_position_status),
            ("Shoulders", data.shoulder_status),
            ("Gaze",      data.eye_contact_status),
        ]:
            cv2.putText(out, f"{label}: {status}", (10, y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, _status_color(status), 1)
            y += 26

        # ── Poor posture streak timer ─────────────────────────────
        streak = data.poor_posture_streak
        if streak > 0:
            m = streak // 60
            s = streak % 60
            col   = (0, 0, 255) if streak >= 300 else (0, 165, 255)
            label = f"[!] POOR: {m}m {s}s" if streak >= 300 else f"Poor: {m}m {s}s"
            cv2.putText(out, label, (10, y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, col, 2 if streak >= 300 else 1)
        else:
            cv2.putText(out, "[OK] Good posture", (10, y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 220, 0), 1)
        y += 26

        # ── Reminder countdown ────────────────────────────────────
        trigger_sec = PostureReminder.TRIGGER_MINUTES * 60
        remaining   = max(0, trigger_sec - streak)
        if streak > 0 and remaining > 0:
            cv2.putText(out, f"Reminder in: {remaining//60}m {remaining%60}s",
                        (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (120, 120, 120), 1)
        elif streak == 0:
            cv2.putText(out, f"Reminder: after {PostureReminder.TRIGGER_MINUTES}m poor posture",
                        (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (80, 80, 80), 1)
        y += 22

        # ── Calibration ───────────────────────────────────────────
        cal_txt = "CAL OK" if self.is_calibrated else f"Calibrating {min(len(self._calib_samples),60)}/60"
        cal_col = (0, 220, 0) if self.is_calibrated else (0, 165, 255)
        cv2.putText(out, cal_txt, (10, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, cal_col, 1)
        y += 22

        # ── Voice / sync status ───────────────────────────────────
        v_txt = "VOC ON  [V]" if self.voice.enabled else "VOC OFF [V]"
        v_col = (0, 220, 0) if self.voice.enabled else (80, 80, 80)
        cv2.putText(out, v_txt, (10, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, v_col, 1)

        # ── FPS top-right ─────────────────────────────────────────
        fps_txt = f"FPS: {self._fps:.0f}"
        (tw, _), _ = cv2.getTextSize(fps_txt, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        cv2.putText(out, fps_txt, (w_px - tw - 10, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)

        # ── Error hint bottom ─────────────────────────────────────
        if data.error_hint:
            cv2.putText(out, data.error_hint, (10, h_px - 14),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 165, 255), 1)

        # ── IN-APP NOTIFICATION BANNER ────────────────────────────
        # Drawn LAST so it appears on top of everything
        if self.reminder.notif_active:
            bh = 80   # banner height
            # Red banner background
            ban = out.copy()
            cv2.rectangle(ban, (0, h_px - bh), (w_px, h_px), (0, 0, 200), -1)
            cv2.addWeighted(ban, 0.85, out, 0.15, 0, out)
            # Border line
            cv2.line(out, (0, h_px - bh), (w_px, h_px - bh), (0, 0, 255), 3)
            # Warning text
            cv2.putText(out, "!! POSTURE REMINDER !!",
                        (w_px // 2 - 160, h_px - bh + 26),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            cv2.putText(out, "Sit up straight! Poor posture detected.",
                        (w_px // 2 - 190, h_px - bh + 58),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 220, 0), 1)

        return out


# ---------------------------------------------------------------------------
# Entry point   ← THIS WAS MISSING — that's why nothing ran
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("=" * 55)
    print("  POSTUREAI — Smart Posture Tracker")
    print("=" * 55)
    print("  ESC = quit")
    print("  V   = toggle voice on/off")
    print("  S   = print live score to terminal")
    print("-" * 55)
    print("  SMART REMINDER fires after 5 minutes of poor posture:")
    print("    -> Screen flashes RED (x2)")
    print("    -> Red notification banner appears at bottom")
    print("    -> Voice alert speaks")
    print("    -> Then 5-minute cooldown before next reminder")
    print("=" * 55)

    tracker = PostureTracker(voice_enabled=True, sync_enabled=SYNC_ENABLED)

    if not tracker.initialize():
        print(f"\nInit failed: {tracker.error_message}")
        exit(1)

    print("\nReady! Camera starting...\n")
    tracker.start()

    try:
        while True:
            frame = tracker.last_annotated_frame
            if frame is not None:
                cv2.imshow("PostureAI", frame)
            else:
                time.sleep(0.05)

            key = cv2.waitKey(1) & 0xFF
            if key == 27:   # ESC
                break
            elif key in (ord('v'), ord('V')):
                tracker.voice.enabled = not tracker.voice.enabled
                print(f"Voice: {'ON' if tracker.voice.enabled else 'OFF'}")
            elif key in (ord('s'), ord('S')):
                d = tracker.get_current_data()
                streak = d.poor_posture_streak
                print(
                    f"Score:{d.posture_score} | "
                    f"Head:{d.head_position_status} | "
                    f"Shoulders:{d.shoulder_status} | "
                    f"Poor streak:{streak//60}m{streak%60}s"
                )

    except KeyboardInterrupt:
        pass
    finally:
        tracker.stop()
        cv2.destroyAllWindows()
        print("Goodbye!")
