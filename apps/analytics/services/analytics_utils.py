from collections import defaultdict
from datetime import date, datetime, timedelta, timezone


METRIC_FIELDS = (
    "impressions",
    "unique_impressions",
    "reach",
    "clicks",
    "likes",
    "comments",
    "shares",
    "page_views",
    "careers_page_views",
    "followers_gained",
)

POST_CONTENT_TYPES = ("ARTICLE", "IMAGE", "VIDEO", "POST")


def utc_today():
    return datetime.now(timezone.utc).date()


def parse_date(value, default=None):
    if not value:
        return default
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def default_date_range(days=30):
    end_date = utc_today()
    return end_date - timedelta(days=days - 1), end_date


def linkedin_time_intervals(start_date, end_date, granularity="DAY"):
    start = parse_date(start_date)
    end = parse_date(end_date)
    start_ms = int(datetime.combine(start, datetime.min.time(), timezone.utc).timestamp() * 1000)
    end_exclusive = end + timedelta(days=1)
    end_ms = int(datetime.combine(end_exclusive, datetime.min.time(), timezone.utc).timestamp() * 1000)
    return f"(timeRange:(start:{start_ms},end:{end_ms}),timeGranularityType:{granularity})"


def linkedin_time_interval_params(start_date, end_date, granularity="DAY"):
    start = parse_date(start_date)
    end = parse_date(end_date)
    start_ms = int(datetime.combine(start, datetime.min.time(), timezone.utc).timestamp() * 1000)
    end_exclusive = end + timedelta(days=1)
    end_ms = int(datetime.combine(end_exclusive, datetime.min.time(), timezone.utc).timestamp() * 1000)
    return {
        "timeIntervals.timeGranularityType": granularity,
        "timeIntervals.timeRange.start": start_ms,
        "timeIntervals.timeRange.end": end_ms,
    }


def date_from_ms(value):
    if not value:
        return None
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).date().isoformat()


def safe_number(value, default=0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value):
    return int(safe_number(value, 0))


def percent_change(current, previous):
    current = safe_number(current)
    previous = safe_number(previous)
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return round(((current - previous) / previous) * 100, 2)


def trend_direction(change_pct):
    if change_pct > 0:
        return "up"
    if change_pct < 0:
        return "down"
    return "flat"


def engagement_rate(metrics):
    impressions = safe_number(metrics.get("impressions"))
    if impressions <= 0:
        return 0.0
    actions = (
        safe_number(metrics.get("clicks"))
        + safe_number(metrics.get("likes"))
        + safe_number(metrics.get("comments"))
        + safe_number(metrics.get("shares"))
    )
    return round(actions / impressions, 6)


def ctr_percent(metrics):
    impressions = safe_number(metrics.get("impressions"))
    if impressions <= 0:
        return 0.0
    return round((safe_number(metrics.get("clicks")) / impressions) * 100, 4)


def total_engagements(metrics):
    return safe_int(
        safe_number(metrics.get("clicks"))
        + safe_number(metrics.get("likes"))
        + safe_number(metrics.get("comments"))
        + safe_number(metrics.get("shares"))
    )


def string_value(value, default=""):
    if value is None:
        return default
    return str(value)


def extract_share_metrics(stats):
    unique_impressions = safe_int(
        stats.get("uniqueImpressionsCount", stats.get("uniqueImpressionsCounts"))
    )
    impressions = safe_int(stats.get("impressionCount"))
    return {
        "impressions": impressions,
        "unique_impressions": unique_impressions,
        "reach": unique_impressions or impressions,
        "clicks": safe_int(stats.get("clickCount")),
        "likes": safe_int(stats.get("likeCount")),
        "comments": safe_int(stats.get("commentCount")),
        "shares": safe_int(stats.get("shareCount")),
        "engagement": safe_number(stats.get("engagement")),
    }


def extract_page_metrics(page_stats):
    views = page_stats.get("views", {}) if isinstance(page_stats, dict) else {}
    all_page_views = views.get("allPageViews", {}) or {}
    careers_views = views.get("careersPageViews", {}) or {}
    return {
        "page_views": safe_int(all_page_views.get("pageViews")),
        "careers_page_views": safe_int(careers_views.get("pageViews")),
    }


