"""Генератор sitemap.xml из живого списка бизнесов (scripts/ops/servgo_sitemap.py)."""
import importlib.util
import json
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ops" / "servgo_sitemap.py"
spec = importlib.util.spec_from_file_location("servgo_sitemap", SCRIPT)
sm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sm)
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def test_build_sitemap_lists_home_and_every_business_including_demo():
    xml = sm.build_sitemap([11, 6, 8], "2026-10-18")
    locs = [e.text for e in ET.fromstring(xml).findall("s:url/s:loc", NS)]
    assert locs[0] == "https://servgo.ru/"
    assert set(locs[1:]) == {f"https://servgo.ru/business/{i}" for i in (6, 8, 11)}


def test_main_writes_file_from_api(tmp_path, monkeypatch):
    api = tmp_path / "businesses.json"
    api.write_text(json.dumps([{"id": 6, "name": "Студия"}, {"id": 8, "name": "Массаж (ПРИМЕР)"}]), encoding="utf-8")
    out = tmp_path / "sitemap.xml"
    assert sm.main(["--api-url", api.as_uri(), "--out", str(out)]) == 0
    assert "https://servgo.ru/business/8" in out.read_text(encoding="utf-8")


def test_main_keeps_previous_file_when_api_fails(tmp_path):
    out = tmp_path / "sitemap.xml"
    out.write_text("PREVIOUS", encoding="utf-8")
    assert sm.main(["--api-url", "http://127.0.0.1:9/none", "--out", str(out)]) == 1
    assert out.read_text(encoding="utf-8") == "PREVIOUS"


def test_main_rejects_non_list_payload(tmp_path):
    api = tmp_path / "bad.json"
    api.write_text('{"detail": "error"}', encoding="utf-8")
    out = tmp_path / "sitemap.xml"
    out.write_text("PREVIOUS", encoding="utf-8")
    assert sm.main(["--api-url", api.as_uri(), "--out", str(out)]) == 1
    assert out.read_text(encoding="utf-8") == "PREVIOUS"
