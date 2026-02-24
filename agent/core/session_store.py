"""
Session Store — Persist conversation history so sessions can be resumed.

Saves to ~/.godmode/sessions/<session-id>.jsonl (one JSON object per line).
Supports listing recent sessions and loading them back into ChatSession.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

SESSIONS_DIR = Path.home() / ".godmode" / "sessions"


def _ensure_dir() -> Path:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    return SESSIONS_DIR


def new_session_id() -> str:
    return str(uuid.uuid4())


def session_path(session_id: str) -> Path:
    return _ensure_dir() / f"{session_id}.jsonl"


def save_message(session_id: str, message: dict) -> None:
    """Append a single message to the session JSONL file."""
    path = session_path(session_id)
    record = {
        "ts": datetime.utcnow().isoformat(),
        **message,
    }
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def save_session_meta(session_id: str, repo_path: str, model: str) -> None:
    """Write/update a .meta.json sidebar file for listing sessions."""
    meta_path = _ensure_dir() / f"{session_id}.meta.json"
    meta = {
        "session_id": session_id,
        "repo_path": repo_path,
        "model": model,
        "started": datetime.utcnow().isoformat(),
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)


def load_session(session_id: str) -> list[dict]:
    """Load all messages from a session file."""
    path = session_path(session_id)
    if not path.exists():
        raise FileNotFoundError(f"Session not found: {session_id}")
    messages = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                data = json.loads(line)
                data.pop("ts", None)  # Remove timestamp metadata
                messages.append(data)
    return messages


def list_sessions(limit: int = 10) -> list[dict]:
    """List recent sessions from .meta.json files, newest first."""
    _ensure_dir()
    meta_files = sorted(
        SESSIONS_DIR.glob("*.meta.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )
    sessions = []
    for mf in meta_files[:limit]:
        try:
            with open(mf) as f:
                sessions.append(json.load(f))
        except Exception:
            pass
    return sessions


def print_sessions() -> Optional[str]:
    """
    Print a numbered list of recent sessions and prompt user to select.

    Returns the selected session_id, or None if user cancels.
    """
    sessions = list_sessions(10)
    if not sessions:
        print("  No saved sessions found.")
        return None

    print("\n📂 Recent God Mode Sessions:\n")
    for i, s in enumerate(sessions):
        print(f"  [{i+1}] {s.get('session_id', '?')[:8]}… | {s.get('repo_path', '?')} | {s.get('model', '?')} | {s.get('started', '')[:16]}")

    print("\n  Enter number to resume (or 'q' to cancel): ", end="")
    try:
        choice = input().strip()
        if choice.lower() == "q" or not choice:
            return None
        idx = int(choice) - 1
        if 0 <= idx < len(sessions):
            return sessions[idx]["session_id"]
    except (ValueError, EOFError):
        pass
    return None
