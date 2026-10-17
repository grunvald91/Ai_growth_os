#!/bin/sh
# Forced command for the GitHub Actions deploy key (authorized_keys "command=").
# The key can only deploy one commit or roll back; it never gets a shell.
set -eu
set -f
# shellcheck disable=SC2086
set -- ${SSH_ORIGINAL_COMMAND:-}
case "${1:-}" in
  deploy)
    [ "$#" -eq 2 ] && printf '%s' "$2" | grep -Eq '^[0-9a-f]{40}$' || { echo "usage: deploy <40-hex sha>"; exit 2; }
    exec /usr/bin/python3 /opt/ai_growth_os/deploy/deploy_growth.py deploy "$2" ;;
  rollback)
    [ "$#" -eq 1 ] || { echo "usage: rollback"; exit 2; }
    exec /usr/bin/python3 /opt/ai_growth_os/deploy/deploy_growth.py rollback ;;
  *)
    echo "allowed: deploy <sha> | rollback"; exit 2 ;;
esac
