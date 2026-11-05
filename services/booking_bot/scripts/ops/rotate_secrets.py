"""Ротация секретов ServGo на сервере. Запускается от root venv-питоном из /opt/booking_bot.

    rotate_secrets.py db                 — новый случайный пароль роли БД из DATABASE_URL API
    rotate_secrets.py token <file>       — новый токен бота из файла (файл удаляется)
    rotate_secrets.py rollback <dir>     — вернуть файлы и пароль из каталога бэкапа

Значения секретов никогда не печатаются: только пути файлов и статусы.
Если старый секрет найден вне известных мест — ничего не меняет и выходит с ошибкой.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

ROOT = Path('/opt/booking_bot')
HANDLED = [Path('/etc/systemd/system'), ROOT]
SCAN = [Path('/etc'), Path('/opt'), Path('/var/spool/cron')]
SKIP_PARTS = {'backups', '.git', 'node_modules', 'venv', '__pycache__', 'secret-rotation'}
SKIP_SUFFIXES = {'.log', '.gz', '.tgz', '.zip', '.png', '.jpg', '.sqlite', '.db'}
SERVICES = ['booking-web-api', 'booking-bot']


def sh(cmd: str, check=True) -> str:
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and res.returncode != 0:
        raise RuntimeError(f'command failed: {cmd.split()[0]}')
    return res.stdout


def api_database_url() -> str:
    env = sh('systemctl show -p Environment --value booking-web-api')
    for item in env.split():
        if item.startswith('DATABASE_URL='):
            return item.split('=', 1)[1]
    raise RuntimeError('DATABASE_URL of booking-web-api not found')


def files_containing(needles: list[str], roots: list[Path]) -> list[Path]:
    found = []
    for root in roots:
        paths = [root] if root.is_file() else (p for p in root.rglob('*') if p.is_file())
        for p in paths:
            if SKIP_PARTS & set(p.parts) or p.suffix in SKIP_SUFFIXES or p.name.startswith('dist'):
                continue
            try:
                if p.stat().st_size > 2_000_000:
                    continue
                data = p.read_text(errors='ignore')
            except (OSError, ValueError):
                continue
            if any(n and n in data for n in needles):
                found.append(p)
    return sorted(set(found))


def is_handled(p: Path) -> bool:
    return any(p == h or h in p.parents for h in HANDLED)


def plan(needles: list[str]) -> list[Path]:
    everywhere = files_containing(needles, SCAN)
    foreign = [p for p in everywhere if not is_handled(p)]
    if foreign:
        print('Secret also found outside known locations — refusing to rotate:', *foreign, sep='\n  ')
        sys.exit(3)
    targets = [p for p in everywhere if is_handled(p)]
    print('Files to update:', *targets, sep='\n  ')
    return targets


def backup(targets: list[Path], meta: dict) -> Path:
    d = Path('/root/secret-rotation') / time.strftime('%Y%m%d-%H%M%S')
    d.mkdir(parents=True, mode=0o700)
    os.chmod(d.parent, 0o700)
    for i, p in enumerate(targets):
        shutil.copy2(p, d / f'{i}.bak')
    (d / 'meta.json').write_text(json.dumps({**meta, 'files': [str(p) for p in targets]}))
    os.chmod(d / 'meta.json', 0o600)
    print('Backup (root-only):', d)
    return d


def replace_in(targets: list[Path], pairs: list[tuple[str, str]]):
    for p in targets:
        text = p.read_text()
        for old, new in pairs:
            text = text.replace(old, new)
        p.write_text(text)


def restart(units: list[str]):
    sh('systemctl daemon-reload')
    sh('systemctl restart ' + ' '.join(units))


def unit_names(targets: list[Path]) -> list[str]:
    names = set(SERVICES)
    # Все активные сервисы, работающие из /opt/booking_bot (читают тот же .env).
    for unit in Path('/etc/systemd/system').glob('*.service'):
        try:
            if str(ROOT) in unit.read_text() and subprocess.run(
                    f'systemctl is-active --quiet {unit.stem}', shell=True).returncode == 0:
                names.add(unit.stem)
        except OSError:
            pass
    for p in targets:
        if p.suffix == '.service':
            names.add(p.stem)
        elif p.parent.name.endswith('.service.d'):
            names.add(p.parent.name[:-len('.service.d')])
    return sorted(names)


def healthy() -> bool:
    for _ in range(30):
        api = subprocess.run('curl -fsS -o /dev/null --max-time 4 http://127.0.0.1:8000/businesses', shell=True).returncode == 0
        bot = subprocess.run('systemctl is-active --quiet booking-bot', shell=True).returncode == 0
        if api and bot:
            time.sleep(8)  # бот не должен упасть сразу после старта
            if subprocess.run('systemctl is-active --quiet booking-bot', shell=True).returncode == 0:
                return True
        time.sleep(2)
    return False


async def set_role_password(dsn: str, role: str, new_password: str):
    import asyncpg
    conn = await asyncpg.connect(dsn)
    try:
        # Роль может сменить собственный пароль; литерал экранируется сервером.
        literal = await conn.fetchval('SELECT quote_literal($1::text)', new_password)
        ident = await conn.fetchval('SELECT quote_ident($1::text)', role)
        await conn.execute(f'ALTER ROLE {ident} PASSWORD {literal}')
    finally:
        await conn.close()


def rotate_db():
    url = api_database_url()
    parts = urlsplit(url)
    role, old_enc = parts.username, parts.password
    if not role or not old_enc:
        raise RuntimeError('DATABASE_URL has no user/password')
    old_raw = unquote(old_enc)
    if len(old_raw) < 8:
        raise RuntimeError('current DB password too short to replace safely; rotate manually')
    new = secrets.token_hex(24)  # без спецсимволов: одинаково в URL и .env
    targets = plan([old_raw, old_enc])
    d = backup(targets, {'kind': 'db', 'role': role})
    plain_dsn = url.replace('+asyncpg', '')
    asyncio.run(set_role_password(plain_dsn, role, new))
    try:
        replace_in(targets, [(old_enc, new), (quote(old_raw, safe=''), new), (old_raw, new)])
        restart(unit_names(targets))
        if not healthy():
            raise RuntimeError('services unhealthy after DB password rotation')
    except Exception:
        print('Rolling back DB password rotation')
        new_dsn = plain_dsn.replace(f':{old_enc}@', f':{new}@')
        asyncio.run(set_role_password(new_dsn, role, old_raw))
        restore(d)
        raise
    print('DB password rotated: ok')


def rotate_token(token_file: str):
    new = Path(token_file).read_text().strip()
    Path(token_file).unlink()
    if not re.fullmatch(r'\d{5,}:[A-Za-z0-9_-]{30,}', new):
        raise RuntimeError('new token has invalid format')
    env = (ROOT / '.env').read_text()
    m = re.search(r'^BOT_TOKEN=["\']?([^"\'\s]+)', env, re.M)
    if not m:
        raise RuntimeError('BOT_TOKEN not found in .env')
    old = m.group(1)
    if old == new:
        print('Token unchanged'); return
    targets = plan([old])
    d = backup(targets, {'kind': 'token'})
    try:
        replace_in(targets, [(old, new)])
        restart(unit_names(targets))
        if not healthy():
            raise RuntimeError('services unhealthy after token rotation')
    except Exception:
        print('Rolling back token rotation')
        restore(d)
        raise
    print('Bot token rotated: ok')


def restore(d: Path):
    meta = json.loads((d / 'meta.json').read_text())
    for i, f in enumerate(meta['files']):
        shutil.copy2(d / f'{i}.bak', f)
    restart(unit_names([Path(f) for f in meta['files']]))
    print('Rollback', 'ok' if healthy() else 'FAILED — needs manual attention')


if __name__ == '__main__':
    mode = sys.argv[1]
    if mode == 'db':
        rotate_db()
    elif mode == 'token':
        rotate_token(sys.argv[2])
    elif mode == 'rollback':
        restore(Path(sys.argv[2]))
    else:
        raise SystemExit('usage: rotate_secrets.py db|token <file>|rollback <dir>')
