# 🧘 Laptop Posture & Eye-Contact Tracker

> A full-stack real-time AI application that monitors posture health and eye-contact quality using just your laptop webcam — **100% local, zero cloud, zero subscription.**

![Python](https://img.shields.io/badge/Python-3.11-blue?style=flat-square&logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green?style=flat-square&logo=fastapi)
![MediaPipe](https://img.shields.io/badge/MediaPipe-0.10+-orange?style=flat-square)
![OpenCV](https://img.shields.io/badge/OpenCV-4.8+-red?style=flat-square&logo=opencv)
![SQLite](https://img.shields.io/badge/SQLite-WAL_Mode-blue?style=flat-square&logo=sqlite)
![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)
![Stars](https://img.shields.io/github/stars/Ayush84190/-Posture-tracker?style=flat-square)

---

## 📖 Table of Contents

- [What It Does](#-what-it-does)
- [Architecture](#-architecture)
- [Tech Stack](#-tech-stack)
- [Quick Start](#-quick-start)
- [API Endpoints](#-api-endpoints)
- [File Overview](#-file-overview)
- [Posture Score](#-posture-score-breakdown)
- [Alert System](#-alert-system)
- [Key Design Decisions](#-key-design-decisions)
- [Desktop Overlay](#-desktop-overlay)
- [Video Call Detection](#-video-call-detection)
- [Privacy](#-privacy)
- [Troubleshooting](#-troubleshooting)
- [Requirements](#-requirements)
- [License](#-license)

---

## 🎯 What It Does

Sits silently as a tiny overlay on your screen and watches your posture all day.

| Problem | How It Detects | Alert Threshold |
|---|---|---|
| Tech neck / forward head | Ear-to-shoulder landmark geometry | > 15° forward |
| Rounded / uneven shoulders | Shoulder slope angle between landmarks | > 10° slope |
| Poor eye contact on calls | Iris position vs eye-centre distance | Gaze offset > 30% eye width |
| Prolonged static sitting | Landmark movement delta over 8-frame buffer | 20 min without movement |
| Extended sitting session | Session timer | 50 min total sitting |

---

## 🏗 Architecture

```
┌─────────────────────────────────────────────────────┐
│                    WEBCAM (OpenCV)                  │
│                    30 frames/sec                    │
└───────────────────────┬─────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────┐
│              posture_tracker.py                     │
│   MediaPipe FaceLandmarker + PoseLandmarker         │
│   • Head forward angle (ear-shoulder geometry)      │
│   • Shoulder slope calculation                      │
│   • Iris gaze offset (478-point face mesh)          │
│   • Brightness guard (low-light detection)          │
│   • Posture score 0-100                             │
└───────────────────────┬─────────────────────────────┘
                        ↓ WebSocket  /ws/posture
┌─────────────────────────────────────────────────────┐
│                main.py  (FastAPI)                   │
│   • Async WebSocket server                          │
│   • Pydantic v2 frame validation                    │
│   • Batch writer (buffers 30fps → writes every 5s)  │
│   • Broadcasts to all connected dashboards          │
│   • Startup session cleanup                         │
│   • 24h background data pruning                     │
└──────────────┬──────────────────┬───────────────────┘
               ↓                  ↓
┌──────────────────┐   ┌────────────────────────────┐
│   SQLite DB      │   │      index.html             │
│   (WAL mode)     │   │   Live browser dashboard    │
│   sessions       │   │   WebSocket client          │
│   events         │   │   Real-time posture score   │
│   posture_events │   │   Session history charts    │
└──────────────────┘   └────────────────────────────┘
               ↓
┌─────────────────────────────────────────────────────┐
│                REST API Endpoints                   │
│   GET /api/sessions   →  session history            │
│   GET /api/events     →  alert events log           │
│   GET /api/stats      →  posture statistics         │
│   GET /api/health     →  server status              │
└─────────────────────────────────────────────────────┘
```

---

## 🛠 Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| AI / CV | MediaPipe Tasks API | FaceLandmarker (478 pts) + PoseLandmarker (33 pts) |
| Webcam | OpenCV 4.8+ | Frame capture, flip, colour conversion |
| Backend | FastAPI (async) | REST API + WebSocket server |
| Real-time | WebSocket | 30fps posture stream to browser dashboard |
| Database | SQLite + SQLAlchemy | Session storage, WAL mode for concurrency |
| Validation | Pydantic v2 | WebSocket frame schema validation |
| Desktop UI | Tkinter | Always-on-top semi-transparent overlay |
| Process scan | psutil | Auto-detect Zoom / Teams / Meet / Slack |
| Language | Python 3.11 | Fully async where needed, threaded elsewhere |

---

## 🚀 Quick Start

### Step 1 — Install dependencies
```cmd
pip install -r requirements.txt
```

### Step 2 — Start the FastAPI backend
```cmd
uvicorn main:app --reload --port 8000
```

You should see:
```
INFO:     Database tables ready
INFO:     Batch writer started
INFO:     Uvicorn running on http://127.0.0.1:8000
```

### Step 3 — Open the live dashboard
Open `index.html` in your browser. It connects automatically to `ws://localhost:8000/ws/posture`.

### Step 4 — Run the AI posture tracker
```cmd
python posture_tracker.py
```

The desktop overlay appears in the top-right corner. Your webcam activates and tracking begins.

### Step 5 — View API docs (optional)
```
http://localhost:8000/docs
```
FastAPI auto-generates a beautiful interactive API explorer.

---

## 📡 API Endpoints

| Endpoint | Method | Description | Response |
|---|---|---|---|
| `/api/health` | GET | Server status + buffered frame count | `{status, version, buffered_frames}` |
| `/api/sessions` | GET | All session history | List of sessions with timestamps |
| `/api/sessions/{id}` | GET | Single session detail | Session + all posture events |
| `/api/events` | GET | Alert events log | List of posture alerts fired |
| `/api/stats` | GET | Aggregated posture statistics | Averages, percentages, trends |
| `/ws/posture` | WebSocket | Live 30fps posture frame stream | JSON PostureFrame objects |

### WebSocket Frame Schema (Pydantic v2)
```python
class PostureFrame(BaseModel):
    session_id:          int
    posture_score:       int          # 0-100
    head_forward_angle:  float        # degrees
    shoulder_slope:      float        # degrees
    gaze_horizontal:     float        # -1.0 to 1.0
    gaze_vertical:       float        # -1.0 to 1.0
    head_position_status: str         # Good / Warning / Alert
    shoulder_status:     str          # Good / Warning / Alert
    eye_contact_status:  str          # Good / Warning / Alert / N/A
    brightness:          float        # mean frame brightness
    timestamp:           float        # epoch seconds
```

---

## 📁 File Overview

```
-Posture-tracker/
│
├── main.py                    ← FastAPI app, WebSocket, batch writer, lifespan
├── posture_tracker.py         ← MediaPipe AI engine (background thread)
├── database.py                ← SQLAlchemy engine, SessionLocal, WAL config
├── models.py                  ← SQLAlchemy ORM table definitions
├── schemas.py                 ← Pydantic v2 request/response schemas
├── sessions.py                ← /api/sessions router
├── events.py                  ← /api/events router
├── stats.py                   ← /api/stats router
├── cleanup.py                 ← Startup cleanup + 24h background pruning
├── websocket_manager.py       ← ConnectionManager (broadcast to all clients)
│
├── index.html                 ← Live browser dashboard (WebSocket client)
├── requirements.txt           ← Python dependencies
├── face_landmarker.task       ← MediaPipe face model (~4 MB, auto-downloaded)
├── pose_landmarker_lite.task  ← MediaPipe pose model (~5 MB, auto-downloaded)
├── .gitignore                 ← Excludes .venv, __pycache__, .db, .log files
└── README.md                  ← This file
```

---

## 📊 Posture Score Breakdown

The score is computed every frame and smoothed over a 3-second rolling window.

```
Starting score:  100

Deductions
──────────────────────────────────────────
Head forward > 20°    →  −30 pts  (critical)
Head forward > 15°    →  −15 pts  (warning)

Shoulder slope > 10°  →  −20 pts  (critical)
Shoulder slope > 5°   →  −10 pts  (warning)

Gaze offset > 30%     →  −25 pts  (critical, iris tracking only)
Gaze offset > 18%     →  −12 pts  (warning, iris tracking only)
──────────────────────────────────────────
Final: max(0, min(100, score))

Grade mapping
──────────────────────────────────────────
85 – 100  →  Excellent 🌟
70 –  84  →  Good 👍
50 –  69  →  Fair ⚠️
 0 –  49  →  Poor 🚨
```

---

## 🔔 Alert System

Smart alerts with per-type cooldown timers to prevent notification fatigue.

| Alert Type | Trigger Condition | Cooldown |
|---|---|---|
| Head posture | Head angle > 15° forward | 2 minutes |
| Shoulder posture | Shoulder slope > 10° | 2 minutes |
| Eye contact | Gaze offset > 30% (calls only) | 1 minute |
| Movement reminder | No movement for 20 minutes | 1 minute |
| Extended sitting | Sitting for 50+ minutes | 5 minutes |

- Alerts appear as **colour-coded popups** (orange = warning, red = critical)
- Each alert has a **"Got it ✓"** dismiss button
- Auto-dismiss after **15 seconds** if not clicked
- Eye contact alerts only fire when a **video call is detected**

---

## ⚙️ Key Design Decisions

### Batch Writer Pattern
At 30fps, writing every frame to SQLite = **~2.6 million rows/day**.
Solution: buffer frames in a `deque(maxlen=500)`, flush to DB every 5 seconds
in a single `bulk_insert_mappings()` transaction.

```python
_frame_buffer: deque = deque(maxlen=500)
_BATCH_INTERVAL = 5.0  # seconds

async def _batch_writer():
    while True:
        await asyncio.sleep(_BATCH_INTERVAL)
        # drain buffer → single DB transaction
```

### WAL Mode (Write-Ahead Logging)
SQLite configured in WAL mode so the FastAPI server can read stats while the
batch writer is writing — no lock contention.

### 3-Thread Desktop Architecture
```
Main thread  →  Tkinter event loop (UI)
Thread 1     →  PostureTracker (webcam + MediaPipe inference)
Thread 2     →  Logic loop (reads data, checks alerts, updates UI)
```
All cross-thread data goes through a `threading.Lock()`.

### Pydantic v2 Frame Validation
Every incoming WebSocket frame is validated before touching the DB.
Bad JSON or out-of-range values are logged and skipped — the server never crashes.

### Startup Session Cleanup
Crashes can leave sessions with `end_time = NULL`.
On every server start, `cleanup.run_startup_cleanup()` closes them automatically.

### 24-Hour Data Pruning
`cleanup.start_background_cleanup()` runs a thread that wakes every 24 hours
and deletes posture events older than the configured retention window.

---

## 🖥 Desktop Overlay

| UI Element | Description |
|---|---|
| Score arc | Circular arc showing 0–100 score, colour-coded |
| Grade label | Excellent / Good / Fair / Poor |
| Head Pos. row | Good (green) / Warning (orange) / Alert (red) |
| Shoulders row | Good / Warning / Alert |
| Eye Contact row | Active only during detected video calls |
| Sitting timer | HH:MM:SS — orange at 35 min, red at 50 min |
| Last movement | Seconds/minutes since last detected movement |
| Call badge | Shows 📹 when a video call app is detected |

- **Drag** by the purple header to reposition
- **Click –** to minimise to a slim bar
- **Click ×** to quit and show the session summary modal

---

## 📹 Video Call Detection

Uses `psutil` to scan running process names for known video call apps:

```
zoom  ·  teams  ·  webex  ·  skype  ·  meet
hangouts  ·  slack  ·  discord  ·  bluejeans  ·  lync
```

Eye-contact alerts **only activate** when a call app is detected.
If your app isn't auto-detected, gaze data is still visible in the overlay.

---

## 🔒 Privacy

- **100% local processing** — no video, images, or metrics ever leave your machine
- Webcam frames are processed in RAM and **never written to disk**
- Only text events are logged to `posture_tracker.log`
- SQLite database is stored locally at `posture_tracker.db`
- No analytics, no telemetry, no accounts required

---

## 🔧 Troubleshooting

### Webcam won't open
```
Windows : Settings → Privacy → Camera → allow Desktop apps
macOS   : System Settings → Privacy & Security → Camera → enable Terminal
Linux   : sudo usermod -aG video $USER  (then log out and back in)
```

### `module 'mediapipe' has no attribute 'solutions'`
You have MediaPipe 0.10.14+ which removed the old API. The app uses the new
Tasks API. Make sure you have the latest `posture_tracker.py` from this repo.

### App runs but no overlay window appears
Run with visible output to see errors:
```cmd
.venv\Scripts\activate
python posture_tracker.py
```

### Low score even when sitting straight
- Position webcam at **eye level** or slightly below
- Ensure **both shoulders are visible** in frame
- Avoid **strong backlighting** (bright window behind you)

### App is slow / high CPU
```python
# In posture_tracker.py, reduce resolution:
self._cap.set(cv2.CAP_PROP_FRAME_WIDTH,  320)
self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
```

### `ModuleNotFoundError: No module named 'mediapipe'`
```cmd
.venv\Scripts\activate
pip install -r requirements.txt
```

### Model files missing
They auto-download on first run (~10 MB total). Check internet connection.
Or manually download and place next to `posture_tracker.py`:
- [face_landmarker.task](https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task)
- [pose_landmarker_lite.task](https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task)

---

## 💻 Requirements

| Requirement | Details |
|---|---|
| Python | 3.11 recommended (3.9–3.12 supported) |
| OS | Windows 10+, macOS 12+, Ubuntu 20.04+ |
| Webcam | Built-in or USB (720p recommended) |
| RAM | ~400 MB free |
| Network | Internet on first run (model download ~10 MB) |

---

## 📄 License

MIT — free to use, modify, and distribute.

---

## 👨‍💻 Author

Built by **Ayush** — CS final year project turned full-stack side project.

[![GitHub](https://img.shields.io/badge/GitHub-Ayush84190-black?style=flat-square&logo=github)](https://github.com/Ayush84190)
