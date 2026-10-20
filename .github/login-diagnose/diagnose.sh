#!/usr/bin/env bash
# Read-only: whiteboard jobs state and storyboard responses (codes, counts, token numbers only).
set -uo pipefail
echo "== storyboard responses, last 6 hours"
docker logs --since 6h ai-growth-os-worker-1 2>&1 | grep -o 'whiteboard_storyboard_response {[^}]*}' | tail -20
echo "== whiteboard jobs updated in the last day (stage, error code, auto resumes)"
docker exec -i ai-growth-os-worker-1 python - <<'PY'
import json
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.domain import Artifact, Task
db = SessionLocal()
since = datetime.now(timezone.utc) - timedelta(days=1)
for task in db.scalars(select(Task).where(Task.assigned_agent.like('%whiteboard%'), Task.updated_at > since)):
    row = db.scalar(select(Artifact).where(Artifact.task_id == task.id, Artifact.kind.like('%state%')).order_by(Artifact.version.desc()).limit(1))
    state = json.loads(row.content) if row else {}
    print(task.status.value, "stage=" + str(state.get("stage")), "error=" + str(state.get("error")),
          "auto_resumes=" + str(state.get("auto_resumes")), "updated=" + task.updated_at.strftime("%H:%M UTC"))
PY
echo DIAG_OK
