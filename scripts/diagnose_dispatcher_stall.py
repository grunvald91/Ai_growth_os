"""Read-only diagnosis of Growth dispatcher responsiveness. No message text, credentials, or private records in logs."""
from __future__ import annotations

import base64
import hashlib
import os

import paramiko

SSH_HOST = "155.212.162.47"
EXPECTED_SHA256_B64 = "uQJp3lT4golKb3lsDTCFozGWR1ihlu1w/Oa4cSzCdpU"

class Pinned(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        actual = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        if actual != EXPECTED_SHA256_B64:
            raise RuntimeError("SSH fingerprint mismatch")
        client.get_host_keys().add(hostname, key.get_name(), key)

INSPECT = r'''
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from app.db.session import SessionLocal
from app.dispatcher.models import DispatcherMessage
from app.models.domain import Artifact, Task, CostRecord
from app.core.config import get_settings

settings = get_settings()
print("model_provider:", settings.llm_provider)
print("configured_model:", settings.llm_model)
now = datetime.now(timezone.utc)
db = SessionLocal()
try:
    msgs = list(db.scalars(select(DispatcherMessage)
                           .where(DispatcherMessage.created_at >= now-timedelta(hours=8))
                           .order_by(DispatcherMessage.created_at.desc()).limit(70)))
    print("recent_dispatcher_messages:", len(msgs))
    by_conv = {}
    for m in msgs:
        by_conv.setdefault(m.conversation_id, []).append(m)
    for convo, items in by_conv.items():
        items.sort(key=lambda m: (m.seq, str(m.id)))
        for i,m in enumerate(items):
            if m.role != "user":
                continue
            txt = (m.text or "").lower()
            kind = "business_advice" if ("бизнес" in txt and ("рекоменд" in txt or "сделат" in txt or "посовет" in txt)) else ("video_listing" if "видео" in txt and any(x in txt for x in ("последн","готов","покаж","сделал","запуст","найд")) else "other")
            next_message = next((n for n in items[i+1:] if n.seq == m.seq+1 and n.role=="assistant"), None)
            dt = (next_message.created_at - m.created_at).total_seconds() if next_message else None
            if kind != "other" or (dt is not None and dt > 40):
                print("turn:", "category="+kind,
                      "submitted_at="+m.created_at.isoformat(timespec="seconds"),
                      "reply_seconds="+str(round(dt,1) if dt is not None else None),
                      "reply="+("yes" if next_message else "no"),
                      "intent="+str(next_message.intent if next_message else None),
                      "capability="+str(next_message.capability if next_message else None),
                      "model="+str(next_message.model if next_message else None),
                      "product_tag="+hashlib.sha256(str(m.product_id).encode()).hexdigest()[:8])
    videos = list(db.scalars(select(Task).where(Task.assigned_agent == "whiteboard_renderer")
                .order_by(Task.created_at.desc()).limit(12)))
    print("recent_whiteboard_tasks:", [(str(t.status.value if hasattr(t.status,"value") else t.status), t.created_at.isoformat(timespec="seconds")) for t in videos])
    assets = list(db.scalars(select(Artifact).where(Artifact.kind.in_(["video_render","whiteboard_video","video_script"]))
                .order_by(Artifact.created_at.desc()).limit(15)))
    print("recent_video_artifact_statuses:", [(a.kind, str(a.status.value if hasattr(a.status,"value") else a.status), a.created_at.isoformat(timespec="seconds")) for a in assets])
    costs = list(db.scalars(select(CostRecord).where(CostRecord.category == "llm",
                CostRecord.incurred_at >= now-timedelta(hours=8))
                .order_by(CostRecord.incurred_at.desc()).limit(200)))
    dispatch_costs = [c for c in costs if (c.details or {}).get("task_type")=="dispatcher"]
    print("recent_dispatcher_cost_events:",len(dispatch_costs))
    from sqlalchemy import func
    from app.models.domain import TaskStatus
    busy = db.scalar(select(func.count()).select_from(Task).where(Task.status == TaskStatus.RUNNING))
    print("running_tasks_blocking_safe_deploy:", busy)
except Exception as exc:
    print("diagnostic_error_type:",type(exc).__name__)
    raise
finally:
    db.close()
'''
import hashlib
def main():
    secret = os.environ.get("SERVER_SSH_PASSWORD")
    if not secret:
        raise RuntimeError("Missing repository SSH secret")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(Pinned())
    client.connect(SSH_HOST,username="root",password=secret,port=22,allow_agent=False,look_for_keys=False,
                   timeout=12,auth_timeout=15,banner_timeout=25)
    try:
        cmd = "docker exec -i ai-growth-os-api-1 python -"
        stdin, stdout, stderr = client.exec_command(cmd,timeout=75)
        stdin.write("import hashlib\n"+INSPECT)
        stdin.channel.shutdown_write()
        out = stdout.read().decode("utf-8","replace")
        status = stdout.channel.recv_exit_status()
        print("=== Growth DB read-only ===\n"+out[:20000])
        print("db_diagnostic_exit_code",status)
        if status:
            print("db_error_type:",stderr.read().decode("utf-8","replace").splitlines()[-1][:160])
        for name,command in {
            "growth_containers": "docker ps --format '{{.Names}}|{{.Status}}' | grep 'ai-growth-os' | head -n 10",
            "growth_recent_http_status_counts": "docker logs --since 90m ai-growth-os-api-1 2>&1 | grep 'POST /studio/products/.*/dispatch/messages' | sed -E 's/^.*HTTP\/1[.]1\" ([0-9]{3}).*$/\\1/' | sort | uniq -c | head -n 12",
            "growth_error_categories": "docker logs --since 90m ai-growth-os-api-1 2>&1 | grep -Ei 'dispatcher_decision_failed|dispatcher_provider_failed|TimeoutError|ReadTimeout|ClientError|ValidationError|upstream timed out' | sed -E 's/^.*(dispatcher_decision_failed|dispatcher_provider_failed|TimeoutError|ReadTimeout|ClientError|ValidationError|upstream timed out).*$/\\1/' | sort | uniq -c | head -n 15",
        }.items():
            _,sout,_=client.exec_command(command,timeout=15)
            val=sout.read().decode("utf-8","replace").strip()[:1500]
            print("=== "+name+" ===\n"+val+"\n",flush=True)
    finally:
        client.close()
if __name__ == "__main__": main()
