# Instagram Analytics — Looker Studio Dashboard: Production Specification

**Status:** Production-ready spec. Consolidates the Phase-1 schema audit, Phase-2 dashboard design, and Phase-3 calculated-fields + connector performance review.
**Connector:** `connector/instagram_connector.gs` → Django `/api/instagram-analytics/?format=dashboard`
**Backend source of truth:** `apps/analytics/services/instagram_service.py`, `instagram_utils.py`, `instagram_sync.py`
**Graph API target:** Facebook-Login path, v24.0/v25.0, `views`-based metric set.

---

## 0. Ground rules — read before building anything

These three data-reality constraints (proven in Phase 1 against the backend code) gate what is chartable. Every downstream decision in this doc obeys them.

1. **`Row Type` is the master discriminator.** The connector returns one flat superset where `daily`, `post`, `reel`, `story`, `country`, `city`, `comparison`, and `summary` rows coexist. **Every chart must carry a chart-level filter on `Row Type`**, or metrics mix and double-count.
2. **Only `Reach` has a real daily series.** `Views`, `Content Interactions`, `Accounts Engaged`, `Engagement Rate` are `total_value`-only on the Graph API (`ACCOUNT_TIMESERIES_METRICS = ("reach",)`). They are **0 on every `daily`/`weekly`/`monthly`/`comparison` row**; real values live only on the `summary` row (account window total) and on `post`/`reel`/`story` rows (per-media). ➡️ *You cannot trend Views/Interactions/Engagement over time until the backend exposes them as a daily series.*
3. **Comparison %s are trustworthy only for Reach.** `MoM/WoW Views/Interactions/Engagement %` derive from the reach-only daily series → structurally ~0. Only **Reach %** is safe. `Follower Growth` maps to `follows_and_unfollows` = churn, not net, until the `follow_type` fix lands.

---

## 1. Final field mapping table (schema ↔ Graph API)

Endpoint key: **`/{ig-user}/insights`** = `GET graph.facebook.com/v24.0/{ig-user-id}/insights`; **`/{media}/insights`** = per-media insights; **user node** = `GET /{ig-user-id}?fields=…`; **derived/computed** = produced in `instagram_utils.py`/`instagram_sync.py`.

### Dimensions

| Field (display) | id | Graph API source | Status |
|---|---|---|---|
| Row Type | `row_type` | assembler (`build_instagram_dashboard_rows`) | Keep |
| Date | `date` | insight `end_time` / media `timestamp` | Keep |
| Year Month | `year_month` | derived `YYYY-MM` from `date` | Fix (use YEAR_MONTH type / `YYYYMM`) |
| Account ID | `organization_id` | `ig_user_id` | Keep |
| Media Type | `media_type` | media field `media_type` (IMAGE/VIDEO/CAROUSEL_ALBUM) | Keep |
| Content Type | `contentType` | media field `media_product_type` (FEED/REELS/STORY/AD) | Keep |
| Media ID | `postId` | media field `id` | Keep |
| Caption | `postText` | media field `caption` | Keep |
| Permalink | `permalink` | media field `permalink` | Keep |
| Audience Segment Type | `segment_type` | `follower_demographics` label | Keep |
| Audience Segment | `segment` | `follower_demographics` dimension value | Keep |
| Audience Segment Label | `segment_label` | derived | Keep |
| Country | `country_name` | `/{ig-user}/insights` `follower_demographics` `breakdown=country` | Keep |
| City | `city` | `follower_demographics` `breakdown=city` | Keep |
| Post Date | `postDate` | media field `timestamp` | Keep |
| Period | `period` | assembler (weekly/monthly bucket key) | Keep |
| Period Type | `period_type` | assembler | Keep |
| Comparison Period / Label | `comparison_period`, `comparison_label` | `comparisons.*` | Keep |
| Current/Previous Start/End | `current_start` … `previous_end` | `build_period_comparison` | Keep |
| Sync Start/End Date | `sync_start_date`, `sync_end_date` | `sync_range` | Keep |
| Sync Granularity | `sync_granularity` | `sync_range.granularity` | Keep |
| Week Trend | `week_trend` | `comparisons.week_over_week.changes.reach_trend` | Keep (reach-trend only) |
| Month Trend | `month_trend` | `comparisons.month_over_month.changes.reach_trend` | Keep (reach-trend only) |
| Insight Section / Insight | `insight_section`, `insight` | `performance_insights` | Keep |
| ~~Metric Mode~~ | `metric_mode` | assembler | **Remove** (always == Row Type) |

