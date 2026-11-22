# Audience sentiment — Phase 1 (YouTube comments and replies)

Status: Phase 1 (YouTube comment and reply sentiment), Phase 2 (YouTube per-video roll-ups, sentiment gap, D-theme / month
roll-ups) and Phase 3 (Instagram comments, nested replies, roll-ups, cross-platform tables) built and validated
2026-11-20. LinkedIn, Snapchat, emotions, entity-target sentiment and any dashboard change are later phases and were not started.

**Verdict: PARTIAL.** The pipeline, counts and storage are sound (27 of 29 checks pass, 2 are warnings about data
that exists in production but not in the older post store). The sentiment labels come from a general-purpose model
that was not calibrated on this corpus; a blind hand-check agreed with it on 73% of rows (95% CI 61–83%, n = 60).
Use the distributions as a rough signal of wording, not as measured audience attitude.

## Three separate layers

Post, comment and reply sentiment are stored in three tables and are never averaged together.

| Layer | Table | Source | What it holds |
|---|---|---|---|
| Post | `post_sentiment` | existing `post_scores.tone_score`, copied unchanged | tone only (the post pipeline stores no label or probabilities), the video's reported comment count, how many comments and replies we hold |
| Comment | `comment_sentiment` | `youtube_comments` where `is_reply = false` | label, p_negative / p_neutral / p_positive, tone_score, ids, parent video, timestamps, flags |
| Reply | `reply_sentiment` | `youtube_comments` where `is_reply = true` | same columns plus `parent_comment_id`, `parent_comment_uid`, `parent_in_extract`, `parent_same_video` |

Relationships: reply → `parent_comment_id` / `parent_comment_uid` → comment; comment and reply → `post_uid`
(= sha1 of `youtube_video|<video_id>`, the same recipe as `post_facts.post_uid`) → post. `comment_uid` uses the
recipe of `01_extract_base.py` (sha1 of `yt_comment|<comment_id>`), so it joins to the older `comment_facts`.

## Model and text (reused, not changed)

- Model: `cardiffnlp/twitter-xlm-roberta-base-sentiment`, 256 tokens, texts capped at 2000 characters, length-sorted
  batches of 64 — the post pipeline's own settings. The definitions live in `scripts/lib/sentiment_model.py`
  (verbatim copies, because `04_nlp_scores.py` imports duckdb and cannot be imported where torch is);
  `scripts/tests/test_audience_sentiment.py` fails if they drift from 04. `04_nlp_scores.py` is not modified.
- Tone: `tone_score = P(positive) − P(negative)`, the same formula and scale as post tone, so a later sentiment gap
  compares like with like. The label is the argmax of the three probabilities.
- Text scored: `COALESCE(translated_comment_text, comment_text)`, the same translated-first rule the post pipeline uses.
  `text_source` records which was used (translated 382,229 rows, original 9,505, none 34).
- Not done (same as posts): no @user / URL masking, which the model card recommends. Kept identical for comparability.

## Handling of unusable rows (nothing is dropped)

Every production row appears in exactly one of `comment_sentiment` / `reply_sentiment`, with `text_status` and
`score_status`:

| text_status | rows | handling |
|---|---|---|
| `ok` | 391,500 | scored (`score_status = scored`; 0 failed) |
| `unusable_url_only` | 171 | kept, not scored |
| `unusable_symbols` | 63 | kept, not scored |
| `empty` | 34 | kept, not scored |
| `deleted_marker` | 0 | rule exists (`[deleted]`, "comment removed"), none found |

Informational flags on scored rows (still scored): `emoji_only` 16,322, `very_short` 1,265, `truncated` 472,
`has_url` 661. Repeated text inside one video (`dup_text_repeat`, spam or "first!") 6,103 rows, flagged and scored;
`sentiment_summary` has an "excluding_repeated_text" cut (shares move by under 1 point).
Duplicate `comment_id`: 0. Orphan replies (parent not in the extract): 0. Replies whose parent is on another video: 0.

## Coverage against production (one snapshot, 2026-11-20 14:27 UTC)

Production counts and the extract were taken in the same read-only transaction.

| Item | Production | Extracted / stored |
|---|---|---|
| `youtube_comments` rows | 391,768 | 391,768 |
| of which replies | 121,506 | 121,506 |
| top-level comments | 270,262 | 270,262 |
| rows with original text | 391,734 | 391,734 |
| rows with translated text | 382,229 | 382,229 |
| distinct videos with comments | 12,523 | 12,523 |
| rows scored / not scored / failed | | 391,500 / 268 / 0 |
| old store `comment_facts` | | 272,453 (stale, now superseded for YouTube) |

Parent-post link: 8,304 of the 12,523 videos (272,462 comments) have a post row in the analytics store with an
existing post tone; **4,219 videos (119,306 comments, 30%) do not**, because the store is an older snapshot. Their
`post_sentiment.tone_score` is NULL and `post_in_store = false`. They were not re-scored on purpose (no
reprocessing of posts in this phase). Scraped comments (plus replies) per video against YouTube's reported comment
count: median ratio 0.90.

## Sentiment distribution (scored rows)

| Layer | Negative | Neutral | Positive | Mean tone |
|---|---|---|---|---|
| Comments (270,122) | 46.1% | 20.3% | 33.6% | −0.123 |
| Replies (121,378) | 51.8% | 31.4% | 16.8% | −0.273 |
| Posts, same videos (existing tone) | | | | −0.161 (mean, 8,304 posts) |

Replies are more negative and less positive than comments; that is a descriptive difference, not evidence of
hostility (see limits). By flag: emoji-only rows are 75% positive, very short rows 73% neutral, URL rows 75%
neutral, truncated (very long) rows 82% negative.

