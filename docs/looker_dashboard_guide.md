# LinkedIn Looker Studio Dashboard Guide

## Data Flow

LinkedIn APIs feed the Django sync command. The sync stores one MongoDB snapshot per day. Looker Studio reads the dashboard endpoint through the Apps Script community connector.

`LinkedIn API -> sync_linkedin -> MongoDB -> /api/linkedin-analytics/?format=dashboard -> Apps Script -> Looker Studio`

## Important Reporting Behavior

Time-bound organization analytics are now available for this app, so the backend requests **true daily rows** from LinkedIn for the selected date range. The dashboard endpoint (`?format=dashboard`) fetches the requested window live from LinkedIn and caches the normalized result in MongoDB (`dashboard_cache`, TTL `DASHBOARD_CACHE_TTL_SECONDS`, default 6h). Set `DASHBOARD_LIVE_FETCH=false` to serve only pre-synced snapshots instead of fetching live.

If the time-bound endpoints ever stop returning daily rows, the backend automatically falls back to a lifetime snapshot and converts it into a daily delta by subtracting the previous stored snapshot (`metric_mode = daily_delta_from_lifetime_snapshot`).

The nightly sync is still useful for building history and warming the cache:

```bash
./venv/bin/python manage.py sync_linkedin
```

## Looker Studio Field Mapping

Use `Date` as the date range dimension and filter most charts to `Row Type = daily`.

Core KPI fields:

- `Impressions`: SUM
- `Reach`: SUM
- `Unique Impressions`: SUM
- `Clicks`: SUM
- `Likes`: SUM
- `Comments`: SUM
- `Shares`: SUM
- `Total Engagements`: SUM
- `Engagement Rate`: AVG
- `CTR %`: AVG
- `Followers`: MAX
- `Followers Gained`: SUM
- `Page Views`: SUM
- `Careers Page Views`: SUM

Segmentation fields:

- `Row Type`: `daily`, `audience`, `post`, `summary`
- `Metric Mode`: `time_bound`, `daily_delta_from_lifetime_snapshot`, `audience`, `post`, `summary`
- `Audience Segment Type`
- `Audience Segment`
- `Post ID`
- `Content Type`

Comparison fields:

- `WoW Impressions Change %`
- `WoW Engagement Change %`
- `WoW Followers Change %`
- `MoM Impressions Change %`
- `MoM Engagement Change %`
- `MoM Followers Change %`
- `Week Trend`
- `Month Trend`

## Suggested Dashboard Layout

Page title and controls:

- Report title: LinkedIn Analytics
- Date range control at the top right
- Optional filter control for `Audience Segment Type`

KPI strip:

- Followers
- Followers Gained
- Impressions
- Reach
- Engagement Rate
- CTR %
- Page Views
- Careers Page Views

Trend section:

- Impressions over time: time series, metric `Impressions`, filter `Row Type = daily`
- Reach and impressions: combo chart, bars `Impressions`, line `Reach`
- Engagement trend: time series or stacked bars with `Likes`, `Comments`, `Shares`
- Click efficiency: combo chart, bars `Clicks`, line `CTR %` on right axis

Comparison section:

- Scorecards for WoW and MoM change percentages
- Use `Week Trend` and `Month Trend` as small table/status fields

Follower section:

- New followers over time: time series, metric `Followers Gained`
- Total followers: scorecard, metric `Followers`

Audience section:

- Followers by country: bar chart, dimension `Country`, metric `Followers`, filter `Row Type = audience` and `Audience Segment Type = Followers by Country`
- Followers by country heatmap: geo chart, dimension `Country`, metric `Followers`, filter `Row Type = audience` and `Audience Segment Type = Followers by Country`
- Followers by industry: bar chart, dimension `Industry`, metric `Followers`, filter `Row Type = audience` and `Audience Segment Type = Followers by Industry`
- Audience detail table: dimensions `Audience Segment Type`, `Audience Segment Label`; metrics `Followers`, `Organic Followers`, `Paid Followers`
- Followers by job function: bar chart, same setup with `Followers by Job Function`

Use `Audience Segment` only for debugging raw LinkedIn URNs. Country and industry charts should use `Country`, `Industry`, or `Audience Segment Label`.

Top content section:

- Table with dimension `Post ID`
- Metrics: `Impressions`, `Reach`, `Clicks`, `Likes`, `Comments`, `Shares`, `Total Engagements`, `Engagement Rate`, `CTR %`
- Filter: `Row Type = post`
- Sort: `Total Engagements` descending

## Chart Scaling Fixes

Do not put `Impressions`, `Clicks`, and `Likes` on the same single-axis time series. Impressions are much larger and make smaller metrics look flat.

Recommended chart choices:

- Impressions alone: normal time series
- Clicks + CTR: combo chart, `Clicks` on left axis, `CTR %` on right axis
- Impressions + Reach: combo chart, both large-scale metrics
- Likes + Comments + Shares: stacked bar or grouped bar
- Engagement Rate: separate line chart or KPI card

## Maintenance

Refresh the Apps Script connector after schema changes:

1. Open Looker Studio data source.
2. Click `Edit connection`.
3. Reconnect or refresh fields.
4. Confirm new fields appear: `Reach`, `Unique Impressions`, `CTR %`, `Total Engagements`, `Metric Mode`, `Country`, `Industry`, `Audience Segment Label`, `Organic Followers`, `Paid Followers`.

If charts show only one date, run the sync on multiple days. If LinkedIn later enables time-bound endpoints for your app, the same dashboard will automatically use true daily rows from LinkedIn instead of fallback deltas.



