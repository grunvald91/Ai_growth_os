"""Production admin hotfix must not overwrite unrelated server-side changes."""
import ast


def test_admin_photo_patch_preserves_unrelated_production_code():
    from scripts.deploy_web_photo_fix import patch_admin_source
    original = '''import logging
from ..database import (Business,)

async def unrelated_rental():
    return "production-only logic"

async def upload_business_photo():
    bot_token = "fake-token-for-test"
    async with httpx.AsyncClient() as http:
        await http.post("sendPhoto")
    async with httpx.AsyncClient() as http:
        await http.post("deleteMessage")

async def upload_employee_photo():
    bot_token = "fake-token-for-test"
    async with httpx.AsyncClient() as http:
        await http.post("sendPhoto")
    async with httpx.AsyncClient() as http:
        await http.post("deleteMessage")
'''
    updated = patch_admin_source(original)
    ast.parse(updated)
    assert 'async def unrelated_rental():\n    return "production-only logic"' in updated
    assert updated.count('async with create_photo_client() as http:') == 4
    assert updated.count('bot_token = os.getenv("BOT_TOKEN")') == 2
    assert "fake-token-for-test" not in updated
    assert updated.count('from ..photo_transport import create_photo_client') == 1
    assert updated.count('import os\n') == 1