def normalize_share_time_series(elements):
    rows = []
    for element in elements or []:
        day = date_from_ms((element.get("timeRange") or {}).get("start"))
        if not day:
            continue
        metrics = extract_share_metrics(element.get("totalShareStatistics") or {})
        metrics["engagement_rate"] = metrics["engagement"] or engagement_rate(metrics)
        metrics["ctr_percent"] = ctr_percent(metrics)
        metrics["total_engagements"] = total_engagements(metrics)
        rows.append({"date": day, **metrics})
    return rows


def normalize_page_time_series(elements):
    rows = []
    for element in elements or []:
        day = date_from_ms((element.get("timeRange") or {}).get("start"))
        if not day:
            continue
        rows.append({"date": day, **extract_page_metrics(element.get("totalPageStatistics") or {})})
    return rows


def normalize_follower_time_series(elements):
    rows = []
    for element in elements or []:
        day = date_from_ms((element.get("timeRange") or {}).get("start"))
        if not day:
            continue
        organic = safe_int(element.get("organicFollowerCount"))
        paid = safe_int(element.get("paidFollowerCount"))
        rows.append({"date": day, "followers_gained": organic + paid})
    return rows


def merge_daily_rows(*row_groups):
    merged = defaultdict(lambda: {field: 0 for field in METRIC_FIELDS})
    for rows in row_groups:
        for row in rows or []:
            day = row.get("date")
            if not day:
                continue
            merged[day]["date"] = day
            for key, value in row.items():
                if key == "date":
                    continue
                if key == "engagement_rate":
                    merged[day][key] = safe_number(value)
                else:
                    merged[day][key] = merged[day].get(key, 0) + safe_number(value)

    output = []
    for day in sorted(merged):
        row = dict(merged[day])
        row["engagement_rate"] = row.get("engagement_rate") or engagement_rate(row)
        row["ctr_percent"] = ctr_percent(row)
        row["total_engagements"] = total_engagements(row)
        output.append(row)
    return output


def aggregate_metrics(rows, metric_fields=None, engagement_fn=None):
    """Sum the given metric fields across rows and recompute derived metrics.

    ``metric_fields``/``engagement_fn`` default to LinkedIn's set and formula so
    existing callers are unchanged; other platforms pass their own (see the
    platform registry) to aggregate the columns and engagement definition that
    actually apply to them.
    """
    metric_fields = metric_fields or METRIC_FIELDS
    engagement_fn = engagement_fn or engagement_rate
    totals = {field: 0 for field in metric_fields}
    for row in rows or []:
        for field in metric_fields:
            totals[field] += safe_number(row.get(field))
    totals = {key: int(value) if float(value).is_integer() else value for key, value in totals.items()}
    totals["engagement_rate"] = engagement_fn(totals)
    totals["ctr_percent"] = ctr_percent(totals)
    totals["total_engagements"] = total_engagements(totals)
    return totals


def aggregate_by_period(rows, period, metric_fields=None, engagement_fn=None):
    buckets = defaultdict(list)
    for row in rows or []:
        day = parse_date(row.get("date"))
        if not day:
            continue
        if period == "week":
            period_start = day - timedelta(days=day.weekday())
            key = period_start.isoformat()
        elif period == "month":
            key = day.replace(day=1).isoformat()
        else:
            key = day.isoformat()
        buckets[key].append(row)

    output = []
    for key in sorted(buckets):
        metrics = aggregate_metrics(buckets[key], metric_fields, engagement_fn)
        output.append({"period": key, "period_type": period, **metrics})
    return output


def rows_between(rows, start_date, end_date):
    start = parse_date(start_date)
    end = parse_date(end_date)
    return [
        row for row in rows or []
        if start <= parse_date(row.get("date"), start) <= end
    ]