### Metrics

| Field (display) | id | Graph API source | Status |
|---|---|---|---|
| Reach | `reach` | `/{ig-user}/insights` reach (`total_value`+`time_series`) & `/{media}/insights` reach | Keep ⚠ (unique count; SUM approximates) |
| Views | `views` | `/{ig-user}/insights` views (`total_value`) & `/{media}/insights` views | Keep ⚠ (0 on daily/period rows) |
| Likes | `likes` | `/{media}/insights` likes → `like_count` fallback | Keep |
| Comments | `comments` | `/{media}/insights` comments → `comments_count` | Keep |
| Shares | `shares` | `/{media}/insights` shares (covers Story Shares) | Keep |
| Saves | `saved` | `/{media}/insights` saved | Keep |
| Content Interactions | `total_interactions` | `/{ig-user}/insights` & `/{media}/insights` total_interactions | Keep ⚠ (0 on daily rows) |
| Accounts Engaged | `accounts_engaged` | `/{ig-user}/insights` accounts_engaged (`total_value`) | Fix (unique count → MAX or summary-only; 0 on daily) |
| Engagement Rate | `engagement_rate` | **computed** `total_interactions/reach` | Fix (recompute as calc field; see §3) |
| Followers | `followers` | user node `followers_count` | Keep (MAX) |
| Follower Growth | `followers_gained` | `/{ig-user}/insights` follows_and_unfollows (`total_value`) | Fix (churn≠net; add `follow_type` breakdown) |
| Story Replies | `replies` | `/{media}/insights` replies (STORY) | Keep (0 in EU/JP) |
| Story Navigation | `navigation` | `/{media}/insights` navigation (STORY, consolidated) | Keep |
| Reel Avg Watch Time | `avg_watch_time` | `/{media}/insights` ig_reels_avg_watch_time (REELS) | Fix (reels-only calc field) |
| MoM/WoW Reach Change % | `month_reach_change_pct`, `week_reach_change_pct` | `comparisons.*.changes.reach_change_pct` | Keep ✔ |
| MoM/WoW Views Change % | `*_views_change_pct` | comparisons | Fix/Remove (derived from reach-only daily → ~0) |
| MoM/WoW Interactions Change % | `*_total_interactions_change_pct` | comparisons | Fix/Remove (~0) |
| MoM/WoW Engagement Change % | `*_engagement_change_pct` | comparisons | Fix/Remove (~0) |
| MoM/WoW Follower Growth Change % | `*_followers_change_pct` | comparisons | Fix (depends on churn mapping) |
| ~~Total Engagements~~ | `total_engagements` | overview only (== total_interactions) | **Remove** (duplicate) |

### Not available (do not attempt — removed by Meta or absent)

Profile Views · Profile Visits · Website/Email/Phone Clicks · Impressions · Video Plays · Reel Plays (all → `views`) · Reel Completion Rate · CTR (no click/impression field). **Accounts Reached** = `reach`; **Story Shares** = `shares` (already present).

### Addable (needs connector work — endpoints identified)

| Metric | Endpoint to add |
|---|---|
| Audience Age | `/{ig-user}/insights?metric=follower_demographics&period=lifetime&metric_type=total_value&breakdown=age` |
| Audience Gender | `…&breakdown=gender` |
| Story Exits / Taps Fwd / Taps Back / Next Story | `/{media}/insights?metric=navigation&breakdown=story_navigation_action_type` |

---

## 2. Final schema (dimensions / metrics / types / aggregations)

**Changes from the original schema:** remove `metric_mode` and `total_engagements` (duplicates); change `accounts_engaged` aggregation to MAX; keep the stored `engagement_rate` but prefer the §3 calc field; fix `year_month` to a YEAR_MONTH type; the non-reach comparison %s are retained in schema but hidden in the UI.

