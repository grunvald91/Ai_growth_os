// Tasks responsive + theme regression using real Growth v4 and ServGo Air CSS.
import assert from "node:assert/strict";
import {createServer} from "node:http";
import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {chromium} from "playwright";

const base=resolve("../app/api/assets");
const v4=await readFile(resolve(base,"growth_os_v4_preview.html"),"utf8");
const workSource=await readFile(resolve("../app/api/content_studio_work_ui.py"),"utf8");
const workCss=workSource.match(/CSS = r"""([\s\S]*?)"""/)?.[1];
const workJs=workSource.match(/JS = r"""([\s\S]*?)"""/)?.[1];
assert.ok(workCss && workJs);
const cssFiles={
  "/tokens.css":await readFile(resolve(base,"servgo-air-tokens.css"),"utf8"),
  "/adapter.css":await readFile(resolve(base,"growth-servgo-air.css"),"utf8"),
  "/work.css":workCss,
};
const company="11111111-1111-4111-8111-111111111111";
const statuses=[
  ["queued","В очереди"],
  ["running","В работе"],
  ["review","Требует решения"],
  ["done","Готово"],
  ["error","Ошибка"],
];
const card=(state,label,index)=>'<article class="sg-resource-widget" data-sg-resource-widget="task">'+
  '<a class="sg-work-item" href="/studio/products/'+company+'/tasks/'+index+'">'+
  '<span class="sg-work-item-head"><span class="sg-work-kind">Бизнес-анализ и рекомендации</span>'+
  '<span class="sg-work-status sg-work-'+state+'">'+label+'</span></span>'+
  '<span class="sg-work-item-main"><b>Разбор вечерних записей, анализ и создание контент-плана для малого бизнеса</b>'+
  '<span class="sg-work-summary">Результат: планирование и рекомендации для владельца, без выдуманных публикаций</span></span>'+
  '<span class="sg-work-item-foot"><time datetime="2026-11-22T05:00:00+00:00">Создано 22.11.2026</time>'+
  '<span class="sg-work-open">Открыть →</span></span></a></article>';
const taskMarkup=
  '<section class="sg-surface sg-work-board" data-sg-work-board>'+
  '<div class="sg-work-toolbar"><p>Недавние задания. Активные и требующие внимания — в приоритете.</p>'+
  '<a class="btn primary" href="/studio/products/'+company+'/create">＋ Создать</a></div>'+
  '<form class="sg-work-search" action="/studio/products/'+company+'/work" role="search">'+
  '<label for="sg-work-q" class="sg-work-search-label">Поиск</label>'+
  '<input id="sg-work-q" placeholder="Название или тема" type="search">'+
  '<label for="sg-work-period" class="sg-work-search-label">Период</label>'+
  '<select id="sg-work-period"><option>За всё время</option></select>'+
  '<label for="sg-work-kind" class="sg-work-search-label">Вид</label>'+
  '<select id="sg-work-kind"><option>Бизнес-анализ</option></select>'+
  '<button class="btn" type="button">Найти</button></form>'+
  '<nav class="sg-work-tabs" aria-label="Фильтр заданий">'+
  '<a class="active" aria-current="page" href="#">Внимание <span class="sg-work-tab-count">9</span></a>'+
  '<a href="#">В работе <span class="sg-work-tab-count">15</span></a>'+
  '<a href="#">Готово <span class="sg-work-tab-count">32</span></a>'+
  '<a href="#">Все <span class="sg-work-tab-count">105</span></a>'+
  '<a href="#">Ошибки <span class="sg-work-tab-count">2</span></a></nav>'+
  '<button type="button" class="sg-work-update" data-sg-work-update hidden>Есть обновления. Показать</button>'+
  '<div class="sg-work-list">'+statuses.map((s,i)=>card(s[0],s[1],i)).join("")+'</div>'+
  '<nav class="sg-work-pagination" aria-label="Страницы заданий">'+
  '<span class="sg-work-page-summary">Показано 1–5 из 105</span>'+
  '<div class="sg-work-page-links"><a href="#">← Назад</a><span>Страница 1 из 21</span>'+
  '<a href="#">Вперёд →</a></div></nav></section>';

const notice='<details class="sg-notifications" data-sg-notifications data-feed-url="/studio/products/'+company+'/activity.json">'+
  '<summary aria-label="Уведомления"><span aria-hidden="true">🔔</span></summary>'+
  '<div class="sg-notify-popover"><strong>Требует внимания</strong>'+
  '<button class="sg-notify-read" type="button">Прочитать все</button>'+
  '<div data-sg-notify-list></div>'+
  '<a class="sg-notify-all" href="#">Все задания</a></div></details>';
