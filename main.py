import os
import json
from pathlib import Path
from typing import List, Optional, Dict, Any
from uuid import uuid4
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from threading import Lock

# Data directory and files
DATA_DIR = Path(__file__).parent / "data"
TYPES_FILE = DATA_DIR / "activity_types.json"
ACTIVITIES_FILE = DATA_DIR / "activities.json"

# Ensure data directory exists
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Thread locks for safe file operations
_types_lock = Lock()
_activities_lock = Lock()

# ---------- Models ----------
class ActivityTypeIn(BaseModel):
    activity_category: str = Field(..., min_length=1)
    activity_name: str = Field(..., min_length=1)

class ActivityType(ActivityTypeIn):
    id: str

class ActivityStartIn(BaseModel):
    activity_category: str
    activity_name: str

class ActivityEndIn(BaseModel):
    id: str

class ActivityRecord(BaseModel):
    id: str
    activity_category: str
    activity_name: str
    start_time: str
    end_time: Optional[str] = None
    duration_seconds: Optional[int] = None

# ---------- Helper functions ----------
def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        # If file is corrupt, back it up and reset
        backup = path.with_suffix(path.suffix + ".bak")
        try:
            path.replace(backup)
        except Exception:
            pass
        return default


def _write_json(path: Path, data) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp_path.replace(path)


def _iso_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


# ---------- FastAPI app ----------
app = FastAPI(title="Activity Tracker API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    return {"message": "Activity Tracker Backend Running"}


# ---------- Activity Types CRUD ----------
@app.get("/api/activity-types", response_model=List[ActivityType])
def list_activity_types():
    with _types_lock:
        items = _read_json(TYPES_FILE, [])
    return items


@app.post("/api/activity-types", response_model=ActivityType)
def create_activity_type(payload: ActivityTypeIn):
    with _types_lock:
        items: List[Dict[str, Any]] = _read_json(TYPES_FILE, [])
        # Prevent duplicates (same category + name)
        for it in items:
            if (
                it.get("activity_category", "").strip().lower() == payload.activity_category.strip().lower()
                and it.get("activity_name", "").strip().lower() == payload.activity_name.strip().lower()
            ):
                raise HTTPException(status_code=400, detail="Activity type already exists")
        new_item: Dict[str, Any] = {
            "id": str(uuid4()),
            "activity_category": payload.activity_category.strip(),
            "activity_name": payload.activity_name.strip(),
        }
        items.append(new_item)
        _write_json(TYPES_FILE, items)
        return new_item


@app.put("/api/activity-types/{type_id}", response_model=ActivityType)
def update_activity_type(type_id: str, payload: ActivityTypeIn):
    with _types_lock:
        items: List[Dict[str, Any]] = _read_json(TYPES_FILE, [])
        for idx, it in enumerate(items):
            if it.get("id") == type_id:
                # Check duplicate against others
                for other in items:
                    if other.get("id") != type_id and (
                        other.get("activity_category", "").strip().lower() == payload.activity_category.strip().lower()
                        and other.get("activity_name", "").strip().lower() == payload.activity_name.strip().lower()
                    ):
                        raise HTTPException(status_code=400, detail="Another activity type with same name exists")
                updated = {
                    "id": type_id,
                    "activity_category": payload.activity_category.strip(),
                    "activity_name": payload.activity_name.strip(),
                }
                items[idx] = updated
                _write_json(TYPES_FILE, items)
                return updated
        raise HTTPException(status_code=404, detail="Activity type not found")


@app.delete("/api/activity-types/{type_id}")
def delete_activity_type(type_id: str):
    with _types_lock:
        items: List[Dict[str, Any]] = _read_json(TYPES_FILE, [])
        new_items = [it for it in items if it.get("id") != type_id]
        if len(new_items) == len(items):
            raise HTTPException(status_code=404, detail="Activity type not found")
        _write_json(TYPES_FILE, new_items)
    return {"status": "ok"}


# ---------- Activities (start/end) ----------
@app.get("/api/activities", response_model=List[ActivityRecord])
def list_activities():
    with _activities_lock:
        items = _read_json(ACTIVITIES_FILE, [])
    return items


@app.post("/api/activities/start", response_model=ActivityRecord)
def start_activity(payload: ActivityStartIn):
    # Prevent multiple concurrent active activities (optional)
    with _activities_lock:
        activities: List[Dict[str, Any]] = _read_json(ACTIVITIES_FILE, [])
        active_exists = any(a.get("end_time") in (None, "") for a in activities)
        if active_exists:
            raise HTTPException(status_code=400, detail="An activity is already in progress. End it before starting a new one.")

        record: Dict[str, Any] = {
            "id": str(uuid4()),
            "activity_category": payload.activity_category.strip(),
            "activity_name": payload.activity_name.strip(),
            "start_time": _iso_now(),
            "end_time": None,
            "duration_seconds": None,
        }
        activities.append(record)
        _write_json(ACTIVITIES_FILE, activities)
        return record


@app.post("/api/activities/end", response_model=ActivityRecord)
def end_activity(payload: ActivityEndIn):
    with _activities_lock:
        activities: List[Dict[str, Any]] = _read_json(ACTIVITIES_FILE, [])
        for idx, a in enumerate(activities):
            if a.get("id") == payload.id:
                if a.get("end_time"):
                    raise HTTPException(status_code=400, detail="Activity already ended")
                end_time = datetime.utcnow().replace(microsecond=0)
                start = datetime.fromisoformat(a["start_time"].replace("Z", ""))
                duration = int((end_time - start).total_seconds())
                updated = {
                    **a,
                    "end_time": end_time.isoformat() + "Z",
                    "duration_seconds": duration,
                }
                activities[idx] = updated
                _write_json(ACTIVITIES_FILE, activities)
                return updated
        raise HTTPException(status_code=404, detail="Active activity not found")


@app.get("/api/activities/active", response_model=Optional[ActivityRecord])
def get_active_activity():
    with _activities_lock:
        activities: List[Dict[str, Any]] = _read_json(ACTIVITIES_FILE, [])
        for a in activities:
            if not a.get("end_time"):
                return a
    return None


# ---------- Summary ----------
@app.get("/api/summary")
def get_summary():
    """
    Returns aggregation suitable for charts:
    {
      dates: ["2025-11-03", ...],
      data: {
        "2025-11-03": {
           "sports": 5400,  # seconds per category
           "study": 3600,
        },
        ...
      }
    }
    """
    with _activities_lock:
        activities: List[Dict[str, Any]] = _read_json(ACTIVITIES_FILE, [])
    agg: Dict[str, Dict[str, int]] = {}

    for a in activities:
        if not a.get("end_time") or not a.get("duration_seconds"):
            # skip ongoing activities in summary
            continue
        # date based on start_time date
        date_key = a["start_time"][0:10]  # YYYY-MM-DD
        cat = a.get("activity_category", "other")
        agg.setdefault(date_key, {})
        agg[date_key][cat] = agg[date_key].get(cat, 0) + int(a["duration_seconds"])  # seconds

    dates_sorted = sorted(agg.keys())
    return {"dates": dates_sorted, "data": agg}


@app.get("/test")
def test():
    return {
        "backend": "✅ Running",
        "storage": "✅ JSON Files",
        "types_file": str(TYPES_FILE),
        "activities_file": str(ACTIVITIES_FILE),
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
