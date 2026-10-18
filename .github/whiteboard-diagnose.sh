#!/usr/bin/env bash
# Read-only: prints recent whiteboard job state, stage manifests and worker errors.
set -u
API=$(docker ps --filter label=com.docker.compose.project=ai-growth-os --filter label=com.docker.compose.service=api --format '{{.Names}}' | head -1)
WORKER=$(docker ps --filter label=com.docker.compose.project=ai-growth-os --filter label=com.docker.compose.service=worker --format '{{.Names}}' | head -1)
echo "api=$API worker=$WORKER"
docker ps --filter label=com.docker.compose.project=ai-growth-os --format '{{.Names}} {{.Image}} {{.Status}}'
echo "=== recent whiteboard jobs"
docker exec -i "$API" python - <<'PY'
import json
from sqlalchemy import select
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.domain import AgentRun, Artifact, Task
from app.content.whiteboard.jobs import job_directory
settings = get_settings()
with SessionLocal() as db:
    tasks = db.scalars(select(Task).where(Task.assigned_agent == 'whiteboard_renderer')
                       .order_by(Task.created_at.desc()).limit(6)).all()
    for t in tasks:
        arts = {a.kind: a for a in db.scalars(select(Artifact).where(Artifact.task_id == t.id))}
        req = json.loads(arts['whiteboard_request'].content) if 'whiteboard_request' in arts else {}
        state = json.loads(arts['whiteboard_job_state'].content) if 'whiteboard_job_state' in arts else {}
        print('\n### task', t.id, t.status.value, 'created', t.created_at, 'updated', t.updated_at)
        print('topic:', (req.get('request') or {}).get('topic'), '| max_api_calls:', req.get('max_api_calls'),
              '| visual_review:', req.get('visual_review'), '| image_provider:', req.get('image_provider'),
              '| repair_from:', req.get('visual_repair_from'))
        print('models:', req.get('models'))
        print('state:', json.dumps(state, ensure_ascii=False)[:2500])
        for r in db.scalars(select(AgentRun).where(AgentRun.task_id == t.id).order_by(AgentRun.started_at)):
            print('  run', r.started_at, r.finished_at, r.agent_name, r.model, r.status, r.error)
        d = job_directory(settings.products_root, t.product_id, t.id)
        m = d / 'stages' / 'manifest.json'
        if m.is_file():
            stages = json.loads(m.read_text()).get('stages', {})
            print('  stages:', {k: v.get('status') for k, v in stages.items()})
        if (d / 'stages').is_dir():
            for f in sorted((d / 'stages').iterdir()):
                if not f.name.endswith('.data') and f.name != 'manifest.json':
                    print('  diag file', f.name, f.stat().st_size)
                    if f.stat().st_size < 4000 and f.suffix in ('.json', '.txt', '.log'):
                        print('   ', f.read_text(errors='replace')[:1500])
        print('  output:', sorted(p.name for p in (d / 'output').iterdir()) if (d / 'output').is_dir() else None)
PY
echo "=== worker log (whiteboard, errors), last 72h"
docker logs --since 72h "$WORKER" 2>&1 | grep -E -i 'whiteboard|Traceback|Error|Exception' | tail -120
echo "=== disk"; df -h / | tail -1
