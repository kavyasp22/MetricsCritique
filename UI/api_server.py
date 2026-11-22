#!/usr/bin/env python3
"""
Serves the dashboard's static files AND a read-only drill-down API.

Every clickable element in the UI (a topic bar, an entity name, a risk/theme
card, an alert row, a platform panel...) calls GET /api/drill here instead of
anything being preloaded into browser memory up front. Each call:
  - opens a short-lived cursor against the existing read-only connection
  - runs exactly one bounded (LIMIT-capped) query selecting only the columns
    that view needs
  - returns JSON and is done -- nothing is cached or accumulated server-side
    between requests, and the browser discards the rows when the drill panel
    is closed. Memory use is "one query's worth of rows", not "the corpus".
  - also attaches a small `related` block (co-occurring entities/categories/
    topics + a few similar posts) computed the same way -- see compute_related().

Usage:
    cd UI && ../.venv/bin/python3 api_server.py [port]   # default 8877
"""
from __future__ import annotations

import json
import re
import sys
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import duckdb

import audience_view as AUD       # Explore > Audience Sentiment (v2 audience tables); read-only, isolated
import keyword_query as KQ
from feedback_store import FeedbackError, FeedbackStore

from gemma_live import gemma_status
from live_view import live_overview, DEFAULT_MILITARY

HERE = Path(__file__).resolve().parent
EDA_ROOT = HERE.parent
DATA = EDA_ROOT / "data"

DEFAULT_LIMIT = 50
MAX_LIMIT = 300
POST_VIRALITY = DATA / "post_virality.parquet"   # scripts/24_post_virality.py; optional, the page works without it
POST_THEME = DATA / "post_theme_v1.parquet"      # scripts/27_post_theme.py: hybrid security theme D01..D09; optional
POST_THEME_KNN = DATA / "post_theme_knn_v1.parquet"   # scripts/28_theme_knn.py: theme spread to untagged posts by nearest-neighbour voting; optional
POST_EVENT_TOPIC = DATA / "post_event_topic_v1.parquet" # scripts/29_post_event_topic.py: event topic A01..A10 (41 subcodes); optional
POST_DEDUP = DATA / "post_dedup.parquet"         # scripts/25_post_dedup.py: is_first = the first post of an exact-copy group
RETWEET_SQL = "(coalesce(p.text, '') ILIKE 'RT @%')"      # a retweet starts "RT @user:"; only Twitter / Truth Social mark reposts this way in the data
VIRALITY_MIN_REF = 20                            # a percentile needs at least this many reference posts to be shown

_main_con = duckdb.connect(database=":memory:", read_only=False)
_main_con.execute(f"ATTACH '{DATA / 'analytics.duckdb'}' AS analytics (READ_ONLY)")
_main_con.execute(f"ATTACH '{DATA / 'metrics.duckdb'}' AS metrics (READ_ONLY)")
_main_con.execute(f"ATTACH '{DATA / 'geo_ontology.duckdb'}' AS geo (READ_ONLY)")
FEEDBACK = FeedbackStore()   # reader votes: its own SQLite file, never the metrics DB or production

POST_COLS = "p.post_uid, p.platform, p.posted_at, p.author_name, left(coalesce(p.text,''), 500) AS text, p.url"


def _limit(qs: dict) -> int:
    try:
        n = int(qs.get("limit", [DEFAULT_LIMIT])[0])
    except (ValueError, TypeError):
        n = DEFAULT_LIMIT
    return max(1, min(n, MAX_LIMIT))


def _day_clause(qs, where, params, col="p.posted_at"):
    """One day (`day`) or a range (`from` / `to`, inclusive). The metrics assign a post to its
    posted day, or to its scraped day when posted_at is missing, so the same rule is used here;
    otherwise a drill-down count would not match the number on the card that opened it."""
    scraped = col.replace("posted_at", "scraped_at")
    expr = f"CAST(coalesce({col}, {scraped}) AS DATE)"
    day = qs.get("day", [None])[0]
    if day:
        where.append(f"{expr} = ?")
        params.append(day)
    d_from, d_to = qs.get("from", [None])[0], qs.get("to", [None])[0]
    if d_from:
        where.append(f"{expr} >= ?")
        params.append(d_from)
    if d_to:
        where.append(f"{expr} <= ?")
        params.append(d_to)


def _platform_clause(qs, where, params, col="p.platform"):
    platform = qs.get("platform", [None])[0]
    if platform:
        where.append(f"{col} = ?")
        params.append(platform)
        return
    plats = [x for x in qs.get("platforms", [""])[0].split(",") if x]
    if plats:
        where.append(f"{col} IN (" + ",".join("?" * len(plats)) + ")")
        params.extend(plats)


def _theme_clause(qs, where, params):
    """Theme filter (D01..D09). Default = the clear matches: stage-27 high tier (74% right on a fresh sample) plus stage-28 neighbour
    votes of share >= 0.9 (about 75%). `theme_all=1` also takes the stage-27 medium tier (one cue term, 59%). Returns the chosen theme or ''."""
    th = qs.get("theme", [""])[0].strip()
    if not th:
        return ""
    if not re.fullmatch(r"D0[1-9]", th):
        raise ValueError("theme must be D01..D09")
    if not POST_THEME.exists():
        raise ValueError("themes are not built yet: run scripts/27_post_theme.py")
    tiers = ("high", "medium") if qs.get("theme_all", ["0"])[0] == "1" else ("high",)
    sub = (f"SELECT post_uid FROM read_parquet('{POST_THEME.as_posix()}') WHERE theme = ? AND tier IN ({','.join('?' * len(tiers))})")
    params.append(th); params.extend(tiers)
    if POST_THEME_KNN.exists():
        sub += f" UNION SELECT post_uid FROM read_parquet('{POST_THEME_KNN.as_posix()}') WHERE knn_theme = ?"
        params.append(th)
    where.append(f"p.post_uid IN ({sub})")
    return th


_ENG_P = "(coalesce(p.likes,0)+coalesce(p.comments,0)+coalesce(p.shares,0))"
_ENG_U = "(coalesce(likes,0)+coalesce(comments,0)+coalesce(shares,0))"
SORTS = ("views", "engagement", "shares", "likes")
POST_METRICS = ", p.views, p.likes, p.comments, p.shares"
COMMENT_METRICS = ", CAST(NULL AS DOUBLE) AS views, c.likes AS likes, CAST(NULL AS DOUBLE) AS comments, CAST(NULL AS DOUBLE) AS shares"


def _sort_key(qs):
    s = qs.get("sort", [None])[0]
    return s if s in SORTS else None


def _order_posts(sort, col="p.posted_at"):
    """ORDER BY for post rows. Views and engagement are different units, so each sort is only
    meaningful within one platform; ties fall back to engagement, then a stable id."""
    if sort == "views":
        return f"p.views DESC NULLS LAST, {_ENG_P} DESC, p.posted_at DESC"
    if sort == "engagement":
        return f"{_ENG_P} DESC, p.posted_at DESC"
    if sort == "shares":
        return f"p.shares DESC NULLS LAST, {_ENG_P} DESC, p.posted_at DESC"
    if sort == "likes":
        return f"p.likes DESC NULLS LAST, {_ENG_P} DESC, p.posted_at DESC"
    return f"{col} DESC"


def _order_comments(sort):
    # comments only carry a like count
    return "c.likes DESC NULLS LAST, c.comment_uid" if sort in SORTS and sort != "shares" else "c.posted_at DESC"


def _json_safe(o):
    if isinstance(o, float):
        return None if (o != o or o in (float("inf"), float("-inf"))) else o
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_safe(v) for v in o]
    return o


class NotYetPopulated(Exception):
    """A well-formed request against a declared-but-not-yet-built table.

    Distinct from ValueError (malformed request) so the handler can answer 503
    "not ready" rather than 400 "you asked wrong" -- the caller did nothing
    wrong and retrying after the pipeline runs will succeed.
    """


def _has_table(name: str, _cache: dict = {}) -> bool:
    """Does `geo.<name>` exist? Cached, because this is checked per request and
    the answer only changes when a pipeline stage runs."""
    if name not in _cache:
        _cache[name] = bool(_main_con.execute(
            "SELECT count(*) FROM duckdb_tables() "
            "WHERE database_name = 'geo' AND table_name = ?", [name]).fetchone()[0])
    return _cache[name]


# Which clustering the topic views serve. v4 is hierarchical: parent topics
# (p0x, level 1) over sub-topics (h0xx, level 2), rebuilt by
# scripts/15_topic_clustering_v4.py. Bumping this is the whole switch -- the
# queries below read it rather than hardcoding a table name, which is what made
# the v3 -> v4 move a one-line change instead of a grep-and-pray.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "lib"))
from topic_version import TOPIC_DIM_TABLE as TOPIC_DIM, TOPIC_POST_TABLE as TOPIC_POST  # noqa: E402


def _resolve_code(key: str) -> tuple[str, str]:
    """Accept either the canonical code_key ('a05_maritime') or the bare CAMEO
    code ('05'); return (code_key, code).

    code_key is canonical for display and routing: bare numeric codes repeat
    across axes and levels, which is precisely the collision the code_key scheme
    exists to prevent, and it is what the taxonomy axes are keyed by -- so an API
    that emitted '05' would make every UI label fall back to the raw string.
    Hierarchy, however, is expressed in the numeric code ('05' -> '05.2'), so
    subtree queries need that form. Both are carried rather than picking one.
    Unknown keys pass through unchanged so the caller still gets an empty result
    rather than an error."""
    if not key or not _has_table("event_codes"):
        return key, key
    row = _main_con.execute(
        "SELECT code_key, code FROM geo.event_codes WHERE code_key = ? OR code = ? LIMIT 1",
        [key, key]).fetchone()
    return (row[0], row[1]) if row else (key, key)


# Which drill view serves each geo_alert_candidates.dimension_type, and which
# query parameter carries the key. This is the extension point: a new dimension
# needs one line here, not a new branch inside the alert router.
ALERT_ROUTES: dict[str, tuple[str, str]] = {
    "entity": ("entity", "entity_uid"),
    "risk": ("risk", "category"),
    "theme": ("theme", "category"),
    "theme_code": ("theme_code", "code"),
    "theme_subcode": ("theme_subcode", "code"),
    "action": ("action", "code"),
}


