#!/usr/bin/env bash
# Run ON THE EXISTING SERVER after comparing its current release with this source.
# No credential replacement, unrelated service changes, database restore or auto-post enablement.
set -Eeuo pipefail
ROOT=/opt/ai_growth_os
RELEASE_SHA=${RELEASE_SHA:?Set the exact tested commit SHA}
EXPECTED_CURRENT_RELEASE=${EXPECTED_CURRENT_RELEASE:?Set the freshly inspected active release name}
export RELEASE_SHA STALE_TASK_IDS
[[ "$RELEASE_SHA" =~ ^[a-f0-9]{40}$ ]] || exit 2
RELEASE="$ROOT/releases/$RELEASE_SHA"
[[ -d "$RELEASE" && -f "$RELEASE/docker-compose.prod.yml" && -f "$ROOT/.env" ]] || exit 3
exec 9>"$ROOT/.content-studio-deploy.lock"
flock -n 9 || { echo 'Another deployment is running'; exit 4; }
PREVIOUS=$(readlink -f "$ROOT/current")
[[ "$(basename "$PREVIOUS")" == "$EXPECTED_CURRENT_RELEASE" ]] || { echo 'Active release changed; inspect again'; exit 5; }
[[ "$PREVIOUS" != "$RELEASE" ]] || { echo 'This release is already active'; exit 6; }
dc_old(){ docker compose --env-file "$ROOT/.env" -p ai-growth-os -f "$PREVIOUS/docker-compose.prod.yml" "$@"; }
dc(){ docker compose --env-file "$ROOT/.env" -p ai-growth-os -f "$RELEASE/docker-compose.prod.yml" "$@"; }

# Fail before stopping anything if an active job would be interrupted.
dc_old exec -T -e STALE_TASK_IDS api python - < "$RELEASE/scripts/content_studio_deploy_tasks.py"

BACKUP="$ROOT/backups/content-studio-$(date -u +%Y%m%dT%H%M%SZ)-$RELEASE_SHA"
umask 077
mkdir -p "$BACKUP"
cp -p "$ROOT/.env" "$BACKUP/runtime.env"
printf '%s\n' "$PREVIOUS" > "$BACKUP/previous-release"
dc_old exec -T postgres pg_dump -U ai_growth -d ai_growth -Fc > "$BACKUP/database.dump"
[[ -s "$BACKUP/database.dump" ]] || exit 7
PROFILE_VOLUME=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/app/company_products"}}{{.Name}}{{end}}{{end}}' "$(dc_old ps -q api)")
[[ -n "$PROFILE_VOLUME" ]] || exit 8
docker run --rm -v "$PROFILE_VOLUME:/data:ro" postgres:16-alpine tar -C /data -czf - . > "$BACKUP/company-products.tar.gz"
printf '%s
' "${STALE_TASK_IDS:-}" > "$BACKUP/recovered-task-ids"

# Keep the exact images currently running so rebuilding cannot spoil rollback.
printf 'services:\n' > "$BACKUP/rollback-images.yml"
for SERVICE in api worker telegram; do
  CONTAINER=$(dc_old --profile telegram ps -q "$SERVICE")
  if [[ -n "$CONTAINER" ]]; then
    IMAGE=$(docker inspect --format '{{.Image}}' "$CONTAINER")
    TAG="ai-growth-rollback:$SERVICE-$(date -u +%Y%m%dT%H%M%S)"
    docker tag "$IMAGE" "$TAG"
    printf '  %s:\n    image: %s\n    build: null\n    pull_policy: never\n' "$SERVICE" "$TAG" >> "$BACKUP/rollback-images.yml"
  fi
done

dc --profile telegram build profile-init api worker telegram
dc run -T --rm --no-deps api python - <<'PY'
from urllib.parse import urlsplit
from app.core.config import get_settings
s = get_settings()
assert s.app_env == 'production'
assert (s.studio_admin_username and s.studio_admin_password) or (s.studio_htpasswd_file and s.studio_htpasswd_file.is_file()), 'Existing operator login must be configured'
assert s.studio_public_origin and urlsplit(s.studio_public_origin).scheme == 'https'
assert s.llm_provider == 'xai' and urlsplit(s.xai_base_url).hostname == 'api.claudehub.fun'
print('Production auth, ClaudeHub and runtime configuration OK')
PY

RESTART_NEEDED=0
rollback(){
  trap - ERR
  if [[ "$RESTART_NEEDED" == 1 ]]; then
    echo 'Release verification failed; restoring previous images'
    docker compose --env-file "$BACKUP/runtime.env" -p ai-growth-os -f "$PREVIOUS/docker-compose.prod.yml" -f "$BACKUP/rollback-images.yml" --profile telegram up -d --no-build api worker
    if grep -q '^TELEGRAM_BOT_TOKEN=.' "$BACKUP/runtime.env"; then
      docker compose --env-file "$BACKUP/runtime.env" -p ai-growth-os -f "$PREVIOUS/docker-compose.prod.yml" -f "$BACKUP/rollback-images.yml" --profile telegram up -d --no-build telegram
    fi
    echo "Rollback images restored. Backup: $BACKUP"
  fi
}
trap rollback ERR

# Check again after build; the old worker must not be stopped mid-generation.
dc_old exec -T -e STALE_TASK_IDS api python - < "$RELEASE/scripts/content_studio_deploy_tasks.py"
RESTART_NEEDED=1
dc_old --profile telegram stop worker telegram
dc_old exec -T -e STALE_TASK_IDS api python - --recover < "$RELEASE/scripts/content_studio_deploy_tasks.py"
dc run -T --rm --no-deps api python -m alembic upgrade head
dc up --no-deps --abort-on-container-exit --exit-code-from profile-init profile-init
dc --profile telegram up -d --no-build api worker
if grep -q '^TELEGRAM_BOT_TOKEN=.' "$ROOT/.env" && grep -q '^TELEGRAM_OWNER_CHAT_ID=.' "$ROOT/.env"; then
  dc --profile telegram up -d --no-build telegram
fi
for ATTEMPT in $(seq 1 30); do
  if curl -fsS --max-time 3 http://127.0.0.1:8091/health >/dev/null; then break; fi
  sleep 2
done
dc exec -T api python - <<'PY'
from fastapi.testclient import TestClient
from app.main import app
from app.core.config import get_settings
s = get_settings()
with TestClient(app) as client:
    assert client.get('/health').status_code == 200
    assert client.get('/health/version').json()['release'] == s.release_sha
    assert client.get('/products').status_code == 401
    from fastapi import Request
    from app.api.content_studio_auth import require_studio_auth
    def verified_operator(request: Request):
        request.state.studio_product_ids = None
        return 'deployment-render-check'
    app.dependency_overrides[require_studio_auth] = verified_operator
    products = client.get('/products')
    assert products.status_code == 200
    for product in products.json():
        base = '/studio/products/' + product['id']
        for suffix in ('/create', '/review', '/calendar', '/channels', '/materials'):
            assert client.get(base + suffix).status_code == 200, suffix
    app.dependency_overrides.pop(require_studio_auth, None)
    assert client.get('/products').status_code == 401
    print('Studio rendering and API protection OK')
PY
curl -fsS --max-time 5 http://127.0.0.1:8091/health/version
ln -sfn "$RELEASE" "$ROOT/current"
RESTART_NEEDED=0
trap - ERR
echo "Active release: $RELEASE_SHA; backup: $BACKUP"
