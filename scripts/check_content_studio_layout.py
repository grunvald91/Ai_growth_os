#!/usr/bin/env python3
"""Browser QA for ServGo Content Studio, *not* a deployment or CI action.

Install locally in Codespaces:
  python -m pip install playwright
  python -m playwright install chromium

Set STUDIO_URL to an owner-authorized /studio/products/<uuid> URL.
Set STUDIO_ADMIN_USERNAME and STUDIO_ADMIN_PASSWORD only in your terminal
environment if HTTP Basic authorization is enabled.
Screenshots can contain business data: output stays local by default.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

SIZES = [
    ("android-320", 320, 720),
    ("android-360", 360, 800),
    ("android-390", 390, 844),
    ("android-430", 430, 932),
    ("tablet-768", 768, 1024),
    ("desktop-1024", 1024, 768),
    ("desktop-1440", 1440, 900),
]
ROUTES = [
    ("overview", ""),
    ("create", "/create"),
    ("review", "/review"),
    ("calendar", "/calendar"),
    ("materials", "/materials"),
    ("channels", "/channels"),
]
EXPECTED_BUILD = "20261011-ux8"


async def check_layout(page, name, width, route):
    """Inspect layout geometry and typography, not simply page HTTP status."""
    return await page.evaluate(
        """({width, route}) => {
          const by = (s) => document.querySelector(s);
          const body = document.body;
          const layout = by('.studio-layout');
          const main = by('.main');
          const header = by('.sg-mobile-shell-head');
          const nav = by('.mobile-nav');
          const notice = by('.sg-draft-alert');
          const build = by('[data-studio-build]')?.dataset.studioBuild ?? null;
          const navText = nav?.innerText?.replace(/\\s+/g, ' ').trim() ?? '';
          const rect = (el) => {
            const r = el?.getBoundingClientRect();
            if (!r) return null;
            return {x:r.x, y:r.y, left:r.left, right:r.right, top:r.top,
                    bottom:r.bottom, width:r.width, height:r.height};
          };
          const style = (el) => el ? getComputedStyle(el) : null;
          const errors = [];
          if (!layout || !main) errors.push('Main layout missing');
          if (!build || build !== '20261011-ux8')
            errors.push('Wrong deployed build: ' + String(build));
          if (document.documentElement.scrollWidth > innerWidth + 2)
            errors.push('Horizontal overflow: ' + document.documentElement.scrollWidth
                        + ' > ' + innerWidth);
          if (width <= 920) {
            if (!header || !nav) errors.push('Missing mobile brand header or navigation');
            if (nav && style(nav).position === 'fixed')
              errors.push('Mobile navigation is fixed and may cover content');
            const rNav = rect(nav), rMain = rect(main);
            if (rNav && rMain && rNav.bottom > rMain.top + 4 && scrollY === 0)
              errors.push('Mobile navigation overlaps main');
            if (navText.includes('Комментарии') || navText.includes('Ещё'))
              errors.push('Legacy mobile menu detected: ' + navText);
            if (!navText.includes('Создать') || !navText.includes('Календарь'))
              errors.push('Wrong mobile menu: ' + navText);
          }
          if (notice) {
            const r = rect(notice), btn = rect(notice.querySelector('.sg-draft-alert-action'));
            if (!btn || btn.left < r.left - 2 || btn.right > r.right + 2
                || btn.bottom > r.bottom + 2)
              errors.push('Draft notice action overflows card');
          }
          const sample = by('.sg-mini-row b') || by('.sg-review-item h3')
                       || by('.sg-section-head h2') || main;
          const metrics = {
            fontFamily: style(main)?.fontFamily ?? '',
            bodyFontSize: style(main)?.fontSize ?? '',
            headlineWeight: style(by('h1'))?.fontWeight ?? '',
            cardFontSize: style(sample)?.fontSize ?? '',
            cardFontWeight: style(sample)?.fontWeight ?? '',
            mobileNavPosition: style(nav)?.position ?? '',
            mobileNavText: navText
          };
          if (parseFloat(metrics.bodyFontSize) < 14)
            errors.push('Body text too small');
          return {route, width, build, metrics, errors};
        }""",
        {"width": width, "route": route},
    )


async def main():
    url = os.environ.get("STUDIO_URL", "").strip().rstrip("/")
    parsed = urlparse(url)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.netloc
        or not re.search(r"/studio/products/[0-9a-fA-F-]{36}$", parsed.path)
    ):
        raise SystemExit(
            "Set STUDIO_URL to an authorized company page: "
            "https://growth.servgo.ru/studio/products/<uuid>"
        )
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise SystemExit("Non-local Studio URL must use HTTPS")

    target = Path(os.environ.get("STUDIO_QA_OUT", "/tmp/servgo-studio-ui-qa"))
    target.mkdir(parents=True, exist_ok=True)
    credentials = None
    username = os.environ.get("STUDIO_ADMIN_USERNAME")
    password = os.environ.get("STUDIO_ADMIN_PASSWORD")
    if username and password:
        credentials = {"username": username, "password": password}

    results = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            for name, width, height in SIZES:
                context = await browser.new_context(
                    viewport={"width": width, "height": height},
                    device_scale_factor=1,
                    is_mobile=width < 600,
                    has_touch=width < 600,
                    http_credentials=credentials,
                    locale="ru-RU",
                )
                page = await context.new_page()
                for label, suffix in ROUTES:
                    response = await page.goto(url + suffix, wait_until="domcontentloaded")
                    if response is None or response.status != 200:
                        results.append({
                            "route": label, "width": width,
                            "errors": [f"Page HTTP status: {response.status if response else '?'}"],
                        })
                        continue
                    await page.wait_for_timeout(120)
                    check = await check_layout(page, name, width, label)
                    results.append(check)
                    if check["errors"] or label == "overview":
                        await page.screenshot(
                            path=str(target / f"{name}-{label}.png"),
                            full_page=True,
                        )
                await context.close()
        finally:
            await browser.close()

    report = target / "report.json"
    report.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    failures = [x for x in results if x.get("errors")]
    print(f"Tested {len(results)} viewports/routes; issues: {len(failures)}.")
    print(f"Local report: {report}")
    for case in failures:
        print(f"  {case['width']}px / {case['route']}: {'; '.join(case['errors'])}")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    asyncio.run(main())
