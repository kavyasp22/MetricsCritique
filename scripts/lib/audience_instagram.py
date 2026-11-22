"""Flatten Instagram `comments_data` (jsonb) into comment and reply rows. Pure functions: no I/O, no database.

Observed structure (production, 2026-11-20): an array of comment objects
    {comment_id, text, username, is_verified, likes, created_at, parent_comment_id, replies: [ <same object> ]}
Replies are nested inside `replies` (one level deep in the data; deeper levels are handled anyway). A few top-level
items are not comments at all (keys such as id / type / url / accessibilityCaption, no comment_id, no text), and a few
top-level comments carry a `parent_comment_id` although their parent is not in the post.

Nothing is dropped: every element becomes a row with `shape` and `item_status` saying what it was.
"""
from __future__ import annotations

import json
from typing import Any

MALFORMED = "malformed_non_comment"


def _s(v: Any) -> str | None:
    if v is None:
        return None
    t = str(v).strip()
    return t or None


def flatten_payload(payload: Any) -> list[dict]:
    """Return one dict per element of a comments_data payload (comments and replies, in order)."""
    rows: list[dict] = []
    if payload is None:
        return rows
    if not isinstance(payload, list):
        return [{"item_status": MALFORMED, "shape": "payload_not_array", "depth": 0, "layer": "comment", "comment_id": None,
                 "parent_comment_id": None, "nest_parent_id": None, "text": None, "username": None, "is_verified": None,
                 "likes": None, "created_at": None, "raw": json.dumps(payload, default=str)[:500]}]
    for item in payload:
        _walk(item, 0, None, rows)
    return rows


def _walk(item: Any, depth: int, nest_parent: str | None, rows: list[dict]) -> None:
    if not isinstance(item, dict):
        rows.append({"item_status": MALFORMED, "shape": "not_an_object", "depth": depth, "layer": "reply" if depth else "comment",
                     "comment_id": None, "parent_comment_id": None, "nest_parent_id": nest_parent, "text": None, "username": None,
                     "is_verified": None, "likes": None, "created_at": None, "raw": json.dumps(item, default=str)[:500]})
        return
    cid = _s(item.get("comment_id"))
    declared = _s(item.get("parent_comment_id"))
    has_text_key = "text" in item
    ok = cid is not None and has_text_key
    # a reply is anything nested under a comment, or a top-level object that names a parent
    layer = "reply" if (depth >= 1 or declared) else "comment"
    if depth >= 1:
        shape = "nested"
    elif declared:
        shape = "top_level_with_parent"
    else:
        shape = "top_level"
    row = {
        "item_status": "ok" if ok else MALFORMED, "shape": shape, "depth": depth, "layer": layer, "comment_id": cid,
        "parent_comment_id": declared or nest_parent, "declared_parent_id": declared, "nest_parent_id": nest_parent,
        "text": item.get("text") if isinstance(item.get("text"), str) else (None if item.get("text") is None else str(item.get("text"))),
        "username": _s(item.get("username")), "is_verified": item.get("is_verified") if isinstance(item.get("is_verified"), bool) else None,
        "likes": item.get("likes") if isinstance(item.get("likes"), int) and not isinstance(item.get("likes"), bool) else None,
        "created_at": _s(item.get("created_at")),
        "raw": None if ok else json.dumps(item, default=str)[:500],
    }
    rows.append(row)
    reps = item.get("replies")
    if isinstance(reps, list):
        for r in reps:
            _walk(r, depth + 1, cid, rows)
    elif reps is not None:
        row["shape"] = row["shape"] + "+replies_not_list"
