// Instagram Insights — Looker Studio community connector.
// Cloned from looker_connector.gs (LinkedIn) per the approved plan: a separate
// connector guarantees zero regression to the live LinkedIn connector. It talks
// only to our Django backend (never to Meta directly), so the long-lived token
// stays server-side.
//
// Metric note: Meta removed impressions / video_views / plays (consolidated into
// "views") and removed profile-view / click metrics with no replacement. This
// schema therefore uses "views" and omits those removed fields — see
// docs/instagram-insights-analysis.md.

var cc = DataStudioApp.createCommunityConnector();
var API_URL = "https://dosysanalytics.dosystemsinc.com/api/instagram-analytics/";

var TEXT_FIELDS = [
  "row_type", "organization_id", "metric_mode", "media_type", "contentType",
  "postId", "postText", "permalink", "segment_type", "segment", "segment_label",
  "country_name", "city", "period", "period_type", "year_month",
  "comparison_period", "comparison_label", "sync_granularity",
  "week_trend", "month_trend", "insight_section", "insight"
];

var DATE_FIELDS = [
  "date", "postDate", "sync_start_date", "sync_end_date",
  "current_start", "current_end", "previous_start", "previous_end"
];

var NUMBER_FIELDS = [
  "reach", "views", "likes", "comments", "shares", "saved",
  "total_interactions", "accounts_engaged", "engagement_rate", "total_engagements",
  "followers", "followers_gained", "replies", "navigation", "avg_watch_time",
  "week_reach_change_pct", "week_views_change_pct", "week_total_interactions_change_pct",
  "week_engagement_change_pct", "week_followers_change_pct",
  "month_reach_change_pct", "month_views_change_pct", "month_total_interactions_change_pct",
  "month_engagement_change_pct", "month_followers_change_pct"
];

var DEFAULT_FIELD_IDS = [
  "row_type", "date", "year_month", "organization_id", "media_type",
  "reach", "views", "likes", "comments", "shares", "saved",
  "total_interactions", "accounts_engaged", "engagement_rate", "total_engagements",
  "followers", "followers_gained", "country_name", "city",
  "postId", "postText", "period", "period_type"
];

function getAuthType() {
  return cc.newAuthTypeResponse().setAuthType(cc.AuthType.NONE).build();
}

function getConfig() {
  return cc.getConfig().setDateRangeRequired(true).build();
}

function addMetric(fields, id, name, aggregation) {
  fields.newMetric().setId(id).setName(name)
    .setType(cc.FieldType.NUMBER)
    .setAggregation(aggregation || cc.AggregationType.SUM);
}

function addDimension(fields, id, name, type) {
  fields.newDimension().setId(id).setName(name).setType(type || cc.FieldType.TEXT);
}

