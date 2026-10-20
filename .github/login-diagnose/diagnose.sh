#!/usr/bin/env bash
# Read-only: whiteboard storyboard responses (status, finish reason, sizes, token counts only).
set -uo pipefail
echo "== storyboard model settings (names only)"
docker exec ai-growth-os-worker-1 python -c "
from app.core.config import get_settings
s = get_settings()
print('storyboard_model:', s.whiteboard_storyboard_model or '(default)')
print('xai_reasoning_effort:', s.xai_reasoning_effort)
"
echo "== storyboard responses, last 3 days (all containers)"
for c in ai-growth-os-worker-1 ai-growth-os-api-1; do
  docker logs --since 72h "$c" 2>&1 | grep -o 'whiteboard_storyboard_response {[^}]*}' | sed "s/^/$c /" | tail -40
done
echo "== whiteboard job errors, last 3 days (codes only)"
docker logs --since 72h ai-growth-os-worker-1 2>&1 | grep -oE "storyboard_(truncated|empty)|whiteboard_[a-z_]+ (failed|resum)[a-z_]*" | sort | uniq -c | head -20
echo DIAG_OK
