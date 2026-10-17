"""Owner-provided ServGo product vision (2026-10-16). Usage: python servgo_vision.py <company-profile.json>"""
import json, sys
POSITIONING = (
    "ServGo Growth: путь от внимания к записи — маркетинговый контент, понятная онлайн-запись и измеримый "
    "цикл улучшений. Видение продукта: ServGo строится как платформа, данные которой легко анализируют "
    "ИИ-агенты. Они помогают владельцу делать правильные выводы по записям, вовремя замечать просадки, "
    "пустые окна и уходящих клиентов и подсказывают, что сделать. AI-маркетинг, ИИ-аналитика записей и "
    "сквозная атрибуция — направление развития: показывать их уверенно как видение и то, куда движется "
    "продукт («мы строим», «скоро»), но не как уже доступные функции."
)
HEDGES = ["пока не умеем", "это ещё не отчёт", "это не гарантия"]
# Owner-provided contacts for calls to action (2026-10-16).
CONVERSION = ("Запросить демонстрацию ServGo: написать в Telegram @Grunvald или на почту grunvaldlab@gmail.com")
path = sys.argv[1]
data = json.load(open(path))
data["brand"]["positioning"] = POSITIONING
data["offer"]["primary_conversion_action"] = CONVERSION
phrases = data["brand"].setdefault("prohibited_phrases", [])
phrases.extend(h for h in HEDGES if h not in phrases)
json.dump(data, open(path, "w"), ensure_ascii=False, indent=2)
open(path, "a").write("\n")
print("updated", path)
