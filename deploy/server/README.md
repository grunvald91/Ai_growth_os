# Production deploy of AI Growth OS

`.github/workflows/deploy-growth.yml` (manual, `workflow_dispatch`) runs the tests, then streams
the tested commit (`git archive` of `app/`, `alembic/versions/`, `pyproject.toml`, `products/`)
over a restricted SSH key. The server builds an image from the configured base image and
recreates `api`, `worker` and `telegram`. A failed health check restores the previous set.

The repository is private, so the server never downloads sources and holds no GitHub token.

## One-time server setup (root)

```sh
install -d -m 700 /opt/ai_growth_os/deploy
install -m 700 deploy_growth.py ssh-deploy-entry.sh /opt/ai_growth_os/deploy/
cp deploy.example.json /opt/ai_growth_os/deploy/deploy.json   # then edit it
```

- `base_image`: an image built from this repository's `Dockerfile` that matches production's
  dependencies and Alembic migrations. A commit that changes `pyproject.toml` or adds/removes a
  migration is refused: build a new base image, apply the migration, update `base_image`.
- `overlay_env`: non-secret flags only. Secrets stay in `/opt/ai_growth_os/.env`.

Generate a dedicated key pair and add the public key to `/root/.ssh/authorized_keys` as a
forced command, so it can only deploy or roll back:

```
command="/opt/ai_growth_os/deploy/ssh-deploy-entry.sh",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty ssh-ed25519 AAAA... github-deploy
```

## GitHub settings

- Secret `DEPLOY_SSH_KEY`: the private key above.
- Secret `DEPLOY_KNOWN_HOSTS`: output of `ssh-keyscan <host>`, checked by hand.
- Variable `DEPLOY_HOST`: the server address.
- Environment `production` with the owner as required reviewer.

## By hand

```sh
git archive --format=tar.gz <sha> app alembic/versions pyproject.toml products \
  | ssh root@<host> python3 /opt/ai_growth_os/deploy/deploy_growth.py deploy <sha>
ssh root@<host> python3 /opt/ai_growth_os/deploy/deploy_growth.py rollback
```

The deploy refuses to start while agent tasks are running, so they are not cut off.
