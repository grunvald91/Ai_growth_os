"""Deployment regression: SQL comments must not become executable statements."""
from pathlib import Path

from scripts.deploy_crm import migration_statements


def test_crm_migration_ignores_comment_semicolons():
    sql = (Path(__file__).resolve().parents[1] / 'migrations/add_crm_contacts.sql').read_text()
    statements = migration_statements(sql)
    assert len(statements) == 4
    assert statements[0].startswith('CREATE TABLE IF NOT EXISTS crm_contacts')
    assert all(s.startswith('CREATE INDEX IF NOT EXISTS') for s in statements[1:])
    assert "DEFAULT ''" in statements[0]
    assert 'uq_crm_business_user UNIQUE (business_id, user_id)' in statements[0]


def test_multiple_semicolons_in_comments_are_not_sql():
    sql = '-- explanation; more; text\nCREATE TABLE example (id INTEGER);\n -- trailing; comment'
    assert migration_statements(sql) == ['CREATE TABLE example (id INTEGER)']
