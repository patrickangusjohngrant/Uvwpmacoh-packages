#!/bin/sh
# Periodically refresh BBC news RSS into Uvwpmacoh-packages/news.xml so the
# guest can fetch it over plain HTTP from the local server (the guest's
# busybox wget does not handle TLS cleanly through user-mode networking).
#
# Usage: ./refresh-news.sh &  (or run from cron / systemd-timer / nohup)

set -e

cd "$(dirname "$0")"
INTERVAL="${INTERVAL:-300}"   # seconds between refreshes
URL="${URL:-https://feeds.bbci.co.uk/news/rss.xml}"
OUT="${OUT:-news.xml}"

while true; do
    curl -sSf -L "$URL" -o "${OUT}.new" && mv "${OUT}.new" "$OUT" \
        || echo "refresh-news: fetch failed, keeping previous $OUT" >&2
    sleep "$INTERVAL"
done