def build_period_comparison(rows, current_start, current_end, label, metric_fields=None, engagement_fn=None):
    metric_fields = metric_fields or METRIC_FIELDS
    current_start = parse_date(current_start)
    current_end = parse_date(current_end)
    period_days = (current_end - current_start).days + 1
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=period_days - 1)

    current = aggregate_metrics(rows_between(rows, current_start, current_end), metric_fields, engagement_fn)
    previous = aggregate_metrics(rows_between(rows, previous_start, previous_end), metric_fields, engagement_fn)

    changes = {}
    for field in (*metric_fields, "engagement_rate"):
        change = percent_change(current.get(field), previous.get(field))
        changes[f"{field}_change_pct"] = change
        changes[f"{field}_trend"] = trend_direction(change)

    return {
        "label": label,
        "current_start": current_start.isoformat(),
        "current_end": current_end.isoformat(),
        "previous_start": previous_start.isoformat(),
        "previous_end": previous_end.isoformat(),
        "current": current,
        "previous": previous,
        "changes": changes,
    }


def build_comparisons(rows, today=None, metric_fields=None, engagement_fn=None):
    today = parse_date(today, utc_today())
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    return {
        "week_over_week": build_period_comparison(
            rows, week_start, today, "Current week vs previous week", metric_fields, engagement_fn),
        "month_over_month": build_period_comparison(
            rows, month_start, today, "Current month vs previous month", metric_fields, engagement_fn),
    }


def _post_id_from_element(element):
    for key in ("share", "ugcPost", "post", "id", "activity"):
        if element.get(key):
            return string_value(element[key])
    return ""


def _post_metadata_id(item):
    post_id = _post_id_from_element(item)
    if post_id:
        return post_id
    for key in ("entity", "urn"):
        if item.get(key):
            return string_value(item[key])
    return ""


def _nested_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "title", "value", "localized", "localizedName"):
            if value.get(key):
                return _nested_text(value[key])
        for nested in value.values():
            text = _nested_text(nested)
            if text:
                return text
    if isinstance(value, list):
        for item in value:
            text = _nested_text(item)
            if text:
                return text
    return ""


def _metadata_text(item):
    for key in ("commentary", "text", "title", "subject", "description"):
        text = _nested_text(item.get(key))
        if text:
            return text
    content = item.get("content") or {}
    if isinstance(content, dict):
        for key in ("title", "description", "article", "media"):
            text = _nested_text(content.get(key))
            if text:
                return text
    return ""


def _metadata_date(item):
    for key in ("publishedAt", "createdAt", "lastModifiedAt"):
        value = item.get(key)
        if value:
            try:
                return date_from_ms(value)
            except (TypeError, ValueError):
                pass
    created = item.get("created") or {}
    if isinstance(created, dict) and created.get("time"):
        return date_from_ms(created.get("time"))
    return ""


def derive_content_type(item):
    candidates = [
        item.get("contentType"),
        item.get("content_type"),
        item.get("type"),
        item.get("mediaType"),
        item.get("specificContent", {}).get("shareContent", {}).get("shareMediaCategory")
        if isinstance(item.get("specificContent"), dict) else "",
    ]
    content = item.get("content") or {}
    if isinstance(content, dict):
        candidates.extend(content.keys())
    raw = " ".join(string_value(candidate).upper() for candidate in candidates if candidate)
    for content_type in POST_CONTENT_TYPES:
        if content_type in raw:
            return content_type
    if "URN:LI:ARTICLE" in string_value(_post_metadata_id(item)).upper():
        return "ARTICLE"
    return "POST" if _post_metadata_id(item) else "UNKNOWN"


def canonical_content_type(value):
    content_type = string_value(value).strip().upper()
    if content_type in POST_CONTENT_TYPES or content_type == "UNKNOWN":
        return content_type
    if not content_type or content_type.isdigit():
        return "UNKNOWN"
    for allowed in POST_CONTENT_TYPES:
        if allowed in content_type:
            return allowed
    return "UNKNOWN"