def build_query(view: str, qs: dict):
    """Returns (sql, params) for a given drill view + querystring dict, or raises ValueError."""
    where, params = [], []

    if view == "topic":  # metrics v2 topic_id (t0xx), from analytics.post_topic
        topic_id = qs.get("topic_id", [None])[0]
        if not topic_id:
            raise ValueError("topic_id required")
        platform = qs.get("platform", [None])[0]
        ent_uid = qs.get("entity_uid", [None])[0]
        ent_sql = "p.post_uid IN (SELECT post_uid FROM geo.entity_mentions WHERE entity_uid = ?)"
        if re.match(r"^[Dd]\d\d$", topic_id):
            if not POST_THEME.exists():
                raise ValueError("themes are not built yet: run scripts/27_post_theme.py")
            if platform == "youtube_comment":
                raise ValueError("security themes do not apply to YouTube comments")
            where.append("pt.theme = ? AND pt.tier IN ('high', 'medium')")
            params.append(topic_id.upper())
            _platform_clause(qs, where, params)
            if ent_uid:
                where.append(ent_sql); params.append(ent_uid)
            _day_clause(qs, where, params)
            sk = _sort_key(qs)
            sql = f"""
                SELECT {POST_COLS}{POST_METRICS}, pt.zs_best_score AS confidence, count(*) OVER () AS total_rows
                FROM read_parquet('{POST_THEME.as_posix()}') pt
                JOIN analytics.post_facts p ON p.post_uid = pt.post_uid
                WHERE {' AND '.join(where)}
                ORDER BY {_order_posts(sk)}
            """
            return sql, params
        if platform == "youtube_comment" and ent_uid:
            raise ValueError("the entity filter does not apply to YouTube comments (they carry no entity mentions)")
        if platform == "youtube_comment":
            c_where = []
            if topic_id in ("t004", "t005"):
                c_where.append("pt.topic_id IN ('t004', 't005')")
            else:
                c_where.append("pt.topic_id = ?")
                params.append(topic_id)
            _day_clause(qs, c_where, params, col="c.posted_at")
            sql = f"""
                SELECT c.comment_uid AS post_uid, 'youtube_comment' AS platform, c.posted_at,
                       coalesce(c.author_display_name, 'anonymous') AS author_name,
                       left(coalesce(c.text, ''), 500) AS text,
                       '' AS url{COMMENT_METRICS},
                       pt.confidence,
                       count(*) OVER () AS total_rows
                FROM analytics.comment_facts c
                JOIN analytics.post_facts pf ON c.video_id = pf.native_id AND pf.platform = 'youtube_video'
                JOIN analytics.post_topic pt ON pt.post_uid = pf.post_uid
                WHERE {' AND '.join(c_where)}
                ORDER BY {_order_comments(_sort_key(qs))}
            """
            return sql, params

        if platform is None:
            # All platforms (or a chosen list): the card counts posts AND YouTube comments, so the
            # drill-down must list both or its total will not match the number on the card.
            plats = [x for x in qs.get("platforms", [""])[0].split(",") if x]
            want_comments = ((not plats) or ("youtube_comment" in plats)) and not ent_uid   # comments carry no entity mentions
            post_plats = [x for x in plats if x != "youtube_comment"]
            parts, uparams = [], []
            if (not plats) or post_plats:
                w, pr = [], []
                if topic_id in ("t004", "t005"):
                    w.append("pt.topic_id IN ('t004', 't005')")
                else:
                    w.append("pt.topic_id = ?"); pr.append(topic_id)
                if post_plats:
                    w.append("p.platform IN (" + ",".join("?" * len(post_plats)) + ")"); pr.extend(post_plats)
                if ent_uid:
                    w.append(ent_sql); pr.append(ent_uid)
                _day_clause(qs, w, pr)
                parts.append(f"""SELECT {POST_COLS}{POST_METRICS}, pt.confidence FROM analytics.post_topic pt
                    JOIN analytics.post_facts p ON p.post_uid = pt.post_uid WHERE {' AND '.join(w)}""")
                uparams += pr
            if want_comments:
                w, pr = [], []
                if topic_id in ("t004", "t005"):
                    w.append("pt.topic_id IN ('t004', 't005')")
                else:
                    w.append("pt.topic_id = ?"); pr.append(topic_id)
                _day_clause(qs, w, pr, col="c.posted_at")
                parts.append(f"""SELECT c.comment_uid AS post_uid, 'youtube_comment' AS platform, c.posted_at,
                       coalesce(c.author_display_name, 'anonymous') AS author_name,
                       left(coalesce(c.text, ''), 500) AS text, '' AS url{COMMENT_METRICS}, pt.confidence
                    FROM analytics.comment_facts c
                    JOIN analytics.post_facts pf ON c.video_id = pf.native_id AND pf.platform = 'youtube_video'
                    JOIN analytics.post_topic pt ON pt.post_uid = pf.post_uid WHERE {' AND '.join(w)}""")
                uparams += pr
            if not parts:
                raise ValueError("no post platform selected: YouTube comments carry no entity mentions")
            sk = _sort_key(qs)
            order = {"views": f"views DESC NULLS LAST, {_ENG_U} DESC, posted_at DESC",
                     "engagement": f"{_ENG_U} DESC, posted_at DESC",
                     "shares": f"shares DESC NULLS LAST, {_ENG_U} DESC, posted_at DESC",
                     "likes": f"likes DESC NULLS LAST, {_ENG_U} DESC, posted_at DESC"}.get(sk, "posted_at DESC")
            sql = ("SELECT *, count(*) OVER () AS total_rows FROM (" + " UNION ALL ".join(parts) +
                   f") u ORDER BY {order}")
            return sql, uparams

        if topic_id in ("t004", "t005"):
            where.append("pt.topic_id IN ('t004', 't005')")
        else:
            where.append("pt.topic_id = ?")
            params.append(topic_id)
        _platform_clause(qs, where, params)
        if ent_uid:
            where.append(ent_sql); params.append(ent_uid)
        _day_clause(qs, where, params)
        sql = f"""
            SELECT {POST_COLS}{POST_METRICS}, pt.confidence, count(*) OVER () AS total_rows
            FROM analytics.post_topic pt
            JOIN analytics.post_facts p ON p.post_uid = pt.post_uid
            WHERE {' AND '.join(where)}
            ORDER BY {_order_posts(_sort_key(qs))}
        """
        return sql, params

    if view in ("topic_v3", "topic_v4"):
        # Hierarchical topics from scripts/15_topic_clustering_v4.py.
        # `topic_v3` is kept as an alias so links minted before the v4 switch
        # keep resolving -- they carry cluster_id, which still means "a cluster".
        # NB: "topic" is NOT accepted here -- it is already taken by the metrics
        # v2 keyword topics (t0xx, analytics.post_topic) handled above.
        cluster_id = qs.get("cluster_id", [None])[0]
        topic_id = qs.get("topic_id", [None])[0]
        platform = qs.get("platform", [None])[0]
        if platform == "youtube_comment":
            c_where = []
            if topic_id and str(topic_id).startswith("p"):
                c_where.append("pt.parent_topic_id = ?")
                params.append(topic_id)
            elif topic_id:
                c_where.append("pt.topic_id = ?")
                params.append(topic_id)
            elif cluster_id is not None:
                c_where.append("pt.cluster_id = ?")
                params.append(int(cluster_id))
            else:
                raise ValueError("cluster_id or topic_id required")
            _day_clause(qs, c_where, params, col="c.posted_at")
            sql = f"""
                SELECT c.comment_uid AS post_uid, 'youtube_comment' AS platform, c.posted_at,
                       coalesce(c.author_display_name, 'anonymous') AS author_name,
                       left(coalesce(c.text, ''), 500) AS text,
                       '' AS url,
                       pt.confidence,
                       count(*) OVER () AS total_rows
                FROM analytics.comment_facts c
                JOIN analytics.post_facts pf ON c.video_id = pf.native_id AND pf.platform = 'youtube_video'
                JOIN geo.{TOPIC_POST} pt ON pt.post_uid = pf.post_uid
                WHERE {' AND '.join(c_where)}
                ORDER BY c.posted_at DESC
            """
            return sql, params

        if topic_id and str(topic_id).startswith("p"):
            # A parent drill returns the union of its sub-topics' posts, the same
            # contract as a CAMEO parent-code drill. Clicking a parent that showed
            # nothing would be the obvious wrong answer.
            where.append("pt.parent_topic_id = ?")
            params.append(topic_id)
        elif topic_id:
            where.append("pt.topic_id = ?")
            params.append(topic_id)
        elif cluster_id is not None:
            where.append("pt.cluster_id = ?")
            params.append(int(cluster_id))
        else:
            raise ValueError("cluster_id or topic_id required")
        _platform_clause(qs, where, params)
        _day_clause(qs, where, params)
        sql = f"""
            SELECT {POST_COLS}, pt.confidence, count(*) OVER () AS total_rows
            FROM geo.{TOPIC_POST} pt
            JOIN analytics.post_facts p ON p.post_uid = pt.post_uid
            WHERE {' AND '.join(where)}
            ORDER BY p.posted_at DESC
        """
        return sql, params

    if view == "tag":
        tag_id = qs.get("tag_id", [None])[0]
        if not tag_id:
            raise ValueError("tag_id required")
        where.append("td.tag_id = ?")
        params.append(tag_id)
        # tag_dim is mined (07_metrics_v2.py::mine_tags) from the UNION of the structured
        # hashtags field and inline #tags in post text -- approximate both here rather than
        # only the structured field, or most tags would look nearly empty on drill-down.
        where.append("""(
            lower(coalesce(p.hashtags,'')) LIKE ('%"' || lower(td.tag) || '"%')
            OR regexp_matches(lower(coalesce(p.text,'')), '(^|[^[:alnum:]_])#' || lower(td.tag) || '([^[:alnum:]_]|$)')
        )""")
        _platform_clause(qs, where, params)
        _day_clause(qs, where, params)
        sql = f"""
            SELECT {POST_COLS}, count(*) OVER () AS total_rows
            FROM metrics.tag_dim td, analytics.post_facts p
            WHERE {' AND '.join(where)}
            ORDER BY p.posted_at DESC
        """
        return sql, params

    if view == "cross_platform":
        story_id = qs.get("story_id", [None])[0]
        if not story_id:
            raise ValueError("story_id required")
        sql = f"""
            SELECT {POST_COLS}, count(*) OVER () AS total_rows
            FROM metrics.cross_platform_posts cp
            JOIN analytics.post_facts p ON p.post_uid = cp.post_uid
            WHERE cp.story_id = ?
            ORDER BY p.posted_at ASC
        """
        return sql, [story_id]

    if view == "cross_platform_url":
        link_id = qs.get("link_id", [None])[0]
        if not link_id:
            raise ValueError("link_id required")
        sql = f"""
            SELECT {POST_COLS}, count(*) OVER () AS total_rows
            FROM metrics.cross_platform_url_posts cp
            JOIN analytics.post_facts p ON p.post_uid = cp.post_uid
            WHERE cp.link_id = ?
            ORDER BY p.posted_at ASC
        """
        return sql, [link_id]

    if view == "cross_platform_event":
        event_id = qs.get("event_id", [None])[0]
        if not event_id:
            raise ValueError("event_id required")
        sql = f"""
            SELECT {POST_COLS}, count(*) OVER () AS total_rows
            FROM metrics.cross_platform_semantic_posts cp
            JOIN analytics.post_facts p ON p.post_uid = cp.post_uid
            WHERE cp.event_id = ?
            ORDER BY p.posted_at ASC
        """
        return sql, [event_id]

    if view == "entity":
        entity_uid = qs.get("entity_uid", [None])[0]
        if not entity_uid:
            raise ValueError("entity_uid required")
        where.append("em.entity_uid = ?")
        params.append(entity_uid)
        _platform_clause(qs, where, params)
        _day_clause(qs, where, params)
        sql = f"""
            SELECT {POST_COLS}, em.surface_form, em.entity_subtype, count(*) OVER () AS total_rows
            FROM geo.entity_mentions em
            JOIN analytics.post_facts p ON p.post_uid = em.post_uid
            WHERE {' AND '.join(where)}
            ORDER BY p.posted_at DESC
        """
        return sql, params

    if view == "entity_subtype":  # taxonomy card drill: every mention of a given subtype, any entity
        subtype = qs.get("entity_subtype", [None])[0]
        if not subtype:
            raise ValueError("entity_subtype required")
        where.append("em.entity_subtype = ?")
        params.append(subtype)
        _platform_clause(qs, where, params)
        _day_clause(qs, where, params)
        sql = f"""
            SELECT {POST_COLS}, em.surface_form, em.entity_subtype, count(*) OVER () AS total_rows
            FROM geo.entity_mentions em
            JOIN analytics.post_facts p ON p.post_uid = em.post_uid
            WHERE {' AND '.join(where)}
            ORDER BY p.posted_at DESC
        """
        return sql, params

    if view in ("risk", "theme"):
        table = "geo.risk_labels" if view == "risk" else "geo.theme_labels"
        category = qs.get("category", [None])[0]
        if not category:
            raise ValueError("category required")
        where.append("rl.category = ?")
        params.append(category)
        _platform_clause(qs, where, params)
        _day_clause(qs, where, params)
        sql = f"""
            SELECT {POST_COLS}, rl.score, rl.gray_zone, count(*) OVER () AS total_rows
            FROM {table} rl
            JOIN analytics.post_facts p ON p.post_uid = rl.post_uid
            WHERE {' AND '.join(where)}
            ORDER BY rl.score DESC
        """
        return sql, params

    if view == "platform":
        platform = qs.get("platform", [None])[0]
        if not platform:
            raise ValueError("platform required")
        sentiment = qs.get("sentiment", [None])[0]
        if platform == "youtube_comment":
            c_where = []
            _day_clause(qs, c_where, params, col="c.posted_at")
            where_sql = f"WHERE {' AND '.join(c_where)}" if c_where else ""
            sql = f"""
                SELECT c.comment_uid AS post_uid, 'youtube_comment' AS platform, c.posted_at,
                       coalesce(c.author_display_name, 'anonymous') AS author_name,
                       left(coalesce(c.text, ''), 500) AS text,
                       '' AS url,
                       count(*) OVER () AS total_rows
                FROM analytics.comment_facts c
                {where_sql}
                ORDER BY c.posted_at DESC
            """
            return sql, params
        where.append("p.platform = ?")
        params.append(platform)
        _day_clause(qs, where, params)
        join_sql = ""
        score_col = ""
        if sentiment:
            join_sql = "JOIN analytics.post_scores sc ON sc.post_uid = p.post_uid"
            score_col = ", round(sc.tone_score, 3) AS tone_score"
            if sentiment == "positive":
                where.append("sc.tone_score > 0.15 AND sc.tone_valid = true")
            elif sentiment == "negative":
                where.append("sc.tone_score < -0.15 AND sc.tone_valid = true")
            elif sentiment == "neutral":
                where.append("sc.tone_score >= -0.15 AND sc.tone_score <= 0.15 AND sc.tone_valid = true")
        sql = f"""
            SELECT {POST_COLS}{score_col}, count(*) OVER () AS total_rows
            FROM analytics.post_facts p
            {join_sql}
            WHERE {' AND '.join(where)}
            ORDER BY p.posted_at DESC
        """
        return sql, params

    if view == "posts":  # generic corpus browse: optional platform/day, no dimension required
        _platform_clause(qs, where, params)
        _day_clause(qs, where, params)
        sql = f"""
            SELECT {POST_COLS}, count(*) OVER () AS total_rows
            FROM analytics.post_facts p
            {'WHERE ' + ' AND '.join(where) if where else ''}
            ORDER BY p.posted_at DESC
        """
        return sql, params

    if view in ("theme_code", "theme_subcode", "action"):
        # CAMEO event codes (scripts/14_cameo_event_coding.py). One table with an
        # `axis` column rather than three, so a hierarchy query is a single scan.
        if not _has_table("event_codes"):
            # Not a 400. The taxonomy declares these axes before the coding pass
            # has populated them (status=declared_not_yet_populated), so "not yet
            # coded" is an expected state, not a malformed request. Returning an
            # error here would make a correctly-declared-but-unpopulated axis
            # indistinguishable from a bug.
            raise NotYetPopulated(
                "event_codes has not been built yet -- run "
                "scripts/14_cameo_event_coding.py --level parent")
        code = qs.get("code", [None])[0] or qs.get("category", [None])[0]
        if not code:
            raise ValueError("code required")
        code_key, code_num = _resolve_code(code)
        axis = "action" if view == "action" else "theme"
        where.append("ec.axis = ?")
        params.append(axis)
        if view == "theme_code":
            # A parent drill must return its whole subtree. Exact equality would
            # show 05 as if it had no 05.x children, which is the opposite of
            # what a user clicking a parent expects. Matching is on the *numeric*
            # code because that is where the hierarchy lives ('05' -> '05.2');
            # code_key does not nest lexically (a05_maritime is not a prefix of
            # a05_2_maritime_disputes).
            where.append("(ec.code_key = ? OR ec.code LIKE ? || '.%')")
            params.extend([code_key, code_num])
        else:
            where.append("ec.code_key = ?")
            params.append(code_key)
        _platform_clause(qs, where, params)
        _day_clause(qs, where, params)
        sql = f"""
            SELECT {POST_COLS}, ec.score, ec.code, ec.gray_zone,
                   ec.threshold_source, count(*) OVER () AS total_rows
            FROM geo.event_codes ec
            JOIN analytics.post_facts p ON p.post_uid = ec.post_uid
            WHERE {' AND '.join(where)}
            ORDER BY ec.score DESC, ec.post_uid
        """
        return sql, params

    if view == "alert":
        # Routes a geo_alert_candidates row (dimension_type + dimension_key + day)
        # to the matching drill view, pre-filtered to that alert's day.
        dtype = qs.get("dimension_type", [None])[0]
        dkey = qs.get("dimension_key", [None])[0]
        if not dtype or not dkey:
            raise ValueError("dimension_type and dimension_key required")
        route = ALERT_ROUTES.get(dtype)
        if route is None:
            # Was a hard `raise ValueError`, which made this the most brittle
            # coupling in the repo: adding ANY new dimension upstream 400'd every
            # drill on it until someone remembered to edit this function. The
            # registry above is the extension point; an unknown type now names
            # itself and lists what IS routable, instead of just failing.
            raise ValueError(
                f"unknown dimension_type {dtype!r}; routable types are "
                f"{sorted(ALERT_ROUTES)}. Add it to ALERT_ROUTES in api_server.py.")
        sub_view, param = route
        sub_qs = dict(qs)
        sub_qs[param] = [dkey]
        return build_query(sub_view, sub_qs)

    raise ValueError(f"unknown view {view!r}")


