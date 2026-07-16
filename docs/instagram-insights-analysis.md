# Instagram Insights — Looker Studio Connector: Phase 1 Analysis

**Status:** Analysis only. No application code has been created, modified, or refactored for this report.
**Analysis date:** 2026-07-14
**Objective under evaluation:** Extend the existing LinkedIn Looker Studio custom connector to also serve **Instagram Insights**, reusing the established architecture, and doing so in a way that makes adding further platforms (Facebook, X, YouTube) low-effort.

> **Two verification caveats up front — read these first.**
> 1. **No Meta/Instagram credentials exist in this repository.** `.env` contains only `LINKEDIN_*`, `MONGO_URI`, and `OPENAI_API_KEY` — no Facebook App ID/secret, no Instagram Business Account ID, no Meta access token. Unlike the LinkedIn Phase‑1 report (which live-introspected a real token), **the actual granted scopes and account setup for an Instagram app could not be live-verified**, because no such app is wired up yet. Every scope/permission statement below is "what the Graph API requires," not "what this app has been granted." Creating and review-approving a Meta app is a **manual prerequisite** (see §4.5, §4.7).
> 2. **Meta Graph API capabilities in §2 were verified against Meta's official docs** (developers.facebook.com) as of 2026-07-14. Meta deprecates metrics aggressively; several metrics on your requested list **no longer exist**. Those are reported explicitly in §2.4 — none are silently dropped or substituted.

---

## 1. LinkedIn architecture reuse analysis

The current pipeline (verified by reading the source, not the older doc — the code has since gained a fetch‑through cache and an extracted `analytics_sync` module):

```
Meta/LinkedIn API ─► collect_*_analytics (sync) ─► analytics_utils (normalize)
                                                          │
                          ┌───────────────────────────────┤
        management cmd ───┘                               ▼
     (sync_linkedin)                        MongoDB linkedin_db
                                          (analytics, tokens, dashboard_cache)
                                                          │
 Looker date range ─► looker_connector.gs ─► GET /api/linkedin-analytics/?format=dashboard
                                                          │
                                    mongo_service.get_dashboard_analytics
                                    (fetch-through cache; live fetch on miss)
                                                          │
                                    connector normalizeRows() ─► Looker rows
```

### 1.1 Component-by-component verdict

