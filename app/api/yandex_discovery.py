"""Company-scoped Yandex discovery for Growth, onboarding and market research."""
from __future__ import annotations

from collections import Counter
from sqlalchemy import select
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from pydantic import BaseModel, Field

from app.api.content_studio import studio_page_shell
from app.api.content_studio_company import _require_operator
from app.company_profile import load_company_profile
from app.core.config import get_settings
from app.db.session import get_db
from app.integrations.yandex_organizations import YandexOrganizationsError, search
from app.integrations.yandex_evidence import sign_organization, verify_organization
from app.models.domain import Artifact, Product
from app.services.artifacts import ArtifactService
from app.workflows.research_contracts import ResearchSource, ResearchSourceBundle

router = APIRouter()


@router.get("/studio/assets/yandex-company.js")
def yandex_company_script():
    return FileResponse(Path(__file__).with_name("assets") / "yandex-company.js",
                        media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})



async def _lookup(query: str, city: str, *, limit: int = 25, skip: int = 0):
    if not 2 <= len(query.strip()) <= 100 or not 2 <= len(city.strip()) <= 80:
        raise HTTPException(422, "Укажите организацию или нишу и город")
    try:
        result = await search(query, city, quota_path=Path(get_settings().products_root) /
                              "_integrations" / "yandex_ppo_usage.sqlite",
                              results=limit, skip=skip)
    except YandexOrganizationsError as exc:
        raise HTTPException(503, str(exc)) from None
    return result


def _company(db, product_id: UUID):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(404, "Компания не найдена")
    return product


@router.get("/studio/organizations/search")
async def before_company_search(
    request: Request,
    query: str = Query(min_length=2, max_length=100),
    city: str = Query(min_length=2, max_length=80),
):
    """For the admin-only 'new company' form, before a company ID exists."""
    _require_operator(request)
    value = await _lookup(query, city, limit=10)
    for item in value["organizations"]:
        item["selection_token"] = sign_organization(item, scope="new", query=query, city=city)
    return JSONResponse(value, headers={"Cache-Control": "no-store"})


@router.get("/studio/products/{product_id}/organizations/search")
async def company_search(
    product_id: UUID,
    query: str = Query(min_length=2, max_length=100),
    city: str = Query(min_length=2, max_length=80),
    skip: int = Query(default=0, ge=0, le=1000),
    db=Depends(get_db),
):
    """Product URL scope is also enforced by the Studio auth dependency."""
    _company(db, product_id)
    value = await _lookup(query, city, skip=skip)
    for item in value["organizations"]:
        item["selection_token"] = sign_organization(
            item, scope=str(product_id), query=query, city=city)
    return JSONResponse(value, headers={"Cache-Control": "no-store"})


class SourceSelection(BaseModel):
    query: str = Field(min_length=2, max_length=100)
    city: str = Field(min_length=2, max_length=80)
    tokens: list[str] = Field(min_length=1, max_length=25)


def _source_rows(organizations: list[dict], selected: set[str]) -> list[ResearchSource]:
    rows = []
    seen = set()
    for item in organizations:
        oid = item["id"]
        if oid not in selected or oid in seen:
            continue
        seen.add(oid)
        # A Yandex Maps result is directory evidence, not a review, price or
        # an independently verified claim about the business's performance.
        uri = item.get("maps_url") or item.get("website")
        if not uri or urlsplit(uri).scheme != "https":
            continue
        excerpt = "Яндекс Карты: " + str(item["name"])
        if item.get("address"):
            excerpt += "; адрес: " + item["address"]
        if item.get("categories"):
            excerpt += "; категории: " + ", ".join(item["categories"][:5])
        rows.append(ResearchSource(
            url=uri, title=item["name"], platform="Яндекс Карты",
            competitor=item["name"], excerpt=excerpt[:4000],
            observed_at=datetime.now(timezone.utc).isoformat(),
            source_type="website",
        ))
    return rows


@router.post("/studio/products/{product_id}/market-research/sources", status_code=201)
async def save_market_sources(product_id: UUID, body: SourceSelection, db=Depends(get_db)):
    """Verify signed Yandex results without re-querying or trusting browser data."""
    product = _company(db, product_id)
    if len(set(body.tokens)) != len(body.tokens):
        raise HTTPException(422, "Повторяющиеся организации")
    try:
        selected = [verify_organization(
            token, scope=str(product_id), query=body.query, city=body.city)
            for token in body.tokens]
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    sources = _source_rows(selected, {item["id"] for item in selected})
    if not sources:
        raise HTTPException(422, "Отобранные организации не найдены в выдаче Яндекса")
    bundle = ResearchSourceBundle(
        label=f"Яндекс Карты: {body.query}, {body.city}"[:300], sources=sources)
    artifact = ArtifactService(db).create(
        product_id=product.id, kind="research_source_bundle", title=bundle.label,
        content=bundle.model_dump_json())
    db.commit()
    return {"artifact_id": str(artifact.id), "saved": len(sources),
            "intelligence_url": f"/studio/products/{product_id}/market-research#ym-saved"}


