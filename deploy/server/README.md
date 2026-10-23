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
  dependencies and Alembic migrations. A commit whose dependencies the image does not satisfy, or
  that adds/removes a migration, is refused: build a new base image, apply the migration, update
  `base_image`.
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

Until the key is set up, the workflow falls back to the existing `SERVER_SSH_PASSWORD` secret
(host `vars.DEPLOY_HOST`, default `155.212.162.47`). It then trusts only the pinned server host key
and refuses to deploy if the running container has files that are not in the commit's history.
The key remains the preferred setup: it can only deploy or roll back.

## By hand

```sh
git archive --format=tar.gz <sha> app alembic/versions pyproject.toml products \
  | ssh root@<host> python3 /opt/ai_growth_os/deploy/deploy_growth.py deploy <sha>
ssh root@<host> python3 /opt/ai_growth_os/deploy/deploy_growth.py rollback
```

The deploy refuses to start while agent tasks are running, so they are not cut off.

## Changing the deploy script itself

The workflow runs the script that is *installed* on the server, not the one in the commit. After a change to
`deploy_growth.py` run the workflow once with `prepare=true`: it checks the database revision, builds the base
image if migrations were added, applies them, and installs the commit's `deploy_growth.py` (old copy kept as
`.bak.<time>`). Later deploys use the new script. (Found 23.10.2026: the ServGo sign-in secrets reached `.env`
but not the containers until the script with the `${SERVGO_...}` references was installed.)
