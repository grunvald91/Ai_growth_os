"""AI Growth OS release 20261016 (content assembly fix). Never prints secrets.
Usage on server:
  python3 deploy_growth_20261016.py build                 # image only, nothing restarted
  python3 deploy_growth_20261016.py up <critic> <router>   # true/false each; recreates api/worker/telegram
  python3 deploy_growth_20261016.py rollback               # previous compose set (image 20261015 set)
"""
from pathlib import Path
import sys, json, subprocess, time, urllib.request, urllib.error

REL = Path('/opt/servgo-releases/20261016/growth'); SRC = REL / 'app'
IMAGE = 'servgo-growth-release:20261016'; BASE_IMAGE = 'servgo-growth-release:20261015'
PROJECT = ['docker', 'compose', '--project-name', 'ai-growth-os', '--env-file', '/opt/ai_growth_os/.env']
SERVICES = ['api', 'worker', 'telegram']
ROLLBACK = REL / 'growth-rollback.json'
# Critic bounds: the largest content stage request is ~26k tokens (~90k chars).
CRITIC_LIMITS = {'AGENT_CRITIC_MAX_CONTEXT_CHARS': '200000',
                 'AGENT_CRITIC_REVIEW_TIMEOUT_SECONDS': '240',
                 'AGENT_CRITIC_REVISION_TIMEOUT_SECONDS': '300',
                 # Studio logins allowed to open /studio/tune.
                 'STUDIO_TUNE_USERNAMES': 'artur'}


def run(args, **kw): return subprocess.run(args, check=True, **kw)
def cap(args): return subprocess.check_output(args, text=True)


def status(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r: return r.status
    except urllib.error.HTTPError as e: return e.code
    except Exception: return 0


def wait(url, want=200, tries=60):
    for _ in range(tries):
        got = status(url)
        if got == want: return
        time.sleep(2)
    raise RuntimeError(f'health {url} -> {got}')


def labels():
    c = json.loads(cap(['docker', 'inspect', 'ai-growth-os-api-1']))[0]['Config']['Labels']
    return c['com.docker.compose.project.config_files'].split(','), c['com.docker.compose.project.working_dir']


def active_whiteboard_jobs():
    code = ('from sqlalchemy import select\nfrom app.db.session import SessionLocal\nfrom app.models.domain import Task\n'
            'with SessionLocal() as s:\n print(len(s.scalars(select(Task.id).where(Task.assigned_agent=="whiteboard_renderer",'
            ' Task.status.in_(["running","queued"]))).all()))')
    return int(cap(['docker', 'exec', 'ai-growth-os-api-1', 'python', '-c', code]).strip())


def build():
    name = 'servgo-release-20261016-build'
    subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    run(['docker', 'create', '--name', name, BASE_IMAGE, 'true'], stdout=subprocess.DEVNULL)
    run(['docker', 'cp', str(SRC) + '/.', name + ':/app/app'])
    run(['docker', 'commit', '--change', 'CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]', name, IMAGE], stdout=subprocess.DEVNULL)
    run(['docker', 'rm', name], stdout=subprocess.DEVNULL)
    # Offline import check of the new image (no network calls, no DB writes).
    out = cap(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'python', IMAGE, '-c',
               'from app.main import app; import app.team_worker as t, inspect; '
               # included routers are lazy (_IncludedRouter): count through OpenAPI
               'print(sum("whiteboard" in p for p in app.openapi()["paths"]), "run_whiteboard_once" in inspect.getsource(t))'])
    routes, worker = out.split()
    if int(routes) == 0 or worker != 'True': raise RuntimeError('image lacks whiteboard wiring')
    print('GROWTH_IMAGE_OK', json.dumps({'image': IMAGE, 'whiteboard_routes': int(routes)}))


def up(critic, router):
    assert critic in ('true', 'false') and router in ('true', 'false')
    files, cwd = labels()
    base = [f for f in files if not f.startswith('/opt/servgo-releases/')]
    if not ROLLBACK.exists():  # first switch: remember the exact running set
        ROLLBACK.write_text(json.dumps({'files': files, 'cwd': cwd}))
    n = active_whiteboard_jobs()
    if n: raise RuntimeError(f'{n} whiteboard job(s) running/queued: not restarting worker')
    overlay = REL / f'growth-overlay-critic{critic}-router{router}.json'
    overlay.write_text(json.dumps({'services': {s: {'image': IMAGE, 'environment': {
        'AGENT_CRITIC_ENABLED': critic, 'MODEL_ROUTER_ENABLED': router, 'RELEASE_SHA': 'release-20261016',
        **CRITIC_LIMITS}} for s in SERVICES}}, indent=2))
    cmd = PROJECT + sum((['-f', f] for f in base + [str(overlay)]), [])
    run(cmd + ['config', '--quiet'], cwd=cwd)
    try:
        run(cmd + ['up', '-d', '--no-deps', '--no-build'] + SERVICES, cwd=cwd)
        wait('http://127.0.0.1:8091/health'); wait('http://127.0.0.1:8091/studio/team')
        time.sleep(10)
        for c in ['ai-growth-os-api-1', 'ai-growth-os-worker-1', 'ai-growth-os-telegram-1']:
            st = json.loads(cap(['docker', 'inspect', c]))[0]['State']
            if not st['Running'] or st['Restarting']: raise RuntimeError(c + ' not running')
        logs = subprocess.run(['docker', 'logs', '--since', '60s', 'ai-growth-os-worker-1'], capture_output=True, text=True)
        if 'Traceback' in logs.stdout + logs.stderr: raise RuntimeError('worker traceback after start')
        run(['docker', 'exec', 'ai-growth-os-api-1', 'python', '-m', 'app.llm.router_preflight'],
            stdout=(REL / f'router-preflight-{critic}-{router}.json').open('w'))
    except Exception:
        print('GROWTH_FAILED_ROLLING_BACK', flush=True); rollback(); raise
    print('GROWTH_RELEASE_OK', json.dumps({'critic': critic, 'router': router, 'image': IMAGE}))


def rollback():
    d = json.loads(ROLLBACK.read_text())
    cmd = PROJECT + sum((['-f', f] for f in d['files']), [])
    run(cmd + ['up', '-d', '--no-deps', '--no-build'] + SERVICES, cwd=d['cwd'])
    wait('http://127.0.0.1:8091/health')
    print('GROWTH_ROLLBACK_OK')


if __name__ == '__main__':
    a = sys.argv[1:]
    {'build': lambda: build(), 'up': lambda: up(a[1], a[2]), 'rollback': lambda: rollback()}[a[0]]()
