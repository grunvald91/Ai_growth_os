"""Real Chromium smoke of the built ServGo PWA with mocked local booking data.

No real users, production API, email or booking changes. Uses only the dist
artifact and a loopback HTTP fixture. Checks cold reload without network.
"""
from __future__ import annotations

from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

from playwright.sync_api import sync_playwright

DIST = Path(__file__).resolve().parents[1] / "web" / "frontend" / "dist"
SERVICE = "Тестовая услуга для офлайн-режима"
BOOKINGS = [{
    "id": 910001,
    "business_id": 1,
    "business_name": "Тестовый салон",
    "business_address": "Тестовый адрес",
    "service_id": 1,
    "service_name": SERVICE,
    "employee_id": 1,
    "employee_name": "Тестовый мастер",
    "date": "2099-11-20",
    "time": "15:00",
    "duration_minutes": 60,
    "status": "confirmed",
    "can_cancel": True,
    "can_reschedule": True,
    "price": 1500,
}]


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIST), **kwargs)

    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path.startswith("/api/admin/prepayments/attention"):
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"detail":"Temporary payment-check failure"}')
            return
        if self.path.startswith("/api/auth/session") and "sg_shared=ok" not in self.headers.get("Cookie", ""):
            self.send_response(401)
            self.end_headers()
            return
        if self.path.startswith("/api/"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.path.startswith("/api/my/bookings"):
                self.wfile.write(json.dumps(BOOKINGS, ensure_ascii=False).encode())
            elif self.path.startswith("/api/auth/me"):
                self.wfile.write(json.dumps({"id":1,"full_name":"Тестовый пользователь","role":"client"}).encode())
            elif self.path.startswith("/api/auth/session"):
                self.wfile.write(json.dumps({
                    "token": "owner-fixture-token", "role": "owner",
                    "user": {"id": 42, "full_name": "Тестовый владелец"},
                }, ensure_ascii=False).encode())
            elif self.path.startswith("/api/studio/businesses"):
                self.wfile.write(b'[]')
            else:
                self.wfile.write(b'{}')
            return
        path = self.path.split("?", 1)[0]
        if path not in ("/",) and not (DIST / path.lstrip("/")).is_file():
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/auth/logout"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie", "sg_shared=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            return
        self.send_error(404)


def main():
    if not (DIST / "sw.js").is_file():
        raise RuntimeError("Build web/frontend/dist first")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context(viewport={"width": 390, "height": 844})
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.add_init_script("""
              localStorage.setItem('token','servgo-browser-offline-fixture');
              localStorage.setItem('userRole','client');
              localStorage.setItem('userName','Тестовый пользователь');
            """)
            seen_api = []
            page.on("response", lambda response: seen_api.append({
              "url": response.url.split("?", 1)[0],
              "status": response.status,
            }) if "/api/" in response.url else None)
            page.goto(url + "/my", wait_until="load")
            try:
                page.get_by_text(SERVICE, exact=False).first.wait_for(timeout=12000)
            except Exception:
                print("PWA_BROWSER_DIAGNOSTICS", json.dumps({
                    "url": page.url, "body": page.locator("body").inner_text()[:1400],
                    "api": seen_api[-20:], "pageErrors": errors[-12:],
                    "token": page.evaluate("!!localStorage.getItem('token')"),
                }, ensure_ascii=False), flush=True)
                raise
            page.evaluate("""async () => {
              const ready = await navigator.serviceWorker.ready;
              if (!ready.active) throw Error('No active service worker');
              await new Promise((resolve,reject) => {
                const req=indexedDB.open('servgo-offline-snapshots-v1');
                req.onerror=()=>reject(req.error);
                req.onsuccess=()=>{
                  const db=req.result;
                  const tx=db.transaction('snapshots','readonly');
                  const all=tx.objectStore('snapshots').getAll();
                  all.onsuccess=()=>{
                    if (!all.result.some(v=>v.id.includes('/my/bookings'))) {
                      reject(Error('Booking snapshot not saved'))
                    } else resolve();
                  };
                  all.onerror=()=>reject(all.error);
                  tx.oncomplete=()=>db.close();
                };
              });
            }""")
            context.set_offline(True)
            # Browser navigation is now served by SW v4's generic app shell.
            page.goto(url + "/my", wait_until="domcontentloaded")
            page.get_by_text(SERVICE, exact=False).first.wait_for(timeout=15000)
            page.get_by_text("Нет соединения", exact=False).first.wait_for(timeout=10000)
            has_cancel=page.get_by_role("button", name="Отменить").count()
            if has_cancel:
                assert page.get_by_role("button", name="Отменить").first.is_disabled(), \
                    "Offline booking mutation must be disabled"
            assert not errors, errors
            print("PWA_BROWSER_COLD_OFFLINE_OK", json.dumps({
                "route":"/my","offline":True,"booking_visible":True,
                "service_worker":"v4","mutations_disabled":True,
            }, ensure_ascii=False), flush=True)
            context.set_offline(False)

            # Isolated owner: failed payment checking must not trap the schedule,
            # and signing out must invalidate the shared cookie and new-tab auth.
            owner = browser.new_context(viewport={"width": 390, "height": 844})
            owner.add_cookies([{"name": "sg_shared", "value": "ok", "url": url, "httpOnly": True}])
            owner_page = owner.new_page()
            owner_page.goto(url + "/admin/schedule", wait_until="domcontentloaded")
            owner_page.get_by_text("Проверка предоплат недоступна", exact=False).first.wait_for(timeout=15000)
            assert owner_page.locator('[role="alertdialog"]').count() == 0, (
                "Failed payment check must not trap schedule behind a modal"
            )
            owner_page.wait_for_function(
                "() => localStorage.getItem('userRole') === 'owner' && "
                "!!document.querySelector('button[aria-label=Выйти]')",
                timeout=15000,
            )
            # The schedule header is re-mounted during its initial data loads.
            # DOM click avoids Playwright's stable-box retry on the detached
            # header; the same real React onClick handler still runs.
            owner_page.evaluate("""() => {
                const button = document.querySelector('button[aria-label="Выйти"]');
                if (!button) throw Error('Schedule logout control missing');
                button.click();
            }""")
            owner_page.wait_for_url(url + "/login", timeout=15000)
            assert not any(cookie["name"] == "sg_shared" for cookie in owner.cookies()), (
                "Logout failed to clear the shared HttpOnly cookie"
            )
            second_tab = owner.new_page()
            second_tab.goto(url + "/admin/schedule", wait_until="domcontentloaded")
            second_tab.wait_for_url(url + "/login", timeout=15000)
            assert second_tab.evaluate("localStorage.getItem('token')") is None
            assert second_tab.evaluate("localStorage.getItem('servgoExplicitLogout')") == "1"
            print("PWA_BROWSER_LOGOUT_AND_PAYMENT_FAILURE_OK", flush=True)
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
