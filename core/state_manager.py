import json
import os
from datetime import date
from config import STATE_FILE, GEMINI_DAILY_LIMIT


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def is_seen(state, email_id):
    return email_id in state.get("_seen_ids", [])


def mark_seen(state, email_id):
    if "_seen_ids" not in state:
        state["_seen_ids"] = []
    if email_id not in state["_seen_ids"]:
        state["_seen_ids"].append(email_id)
    return state


def gemini_quota_ok(state):
    today = str(date.today())
    usage = state.get("_gemini_usage", {})
    if usage.get("date") != today:
        return True
    return usage.get("count", 0) < GEMINI_DAILY_LIMIT


def increment_gemini_usage(state):
    today = str(date.today())
    usage = state.get("_gemini_usage", {})
    if usage.get("date") != today:
        state["_gemini_usage"] = {"date": today, "count": 1}
    else:
        state["_gemini_usage"]["count"] = usage.get("count", 0) + 1
    return state
