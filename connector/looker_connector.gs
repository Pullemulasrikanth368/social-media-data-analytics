var cc = DataStudioApp.createCommunityConnector();
var API_URL = "https://unnamed-anew-frays.ngrok-free.dev/api/linkedin-analytics/";

var TEXT_FIELDS = [
  "row_type", "organization_id", "metric_mode", "segment_type", "segment",
  "segment_label", "country_name", "industry_name",
  "postId", "contentType", "postText", "period", "period_type",
  "comparison_period", "comparison_label", "sync_granularity",
  "week_trend", "month_trend", "insight_section", "insight"
];

var DATE_FIELDS = [
  "date", "postDate", "sync_start_date", "sync_end_date",
  "current_start", "current_end", "previous_start", "previous_end"
];

var NUMBER_FIELDS = [
  "impressions", "unique_impressions", "reach", "clicks", "likes", "comments",
  "shares", "page_views", "careers_page_views", "followers_gained",
  "engagement", "engagement_rate", "ctr_percent", "total_engagements", "followers",
  "organic_followers", "paid_followers", "follower_count",
  "daily_impressions", "daily_clicks", "daily_followers", "daily_likes",
  "highest_reach_post_impressions", "highest_engagement_rate",
  "week_impressions_change_pct", "week_unique_impressions_change_pct",
  "week_reach_change_pct", "week_clicks_change_pct", "week_likes_change_pct",
  "week_comments_change_pct", "week_shares_change_pct",
  "week_page_views_change_pct", "week_careers_page_views_change_pct",
  "week_engagement_change_pct", "week_followers_change_pct",
  "month_impressions_change_pct", "month_unique_impressions_change_pct",
  "month_reach_change_pct", "month_clicks_change_pct", "month_likes_change_pct",
  "month_comments_change_pct", "month_shares_change_pct",
  "month_page_views_change_pct", "month_careers_page_views_change_pct",
  "month_engagement_change_pct", "month_followers_change_pct"
];

var DEFAULT_FIELD_IDS = [
  "row_type", "date", "organization_id", "impressions", "unique_impressions",
  "reach", "clicks", "likes", "comments", "shares", "page_views",
  "careers_page_views", "followers_gained", "engagement_rate", "ctr_percent",
  "total_engagements", "segment_type", "segment", "segment_label",
  "country_name", "industry_name", "followers",
  "organic_followers", "paid_followers", "postId", "contentType", "postDate",
  "postText", "period", "period_type"
];

function getAuthType() {
  return cc.newAuthTypeResponse().setAuthType(cc.AuthType.NONE).build();
}

function getConfig() {
  return cc.getConfig()
    .setDateRangeRequired(true)
    .build();
}

function addMetric(fields, id, name, aggregation) {
  fields.newMetric()
    .setId(id)
    .setName(name)
    .setType(cc.FieldType.NUMBER)
    .setAggregation(aggregation || cc.AggregationType.SUM);
}

function addDimension(fields, id, name, type) {
  fields.newDimension()
    .setId(id)
    .setName(name)
    .setType(type || cc.FieldType.TEXT);
}