@router.get("/studio/products/{product_id}/market-research", response_class=HTMLResponse)
def market_research(product_id: UUID, db=Depends(get_db)):
    product = _company(db, product_id)
    profile = load_company_profile(get_settings().products_root, product.slug).profile
    identity = profile.identity if profile else None
    initial_niche = (identity.category or identity.industry or "") if identity else ""
    initial_city = (identity.service_geography[0] if identity and identity.service_geography
                    else identity.geography or "" if identity else "")
    # Always show the current tenant only. /intelligence is an operator-only
    # console and is not reachable by company-scoped ServGo accounts.
    previous = list(db.scalars(select(Artifact).where(
        Artifact.product_id == product_id,
        Artifact.kind == "research_source_bundle",
    ).order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(12)))
    source_cards = []
    for entry in previous:
        try:
            bundle = ResearchSourceBundle.model_validate_json(entry.content)
        except (ValueError, TypeError):
            continue
        source_cards.append(
            '<li><strong>' + escape(bundle.label) + '</strong> · '
            + str(len(bundle.sources)) + ' организаций и источников</li>'
        )
    saved_html = (
        '<ul>' + ''.join(source_cards) + '</ul>' if source_cards
        else '<p>Источники ещё не сохранены.</p>'
    )
    body = f"""
<section class="sg-surface" style="padding:20px">
  <h2>Организации и конкуренты по Яндекс Картам</h2>
  <p>Выберите отрасль и город. Результаты — карточки организаций, а не данные о поисковом спросе.</p>
  <form id="ym-research-form" class="sg-intake-form">
    <label>Отрасль или запрос<input id="ym-niche" required minlength="2" maxlength="100" value="{escape(initial_niche,quote=True)}"></label>
    <label>Город<input id="ym-city" required minlength="2" maxlength="80" value="{escape(initial_city,quote=True)}"></label>
    <button class="btn primary" type="submit">Найти организации</button>
  </form>
  <p id="ym-status" role="status" aria-live="polite"></p>
  <div id="ym-stats"></div><div id="ym-results"></div>
  <button id="ym-save" class="btn" type="button" hidden>Добавить выбранных в бизнес-разведку</button>
  <section id="ym-saved"><h3>Сохранённые источники компании</h3>
  {saved_html}</section>
</section>
<script>
(()=>{{'use strict';
const id={str(product_id)!r}, $=x=>document.getElementById(x); let rows=[];
function elt(tag,txt,parent){{const e=document.createElement(tag);if(txt!==undefined)e.textContent=txt;if(parent)parent.append(e);return e;}}
$('ym-research-form').onsubmit=async ev=>{{
  ev.preventDefault();$('ym-status').textContent='Ищу организации…';
  const query=$('ym-niche').value.trim(),city=$('ym-city').value.trim(),root=$('ym-results');root.replaceChildren();
  $('ym-save').hidden=true;rows=[];
  try{{
    const r=await fetch('/studio/products/'+id+'/organizations/search?'+new URLSearchParams({{query,city}}));
    const data=await r.json();if(!r.ok)throw Error(data.detail||'Ошибка поиска');
    rows=data.organizations||[];const groups={{}};rows.forEach(o=>(o.categories||[]).forEach(c=>groups[c]=(groups[c]||0)+1));
    $('ym-stats').textContent='Найдено: '+rows.length+' · категории: '+Object.entries(groups).sort((a,b)=>b[1]-a[1]).slice(0,6).map(x=>x[0]+' ('+x[1]+')').join(', ');
    rows.forEach(o=>{{
      const p=elt('div',undefined,root);p.style='padding:12px 0;border-bottom:1px solid var(--sg-line,#ddd)';
      const l=elt('label',undefined,p),check=elt('input',undefined,l);check.type='checkbox';check.value=o.selection_token;check.checked=true;
      elt('strong',' '+o.name,l);
      elt('p',[o.address,(o.categories||[]).join(', ')].filter(Boolean).join(' · '),p);
      const link=elt('a','Открыть в Картах',p);link.href=o.maps_url||o.website||'#';link.target='_blank';link.rel='noopener noreferrer';
    }});
    $('ym-save').hidden=!rows.length;$('ym-status').textContent=rows.length?'Выберите компании и сохраните как источники исследования.':'Совпадений нет.';
  }}catch(e){{$('ym-status').textContent=String(e.message||e);}}
}};
$('ym-save').onclick=async()=>{{
 const tokens=[...$('ym-results').querySelectorAll('input:checked')].map(x=>x.value);
 if(!tokens.length){{$('ym-status').textContent='Выберите хотя бы одну компанию';return;}}
 const query=$('ym-niche').value.trim(),city=$('ym-city').value.trim();
 try{{
  const r=await fetch('/studio/products/'+id+'/market-research/sources',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{query,city,tokens}})}});
  const data=await r.json();if(!r.ok)throw Error(data.detail||'Ошибка сохранения');
  $('ym-status').textContent='Сохранено источников: '+data.saved+'. Обновите страницу для списка сохранённых источников.';
 }}catch(e){{$('ym-status').textContent=String(e.message||e);}}
}};
}})();
</script>
"""
    return HTMLResponse(studio_page_shell(
        product, active="intelligence", title="Исследование рынка",
        subtitle="Поиск компаний по отрасли, городу и категориям через Яндекс Карты.",
        body=body), headers={"Cache-Control": "no-store"})
