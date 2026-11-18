"""One-time public GEO P0 validation using independently located Globalping probes.

No credentials, paid models, company content or private URL requests.
Does not claim actual AI bot traffic, indexing, search visibility or citations.
The Globalping API uses its OWN HTTP User-Agent (not OAI-SearchBot).
"""
from __future__ import annotations

import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler

API = "https://api.globalping.io/v1/measurements"
TARGET = "servgo.ru"
# All paths are public and independently observed before this check.
PATHS = ("/blog", "/blog/programma-dlya-zapisi-klientov-po-vremeni",
         "/blog/sitemap.xml")
_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
open_direct = build_opener(ProxyHandler({}))


def query(url: str, *, payload: dict | None = None) -> dict:
    content = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = Request(url, data=content,
                  headers={"Accept": "application/json", "Content-Type": "application/json",
                           "User-Agent": "ServGoGEOAudit/1.0"},
                  method="POST" if payload is not None else "GET")
    try:
        with open_direct.open(req, timeout=16) as response:
            return json.loads(response.read(700_000))
    except HTTPError as exc:
        return {"provider_error_code": exc.code}
    except (URLError, TimeoutError, OSError, ValueError):
        return {"provider_error_code": "unavailable"}


def probe(path: str) -> dict:
    body = {
        "type": "http", "target": TARGET,
        "locations": [{"country": "RU"}],
        "limit": 2,
        "measurementOptions": {
            "protocol": "HTTPS",
            "request": {"method": "HEAD", "path": path},
        },
        "timeout": 15,
    }
    result = {"path": path, "provider": "Globalping", "requested_country": "RU",
              "measured": False, "probe_count": 0, "status": "unknown"}
    created = query(API, payload=body)
    identifier = str(created.get("id", ""))
    if not _ID.fullmatch(identifier):
        result["provider_error_code"] = created.get("provider_error_code", "no_ru_probe_or_invalid_response")
        return result
    result["measurement_url"] = f"https://globalping.io?measurement={identifier}"
    for i in range(20):
        answer = query(API + "/" + identifier)
        if answer.get("status") not in ("in-progress", "finished"):
            result["provider_status"] = str(answer.get("status") or "not_reported")[:55]
            result["provider_error_code"] = answer.get("provider_error_code")
            return result
        if answer.get("status") == "finished":
            rows = answer.get("results", [])
            result["probe_count"] = len(rows)
            result["probes"] = []
            for item in rows:
                loc = item.get("probe") or {}
                response = item.get("result") or {}
                country = loc.get("country")
                http = response.get("statusCode")
                result["probes"].append({
                    "country": country,
                    "city": str(loc.get("city") or "")[:80],
                    "measurement_status": response.get("status"),
                    "http": http if isinstance(http, int) else None,
                })
            if rows:
                result["measured"] = True
                if all(x["country"] == "RU" and x["measurement_status"] == "finished"
                       and x["http"] == 200 for x in result["probes"]):
                    result["status"] = "pass"
                else:
                    result["status"] = "fail"
            return result
        time.sleep(2)
    result["provider_status"] = "timeout"
    return result


def main() -> None:
    results = [probe(path) for path in PATHS]
    print(json.dumps({"scope": "public-HEAD-only", "probes": results},
                     ensure_ascii=False, indent=2))
    # Do not equate no Russian probes with a failing website; the CI job fails
    # to clearly signal that this required region has NOT been proven.
    raise SystemExit(0 if all(x["status"] == "pass" for x in results) else 1)


if __name__ == "__main__":
    main()
