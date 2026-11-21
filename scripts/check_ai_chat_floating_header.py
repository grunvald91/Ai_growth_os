"""Regression: standalone AI Chat content scrolls behind floating controls.

Uses real HTML+CSS without provider requests, credentials, or data mutations.
CI: python scripts/check_ai_chat_floating_header.py
"""
from __future__ import annotations

from pathlib import Path
import re

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "app/api/ai_chat_assets/chat.html"
STYLES = ROOT / "app/api/ai_chat_assets/ai-chat.css"
TOKENS = ROOT / "app/api/assets/servgo-air-tokens.css"
CAPTURES = Path("/tmp/servgo-growth-voice-ui")


def _static_html() -> str:
    html = TEMPLATE.read_text(encoding="utf-8")
    html = re.sub(r'<link\s+rel="stylesheet"[^>]*>', "", html)
    html = re.sub(r'<script\b[^>]*\bsrc="[^"]+"[^>]*></script>', "", html)
    html = html.replace("__BOOT_CONFIG__", "{}")
    return html.replace("</head>", (
        "<style>" + TOKENS.read_text(encoding="utf-8") + "</style>"
        "<style>" + STYLES.read_text(encoding="utf-8") + "</style></head>"
    ), 1)


def main() -> None:
    CAPTURES.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 800})
        page.emulate_media(reduced_motion="reduce")
        html = _static_html()
        for width in (320, 360, 390, 768, 860, 861, 1024, 1440):
            page.set_viewport_size({"width": width, "height": 800})
            for mode in ("light", "dark"):
                page.set_content(html, wait_until="domcontentloaded")
                page.evaluate("""(theme) => {
                  document.documentElement.dataset.theme = theme;
                  document.getElementById('empty-state').hidden = true;
                  const list=document.getElementById('message-list');
                  for(let i=0;i<38;i++) {
                    const item=document.createElement('div');
                    item.className='ai-msg ai-msg-assistant';
                    const card=document.createElement('div');
                    card.className='ai-msg-card';
                    card.textContent='Тест прокрутки: сообщение '+i;
                    item.appendChild(card);
                    list.appendChild(item);
                  }
                }""", mode)
                result=page.evaluate("""() => {
                  const rect=(selector)=>document.querySelector(selector).getBoundingClientRect();
                  const header=document.querySelector('.ai-topbar');
                  const feed=document.querySelector('.ai-chat-scroll');
                  const first=document.querySelector('#message-list .ai-msg');
                  const control=document.querySelector('.ai-model-trigger');
                  const left=rect('.ai-topbar-left'),right=rect('.ai-topbar-right');
                  const controlRect=control.getBoundingClientRect();
                  const feedRect=feed.getBoundingClientRect();
                  feed.scrollTop=0;
                  const initial=first.getBoundingClientRect().top;
                  feed.scrollTop=90;
                  const moved=first.getBoundingClientRect().top;
                  const computed=getComputedStyle(header);
                  const fill=getComputedStyle(control).backgroundColor;
                  const underlay=feedRect.top<=3 && feedRect.top<controlRect.bottom &&
                    initial>=controlRect.bottom &&
                    moved<controlRect.bottom-16 &&
                    moved>=feedRect.top-20 &&
                    feed.scrollTop>=85;
                  const hit=document.elementFromPoint(controlRect.left+controlRect.width/2,
                    controlRect.top+controlRect.height/2);
                  return {width:innerWidth,headerHeight:header.getBoundingClientRect().height,
                    headerBackground:computed.backgroundColor,headerPointer:computed.pointerEvents,
                    controlFill:fill,controlOpacity:getComputedStyle(control).opacity,
                    leftRight:left.right,rightLeft:right.left,
                    feedTop:feedRect.top,controlsBottom:controlRect.bottom,
                    initial,moved,scrollTop:feed.scrollTop,underlay,
                    controlHit:control.contains(hit),contentWidth:document.documentElement.scrollWidth,
                    composeBottom:rect('.ai-composer-shell').bottom,
                    viewportHeight:innerHeight};
                }""")
                assert result["headerHeight"]<=2,result
                assert result["headerBackground"]=="rgba(0, 0, 0, 0)",result
                assert result["headerPointer"]=="none",result
                assert result["controlOpacity"]=="1",result
                assert result["controlFill"]!="rgba(0, 0, 0, 0)",result
                assert result["underlay"],result
                assert result["contentWidth"]<=width,result
                assert result["controlHit"],result
                assert result["composeBottom"]<=801,result
                if width>=861:
                    assert result["leftRight"]<=result["rightLeft"]+2,result
                if width in (320,390,861,1440):
                    page.screenshot(path=str(CAPTURES/f"ai-chat-header-{width}-{mode}.png"),
                                    animations="disabled")
        browser.close()
    print("PASS standalone AI Chat: floating translucent controls and scroll-under on "
          "8 phone/tablet/desktop widths, both themes; no horizontal overflow")


if __name__=="__main__":
    main()
