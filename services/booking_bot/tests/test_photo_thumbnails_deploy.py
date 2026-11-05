"""The scoped rollout replaces only the reviewed old photo handler block."""
import hashlib
from pathlib import Path

import pytest

from scripts import deploy_photo_thumbnails as deploy

REPOSITORY = Path("web/api/routes/client.py").read_text(encoding="utf-8")
OLD_BLOCK = Path("tests/fixtures/old_photo_handler_block.txt").read_text(encoding="utf-8")
PREFIX = "from typing import Optional\nfrom fastapi import Query\nrouter = None\n\n"
SUFFIX = "async def other():\n    return 1\n"


def production(block=OLD_BLOCK):
    return PREFIX + block + deploy.END + " ----------\n" + SUFFIX


def test_old_block_hash_matches_reviewed_production_version():
    assert hashlib.sha256(OLD_BLOCK.encode()).hexdigest() == deploy.OLD_BLOCK_SHA


def test_new_source_replaces_only_the_photo_block():
    result = deploy.new_client_source(production(), REPOSITORY)
    ra, rb = deploy.block_of(REPOSITORY)
    assert result.startswith(PREFIX) and result.endswith(deploy.END + " ----------\n" + SUFFIX)
    assert REPOSITORY[ra:rb] in result and 'size == "thumb"' in result and OLD_BLOCK not in result


def test_modified_production_block_is_refused():
    with pytest.raises(RuntimeError):
        deploy.new_client_source(production(OLD_BLOCK.replace("Photo not found", "Photo gone", 1)), REPOSITORY)