# ---------------------------------------------------------------- related --
# "Related items" for the drill panel: co-occurring entities, co-occurring
# risk/theme categories, related topics (the served clustering -- the more
# refined of the two topic ontologies in this project), and a handful of
# similar posts (single-seed cosine similarity via DuckDB's built-in
# list_cosine_similarity against the existing bge-m3 embeddings -- no
# separate cache, no new dependency, same one-bounded-query-per-bit-of-work
# style as the rest of this file). Co-occurrence is normalized by the
# candidate's own overall frequency (a lift-style score), not raw counts, or
# every card would just surface the globally most-common entities.
#
# Best-effort: any failure here must never break the main `rows` response
# (see do_GET), so this returns partial/empty results rather than raising
# where practical.

RELATED_LIMIT = 6
SIMILAR_LIMIT = 5

# Which event_codes (axis, level) each CAMEO dimension covers. Candidates are
# emitted under these dim_type names so a click routes straight back through
# ALERT_ROUTES. level=None means "any" (the action axis is flat).
CAMEO_RELATED_AXES: dict[str, tuple[str, int | None]] = {
    "theme_code": ("theme", 1),
    "theme_subcode": ("theme", 2),
    "action": ("action", None),
}
RELATED_MIN_PER_AXIS = 2  # see _per_axis_cap
SEL_SCOPE_LIMIT = 2000  # cap on how many posts back the co-occurrence stats, independent of the small `rows` limit

# Association scoring. The first version of this divided a *mention* count by a
# *post* count (units mismatch, scores could exceed 1.0) with only a 2-mention
# floor, which meant an entity seen in one post outranked everything: for
# entity=Iran all six slots were singletons (Somaliland 6 mentions / 1 post),
# while the real partners (Red Sea, Yemen, Strait of Hormuz) never appeared.
#
# Now: count DISTINCT posts on both sides, score by lift against the corpus
# base rate, and shrink toward 1.0 by co-occurrence support so tiny counts
# cannot dominate. Floors below are deliberately blunt -- they exist to keep
# one-post coincidences out of a 6-slot list, not to be finely tuned.
MIN_CO_POSTS = 3       # candidate must co-occur in at least this many distinct posts
MIN_GLOBAL_POSTS = 10  # ...and be common enough that its base rate is meaningful
SHRINK_K = 5.0         # lift * co/(co+k): damps small-sample lift spikes


def _current_topic_v3_cluster(qs: dict) -> int:
    cid = qs.get("cluster_id", [None])[0]
    if cid is not None:
        try:
            return int(cid)
        except ValueError:
            return -1
    tid = qs.get("topic_id", [None])[0]
    # v4 sub-topics are h0xx and parents p0x; v3 used g0xx. Accept all three so a
    # stale link still self-excludes correctly rather than suggesting itself back.
    if tid and tid[:1] in ("h", "p", "g") and tid[1:].isdigit():
        return int(tid[1:])
    return -1