function getFields() {
  var fields = cc.getFields();
  var agg = cc.AggregationType;
  var types = cc.FieldType;

  addDimension(fields, "row_type", "Row Type", types.TEXT);
  addDimension(fields, "date", "Date", types.YEAR_MONTH_DAY);
  addDimension(fields, "organization_id", "Organization ID", types.TEXT);
  addDimension(fields, "metric_mode", "Metric Mode", types.TEXT);
  addDimension(fields, "segment_type", "Audience Segment Type", types.TEXT);
  addDimension(fields, "segment", "Audience Segment", types.TEXT);
  addDimension(fields, "segment_label", "Audience Segment Label", types.TEXT);
  addDimension(fields, "country_name", "Country", types.TEXT);
  addDimension(fields, "industry_name", "Industry", types.TEXT);
  addDimension(fields, "postId", "Post ID", types.TEXT);
  addDimension(fields, "contentType", "Content Type", types.TEXT);
  addDimension(fields, "postDate", "Post Date", types.YEAR_MONTH_DAY);
  addDimension(fields, "postText", "Post Text", types.TEXT);
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

  addMetric(fields, "impressions", "Impressions", agg.SUM);
  addMetric(fields, "unique_impressions", "Unique Impressions", agg.SUM);
  addMetric(fields, "reach", "Reach", agg.SUM);
  addMetric(fields, "clicks", "Clicks", agg.SUM);
  addMetric(fields, "likes", "Likes", agg.SUM);
  addMetric(fields, "comments", "Comments", agg.SUM);
  addMetric(fields, "shares", "Shares", agg.SUM);
  addMetric(fields, "page_views", "Page Views", agg.SUM);
  addMetric(fields, "careers_page_views", "Careers Page Views", agg.SUM);
  addMetric(fields, "followers_gained", "Followers Gained", agg.SUM);
  addMetric(fields, "engagement", "Engagement", agg.AVG);
  addMetric(fields, "engagement_rate", "Engagement Rate", agg.AVG);
  addMetric(fields, "ctr_percent", "CTR %", agg.AVG);
  addMetric(fields, "total_engagements", "Total Engagements", agg.SUM);
  addMetric(fields, "followers", "Followers", agg.MAX);
  addMetric(fields, "follower_count", "Follower Count", agg.MAX);
  addMetric(fields, "organic_followers", "Organic Followers", agg.SUM);
  addMetric(fields, "paid_followers", "Paid Followers", agg.SUM);
  addMetric(fields, "daily_impressions", "Daily Impressions", agg.SUM);
  addMetric(fields, "daily_clicks", "Daily Clicks", agg.SUM);
  addMetric(fields, "daily_followers", "Daily Followers", agg.SUM);
  addMetric(fields, "daily_likes", "Daily Likes", agg.SUM);
  addMetric(fields, "highest_reach_post_impressions", "Highest Reach Post Impressions", agg.MAX);
  addMetric(fields, "highest_engagement_rate", "Highest Engagement Rate", agg.MAX);
  addMetric(fields, "week_impressions_change_pct", "WoW Impressions Change %", agg.AVG);
  addMetric(fields, "week_unique_impressions_change_pct", "WoW Unique Impressions Change %", agg.AVG);
  addMetric(fields, "week_reach_change_pct", "WoW Reach Change %", agg.AVG);
  addMetric(fields, "week_clicks_change_pct", "WoW Clicks Change %", agg.AVG);
  addMetric(fields, "week_likes_change_pct", "WoW Likes Change %", agg.AVG);
  addMetric(fields, "week_comments_change_pct", "WoW Comments Change %", agg.AVG);
  addMetric(fields, "week_shares_change_pct", "WoW Shares Change %", agg.AVG);
  addMetric(fields, "week_page_views_change_pct", "WoW Page Views Change %", agg.AVG);
  addMetric(fields, "week_careers_page_views_change_pct", "WoW Careers Page Views Change %", agg.AVG);
  addMetric(fields, "week_engagement_change_pct", "WoW Engagement Change %", agg.AVG);
  addMetric(fields, "week_followers_change_pct", "WoW Followers Change %", agg.AVG);
  addMetric(fields, "month_impressions_change_pct", "MoM Impressions Change %", agg.AVG);
  addMetric(fields, "month_unique_impressions_change_pct", "MoM Unique Impressions Change %", agg.AVG);
  addMetric(fields, "month_reach_change_pct", "MoM Reach Change %", agg.AVG);
  addMetric(fields, "month_clicks_change_pct", "MoM Clicks Change %", agg.AVG);
  addMetric(fields, "month_likes_change_pct", "MoM Likes Change %", agg.AVG);
  addMetric(fields, "month_comments_change_pct", "MoM Comments Change %", agg.AVG);
  addMetric(fields, "month_shares_change_pct", "MoM Shares Change %", agg.AVG);
  addMetric(fields, "month_page_views_change_pct", "MoM Page Views Change %", agg.AVG);
  addMetric(fields, "month_careers_page_views_change_pct", "MoM Careers Page Views Change %", agg.AVG);
  addMetric(fields, "month_engagement_change_pct", "MoM Engagement Change %", agg.AVG);
  addMetric(fields, "month_followers_change_pct", "MoM Followers Change %", agg.AVG);

  return fields;
}

function getSchema(request) {
  return { schema: getFields().build() };
}