### Dimensions (final)

| Field | Data type | Notes |
|---|---|---|
| Row Type | TEXT | required filter on every chart |
| Date, Post Date, Sync Start/End, Current/Previous Start/End | YEAR_MONTH_DAY | `ymd()` → `YYYYMMDD` |
| Year Month | **YEAR_MONTH** (`YYYYMM`) | changed from TEXT |
| Account ID, Media Type, Content Type, Media ID, Caption, Permalink | TEXT | |
| Audience Segment / Type / Label, Country, City | TEXT | Country geocodable; City not (no country context) |
| Period, Period Type, Comparison Period/Label, Sync Granularity | TEXT | |
| Week Trend, Month Trend | TEXT | reach-trend only |
| Insight Section, Insight | TEXT | |

### Metrics (final)

| Field | Data type | Aggregation | Notes |
|---|---|---|---|
| Reach | NUMBER | SUM | unique count; SUM is an accepted approximation |
| Views | NUMBER | SUM | real on post/summary only |
| Likes, Comments, Shares, Saves | NUMBER | SUM | per-media |
| Content Interactions | NUMBER | SUM | real on post/summary only |
| Accounts Engaged | NUMBER | **MAX** | changed from SUM (unique count) |
| Engagement Rate | NUMBER | AVG | prefer §3 calc field instead |
| Followers | NUMBER | MAX | snapshot |
| Follower Growth | NUMBER | SUM | churn caveat |
| Story Replies, Story Navigation | NUMBER | SUM | story rows |
| Reel Avg Watch Time | NUMBER | AVG | reels-only via calc field |
| MoM/WoW Reach Change % | NUMBER | AVG | only reliable comparison metric |
| MoM/WoW Views/Interactions/Engagement/Follower % | NUMBER | AVG | hidden in UI (unreliable) |

---

## 3. Calculated fields (Looker Studio formulas)

**Setup:** ratios are aggregated metrics (`SUM(x)/SUM(y)`). Set the component fields' aggregation to **None** in the data source to avoid *"aggregation within an aggregation."* Leave results as 0–1 ratios and set field **Type = Percent**.

```
-- Total Engagement (replaces removed duplicate)
Content Interactions

-- Engagement Rate (interactions per account reached)
CASE WHEN SUM(Reach)=0 THEN NULL ELSE SUM(Content Interactions)/SUM(Reach) END

-- Like / Comment / Share / Save Rate (share of reach)
CASE WHEN SUM(Reach)=0 THEN NULL ELSE SUM(Likes)/SUM(Reach) END
CASE WHEN SUM(Reach)=0 THEN NULL ELSE SUM(Comments)/SUM(Reach) END
CASE WHEN SUM(Reach)=0 THEN NULL ELSE SUM(Shares)/SUM(Reach) END
CASE WHEN SUM(Reach)=0 THEN NULL ELSE SUM(Saves)/SUM(Reach) END

-- Reach Rate (% of followers reached; scope to Row Type=summary)
CASE WHEN MAX(Followers)=0 THEN NULL ELSE SUM(Reach)/MAX(Followers) END

-- Avg per Post (scope to Row Type IN post,reel)
SUM(Reach)/COUNT_DISTINCT(Media ID)
SUM(Views)/COUNT_DISTINCT(Media ID)
SUM(Content Interactions)/COUNT_DISTINCT(Media ID)

-- Virality Score (shares as fraction of reach; +saves variant)
CASE WHEN SUM(Reach)=0 THEN NULL ELSE SUM(Shares)/SUM(Reach) END
CASE WHEN SUM(Reach)=0 THEN NULL ELSE (SUM(Shares)+SUM(Saves))/SUM(Reach) END

-- Interaction Rate (interactions per view; distinct from Engagement Rate)
CASE WHEN SUM(Views)=0 THEN NULL ELSE SUM(Content Interactions)/SUM(Views) END

-- Follower Growth % (net over starting base) — churn caveat applies
CASE WHEN (MAX(Followers)-SUM(Follower Growth))=0 THEN NULL
     ELSE SUM(Follower Growth)/(MAX(Followers)-SUM(Follower Growth)) END

-- Avg Watch Time (Reels only)
CASE WHEN Row Type="reel" THEN Reel Avg Watch Time END
```

