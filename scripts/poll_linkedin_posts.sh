#!/usr/bin/env bash
# Poll the LinkedIn new-post endpoint. When a new post is detected the endpoint
# refreshes cached analytics, so the Looker Studio dashboard reflects it on its
# next read. Intended to be run by cron every 15 minutes.
set -uo pipefail

URL="https://dosysanalytics.dosystemsinc.com/api/linkedin/posts/"
LOG="/var/log/linkedin_posts_poll.log"   # ensure this is writable by the cron user
TS="$(date '+%Y-%m-%d %H:%M:%S')"

# --fail-with-body: non-2xx still prints the body (so token/502 errors are logged).
RESP="$(curl -sS --max-time 60 --fail-with-body "$URL" 2>&1)"
CURL_RC=$?

if [ $CURL_RC -ne 0 ]; then
    echo "$TS ERROR curl_rc=$CURL_RC resp=$RESP" >>"$LOG"
    exit 1
fi

# Pull the two flags out of the JSON without needing jq.
NEW_COUNT="$(printf '%s' "$RESP" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("new_post_count",0))' 2>/dev/null || echo "?")"
REFRESHED="$(printf '%s' "$RESP" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("analytics_refreshed",False))' 2>/dev/null || echo "?")"

echo "$TS OK new_post_count=$NEW_COUNT analytics_refreshed=$REFRESHED" >>"$LOG"
