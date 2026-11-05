"""Mail navigation must stay protected, on the unified PWA origin and backwards compatible."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADMIN = (ROOT / "web/frontend/src/components/AdminShell.tsx").read_text(encoding="utf-8")
APP = (ROOT / "web/frontend/src/App.tsx").read_text(encoding="utf-8")
ENTRY = (ROOT / "web/frontend/src/pages/client/OwnerMailPage.tsx").read_text(encoding="utf-8")
BACKEND = (ROOT / "web/api/routes/mail_pwa.py").read_text(encoding="utf-8")
SEARCH = (ROOT / "web/api/routes/mail_company_search.py").read_text(encoding="utf-8")


def test_navigation_exposes_one_protected_mail_workspace():
    assert "to: '/workspace/mail', label: 'Почта ServGo'" in ADMIN
    assert "mailAllowed" in ADMIN and 'owner-mail/permission' in ADMIN
    assert "mailAllowed || item.to !== '/workspace/mail'" in ADMIN
    assert '<Route path="/workspace/mail" element={<AdminRoute restorationPending={authRestoring}><OwnerMailPage /></AdminRoute>} />' in APP


def test_legacy_entry_and_existing_mail_origin_not_removed():
    assert '<Route path="/mail" element={<OwnerMailPage />} />' in APP
    assert "data.url.startsWith('https://mail.servgo.ru/claim?ticket=')" in ENTRY
    assert "data.url.startsWith('https://servgo.ru/api/owner-mail/claim?ticket=')" in ENTRY


def test_gateway_requires_actual_canonical_origin_and_owner_session():
    assert "canonical_mail_request(request)" in BACKEND
    assert "await mail_owner(request, session)" in BACKEND
    assert 'return await _workspace(request, session, "mail_campaign_dashboard.html")' in BACKEND
    assert 'return await _workspace(request, session, "owner_mail_ui.html", mailbox=True)' in BACKEND


def test_exa_and_jev_not_mandatory_for_2gis_only_path():
    assert 'provider: Literal["2gis", "exa"] = "2gis"' in SEARCH
    assert 'os.getenv("SERVGO_MAIL_USE_JEV", "false")' in SEARCH
    assert 'engine = run_exa_job if data.provider == "exa" else run_job' in SEARCH