def _self_exclusions(view: str, qs: dict):
    """(exclude_entity_uid, exclude_cat_dim, exclude_cat_key, exclude_cluster_id,
    (exclude_axis, exclude_code)) for the dimension value currently being viewed, so
    it's never suggested back as its own 'related' item. Empty string / -1 sentinels
    never match a real key.

    The 5th slot is the CAMEO one and is deliberately *not* an exact key: on a
    hierarchical axis, self-exclusion has to cover the whole ancestor/descendant
    chain. A parent code co-occurs with its own child in 100% of the child's posts,
    which is maximal lift by construction -- match only the exact key and code 05
    tops the related list for every one of its sub-codes, in every slot, forever.
    That is a tautology, not an association. The axis (not the dim_type) is what
    scopes it, because theme_code and theme_subcode are two levels of one tree."""
    def cameo(dtype: str, key: str):
        axis, _lvl = CAMEO_RELATED_AXES.get(dtype, ("", None))
        return ("", "", "", -1, (axis, key if axis else ""))

    if view == "alert":
        dtype = qs.get("dimension_type", [None])[0] or ""
        dkey = qs.get("dimension_key", [None])[0] or ""
        if dtype == "entity":
            return dkey, "", "", -1, ("", "")
        if dtype in ("risk", "theme"):
            return "", dtype, dkey, -1, ("", "")
        if dtype in CAMEO_RELATED_AXES:
            return cameo(dtype, dkey)
        return "", "", "", -1, ("", "")
    if view == "entity":
        return qs.get("entity_uid", [None])[0] or "", "", "", -1, ("", "")
    if view in ("risk", "theme"):
        return "", view, qs.get("category", [None])[0] or "", -1, ("", "")
    if view in CAMEO_RELATED_AXES:
        return cameo(view, qs.get("code", [None])[0] or "")
    if view in ("topic_v3", "topic_v4"):
        return "", "", "", _current_topic_v3_cluster(qs), ("", "")
    return "", "", "", -1, ("", "")