function getFields() {
  var fields = cc.getFields();
  var agg = cc.AggregationType;
  var types = cc.FieldType;

  addDimension(fields, "row_type", "Row Type", types.TEXT);
  addDimension(fields, "date", "Date", types.YEAR_MONTH_DAY);
  addDimension(fields, "year_month", "Year Month", types.TEXT);
  addDimension(fields, "organization_id", "Account ID", types.TEXT);
  addDimension(fields, "metric_mode", "Metric Mode", types.TEXT);
  addDimension(fields, "media_type", "Media Type", types.TEXT);
  addDimension(fields, "contentType", "Content Type", types.TEXT);
  addDimension(fields, "postId", "Media ID", types.TEXT);
  addDimension(fields, "postText", "Caption", types.TEXT);
  addDimension(fields, "permalink", "Permalink", types.TEXT);
  addDimension(fields, "segment_type", "Audience Segment Type", types.TEXT);
  addDimension(fields, "segment", "Audience Segment", types.TEXT);
  addDimension(fields, "segment_label", "Audience Segment Label", types.TEXT);
  addDimension(fields, "country_name", "Country", types.TEXT);
  addDimension(fields, "city", "City", types.TEXT);
  addDimension(fields, "postDate", "Post Date", types.YEAR_MONTH_DAY);
  addDimension(fields, "period", "Period", types.TEXT);
  addDimension(fields, "period_type", "Period Type", types.TEXT);
  addDimension(fields, "comparison_period", "Comparison Period", types.TEXT);
  addDimension(fields, "comparison_label", "Comparison Label", types.TEXT);
  addDimension(fields, "sync_start_date", "Sync Start Date", types.YEAR_MONTH_DAY);
  addDimension(fields, "sync_end_date", "Sync End Date", types.YEAR_MONTH_DAY);
  addDimension(fields, "sync_granularity", "Sync Granularity", types.TEXT);
  addDimension(fields, "current_start", "Current Start", types.YEAR_MONTH_DAY);
  addDimension(fields, "current_end", "Current End", types.YEAR_MONTH_DAY);
  addDimension(fields, "previous_start", "Previous Start", types.YEAR_MONTH_DAY);
  addDimension(fields, "previous_end", "Previous End", types.YEAR_MONTH_DAY);
  addDimension(fields, "week_trend", "Week Trend", types.TEXT);
  addDimension(fields, "month_trend", "Month Trend", types.TEXT);
  addDimension(fields, "insight_section", "Insight Section", types.TEXT);
  addDimension(fields, "insight", "Insight", types.TEXT);

  addMetric(fields, "reach", "Reach", agg.SUM);
  addMetric(fields, "views", "Views", agg.SUM);
  addMetric(fields, "likes", "Likes", agg.SUM);
  addMetric(fields, "comments", "Comments", agg.SUM);
  addMetric(fields, "shares", "Shares", agg.SUM);
  addMetric(fields, "saved", "Saves", agg.SUM);
  addMetric(fields, "total_interactions", "Content Interactions", agg.SUM);
  addMetric(fields, "accounts_engaged", "Accounts Engaged", agg.SUM);
  addMetric(fields, "engagement_rate", "Engagement Rate", agg.AVG);
  addMetric(fields, "total_engagements", "Total Engagements", agg.SUM);
  addMetric(fields, "followers", "Followers", agg.MAX);
  addMetric(fields, "followers_gained", "Follower Growth", agg.SUM);
  addMetric(fields, "replies", "Story Replies", agg.SUM);
  addMetric(fields, "navigation", "Story Navigation", agg.SUM);
  addMetric(fields, "avg_watch_time", "Reel Avg Watch Time", agg.AVG);
  addMetric(fields, "week_reach_change_pct", "WoW Reach Change %", agg.AVG);
  addMetric(fields, "week_views_change_pct", "WoW Views Change %", agg.AVG);
  addMetric(fields, "week_total_interactions_change_pct", "WoW Interactions Change %", agg.AVG);
  addMetric(fields, "week_engagement_change_pct", "WoW Engagement Change %", agg.AVG);
  addMetric(fields, "week_followers_change_pct", "WoW Follower Growth Change %", agg.AVG);
  addMetric(fields, "month_reach_change_pct", "MoM Reach Change %", agg.AVG);
  addMetric(fields, "month_views_change_pct", "MoM Views Change %", agg.AVG);
  addMetric(fields, "month_total_interactions_change_pct", "MoM Interactions Change %", agg.AVG);
  addMetric(fields, "month_engagement_change_pct", "MoM Engagement Change %", agg.AVG);
  addMetric(fields, "month_followers_change_pct", "MoM Follower Growth Change %", agg.AVG);

  return fields;
}

function getSchema(request) {
  return { schema: getFields().build() };
}

function ymd(value) {
  if (!value) { return ""; }
  if (value.$date) { value = value.$date; }
  else if (value.$numberLong) { value = Number(value.$numberLong); }
  if (value instanceof Date) { return value.toISOString().slice(0, 10).replace(/-/g, ""); }
  if (typeof value === "number") { return new Date(value).toISOString().slice(0, 10).replace(/-/g, ""); }
  var text = String(value);
  if (/^\d{8}$/.test(text)) { return text; }
  return text.replace(/-/g, "").slice(0, 8);
}

function numberValue(value) {
  var number = Number(value);
  return isNaN(number) ? 0 : number;
}

function textValue(value) {
  if (value === null || value === undefined) { return ""; }
  return String(value);
}

function firstValue() {
  for (var i = 0; i < arguments.length; i++) {
    if (arguments[i] !== null && arguments[i] !== undefined && arguments[i] !== "") {
      return arguments[i];
    }
  }
  return "";
}

function yearMonth(value) {
  var text = textValue(value);
  if (/^\d{4}-\d{2}/.test(text)) { return text.slice(0, 7); }
  return "";
}

function reportEndDate(payload) {
  var syncRange = payload.sync_range || {};
  return firstValue(payload.end_date, payload.date, syncRange.end_date);
}

function reportStartDate(payload) {
  var syncRange = payload.sync_range || {};
  return firstValue(payload.start_date, syncRange.start_date);
}

function totalFollowers(payload) {
  var overview = payload.overview || {};
  return numberValue(firstValue(overview.followers, payload.followers));
}

function setSyncFields(row, payload) {
  var syncRange = payload.sync_range || {};
  row.sync_start_date = reportStartDate(payload);
  row.sync_end_date = reportEndDate(payload);
  row.sync_granularity = textValue(syncRange.granularity);
}

function setMetricFields(row, source) {
  var metricIds = [
    "reach", "views", "likes", "comments", "shares", "saved",
    "total_interactions", "accounts_engaged", "engagement_rate", "total_engagements",
    "followers", "followers_gained", "replies", "navigation", "avg_watch_time"
  ];
  metricIds.forEach(function(id) { row[id] = numberValue(source[id]); });
}