const rail='<aside class="rail" id="rail">'+
  '<div class="rail-head"><h2>Задание</h2><span>Быстрый просмотр</span></div>'+
  '<section class="sg-work-preview" data-sg-work-preview><p data-sg-preview-hint>Выберите задание</p>'+
  '<span data-sg-preview-announcement role="status" aria-live="polite"></span>'+
  '<div id="sgWorkPreviewDetail" data-sg-preview-detail hidden>'+
  '<span data-sg-preview-kind></span><h3 data-sg-preview-title></h3>'+
  '<span data-sg-preview-status></span><p data-sg-preview-summary></p>'+
  '<p data-sg-preview-date></p><a class="btn blue" data-sg-preview-link href="#">Открыть задание</a>'+
  '</div></section></aside>';

function render(theme){
  let h=v4.replace(/<script\b[^>]*>[\s\S]*?<\/script>/g,"");
  h=h.replace('<html lang="ru">','<html lang="ru" data-theme="'+theme+'">');
  h=h.replace('<body>','<body data-growth-design="v4">');
  h=h.replace(/<span class="chip"[^>]*>Демо · API не подключены<\/span>/,'');
  h=h.replace(/<button class="cmd-search" id="openSearch">[\s\S]*?<\/button>/,'');
  h=h.replace(/<button class="icon-btn" id="themeTop"[\s\S]*?<\/button>/,'');
  h=h.replace(/<button class="icon-btn" id="notifyBtn"[\s\S]*?<\/button>/,notice);
  h=h.replace('<main id="main" class="main" tabindex="-1"></main>',
    '<main id="main" class="main" tabindex="-1"><div class="route-content">'+taskMarkup+'</div></main>');
  h=h.replace('<aside class="rail" id="rail"></aside>',rail);
  h=h.replace('<nav class="mobile-nav" id="mobileNav"></nav>',
    '<nav class="mobile-nav" id="mobileNav">'+
    ['Главная','Создать','Задания','SEO','Материалы'].map(t=>'<button>'+t+'</button>').join("")+'</nav>');
  h=h.replace('</head>','<link rel="stylesheet" href="/tokens.css">'+
    '<link rel="stylesheet" href="/work.css">'+
    '<link rel="stylesheet" href="/adapter.css">'+
    '<style>@media(max-width:780px){.biz-switch{min-height:38px;max-width:min(33vw,165px)}'+
    '.biz-switch .avatar{display:none}.biz-switch>span:not(.avatar){display:block;min-width:0;max-width:100%}'+
    '.biz-switch strong{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}'+
    '.biz-switch small{display:none}.biz-switch .icon{display:block}}</style></head>');
  return h.replace('</body>','<script src="/work.js"></script></body>');
}
const server=createServer((req,res)=>{
  const path=(req.url||"/").split("?")[0];
  const reply=(type,body)=>{
    res.writeHead(200,{"Content-Type":type,"Cache-Control":"no-store"});
    res.end(body);
  };
  if(Object.hasOwn(cssFiles,path))return reply("text/css",cssFiles[path]);
  if(path==="/work.js")return reply("application/javascript",workJs);
  if(path.endsWith("/activity.json"))return reply("application/json",'{"jobs":[],"notices":[]}');
  return reply("text/html",render(new URL(req.url||"/","http://test").searchParams.get("theme")||"light"));
});
await new Promise((resolve,reject)=>{
  server.once("error",reject);server.listen(0,"127.0.0.1",resolve);
});
const browser=await chromium.launch({
  headless:true,executablePath:process.env.SERVGO_CHROME_PATH||"/usr/bin/google-chrome"
});
const url="http://127.0.0.1:"+server.address().port+"/studio/products/"+company+"/work";