function ymd(value) {
  if (!value) {
    return "";
  }
  if (value.$date) {
    value = value.$date;
  } else if (value.$numberLong) {
    value = Number(value.$numberLong);
  }
  if (value instanceof Date) {
    return value.toISOString().slice(0, 10).replace(/-/g, "");
  }
  if (typeof value === "number") {
    return new Date(value).toISOString().slice(0, 10).replace(/-/g, "");
  }
  var text = String(value);
  if (/^\d{8}$/.test(text)) {
    return text;
  }
  return text.replace(/-/g, "").slice(0, 8);
}

function numberValue(value) {
  var number = Number(value);
  return isNaN(number) ? 0 : number;
}

function textValue(value) {
  if (value === null || value === undefined) {
    return "";
  }
  return String(value);
}

function contentTypeValue(value) {
  var contentType = textValue(value).toUpperCase();
  if (["POST", "ARTICLE", "IMAGE", "VIDEO", "UNKNOWN"].indexOf(contentType) !== -1) {
    return contentType;
  }
  if (!contentType || /^[0-9]+$/.test(contentType)) {
    return "UNKNOWN";
  }
  if (contentType.indexOf("ARTICLE") !== -1) {
    return "ARTICLE";
  }
  if (contentType.indexOf("IMAGE") !== -1) {
    return "IMAGE";
  }
  if (contentType.indexOf("VIDEO") !== -1) {
    return "VIDEO";
  }
  if (contentType.indexOf("POST") !== -1) {
    return "POST";
  }
  return "UNKNOWN";
}

function firstValue() {
  for (var i = 0; i < arguments.length; i++) {
    if (arguments[i] !== null && arguments[i] !== undefined && arguments[i] !== "") {
      return arguments[i];
    }
  }
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
  var followers = payload.followers || {};
  return numberValue(firstValue(
    overview.followers,
    overview.follower_count,
    payload.follower_count,
    followers.firstDegreeSize,
    payload.followers
  ));
}

function setSyncFields(row, payload) {
  var syncRange = payload.sync_range || {};
  row.sync_start_date = reportStartDate(payload);
  row.sync_end_date = reportEndDate(payload);
  row.sync_granularity = textValue(syncRange.granularity);
}

function setMetricFields(row, source) {
  var metricIds = [
    "impressions", "unique_impressions", "reach", "clicks", "likes", "comments",
    "shares", "page_views", "careers_page_views", "followers_gained",
    "engagement", "engagement_rate", "ctr_percent", "total_engagements",
    "followers", "organic_followers", "paid_followers", "follower_count"
  ];
  metricIds.forEach(function(id) {
    row[id] = numberValue(source[id]);
  });
  if (!row.engagement && source.engagement_rate) {
    row.engagement = numberValue(source.engagement_rate);
  }
  if (!row.engagement_rate && source.engagement) {
    row.engagement_rate = numberValue(source.engagement);
  }
}

function setAudienceFields(row, source) {
  row.segment_type = textValue(source.segment_type || source.audience_segment_type);
  row.segment = textValue(source.segment || source.audience_segment);
  row.segment_label = textValue(
    source.segment_label || source.country_name || source.industry_name || row.segment
  );
  row.country_name = row.segment_type === "Followers by Country"
    ? textValue(source.country_name || source.segment_label || row.segment_label)
    : "";
  row.industry_name = row.segment_type === "Followers by Industry"
    ? textValue(source.industry_name || source.segment_label || row.segment_label)
    : "";
}

function setTopLevelMetricFields(row, payload) {
  var overview = payload.overview || {};
  var source = overview.impressions || overview.clicks ? overview : payload;
  setMetricFields(row, source);
  row.followers = totalFollowers(payload);
  row.follower_count = totalFollowers(payload);
  row.daily_impressions = numberValue(payload.daily_impressions);
  row.daily_clicks = numberValue(payload.daily_clicks);
  row.daily_followers = numberValue(payload.daily_followers);
  row.daily_likes = numberValue(payload.daily_likes);
}

