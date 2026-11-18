"""Reader feedback (thumbs up / down with reasons) on alerts, stories and topics.

Stored in its OWN small SQLite file (default data/feedback.sqlite, git-ignored). It is never the metrics database and
never production: the dashboard stays read-only everywhere else. One row per (anonymous user, item, day); voting again
replaces the vote, and vote 0 withdraws it. Free text is length-limited and always shown as plain text.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ITEM_TYPES = ("alert", "story", "topic")
REASONS = ("not_a_real_spike", "routine_volume", "duplicate", "wrong_topic", "wrong_name", "bad_text", "other")
MAX_ID, MAX_LABEL, MAX_TEXT, MAX_USER = 200, 200, 300, 64


class FeedbackError(ValueError):
    pass


class FeedbackStore:
    def __init__(self, path: Path | str | None = None):
        default = Path(__file__).resolve().parents[1] / "data" / "feedback.sqlite"
        self.path = Path(path or os.environ.get("FEEDBACK_DB") or default)

    def _con(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path, timeout=10)
        con.execute("""CREATE TABLE IF NOT EXISTS votes(
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, user TEXT NOT NULL, item_type TEXT NOT NULL,
            item_id TEXT NOT NULL, day TEXT NOT NULL DEFAULT '', label TEXT, vote INTEGER NOT NULL, reasons TEXT, other_text TEXT,
            UNIQUE(user, item_type, item_id, day))""")
        return con

    @staticmethod
    def _clean(body: dict) -> dict:
        if not isinstance(body, dict):
            raise FeedbackError("body must be a JSON object")
        user = str(body.get("user") or "").strip()[:MAX_USER]
        if not user:
            raise FeedbackError("user is required")
        item_type = body.get("item_type")
        if item_type not in ITEM_TYPES:
            raise FeedbackError(f"item_type must be one of {ITEM_TYPES}")
        item_id = str(body.get("item_id") or "").strip()
        if not item_id or len(item_id) > MAX_ID:
            raise FeedbackError("item_id is required (at most %d characters)" % MAX_ID)
        vote = body.get("vote")
        if vote not in (1, -1, 0):
            raise FeedbackError("vote must be 1, -1 or 0")
        reasons = [r for r in (body.get("reasons") or []) if r in REASONS] if vote == -1 else []
        other = str(body.get("other_text") or "").strip()[:MAX_TEXT] if vote == -1 else ""
        return dict(user=user, item_type=item_type, item_id=item_id, day=str(body.get("day") or "")[:10],
                    label=str(body.get("label") or "")[:MAX_LABEL], vote=vote, reasons=reasons, other_text=other)

    def vote(self, body: dict) -> dict:
        v = self._clean(body)
        con = self._con()
        try:
            if v["vote"] == 0:
                con.execute("DELETE FROM votes WHERE user=? AND item_type=? AND item_id=? AND day=?",
                            (v["user"], v["item_type"], v["item_id"], v["day"]))
            else:
                con.execute("""INSERT INTO votes(created_at,user,item_type,item_id,day,label,vote,reasons,other_text)
                               VALUES(?,?,?,?,?,?,?,?,?)
                               ON CONFLICT(user,item_type,item_id,day) DO UPDATE SET created_at=excluded.created_at,
                               label=excluded.label, vote=excluded.vote, reasons=excluded.reasons, other_text=excluded.other_text""",
                            (datetime.now(timezone.utc).isoformat(timespec="seconds"), v["user"], v["item_type"], v["item_id"],
                             v["day"], v["label"], v["vote"], json.dumps(v["reasons"]), v["other_text"]))
            con.commit()
        finally:
            con.close()
        return {"ok": True, "vote": v["vote"]}

    def votes_for(self, user: str, item_type: str, ids: list, day: str = "") -> dict:
        """{item_id: {vote, reasons}} for one reader, so the buttons can show what they already chose."""
        if not user or item_type not in ITEM_TYPES or not ids:
            return {}
        ids = [str(i)[:MAX_ID] for i in ids][:200]
        con = self._con()
        try:
            q = ",".join("?" * len(ids))
            rows = con.execute(f"SELECT item_id, vote, reasons FROM votes WHERE user=? AND item_type=? AND day=? AND item_id IN ({q})",
                               [user[:MAX_USER], item_type, day[:10]] + ids).fetchall()
        finally:
            con.close()
        return {r[0]: {"vote": r[1], "reasons": json.loads(r[2] or "[]")} for r in rows}

    def summary(self) -> dict:
        con = self._con()
        try:
            by = con.execute("SELECT item_type, vote, COUNT(*) FROM votes GROUP BY 1,2").fetchall()
            reasons = {}
            for (js,) in con.execute("SELECT reasons FROM votes WHERE vote=-1"):
                for r in json.loads(js or "[]"):
                    reasons[r] = reasons.get(r, 0) + 1
            users = con.execute("SELECT COUNT(DISTINCT user) FROM votes").fetchone()[0]
            worst = con.execute("""SELECT item_type, item_id, MAX(label), SUM(vote=-1) d, SUM(vote=1) u FROM votes
                                   GROUP BY 1,2 HAVING d>0 ORDER BY d DESC, u ASC LIMIT 10""").fetchall()
        finally:
            con.close()
        out = {"readers": users, "by_type": {}, "down_reasons": reasons,
               "most_downvoted": [{"item_type": a, "item_id": b, "label": c, "down": d, "up": u} for a, b, c, d, u in worst]}
        for t, v, n in by:
            out["by_type"].setdefault(t, {"up": 0, "down": 0})["up" if v == 1 else "down"] += n
        return out