**CTR — not possible.** No click-type or impressions field exists in the corrected schema (Meta removed all click metrics; impressions → views). Document as "not available," do not substitute a proxy.

---

## 4. Dashboard wireframes (6 pages)

**Report-level controls on every page:** Date range (default *Last 28 days*), Account ID drop-down.

### Page 1 — Executive Overview  (KPIs → `Row Type=summary`; trend → `Row Type=daily`)
```
┌ INSTAGRAM EXECUTIVE OVERVIEW        [Account ▾]  [ Date range ▾ ] ┐
│ [Followers] [Follower Growth⚠] [Reach] [Views] [Eng Rate*] [Acct Engaged] [Total Eng] │  ← 7 scorecards
├───────────────────────────────────────┬──────────────────────────┤
│ REACH OVER TIME (time series, daily)   │ REACH vs PRIOR (comp card)│
│ CUMULATIVE REACH (area, running total) │ MoM Reach %✔ + Month Trend│
└───────────────────────────────────────┴──────────────────────────┘
```
🚩 Scorecard deltas / comparison reliable **for Reach only**; disable "vs previous" on Views/Interactions/Engagement. Follower Growth = churn. Time series & area = Reach only.

### Page 2 — Content Performance  (all tiles → `Row Type IN post,reel`)
```
┌ CONTENT PERFORMANCE   [Media Type ▾][Content Type ▾]  [ Date range ▾ ] ┐
│ TOP POSTS table (heatmap on ER%): Caption|Post Date|Media Type|Reach|  │
│   Views|Likes|Comments|Shares|Saves|Total Eng|Eng Rate                 │
├──────────────────┬───────────────────────┬─────────────────────────────┤
│ TOP 10 BY REACH  │ REACH vs ENGAGEMENT    │ TOP BY VIEWS (bar)          │
│ (bar)            │ (scatter, bubble=Views)│                             │
└──────────────────┴───────────────────────┴─────────────────────────────┘
```
✅ Fully supported. Table is cross-filter master; bars drill `Content Type ▸ Media Type ▸ Caption`.

### Page 3 — Audience Insights  (country → `Row Type=country`; city → `Row Type=city`)
```
┌ AUDIENCE INSIGHTS                                   [ Date range ▾ ] ┐
│ FOLLOWERS BY COUNTRY (geo map)  │ FOLLOWERS BY COUNTRY (pie, top 8)  │
├─────────────────────────────────┼────────────────────────────────────┤
│ FOLLOWERS BY CITY (bar, top 15) │ AUDIENCE AGE 🚩 / GENDER 🚩         │
├─────────────────────────────────┴────────────────────────────────────┤
│ AUDIENCE SEGMENTS (treemap): Segment Type ▸ Segment Label / Followers  │
└───────────────────────────────────────────────────────────────────────┘
```
🚩 Age & Gender **not in schema** (needs connector additions). City = bar not map (no country context). Demographics are lifetime + top-45 + <100-follower floor.

### Page 4 — Engagement Analytics  (breakdown tiles → `Row Type IN post,reel`)
```
┌ ENGAGEMENT ANALYTICS   [Content Type ▾]            [ Date range ▾ ] ┐
│ ENGAGEMENT MIX by type (100% stacked) │ ENG RATE by type (bar)      │
├───────────────────────────────────────┴─────────────────────────────┤
│ ENGAGEMENT HEATMAP (pivot: Post Date × Content Type, Total Eng)      │
├─────────────────────────────────────────────────────────────────────┤
│ ENGAGEMENT COMPONENTS PER POST (stacked bar by Post Date)           │
└─────────────────────────────────────────────────────────────────────┘
```
🚩 Account-level engagement *time series* not possible (metrics are total_value-only). Per-post time-ordered bar used instead.

