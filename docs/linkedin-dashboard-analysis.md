# LinkedIn Analytics Dashboard — Looker Studio Connector: Phase 1 Analysis

**Status:** Analysis only. No application code has been created, modified, or refactored for this report.
**Date of analysis:** 2026-07-13
**Objective under evaluation:** A dynamic LinkedIn Analytics Dashboard in Looker Studio where selecting a date range / filter fetches the corresponding LinkedIn Marketing API data and renders it.

> **Note on secrets:** This report references credentials only by presence, status, scope, and expiry. No raw secret values are printed here. Live checks below were run against LinkedIn's official introspection and REST endpoints using the credentials already present in the repo.

---

## 1. Current implementation, end to end

### 1.1 File inventory by concern

| Concern | File(s) | Role |
|---|---|---|
| LinkedIn auth (3-legged) | [apps/analytics/views.py](../apps/analytics/views.py) `linkedin_login`, `linkedin_callback` | Browser OAuth: builds authorize URL, exchanges `code` → token, saves token to Mongo. |
| LinkedIn auth (refresh) | [apps/analytics/services/auth_service.py](../apps/analytics/services/auth_service.py) `get_access_token` | Refresh-token grant. **Dead code** — never called; refresh token is a placeholder. |
| LinkedIn API client | [apps/analytics/services/linkedin_service.py](../apps/analytics/services/linkedin_service.py) `LinkedInService` | REST/v2 client: token resolution, URN building, pagination, retry/backoff, time-bound vs lifetime fetch. |
| Normalization / business logic | [apps/analytics/services/analytics_utils.py](../apps/analytics/services/analytics_utils.py) | Pure functions: metric extraction, aggregation, WoW/MoM comparisons, post/audience normalization, URN→name. |
| Data sync (ETL) | [apps/analytics/management/commands/sync_linkedin.py](../apps/analytics/management/commands/sync_linkedin.py) | Orchestrates fetch → normalize → save. Entry point for the whole pipeline. |
| MongoDB storage | [apps/analytics/services/mongo_service.py](../apps/analytics/services/mongo_service.py) | `save_analytics`, `get_all_analytics`, `get_dashboard_analytics`, token save/read. |
| AI insights | [apps/analytics/services/openai_service.py](../apps/analytics/services/openai_service.py) | `generate_insight` (gpt-4o-mini). **Dead code** — never called. |
| Scheduler | [apps/analytics/services/scheduler.py](../apps/analytics/services/scheduler.py) + [apps/analytics/apps.py](../apps/analytics/apps.py) | APScheduler cron 00:02 daily; started only when `RUN_MAIN=true`. |
| Shell cron wrapper | [scripts/sync_linkedin_daily.sh](../scripts/sync_linkedin_daily.sh) | Calls `manage.py sync_linkedin`. Hardcoded path does **not** match this repo. |
| Looker Studio connector | [connector/looker_connector.gs](../connector/looker_connector.gs) | Apps Script community connector: schema, fetch, flatten, 5-min cache. |
| Config / env | [config/settings.py](../config/settings.py), [.env](../.env) | Django + LinkedIn/Mongo/OpenAI config. |
| Dashboard guide | [docs/looker_dashboard_guide.md](../docs/looker_dashboard_guide.md) | Field mapping + layout guide. |

### 1.2 Current data flow

```
                          (nightly, or manual)
LinkedIn REST/v2  ──►  sync_linkedin  ──►  analytics_utils (normalize)
                                                  │
                                                  ▼
                                       MongoDB linkedin_db.analytics   (one doc per org+date)
                                                  │
   Looker Studio date range ──► Apps Script connector
        (looker_connector.gs) ──► GET /api/linkedin-analytics/?format=dashboard&start_date&end_date
                                                  │
                                                  ▼
                                  mongo_service.get_dashboard_analytics  (reads stored snapshots)
                                                  │
                                                  ▼
                                  connector normalizeRows() ──► Looker rows
```