function setComparisonFields(row, payload) {
  var mappings = [
    ["impressions", "impressions"],
    ["unique_impressions", "unique_impressions"],
    ["reach", "reach"],
    ["clicks", "clicks"],
    ["likes", "likes"],
    ["comments", "comments"],
    ["shares", "shares"],
    ["page_views", "page_views"],
    ["careers_page_views", "careers_page_views"],
    ["engagement_rate", "engagement"],
    ["followers_gained", "followers"]
  ];
  mappings.forEach(function(mapping) {
    row["week_" + mapping[1] + "_change_pct"] = comparisonMetric(
      payload, "week_over_week", mapping[0] + "_change_pct"
    );
    row["month_" + mapping[1] + "_change_pct"] = comparisonMetric(
      payload, "month_over_month", mapping[0] + "_change_pct"
    );
  });
  row.week_trend = comparisonTrend(payload, "week_over_week", "impressions_trend");
  row.month_trend = comparisonTrend(payload, "month_over_month", "impressions_trend");
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
    try {
      return JSON.parse(cached);
    } catch (e) {
      cache.remove(url);
    }
  }


  var response = UrlFetchApp.fetch(url, {
    headers: { "ngrok-skip-browser-warning": "true" },
    muteHttpExceptions: true
  });

  if (response.getResponseCode() >= 400) {
    return {};
  }

  try {
    var payload = JSON.parse(response.getContentText());
    try {
      cache.put(url, JSON.stringify(payload), 300);
    } catch (cacheError) {
      // Large responses can exceed Apps Script cache limits; data still returns normally.
    }
    return payload;
  } catch (e) {
    return {};
  }
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

function buildInsightRows(payload) {
  var insights = payload.performance_insights || {};
  var topPostId = insights.highest_engagement_post_id || insights.highest_reach_post_id || "";
  var rows = [];
  var summary = emptyRow(payload, "summary");
  summary.metric_mode = "summary";
  summary.date = reportEndDate(payload);
  summary.insight_section = "Executive Summary";
  summary.insight = "Current reporting period summary";
  setTopLevelMetricFields(summary, payload);
  setComparisonFields(summary, payload);
  rows.push(summary);

  var post = emptyRow(payload, "summary");
  post.metric_mode = "summary";
  post.date = reportEndDate(payload);
  post.insight_section = "Top Post Analysis";
  post.insight = topPostId || "No post-level data returned by LinkedIn";
  post.postId = textValue(topPostId);
  post.contentType = contentTypeValue(insights.best_content_type || "");
  post.highest_reach_post_impressions = numberValue(insights.highest_reach_post_impressions);
  post.highest_engagement_rate = numberValue(insights.highest_engagement_rate);
  post.impressions = numberValue(insights.highest_reach_post_impressions);
  post.engagement_rate = numberValue(insights.highest_engagement_rate);
  post.engagement = numberValue(insights.highest_engagement_rate);
  rows.push(post);

  var active = emptyRow(payload, "summary");
  active.metric_mode = "summary";
  active.date = reportEndDate(payload);
  active.insight_section = "Weekly Insights";
  active.insight = insights.most_active_engagement_day || "No active day identified";
  rows.push(active);
  return rows;
}

function normalizeDashboardRow(payload, source) {
  var rowType = textValue(source.row_type || source.metric_mode || "daily");
  var row = emptyRow(payload, rowType);
  row.metric_mode = textValue(source.metric_mode || payload.metric_mode || rowType);
  row.date = firstValue(source.date, source.period, source.postDate, reportEndDate(payload));
  row.organization_id = textValue(firstValue(
    source.organization_id,
    payload.organization_id,
    (payload.overview || {}).organization_id
  ));
  setAudienceFields(row, source);
  row.postId = textValue(source.postId || source.post_id);
  var rawContentType = firstValue(source.contentType, source.content_type);
  row.contentType = rawContentType ? contentTypeValue(rawContentType) : (rowType === "post" ? "UNKNOWN" : "");
  row.postDate = firstValue(source.postDate, rowType === "post" ? row.date : "");
  row.postText = textValue(source.postText || source.title || source.commentary);
  row.period = textValue(source.period || (rowType === "daily" ? source.date : ""));
  row.period_type = textValue(source.period_type || rowType);
  setMetricFields(row, source);
  if (rowType === "daily" && !row.followers) {
    row.followers = totalFollowers(payload);
    row.follower_count = totalFollowers(payload);
  }
  if (row.followers && !row.follower_count) {
    row.follower_count = row.followers;
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
    row.period = textValue(source.period || source.date);
    row.period_type = textValue(source.period_type || rowType);
    return row;
  });
}