### Page 5 — Stories & Reels  (story → `Row Type=story`; reel → `Row Type=reel`)
```
┌ STORIES & REELS                                     [ Date range ▾ ] ┐
│ REELS: Views(bar)+Avg Watch Time(line) combo │ REELS: Views trend    │
├──────────────────────────────────────────────┼───────────────────────┤
│ STORIES: Replies vs Navigation (column)       │ STORIES: Views & Reach│
├──────────────────────────────────────────────┴───────────────────────┤
│ REEL LEADERBOARD (table): Caption|Views|Reach|Avg Watch Time|ER%     │
└───────────────────────────────────────────────────────────────────────┘
```
✅ Per-media rows are real. 🚩 Story Navigation is consolidated (exits/taps need breakdown); Story Replies 0 in EU/JP.

### Page 6 — Growth Analytics  (growth → `Row Type=daily`; comparison → `Row Type=comparison`)
```
┌ GROWTH ANALYTICS                                    [ Date range ▾ ] ┐
│ FOLLOWERS(line,MAX)+GROWTH(bar) combo │ MoM CHANGE %: Reach✔ Views🚩 │
│                                        │  Eng🚩 Followers⚠            │
├────────────────────────────────────────┼──────────────────────────────┤
│ MONTHLY REACH (line, by Year Month)    │ WoW vs MoM (comparison cards)│
├────────────────────────────────────────┴──────────────────────────────┤
│ FOLLOWER GROWTH "WATERFALL" 🚩 (no native viz → running-total bar)    │
└─────────────────────────────────────────────────────────────────────────┘
```
🚩 No native waterfall (use running-total bar / community viz). Only MoM Reach % reliable. Follower Growth = churn. Fix `Year Month` type for correct axis sort.

---

## 5. Chart recommendations per page

| Page | Chart | Type | Key fields | Row Type |
|---|---|---|---|---|
| 1 | KPI row | Scorecard ×7 | Followers(MAX), Follower Growth, Reach, Views, Engagement Rate(calc), Accounts Engaged, Total Engagement(calc) | summary |
| 1 | Reach trend | Time series (line) | Date × Reach | daily |
| 1 | Cumulative reach | Area (running total) | Date × Reach | daily |
| 1 | Reach comparison | Comparison/bullet card | MoM Reach %, Month Trend | comparison |
| 2 | Top posts | Table + conditional fmt | Caption, Post Date, Media Type, Reach, Views, Likes, Comments, Shares, Saves, Total Eng, ER% | post, reel |
| 2 | Top 10 reach / views | Horizontal bar | Caption × Reach / Views | post, reel |
| 2 | Reach vs engagement | Scatter (bubble) | Reach × Total Eng, size=Views, color=Content Type | post, reel |
| 3 | Country | Geo map (filled) + pie | Country × Followers | country |
| 3 | City | Bar (top 15) | City × Followers | city |
| 3 | Segments | Treemap | Segment Type ▸ Label × Followers | country+city |
| 4 | Engagement mix | 100% stacked column | Content Type × Likes/Comments/Shares/Saves | post, reel |
| 4 | ER by type | Bar | Content Type × Engagement Rate(calc) | post, reel |
| 4 | Heatmap | Pivot + heatmap | Post Date × Content Type × Total Eng | post, reel |
| 5 | Reels views+watch | Combo (bar+line) | Post Date × Views + Avg Watch Time(reels) | reel |
| 5 | Story replies/nav | Grouped column | Post Date × Replies/Navigation | story |
| 5 | Reel leaderboard | Table | Caption, Views, Reach, Avg Watch Time, ER% | reel |
| 6 | Followers + growth | Combo (line+bar) | Date × Followers(MAX)+Follower Growth | daily |
| 6 | Monthly reach | Line | Year Month × Reach | monthly |
| 6 | Growth waterfall | Running-total bar (fallback) | Year Month × Follower Growth | monthly |

---

## 6. API / connector code fixes

### Connector (`instagram_connector.gs`)

**C1 — Remove duplicate fields.** Delete `metric_mode` (always == `row_type`) and `total_engagements` (== `total_interactions`) from `getFields`, `TEXT_FIELDS`/`NUMBER_FIELDS`, and the setters.

