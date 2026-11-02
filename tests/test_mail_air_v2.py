"""ServGo Air V2 mail UI structure: no external operations or credentials.

Layout must preserve existing HTML ids/hooks so the owner mail API and
campaign actions keep working on both legacy and same-origin routes.
"""
from html.parser import HTMLParser
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / "web" / "api" / "routes"


class MailPageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.links = []
        self.duplicate_ids = set()
        self.body_classes = ""

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag == "body":
            self.body_classes = fields.get("class", "")
        if "id" in fields:
            if fields["id"] in self.ids:
                self.duplicate_ids.add(fields["id"])
            self.ids.add(fields["id"])
        if tag == "link":
            self.links.append(fields.get("href", ""))


class MailAirV2ContractTests(unittest.TestCase):
    def pages(self):
        records = []
        for name, body_class in (
            ("owner_mail_ui.html", "servgo-mail-inbox"),
            ("mail_campaign_dashboard.html", "servgo-mail-campaigns"),
        ):
            with self.subTest(page=name):
                text = (ROOT / name).read_text(encoding="utf-8")
                page = MailPageParser()
                page.feed(text)
                self.assertIn("servgo-mail-v2", page.body_classes)
                self.assertIn(body_class, page.body_classes)
                self.assertFalse(page.duplicate_ids)
                self.assertIn("/api/servgo-air-tokens.css?v=1", page.links)
                self.assertIn("/api/servgo-mail-air.css?v=3", page.links)
                self.assertIn('href="#mail-main-content"', text)
                self.assertIn('id="mail-main-content"', text)
                self.assertIn('aria-label="Разделы почты"', text)
                self.assertIn("servgo-mail-theme", page.ids)
                self.assertIn('class="mail-workspace-links"', text)
                self.assertIn('href="https://servgo.ru/workspace/growth"', text)
                self.assertIn('href="/api/logout"', text)
                records.append((name, page, text))
        return records

    def test_pages_share_accessible_servgo_air_shell(self):
        records = self.pages()
        self.assertEqual(len(records), 2)

    def test_mailbox_existing_api_and_compose_hooks_survive(self):
        _, parsed, source = self.pages()[0]
        for key in ("compose-dialog", "compose-form", "compose-to",
                    "compose-subject", "compose-body", "compose-submit",
                    "fab-compose", "list", "detail", "refresh"):
            self.assertIn(key, parsed.ids)
        self.assertIn('fetch("/api"+path', source)
        self.assertIn('confirm("Отправить письмо на "', source)
        self.assertIn('$("compose").onclick = () => openCompose(state.replyFull ?', source)
        for key in ('data-tab="inbox"', 'data-tab="outbox"',
                    'data-tab="drafts"', 'data-tab="prospects"'):
            self.assertIn(key, source)

    def test_campaign_actions_remain_behind_existing_approval(self):
        _, parsed, source = self.pages()[1]
        for key in ("auto-switch", "campaign-list", "campaign-detail",
                    "new-dialog", "search-dialog", "edit-dialog"):
            self.assertIn(key, parsed.ids)
        self.assertIn("Включить автоматическую рассылку?", source)
        self.assertIn('fetch("/api/campaigns"+path', source)

    def test_layout_is_scoped_and_supports_accessible_mobile_nav(self):
        css = (ROOT / "servgo-mail-air.css").read_text(encoding="utf-8")
        self.assertIn("body.servgo-mail-v2", css)
        self.assertIn("@media (max-width: 850px)", css)
        self.assertIn("@media (max-width: 760px)", css)
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn("safe-area-inset-bottom", css)
        self.assertIn("focus-visible", css)
        # Switch is never included in the full-width menu-button selector.
        self.assertIn(".mail-v3-popover-content :is(button:not(.switch),a,.btn)", css)
        self.assertIn(".mail-v3-popover-content #auto-switch", css)
        self.assertIn("grid-template-columns:minmax(0,1fr) 52px", css)
        self.assertIn("grid-template-columns:repeat(3,minmax(0,1fr))", css)


    def test_v3_bottom_navigation_is_consistent_and_contacts_are_nested(self):
        inbox, campaigns = self.pages()
        for _name, _parsed, source in (inbox, campaigns):
            self.assertIn('class="mail-v3-dock"', source)
            self.assertIn('href="/api/campaigns-dashboard#search"', source)
            self.assertIn('href="/api/campaigns-dashboard#campaigns"', source)
            self.assertIn('href="/api/mailbox-dashboard#inbox"', source)
            self.assertIn('class="mail-v3-nav" aria-label="Разделы почты"', source)
        self.assertIn('aria-label="Папки почты"', inbox[2])
        self.assertNotIn('>Контакты</a>', inbox[2])
        self.assertIn('id="search-campaign"', campaigns[2])
        self.assertIn('id="search-dialog" class="mail-v3-search" hidden', campaigns[2])
        self.assertEqual(campaigns[2].count('id="search-suggest"'), 1)
        self.assertIn('id="campaign-action-dock"', campaigns[2])
        self.assertNotIn('el("search-dialog").showModal()', campaigns[2])

    def test_v4_search_and_send_safety_controls(self):
        _inbox, campaigns = self.pages()
        _, parsed, source = campaigns
        for key in ("search-provider", "search-history", "search-create-campaign",
                    "search-resume", "search-campaign", "campaign-automation-summary"):
            self.assertIn(key, parsed.ids)
        self.assertIn('firm_ids:[...found.picks]', source)
        self.assertIn('legal_basis_confirmed:true', source)
        self.assertIn('function loadHistory()', source)
        self.assertIn('function loadSearchProviders()', source)
        self.assertIn('search-review-yes', source)
        self.assertIn('search-review-no', source)

    def test_v3_controls_live_at_the_bottom(self):
        css = (ROOT / "servgo-mail-air.css").read_text(encoding="utf-8")
        self.assertIn(".mail-v3-dock {position:fixed;bottom:0", css)
        self.assertIn(".mail-v3-search-actions {position:fixed", css)
        self.assertIn(".mail-v3-actionbar #compose {display:inline-flex!important", css)
        self.assertIn("safe-area-inset-bottom", css)

if __name__ == "__main__":
    unittest.main()
