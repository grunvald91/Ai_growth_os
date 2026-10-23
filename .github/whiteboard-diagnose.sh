#!/usr/bin/env bash
# Read-only. Prints only codes, statuses, counters and error types of whiteboard video jobs: no topics, no texts.
set -u
API=$(docker ps --filter label=com.docker.compose.project=ai-growth-os --filter label=com.docker.compose.service=api --format '{{.Names}}' | head -1)
WORKER=$(docker ps --filter label=com.docker.compose.project=ai-growth-os --filter label=com.docker.compose.service=worker --format '{{.Names}}' | head -1)
echo "api=$API worker=$WORKER"
echo "=== jobs"
docker exec -i "$API" python - <<'PY'
import json, collections
from sqlalchemy import select
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.domain import AgentRun, Artifact, Task
from app.content.whiteboard.jobs import job_directory
settings = get_settings()
summary = collections.Counter()
with SessionLocal() as db:
    tasks = db.scalars(select(Task).where(Task.assigned_agent == 'whiteboard_renderer')
                       .order_by(Task.created_at.desc()).limit(60)).all()
    print('total listed:', len(tasks))
    for t in reversed(tasks):
        arts = {a.kind: a for a in db.scalars(select(Artifact).where(Artifact.task_id == t.id))}
        req = json.loads(arts['whiteboard_request'].content) if 'whiteboard_request' in arts else {}
        state = json.loads(arts['whiteboard_job_state'].content) if 'whiteboard_job_state' in arts else {}
        r = req.get('request') or {}
        dur = (t.updated_at - t.created_at).total_seconds() if t.updated_at and t.created_at else None
        print('\n## %s %s created=%s dur=%ss scenes=%s calls=%s/%s review=%s img=%s' % (
            str(t.id)[:8], t.status.value, str(t.created_at)[:16], int(dur) if dur is not None else '?',
            r.get('scenes'), state.get('calls'), req.get('max_api_calls'), req.get('visual_review'), req.get('image_provider')))
        print('  models:', req.get('models'))
        print('  stage=%s error=%s failed_stage=%s repair_from=%s' % (
            state.get('stage'), state.get('error'), state.get('failed_stage'), bool(req.get('visual_repair_from'))))
        for e in state.get('errors') or []:
            keep = {k: e.get(k) for k in ('at', 'stage', 'code', 'calls', 'exception_type', 'http_status',
                                          'validation_types', 'reason', 'refunded', 'auto_resume') if k in e}
            keep['at'] = str(keep.get('at'))[:16]
            print('  err:', json.dumps(keep, ensure_ascii=False)[:400])
            summary[(e.get('code'), e.get('stage'), e.get('exception_type'), e.get('http_status'),
                     tuple(e.get('validation_types') or []))] += 1
        for run in db.scalars(select(AgentRun).where(AgentRun.task_id == t.id).order_by(AgentRun.started_at)):
            if run.status != 'completed':
                print('  run', run.agent_name, run.model, run.status, (run.error or '')[:80])
        d = job_directory(settings.products_root, t.product_id, t.id)
        m = d / 'stages' / 'manifest.json'
        if m.is_file():
            try:
                stages = json.loads(m.read_text()).get('stages', {})
                print('  stages:', {k: v.get('status') for k, v in stages.items()})
            except Exception as exc:
                print('  manifest unreadable', type(exc).__name__)
        if (d / 'stages').is_dir():
            names = sorted(f.name for f in (d / 'stages').iterdir() if 'provider-error' in f.name or f.name.endswith('.log'))
            if names:
                print('  diagnostic files:', names)
        out = sorted(p.name for p in (d / 'output').iterdir()) if (d / 'output').is_dir() else None
        print('  output:', out)
print('\n=== error summary (count, code, stage, exception, http, validation)')
for key, n in summary.most_common():
    print(n, key)
PY
echo "=== worker: failures by code/stage (14 days)"
docker logs --since 336h "$WORKER" 2>&1 | grep 'whiteboard_failed' | sed -E 's/.*"stage": ("[^"]*"|null).*"code": ("[^"]*").*/stage=\1 code=\2/' | sort | uniq -c | sort -rn | head -40
echo "=== worker: exception class names in tracebacks (72h)"
docker logs --since 72h "$WORKER" 2>&1 | grep -E '^[A-Za-z_.]+(Error|Exception)\b' | sed -E 's/:.*//' | sort | uniq -c | sort -rn | head -20
echo "=== container restarts / OOM"
docker inspect -f '{{.Name}} restarts={{.RestartCount}} oom={{.State.OOMKilled}} started={{.State.StartedAt}}' $WORKER $API
dmesg 2>/dev/null | grep -i -E 'out of memory|oom-kill' | tail -5 | sed -E 's/[0-9]+\.[0-9]+//'
echo "=== disk/mem"; df -h / | tail -1; free -m | sed -n 2p