**C2 — Cache silently fails > 100 KB → chunk + hash the key.**
```js
var CACHE_TTL_SECONDS = 300, CACHE_CHUNK_BYTES = 90 * 1024;
function cachePutLarge(cache, key, value, ttl) {
  var entries = {}, n = 0;
  for (var i = 0; i < value.length; i += CACHE_CHUNK_BYTES, n++) entries[key+"."+n] = value.slice(i, i+CACHE_CHUNK_BYTES);
  entries[key+".meta"] = String(n); cache.putAll(entries, ttl);
}
function cacheGetLarge(cache, key) {
  var count = cache.get(key+".meta"); if (!count) return null;
  var parts = [];
  for (var i = 0; i < Number(count); i++) { var p = cache.get(key+"."+i); if (p===null) return null; parts.push(p); }
  return parts.join("");
}
```

**C3 — Retry transient errors + surface hard failures.**
```js
var MAX_FETCH_ATTEMPTS = 3;
function fetchWithRetry(url) {
  for (var a = 1; ; a++) {
    var r = UrlFetchApp.fetch(url, { headers: { "Accept": "application/json" }, muteHttpExceptions: true });
    var code = r.getResponseCode();
    if (code < 400) return r;
    if ((code === 429 || code >= 500) && a < MAX_FETCH_ATTEMPTS) { Utilities.sleep(Math.pow(2, a) * 500); continue; }
    return r;
  }
}
function fetchDashboard(request) {
  var url = buildUrl(request), cache = CacheService.getScriptCache();
  var key = "ig:" + Utilities.base64EncodeWebSafe(Utilities.computeDigest(Utilities.DigestAlgorithm.MD5, url));
  var cached = cacheGetLarge(cache, key);
  if (cached) { try { return JSON.parse(cached); } catch (e) {} }
  var r = fetchWithRetry(url), code = r.getResponseCode();
  if (code >= 400) cc.newUserError().setText("Instagram analytics service unavailable (HTTP "+code+"). Retry shortly.")
       .setDebugText("GET "+url+" -> "+code+" :: "+r.getContentText().slice(0,500)).throwException();
  var body = r.getContentText();
  try { var p = JSON.parse(body); try { cachePutLarge(cache, key, JSON.stringify(p), CACHE_TTL_SECONDS); } catch (e) {} return p; }
  catch (e) { cc.newUserError().setText("Invalid response from analytics service.")
       .setDebugText("JSON parse failed: "+e+" :: "+body.slice(0,500)).throwException(); }
}
```

**C4 — Short-circuit empty payloads** in `normalizeRows` (avoid building comparison/insight rows for nothing).

**C5 — Drop dead `ngrok-skip-browser-warning` header; add `account_id` to `buildUrl`** so the ScriptCache key can't collide once multiple accounts exist (ties to the Phase-1 cache-key finding).

### Backend correctness (surfaced by the connector)

**C6 — Follower Growth:** map net growth via `follows_and_unfollows` `breakdown=follow_type` (follows − unfollows) or `follower_count` deltas, not the raw total_value (`instagram_sync.py:158`).

**C7 — Meaningful comparisons:** feed `build_comparisons` real per-period totals, not the reach-only `daily_metrics` (`instagram_sync.py:194`), so non-reach MoM/WoW %s stop reading ~0.

**C8 — Pagination truncation:** `_get_paginated` stops at `page > 100` silently — add a `warn(...)` when the cap is hit (`instagram_service.py:146`).

---

## 7. Performance improvements

| # | Area | Where | Action |
|---|---|---|---|
| P1 | **Batch media insights (biggest win)** | `_media_insights_by_id` (N+1: one `/insights` call per media) | Use Graph API batch (`POST ?batch=[…]`, ≤50 sub-requests) — turns 200 calls into ~4 |
| P2 | Connector cache | `fetchDashboard` | Chunked + hashed cache (C2) so payloads > 100 KB actually cache |
| P3 | Parallel backend fetch | `collect_instagram_analytics` | Run independent calls (account, country/city demographics, media) via `ThreadPoolExecutor` |
| P4 | Incremental sync | `collect_instagram_analytics` | Add a watermark (last-synced media id/timestamp); only fetch insights for new/updated media |
| P5 | Connector CPU | `normalizeRows` | Build only the row groups whose fields/row-types are requested; early-return on empty |
| P6 | Rate-limit safety | `base_service._request` | Retry/backoff already present; P1 removes the main throttling risk (N+1 loop) |

