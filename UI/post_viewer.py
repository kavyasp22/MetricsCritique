"""
post_viewer.py — Full, authentic Post Viewer integration.
Uses the exact original Jinja2 template from post-read-db, preserving:
- Platform dropdown selector with live post counts
- Language, Handles, Keyword, and Date Range filters
- Theme toggle (Light / Dark mode) & DB row counts Refresh button
- Pagination, full-text toggle, translations, and inline translation editing
"""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from urllib.parse import parse_qs

from jinja2 import Environment, FileSystemLoader
import psycopg2
import psycopg2.extras

from platform_config import PLATFORMS

HERE = Path(__file__).resolve().parent
TEMPLATES_DIR = HERE / "pv_templates"
AUDIT_FILE = HERE / "audit_results.json"

# ── DB config ──────────────────────────────────────────────────────────────
_DB = {
    "host":            os.getenv("PV_DB_HOST",     "10.10.180.241"),
    "port":            int(os.getenv("PV_DB_PORT", "13366")),
    "dbname":          os.getenv("PV_DB_NAME",     "shaktidb_social"),
    "user":            os.getenv("PV_DB_USER",     "social-media"),
    "password":        os.getenv("PV_DB_PASSWORD", "social-media"),
    "connect_timeout": int(os.getenv("PV_DB_CONNECT_TIMEOUT", "10")),
}

PAGE_SIZE = int(os.getenv("PAGE_SIZE", "50"))

LANGUAGE_MAP = {
    'af': 'Afrikaans', 'am': 'Amharic', 'ar': 'Arabic', 'as': 'Assamese', 'az': 'Azerbaijani',
    'bal': 'Balochi', 'be': 'Belarusian', 'bg': 'Bulgarian', 'bn': 'Bengali', 'bo': 'Tibetan',
    'bs': 'Bosnian', 'ca': 'Catalan', 'ceb': 'Cebuano', 'chinese': 'Chinese', 'cs': 'Czech',
    'cy': 'Welsh', 'da': 'Danish', 'de': 'German', 'dh': 'Dhivehi', 'dhiv': 'Dhivehi',
    'dhivethi': 'Dhivehi', 'dv': 'Dhivehi', 'el': 'Greek', 'en': 'English', 'english': 'English',
    'es': 'Spanish', 'et': 'Estonian', 'eu': 'Basque', 'fa': 'Persian', 'fi': 'Finnish',
    'fr': 'French', 'french': 'French', 'german': 'German', 'gu': 'Gujarati', 'he': 'Hebrew',
    'hi': 'Hindi', 'hindi': 'Hindi', 'hinglish': 'Hinglish', 'hr': 'Croatian', 'ht': 'Haitian Creole',
    'hu': 'Hungarian', 'id': 'Indonesian', 'in': 'Indonesian', 'indonesian': 'Indonesian',
    'is': 'Icelandic', 'it': 'Italian', 'ja': 'Japanese', 'jv': 'Javanese', 'km': 'Khmer',
    'kn': 'Kannada', 'ko': 'Korean', 'ku': 'Kurdish', 'ky': 'Kyrgyz', 'kyr': 'Kyrgyz',
    'la': 'Latin', 'lo': 'Lao', 'lt': 'Lithuanian', 'lv': 'Latvian', 'malagasy': 'Malagasy',
    'malay': 'Malay', 'malayalam': 'Malayalam', 'maldivian': 'Dhivehi', 'marathi': 'Marathi',
    'mk': 'Macedonian', 'ml': 'Malayalam', 'mn': 'Mongolian', 'mni': 'Manipuri', 'mr': 'Marathi',
    'ms': 'Malay', 'my': 'Burmese', 'ne': 'Nepali', 'nl': 'Dutch', 'no': 'Norwegian',
    'or': 'Odia', 'pa': 'Punjabi', 'pl': 'Polish', 'pnb': 'Western Punjabi', 'ps': 'Pashto',
    'pt': 'Portuguese', 'portuguese': 'Portuguese', 'ro': 'Romanian', 'ru': 'Russian',
    'si': 'Sinhala', 'sinhala': 'Sinhala', 'sk': 'Slovak', 'sl': 'Slovenian', 'so': 'Somali',
    'sq': 'Albanian', 'sr': 'Serbian', 'serbian': 'Serbian', 'sv': 'Swedish', 'sw': 'Swahili',
    'swahili': 'Swahili', 'ta': 'Tamil', 'te': 'Telugu', 'th': 'Thai', 'tl': 'Tagalog',
    'tagalog': 'Tagalog', 'tr': 'Turkish', 'turkish': 'Turkish', 'uk': 'Ukrainian', 'ur': 'Urdu',
    'vi': 'Vietnamese', 'yo': 'Yoruba', 'zh': 'Chinese', 'zh-cn': 'Chinese (Simplified)',
    'und': 'Undetermined', 'unknown': 'Unknown',
}