function setAudienceFields(row, source) {
  row.segment_type = textValue(source.segment_type);
  row.segment = textValue(source.segment);
  row.segment_label = textValue(source.segment_label || source.country_name || source.city || source.segment);
  row.country_name = textValue(source.country_name);
  row.city = textValue(source.city);
}

function comparisonMetric(payload, period, key) {
  var comparison = (payload.comparisons && payload.comparisons[period]) || {};
  var changes = comparison.changes || {};
  return numberValue(changes[key]);
}

function comparisonTrend(payload, period, key) {
  var comparison = (payload.comparisons && payload.comparisons[period]) || {};
  var changes = comparison.changes || {};
  return textValue(changes[key] || "flat");
}

function setComparisonFields(row, payload) {
  var mappings = [
    ["reach", "reach"],
    ["views", "views"],
    ["total_interactions", "total_interactions"],
    ["engagement_rate", "engagement"],
    ["followers_gained", "followers"]
  ];
  mappings.forEach(function(m) {
    row["week_" + m[1] + "_change_pct"] = comparisonMetric(payload, "week_over_week", m[0] + "_change_pct");
    row["month_" + m[1] + "_change_pct"] = comparisonMetric(payload, "month_over_month", m[0] + "_change_pct");
  });
  row.week_trend = comparisonTrend(payload, "week_over_week", "reach_trend");
  row.month_trend = comparisonTrend(payload, "month_over_month", "reach_trend");
}

function buildUrl(request) {
  var params = ["format=dashboard"];
  if (request && request.dateRange) {
    params.push("start_date=" + encodeURIComponent(request.dateRange.startDate));
    params.push("end_date=" + encodeURIComponent(request.dateRange.endDate));
  }
  return API_URL + "?" + params.join("&");
}

function fetchDashboard(request) {
  var url = buildUrl(request);
  var cache = CacheService.getScriptCache();
  var cached = cache.get(url);
  if (cached) {
    try { return JSON.parse(cached); } catch (e) { cache.remove(url); }
  }

  var response = UrlFetchApp.fetch(url, {
    headers: { "ngrok-skip-browser-warning": "true" },
    muteHttpExceptions: true
  });

  if (response.getResponseCode() >= 400) { return {}; }

  try {
    var payload = JSON.parse(response.getContentText());
    try { cache.put(url, JSON.stringify(payload), 300); } catch (cacheError) {}
    return payload;
  } catch (e) {
    return {};
  }
}

function emptyRow(payload, rowType) {
  var row = {};
  TEXT_FIELDS.forEach(function(id) { row[id] = ""; });
  DATE_FIELDS.forEach(function(id) { row[id] = ""; });
  NUMBER_FIELDS.forEach(function(id) { row[id] = 0; });
  row.row_type = rowType || "";
  row.organization_id = textValue(payload.organization_id || (payload.overview || {}).organization_id);
  setSyncFields(row, payload);
  return row;
}

function normalizeDashboardRow(payload, source) {
  var rowType = textValue(source.row_type || source.metric_mode || "daily");
  var row = emptyRow(payload, rowType);
  row.metric_mode = textValue(source.metric_mode || payload.metric_mode || rowType);
  row.date = firstValue(source.date, source.period, source.postDate, reportEndDate(payload));
  row.year_month = yearMonth(row.date);
  row.organization_id = textValue(firstValue(
    source.organization_id, payload.organization_id, (payload.overview || {}).organization_id));
  setAudienceFields(row, source);
  row.postId = textValue(source.postId || source.post_id);
  row.media_type = textValue(source.media_type);
  row.contentType = textValue(source.contentType || source.content_type || source.media_product_type);
  row.postDate = firstValue(source.postDate, (rowType === "post" || rowType === "reel" || rowType === "story") ? row.date : "");
  row.postText = textValue(source.postText || source.caption);
  row.permalink = textValue(source.permalink);
  row.period = textValue(source.period || (rowType === "daily" ? source.date : ""));
  row.period_type = textValue(source.period_type || rowType);
  setMetricFields(row, source);
  if (rowType === "daily" && !row.followers) {
    row.followers = totalFollowers(payload);
  }
  setComparisonFields(row, payload);
  return row;
}

function buildMetricRows(payload, sources, rowType) {
  return (sources || []).map(function(source) {
    var row = normalizeDashboardRow(payload, source);
    row.row_type = rowType;
    row.metric_mode = rowType;
    row.date = firstValue(source.period, source.date, reportEndDate(payload));
    row.year_month = yearMonth(row.date);
    row.period = textValue(source.period || source.date);
    row.period_type = textValue(source.period_type || rowType);
    return row;
  });
}