## Validation (`audience_sentiment.validation_checks`)

27 PASS, 2 WARN, 0 FAIL. Checks cover: extract = production (rows, replies, rows with text, videos); comment + reply =
all rows and no row in both layers; no null ids; no duplicate `comment_id` / `comment_uid`; no reply without a
parent, no orphan or cross-video parent; scored + failed = rows with text, not scored = rows without usable text;
0 failed predictions; probabilities sum to 1 (max error 1.4e-07); label is the argmax (0 mismatches);
`tone_score = p_positive − p_negative` (max error 0); re-scoring 200 random rows reproduces the stored scores
exactly; all three labels present in both layers with none above 90%.
Warnings: `posted_at` is missing on 30,446 rows (19,519 comments, 10,927 replies; kept, never used to drop rows),
and 4,219 videos have no post row in the analytics store (above).

**Hand-check (blind, one reviewer, the model's label hidden):** 60 scored rows, 10 per layer × predicted label.
Agreement 44/60 = 73% (95% CI 61–83%): comments 21/30, replies 23/30. By predicted label: negative 14/20, neutral
15/20, positive 15/20. Positive↔negative flips: 5 of 60 (8%). Another 11 disagreements are neutral vs polar.
Reviewer is not a human domain expert and n is small; read this as "usable for aggregate trends, noisy per row".

## Storage

- Local: `data/audience_sentiment.duckdb` (own file, so it never needs a lock on the metrics or geo databases);
  tables `post_sentiment`, `comment_sentiment`, `reply_sentiment`, `comment_text` (text, local only),
  `sentiment_summary`, `coverage_report`, `validation_checks`, `run_meta`. Intermediate files under
  `data/audience/` (all git-ignored; they hold comment text).
- Delivery Postgres: new schema `audience_sentiment` (same tables except `comment_text`; no comment text and no author
  names are delivered, authors only as a hash), listed in `public._delivery_manifest`. Loaded by
  `delivery/ingest_audience_sentiment.py`, which replaces only this schema and leaves `metrics_v2` and
  `geo_ontology` untouched.
- Production Postgres: read-only throughout (`lib.pg.read_only_snapshot`, `transaction_read_only = on` asserted at
  the start of the extract, SELECT only, always ROLLBACK).

## Run order

```bash
.venv/bin/python scripts/32_audience_extract.py           # read-only pull from production, ~3 min
PYTHONPATH=<dir with sentencepiece 0.2.0> /usr/bin/python3 scripts/33_audience_score.py   # torch env, ~23 min on a free GPU, resumable
.venv/bin/python scripts/34_audience_build.py             # layers, coverage, validation
python3 delivery/ingest_audience_sentiment.py             # new delivery schema
python -m pytest scripts/tests/test_audience_sentiment.py
```

Environment note: with transformers 5.x the multilingual tokenizer needs `sentencepiece` (0.2.0 here; newer builds
clash with the installed protobuf). It was installed outside the project environments.

## Limits

- General-purpose model, not calibrated on this corpus; it reads wording, not stance. Sarcasm, quoted or reported
  violence, and war vocabulary push comments toward "negative" regardless of attitude toward the video. The 46% / 52%
  negative shares are not a finding about hostility. Hate scores remain uncalibrated and are not part of this phase.
- Language is not measured per row; the model is weaker on languages outside its training set, and about 98% of rows
  are scored on the translated text, whose quality was not checked.
- The hand-check is one reviewer and 60 rows.
- Comment text is a scrape at one point in time; production keeps growing (391,702 rows earlier the same day, 391,768 at
  the snapshot), so rerun 32–34 to refresh.
- 30% of comments belong to videos missing from the post store, so the post layer is incomplete for them.
- Aggregation to audience sentiment at post, topic and event level, and the sentiment gap, are Phase 2+.

---

# Phase 2 — YouTube per-video roll-ups, sentiment gap, topic roll-ups

Stage `scripts/35_audience_rollup.py`, definitions in `scripts/lib/audience_rollup.py`.
Adds three tables to `data/audience_sentiment.duckdb` and to the delivery schema `audience_sentiment`; the Phase 1
tables are not touched (validation fingerprints them before and after).

| Table | Grain | Contents |
|---|---|---|
| `post_audience_sentiment` | one row per YouTube video (14,938 = 10,719 in the post store + 4,219 with comments but no post row) | comment metrics `c_*` and reply metrics `r_*` in separate columns, the post's existing tone, three gaps, data status, theme, month |
| `audience_rollup` | dimension × D-theme × month (272 rows) | pooled counts and sums, percentages, net sentiment, mean tone, gap summaries |
| `audience_rollup_validation` | one row per check | 44 checks, all PASS |

## Per-video metrics (comments `c_`, replies `r_`; never merged)

`rows`, `scored`, `not_scored`, `failed`; `neg`, `neu`, `pos` counts (the Phase 1 label, scored rows only);
`neg_pct`, `neu_pct`, `pos_pct`; `net_sentiment` = 100 × (positive % − negative %) (same units as metrics v2's
`net_sentiment_score`); `tone_mean`, `tone_sd`, `tone_se` (tone = P(positive) − P(negative), the post-tone scale);
`conf_mean` (mean of the label's top probability, 0.33–1, a model-confidence indicator, not accuracy);
`supported` (scored ≥ 5); `repeat_text_rows`; plus the summable `tone_sum`, `tone_sumsq`, `conf_sum`.
`rows_total` (comments + replies) exists only as a coverage figure, with `reported_comment_count` and `coverage_ratio`.

## Sentiment gap (one definition, one scale)

Post, comment and reply tone are all P(positive) − P(negative) from the same model, so each lives on [−1, 1].

- `gap_comment_vs_post` = mean comment tone − post tone
- `gap_reply_vs_post` = mean reply tone − post tone
- `gap_reply_vs_comment` = mean reply tone − mean comment tone (discussion escalation or disagreement)

Range −2…+2; negative means the audience is more negative than the post. `gap_*_direction` is
`audience_more_negative` / `audience_more_positive` when |gap| ≥ 0.30 (twice the 0.15 class threshold of metrics v2;
provisional, not calibrated), otherwise `aligned`. A gap is NULL when the post has no tone (never treated as neutral),
when the audience layer has fewer than 5 scored rows, or when either value is missing. Unit tests cover a neutral post
with very negative comments, a positive post with a negative audience, aligned cases, the threshold edge, and every NULL case.

## Data integrity rules

- No invented post tone: videos without an existing post score (the 4,219 whose post row is not in the analytics
  store) have `post_tone_score` NULL and all gaps NULL; their comment and reply metrics are still computed.
- **Zero comments vs missing data** (`comment_data_status`): `has_comments` 12,523; `not_collected_known` 80 (YouTube
  reports comments, we hold none); `unknown_no_comment_data` 2,335 (nothing collected and no reported count);
  `confirmed_zero` 0. YouTube's reported count is NULL for most videos and never 0 in this data, so a real zero cannot
  be confirmed; the status is kept so it can be once a zero count appears.
- Counts below minimum support: mean, SD, net sentiment and gap are NULL (never 0) and the counts stay.
- One comment is counted once: roll-ups are sums of the per-video rows, which are sums of the Phase 1 rows.

## Roll-ups (topic, time, platform, event)

`audience_rollup` has `dimension` = `all`, `d_theme`, `month`, `d_theme_month`. Statistics are pooled over comments
(comment-weighted), computed from summable counts and sums, so any group can be re-summed. Pooled statistics are NULL
below 30 scored rows. `gap_*_mean` is the mean of per-video gaps (video-weighted) and needs at least 5 videos with a gap;
it comes with `gap_*_share_audience_more_negative` / `_positive`.

- **Topic = D-theme** (existing hybrid labels, unchanged): D01–D09, plus explicit buckets `unassigned` (6,987 videos
  with no theme) and `post_not_in_store` (4,219). D-theme accuracy in the 2026-11-20 audit was 81% lenient / 56%
  strict, so topic roll-ups inherit that error.
- **Time** = month the video was posted (`period_basis = video_posted_month`); `unknown` where there is no post date.
- **Platform**: only YouTube so far (`platform` column on the per-video table); other platforms join in by the same keys.
- **Event**: not built, but the per-video table carries `post_uid`, `posted_at` and `d_theme`, so a post-to-event
  mapping can be joined and summed with the same `c_*` / `r_*` columns.

```sql
-- any custom group-by over the per-video table stays correct if you sum counts and sums, not averages
SELECT d_theme, sum(c_scored) n, 100.0*(sum(c_pos)-sum(c_neg))/sum(c_scored) AS comment_net_sentiment
FROM audience_sentiment.post_audience_sentiment WHERE c_scored > 0 GROUP BY 1;
```

## Results (YouTube, 2026-11-20)

- 14,938 videos; 12,523 with comment rows; audience layers with at least 5 scored rows: comments 7,709 videos,
  replies 3,517 videos.
- Gap availability: comment gap 5,263 videos, reply gap 2,443, reply-vs-comment 3,479.
- Comment gap: mean +0.05, median +0.04, 10th–90th percentile −0.34 to +0.46; 685 videos with the audience clearly more
  negative than the post, 1,161 clearly more positive. Reply gap: mean −0.03, median −0.06; 444 more negative, 350 more positive.
  Replies are slightly more negative than comments (mean −0.05).
- By D-theme (comment net sentiment / reply net sentiment): D01 −33 / −48, D02 −21 / −44, D03 −25 / −44, D04 −10 / −41,
  D05 −13 / −38, D06 −9 / −34, D07 −17 / −43, D09 −21 / −41; unassigned −6 / −31. D08 has 19 videos and 82 scored
  comments (reply statistics NULL). Replies are more negative than comments in every theme.
- Coverage: scraped comments + replies per video against the reported count has a median ratio of 0.89 (3,975 videos have a reported count).

## Validation (`audience_rollup_validation`)

44 PASS, 0 WARN, 0 FAIL. Per-video sums equal the Phase 1 comment (270,262 / 270,122 scored) and reply (121,506 / 121,378)
counts; roll-up totals by theme, month and theme × month equal the totals; pooled counts equal a recount from the raw
comment rows; per-video counts and mean tone equal an independent recomputation; percentages sum to 100 and net
sentiment equals positive % − negative % (error 1e-14); no duplicate `post_uid`, none missing for any comment or reply;
post tone identical to `post_scores` (max difference 0); every gap equals mean − post tone (error 0) and exists only with
a post tone and at least 5 scored rows; NULL-not-zero rules hold; Phase 1 tables unchanged (row counts, ids, md5 of id|label).

## Limits (Phase 2)

- The roll-ups inherit Phase 1's limits: the model reads wording, not stance, and war vocabulary pushes comments and
  replies toward negative. A gap shows how the audience's wording differs from the video's wording, not a measured change of attitude.
- Post tone comes from the video's title, description and transcript text; comment tone from short comments, so the two are
  comparable on the scale but not in text type.
- Topic roll-ups use hybrid D-themes that cover 35% of store videos; 65% fall into `unassigned`.
- 30% of comments belong to videos with no post row in the analytics store, so they have no post tone, no theme and no
  video date, and appear only in the `post_not_in_store` and `unknown` buckets.
- The 0.30 gap flag and the minimum supports (5 scored rows per video, 30 pooled) are provisional choices, not calibrated.

---

# Phase 3 — Instagram comments, nested replies, per-post roll-ups

Stages `scripts/36_instagram_extract.py` (read-only extraction), `scripts/33_audience_score.py --platform instagram`
(same model, same code as YouTube), `scripts/37_instagram_build.py` (layers, coverage, validation, roll-ups, cross-platform
tables). Parsing lives in `scripts/lib/audience_instagram.py`. The Phase 2 roll-up code (`35_audience_rollup.py`) was
parametrised with a `Spec`; with the default (YouTube) spec its outputs are byte-identical to before (md5 of every row
compared, excluding the build timestamp), and the Instagram build re-checks that all YouTube tables are unchanged.

## The data, and what it means for the unit of analysis

- `instagram_posts` has 959,236 rows but only 15,861 distinct `post_url` (plus one row with no url): the same post is
  stored up to 501 times. 98,389 rows have comments, but they are copies of only 1,397 posts (1,419 distinct payloads).
  The project already knew this (stage 25: 99% of Instagram posts are copies). **The Instagram post is the distinct `post_url`.**
  Counting rows would have inflated comments and replies about 12 times (747,040 elements with copies in production for 64,318 distinct ones).
- `comments_data` is an array of comment objects `{comment_id, text, username, is_verified, likes, created_at,
  parent_comment_id, replies: [...]}`; replies sit one level deep in `replies` (no deeper level exists; deeper levels
  would be handled). A `comment_id` is used on exactly one post. No translated text exists in `comments_data`.
- Irregular items (all kept, none dropped): 183 items that are not comments (no id, no text; keys such as `id`, `type`,
  `url`, `accessibilityCaption`) get `text_status = malformed_non_comment`, are not scored, and keep a short `raw`
  excerpt locally; 964 top-level objects name a `parent_comment_id` whose parent Instagram did not return: layer =
  `reply`, `shape = top_level_with_parent`, `parent_in_extract = false`.

## Extraction and normalisation

- Read-only (`read_only_snapshot`, `transaction_read_only = on` asserted, SELECT only). No Instagram requests.
- Only distinct payloads are fetched (`DISTINCT ON (post_url, md5(comments_data))`, the latest scrape of each), with the
  number of rows carrying each, then flattened. One row per distinct `(post_url, comment_id)`; `n_source_rows` records how
  many production rows carried it. **Reconciliation:** production top-level elements counted with copies = 632,215 =
  extracted x copies; nested replies 114,825 = 114,825; distinct (post, comment_id) 41,461 + 22,857 = 64,318 = extracted rows.
- Relationships: post (`post_uid`) → comment (`comment_uid` = sha1 of `ig_comment|<comment_id>`) → reply
  (`parent_comment_id` / `parent_comment_uid`). `post_uid` is the existing store `post_uid` when the post is in the
  analytics store (so it joins to `post_facts`, `post_scores`, the theme files), else sha1 of `instagram|<smallest row id>`.
  Items without an id get a repeatable id from post + content hash + occurrence number (the first build had given all 183
  the same id; the validation caught it, it was fixed in the code and in the extract file without re-reading production).
- Triage uses the same rules as YouTube (`lib/audience_text.py`): empty, deleted marker, URL-only and symbols-only are kept
  and not scored; emoji-only, very short, truncated, has-URL and repeated text are flagged and scored. New flag
  `mention_only` (text is only @handles, informational, scored like any text). Author names are never stored, only a hash.

## Scoring (reused unchanged)

Same model, 256 tokens, 2000-character cap, pos−neg tone, same probabilities and label rule as posts and YouTube
(`scripts/lib/sentiment_model.py`, guarded by the drift test). Comments and replies are scored in one pass and kept in
separate tables. 64,057 rows scored, 261 not scored, 0 failed; re-scoring 200 rows reproduces the stored scores exactly.
Text is scored as scraped: unlike YouTube there is no translation, so non-English comments (Hindi / Hinglish,
Indonesian, Spanish, Portuguese, Arabic and others are common) go to the multilingual model untranslated.

## Tables (local `data/audience_sentiment.duckdb`; the delivery schema `audience_sentiment` has the same minus `ig_comment_text`)

| Table | Contents |
|---|---|
| `ig_post_sentiment` | post layer: 15,862 distinct posts (one has no url), existing post tone (NULL where none), copies, reported comment count, rows extracted |
| `ig_comment_sentiment` | 40,497 top-level comments: label, p_negative / p_neutral / p_positive, tone_score, ids, post links, status, flags |
| `ig_reply_sentiment` | 23,821 replies: same columns + parent links (`parent_comment_id`, `parent_comment_uid`, `parent_in_extract`) and `shape` |
| `ig_comment_text` | the scored text, local only |
| `ig_sentiment_summary`, `ig_coverage_report`, `ig_validation_checks`, `ig_run_meta` | distribution, coverage against production, checks, run metadata |
| `ig_post_audience_sentiment`, `ig_audience_rollup`, `ig_audience_rollup_validation` | per-post roll-ups, pooled roll-ups, 44 roll-up checks (same code and definitions as Phase 2; `n_videos` means posts here) |
| `xplat_post_audience_sentiment` | YouTube + Instagram per-post rows in one schema (`platform`, `post_native_id`, `post_url` NULL for YouTube), 30,800 rows |
| `xplat_audience_rollup` | pooled roll-ups by platform (youtube_video, instagram, all) x D-theme x month (`n_posts`), 611 rows |

YouTube tables are unchanged. Instagram uses `post_native_id` where YouTube has `video_id`; the cross-platform tables use `post_native_id`.

## Coverage against production (snapshot 2026-11-20 15:24 UTC)

| Item | Production | Extracted |
|---|---|---|
| rows in `instagram_posts` | 959,236 | 959,236 mapped to 15,861 post_urls (+1 row without a url) |
| rows with comments / distinct posts with comments | 98,389 / 1,397 | 98,389 / 1,397 |
| top-level elements, with copies | 632,215 | 632,215 |
| nested reply elements, with copies | 114,825 | 114,825 |
| distinct comments + replies | 64,318 | 64,318 (40,497 comments + 23,821 replies, of which 964 top-level objects with a parent id) |
| scored / not scored / failed | | 64,057 / 261 (malformed 183, URL-only 37, symbols-only 41) / 0 |

Collection coverage is the important gap: only **1,397 of 15,861 posts (9%) have any comment data**. Instagram reports a
comment count, so zero can be told from missing here: 7,536 posts have a reported count of 0 (`confirmed_zero`), 6,929
report comments that we do not hold (`not_collected_known`), none are unknown. On the 1,397 posts with data we hold 64,318
rows against about 645,000 reported comments (10%); per post the median extracted-to-reported ratio is 0.98 but the largest
posts are cut off (for example 625 reported, 9 held).

## Sentiment distribution (scored rows)

| Layer | Negative | Neutral | Positive | Mean tone |
|---|---|---|---|---|
| Instagram comments (40,243) | 35.1% | 29.0% | 35.8% | +0.007 |
| Instagram replies (23,814) | 41.7% | 36.9% | 21.4% | −0.106 |
| YouTube comments (270,122) | 46.1% | 20.3% | 33.6% | −0.123 |
| YouTube replies (121,378) | 51.8% | 31.4% | 16.8% | −0.273 |

As on YouTube, replies are more negative and less positive than comments. Instagram is less negative and more neutral than
YouTube. That reflects what people write and how much of it is short or emoji (8,420 emoji-only, 1,213 mention-only, 1,059
very short rows) as much as attitude; no cross-platform conclusion should be drawn from these shares. Repeated text rows: 5,342.

## Validation

`ig_validation_checks`: 38 PASS, 3 WARN, 0 FAIL (41 checks, including three on YouTube being unchanged). Checks cover:
production reconciliation (elements with copies, distinct comments, rows and posts with comments), comment + reply =
all rows, no row in both layers, no null ids except malformed items, no duplicate `comment_uid` or `(post, comment_id)`, no
`comment_id` shared across posts, every post mapped, replies all have a parent id, no reply-of-reply, no nested reply whose
declared parent differs from where it sits, text-status accounting, 0 failed predictions, probabilities sum to 1 (error 1.3e-07),
label = argmax, tone = p_positive − p_negative (error 0), reproducible scoring, same model as YouTube, YouTube tables unchanged.
Warnings (kept data, flagged): 964 replies whose parent comment is not in the extract; `posted_at` missing on the 183
malformed items; 972 posts with comments have no post row in the older analytics store. `ig_audience_rollup_validation`: 44 PASS.

**Manual review (blind, one reviewer, the model's label hidden):** 60 scored rows, 10 per layer x predicted label, drawn at
random without length filtering. Agreement 43/60 = 72% (95% CI 59–81%): comments 20/30, replies 23/30; by predicted label
negative 12/20, neutral 15/20, positive 16/20. Positive↔negative flips 4 of 60 (a Hindi insult scored positive, "You deserve
Osama Bin Laden" scored positive, a gloating comment scored negative, an election call scored negative); 13 more
disagreements are neutral vs polar. @mention-led replies (24 of 60) agreed 18/24. This matches the YouTube check (73%) within
the sample's noise; the reviewer is not a native speaker of the many languages in the sample and n is small.

## Per-post roll-ups (`ig_post_audience_sentiment`, `xplat_post_audience_sentiment`)

Same columns and definitions as YouTube Phase 2: counts, percentages, net sentiment (100 × (pos% − neg%)), mean tone,
spread, mean label confidence (Instagram 0.63 comments / 0.60 replies), minimum-support flags (5 scored rows), coverage
indicators, the three gaps, comment-data status. 15,862 posts: 1,397 `has_comments`, 7,536 `confirmed_zero`, 6,929
`not_collected_known`. Audience layers with at least 5 scored rows: comments 601 posts, replies 317; at least 30 scored
comments: 267 posts. Gaps (mean audience tone − post tone): comment gap on 123 posts (mean +0.10, median +0.02; 16 audience
clearly more negative, 35 more positive), reply gap on 45, reply-vs-comment on 311 (mean −0.05). Gaps are few because only
969 of the posts have a post tone and only 412 of those have comments; **14,879 posts (972 of the 1,397 with comments) are
not in the older post store**, so their post tone, theme and date are NULL (never neutral).

Roll-ups (`ig_audience_rollup`, `xplat_audience_rollup`) are pooled by D-theme x month with the same rules (pooled statistics
NULL below 30 scored rows). With so few themed posts (D05 105 posts, D04 63, the rest under 20) the theme roll-ups are thin:
only the `post_not_in_store` and `unassigned` buckets and the month totals have enough comments. The `all` platform row pools
YouTube and Instagram comment-weighted: comments −10.8 net sentiment (YouTube −12.6, Instagram +0.7), replies −32.6.

## Cross-platform compatibility

`xplat_post_audience_sentiment` has one row per post with `platform` in {youtube_video, instagram}, the same `c_*` / `r_*`
columns, gaps, `d_theme`, `theme_status`, `posted_month`; `xplat_audience_rollup` aggregates it by platform, D-theme and month.
Any custom aggregation by platform, post, D-theme or month is a sum of counts and sums over this table (same SQL as the
YouTube example). No dashboard or visualization was built.

## Performance

Extraction 2 min 36 s (read-only; production counts, distinct payloads, post map); scoring 9.5 min for 64,057 rows on the
shared GPU (about 190–300 rows/s; the longest texts at the end are slowest; model load about 1 minute); build and roll-ups
about 5 s; delivery load under a minute. The YouTube refactor check re-ran the YouTube roll-up (about 1 minute).

## Limits (Phase 3)

- Collection coverage is the main gap: 9% of Instagram posts have comment data and 6,929 report comments we do not hold.
  Results describe the 1,397 posts with comments, not Instagram in general.
- The model reads wording, not stance; Instagram comments are heavily short, emoji-led, slang, code-switched or @mention-led,
  and the text is not translated, so quality is lower for non-English and mixed-script comments than the 72% suggests for the
  sample's average. Language is not measured per row.
- Post tone, D-theme and date exist for only 983 of 15,862 posts (the analytics store is older than production); gaps and
  theme roll-ups are therefore few and thin. Refreshing the store (stage 01) would fill them without any change to this pipeline.
- 964 replies have no parent comment in the data, so their place in a discussion thread cannot be rebuilt.
- 22 posts have two different comment payloads that share no comment_id, so their comments are the union of both (no
  comment was found in two payloads; `n_variants_seen` is 1 everywhere). Copies of a post otherwise carry identical payloads.
- The gap flag (0.30) and the minimum supports (5 scored rows per post, 30 pooled) are provisional.

---

# Targeted post enrichment (post tone, themes, dates for audience posts missing from the older post store)

Why: 4,219 YouTube videos and 972 Instagram posts that have comments are not in the analytics post store (it stops at 2026-10-23),
so they had no post tone, theme or reliable date and could not enter the sentiment gap or theme roll-ups. A full stage 01 refresh
was ruled out: it would load ~1.03 million Instagram rows (copies of ~15.9k posts) and stages 04 / 07 are not dedup-aware.
Instead only the posts that carry audience data were read, one canonical row per post, and enriched in new tables.

Stages `scripts/38_audience_enrich_extract.py` (read-only), `39_audience_enrich_tone.py` (torch), `40_audience_enrich_build.py`
(themes, tables, v2 roll-ups, comparison, validation). The analytics store, `metrics_v2`, stage 01 / 04 / 07 and the dashboard are
untouched; the enriched posts are not added to `post_facts`.

## Target scope (counted before processing)

| | Posts with comment / reply data | Already in the store with a tone | Processed |
|---|---|---|---|
| Instagram (one row per distinct post_url) | 1,397 | 412 | **985** (972 not in the store + 13 in the store with an empty stored caption) |
| YouTube | 12,523 | 8,304 | **4,219** (not in the store) |

Instagram posts without comment data (14,465) and in-store posts with a valid tone were not processed. Instagram canonical row =
smallest `id` of the copies of a post_url (the copies' captions are identical for every target); post_uid = sha1 of
`instagram|<id>` as in Phase 3. YouTube text is built as stage 01 builds it (title, description, first 4,000 transcript characters).

## What was done

- **Post tone** (same model, 2,000-character cap, batching and pos−neg formula as stage 04; `lib/sentiment_model.py`):
  5,185 new results (Instagram 966, YouTube 4,219). 19 Instagram posts have no caption in production (the 13 in-store ones
  plus 6 new ones), so no tone and no embedding.
- **Themes** (stages 26, 27, 28 replayed on the 5,191 new posts only, same code and thresholds, reference set rebuilt from the stored
  stage 26/27 files): **1,318 assigned** (YouTube 1,166 = 27.6%, hybrid 1,050 + neighbour 116; Instagram 152 = 15.6%, hybrid
  114 + neighbour 38); the rest stay "unassigned" exactly as in the stored data. In-store posts keep their stored themes.
- **Mappings recovered:** post_uid, platform id, post date (month) and reported comment count for all 5,204; 5,191 new posts now
  have a post row, a date and (where scoreable) a tone available to the audience tables.
- **New tables** (DuckDB + delivery schema, no text): `enriched_post_facts`, `enriched_post_scores`, `enriched_post_theme`,
  `enrichment_targets`, `enrichment_validation`, `enrichment_run_meta`; `enriched_post_text` is local only. **Versioned roll-ups**
  (v1 kept for comparison): `post_audience_sentiment_v2`, `audience_rollup_v2`, `audience_rollup_validation_v2`,
  `ig_post_audience_sentiment_v2`, `ig_audience_rollup_v2`, `ig_audience_rollup_validation_v2`, `xplat_post_audience_sentiment_v2`,
  `xplat_audience_rollup_v2`. v2 per-post tables add `post_source` (store | targeted_enrichment) and `post_tone_source`.

## Before and after (posts with comment / reply data)

| | YouTube before | after | Instagram before | after |
|---|---|---|---|---|
| posts with comment data | 12,523 | 12,523 | 1,397 | 1,397 |
| post known (in store or enriched) | 8,304 | 12,523 | 425 | 1,397 |
| with a post tone | 8,304 | 12,523 | 412 | 1,378 |
| with a D-theme | 3,019 | 4,185 (+1,166) | 78 | 230 (+152) |
| with a post month | 8,304 | 12,523 | 1,397 (from production) | 1,397 |
| comment gap (post tone and >= 5 scored comments) | 5,263 | **7,709** | 123 | **596** |
| reply gap | 2,443 | **3,517** | 45 | **316** |
| comment gap clearly flagged (|gap| >= 0.30) | 1,846 | 2,746 | 51 | 267 |
| reply gap clearly flagged | 794 | 1,133 | 20 | 147 |

Every video with at least 5 scored comments (7,709) or replies (3,517) now has a gap. Instagram: 596 of 601 and 316 of 317 (the
gaps are missing only for the posts without a caption). Roll-ups: the `post_not_in_store` theme bucket and the `unknown` month
bucket are empty for YouTube and Instagram commented posts; scored-comment totals per dimension are unchanged (270,122 / 121,378 YouTube,
40,243 / 23,814 Instagram). Gap results after enrichment: YouTube comment gap mean +0.05 (n 7,709), reply gap mean −0.03; Instagram comment gap
mean +0.10 (n 596), reply gap mean +0.03 (n 316). By-theme Instagram roll-ups are still thin (D05 73 posts, D04 41, D03 32, others under 25).

## Validation (`enrichment_validation`): 39 PASS, 3 WARN, 0 FAIL

- Processed targets equal the deduplicated target list (985 / 4,219); no duplicate post_uid, native id or Instagram post_url; none of the
  5,191 new posts already exists in `post_facts`; post_uid equals sha1(platform|native id) for every new post; the Instagram canonical id
  equals the id in the Phase 3 post table; every enriched post is in the v2 tables with a tone wherever a valid one exists.
- v2 per-post tables keep v1's universe (14,938 / 15,862); posts that were not enriched are identical in v1 and v2; enriched posts keep
  identical comment and reply metrics (only post tone, theme, date and gaps change); roll-up groups that changed are only groups that
  hold an enriched post (YouTube 35 of 283, Instagram 46 of 73, none outside); scored-comment totals unchanged; the 44-check roll-up
  suites pass on v2 for both platforms.
- Nothing existing changed: comment, reply and Phase 1 post tables, the v1 per-post / roll-up / cross-platform tables, the analytics
  `post_scores` (497,530 rows, same md5) and `post_facts` (497,530, same md5), and the `metrics`, `geo_ontology`, stage 26/27/28
  files (modification times) are all unchanged; production: read-only session asserted (`transaction_read_only = on`), SELECT only,
  no write statement exists in scripts 38-40.
- Reproducibility on 300 in-store posts re-read from production: tone Instagram 150/150 within 1e-4, YouTube 148/150 (max difference
  0.0018: the text changed after the store snapshot); **code check: replaying stages 26/27 and 28 on the stored embedding and text
  reproduces the stored results 150/150 on both platforms (88 and 102 neighbour votes, all equal).**
- The 3 warnings are the same finding: **production embeddings differ from the stored ones** for many posts (YouTube: 24 of 150
  identical, median cosine 0.98; Instagram: 142 of 150). With current production embeddings the themes agree with the stored themes for
  150/150 Instagram and 139/150 YouTube posts (neighbour votes 87/88 and 81/93). The cause is the embeddings, not the replay code. New posts
  use the current production embeddings, which is what a stage 01 refresh would use.

## Performance

Extraction 48 s (read-only); post tone 26 s on the GPU for 5,504 texts (5,204 targets + 300 check posts); build 17 min the first time and 12 min on the re-run
(most of it loading the 122,564 reference vectors, which the re-run saved to `data/audience/ref_cache.npz`, 0.5 GB, git-ignored, so later runs skip
that step); delivery load under a minute.

## Limits

- Theme coverage is low (YouTube 27.6%, Instagram 15.6% of the new posts) and hybrid theme accuracy is 81% lenient / 56% strict
  (2026-11-20 audit); Instagram theme roll-ups stay thin.
- 19 Instagram posts have no caption (13 in the store, 6 new): no tone, and 6 of the new ones no embedding, so no theme.
- Embedding drift: the stored and current production embeddings are not the same for many posts, so a re-read of an in-store post can
  get a different theme than it has stored; the stored themes were not changed.
- The enriched posts are only in the audience tables: they are not in `post_facts`, `metrics_v2`, the dashboard or the entity / theme
  pipelines. The production Instagram table keeps adding duplicate copies; any later full refresh still needs a collapse of Instagram
  copies in stage 01. Re-running scripts 38-40 enriches posts that appear later with comments.
- v1 tables are kept beside v2 for comparison; consumers should switch to the v2 tables.

---

# Dashboard: Explore > Audience Sentiment (v2 tables)

The dashboard had no audience section (its "Sentiment Analysis" tile shows post tone from the metrics snapshot and is unchanged), so there were
no v1 audience queries to switch. A new tile, **Audience Sentiment**, was added to the Explore page; it reads only the v2 tables.

| Piece | Where |
|---|---|
| Query logic (new, isolated) | `UI/audience_view.py` |
| Route | `GET /api/explore/audience` in `UI/api_server.py` (one import and one route, existing code unchanged) |
| Tile and view | `UI/assets/dash.js` (new tile + `exAudView`; 6 existing lines touched: the tile list, the dispatch, the state object, and three shared-control conditions so the entity and theme pickers show for the new tile and the free-text box is hidden), `UI/assets/dash.css` (`aud-*` classes only) |
| Tests | `UI/tests/check_audience_api.py` (46), `UI/tests/check_audience.mjs` (47 with the live server; 38 with canned data); `check_explore.mjs` now expects six tiles |

**Tables read:** `xplat_post_audience_sentiment_v2` (post tone, comment `c_*` and reply `r_*` metrics, gaps, theme, date, data status) and, for the comment-date trend,
`comment_sentiment`, `reply_sentiment`, `ig_comment_sentiment`, `ig_reply_sentiment`; `enriched_post_text` and `analytics.post_facts` only for the opening text of the posts in the gap lists; `geo.entity_mentions` for the
entity filter. The v1 per-post and roll-up tables are not read (a test checks the source for them). The audience file is attached read-only for one request and then closed, so the pipeline scripts that
write it are not locked out; if it is being rebuilt the endpoint answers 503 and the tile says so.

**What the tile shows:** post tone, comment sentiment and reply sentiment as separate layers per platform (YouTube, Instagram, both) with counts, positive / neutral / negative shares, net sentiment, mean tone and
label confidence, and stacked distribution bars; the sentiment gap (comments vs post, replies vs post, replies vs comments) with the share of posts where the audience is clearly more negative or positive and a histogram;
trends over time (net sentiment of comments and of replies, YouTube vs Instagram, by post date or by comment date; daily, weekly or monthly points chosen from the range length); a by-theme table; a coverage table
that separates "comments collected", "reported zero", "reported but none collected" and "unknown"; and the posts where the audience differs most from the post (with links).

**Filters:** date range (the page range, All dates, or a range chosen on the tile, because the page's own range stops at the metrics snapshot's last day, 2026-10-26, while audience data runs to 2026-11-20), platform,
entity (the existing entity picker, via `geo.entity_mentions`), theme (the existing D-theme select; the theme list is read from the data, so a later taxonomy change needs no change here, and a value outside the known taxonomy is shown with its raw
code and a note rather than dropped). The topic / sub-topic taxonomy was not changed.

**Limits:** the entity filter uses the production entity extract, which predates the 5,191 enriched posts: 0 of them can match an entity (YouTube 8,206 of 12,523 posts with audience data have entity data; Instagram 92 of 1,397); the tile says so when an
entity is chosen. Themes are provisional (81% lenient / 56% strict) and most posts have none. Platforms other than YouTube and Instagram have no comment text and are reported as unsupported. The date range and theme apply to the post; only the trend
can be grouped by comment date. Post sentiment here is the post's tone classified at the metrics' +-0.15 cut-off (the post pipeline stores no label).


## Research proposal (not implemented): does the gap depend on the post's own tone?

Status: **proposal only.** No gap value, threshold or table was changed. Figures below were computed read-only from
`xplat_post_audience_sentiment_v2` (8,305 posts with a comment gap; YouTube 7,709, Instagram 596) on 2026-11-21.

**Finding (confirmed).** The gap is systematically related to the post's tone.
- Comment tone ≈ −0.02 + 0.57 × post tone (R² 0.23). YouTube slope 0.60, Instagram 0.35. A slope below 1 means the
  gap (comment − post) has a built-in slope of about −0.43 against post tone.
- Mean gap by post-tone band: ≤ −0.6: +0.39 (59% of those posts exceed the ±0.30 flag); −0.2 to +0.2: about 0;
  ≥ +0.4: −0.29. Among posts with tone ≤ −0.4, 43% are flagged "audience more positive" and 0% "more negative".
- Two causes mix: (1) regression to the mean, because a post's tone is one noisy reading of one text, while a comment mean
  averages many; (2) a register difference, since comments are short and blunt and rarely reach the extremes of a news caption
  or a promotional caption. Comment-mean sampling noise explains only about 16% of the comment-mean variance (mean SE 0.12).
  Instagram's low slope (0.35) fits promotional captions with emoji comments.
- A baseline using platform × post-tone band explains about 18% of the gap variance on a held-out half. Adding the D-theme
  explains nothing further (residual variance 0.086 either way).

**Proposed baseline.** Expected gap = mean gap of posts in the same platform and post-tone band, using 8 fixed bands
(−1, −0.6, −0.4, −0.2, −0.05, +0.05, +0.2, +0.4, +1), fitted on the whole corpus and re-fitted on rebuild. Alternatives, in
order of simplicity: a per-platform linear fit of comment tone on post tone; isotonic regression (monotone, no band edges).
Use bands first: they are explainable in one sentence and need no model.

**Proposed comparison.** Show the observed gap beside the expected gap and the residual (observed − expected). Keep the
raw gap and the 0.30 flag unchanged and add the residual as a second column, never a replacement. Mark the residual as unsupported
below 10 scored comments. Optionally compare the residual with the post's comment-mean standard error (`c_tone_se`) to separate
a gap from sampling noise. That is the first statistical statement the gap would have; the 0.30 flag is operational only.

**Held-out check (random half fitted, other half evaluated).**
- Raw flag rate 37%; residual flag rate 32%.
- 25% of raw flags disappear on the residual.
- For posts with tone ≤ −0.4, the "audience more positive" flags drop from 43% to 12%, and a balancing 8% appear as
  "more negative". For posts with tone ≥ +0.2 the flags move from 7% / 27% to 22% / 14%.
- Of the 100 most negative raw gaps, 79 stay in the residual top 100. The platform mix of that list does not change
  (84 YouTube, 16 Instagram), so the residual would not by itself fix the Instagram dominance of the top-10.

**Would it give useful insight?** Yes, moderately, as a secondary column. It removes the artefact in the "audience more positive"
direction (war and news posts with very negative wording) and shows which divergences are surprising for that kind of post.
Limits: it explains under a fifth of the variance; it adds a second number analysts must interpret; the baseline is corpus-specific
and will shift as the corpus changes (re-fit and version it as `_v3`); the extremes are thin (122 posts above +0.4), so
the band means there are noisy. **Recommendation:** adopt only after a judged sample of 50–100 flagged posts (raw-only, residual-only,
both) shows that residual-only posts are more often genuine divergences than raw-only posts. Until then keep the raw gap, the sample
size and the explanation shown on the dashboard.
