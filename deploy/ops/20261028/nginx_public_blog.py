"""Open the read-only blog feed on growth.servgo.ru: `location /public/blog/` next to `location /studio`.

The blog on servgo.ru reads published articles from https://growth.servgo.ru/public/blog/servgo/articles.
Only GET and HEAD are passed, only under /public/blog/, and the proxy target is copied from the existing
/studio block, so the feed reaches the same application and nothing else becomes reachable.

Usage (on the server, as root):
    cp /etc/nginx/sites-available/growth-servgo /opt/ai_growth_os/origin-fixes/growth-servgo.before-public-blog
    python3 nginx_public_blog.py /etc/nginx/sites-available/growth-servgo
    nginx -t && systemctl reload nginx
    curl -s https://growth.servgo.ru/public/blog/servgo/articles
Rollback: restore the backup copy, then `nginx -t && systemctl reload nginx`.
"""
import re
import sys

path = sys.argv[1]
text = open(path).read()
if "location /public/blog/" in text:
    print("already applied")
    sys.exit(0)

start = text.index("    location /studio {")
end = text.index("    }\n", start) + len("    }\n")
studio = text[start:end]
proxy = re.search(r"^\s*proxy_pass\s+[^;]+;\s*$", studio, re.M)
assert proxy is not None, "no proxy_pass in the /studio block"
headers = re.findall(r"^\s*proxy_set_header\s+(?:Host|X-Real-IP|X-Forwarded-For|X-Forwarded-Proto)\s+[^;]+;\s*$",
                     studio, re.M)

block = (
    "\n    location /public/blog/ {\n"
    "        limit_except GET HEAD { deny all; }\n"
    f"{proxy.group(0)}\n"
    + "".join(h + "\n" for h in headers)
    + "        proxy_read_timeout 30s;\n"
    "    }\n"
)
open(path, "w").write(text[:end] + block + text[end:])
print("edited")