function buildComparisonRows(payload) {
  var rows = [];
  [["week_over_week", "week"], ["month_over_month", "month"]].forEach(function(config) {
    var comparison = (payload.comparisons && payload.comparisons[config[0]]) || null;
    if (!comparison) { return; }
    var row = emptyRow(payload, "comparison");
    row.metric_mode = "comparison";
    row.comparison_period = config[0];
    row.comparison_label = textValue(comparison.label);
    row.date = comparison.current_end || reportEndDate(payload);
    row.year_month = yearMonth(row.date);
    row.current_start = comparison.current_start;
    row.current_end = comparison.current_end;
    row.previous_start = comparison.previous_start;
    row.previous_end = comparison.previous_end;
    setMetricFields(row, comparison.current || {});
    setComparisonFields(row, payload);
    rows.push(row);
  });
  return rows;
}

function buildInsightRows(payload) {
  var insights = payload.performance_insights || {};
  var rows = [];
  var summary = emptyRow(payload, "summary");
  summary.metric_mode = "summary";
  summary.date = reportEndDate(payload);
  summary.year_month = yearMonth(summary.date);
  summary.insight_section = "Executive Summary";
  summary.insight = "Current reporting period summary";
  setMetricFields(summary, payload.overview || payload);
  summary.followers = totalFollowers(payload);
  setComparisonFields(summary, payload);
  rows.push(summary);

  var post = emptyRow(payload, "summary");
  post.metric_mode = "summary";
  post.date = reportEndDate(payload);
  post.year_month = yearMonth(post.date);
  post.insight_section = "Top Media Analysis";
  post.insight = textValue(insights.highest_engagement_post_id || insights.highest_reach_post_id
    || "No media-level data returned by Instagram");
  post.postId = textValue(insights.highest_engagement_post_id || insights.highest_reach_post_id);
  post.views = numberValue(insights.highest_reach_post_impressions);
  post.engagement_rate = numberValue(insights.highest_engagement_rate);
  rows.push(post);
  return rows;
}

function normalizeRows(payload) {
  payload = payload || {};
  var rows = [];
  var dashboardRows = payload.dashboard_rows || [];
  var audienceRows = payload.audience_analytics || [];
  var postRows = payload.post_analytics || [];

  if (dashboardRows.length) {
    rows = dashboardRows.map(function(source) { return normalizeDashboardRow(payload, source); });
  } else {
    var overview = payload.overview || {};
    (payload.daily_metrics || []).forEach(function(source) {
      var row = emptyRow(payload, "daily");
      row.metric_mode = textValue(source.metric_mode || "daily");
      row.date = source.date;
      row.year_month = yearMonth(row.date);
      setMetricFields(row, source);
      row.followers = numberValue(overview.followers) || totalFollowers(payload);
      setComparisonFields(row, payload);
      rows.push(row);
    });
    postRows.forEach(function(source) { rows.push(normalizeDashboardRow(payload, source)); });
    audienceRows.forEach(function(source) { rows.push(normalizeDashboardRow(payload, source)); });
  }

  return rows
    .concat(buildMetricRows(payload, payload.weekly_metrics, "weekly"))
    .concat(buildMetricRows(payload, payload.monthly_metrics, "monthly"))
    .concat(buildComparisonRows(payload))
    .concat(buildInsightRows(payload));
}

function valueForField(row, id) {
  if (DATE_FIELDS.indexOf(id) !== -1) { return ymd(row[id]); }
  if (TEXT_FIELDS.indexOf(id) !== -1) { return textValue(row[id]); }
  return numberValue(row[id]);
}

function filterRowsForRequestedFields(rows, requestedIds) {
  var hasCountry = requestedIds.indexOf("country_name") !== -1;
  var hasCity = requestedIds.indexOf("city") !== -1;

  if (hasCountry && !hasCity) {
    rows = rows.filter(function(row) {
      return row.row_type === "country" && textValue(row.country_name);
    });
  }
  if (hasCity && !hasCountry) {
    rows = rows.filter(function(row) {
      return row.row_type === "city" && textValue(row.city);
    });
  }
  return rows;
}

function getData(request) {
  var payload = fetchDashboard(request);
  var fields = getFields();
  var requestedIds = (request && request.fields)
    ? request.fields.map(function(field) { return field.name; })
    : DEFAULT_FIELD_IDS;
  var requestedFields = fields.forIds(requestedIds);
  var normalizedRows = filterRowsForRequestedFields(normalizeRows(payload), requestedIds);

  return {
    schema: requestedFields.build(),
    rows: normalizedRows.map(function(row) {
      return { values: requestedIds.map(function(id) { return valueForField(row, id); }) };
    })
  };
}

function isAdminUser() {
  return true;
}