def _per_axis_cap(n_axes: int) -> int:
    """How many related slots any single axis may take.

    Sub-codes are the narrowest categories and therefore carry the highest lift,
    so an unbudgeted top-N over the union hands every slot to one axis. Scale the
    cap so the pre-CAMEO behaviour is unchanged (2 axes -> 3 each -> the same 6),
    while a fully-populated 5-axis union guarantees at least three axes appear."""
    if n_axes <= 0:
        return RELATED_LIMIT
    return max(RELATED_MIN_PER_AXIS, -(-RELATED_LIMIT // n_axes))


def _snippet(text: str, words: list, width: int = 320) -> str:
    """First `width` characters, or - when searching - a window around the first matching word so the match is visible."""
    if len(text) <= width:
        return text
    if words:
        alts = "|".join(re.escape(w).replace("\\ ", r"\s+") for w in sorted(words, key=len, reverse=True))
        m = re.search(r"(?<![\w])(" + alts + r")(?![\w])", text, re.IGNORECASE)
        if m and m.start() > width - 60:
            start = max(0, m.start() - width // 3)
            return "\u2026" + text[start:start + width]
    return text[:width]


def _clean_posts(rows, terms):
    """NaN is not valid JSON (a browser's JSON.parse rejects the whole response): send null; cut the text to a snippet."""
    out = []
    for r in rows:
        r = {k: (None if isinstance(v, float) and v != v else v) for k, v in r.items()}
        r["text"] = _snippet(r.get("text") or "", terms)
        out.append(r)
    return out


_PV_COLS = None


def _pv_cols() -> set:
    """Columns of data/post_virality.parquet. Re-read until the activity columns are there, so a regenerated file needs no restart."""
    global _PV_COLS
    if _PV_COLS is None or "activity_top10" not in _PV_COLS:
        _PV_COLS = {r[0] for r in _main_con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{POST_VIRALITY.as_posix()}')").fetchall()}
    return _PV_COLS


def _pv_has_floor() -> bool:
    return "activity_ok" in _pv_cols()


def explore_virality(qs, where, params, join, params_join, terms, limit):
    """Viral Posts ranked by the per-post virality percentile (stage 24): where a post sits among posts of the SAME platform and
    age band (0.5 typical, 1.0 top). It is comparable across platforms, unlike raw views. Only posts with a usable ratio and a
    reference set of at least VIRALITY_MIN_REF posts are ranked. `per_platform=N` returns the top N of every platform instead of one
    mixed list. Ties are broken by engagement, so the order is stable.
    The percentile ranks a RATIO, so a post with 4 views and 1 share can reach 1.00. A post must also reach a floor on its raw
    activity (views, else likes + comments + shares), relative to rated posts of its own platform and basis (no fixed number):
    `floor=top10` (default) = top 10% of its platform, `floor=median` = the platform median or more, `floor=off` = no floor.
    `order=biggest` (default) sorts by where the post's raw attention sits among posts of the same platform, basis and age band
    (activity_pct, comparable across platforms), then by its multiple of the platform's top-10% line, so the largest posts come first
    whatever their share rate; posts with no ratio (for example Snapchat) can appear. `order=amplified` sorts by the ratio
    percentile described above. The two are deliberately not combined: a geometric mean put the most viewed posts at ranks 26-30,003."""
    order = qs.get("order", ["biggest"])[0]
    order = order if order in ("biggest", "amplified") else "biggest"
    if order == "biggest" and not {"activity_mult", "activity_pct"} <= _pv_cols():
        order = "amplified"                   # older file without the multiple
    mode = qs.get("floor", ["top10"])[0]
    mode = mode if mode in ("top10", "median", "off") else ("median" if mode == "on" else "top10")
    cols_have = _pv_cols()
    if mode == "top10" and "activity_top10" not in cols_have:
        mode = "median"                       # older file: fall back to the median floor
    floor_col = {"top10": "activity_top10", "median": "activity_ok"}.get(mode)
    floor_on = mode != "off" and floor_col in cols_have
    cols = "p.post_uid, p.platform, p.posted_at, p.author_name, left(coalesce(p.text,''), 100000) AS text, p.url"
    inner = (f"SELECT {cols}, p.views, p.likes, p.comments, p.shares, "
             f"coalesce(p.likes,0)+coalesce(p.comments,0)+coalesce(p.shares,0) AS engagement, {RETWEET_SQL} AS is_retweet, v.virality_pct, v.virality_ref_n, "
             f"{'v.activity_mult AS attention_mult, v.activity_basis, v.activity_pct AS attention_pct, v.activity_ref_n AS attention_ref_n' if {'activity_mult', 'activity_pct'} <= cols_have else 'CAST(NULL AS DOUBLE) AS attention_mult, CAST(NULL AS VARCHAR) AS activity_basis, CAST(NULL AS DOUBLE) AS attention_pct, CAST(NULL AS DOUBLE) AS attention_ref_n'} "
             f"FROM analytics.post_facts p {join} JOIN read_parquet('{POST_VIRALITY.as_posix()}') v ON v.post_uid = p.post_uid "
             f"WHERE {' AND '.join(where)} AND " +
             ("v.activity IS NOT NULL" if order == "biggest" else f"v.virality_pct IS NOT NULL AND v.virality_ref_n >= {VIRALITY_MIN_REF}"))
    order_by = ("attention_pct DESC NULLS LAST, attention_mult DESC NULLS LAST, engagement DESC, post_uid" if order == "biggest"
                else "virality_pct DESC, engagement DESC, post_uid")
    inner_all = inner
    if floor_on:
        inner = inner + f" AND v.{floor_col}"
    try:
        per = int(qs.get("per_platform", ["0"])[0])
    except ValueError:
        per = 0
    cur = _main_con.cursor()
    try:
        if per > 0:
            per = min(per, 20)
            sql = (f"SELECT * EXCLUDE (rn) FROM (SELECT *, row_number() OVER (PARTITION BY platform ORDER BY {order_by}) AS rn "
                   f"FROM ({inner}) t) u WHERE rn <= {per} ORDER BY platform, rn")
            rows = cur.execute(sql, params_join + params).fetchdf().to_dict(orient="records")
        else:
            rows = cur.execute(f"SELECT * FROM ({inner}) t ORDER BY {order_by} LIMIT ?",
                               params_join + params + [limit]).fetchdf().to_dict(orient="records")
        rated = cur.execute(f"SELECT count(*) FROM ({inner}) t", params_join + params).fetchall()[0][0]
        rated_all = cur.execute(f"SELECT count(*) FROM ({inner_all}) t", params_join + params).fetchall()[0][0] if floor_on else rated
        total = cur.execute(f"SELECT count(*) FROM analytics.post_facts p {join} WHERE {' AND '.join(where)}",
                            params_join + params).fetchall()[0][0]
    finally:
        cur.close()
    return {"rank": "virality", "order": order, "per_platform": per, "total_posts": int(total), "rated_posts": int(rated),
            "floor_on": bool(floor_on), "floor_mode": mode if floor_on else "off", "below_floor": int(rated_all - rated),
            "min_ref": VIRALITY_MIN_REF, "by_virality": _clean_posts(rows, terms), "terms": terms}


def explore_viral(qs: dict) -> dict:
    """Explore page: posts ranked by observed views (and, separately, engagement for platforms
    that give no view count). Read-only. Filters: from/to (day), platforms (csv), q (text),
    entity_uid. Returns two lists so a post with no views is never ranked as zero."""
    where, params = ["1=1"], []
    d_from, d_to = qs.get("from", [""])[0], qs.get("to", [""])[0]
    if d_from:
        where.append("CAST(p.posted_at AS DATE) >= ?"); params.append(d_from)
    if d_to:
        where.append("CAST(p.posted_at AS DATE) <= ?"); params.append(d_to)
    plats = [x for x in qs.get("platforms", [""])[0].split(",") if x]
    if plats:
        where.append("p.platform IN (" + ",".join("?" * len(plats)) + ")"); params += plats
    q = qs.get("q", [""])[0].strip()
    terms = []
    if q:
        kq = KQ.parse(q)                       # raises KeywordError -> 400 with a readable message
        cond, cp = KQ.to_sql(kq, "p.text")
        where.append(cond); params.extend(cp)
        terms = kq.words()
    tid = qs.get("topic_id", [""])[0].strip()
    if tid:
        if not re.fullmatch(r"[A-Za-z0-9_]+", tid):
            raise ValueError("topic_id has an invalid form")
        ids = ["t004", "t005"] if tid in ("t004", "t005") else [tid]      # the dashboard shows these two as one topic
        where.append("p.post_uid IN (SELECT post_uid FROM analytics.post_topic WHERE topic_id IN (" + ",".join("?" * len(ids)) + "))")
        params += ids
    ent = qs.get("entity_uid", [""])[0].strip()
    join = ""
    if ent:
        join = "JOIN (SELECT DISTINCT post_uid FROM geo.entity_mentions WHERE entity_uid = ?) em ON em.post_uid = p.post_uid"
        params_join = [ent]
    else:
        params_join = []
    if qs.get("retweets", ["include"])[0] == "hide":
        where.append("NOT " + RETWEET_SQL)
    _theme_clause(qs, where, params)
    limit = _limit(qs)
    have_v = POST_VIRALITY.exists()
    vjoin = f" LEFT JOIN read_parquet('{POST_VIRALITY.as_posix()}') v ON v.post_uid = p.post_uid" if have_v else ""
    vcols = ", v.virality_pct, v.virality_ref_n" if have_v else ", CAST(NULL AS DOUBLE) AS virality_pct, CAST(NULL AS BIGINT) AS virality_ref_n"
    rank = qs.get("rank", ["views"])[0]
    if rank == "virality":
        if not have_v:
            raise ValueError("per-post virality is not built yet: run scripts/24_post_virality.py")
        return explore_virality(qs, where, params, join, params_join, terms, limit)
    # the text column is read long (100000 chars) so a snippet can be cut around the first match; it is shortened below
    cols = "p.post_uid, p.platform, p.posted_at, p.author_name, left(coalesce(p.text,''), 100000) AS text, p.url"
    base = (f"SELECT {cols}, p.views, p.likes, p.comments, p.shares, "
            f"coalesce(p.likes,0)+coalesce(p.comments,0)+coalesce(p.shares,0) AS engagement, {RETWEET_SQL} AS is_retweet{vcols} "
            f"FROM analytics.post_facts p {join}{vjoin} WHERE {' AND '.join(where)}")
    cur = _main_con.cursor()
    try:
        with_views = cur.execute(base + " AND p.views > 0 ORDER BY p.views DESC, p.post_uid LIMIT ?",
                                 params_join + params + [limit]).fetchdf().to_dict(orient="records")
        no_views = cur.execute(base + " AND (p.views IS NULL OR p.views = 0) AND "
                               "(coalesce(p.likes,0)+coalesce(p.comments,0)+coalesce(p.shares,0)) > 0 "
                               "ORDER BY engagement DESC, p.post_uid LIMIT ?",
                               params_join + params + [limit]).fetchdf().to_dict(orient="records")
        total = cur.execute(f"SELECT count(*) FROM analytics.post_facts p {join} WHERE {' AND '.join(where)}",
                            params_join + params).fetchall()[0][0]
    finally:
        cur.close()
    def clean(rows):
        return _clean_posts(rows, terms)

    out = {"total_posts": int(total), "with_views": clean(with_views), "without_views": clean(no_views), "terms": terms}
    try:
        nl = max(0, min(int(qs.get("latest", ["0"])[0]), 50))
    except ValueError:
        nl = 0
    if nl:                      # the newest matching posts, whatever their counters (search page: "what was posted last")
        cur2 = _main_con.cursor()
        try:
            out["latest"] = clean(cur2.execute(base + " AND p.posted_at IS NOT NULL ORDER BY p.posted_at DESC, p.post_uid LIMIT ?",
                                               params_join + params + [nl]).fetchdf().to_dict(orient="records"))
        finally:
            cur2.close()
    return out


ANOM_WINDOW, ANOM_MIN_DAYS, ANOM_MIN_MEAN, ANOM_MIN_V = 28, 14, 0.5, 3   # the F3 gates of the metrics pipeline (docs/METRICS_V2_SPEC.md)


def _search_anomaly(daily, d_from: str, d_to: str) -> dict:
    """Is a search unusual against its own recent past? Same primitives and gates as the metrics pipeline (scripts/lib/stats_v2.py):
    for each selected day a Negative Binomial baseline is fitted on the 28 days BEFORE it (never including it, at least 14 days), on counts
    rescaled to the same collection volume (a day that collected more posts does not look like more interest); surprise = -log10 P(X >= observed);
    Benjamini-Hochberg q-values over the selected days; alert day = q < 0.05. A day gets no verdict (NULL, never 0) with fewer than 3 matching
    posts or a usual level under 0.5 posts a day."""
    import numpy as np
    import pandas as pd
    import stats_v2 as SV
    D = daily.set_index("d")
    start = (pd.Timestamp(d_from) - pd.Timedelta(days=ANOM_WINDOW)).strftime("%Y-%m-%d")
    cal = pd.date_range(start, d_to).strftime("%Y-%m-%d")
    n = D["n"].reindex(cal).fillna(0).to_numpy(float)
    tot = D["total"].reindex(cal).to_numpy(float)
    valid = np.isfinite(tot) & (tot > 0)
    if valid.sum() < ANOM_MIN_DAYS + 1:
        return {"ok": False, "reason": "not enough collected days before the selected range", "rows": []}
    tbar = float(np.mean(tot[valid]))
    scaled = np.where(valid, n * tbar / np.where(valid, tot, 1.0), np.nan)
    idx = [i for i, d in enumerate(cal) if d_from <= d <= d_to]
    rows, ps = [], []
    for i in idx:
        base = scaled[max(0, i - ANOM_WINDOW):i]
        base = base[np.isfinite(base)]
        r = {"day": cal[i], "n": int(n[i]), "expected": None, "ratio": None, "surprise": None, "q": None, "alert": False}
        if valid[i] and base.size >= ANOM_MIN_DAYS and n[i] >= ANOM_MIN_V:
            fit = SV.fit_negbinom(base)
            if fit.ok and fit.mu >= ANOM_MIN_MEAN:
                r["expected"] = round(float(fit.mu * tot[i] / tbar), 2)
                r["ratio"] = round(float(n[i] / max(r["expected"], 1e-9)), 2) if r["expected"] else None
                r["surprise"] = round(float(SV.surprise_from_p(SV.exceedance_p(scaled[i], fit))), 2)
                ps.append((len(rows), SV.exceedance_p(scaled[i], fit, midp=True)))
        rows.append(r)
    if ps:
        qv = SV.bh_qvalues([p for _, p in ps])
        for (k, _), q in zip(ps, qv):
            rows[k]["q"] = round(float(q), 4) if np.isfinite(q) else None
            rows[k]["alert"] = bool(np.isfinite(q) and q < 0.05)
    judged = sum(1 for r in rows if r["surprise"] is not None)
    return {"ok": judged > 0, "reason": None if judged else "too few matching posts a day, or a usual level under 0.5 a day, to judge", "judged_days": judged,
            "alert_days": [r["day"] for r in rows if r["alert"]], "max_ratio": max([r["ratio"] for r in rows if r["alert"] and r["ratio"]] or [None]) if any(r["alert"] for r in rows) else None,
            "window": ANOM_WINDOW, "rows": rows}


def _platform_speed(m, tot_plat, d_from: str, d_to: str) -> list:
    """Per platform: volume in the selected days, when the search first showed up there (first day with at least 3 matching posts, looking back
    up to 28 days before the range: `before` = already active when the range starts), its peak day, and whether it is rising on that platform
    (last 3 days against the 3 before, per collected post of that platform; needs 10 posts in the last 6 days)."""
    import pandas as pd
    back = (pd.Timestamp(d_from) - pd.Timedelta(days=ANOM_WINDOW)).strftime("%Y-%m-%d")
    all_days = pd.date_range(back, d_to).strftime("%Y-%m-%d").tolist()
    days = [d for d in all_days if d >= d_from]
    out = []
    for plat, g in m[m["d"] >= back].groupby("platform"):
        da = g.groupby("d").size().reindex(all_days).fillna(0)
        daily = da[da.index >= d_from]
        if daily.sum() == 0:
            continue
        tt = tot_plat[tot_plat["platform"] == plat].set_index("d")["n"].reindex(days).fillna(0)
        hit = da[da >= 3]
        row = {"platform": plat, "n": int(daily.sum()), "first_day": hit.index[0] if len(hit) else None, "before": bool(len(hit) and hit.index[0] < d_from),
               "peak_day": daily.idxmax(), "peak_n": int(daily.max()), "trend": "few", "ratio": None}
        if len(days) >= 6 and daily.iloc[-6:].sum() >= 10:
            a, b = daily.iloc[-3:].sum(), daily.iloc[-6:-3].sum()
            ta, tb = tt.iloc[-3:].sum(), tt.iloc[-6:-3].sum()
            if b > 0 and ta > 0 and tb > 0:
                ratio = (a / ta) / (b / tb)
                row["ratio"] = round(float(ratio), 2)
                row["trend"] = "rising" if ratio >= 1.25 else ("declining" if ratio <= 0.8 else "steady")
        out.append(row)
    out.sort(key=lambda r: -r["n"])
    return out[:8]


from entity_people import GENERIC_PERSON, clean_candidates as _clean_candidates  # noqa: E402  (scripts/lib is on sys.path)


def _people_involved(cur, tx, etype: str, esub: str, limit: int = 20) -> dict:
    """"Who is involved": the entities of one type (and subtype) named in the matching posts, counted BY NAME IN THE POST TEXT so that every platform counts
    (the production entity extract stops earlier on some platforms). Candidates: the 200 best-known entities of that type / subtype in entity_dim, cleaned by
    `_clean_candidates`, then the 80 most frequent. `tx` = one row per matching post of the two periods (`t` = lower-cased text, `per` = 'cur' | 'prev').
    A name is matched as a whole word; a post counts once per person."""
    import pandas as pd
    where, params = ["entity_type = ?"], [etype]
    if esub:
        where.append("entity_subtype = ?"); params.append(esub)
    cand = cur.execute(f"SELECT entity_uid, canonical_name, entity_type, entity_subtype, distinct_posts FROM geo.entity_dim WHERE {' AND '.join(where)} "
                       "ORDER BY distinct_posts DESC LIMIT 200", params).fetchdf()
    cl = sorted(_clean_candidates(cand), key=lambda x: -x[3])[:80]
    rows = []
    for uid, name, sub, corpus, pats in cl:
        hit = pd.Series(False, index=tx.index)
        for x in dict.fromkeys(pats):
            pre = tx["t"].str.contains(x, regex=False)                    # plain substring first (fast), whole-word check only on those rows
            if pre.any():
                w = re.compile(r"(?<![^\W_])" + re.escape(x) + r"(?![^\W_])")
                hit |= pre & tx["t"].where(pre, "").map(lambda t: bool(w.search(t)))
        n, pv = int((hit & (tx["per"] == "cur")).sum()), int((hit & (tx["per"] == "prev")).sum())
        if n >= 3:
            rows.append({"entity_uid": uid, "name": name, "subtype": sub, "corpus": corpus, "n": n, "n_prev": pv})
    rows.sort(key=lambda x: -x["n"])
    return {"type": etype, "subtype": esub, "method": "name in the post text, all platforms", "candidates": len(cl), "rows": rows[:limit]}


SEARCH_TREND_MIN = 20     # fewer matching posts in the selected days than this: the page says "too few posts to call a trend"
# Keyword search: a topic counts as "about" the typed words only when enough of its posts match.
# Our topics are macro-buckets (10k–234k posts each), so the news-project rule (share-of-topic >= 30%) never fires.
# Instead we use: topic must have >= min_matches posts AND those posts are >= min_kw_share of the keyword's OWN result set.
# Hand-checked on flood / Hormuz / drone / ceasefire / Modi (2024-01-01→2026-10-26):
#   5% cleanly separates the 1–4 genuinely-related topics from the 4–6 minor-mention ones.
KW_TOPIC_MIN_MATCHES = 3    # at least this many keyword-matching posts in the topic
KW_TOPIC_MIN_KW_SHARE = 0.05  # those posts must be >= 5% of ALL keyword-matching posts in the period
KW_THEME_MIN_KW_SHARE = 0.03  # security themes: >= 3% of search results
KW_SUBTOPIC_MIN_SUB_SHARE = 0.15  # fine-grained subtopics: >= 15% of that subtopic's posts in period


def _subcode_names() -> dict[str, str]:
    try:
        sys.path.insert(0, str(EDA_ROOT / "scripts"))
        from lib.taxonomy import build
        tsc = build()["axes"]["theme_subcode"]["entries"]
        return {e["code"]: e.get("display", e["code"]) for e in tsc.values() if "code" in e}
    except Exception:
        return {}


_SUBCODE_NAMES = _subcode_names()


def explore_search_trend(qs: dict) -> dict:
    """Explore > a keyword, entity or theme search: how much it is posted per day, against the previous period and against all posts of the same
    days and platforms (so a collector ramp-up does not look like a surge), where it is posted, and which topics and themes the matching posts fall in.
    Read-only. Day rule = posted day, else scraped day (the one the metrics use). Posts are counted with repeats and, separately, as distinct messages."""
    import numpy as np
    import pandas as pd
    d_from, d_to = qs.get("from", [""])[0], qs.get("to", [""])[0]
    p_from, p_to = qs.get("prev_from", [""])[0], qs.get("prev_to", [""])[0]
    if not (d_from and d_to):
        raise ValueError("from and to required")
    expr = "CAST(coalesce(p.posted_at, p.scraped_at) AS DATE)"
    back = (pd.Timestamp(d_from) - pd.Timedelta(days=ANOM_WINDOW)).strftime("%Y-%m-%d")      # the anomaly baseline needs the 28 days before the range
    lo = min(p_from or d_from, back)
    base_where, base_params = [f"{expr} >= ?", f"{expr} <= ?"], [lo, d_to]
    _platform_clause(qs, base_where, base_params)
    where, params = list(base_where), list(base_params)
    q = qs.get("q", [""])[0].strip()
    terms = []
    if q:
        kq = KQ.parse(q)
        cond, cp = KQ.to_sql(kq, "p.text")
        where.append(cond); params.extend(cp)
        terms = kq.words()
    theme = _theme_clause(qs, where, params)
    ent = qs.get("entity_uid", [""])[0].strip()
    join, pj = "", []
    if ent:
        join = "JOIN (SELECT DISTINCT post_uid FROM geo.entity_mentions WHERE entity_uid = ?) em ON em.post_uid = p.post_uid"
        pj = [ent]
    if not (q or theme or ent):
        raise ValueError("give a keyword, a theme or an entity")
    dd = (f"LEFT JOIN read_parquet('{POST_DEDUP.as_posix()}') dd ON dd.post_uid = p.post_uid" if POST_DEDUP.exists() else "")
    first = "coalesce(dd.is_first, TRUE)" if dd else "TRUE"
    cur = _main_con.cursor()
    try:
        etype = qs.get("entity_type", [""])[0].strip()
        want_text = ", left(lower(coalesce(p.text, '')), 1500) AS t" if etype else ""                 # only the typed "who is involved" search needs the text
        m = cur.execute(f"SELECT p.post_uid, p.platform, {expr} AS d, {first} AS is_first{want_text} FROM analytics.post_facts p {join} {dd} "
                        f"WHERE {' AND '.join(where)}", pj + params).fetchdf()
        tot_plat = cur.execute(f"SELECT {expr} AS d, p.platform AS platform, count(*) AS n FROM analytics.post_facts p WHERE {' AND '.join(base_where)} GROUP BY 1, 2",
                               base_params).fetchdf()
        m["d"] = m["d"].astype(str); tot_plat["d"] = tot_plat["d"].astype(str)
        tot = tot_plat.groupby("d", as_index=False)["n"].sum()
        in_cur = (m["d"] >= d_from) & (m["d"] <= d_to)
        in_prev = (m["d"] >= p_from) & (m["d"] <= p_to) if (p_from and p_to) else pd.Series(False, index=m.index)
        cm = m[in_cur]
        daily = m.groupby("d").agg(n=("post_uid", "size"), distinct=("is_first", "sum")).reset_index()
        daily = daily.merge(tot.rename(columns={"n": "total"}), on="d", how="outer").fillna(0).sort_values("d")
        plat = [{"platform": k, "n_cur": int(v), "n_prev": int(((m["platform"] == k) & in_prev).sum())}
                for k, v in cm.groupby("platform").size().sort_values(ascending=False).items()]
        anomaly = _search_anomaly(daily, d_from, d_to)
        speed = _platform_speed(m, tot_plat, d_from, d_to)
        topics, themes, entities, tags, people = [], [], [], [], None
        mm = m[in_cur | in_prev][["post_uid"]].assign(per=lambda x: "cur")
        mm["per"] = np.where(in_cur[in_cur | in_prev].to_numpy(), "cur", "prev")
        if len(mm):
            cur.register("m_per", mm)
            topics = [{"topic_id": r[0], "n": int(r[1]), "n_prev": int(r[2])} for r in cur.execute(
                "SELECT pt.topic_id, count(*) FILTER (WHERE per = 'cur') AS c, count(*) FILTER (WHERE per = 'prev') AS pv "
                "FROM analytics.post_topic pt JOIN m_per ON m_per.post_uid = pt.post_uid GROUP BY 1 HAVING c > 0 ORDER BY c DESC").fetchall()]
            if POST_THEME.exists():
                sql = (f"SELECT t.theme AS theme, t.tier AS tier, count(*) FILTER (WHERE per = 'cur') AS c, count(*) FILTER (WHERE per = 'prev') AS pv "
                       f"FROM read_parquet('{POST_THEME.as_posix()}') t JOIN m_per ON m_per.post_uid = t.post_uid WHERE t.theme <> 'unassigned' GROUP BY 1, 2")
                if POST_THEME_KNN.exists():      # untagged posts that took a theme from their nearest neighbours (disjoint from the rows above)
                    sql += (f" UNION ALL SELECT k.knn_theme, 'neighbour', count(*) FILTER (WHERE per = 'cur'), count(*) FILTER (WHERE per = 'prev') "
                            f"FROM read_parquet('{POST_THEME_KNN.as_posix()}') k JOIN m_per ON m_per.post_uid = k.post_uid WHERE k.knn_theme <> 'unassigned' GROUP BY 1")
                themes = [{"theme": r[0], "tier": r[1], "n": int(r[2]), "n_prev": int(r[3])} for r in cur.execute(
                    f"SELECT * FROM ({sql}) WHERE c > 0 ORDER BY c DESC").fetchall()]
            # people, organisations, places named in the matching posts (production entity extract; some platforms stop earlier, see coverage)
            entities = [{"entity_uid": r[0], "name": r[1], "type": r[2], "corpus": int(r[3] or 0), "n": int(r[4]), "n_prev": int(r[5])} for r in cur.execute(
                "SELECT e.entity_uid, any_value(d.canonical_name), any_value(d.entity_type), any_value(d.distinct_posts), "
                "count(DISTINCT m_per.post_uid) FILTER (WHERE per = 'cur') AS c, count(DISTINCT m_per.post_uid) FILTER (WHERE per = 'prev') AS pv "
                "FROM geo.entity_mentions e JOIN m_per ON m_per.post_uid = e.post_uid JOIN geo.entity_dim d ON d.entity_uid = e.entity_uid "
                "WHERE e.entity_uid <> ? GROUP BY e.entity_uid HAVING c >= 5 ORDER BY c DESC LIMIT 20", [ent or ""]).fetchall()]
            entities = [e for e in entities if not (e["type"] == "PERSON" and GENERIC_PERSON.match(e["name"] or ""))][:12]
            esub = qs.get("entity_subtype", [""])[0].strip()
            tx = m.loc[in_cur | in_prev, ["t"]].assign(per=np.where(in_cur[in_cur | in_prev].to_numpy(), "cur", "prev")) if "t" in m else None
            if etype and tx is not None and re.fullmatch(r"[A-Z]+", etype) and re.fullmatch(r"[a-z_]*", esub):
                people = _people_involved(cur, tx, etype, esub)
            tags = [{"tag": r[0], "n": int(r[1]), "n_prev": int(r[2])} for r in cur.execute(
                "WITH t AS (SELECT m_per.per AS per, m_per.post_uid AS uid, unnest(list_distinct(regexp_extract_all(lower(coalesce(p.text, '')), '#[\\p{L}\\p{N}_]+'))) AS tag "
                "FROM m_per JOIN analytics.post_facts p ON p.post_uid = m_per.post_uid) "
                "SELECT tag, count(*) FILTER (WHERE per = 'cur') AS c, count(*) FILTER (WHERE per = 'prev') AS pv FROM t "
                "WHERE length(tag) > 2 GROUP BY tag HAVING c >= 5 ORDER BY c DESC LIMIT 12").fetchall()]
            subtopics = []
            if POST_EVENT_TOPIC.exists():
                sql_sub = (f"SELECT s.subcode, "
                           f"count(*) FILTER (WHERE per = 'cur') AS c, "
                           f"count(*) FILTER (WHERE per = 'prev') AS pv "
                           f"FROM read_parquet('{POST_EVENT_TOPIC.as_posix()}') s "
                           f"JOIN m_per ON m_per.post_uid = s.post_uid "
                           f"WHERE s.subcode IS NOT NULL AND s.subcode <> '' "
                           f"GROUP BY 1 HAVING c > 0 ORDER BY c DESC LIMIT 20")
                raw_sub = cur.execute(sql_sub).fetchall()
                if raw_sub:
                    sc_codes = [r[0] for r in raw_sub]
                    tot_sql = (f"SELECT s.subcode, count(*) "
                               f"FROM read_parquet('{POST_EVENT_TOPIC.as_posix()}') s "
                               f"JOIN analytics.post_facts p ON p.post_uid = s.post_uid "
                               f"WHERE {expr} >= ? AND {expr} <= ? AND s.subcode IN ({','.join(['?']*len(sc_codes))}) "
                               f"GROUP BY 1")
                    tot_sub = dict(cur.execute(tot_sql, [d_from, d_to] + sc_codes).fetchall())
                    subtopics = [{
                        "subcode": r[0],
                        "name": _SUBCODE_NAMES.get(r[0], r[0]),
                        "n": int(r[1]),
                        "n_prev": int(r[2]),
                        "n_sub_total": tot_sub.get(r[0], int(r[1])),
                        "n_keyword": int(len(cm)),
                    } for r in raw_sub]
            cur.unregister("m_per")
            if q and topics:       # n_keyword = total matching posts in the period (all topics), so the UI can apply the share gate
                n_keyword = int(len(cm))   # cm is already filtered to the current period
                for t in topics:
                    t["n_keyword"] = n_keyword   # share = t["n"] / n_keyword; pass when >= KW_TOPIC_MIN_KW_SHARE
    finally:
        cur.close()
    return {"q": q, "theme": theme, "entity_uid": ent, "terms": terms, "min_posts": SEARCH_TREND_MIN,
            "topic_rule": {"applies": bool(q), "min_matches": KW_TOPIC_MIN_MATCHES, "min_kw_share": KW_TOPIC_MIN_KW_SHARE},
            "theme_rule": {"applies": bool(q), "min_matches": KW_TOPIC_MIN_MATCHES, "min_kw_share": KW_THEME_MIN_KW_SHARE},
            "subtopic_rule": {"applies": bool(q), "min_matches": KW_TOPIC_MIN_MATCHES, "min_sub_share": KW_SUBTOPIC_MIN_SUB_SHARE},
            "n_cur": int(len(cm)), "n_prev": int(in_prev.sum()), "distinct_cur": int(cm["is_first"].sum()),
            "unassigned_cur": int(len(cm)) - int(sum(t["n"] for t in themes)),
            "entities": entities, "hashtags": tags, "anomaly": anomaly, "platform_speed": speed, "people": people, "entity_coverage": _entity_cover(),
            "daily": [{"day": r.d, "n": int(r.n), "distinct": int(r.distinct), "total": int(r.total)} for r in daily.itertuples()],
            "platforms": plat, "topics": topics, "themes": themes, "subtopics": subtopics}


def explore_scenario_people(qs: dict) -> dict:
    """Explore > a scenario searched with an entity type chosen: who is actually involved and the posts about their part in it (meaning first, then
    Gemma verification; see scripts/lib/scenario_search.py). Slow the first time (about 40 s: Gemma brief, retrieval, 18 verification calls), cached after."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from lib import scenario_search as SS
    scenario = qs.get("scenario", [""])[0].strip()
    d_from, d_to = qs.get("from", [""])[0], qs.get("to", [""])[0]
    etype = qs.get("entity_type", [""])[0].strip()
    esub = qs.get("entity_subtype", [""])[0].strip()
    if not scenario or len(scenario) > 120:
        raise ValueError("give a scenario of at most 120 characters")
    if not (d_from and d_to):
        raise ValueError("from and to required")
    if not re.fullmatch(r"[A-Z]+", etype) or not re.fullmatch(r"[a-z_]*", esub):
        raise ValueError("entity_type must be like PERSON and entity_subtype like political_leader")
    plats = [x for x in qs.get("platforms", [""])[0].split(",") if x]
    cur = _main_con.cursor()
    try:
        return SS.run(cur, scenario, d_from, d_to, plats or None, etype, esub, refresh=qs.get("refresh", ["0"])[0] == "1")
    finally:
        cur.close()


_ENT_COVER = None


def _entity_cover() -> list:
    """Per platform: the last day that has any entity mention, and how many posts have one. The production entity extract is
    taken at one moment, so some platforms stop earlier than the posts do; the page says so instead of showing a silent gap."""
    global _ENT_COVER
    if _ENT_COVER is None:
        cur = _main_con.cursor()
        try:
            rows = cur.execute(
                "SELECT p.platform, CAST(max(coalesce(p.posted_at, p.scraped_at)) AS DATE) AS last_day, count(DISTINCT m.post_uid) AS n "
                "FROM geo.entity_mentions m JOIN analytics.post_facts p ON p.post_uid = m.post_uid GROUP BY p.platform").fetchall()
        finally:
            cur.close()
        _ENT_COVER = [{"platform": r[0], "last_day": str(r[1]) if r[1] else None, "posts": int(r[2])} for r in rows]
    return _ENT_COVER


def explore_entity_topics(qs: dict) -> dict:
    """Explore > Trending Topics with an entity chosen: for each topic, how many posts name the entity in the
    selected days (`from`..`to`) and in the period just before (`prev_from`..`prev_to`, sent by the page so both
    sides use the same day list). Read-only. Posts only: YouTube comments carry no entity mentions.
    The day rule is the one the metrics use (posted day, else scraped day)."""
    ent = qs.get("entity_uid", [""])[0].strip()
    if not ent:
        raise ValueError("entity_uid required")
    d_from, d_to = qs.get("from", [""])[0], qs.get("to", [""])[0]
    p_from, p_to = qs.get("prev_from", [""])[0], qs.get("prev_to", [""])[0]
    if not (d_from and d_to):
        raise ValueError("from and to required")
    expr = "CAST(coalesce(p.posted_at, p.scraped_at) AS DATE)"
    lo = p_from or d_from
    where, params = [f"{expr} >= ?", f"{expr} <= ?"], [lo, d_to]
    _platform_clause(qs, where, params)
    cur_sql = f"{expr} >= ? AND {expr} <= ?"
    prev_sql = f"{expr} >= ? AND {expr} <= ?" if (p_from and p_to) else "FALSE"
    sql = (f"SELECT pt.topic_id, "
           f"count(*) FILTER (WHERE {cur_sql}) AS n_cur, "
           f"count(*) FILTER (WHERE {prev_sql}) AS n_prev "
           f"FROM analytics.post_topic pt JOIN analytics.post_facts p ON p.post_uid = pt.post_uid "
           f"JOIN (SELECT DISTINCT post_uid FROM geo.entity_mentions WHERE entity_uid = ?) em ON em.post_uid = p.post_uid "
           f"WHERE {' AND '.join(where)} GROUP BY pt.topic_id")
    sel = [d_from, d_to] + ([p_from, p_to] if (p_from and p_to) else []) + [ent] + params
    cur = _main_con.cursor()
    try:
        rows = cur.execute(sql, sel).fetchall()
    finally:
        cur.close()
    return {"entity_uid": ent, "coverage": _entity_cover(),
            "rows": [{"topic_id": r[0], "n_cur": int(r[1]), "n_prev": int(r[2])} for r in rows if r[1] or r[2]]}


def compute_related(cur, view: str, qs: dict, seed_post_uid):
    sel_sql, sel_params = build_query(view, qs)
    # Deterministic scope sample. `SELECT DISTINCT ... LIMIT n` discards the
    # inner ORDER BY, so the previous form returned a different arbitrary set
    # on every call -- four identical requests gave four different payloads.
    # hash() ordering is the idiom this project already uses instead of
    # USING SAMPLE (DuckDB reservoir sampling misbehaves with WHERE).
    sel_cte = (
        f"WITH sel AS (SELECT DISTINCT post_uid FROM ({sel_sql}) t "
        f"ORDER BY hash(post_uid) LIMIT {SEL_SCOPE_LIMIT}), "
        f"scope AS (SELECT count(*) AS n FROM sel)"
    )
    excl_entity, excl_cat_dim, excl_cat_key, excl_cluster, (excl_axis, excl_code) = \
        _self_exclusions(view, qs)
    # The viewed key may arrive as either form; resolve once so the exclusion can
    # match code_key exactly and nest on the numeric code.
    excl_code_key, excl_num = _resolve_code(excl_code) if excl_code else ("", "")

    corpus_n = cur.execute("SELECT count(*) FROM analytics.post_facts").fetchone()[0] or 1

    # lift = P(candidate | in scope) / P(candidate | corpus)
    #      = (co_posts / scope_n) / (global_posts / corpus_n)
    # score = lift * co_posts/(co_posts + K)   -- shrink small-sample lift spikes
    entities_df = cur.execute(f"""
        {sel_cte}
        SELECT em.entity_uid,
               e.canonical_name AS name,
               count(DISTINCT em.post_uid) AS co_posts,
               e.distinct_posts AS global_posts,
               ((count(DISTINCT em.post_uid)::DOUBLE / (SELECT n FROM scope))
                 / (e.distinct_posts::DOUBLE / {corpus_n}))
                 * (count(DISTINCT em.post_uid)::DOUBLE
                    / (count(DISTINCT em.post_uid) + {SHRINK_K})) AS score
        FROM sel s
        JOIN geo.entity_mentions em ON em.post_uid = s.post_uid
        JOIN geo.entity_dim e ON e.entity_uid = em.entity_uid
        WHERE em.entity_uid != ?
          AND e.distinct_posts >= {MIN_GLOBAL_POSTS}
          AND e.canonical_name NOT LIKE '%##%'
          AND length(e.canonical_name) > 2
        GROUP BY 1, 2, e.distinct_posts
        HAVING count(DISTINCT em.post_uid) >= {MIN_CO_POSTS}
        ORDER BY score DESC, co_posts DESC, em.entity_uid
        LIMIT {RELATED_LIMIT}
    """, sel_params + [excl_entity]).fetchdf()

    # Axis union. Legacy axes carry axis='' so the hierarchical exclusion below
    # can never fire on them; CAMEO axes are added only once the coding pass has
    # actually written the table, so a half-built pipeline degrades to the
    # pre-CAMEO related list instead of erroring.
    # `key` is what the UI labels and routes by (code_key for CAMEO, category for
    # legacy); `num` is the numeric CAMEO code, carried only so the hierarchical
    # exclusion below can nest -- code_key does not nest lexically.
    co_ctes = [
        "risk_co AS (SELECT 'risk' AS dim, '' AS axis, rl.category AS key, '' AS num, "
        "count(DISTINCT rl.post_uid) AS co_posts FROM sel s "
        "JOIN geo.risk_labels rl ON rl.post_uid = s.post_uid GROUP BY 1, 2, 3, 4)",
        "theme_co AS (SELECT 'theme' AS dim, '' AS axis, tl.category AS key, '' AS num, "
        "count(DISTINCT tl.post_uid) AS co_posts FROM sel s "
        "JOIN geo.theme_labels tl ON tl.post_uid = s.post_uid GROUP BY 1, 2, 3, 4)",
    ]
    total_parts = [
        "SELECT 'risk' AS dim, category AS key, count(DISTINCT post_uid) AS total "
        "FROM geo.risk_labels GROUP BY 1, 2",
        "SELECT 'theme' AS dim, category AS key, count(DISTINCT post_uid) AS total "
        "FROM geo.theme_labels GROUP BY 1, 2",
    ]
    if _has_table("event_codes"):
        for dim, (axis, lvl) in CAMEO_RELATED_AXES.items():
            lvl_co = "" if lvl is None else f" AND ec.level = {int(lvl)}"
            lvl_tot = "" if lvl is None else f" AND level = {int(lvl)}"
            co_ctes.append(
                f"{dim}_co AS (SELECT '{dim}' AS dim, '{axis}' AS axis, "
                f"ec.code_key AS key, ec.code AS num, "
                f"count(DISTINCT ec.post_uid) AS co_posts FROM sel s "
                f"JOIN geo.event_codes ec ON ec.post_uid = s.post_uid "
                f"WHERE ec.axis = '{axis}'{lvl_co} GROUP BY 1, 2, 3, 4)")
            total_parts.append(
                f"SELECT '{dim}' AS dim, code_key AS key, count(DISTINCT post_uid) AS total "
                f"FROM geo.event_codes WHERE axis = '{axis}'{lvl_tot} GROUP BY 1, 2")
    union_co = " UNION ALL ".join(f"SELECT * FROM {c.split(' AS ')[0]}" for c in co_ctes)
    per_axis = _per_axis_cap(len(co_ctes))

    categories_df = cur.execute(f"""
        {sel_cte},
        {', '.join(co_ctes)},
        totals AS ({' UNION ALL '.join(total_parts)}),
        scored AS (
            SELECT c.dim AS dim_type, c.key AS category, c.co_posts,
                   t.total AS global_posts,
                   ((c.co_posts::DOUBLE / (SELECT n FROM scope)) / (t.total::DOUBLE / {corpus_n}))
                     * (c.co_posts::DOUBLE / (c.co_posts + {SHRINK_K})) AS score
            FROM ({union_co}) c
            JOIN totals t ON t.dim = c.dim AND t.key = c.key
            WHERE NOT (c.dim = ? AND c.key = ?)
              AND NOT (c.axis = ? AND (c.key = ?
                                       OR (c.num <> '' AND ? <> ''
                                           AND (? LIKE c.num || '.%'
                                                OR c.num LIKE ? || '.%'))))
              AND c.co_posts >= {MIN_CO_POSTS}
        ),
        ranked AS (
            SELECT *, row_number() OVER (PARTITION BY dim_type
                                         ORDER BY score DESC, co_posts DESC, category) AS rn
            FROM scored
        )
        SELECT dim_type, category, co_posts, global_posts, score
        FROM ranked
        WHERE rn <= {per_axis}
        ORDER BY score DESC, co_posts DESC, dim_type, category
        LIMIT {RELATED_LIMIT}
    """, sel_params + [excl_cat_dim, excl_cat_key,
                       excl_axis, excl_code_key,
                       excl_num, excl_num, excl_num]).fetchdf()

    topics_df = cur.execute(f"""
        {sel_cte}
        SELECT pt.cluster_id, td.topic_id, td.name,
               count(DISTINCT pt.post_uid) AS co_posts,
               td.size AS global_posts,
               ((count(DISTINCT pt.post_uid)::DOUBLE / (SELECT n FROM scope))
                 / (td.size::DOUBLE / {corpus_n}))
                 * (count(DISTINCT pt.post_uid)::DOUBLE
                    / (count(DISTINCT pt.post_uid) + {SHRINK_K})) AS score
        FROM sel s
        JOIN geo.{TOPIC_POST} pt ON pt.post_uid = s.post_uid
        JOIN geo.{TOPIC_DIM} td ON td.cluster_id = pt.cluster_id
        WHERE pt.cluster_id != ? AND pt.cluster_id >= 0 AND td.coherent
        GROUP BY 1, 2, 3, td.size
        HAVING count(DISTINCT pt.post_uid) >= {MIN_CO_POSTS}
        ORDER BY score DESC, co_posts DESC, pt.cluster_id
        LIMIT {RELATED_LIMIT}
    """, sel_params + [excl_cluster]).fetchdf()

    similar_records = []
    if seed_post_uid:
        has_seed = cur.execute(
            "SELECT count(*) FROM analytics.post_embeddings WHERE post_uid = ?", [seed_post_uid]
        ).fetchone()[0]
        if has_seed:
            # Dedup by normalised text before taking the top N. The corpus is
            # heavily templated (repeated air-raid alerts, 125 identical
            # LinkedIn 404 bodies), so without this every slot comes back as a
            # byte-identical copy at similarity 1.000000 -- measured, not
            # hypothetical. Keep the best-scoring post per distinct text.
            similar_records = cur.execute(f"""
                {sel_cte},
                cand AS (
                    SELECT pe.post_uid, p.platform, p.posted_at, p.author_name,
                           left(coalesce(p.text,''), 500) AS text, p.url,
                           list_cosine_similarity(pe.embedding,
                               (SELECT embedding FROM analytics.post_embeddings WHERE post_uid = ?)) AS similarity,
                           md5(lower(regexp_replace(coalesce(p.text,''), '[^[:alnum:]]', '', 'g'))) AS text_key
                    FROM analytics.post_embeddings pe
                    JOIN analytics.post_facts p ON p.post_uid = pe.post_uid
                    WHERE pe.post_uid NOT IN (SELECT post_uid FROM sel) AND pe.post_uid != ?
                ),
                ranked AS (
                    SELECT *, row_number() OVER (
                        PARTITION BY text_key ORDER BY similarity DESC, post_uid
                    ) AS rn
                    FROM cand
                )
                SELECT post_uid, platform, posted_at, author_name, text, url, similarity
                FROM ranked WHERE rn = 1
                ORDER BY similarity DESC, post_uid
                LIMIT {SIMILAR_LIMIT}
            """, sel_params + [seed_post_uid, seed_post_uid]).fetchdf().to_dict(orient="records")

    return {
        "entities": entities_df.rename(columns={"canonical_name": "name"}).to_dict(orient="records"),
        "categories": categories_df.to_dict(orient="records"),
        "topics": topics_df.to_dict(orient="records"),
        "similar_posts": similar_records,
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(HERE), **kwargs)

    def log_message(self, fmt, *args):
        pass  # quiet; avoid growing a log file for a local dev server

    def send_header(self, keyword, value):
        if keyword.lower() == "cache-control":
            self._cc_sent = True
        super().send_header(keyword, value)

    def end_headers(self):
        # Static files (index.html, assets/*.js, *.css) carried only Last-Modified, so a browser may keep using a copy it
        # fetched earlier and not show a rebuild. "no-cache" = ask the server every time; an unchanged file costs a 304.
        if not getattr(self, "_cc_sent", False):
            super().send_header("Cache-Control", "no-cache")
        self._cc_sent = False
        super().end_headers()

    def _json(self, obj, status=200):
        # NaN / Infinity are not valid JSON: a browser's JSON.parse rejects the whole response, so
        # missing numbers (for example a platform with no view counter) are sent as null.
        body = json.dumps(_json_safe(obj), default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/feedback":
            self._json({"error": "not found"}, status=404)
            return
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if n <= 0 or n > 4096:
                raise FeedbackError("body must be 1 to 4096 bytes of JSON")
            body = json.loads(self.rfile.read(n).decode("utf-8"))
            self._json(FEEDBACK.vote(body))
        except (FeedbackError, json.JSONDecodeError, UnicodeDecodeError) as e:
            self._json({"error": str(e)}, status=400)
        except Exception as e:
            self._json({"error": f"could not save: {e}"}, status=500)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._json({"ok": True})
            return
        if parsed.path == "/data/metrics.json" and "gzip" in self.headers.get("Accept-Encoding", ""):
            gz_file = HERE / "data" / "metrics.json.gz"
            if gz_file.exists():
                st = gz_file.stat()
                etag = '"%x-%x"' % (st.st_mtime_ns, st.st_size)
                if self.headers.get("If-None-Match") == etag:        # unchanged since the browser last fetched it
                    self.send_response(304)
                    self.send_header("ETag", etag)
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    return
                content = gz_file.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(content)
                return
        if parsed.path == "/api/gemma/status":
            try:
                self._json(gemma_status())
            except Exception as e:  # never 500
                self._json({"available": False, "error": f"{type(e).__name__}: {e}"})
            return
        if parsed.path == "/api/live/overview":
            qs = parse_qs(parsed.query)
            try:
                days = int(qs.get("days", ["7"])[0])
            except ValueError:
                days = 7
            raw = qs.get("domains", [""])[0]
            doms = tuple(d for d in raw.split(",") if d) or DEFAULT_MILITARY
            self._json(live_overview(days, doms))
            return
        if parsed.path == "/api/explore/entity_topics":
            try:
                self._json(explore_entity_topics(parse_qs(parsed.query)))
            except ValueError as e:
                self._json({"error": str(e)}, status=400)
            except Exception as e:
                self._json({"error": f"query failed: {e}"}, status=500)
            return
        if parsed.path == "/api/explore/scenario_people":
            try:
                self._json(explore_scenario_people(parse_qs(parsed.query)))
            except ValueError as e:
                self._json({"error": str(e)}, status=400)
            except Exception as e:
                self._json({"error": f"scenario search failed: {type(e).__name__}: {e}"}, status=503)
            return
        if parsed.path == "/api/explore/search_trend":
            try:
                self._json(explore_search_trend(parse_qs(parsed.query)))
            except (ValueError, KQ.KeywordError) as e:
                self._json({"error": str(e)}, status=400)
            except Exception as e:
                self._json({"error": f"query failed: {e}"}, status=500)
            return
        if parsed.path == "/api/explore/viral":
            try:
                self._json(explore_viral(parse_qs(parsed.query)))
            except KQ.KeywordError as e:
                self._json({"error": str(e)}, status=400)
            except Exception as e:
                self._json({"error": f"query failed: {e}"}, status=500)
            return
        if parsed.path == "/api/explore/audience":
            cur = _main_con.cursor()
            try:
                self._json(AUD.explore_audience(parse_qs(parsed.query), cur))
            except ValueError as e:
                self._json({"error": str(e)}, status=400)
            except AUD.AudienceUnavailable as e:
                self._json({"error": str(e), "unavailable": True}, status=503)
            except Exception as e:
                self._json({"error": f"query failed: {type(e).__name__}: {e}"}, status=500)
            finally:
                cur.close()
            return
        if parsed.path == "/api/feedback/votes":
            q = parse_qs(parsed.query)
            self._json({"votes": FEEDBACK.votes_for(q.get("user", [""])[0], q.get("type", [""])[0],
                                                    [x for x in q.get("ids", [""])[0].split(",") if x], q.get("day", [""])[0])})
            return
        if parsed.path == "/api/feedback/summary":
            self._json(FEEDBACK.summary())
            return
        if parsed.path == "/api/drill":
            qs = parse_qs(parsed.query)
            view = qs.get("view", [None])[0]
            limit = _limit(qs)
            try:
                sql, params = build_query(view, qs)
                cur = _main_con.cursor()
                try:
                    try:
                        offset = max(0, int(qs.get("offset", [0])[0]))
                    except (ValueError, TypeError):
                        offset = 0
                    rows = cur.execute(sql + " LIMIT ? OFFSET ?", params + [limit, offset]).fetchdf()
                    total = int(rows["total_rows"].iloc[0]) if len(rows) else 0
                    cols = [c for c in rows.columns if c != "total_rows"]
                    records = rows[cols].to_dict(orient="records")
                    related = None
                    # a ranked-posts drill (sort=...) is a list of the top posts, not an exploration of
                    # related items; skipping the related block avoids a ~8 s scan of all embeddings
                    if (view not in ("cross_platform", "cross_platform_url", "cross_platform_event")
                            and not _sort_key(qs)):
                        try:
                            seed = min((r["post_uid"] for r in records), default=None) if records else None
                            related = compute_related(cur, view, qs, seed)
                        except Exception as e:
                            related = None
                            print(f"WARN: compute_related failed for view={view!r} qs={dict(qs)!r}: "
                                  f"{type(e).__name__}: {e}", file=sys.stderr, flush=True)
                finally:
                    cur.close()
                resp = {"view": view, "shown": len(records), "total": total, "offset": offset, "rows": records}
                if related is not None:
                    resp["related"] = related
                self._json(resp)
            except NotYetPopulated as e:
                self._json({"error": str(e), "not_yet_populated": True}, status=503)
            except ValueError as e:
                self._json({"error": str(e)}, status=400)
            except Exception as e:
                self._json({"error": f"query failed: {e}"}, status=500)
            return
        super().do_GET()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8877
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Serving {HERE} + /api/drill on http://0.0.0.0:{port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