def _metadata_map(metadata_elements):
    mapped = {}
    for item in metadata_elements or []:
        post_id = _post_metadata_id(item)
        if not post_id:
            continue
        mapped[post_id] = {
            "postId": post_id,
            "post_id": post_id,
            "contentType": derive_content_type(item),
            "content_type": derive_content_type(item),
            "postDate": _metadata_date(item),
            "postText": _metadata_text(item),
        }
    return mapped


def _post_row(post_id, metadata, metrics=None):
    metrics = metrics or {}
    content_type = canonical_content_type(metadata.get("contentType") or metadata.get("content_type"))
    return {
        "postId": string_value(post_id),
        "post_id": string_value(post_id),
        "contentType": content_type,
        "content_type": content_type,
        "postDate": metadata.get("postDate", ""),
        "postText": metadata.get("postText", ""),
        **metrics,
    }


def normalize_post_rows(elements, metadata_elements=None):
    posts = []
    metadata_by_id = _metadata_map(metadata_elements)
    seen_ids = set()

    for element in elements or []:
        post_id = _post_id_from_element(element)
        if not post_id:
            continue
        metrics = extract_share_metrics(element.get("totalShareStatistics") or {})
        metrics["engagement_rate"] = metrics["engagement"] or engagement_rate(metrics)
        metrics["ctr_percent"] = ctr_percent(metrics)
        metrics["total_engagements"] = total_engagements(metrics)
        metadata = metadata_by_id.get(post_id, {
            "contentType": derive_content_type(element),
            "postDate": "",
            "postText": "",
        })
        posts.append(_post_row(post_id, metadata, metrics))
        seen_ids.add(post_id)

    for post_id, metadata in metadata_by_id.items():
        if post_id in seen_ids:
            continue
        metrics = extract_share_metrics({})
        metrics["engagement_rate"] = 0.0
        metrics["ctr_percent"] = 0.0
        metrics["total_engagements"] = 0
        posts.append(_post_row(post_id, metadata, metrics))

    return sorted(posts, key=lambda item: (item.get("engagement_rate", 0), item.get("impressions", 0)), reverse=True)


def top_post(posts, metric):
    if not posts:
        return {}
    return max(posts, key=lambda item: safe_number(item.get(metric)))


def summarize_performance(rows, posts, reach_metric="impressions"):
    """Executive-summary insights. ``reach_metric`` is the field used to rank the
    "highest reach" post — ``impressions`` for LinkedIn, ``views`` for Instagram
    (LinkedIn's impressions metric was removed from the Meta Graph API)."""
    best_reach = top_post(posts, reach_metric)
    best_engagement = top_post(posts, "engagement_rate")
    active_day = top_post(rows, "engagement_rate")
    return {
        "highest_reach_post_id": best_reach.get("postId") or best_reach.get("post_id", ""),
        "highest_reach_post_impressions": best_reach.get(reach_metric, 0),
        "highest_engagement_post_id": best_engagement.get("postId") or best_engagement.get("post_id", ""),
        "highest_engagement_rate": best_engagement.get("engagement_rate", 0),
        "best_content_type": best_engagement.get("contentType") or best_engagement.get("content_type", ""),
        "most_active_engagement_day": active_day.get("date", ""),
    }


def tag_rows(rows, row_type, extra=None):
    """Prefix each row with a ``row_type`` (and optional shared fields). Used to
    assemble platform dashboard rows without duplicating the tagging logic."""
    extra = extra or {}
    return [{"row_type": row_type, **extra, **dict(row)} for row in rows or []]


AUDIENCE_BREAKDOWNS = {
    "followerCountsByGeoCountry": "Followers by Country",
    "followerCountsByIndustry": "Followers by Industry",
    "followerCountsByFunction": "Followers by Job Function",
    "followerCountsBySeniority": "Followers by Seniority",
    "followerCountsByStaffCountRange": "Followers by Company Size",
}


LINKEDIN_COUNTRY_NAMES = {
    "100565514": "Belgium",
    "101174742": "Canada",
    "101282230": "Germany",
    "101355337": "Japan",
    "101452733": "Australia",
    "101728296": "Russia",
    "102095887": "New Zealand",
    "102713980": "India",
    "102890719": "Netherlands",
    "102890883": "China",
    "103323778": "Mexico",
    "103350119": "Italy",
    "103644278": "United States",
    "104305776": "United Arab Emirates",
    "105015875": "France",
    "105117694": "Sweden",
    "105149562": "South Korea",
    "105646813": "Spain",
    "106057199": "Brazil",
    "106693272": "Switzerland",
}