function luminance(rgb){
  const values=rgb.match(/\d*\.?\d+/g)?.map(Number);
  assert.ok(values && values.length>=3,"expected computed RGB: "+rgb);
  // Chrome can preserve CSS Color 4 values as color(srgb .8 .9 1)
  // instead of rgb(204, 230, 255). CSS color() channels are 0..1.
  const channels=values.slice(0,3).map(x=>rgb.startsWith("color(srgb")?x:x/255);
  assert.ok(channels.every(x=>x>=0&&x<=1),"invalid RGB channels: "+rgb);
  const c=channels.map(x=>x<=0.04045?x/12.92:((x+0.055)/1.055)**2.4);
  return 0.2126*c[0]+0.7152*c[1]+0.0722*c[2];
}
function contrast(a,b){const x=luminance(a),y=luminance(b);return (Math.max(x,y)+0.05)/(Math.min(x,y)+0.05);}
try{
  for(const theme of ["light","dark"]){
    for(const width of [320,360,390,768,1024,1081,1280]){
      const page=await browser.newPage({viewport:{width,height:780}});
      const errors=[];
      page.on("pageerror",e=>errors.push(e.message));
      await page.goto(url+"?theme="+theme);
      const layout=await page.evaluate(()=>{
        const rect=sel=>{const e=document.querySelector(sel);if(!e)return null;
          const r=e.getBoundingClientRect();return {x:r.x,right:r.right,width:r.width,height:r.height};};
        const css=sel=>{const el=document.querySelector(sel);const st=getComputedStyle(el);
          return {color:st.color,background:st.backgroundColor,minHeight:parseFloat(st.minHeight)||0};};
        return {
          width:innerWidth,pageWidth:document.documentElement.scrollWidth,
          work:rect(".sg-work-board"),toolbar:rect(".sg-work-toolbar"),
          button:rect(".sg-work-toolbar .btn"),period:rect("#sg-work-period"),
          kind:rect("#sg-work-kind"),search:rect("#sg-work-q"),
          card:rect(".sg-work-item"),header:rect(".topbar"),
          logo:rect(".topbar .crumbs"),actions:rect(".topbar .top-actions"),
          tabScroll:document.querySelector(".sg-work-tabs").scrollWidth >
            document.querySelector(".sg-work-tabs").clientWidth,
          pills:[...document.querySelectorAll(".sg-work-item-head .sg-work-status")].map(el=>{
            const s=getComputedStyle(el);return {color:s.color,background:s.backgroundColor};
          }),
          active:css(".sg-work-tabs a.active"),
          open:css(".sg-work-open"),
          summary:css(".sg-work-summary"),
          date:css(".sg-work-item-foot"),
          cardColor:css(".sg-work-item"),
        };
      });
      assert.ok(layout.pageWidth<=width+1,
        "horizontal overflow "+theme+" "+width+": "+JSON.stringify(layout));
      for(const key of ["button","card","search","period","kind"]){
        assert.ok(layout[key] && layout[key].x>=-1 && layout[key].right<=width+1,
          "clipped "+key+" at "+theme+" "+width+": "+JSON.stringify(layout[key]));
      }
      assert.ok(layout.button.height>=44 && layout.search.height>=44,
        "touch targets too small at "+width);
      for(const [index,pill] of layout.pills.entries()){
        assert.ok(contrast(pill.color,pill.background)>=4.5,
          "low contrast status "+index+" "+theme+" "+width+": "+
          contrast(pill.color,pill.background).toFixed(2)+
          " "+JSON.stringify(pill));
      }
      const bg=layout.cardColor.background==="rgba(0, 0, 0, 0)"?
        (theme==="dark"?"rgb(23, 34, 55)":"rgb(255, 255, 255)"):layout.cardColor.background;
      assert.ok(contrast(layout.summary.color,bg)>=4.5,
        "low contrast summary "+theme+" "+width);
      assert.ok(contrast(layout.date.color,bg)>=4.5,
        "low contrast date "+theme+" "+width);
      assert.ok(contrast(layout.open.color,bg)>=4.5,
        "low contrast card action "+theme+" "+width);
      assert.ok(contrast(layout.active.color,layout.active.background)>=4.5,
        "low contrast active filter "+theme+" "+width);
      if(width<=780){
        assert.ok(layout.logo.right<=layout.actions.x+1,
          "floating header controls overlap "+theme+" "+width+
          ": "+JSON.stringify({logo:layout.logo,actions:layout.actions}));
      }
      if(width===320){
        assert.ok(layout.kind.width>200 && layout.period.width>200,
          "320px should use single-column full-width filters");
      }
      if(width<=1080){
        assert.equal(await page.locator("[data-sg-task-peek]").count(),0);
      }
      if(width===320||width===1280){
        await page.screenshot({path:resolve("../studio-resource-widgets-tasks-"+theme+"-"+width+".png"),
          fullPage:true});
      }
      // The floating bell's popup must not revert to light-only action ink.
      await page.locator(".top-actions .sg-notifications > summary").click();
      const notificationContrast=await page.evaluate(()=>{
        const bg=getComputedStyle(document.querySelector(".sg-notify-popover")).backgroundColor;
        return ["sg-notify-all","sg-notify-read"].map(cl=>({
          label:cl,ink:getComputedStyle(document.querySelector("."+cl)).color,bg,
          height:document.querySelector("."+cl).getBoundingClientRect().height
        }));
      });
      for(const n of notificationContrast){
        assert.ok(contrast(n.ink,n.bg)>=4.5,
          "floating notification contrast "+theme+" "+width+" "+JSON.stringify(n));
      }
      assert.ok(notificationContrast[1].height>=44,"notification touch target");
      assert.deepEqual(errors,[],"no page exceptions "+theme+" "+width);
      await page.close();
    }
  }
}finally{
  await browser.close();
  await new Promise(resolve=>server.close(resolve));
}
console.log("Tasks v4 theme and layout: 320/360/390/768/1024/1081/1280 light and dark OK");