| Layer | File(s) / symbols | Reuse verdict | Rationale |
|---|---|---|---|
| **HTTP transport** (retry/backoff on 429/5xx, 401→refresh, timeout) | `linkedin_service.py` `_request`, `_get`, `_get_raw_query` | **Generalize** → extract a base client | The retry/backoff/refresh loop is entirely platform-agnostic. Only the header set, base URLs, and the privacy-cost special case are LinkedIn-specific. |
| **Pagination** | `linkedin_service.py` `_get_paginated` (offset/`start`+`count`) | **Platform-specific** | Meta uses **cursor-based** paging (`paging.next` / `after` cursors), not offset. A new `_get_paginated` variant is required in the Instagram client. |
| **Auth — interactive OAuth** | `views.py` `linkedin_login` / `linkedin_callback` | **Platform-specific impl, reusable pattern** | Meta uses different authorize/token endpoints, a **long-lived-token exchange** step, and an **IG-Business-Account discovery** step (Page → connected IG account). The "redirect → exchange code → persist token to Mongo" *shape* is reusable; the code is not. |
| **Auth — token refresh** | `auth_service.py` `refresh_access_token` | **Platform-specific impl, reusable pattern** | LinkedIn uses `grant_type=refresh_token` with rotation. Meta long-lived tokens refresh via `GET graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token` (or the FB `fb_exchange_token` flow) with a 60-day lifecycle. Different mechanism; same "persist newest token to Mongo, read latest" pattern. |
| **Token storage** | `mongo_service.py` `save_token`, `get_latest_access_token`, `get_latest_refresh_token`; `tokens` collection | **Generalize** (add `platform` field) | Today `tokens` is single-tenant/LinkedIn-only. Add a `platform` discriminator and filter reads by it. |
| **API client — domain methods** | `linkedin_service.py` `get_share_statistics`, `get_follower_statistics`, `get_page_statistics`, `get_organization_posts`, URN helpers | **Platform-specific** | Endpoints, URN construction, `Linkedin-Version` header, `TABLE_MAX_PRIVACY_COST_EXCEEDED` are all LinkedIn-only. A sibling `InstagramService` is required. |
| **Normalization — math/utilities** | `analytics_utils.py`: `safe_number`, `safe_int`, `percent_change`, `trend_direction`, `parse_date`, `default_date_range`, `date_from_ms`, `rows_between`, `aggregate_metrics`, `aggregate_by_period`, `build_period_comparison`, `build_comparisons`, `merge_daily_rows`, `top_post`, `string_value` | **Reusable as-is** | Pure, tested, platform-neutral. WoW/MoM comparison and period bucketing apply unchanged. |
| **Normalization — metric field set** | `analytics_utils.py` `METRIC_FIELDS`, `engagement_rate`, `ctr_percent`, `total_engagements` | **Generalize** | `METRIC_FIELDS` hardcodes LinkedIn's columns (`impressions`, `clicks`, `careers_page_views`…). `engagement_rate`/`ctr_percent` assume `impressions`+`clicks`, which **Instagram no longer has** (see §2.4). Needs a per-platform metric-field set + per-platform engagement formula. |
| **Normalization — time params** | `analytics_utils.py` `linkedin_time_intervals`, `linkedin_time_interval_params` | **Platform-specific** | LinkedIn uses millisecond `timeRange` tuples. Meta uses `since`/`until` **Unix seconds** + a `period` enum (`day`, `week`, `days_28`, `lifetime`). New helpers required. |
| **Normalization — response extractors** | `analytics_utils.py` `extract_share_metrics`, `extract_page_metrics`, `normalize_*_time_series` | **Platform-specific** | Bound to LinkedIn JSON shapes. Instagram's `/insights` returns `data[].values[].value` (sometimes keyed breakdowns); needs its own extractors. |
| **Normalization — audience/geo** | `analytics_utils.py` `AUDIENCE_BREAKDOWNS`, `LINKEDIN_COUNTRY_NAMES`, `resolve_country_name`, `resolve_industry_name` | **Platform-specific, concept reusable** | LinkedIn returns geo/industry **URNs** needing a lookup table. Instagram `follower_demographics` returns human-readable country/city already. The "emit one audience row per segment" concept generalizes; the URN maps don't apply. |
| **Normalization — content type** | `analytics_utils.py` `POST_CONTENT_TYPES`, `derive_content_type`, `canonical_content_type` | **Platform-specific mapping** | LinkedIn types (ARTICLE/IMAGE/VIDEO/POST) differ from Instagram's `media_product_type` (FEED/REELS/STORY/AD) × `media_type` (IMAGE/VIDEO/CAROUSEL_ALBUM). |
| **Normalization — row assembly** | `analytics_utils.py` `build_dashboard_rows`, `normalize_post_rows` (skeleton), `summarize_performance` | **Generalize** | Row-tagging (`row_type`) and post-row assembly are largely generic once the extractors feed them. `summarize_performance` uses `impressions`/`engagement_rate` keys — parameterize the ranking metric. |
| **Collection orchestration** | `analytics_sync.py` `collect_linkedin_analytics` | **Platform-specific impl, reusable pattern** | The "fetch N endpoints via `_fetch_or_empty` → merge daily → posts → audience → assemble doc" pattern is exactly what Instagram needs; the endpoint calls and the output doc's metric keys differ. Add `collect_instagram_analytics` registered in a registry (§4.3). |
| **Storage + fetch-through cache** | `mongo_service.py` `save_analytics`, `get_dashboard_analytics`, `_get_cached_dashboard`, `_store_cached_dashboard`, `_fetch_lock`, `_ensure_indexes` | **Generalize** (critical) | Cache key is `(start, end, granularity)` with **no platform/account dimension** — LinkedIn and Instagram requests for the same date range would **collide in the cache**. `db = client["linkedin_db"]` is a hardcoded DB name, and `get_dashboard_analytics` hardcodes `from .analytics_sync import collect_linkedin_analytics`. All three must become platform-aware. |
| **HTTP view + routing** | `views.py` `linkedin_analytics`; `urls.py` `/api/linkedin-analytics/` | **Generalize** | Add a platform dimension (either a `platform` path segment / query param, or a parallel `instagram-analytics` route). Keep the existing LinkedIn route working verbatim for zero regression. Also note the view has **no auth** (`AuthType.NONE`) — carried forward, not worsened. |
| **Looker connector** | `looker_connector.gs` | **Generalize or clone (decision in §4.6)** | Schema is a flat LinkedIn superset; fetch/flatten/`row_type` machinery and field-filtering are reusable. Instagram needs new fields (`views`, `saved`, `media_type`, `city`, reel/story metrics) and new row types (`reel`, `story`, `country`). |
| **Config** | `settings.py` (`LINKEDIN_*`, `DASHBOARD_*`) | **Generalize** | Add `META_*`/`INSTAGRAM_*` env vars and platform-namespaced settings; the `DASHBOARD_LIVE_FETCH`/`DASHBOARD_CACHE_TTL_SECONDS` knobs are already platform-neutral. |
| **Scheduling / CLI** | `scheduler.py`, `management/commands/sync_linkedin.py` | **Generalize** | Turn `sync_linkedin` into `sync_analytics --platform <name>` (keep a thin `sync_linkedin` alias for the existing cron), and have the scheduler iterate registered platforms. |
| **Dead/uninvolved code** | `openai_service.py` (unused), SQLite `models.py` (unused) | **Ignore** | Not on any Instagram path. |

