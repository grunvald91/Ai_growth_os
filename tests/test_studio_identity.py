"""Cookie binding that keeps the central ServGo account and Studio in sync."""
import os
import unittest
from unittest.mock import patch

from starlette.responses import Response
from web.api import studio_identity as identity


class StudioIdentityCookieTests(unittest.TestCase):
    def setUp(self):
        self.secret = patch.dict(os.environ, {"STUDIO_SSO_SECRET": "s" * 40})
        self.secret.start()

    def tearDown(self):
        self.secret.stop()

    def test_signed_account_switch_and_expiry(self):
        old = identity.issue(7, now=1000)
        self.assertEqual(identity.read(old, now=1001), 7)
        self.assertIsNone(identity.read(old, now=1000 + identity.MAX_AGE + 1))
        self.assertIsNone(identity.read(old + "changed", now=1001))
        other = identity.issue(9, now=1000)
        self.assertEqual(identity.read(other, now=1001), 9)
        self.assertNotEqual(old, other)

    def test_cookie_rotates_on_login_but_not_on_background_renewal(self):
        response = Response()
        identity.set_identity(response, 7)
        header = response.headers.get("set-cookie", "")
        self.assertIn("sg_identity=", header)
        self.assertIn("httponly", header.lower())
        self.assertIn("secure", header.lower())
        self.assertIn("samesite=lax", header.lower())
        self.assertNotIn("domain=", header.lower())
        token = header.split("sg_identity=", 1)[1].split(";", 1)[0]
        same = Response()
        identity.ensure_identity(same, 7, token)
        self.assertNotIn("set-cookie", same.headers)
        changed = Response()
        identity.ensure_identity(changed, 9, token)
        self.assertIn("sg_identity=", changed.headers["set-cookie"])
        logged_out = Response()
        identity.clear_identity(logged_out)
        self.assertIn("max-age=0", logged_out.headers["set-cookie"].lower())