Token refresh (`ensure_fresh_token` proactive + reactive on 190/401) is **solid — no change needed**.

---

## 8. Looker Studio UX best practices

**Filters & controls**
- Make Date range + Account ID **report-level** so they persist across pages.
- Set every chart's **chart-level `Row Type` filter** (§0 rule) — this is non-negotiable for correctness.
- Add page-scoped controls where they help: Media Type / Content Type (Pages 2, 4), Comparison Period (Page 6).
- Note that demographics (Page 3) are `lifetime` and **do not respond to the date filter** — label them so viewers don't expect them to move.

**Drill-down & cross-filter**
- Enable **cross-filtering** on the Page-2 top-posts table (master) and the Page-4 stacked charts.
- Configure **drill-down hierarchies**: `Content Type ▸ Media Type ▸ Caption` (content); `Segment Type ▸ Segment Label` (audience); `Year Month ▸ Date` (time).
- Make Caption link out via `Permalink` (URL field / hyperlink formatting).

**Formatting**
- Ratio calc fields → **Type = Percent**; large counts → compact ("12.4K").
- Conditional formatting/heatmap on Engagement Rate (Page 2 table, Page 4 heatmap).
- Consistent color: one accent per metric family (reach=blue, engagement=green, growth=purple); reserve red/amber for negative deltas.
- Pin the ~2-year Meta window and the Apr-2025 `views` discontinuity as **text-box footnotes** on Pages 1 & 6.
- Hide the unreliable non-reach comparison %s from the field picker (or move to a clearly-labeled "experimental" section) so analysts don't build on ~0 values.

**Layout**
- Fixed canvas, KPI row across the top, heaviest table/chart below the fold.
- Add a persistent header band with report title + "Data as of {Sync End Date}" using the `Sync End Date` field.

---

## 9. Production readiness checklist

**Data & backend**
- [ ] C6 Follower Growth mapped to net (`follow_type`) — verified against a known account
- [ ] C7 Comparisons fed real per-period totals (non-reach MoM/WoW no longer ~0)
- [ ] C8 Pagination truncation warns instead of silently dropping media
- [ ] P1 Media insights batched (verified call count drop)
- [ ] P4 Incremental sync watermark in place
- [ ] Token refresh confirmed working; 60-day re-auth runbook documented
- [ ] `<100-follower` suppression + top-45 demographics handled gracefully (empty, not error)

**Connector**
- [ ] C1 `metric_mode` + `total_engagements` removed from schema
- [ ] C2 Chunked/hashed cache verified with a >100 KB payload
- [ ] C3 Retry + `cc.newUserError` on hard failures (no more silent empty dashboards)
- [ ] C4/C5 Empty-payload short-circuit; dead header removed; `account_id` in URL
- [ ] `year_month` emitted as `YYYYMM` (YEAR_MONTH type)

**Dashboard**
- [ ] All 25+ tiles carry a chart-level `Row Type` filter
- [ ] Calculated fields (§3) created; component fields set to aggregation = None
- [ ] Report-level Date + Account controls; cross-filter/drill-down configured
- [ ] Unreliable metrics hidden; footnotes for `views` discontinuity + lifetime demographics
- [ ] Page 3 Age/Gender either shipped (connector added the breakdowns) or shown as labeled "coming soon" placeholders — not silently blank

**Ops**
- [ ] Backend `/api/instagram-analytics/` behind auth or IP allowlist (currently `AuthType.NONE`)
- [ ] Graph API version pinned to v24.0/v25.0 (v20.0 sunsets 2026-09-24)
- [ ] Monitoring/alert on sync failures and token expiry
- [ ] Dashboard load-tested on the largest expected account (many posts) within Apps Script's 6-min limit

---

*Compiled from Phase 1 (schema audit), Phase 2 (dashboard design), Phase 3a (calculated fields), Phase 3b (connector performance review).*