**Reuse summary:** roughly **the transport loop, all the aggregation/comparison math, the fetch-through cache mechanics, the row-type convention, and the connector's flatten pipeline are reusable** (as-is or generalized). The **auth flows, API-domain methods, response extractors, geo/content-type maps, and time-param builders are inherently platform-specific** and belong in an Instagram sibling module plus a small registry.

---

## 2. Meta Graph API verification

> Verified against Meta official docs on 2026-07-14. Where a claim rests on secondary sources, it is flagged. **The single most consequential fact:** Meta ran a large metrics cull in Oct 2024 → Apr 2025 that **removed** impressions, all profile-action click metrics, `video_views`, and `plays`, consolidating play/impression-style counts into one **`views`** metric. Several requested metrics therefore no longer exist.

### 2.1 Account setup & auth
- **Instagram Basic Display API is permanently shut down** (disabled 2024‑12‑04). Not an option.
- Two **current** paths:
  - **Instagram API with Facebook Login** — IG Business/Creator account **linked to a Facebook Page**; app user has admin access on the Page. This is the traditional Graph API path. **Story insights are only available on this path.**
  - **Instagram API with Instagram Login** (Business Login) — connect an IG professional account **without a Facebook Page**, via `graph.instagram.com`. Meta's recommended Basic-Display replacement.
- **Recommendation for this project: Instagram API with Facebook Login**, because (a) story insights require it and stories are on your requested list, and (b) the account-discovery model (Page → connected IG account ID) is closest to LinkedIn's org-ID model already in the code.

