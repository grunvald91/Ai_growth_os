"""Read-only contract and nginx syntax checks for an *unapplied* Growth PWA gateway."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GATEWAY = ROOT / "deploy" / "nginx" / "servgo-growth-gateway.inc.example"


class GatewayConfigurationTests(unittest.TestCase):
    def test_gateway_is_narrow_and_uses_only_loopback_upstream(self):
        content = GATEWAY.read_text()
        locations = re.findall(r"^location\s+(.+?)\s*\{\s*$", content, re.M)
        self.assertEqual(len(locations), 9, locations)
        self.assertIn("= /studio", locations)
        self.assertIn("= /studio/sso", locations)
        self.assertIn("access_log off;", content)
        self.assertIn("^~ /studio/", locations)
        self.assertIn("= /workspace/growth", locations)
        self.assertIn("= /workspace/growth/", locations)
        self.assertIn("= /intelligence", locations)
        self.assertIn("= /competitive-intelligence/runs", locations)
        self.assertIn("^~ /competitive-intelligence/runs/", locations)
        self.assertTrue(any('^/products/' in value and 'comments' in value for value in locations))
        self.assertNotIn("location / {", content)
        self.assertNotIn("location /api/", content)
        self.assertNotIn("location ^~ /products/", content)
        self.assertEqual(len(re.findall(r"^\s*proxy_pass\s+", content, re.M)), 7)
        self.assertNotIn("proxy_set_header Authorization", content)
        self.assertNotIn("proxy_set_header Origin", content)
        for address in re.findall(r"^\s*proxy_pass\s+([^;]+);", content, re.M):
            self.assertEqual(address, "http://127.0.0.1:8091")
        self.assertIn("proxy_set_header Host $host;", content)
        self.assertIn("proxy_set_header X-Forwarded-Proto $scheme;", content)
        self.assertIn('location ~ "^/products/', content)

    def test_unapplied_gateway_nginx_syntax(self):
        nginx = shutil.which("nginx")
        if not nginx:
            self.skipTest("nginx binary not installed (CI installs it before this test)")
        with tempfile.TemporaryDirectory() as td:
            test_config = Path(td) / "gateway.conf"
            test_config.write_text(
                "pid " + str(Path(td) / "nginx.pid") + ";\n"
                "error_log stderr notice;\n"
                "events {}\n"
                "http {\n"
                "  access_log off;\n"
                "  client_body_temp_path " + str(Path(td) / "body") + ";\n"
                "  proxy_temp_path " + str(Path(td) / "proxy") + ";\n"
                "  server {\n"
                "    listen 127.0.0.1:18879;\n"
                "    server_name servgo.ru;\n"
                "    include " + str(GATEWAY) + ";\n"
                "    location / { return 404; }\n"
                "  }\n"
                "}\n"
            )
            result = subprocess.run(
                [nginx, "-t", "-c", str(test_config), "-p", td],
                capture_output=True, text=True, check=False, timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
