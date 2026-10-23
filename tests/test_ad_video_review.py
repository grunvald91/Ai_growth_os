"""Критик рекламного ролика: один просмотр, решение автора, не больше одной перерисовки (без сети и рендера)."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("ad_review", Path(__file__).parent.parent / "tools/ad_video/review.py")
ad = importlib.util.module_from_spec(SPEC)
sys.modules["ad_review"] = ad
SPEC.loader.exec_module(ad)

SCRIPT = json.loads((Path(__file__).parent.parent / "tools/ad_video/script.example.json").read_text())


def issue(i="b2-small-text", beat=2, severity="major", confidence="high", category="readability"):
    return {"id": i, "beat_index": beat, "severity": severity, "confidence": confidence, "category": category,
            "evidence": "Шаги в сцене 3 написаны слишком мелко", "correction": "Укоротить подписи шагов, чтобы они крупнее"}


def decisions(*pairs):
    return json.dumps({"decisions": [{"issue_id": i, "accept": a, "reason": "Причина решения по замечанию"} for i, a in pairs]})


class Harness:
    def __init__(self, tmp, monkeypatch, *, vision, text, safe_issues_v2=()):
        self.builds, self.vision_calls, self.text_calls = [], 0, []
        self.safe_issues_v2 = list(safe_issues_v2)

        def fake_build(script_path, out):
            out.mkdir(parents=True, exist_ok=True)
            (out / "ad.mp4").write_bytes(b"video")
            self.builds.append(out.name)
            report = {"total_seconds": 20.0, "timing_method": "x", "beats": [], "frames": [{"beat": 0, "t": 1, "file": "f.png"}],
                      "safe_zone_issues": self.safe_issues_v2 if out.name == "v2" else []}
            (out / "report.json").write_text(json.dumps(report))
            return report

        def fake_vision(prompt, images):
            self.vision_calls += 1
            return vision(prompt, images) if callable(vision) else vision

        def fake_text(system, user):
            self.text_calls.append(system[:20])
            return text.pop(0)

        monkeypatch.setattr(ad, "build", fake_build)
        monkeypatch.setattr(ad, "jpeg", lambda *a, **k: b"jpg")
        self.script_path = tmp / "s.json"
        self.script_path.write_text(json.dumps(SCRIPT))
        self.out = tmp / "out"
        self.vision, self.text = fake_vision, fake_text

    def run(self):
        return ad.run_review(self.script_path, self.out, self.vision, self.text)


def test_only_serious_confident_non_style_issues_are_eligible():
    assert ad.AdIssue(**issue()).eligible
    assert not ad.AdIssue(**issue(severity="minor")).eligible
    assert not ad.AdIssue(**issue(confidence="uncertain")).eligible
    assert not ad.AdIssue(**issue(category="style")).eligible


def test_author_must_decide_every_issue_once_and_cannot_accept_ineligible():
    review = ad.AdReview(issues=[ad.AdIssue(**issue()), ad.AdIssue(**issue("b1-minor", 1, severity="minor"))])
    with pytest.raises(ValueError):
        ad.AdDecision.model_validate_json(decisions(("b2-small-text", True))).check_for(review)
    with pytest.raises(ValueError):
        ad.AdDecision.model_validate_json(decisions(("b2-small-text", True), ("b1-minor", True))).check_for(review)
    ok = ad.AdDecision.model_validate_json(decisions(("b2-small-text", True), ("b1-minor", False)))
    assert [i.id for i in ok.accepted(review)] == ["b2-small-text"]


def test_issue_id_must_match_its_beat():
    with pytest.raises(ValueError):
        ad.AdReview(issues=[ad.AdIssue(**issue("b1-wrong", 2))])


def test_patch_changes_only_allowed_text_fields():
    new = ad.apply_patch(SCRIPT, {"beats": [{"i": 2, "title": "Запись за минуту онлайн"}]})
    assert new["beats"][2]["title"] == "Запись за минуту онлайн" and SCRIPT["beats"][2]["title"] == "Запись за минуту"
    for bad in ({"i": 0, "type": "cta"}, {"i": 3, "open": 9}, {"i": 2, "items": ["Один", "Два"]},
                {"i": 2, "title": 5}, {"i": 9, "title": "Нет такой сцены"}, {"i": 1, "voice": "Барбершоп. Запись на время."},
                {"i": 0, "lines": [1, 1]}, {"i": 2, "colour": "red"}):
        with pytest.raises(ValueError):
            ad.apply_patch(SCRIPT, {"beats": [bad]})
    with pytest.raises(ValueError):
        ad.apply_patch(SCRIPT, {"beats": []})


def test_no_eligible_issues_means_one_look_and_no_redraw(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, vision=json.dumps({"issues": [issue(severity="minor")]}), text=[])
    result = h.run()
    assert (h.vision_calls, h.text_calls, h.builds) == (1, [], ["v1"])
    assert result["final"] == "v1" and (h.out / "final.mp4").exists()


def test_accepted_issue_redraws_exactly_once(tmp_path, monkeypatch):
    patch = json.dumps({"beats": [{"i": 2, "title": "Запись за минуту онлайн"}]})
    h = Harness(tmp_path, monkeypatch, vision=json.dumps({"issues": [issue()]}),
                text=[decisions(("b2-small-text", True)), patch])
    result = h.run()
    assert (h.vision_calls, len(h.text_calls), h.builds) == (1, 2, ["v1", "v2"])
    assert result["final"] == "v2"
    assert json.loads((h.out / "script_v2.json").read_text())["beats"][2]["title"] == "Запись за минуту онлайн"


def test_author_rejecting_everything_keeps_the_first_video(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, vision=json.dumps({"issues": [issue()]}), text=[decisions(("b2-small-text", False))])
    result = h.run()
    assert h.builds == ["v1"] and result["final"] == "v1"


def test_broken_critic_or_author_never_breaks_the_video(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, vision="not json", text=[])
    assert h.run()["final"] == "v1" and "critic unavailable" in h.run()["note"]
    h = Harness(tmp_path / "b", monkeypatch, vision=json.dumps({"issues": [issue()]}), text=["{}"]) if (tmp_path / "b").mkdir() is None else None
    assert h.run()["final"] == "v1" and h.builds == ["v1"]


def test_redraw_that_leaves_the_safe_zone_is_discarded(tmp_path, monkeypatch):
    patch = json.dumps({"beats": [{"i": 2, "title": "Запись за минуту онлайн"}]})
    h = Harness(tmp_path, monkeypatch, vision=json.dumps({"issues": [issue()]}),
                text=[decisions(("b2-small-text", True)), patch], safe_issues_v2=[{"text": "x"}])
    result = h.run()
    assert h.builds == ["v1", "v2"] and result["final"] == "v1"