**Key characteristic:** The connector does **not** call LinkedIn directly. It calls the Django backend, which serves **previously synced MongoDB snapshots**. "Dynamic" today means "filter the already-synced data by date," not "fetch live from LinkedIn on demand."

### 1.3 How a request is served today
1. Looker requests data with a date range → connector `getData` ([looker_connector.gs:596](../connector/looker_connector.gs#L596)) → `fetchDashboard` builds `?format=dashboard&start_date=…&end_date=…` and GETs the backend (5-min script cache).
2. Django `linkedin_analytics` ([views.py:14](../apps/analytics/views.py#L14)) → `get_dashboard_analytics(start, end)` ([mongo_service.py:253](../apps/analytics/services/mongo_service.py#L253)).
3. That reads `analytics` docs in range, concatenates each snapshot's `daily_metrics`, `audience_analytics`, `post_analytics`, builds `dashboard_rows`, returns JSON.
4. Connector `normalizeRows` ([looker_connector.gs:495](../connector/looker_connector.gs#L495)) flattens `dashboard_rows` (or falls back to daily/audience/post arrays), appends weekly/monthly/comparison/insight rows, maps to the fixed schema.

### 1.4 How sync currently produces data
`sync_linkedin.handle` ([sync_linkedin.py:85](../apps/analytics/management/commands/sync_linkedin.py#L85)) fetches ~9 resources (time-bound share/follower/page stats, lifetime share/page stats, follower demographics, posts, post-level stats, follower count), each wrapped in `_fetch_or_empty`. If time-bound daily rows come back empty it falls back to a single **lifetime snapshot** and, in `save_analytics`, converts today's cumulative totals into a daily delta by subtracting yesterday's stored snapshot (`_daily_rows_from_lifetime_snapshot`, [mongo_service.py:88](../apps/analytics/services/mongo_service.py#L88)).

---

## 2. Credentials and API access (live-verified)

### 2.1 Credential presence (`.env`)
| Credential | Status |
|---|---|
| `LINKEDIN_CLIENT_ID` | Present |
| `LINKEDIN_CLIENT_SECRET` | Present |
| `LINKEDIN_ACCESS_TOKEN` | Present, **active** (see below) |
| `LINKEDIN_REFRESH_TOKEN` | **Placeholder only** (`your_refresh_token`) — no usable refresh token |
| `LINKEDIN_ORG_ID` | Present (organization `14544608`) |
| `OPENAI_API_KEY` | Present (unused by any live code path) |

### 2.2 Token introspection (LinkedIn `introspectToken`, live)
- **Status:** active
- **Auth type:** `3L` (three-legged / member-authorized token)
- **Granted scopes:** `openid`, `profile`, `email`, `r_organization_admin`, `r_organization_social`, `rw_organization_admin`, `w_member_social`, `w_organization_social`
- **Created / authorized:** 2026-05-19
- **Expires:** **2026-07-18** — approximately **5 days from the analysis date**

> ⚠️ **Critical:** This is a 3-legged token expiring in ~5 days, and there is **no valid refresh token**. When it expires, every downstream feature (sync and any live dashboard fetch) stops working until a human re-runs the browser OAuth flow (`/api/login/` → `/api/callback/`). Any design that fetches live from LinkedIn is blocked on solving token lifecycle first.

### 2.3 Endpoint accessibility (live REST probe, org 14544608, version `202604`)
| Endpoint (via `LinkedInService`) | Result |
|---|---|
| `networkSizes` (follower count, v2) | **OK** — returns `firstDegreeSize` |
| `organizationalEntityShareStatistics` **lifetime** | **OK** — 1 element |
| `organizationPageStatistics` **lifetime** | **OK** — 1 element |
| `organizationalEntityFollowerStatistics` (demographics) | **OK** — 1 element |
| `posts` (organization posts) | **OK** — 105 elements |
| `organizationalEntityShareStatistics` **time-bound (DAY)** | **OK — 30 daily elements** |
| `organizationPageStatistics` **time-bound (DAY)** | **OK — 30 daily elements** |
| `organizationalEntityFollowerStatistics` **time-bound (DAY)** | **OK — 30 daily elements** |

**Major finding:** **Time-bound daily analytics work today.** This contradicts the assumption in the code and in [docs/looker_dashboard_guide.md:11-13](../docs/looker_dashboard_guide.md#L11-L13) that LinkedIn rejects time-bound org analytics. The lifetime-snapshot-delta fallback is therefore **no longer the primary path** and should be demoted to a true fallback.

**Permission gaps / notes:**
- Scopes cover organization read/admin and social — sufficient for all analytics endpoints the project uses. No gap observed for the current objective.
- Post-level share statistics (per-post metrics) were not probed individually here; the sync fetches them via `shares=List(...)` and they may be partial depending on post type. To be validated in Phase 2 against live data.
- No `r_ads`/`r_ads_reporting` scope → paid campaign analytics are **out of scope** with these credentials (organic only).

---

## 3. Gap analysis (against the dashboard objective)

### Already implemented (reusable as-is or lightly adjusted)
- A complete, resilient LinkedIn client with retry/backoff, pagination, and time-bound support ([linkedin_service.py](../apps/analytics/services/linkedin_service.py)).
- A strong, unit-tested normalization layer ([analytics_utils.py](../apps/analytics/services/analytics_utils.py), [tests.py](../apps/analytics/tests.py)).
- A working connector schema + flatten pipeline ([looker_connector.gs](../connector/looker_connector.gs)) that already consumes `start_date`/`end_date` from Looker.
- A backend endpoint that already accepts date-range params ([views.py:14](../apps/analytics/views.py#L14)).

### Partially implemented / needs change
- **Date-range dynamism.** The connector passes the date range, but the backend serves **pre-synced** data, not a range-specific live fetch. For "select a range → get that range's data," the backend must fetch time-bound data for the requested window (now that time-bound works), with caching.
- **API version ceiling.** `_normalize_api_version` ([linkedin_service.py:40-48](../apps/analytics/services/linkedin_service.py#L40-L48)) hard-caps at `202604`. Works today but will silently pin the app to an aging version; should be configurable.
- **Dashboard aggregation duplication.** `get_dashboard_analytics` re-appends each snapshot's audience/post rows ([mongo_service.py:266-280](../apps/analytics/services/mongo_service.py#L266-L280)) → duplicated rows / double-counting across multi-day ranges.

### Missing
- **Token lifecycle management.** No working refresh path; token expires in days. No proactive refresh, no expiry alerting.
- **On-demand / fetch-through caching** keyed by `(org, start, end, granularity)`.
- **Server-side auth** on the analytics endpoint (currently public; connector auth = NONE).
- **MongoDB indexes** (all reads are collection scans).
- **Env/config hygiene:** committed secrets, `DEBUG=True`, `ALLOWED_HOSTS=["*"]`, hardcoded `redirect_uri`/OAuth `state`; `apscheduler` missing from [requirements.txt](../requirements.txt); connector `API_URL` (`dosysanalytics.dosystemsinc.com`) vs backend `localhost:8001` mismatch.

### Implemented incorrectly / inefficiently
- **Lifetime-snapshot fallback treated as the norm.** Given the live finding, this should become a genuine fallback, not the main path — otherwise the dashboard shows reconstructed deltas instead of true LinkedIn daily data.
- **Full `raw` API payloads persisted per daily snapshot** ([mongo_service.py:219](../apps/analytics/services/mongo_service.py#L219)) → document bloat.
- **Redundant computation:** `dashboard_rows` built at save time and rebuilt at read time.

---

## 4. Recommended architecture

### 4.1 Keep the backend-mediated pattern (do NOT call LinkedIn directly from Apps Script)
**Recommendation: reuse the existing `connector → Django → LinkedIn` topology.** Rationale, in priority order:
- **Correctness/best practice:** LinkedIn 3-legged OAuth, token refresh, and versioned headers are far easier and safer server-side than inside an Apps Script community connector.
- **Security:** keeps client secret and access token out of Apps Script/Looker.
- **Rate limits:** a single server-side cache absorbs Looker's many repeated field/preview requests; calling LinkedIn directly from the connector would multiply calls and risk throttling.
- **Reuse:** the connector's schema/flatten logic and the backend's normalization are already built and tested.

> This explicitly **rejects** the alternative of a direct LinkedIn-from-connector design.

### 4.2 Make the backend fetch time-bound data for the requested range, with MongoDB as a cache
Because time-bound endpoints now work, redesign the read path as **fetch-through cache**:

1. Connector sends `start_date`, `end_date` (already does).
2. Backend computes a cache key `(organization_id, start, end, granularity)`.
3. **Cache hit & fresh** (within a configurable TTL, e.g. 6–24h): serve stored normalized rows from Mongo.
4. **Cache miss / stale:** call LinkedIn **time-bound** for the exact window, normalize via existing `analytics_utils`, store, and serve.
5. **Fallback:** if time-bound fails for a window (permission/version regression), fall back to the existing lifetime-snapshot-delta path — now clearly labeled as fallback.

This preserves backward compatibility (same endpoint, same JSON shape, same connector) while making the dashboard genuinely range-driven and live-ish.

### 4.3 Filter/date-range → API mapping
- Looker `dateRange.startDate/endDate` → `analytics_utils.linkedin_time_intervals()` (already implemented) → `timeIntervals` param on share/follower/page statistics.
- Granularity: default `DAY`; optionally derive `MONTH` for very long ranges to limit payload size.
- Non-date filters (audience segment type, content type, row type) are already handled connector-side via `filterRowsForRequestedFields` and Looker's own filtering; no extra API calls needed.

### 4.4 Token lifecycle (prerequisite, not optional)
- Short term: document the manual re-auth via `/api/login/` and **rotate the near-expiry token** before it lapses.
- Proper fix: enable and store a real **refresh token**, and have `LinkedInService` refresh proactively on expiry/401. `auth_service.get_access_token` can be revived for this instead of remaining dead code.

### 4.5 Where I recommend replacing vs reusing
| Component | Decision | Why |
|---|---|---|
| `LinkedInService` | **Reuse**, minor changes | Solid client; only make version cap configurable + add 401→refresh. |
| `analytics_utils` | **Reuse** | Tested, pure, correct. |
| `mongo_service.get_dashboard_analytics` | **Modify** | Add fetch-through caching; fix audience/post duplication. |
| Lifetime-snapshot delta logic | **Keep but demote** | Becomes fallback, not default. Trade-off: keeps code that's only rarely exercised — justified for resilience against LinkedIn regressions. |
| `looker_connector.gs` | **Reuse**, minor changes | Reconcile `API_URL`; otherwise schema/flatten stays. |
| `openai_service` / `auth_service` | **Revive or remove** | Currently dead. Revive `auth_service` for refresh; decide on `openai_service` (insights feature) explicitly. |
| Analytics endpoint auth | **Add** | Currently public. |

---

## 5. Implementation plan (for Phase 2 — pending your approval)

### 5.1 Files to modify / create
| File | Change | Impact |
|---|---|---|
| [apps/analytics/services/mongo_service.py](../apps/analytics/services/mongo_service.py) | Add fetch-through cache in `get_dashboard_analytics` (call sync/fetch for uncached ranges); dedupe audience/post rows; add index creation on startup. | Core behavior change; makes dashboard range-driven. Backward-compatible JSON shape. |
| [apps/analytics/services/linkedin_service.py](../apps/analytics/services/linkedin_service.py) | Make API version configurable (remove hard cap or raise it); add 401→refresh hook. | Prevents version lock-in; enables live fetch reliability. |
| [apps/analytics/services/auth_service.py](../apps/analytics/services/auth_service.py) | Revive for refresh-token flow; wire into `LinkedInService`. | Fixes the token-expiry cliff. Requires a real refresh token. |
| [apps/analytics/views.py](../apps/analytics/views.py) | Add lightweight API auth (key/token); validate date params; stop leaking `str(exc)`/token in responses. | Security; small contract change (auth header). |
| [config/settings.py](../config/settings.py) | Move `SECRET_KEY`/`DEBUG`/`ALLOWED_HOSTS` to env; add cache TTL + connector API-key settings. | Security/config hygiene. |
| [requirements.txt](../requirements.txt) | Add `apscheduler` (and any new deps). | Fixes clean-install boot failure. |
| [connector/looker_connector.gs](../connector/looker_connector.gs) | Reconcile `API_URL` with environment; send auth header if API is secured; no schema change expected. | Connector points at correct backend + auth. |
| [docs/looker_dashboard_guide.md](../docs/looker_dashboard_guide.md) | Update to reflect that time-bound daily data is live (remove "lifetime fallback is normal" guidance). | Docs accuracy. |
| [.env](../.env) / `.env.example` | Rotate secrets; add `.env.example`; ensure `.gitignore` excludes `.env`. | Security. |

**No new modules are proposed** beyond possibly a small `.env.example` — the fetch-through cache and refresh logic fit inside existing services, avoiding duplicate logic.

### 5.2 DB / config changes
- **Indexes:** unique `{organization_id:1, date:1}`, plus `{date:1}` on `analytics` and `{created_at:-1}` on `tokens`.
- **New settings:** dashboard cache TTL, connector API key, configurable LinkedIn API version, refresh-token handling.
- **No schema migration** for SQLite (still unused); Mongo is schemaless — additive only.

### 5.3 Testing / validation strategy
- **Unit:** extend [tests.py](../apps/analytics/tests.py) for the fetch-through cache (hit/miss/stale), dedup logic, and date→interval mapping. These run without network (existing tests already mock Mongo).
- **Integration (live, gated):** reuse the Phase-1 probe approach (scratchpad scripts) to confirm time-bound fetch + normalization for a chosen range; assert row counts and that daily rows are true (not deltas).
- **Connector validation:** a test Looker Studio data source pointed at a staging backend; verify a 7-day and 30-day range render true daily series, audience and post tables populate, and WoW/MoM scorecards compute.
- **Rate limits/quotas:** rely on the server-side cache (TTL) so Looker's repeated requests hit Mongo, not LinkedIn; keep the connector's 5-min `CacheService` layer; add backoff (already present in `_request`). Document LinkedIn's per-app throttling as an operational limit.

### 5.4 Risks and open questions
1. **Token expiry (~5 days) with no refresh token** — highest risk. Phase 2 live work is blocked until the token is refreshed/rotated and a refresh mechanism exists. **Open question:** does the LinkedIn app have "programmatic refresh tokens" enabled, and can you provide a real refresh token or re-auth?
2. **Committed/near-expiry secrets** — should be rotated before any further use (also a security issue flagged in the prior report).
3. **Backend environment** — where does the connector's `dosysanalytics.dosystemsinc.com` point, and is it the same code as this repo? Needed to align the connector.
4. **Post-level statistics completeness** — per-post metrics vary by post/content type; needs live validation.
5. **Paid vs organic** — no ads scopes; dashboard is organic-only. Confirm that meets the objective.
6. **Caching freshness expectations** — how "live" must the dashboard be (minutes vs daily)? Determines TTL and whether the nightly sync is still needed alongside fetch-through.

---

## Summary of the single most important finding
The codebase was built around the belief that LinkedIn rejects time-bound organization analytics, so it stores lifetime snapshots and reconstructs daily deltas. **Live testing shows time-bound daily analytics now work**, which means the dashboard can be made genuinely date-range-driven by adding a fetch-through cache in the existing backend — reusing almost all existing code — rather than rebuilding anything. The blocking prerequisite is **LinkedIn token lifecycle** (current token expires in ~5 days with no refresh token).

**Phase 1 complete. Awaiting your explicit go-ahead before starting Phase 2. No application code will be changed until you approve.**