IGNORED_LANGS = {
    'art', 'und', 'unknown', 'zxx', 'mis', 'mix', 'qam', 'qht', 'qme', 'qst', 'multilingual'
}

def lang_name_filter(code):
    if not code:
        return ''
    c = str(code).lower().strip()
    if c in IGNORED_LANGS:
        return ''
    return LANGUAGE_MAP.get(c, code.title() if len(code) > 3 else code.upper())

_seen = set()
ALL_LANGUAGES_SORTED = [
    (code, name) for code, name in sorted(LANGUAGE_MAP.items(), key=lambda x: x[1])
    if code not in IGNORED_LANGS and len(code) <= 5 and not code.isdigit() and not (code in _seen or _seen.add(code))
]

ROW_COUNTS = {}
OPTIONS_CACHE = {}
LANGS_CACHE = {}
OPTIONS_TTL = 3600  # 1 hour cache for handles and keywords
LANGS_TTL = 3600    # 1 hour cache for languages

# Setup Jinja2 environment
_jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
_jinja_env.filters['lang_name'] = lang_name_filter


def _get_conn():
    return psycopg2.connect(**_DB)


def fetch_rows(query, params=None):
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            return cur.fetchall()
    finally:
        conn.close()


def fetch_one(query, params=None):
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            return cur.fetchone()
    finally:
        conn.close()


def execute_write(query, params=None):
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rowcount = cur.rowcount
        conn.commit()
        return rowcount
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_platform_options(table, cfg, force=False):
    now = time.time()
    if not force and table in OPTIONS_CACHE:
        cached_data, cached_ts = OPTIONS_CACHE[table]
        if now - cached_ts < OPTIONS_TTL:
            return cached_data

    handles = []
    keywords = []

    author_col = cfg.get("author_col")
    if author_col:
        try:
            sql = f"""
                SELECT {author_col} as val, COUNT(*) as cnt
                FROM (SELECT {author_col} FROM {table} WHERE {author_col} IS NOT NULL AND {author_col} != '' LIMIT 25000) sub
                GROUP BY {author_col}
                HAVING COUNT(*) >= 2
                ORDER BY cnt DESC
                LIMIT 250
            """
            rows = fetch_rows(sql)
            if len(rows) < 10:
                sql_fallback = f"""
                    SELECT {author_col} as val, COUNT(*) as cnt
                    FROM (SELECT {author_col} FROM {table} WHERE {author_col} IS NOT NULL AND {author_col} != '' LIMIT 15000) sub
                    GROUP BY {author_col}
                    ORDER BY cnt DESC
                    LIMIT 200
                """
                rows = fetch_rows(sql_fallback)

            raw_handles = [{"val": r["val"], "cnt": r["cnt"]} for r in rows if r["val"] and str(r["val"]).strip()]
            handles = sorted(
                raw_handles,
                key=lambda x: (
                    0 if str(x["val"]).strip().lstrip("@_#") and str(x["val"]).strip().lstrip("@_#")[0].isalpha() else 1,
                    str(x["val"]).strip().lstrip("@_#").lower()
                )
            )
        except Exception as e:
            print(f"Error fetching handles for {table}: {e}")

    kw_col = cfg.get("keyword_col")
    if kw_col:
        try:
            sql = f"""
                SELECT {kw_col} as val, COUNT(*) as cnt
                FROM (SELECT {kw_col} FROM {table} WHERE {kw_col} IS NOT NULL AND {kw_col} != '' LIMIT 20000) sub
                GROUP BY {kw_col}
                ORDER BY cnt DESC
                LIMIT 100
            """
            rows = fetch_rows(sql)
            raw_keywords = [{"val": r["val"], "cnt": r["cnt"]} for r in rows if r["val"] and str(r["val"]).strip()]
            keywords = sorted(
                raw_keywords,
                key=lambda x: (
                    0 if str(x["val"]).strip().lstrip("@_#") and str(x["val"]).strip().lstrip("@_#")[0].isalpha() else 1,
                    str(x["val"]).strip().lstrip("@_#").lower()
                )
            )
        except Exception as e:
            print(f"Error fetching keywords for {table}: {e}")

    OPTIONS_CACHE[table] = ({"handles": handles, "keywords": keywords}, now)
    return OPTIONS_CACHE[table][0]


