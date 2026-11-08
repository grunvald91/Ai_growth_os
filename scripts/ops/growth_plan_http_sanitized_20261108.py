"""Aggregate API request/CSRF errors without revealing identities or payloads."""
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
target_id = "ab4bdf82-0059-4197-8f08-0450f60c5dec"
proc = subprocess.run(
    ["docker","logs","--since","2026-11-07T00:00:00Z","--tail","50000","ai-growth-os-api-1"],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", timeout=45)
log = proc.stdout.splitlines()
request_counts = Counter()
csrf_counts = Counter()
other_errors = Counter()
for line in log:
    if ("studio_origin_rejected" in line or "/plan/generate" in line or
        (target_id in line and ("dispatch" in line or "HTTP/" in line))):
        if "studio_origin_rejected" in line:
            if "/plan/generate" in line:
                category = "generate"
            elif "/dispatch" in line:
                category = "dispatch"
            else:
                category = "other"
            result = re.search(r'"reason": "([^"]{0,70})"',line)
            csrf_counts[(category, result.group(1) if result else "unknown")] += 1
        elif "/plan/generate" in line:
            status = re.search(r'HTTP/[^ ]+["]?\s+(\d{3})',line)
            request_counts[status.group(1) if status else "route_seen_no_status"] += 1
        else:
            status = re.search(r'HTTP/[^ ]+["]?\s+(\d{3})',line)
            other_errors[status.group(1) if status else "dispatch_seen_no_status"] += 1
print("LOG_EXIT_CODE",proc.returncode)
print("LOG_LINE_COUNT",len(log))
print("PLAN_GENERATE_STATUS_COUNTS",sorted(request_counts.items()))
print("PLAN_ORIGIN_REJECTS",sorted(csrf_counts.items()))
print("PRORESULTAT_DISPATCH_HTTP",sorted(other_errors.items()))
