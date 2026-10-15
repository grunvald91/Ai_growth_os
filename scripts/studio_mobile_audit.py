"""Open every Studio section at phone width and report layout problems.

    python scripts/studio_mobile_audit.py BASE_URL [--user U --password P] [--width 360]

BASE_URL is the product page, e.g. https://growth.servgo.ru/studio/products/<id>.
Needs: pip install playwright && playwright install chromium
"""
import argparse
import sys

ROUTES = ["", "/create", "/work", "/review", "/materials", "/plan", "/calendar",
          "/channels", "/analytics", "/comments", "/whiteboard"]

AUDIT = """()=>{
 const vw=window.innerWidth, res={scrollW:document.documentElement.scrollWidth, off:[], small:[]};
 document.querySelectorAll('a,button,summary,input,select,textarea').forEach(e=>{
   const r=e.getBoundingClientRect(); if(r.width<2||r.height<2) return;
   const cs=getComputedStyle(e); if(cs.visibility==='hidden'||cs.display==='none') return;
   const label=(e.textContent||e.name||'').trim().slice(0,28);
   if(!e.closest('.calendar-date-tabs') && (r.right>vw+1||r.left<-1)) res.off.push(label+' '+Math.round(r.left)+'..'+Math.round(r.right));
   const inline=e.tagName==='A'&&e.closest('p,li,.article-text,.back-link');
   if(!inline&&r.height<40&&!['hidden','checkbox','radio'].includes(e.type)) res.small.push(label+' h'+Math.round(r.height));
 });
 res.off=res.off.slice(0,8); res.small=res.small.slice(0,8); return res;}"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--user")
    ap.add_argument("--password")
    ap.add_argument("--width", type=int, default=360)
    args = ap.parse_args()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Установите: pip install playwright && playwright install chromium")
        return 2
    failed = False
    with sync_playwright() as p:
        browser = p.chromium.launch()
        creds = {"username": args.user, "password": args.password} if args.user else None
        ctx = browser.new_context(viewport={"width": args.width, "height": 780}, is_mobile=True,
                                  has_touch=True, device_scale_factor=2, http_credentials=creds)
        page = ctx.new_page()
        for route in ROUTES:
            resp = page.goto(args.base.rstrip("/") + route, wait_until="networkidle")
            res = page.evaluate(AUDIT)
            problems = []
            if resp.status != 200:
                problems.append(f"HTTP {resp.status}")
            if res["scrollW"] > args.width:
                problems.append(f"горизонтальная прокрутка ({res['scrollW']} px)")
            if res["off"]:
                problems.append(f"за краем экрана: {res['off']}")
            if res["small"]:
                problems.append(f"мелкие кнопки: {res['small']}")
            print(f"{route or '/':<12} {'OK' if not problems else 'ПРОБЛЕМА'}")
            for line in problems:
                print("    -", line)
            failed |= bool(problems)
        browser.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