### 2.2 Long-lived token lifecycle
- Long-lived token: **valid 60 days**, refreshable for another 60 provided it is ≥24h old and unexpired.
- **A token not refreshed within 60 days expires unrecoverably** → user must re-authenticate. (Contrast: LinkedIn's own token also expires and this repo already has that exact failure mode — the pattern is familiar.)
- Refresh (IG Login path): `GET https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token&access_token={token}`. FB Login path uses the long-lived `fb_exchange_token` exchange.
- Operational tip (secondary/community, not a Meta rule): refresh around day ~50.

### 2.3 API version
- **Latest is v25.0** (released 2026‑02‑18); v24.0 (2025‑10‑08) also current.
- Already sunset: v18.0 (2026‑01‑26), v19.0 (2026‑05‑21). **v20.0 sunsets 2026‑09‑24** — within ~2 months of this analysis.
- **Target v24.0 or v25.0.** Anything ≤ v20.0 both sunsets imminently and still exposes now-removed metrics that error on newer content.
- ⚠️ *Secondary sources claimed a v23.0 EOL of 2026‑06‑09; Meta's own versions page lists it as TBD. Treat that specific date as unverified.*

### 2.4 Requested-metric verification (the important part)

Legend: ✅ available now · ⚠️ available but caveated/in-development · ❌ removed from the API (no substitute) · ↦ replaced by.

**Account-level**

| Requested metric | Verdict | Graph API detail |
|---|---|---|
| Followers | ✅ | `follower_count` (insight) / `followers_count` (user field). Suppressed for accounts < 100 followers. |
| Follower Growth | ✅ | `follows_and_unfollows` (`total_value`, `follow_type` breakdown); or derive from `follower_count` deltas. |
| Reach / Accounts Reached | ✅ | `reach` (`metric_type=total_value`; breakdowns `media_product_type`, `follow_type`). |
| Accounts Engaged | ✅ | `accounts_engaged` (`total_value`; estimated). |
| **Impressions** | ❌ **removed 2025‑04‑21** | ↦ **`views`**. Not numerically identical (views includes replays; different aggregation) → expect a trend discontinuity around Apr 2025. |
| **Profile Views** | ❌ **removed 2025‑01‑08** | No replacement. Cannot be shown. |
| **Website / Email / Phone / Text-Message clicks** | ❌ **removed 2025‑01‑08** | `website_clicks`, `email_contacts`, `phone_call_clicks`, `text_message_clicks`, `get_directions_clicks` all removed. No replacement. Cannot be shown. |
| Content Interactions | ✅ | `total_interactions` (`total_value`; `media_product_type` breakdown). |
| Engagement Rate | ⚠️ **not API-provided** | Must be **computed** server-side (e.g. `total_interactions / reach` or `/ follower_count`). The formula is convention, not Meta-specified. |
| *(bonus, available)* | ✅ | `views` (account, new in v22.0), `online_followers` (last 30 days, <100-follower floor). |

**Media / post-level** (`/{ig-media-id}/insights` + media fields)

| Requested metric | Verdict | Detail |
|---|---|---|
| Caption | ✅ | field `caption` |
| Media Type | ✅ | fields `media_type` (IMAGE/VIDEO/CAROUSEL_ALBUM) + `media_product_type` (FEED/REELS/STORY/AD) |
| Published Date | ✅ | field `timestamp` |
| Reach | ✅ | `reach` (FEED/REELS/STORY) |
| **Impressions** | ❌ **removed** | Gone for media created after 2024‑07‑02; errors on all versions after 2025‑04‑21. ↦ `views` |
| Likes | ✅ | `like_count` field or `likes` insight (organic only) |
| Comments | ✅ | `comments_count` field or `comments` insight |
| Shares | ✅ | `shares` insight |
| Saves | ✅ | `saved` insight |
| Engagement | ✅ (compute) | `total_interactions` provided; a rate must be computed |
| **Video Views** | ❌ **removed 2025‑01‑08** | ↦ `views` |

**Reels** (`media_product_type=REELS` media insights)

| Metric | Verdict | Detail |
|---|---|---|
| Views | ✅ | primary play metric now |
| Reach, Likes, Comments, Shares, Saves, Total Interactions | ✅ | standard media insights |
| `ig_reels_avg_watch_time` | ✅ | average play time |
| `ig_reels_video_view_total_time` | ⚠️ | reference flags "metric in development" — validate at runtime |
| **`plays` / `clips_replays_count` / `ig_reels_aggregated_all_plays_count`** | ❌ **removed 2025‑04‑21** | ↦ `views` (now includes replays) |

**Stories** (FB Login path only)

| Metric | Verdict | Detail |
|---|---|---|
| Reach, Views, Shares, Total Interactions | ✅ | |
| Replies | ✅ | returns `0` for creators in EU/Japan |
| Navigation | ✅ | consolidated; breakdown values `TAP_FORWARD`, `TAP_BACK`, `TAP_EXIT`, `SWIPE_FORWARD`, `AUTO_ADVANCE` |
| **Impressions** | ❌ removed 2025‑04‑21 | ↦ `views` |
| **`taps_forward` / `taps_back` / `exits`** | ❌ removed 2023‑09‑12 | ↦ `navigation` breakdown |

**Demographics**

| Metric | Verdict | Detail |
|---|---|---|
| `follower_demographics` (age / gender / **city** / **country**) | ✅ | `period=lifetime`, `metric_type=total_value`; **top 45 only**; <100-follower floor |
| `engaged_audience_demographics` | ✅ | `lifetime`+`total_value`; ≥100 engaged accounts floor |
| `reached_audience_demographics` | ✅ | same requirements |
| **`audience_country` / `audience_city`** (legacy names on your list) | ❌ removed 2023‑12‑11 | ↦ use `follower_demographics` country/city breakdowns |

### 2.5 Net mismatches to your requested list (nothing dropped silently)
- **Gone, no substitute — cannot appear on the dashboard:** Profile Views; Website/Email/Phone/Text-Message clicks. (These make LinkedIn's "page_views/careers_page_views/clicks/CTR %" columns *not* have Instagram equivalents.)
- **Renamed/consolidated — will be served as `views`:** Impressions, Video Views, Reel plays. Flag the Apr‑2025 discontinuity to dashboard users.
- **Computed, not fetched:** Engagement Rate.
- **Conditional:** Story insights require the Facebook-Login path; several metrics suppressed under 100 followers; demographics capped at top 45.

---

## 3. Looker Studio schema plan

Reuses the existing connector's **row-type + flat-superset** convention. New fields are **additive** — the LinkedIn schema is untouched, so the existing LinkedIn data source keeps working.

### 3.1 Dimensions

| Dimension | Field id | Source | Notes |
|---|---|---|---|
| Date | `date` (existing) | insight day / media timestamp | reused |
| Year Month | `year_month` (**new**) | derived `YYYY-MM` from `date` | new derived dimension (also computable in Looker; providing it server-side keeps parity with LinkedIn's period rows) |
| Row Type | `row_type` (existing) | assembler | values extended: `daily`, `monthly`, `country`, `post`, `reel`, `story`, `summary` |
| Media Type | `media_type` (**new**) OR reuse `contentType` | media fields | recommend a dedicated `media_type` for IG (FEED/REELS/STORY/IMAGE/VIDEO/CAROUSEL_ALBUM) and keep `contentType` for LinkedIn |
| Post Caption | `postText` (existing) | `caption` | reuse LinkedIn's caption/text field |
| Country | `country_name` (existing) | `follower_demographics` country breakdown | reuse |
| City | `city` (**new**) | `follower_demographics` city breakdown | new |

### 3.2 Metrics (verified-available only)

Reused as-is: `reach`, `likes`, `comments`, `shares`, `total_engagements`, `engagement_rate`, `followers`, `followers_gained`.
New: `views`, `saved`, `accounts_engaged`, `total_interactions`, `avg_watch_time` (reels), `replies` (stories), `navigation` (stories).
**Explicitly not added** (removed by Meta): impressions, profile_views, and the click metrics — leaving them absent keeps the dashboard honest.

### 3.3 Row-type strategy (mirrors LinkedIn)

| Row Type | Contents | LinkedIn analogue |
|---|---|---|
| `daily` | per-day account metrics (reach, views, total_interactions, followers, followers_gained, engagement_rate) | `daily` |
| `monthly` | month-bucketed aggregates (via existing `aggregate_by_period`) | `monthly`/`weekly` |
| `country` | one row per country from `follower_demographics` | `audience` (Followers by Country) |
| `post` | one row per FEED post (reach, views, likes, comments, shares, saved, total_interactions, media_type, caption, timestamp) | `post` |
| `reel` | one row per REELS media (+ avg_watch_time) | *(new)* |
| `story` | one row per STORY media (reach, views, replies, navigation) | *(new)* |
| `summary` | executive summary + top-post insight | `summary` |

Comparison rows (WoW/MoM) come for free from the reused `build_comparisons`.

---

## 4. Implementation plan (for Phase 2 — pending approval)

Design principle: **extract shared logic into a base + registry; put Instagram specifics in sibling modules; touch LinkedIn code only to generalize, never to change its behavior.**

### 4.1 Files to modify (and LinkedIn impact)

| File | Change | LinkedIn impact |
|---|---|---|
| `analytics_utils.py` | Extract the platform-agnostic helpers (already agnostic) as the shared core; **parameterize** `METRIC_FIELDS`, `engagement_rate`, `summarize_performance`'s ranking metric so they aren't LinkedIn-hardcoded. Leave LinkedIn extractors/URN maps in place. | Behavior-preserving refactor; guarded by existing `tests.py`. |
| `mongo_service.py` | Add `platform` (and account id) to the `dashboard_cache` key and to `analytics`/`tokens` docs; replace the hardcoded `collect_linkedin_analytics` import with **registry dispatch**; parameterize the DB/collection name. Extend `_ensure_indexes` for the new key. | LinkedIn continues to work; its cache entries simply carry `platform="linkedin"`. **This is the highest-risk change — see §4.4.** |
| `linkedin_service.py` | Pull the generic `_request`/retry loop up into a base client that `LinkedInService` subclasses. | No functional change. |
| `auth_service.py` | Generalize the "read latest / persist newest token" helpers to accept a platform; keep LinkedIn refresh logic. | No functional change. |
| `views.py` / `urls.py` | Add a platform-aware analytics view + an `/api/instagram-analytics/` route (and/or `/api/analytics/?platform=`). Keep `/api/linkedin-analytics/` verbatim. | Existing route/response unchanged. |
| `settings.py` | Add `META_APP_ID`, `META_APP_SECRET`, `INSTAGRAM_BUSINESS_ACCOUNT_ID`, `META_GRAPH_API_VERSION`, `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_REFRESH_TOKEN`, and a platform registry entry. | Additive. |
| `scheduler.py` | Iterate registered platforms instead of calling `sync_linkedin` directly. | LinkedIn still synced nightly. |
| `.gitignore` / `.env.example` | Add a `.env.example` documenting the new Meta vars (secrets stay out of git — you're already on a branch that just added `.env` to `.gitignore`). | none |

### 4.2 New files (why they can't live in existing shared modules)

| New file | Why |
|---|---|
| `services/base_service.py` | Home for the extracted `BaseAnalyticsClient` (transport/retry/refresh). Keeps `linkedin_service.py` from being imported by `instagram_service.py`. |
| `services/instagram_service.py` | `InstagramService(BaseAnalyticsClient)` — Meta endpoints, cursor pagination, `since/until/period` params, account discovery. Platform-specific by nature. |
| `services/instagram_utils.py` | Instagram response extractors + `media_product_type`/`media_type` mapping + demographics (country/city) normalizers. Belongs beside, not inside, the LinkedIn extractors to keep each platform's parsing isolated. |
| `services/instagram_sync.py` | `collect_instagram_analytics()` — the Instagram analogue of `collect_linkedin_analytics`, reusing the shared assembly helpers. |
| `services/registry.py` | **Platform registry**: `platform → {service, collector, metric_fields, engagement_fn, default_account}`. This is the extensibility keystone — adding Facebook/X/YouTube later means one new sibling trio + one registry entry, no edits to `mongo_service`/`views`. |
| `management/commands/sync_analytics.py` | Generic `sync_analytics --platform <name>`; keep `sync_linkedin.py` as a thin wrapper so the existing cron/script keeps working. |
| `connector/instagram_connector.gs` *(if cloning — see §4.6)* | Instagram schema/flatten. |

### 4.3 Extensibility via a registry (not per-platform branches)
`get_dashboard_analytics(platform, start, end, granularity)` looks up the collector in `registry.py` rather than importing a specific module. New platforms register themselves; the storage/view/cache layers stay platform-agnostic. This directly satisfies the "low-effort future platforms" objective and the "registry rather than hardcoded branches" instruction.

### 4.4 DB / config / secret-storage changes
- **Cache key must gain `platform` (+ account id).** Current key `(start, end, granularity)` would let LinkedIn and Instagram responses for the same window overwrite each other — a correctness bug the moment a second platform exists. New key: `(platform, account_id, start, end, granularity)`; update the unique index accordingly.
- `analytics` and `tokens` docs gain a `platform` field; indexes become `{platform, organization/account_id, date}` and `{platform, created_at}`.
- **Secure token storage:** reuse the existing Mongo `tokens` pattern (tokens live in Mongo, never in git). Meta App ID/secret go in `.env` (now git-ignored) with a committed `.env.example`. No secrets in the connector (`AuthType.NONE` unchanged, but the connector still only talks to our backend, never to Meta directly — the long-lived token stays server-side).
- No SQLite migration (still unused); Mongo changes are additive.

### 4.5 Testing strategy
- **Unit (no network, the bulk):** commit **fixture JSON** captured from the Graph API shapes (`/insights` `data[].values[]`, media list, `follower_demographics` breakdowns) under `tests/fixtures/instagram/`. Test: response extractors, `media_product_type` routing to row types, demographics→country/city rows, engagement-rate computation, registry dispatch, and the **new cache-key isolation** (LinkedIn vs Instagram don't collide). Mirror the style of the existing `SimpleTestCase`s that patch `mongo_service.db`.
- **Contract test:** assert the connector's expected field ids ⊆ backend payload keys, so a schema drift fails CI rather than silently emptying a chart.
- **Live calls — minimal, one-time, manual:** (a) OAuth + long-lived-token exchange and **IG Business Account ID discovery**; (b) a single smoke fetch per row type to confirm scopes and the <100-follower/ top-45 behaviors. Everything else is mocked to avoid Graph API rate limits. The existing fetch-through cache + connector 5-min cache keep live pulls rare in normal operation.

### 4.6 Open decision — one connector or two?
| Option | Pros | Cons |
|---|---|---|
| **A. Clone** `looker_connector.gs` → `instagram_connector.gs` | Zero risk to the live LinkedIn connector; simplest to reason about | Two files drift over time |
| **B. Parameterize** one connector with a `platform` config field | DRY; single deploy | Editing the running LinkedIn connector risks regressions; Apps Script has no tests |
| **Recommendation** | **A now, B later** — clone for Instagram to guarantee LinkedIn zero-regression, then converge to a parameterized connector once both schemas are stable. | |

*(I'll want your call on this in Phase 2 — it's the one genuinely two-way-door choice.)*

### 4.7 Risks & open questions
1. **No Meta app exists yet.** Blocking prerequisite: create a Meta app, add Instagram Graph API, complete **App Review** for `instagram_manage_insights` + `pages_read_engagement` (+ `instagram_basic`, `pages_show_list`), connect the IG Business account to a Page. Until then Phase 2 can build against fixtures but cannot do a live smoke test. **Q: do you have / can you create the Meta app and target IG Business account?**
2. **Removed metrics** (profile views, all clicks, impressions) mean the Instagram dashboard **cannot** reach parity with LinkedIn's clicks/CTR/page-views columns. **Q: acceptable to show `views` in place of impressions and simply omit the click/profile-view tiles?**
3. **Story insights require the Facebook-Login path.** If you prefer the Instagram-Login path (no FB Page), stories drop off the list. **Q: FB-Login path OK?** (My recommendation: yes — see §2.1.)
4. **`views` discontinuity (Apr 2025)** — historical trends spanning that date will step. Needs a footnote on the dashboard.
5. **<100-follower suppression & top-45 demographics caps** — small accounts will show gaps; handle empty insight responses gracefully (the existing `_fetch_or_empty` pattern already does this).
6. **Token 60-day expiry** — same operational risk LinkedIn already has here; the refresh helper must be wired and a re-auth runbook documented.
7. **API version pinning** — target v24.0/v25.0 and make it a setting; v20.0 sunsets 2026‑09‑24.

---

## Summary
Roughly the transport loop, all aggregation/comparison math, the fetch-through cache mechanics, the row-type convention, and the connector flatten pipeline are **reusable**; the auth flows, API-domain methods, response parsers, and geo/content maps are **platform-specific** and go in Instagram sibling modules plus a small **platform registry**. The one change that carries real regression risk is making the **cache key + storage + collector dispatch platform-aware** — done carefully it leaves LinkedIn untouched. On metrics, Meta has **removed impressions, profile views, all click metrics, and video/plays** — I recommend serving **`views`** in their place where a substitute exists and **omitting** the ones with no substitute, computing engagement rate ourselves, and building on **Graph API v24.0/v25.0 via the Facebook-Login path** (required for stories). The blocking prerequisite is a **reviewed Meta app + connected IG Business account**, which does not exist in this repo yet.

**Phase 1 complete. Awaiting your explicit go-ahead before starting Phase 2. No application code will be changed until you approve.**
