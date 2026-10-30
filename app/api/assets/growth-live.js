(function(){
 'use strict';
 var body=document.body;
 try{body.classList.toggle('gv-dark',localStorage.getItem('growth-live-theme')==='dark')}catch(e){}
 document.getElementById('gv-theme').addEventListener('click',function(){
  var dark=body.classList.toggle('gv-dark');
  try{localStorage.setItem('growth-live-theme',dark?'dark':'light')}catch(e){}
 });
 document.addEventListener('keydown',function(e){if(e.key==='Escape')document.querySelectorAll('.gv-mobile details[open]').forEach(function(d){d.open=false})});
 // Navigation prepares a real dispatcher request; it never sends or approves it automatically.
 var command=new URLSearchParams(location.search).get('command');
 var input=document.getElementById('dp-input');
 var commands={seo_factory:'Запусти SEO-завод для моей компании',campaign:'Помоги спланировать маркетинговую кампанию: цель, аудитория, каналы и показатели'};
 if(input&&commands[command]){input.value=commands[command];input.dispatchEvent(new Event('input',{bubbles:true}));input.focus()}
})();
