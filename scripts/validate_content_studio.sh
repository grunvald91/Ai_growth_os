#!/usr/bin/env bash
# Offline validation for Content Studio. No GitHub Actions, network or production server.
set -Eeuo pipefail
cd "$(dirname "$0")/.."

python -m compileall -q app tests

python -m pytest -q \
  tests/test_content_studio.py \
  tests/test_content_studio_auth.py \
  tests/test_content_studio_audit_fixes.py \
  tests/test_content_calendar.py \
  tests/test_content_factory.py \
  tests/test_articles.py \
  tests/test_quick_post.py \
  tests/test_content_quick_post_owner.py \
  tests/test_content_fallbacks.py \
  tests/test_content_quality.py \
  tests/test_post_revisions.py \
  tests/test_content_creative.py \
  tests/test_telegram_publishing.py \
  tests/test_telegram_content_owner_publishing.py \
  tests/test_content_studio_v5.py

if [[ "${FULL_TESTS:-0}" == "1" ]]; then
  python -m pytest -q
fi