def get_row_counts(force=False):
    global ROW_COUNTS
    if ROW_COUNTS and not force:
        return ROW_COUNTS

    if not force and AUDIT_FILE.exists():
        try:
            with open(AUDIT_FILE, "r") as f:
                ROW_COUNTS = json.load(f)
                return ROW_COUNTS
        except Exception:
            pass

    counts = {}
    for t in PLATFORMS:
        try:
            row = fetch_one(f'SELECT COUNT(*) as cnt FROM public."{t}"')
            counts[t] = row["cnt"] if row else 0
        except Exception:
            counts[t] = 0

    ROW_COUNTS = counts
    try:
        with open(AUDIT_FILE, "w") as f:
            json.dump(ROW_COUNTS, f, indent=2)
    except Exception:
        pass

    return ROW_COUNTS


def render_platform_view(table: str, qs: dict) -> tuple[int, str]:
    """Renders the exact index.html template from post-read-db."""
    if table not in PLATFORMS:
        table = "twitter_posts"

    cfg = PLATFORMS[table]
    counts = get_row_counts()
    page = int(qs.get("page", ["1"])[0] or 1)
    search = (qs.get("search", [""])[0] or "").strip()
    handle = (qs.get("handle", [""])[0] or "").strip()
    keyword = (qs.get("keyword", [""])[0] or "").strip()
    lang_filter = (qs.get("lang", [""])[0] or "").strip()
    from_date = (qs.get("from_date", [""])[0] or "").strip()
    to_date = (qs.get("to_date", [""])[0] or "").strip()
    offset = (page - 1) * PAGE_SIZE

    conditions = []
    params = []

    if search:
        text_conditions = []
        for col in cfg["text_cols"]:
            text_conditions.append(f"{col} ILIKE %s")
            params.append(f"%{search}%")
        conditions.append("(" + " OR ".join(text_conditions) + ")")

    if handle and cfg.get("author_col"):
        conditions.append(f"{cfg['author_col']} ILIKE %s")
        params.append(f"%{handle}%")

    if keyword and cfg.get("keyword_col"):
        kw_col = cfg["keyword_col"]
        if table == "reddit_posts":
            conditions.append(f"({kw_col} ILIKE %s OR subreddit ILIKE %s)")
            params.extend([f"%{keyword}%", f"%{keyword}%"])
        elif table == "snapchat_posts":
            conditions.append(f"({kw_col} ILIKE %s OR search_term ILIKE %s)")
            params.extend([f"%{keyword}%", f"%{keyword}%"])
        elif table == "discord_posts":
            conditions.append(f"({kw_col} ILIKE %s OR server_name ILIKE %s OR channel_name ILIKE %s)")
            params.extend([f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"])
        else:
            conditions.append(f"{kw_col} ILIKE %s")
            params.append(f"%{keyword}%")

    if lang_filter:
        conditions.append("(language = %s OR detected_language = %s)")
        params.extend([lang_filter, lang_filter])

    time_col = cfg.get("time_col")
    if from_date and time_col:
        conditions.append(f"{time_col} >= %s")
        params.append(f"{from_date} 00:00:00")

    if to_date and time_col:
        conditions.append(f"{time_col} <= %s")
        params.append(f"{to_date} 23:59:59")

    where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    if not conditions:
        total_rows = counts.get(table, 0)
    else:
        count_sql = f"SELECT COUNT(*) as cnt FROM {table} {where_sql}"
        cnt_res = fetch_one(count_sql, params)
        total_rows = cnt_res["cnt"] if cnt_res else 0

    total_pages = max(1, math.ceil(total_rows / PAGE_SIZE))

    id_col = cfg["id_col"]
    author_col = cfg["author_col"]
    url_col = cfg["url_col"]
    text_cols = cfg["text_cols"]
    translated_cols = cfg["translated_cols"]
    extra_cols = cfg["extra_cols"]

    select_parts = [
        f"{id_col} as _id",
        f"{author_col} as _author",
        f"language",
        f"detected_language",
    ]
    if time_col:
        select_parts.append(f"{time_col} as _time")
    if url_col:
        select_parts.append(f"{url_col} as _url")
    for i, c in enumerate(text_cols):
        select_parts.append(f"{c} as _text_{i}")
    for i, c in enumerate(translated_cols):
        select_parts.append(f"{c} as _translated_{i}")
    for c in extra_cols:
        select_parts.append(f"{c}")

    select_sql = ", ".join(select_parts)
    order_sql = f"ORDER BY {time_col} DESC NULLS LAST" if time_col else f"ORDER BY {id_col} DESC"

    data_sql = f"""
        SELECT {select_sql}
        FROM {table}
        {where_sql}
        {order_sql}
        LIMIT {PAGE_SIZE} OFFSET {offset}
    """

    posts = fetch_rows(data_sql, params if params else None)

    # Distinct languages mapped to full names (cached with 1hr TTL)
    langs = []
    now = time.time()
    if table in LANGS_CACHE and (now - LANGS_CACHE[table][1]) < LANGS_TTL:
        langs = LANGS_CACHE[table][0]
    else:
        try:
            lang_sql = f"""
                SELECT DISTINCT COALESCE(language, detected_language) as lang
                FROM (SELECT language, detected_language FROM {table} LIMIT 5000) sub
                WHERE COALESCE(language, detected_language) IS NOT NULL AND COALESCE(language, detected_language) != ''
                ORDER BY 1
            """
            raw_langs = [r["lang"] for r in fetch_rows(lang_sql) if r["lang"]]
            langs_list = []
            for code in raw_langs:
                c = str(code).lower().strip()
                if c in IGNORED_LANGS:
                    continue
                name = LANGUAGE_MAP.get(c, code.title() if len(code) > 3 else code.upper())
                langs_list.append({"code": code, "name": name})
            langs = sorted(langs_list, key=lambda x: x["name"].lower())
            LANGS_CACHE[table] = (langs, now)
        except Exception:
            langs = []

    platform_opts = get_platform_options(table, cfg)
    handles = platform_opts["handles"]
    keywords = platform_opts["keywords"]
    total_all = sum(counts.values())

    template = _jinja_env.get_template("index.html")
    html_output = template.render(
        platforms=PLATFORMS,
        counts=counts,
        total=total_all,
        active=table,
        cfg=cfg,
        posts=posts,
        page=page,
        total_pages=total_pages,
        total_rows=total_rows,
        search=search,
        handle=handle,
        keyword=keyword,
        handles=handles,
        keywords=keywords,
        lang_filter=lang_filter,
        langs=langs,
        from_date=from_date,
        to_date=to_date,
        all_languages=ALL_LANGUAGES_SORTED,
    )
    return 200, html_output


def handle_api_counts(refresh=False):
    if refresh:
        OPTIONS_CACHE.clear()
        LANGS_CACHE.clear()
    counts = get_row_counts(force=refresh)
    return json.dumps(counts).encode()


def handle_update_translation(body: bytes) -> tuple[int, bytes]:
    data = json.loads(body.decode("utf-8")) if body else {}
    table = data.get("table")
    post_id = data.get("post_id")
    field = data.get("field")
    new_text = data.get("new_text", "")
    new_lang = data.get("detected_language")

    if not table or not post_id or not field:
        return 400, json.dumps({"success": False, "error": "Missing required fields (table, post_id, field)"}).encode()

    if table not in PLATFORMS:
        return 400, json.dumps({"success": False, "error": f"Invalid platform table '{table}'"}).encode()

    cfg = PLATFORMS[table]
    if field not in cfg.get("translated_cols", []):
        return 400, json.dumps({"success": False, "error": f"Field '{field}' is not an editable translation column"}).encode()

    id_col = cfg["id_col"]

    try:
        set_clauses = [f"{field} = %s"]
        params = [new_text]
        if new_lang is not None:
            clean_lang = new_lang.strip().lower() if new_lang else None
            set_clauses.append("detected_language = %s")
            set_clauses.append("language = %s")
            params.extend([clean_lang, clean_lang])
        params.append(str(post_id))

        sql = f"UPDATE {table} SET {', '.join(set_clauses)} WHERE {id_col}::text = %s::text"
        rows_affected = execute_write(sql, tuple(params))
        if rows_affected == 0:
            return 404, json.dumps({"success": False, "error": "Post not found or no rows were updated in DB"}).encode()

        lang_name = lang_name_filter(new_lang) if new_lang else ""

        return 200, json.dumps({
            "success": True,
            "message": "Translation and language updated successfully in database",
            "table": table,
            "post_id": str(post_id),
            "field": field,
            "new_text": new_text,
            "detected_language": new_lang,
            "detected_language_name": lang_name
        }).encode()
    except Exception as e:
        return 500, json.dumps({"success": False, "error": str(e)}).encode()