LINKEDIN_INDUSTRY_NAMES = {
    "4": "Software Development",
    "6": "Internet",
    "96": "Information Technology & Services",
}


def _linkedin_urn_id(value):
    text = string_value(value)
    if text.startswith("urn:li:"):
        return text.rsplit(":", 1)[-1]
    return text


def resolve_country_name(value):
    text = string_value(value)
    urn_id = _linkedin_urn_id(text)
    return LINKEDIN_COUNTRY_NAMES.get(urn_id) or (f"LinkedIn Geo {urn_id}" if urn_id else "")


def resolve_industry_name(value):
    text = string_value(value)
    urn_id = _linkedin_urn_id(text)
    return LINKEDIN_INDUSTRY_NAMES.get(urn_id) or (f"LinkedIn Industry {urn_id}" if urn_id else "")


def normalize_audience_dashboard_row(row, organization_id="", report_date=""):
    audience = dict(row or {})
    segment_type = string_value(audience.get("segment_type") or audience.get("audience_segment_type"))
    segment = string_value(audience.get("segment") or audience.get("audience_segment"))

    country_name = string_value(audience.get("country_name"))
    industry_name = string_value(audience.get("industry_name"))
    if segment_type == "Followers by Country":
        country_name = country_name or resolve_country_name(segment)
        industry_name = ""
    elif segment_type == "Followers by Industry":
        industry_name = industry_name or resolve_industry_name(segment)
        country_name = ""

    segment_label = string_value(
        audience.get("segment_label")
        or country_name
        or industry_name
        or segment
    )

    audience.update({
        "row_type": "audience",
        "metric_mode": "audience",
        "date": string_value(audience.get("date") or report_date),
        "organization_id": string_value(audience.get("organization_id") or organization_id),
        "segment_type": segment_type,
        "segment": segment,
        "segment_label": segment_label,
        "country_name": country_name,
        "industry_name": industry_name,
        "followers": safe_int(audience.get("followers")),
        "organic_followers": safe_int(audience.get("organic_followers")),
        "paid_followers": safe_int(audience.get("paid_followers")),
    })
    return audience


def _facet_name(item, fallback):
    for key in ("geo", "country", "industry", "function", "seniority", "staffCountRange", "localizedName", "name"):
        if item.get(key):
            return str(item[key])
    return fallback


def normalize_audience_rows(elements):
    rows = []
    for element in elements or []:
        for key, label in AUDIENCE_BREAKDOWNS.items():
            for index, item in enumerate(element.get(key) or []):
                counts = item.get("followerCounts") or {}
                organic = safe_int(counts.get("organicFollowerCount"))
                paid = safe_int(counts.get("paidFollowerCount"))
                rows.append({
                    "segment_type": label,
                    "segment": _facet_name(item, f"{label} {index + 1}"),
                    "followers": organic + paid,
                    "organic_followers": organic,
                    "paid_followers": paid,
                })
    return sorted(
        [normalize_audience_dashboard_row(row) for row in rows],
        key=lambda item: item["followers"],
        reverse=True,
    )


def build_dashboard_rows(daily_rows, audience_rows=None, post_rows=None, report_date=""):
    rows = []
    for row in daily_rows or []:
        rows.append({"row_type": "daily", **row})
    for row in audience_rows or []:
        rows.append(normalize_audience_dashboard_row(row, report_date=report_date))
    for row in post_rows or []:
        post = dict(row)
        post_id = string_value(post.get("postId") or post.get("post_id"))
        content_type = canonical_content_type(post.get("contentType") or post.get("content_type"))
        post["postId"] = post["post_id"] = post_id
        post["contentType"] = post["content_type"] = content_type
        rows.append({"row_type": "post", **post})
    return rows