function buildComparisonRows(payload) {
  var rows = [];
  [
    ["week_over_week", "week"],
    ["month_over_month", "month"]
  ].forEach(function(config) {
    var comparison = (payload.comparisons && payload.comparisons[config[0]]) || null;
    if (!comparison) {
      return;
    }
    var row = emptyRow(payload, "comparison");
    row.metric_mode = "comparison";
    row.comparison_period = config[0];
    row.comparison_label = textValue(comparison.label);
    row.date = comparison.current_end || reportEndDate(payload);
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

function normalizeRows(payload) {
  payload = payload || {};
  var rows = [];
  var dashboardRows = payload.dashboard_rows || [];
  var dailyRows = payload.daily_metrics || [];
  var audienceRows = payload.audience_analytics || [];
  var postRows = payload.post_analytics || [];

  if (dashboardRows.length) {
    rows = dashboardRows.map(function(source) {
      return normalizeDashboardRow(payload, source);
    });
  } else {
    var overview = payload.overview || {};

    dailyRows.forEach(function(source) {
      var row = emptyRow(payload, "daily");
      row.metric_mode = textValue(source.metric_mode || payload.metric_mode || "daily");
      row.date = source.date;
      row.organization_id = textValue(source.organization_id || row.organization_id);
      setMetricFields(row, source);
      row.followers = numberValue(overview.followers) || totalFollowers(payload);
      row.follower_count = row.followers;
      setComparisonFields(row, payload);
      rows.push(row);
    });

    audienceRows.forEach(function(source) {
      var row = emptyRow(payload, "audience");
      row.metric_mode = "audience";
      row.date = reportEndDate(payload);
      row.organization_id = textValue(source.organization_id || row.organization_id);
      setAudienceFields(row, source);
      setMetricFields(row, source);
      row.followers = numberValue(source.followers);
      row.follower_count = numberValue(source.followers);
      rows.push(row);
    });

    postRows.forEach(function(source) {
      var row = emptyRow(payload, "post");
      row.metric_mode = "post";
      row.date = source.postDate || reportEndDate(payload);
      row.organization_id = textValue(source.organization_id || row.organization_id);
      row.postId = textValue(source.postId || source.post_id);
      row.contentType = contentTypeValue(source.contentType || source.content_type || "UNKNOWN");
      row.postDate = source.postDate || row.date;
      row.postText = textValue(source.postText || source.title);
      setMetricFields(row, source);
      if (!row.postId) {
        console.log("Missing postId in post_analytics row");
      }
      rows.push(row);
    });
  }

  if (!audienceRows.length) {
    console.log("Missing audience_analytics in dashboard payload");
  }
  if (!postRows.length) {
    console.log("Empty post_analytics in dashboard payload");
  }

  return rows
    .concat(buildMetricRows(payload, payload.weekly_metrics, "weekly"))
    .concat(buildMetricRows(payload, payload.monthly_metrics, "monthly"))
    .concat(buildComparisonRows(payload))
    .concat(buildInsightRows(payload));
}

function valueForField(row, id) {
  if (DATE_FIELDS.indexOf(id) !== -1) {
    return ymd(row[id]);
  }
  if (TEXT_FIELDS.indexOf(id) !== -1) {
    return textValue(row[id]);
  }
  return numberValue(row[id]);
}

function filterRowsForRequestedFields(rows, requestedIds) {
  var hasCountry = requestedIds.indexOf("country_name") !== -1;
  var hasIndustry = requestedIds.indexOf("industry_name") !== -1;

  if (hasCountry && !hasIndustry) {
    rows = rows.filter(function(row) {
      return row.row_type === "audience"
        && row.segment_type === "Followers by Country"
        && textValue(row.country_name);
    });
  }
  if (hasIndustry && !hasCountry) {
    rows = rows.filter(function(row) {
      return row.row_type === "audience"
        && row.segment_type === "Followers by Industry"
        && textValue(row.industry_name);
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
      return {
        values: requestedIds.map(function(id) {
          return valueForField(row, id);
        })
      };
    })
  };
}

function isAdminUser() {
  return true;
}
