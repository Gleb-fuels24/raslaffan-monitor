"""
site_builder.py — генератор статического сайта для GitHub Pages.

Берёт результат работы бота и собирает папку docs/, которую GitHub
публикует как обычный сайт. Внутри:

    docs/index.html   дашборд, он же лид-магнит
    docs/data.json    те же данные машиночитаемо (для клиентов и API)
    docs/.nojekyll    служебный файл, чтобы GitHub не трогал вёрстку
    docs/img/         картинки зон со снимков

Почему именно так. GitHub Pages умеет отдавать только готовые файлы, он
не может выполнять Python. Поэтому бот считает всё у вас на машине (или
на сервере по расписанию), складывает результат в docs/ и делает git push.
Сайт обновляется сам в момент пуша.

Про закрытую часть. Первые несколько линий показаны открыто, остальные
закрыты плашкой с призывом оставить почту — это и есть лид-магнит.
Закрытые значения в HTML НЕ попадают вовсе: вместо них подставляются
точки. Если бы мы просто размывали их через CSS, любой желающий увидел
бы цифры в исходном коде страницы.
"""

import json
import os
import shutil
import datetime as dt

import config
import fusion

# ---------------------------------------------------------------------------
# Палитра. Статусные цвета фиксированы и всегда идут вместе со значком и
# подписью — цвет никогда не несёт смысл в одиночку (это требование
# доступности: примерно каждый двенадцатый мужчина не различает часть
# оттенков).
# ---------------------------------------------------------------------------
STATUS_COLOR = {
    "good": "var(--status-good)",
    "warning": "var(--status-warning)",
    "serious": "var(--status-serious)",
    "critical": "var(--status-critical)",
    "none": "var(--ink-muted)",
}

TRAIN_STATUS = {
    "PRODUCING":  {"label": "producing",     "icon": "▲", "status": "good"},
    "REBUILDING": {"label": "works under way","icon": "◆", "status": "serious"},
    "DESTROYED":  {"label": "destroyed",     "icon": "✕", "status": "critical"},
    "IDLE":       {"label": "idle, intact",  "icon": "■", "status": "warning"},
    "NO_DATA":    {"label": "no data",       "icon": "–", "status": "none"},
    "ERROR":      {"label": "error",         "icon": "!", "status": "none"},
}

VESSEL_LABEL = {
    "gas_carrier": "LNG carrier",
    "product_tanker": "product tanker",
    "cargo": "dry cargo",
    "tug": "tug",
    "dredger": "dredger",
    "support": "support",
    "unknown": "unidentified",
}

CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{
  color-scheme:light;
  --plane:#f9f9f7; --surface:#fcfcfb;
  --ink:#0b0b0b; --ink-2:#52514e; --ink-muted:#898781;
  --grid:#e1e0d9; --axis:#c3c2b7; --ring:rgba(11,11,11,.10);
  --status-good:#0ca30c; --status-warning:#fab219;
  --status-serious:#ec835a; --status-critical:#d03b3b;
  --seq-400:#3987e5; --seq-450:#2a78d6; --seq-250:#86b6ef;
  --accent:#2a78d6;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    color-scheme:dark;
    --plane:#0d0d0d; --surface:#1a1a19;
    --ink:#f0efec; --ink-2:#c3c2b7; --ink-muted:#898781;
    --grid:#2c2c2a; --axis:#383835; --ring:rgba(255,255,255,.10);
    --seq-400:#3987e5; --seq-450:#2a78d6; --seq-250:#184f95;
    --accent:#3987e5;
  }
}
:root[data-theme="dark"]{
  color-scheme:dark;
  --plane:#0d0d0d; --surface:#1a1a19;
  --ink:#f0efec; --ink-2:#c3c2b7; --ink-muted:#898781;
  --grid:#2c2c2a; --axis:#383835; --ring:rgba(255,255,255,.10);
  --seq-400:#3987e5; --seq-450:#2a78d6; --seq-250:#184f95;
  --accent:#3987e5;
}
body{
  margin:0; background:var(--plane); color:var(--ink);
  font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;
  -webkit-text-size-adjust:100%;
}
.wrap{max-width:1040px; margin:0 auto; padding:0 16px 72px}
header{padding:28px 0 8px}
.brand{display:flex; align-items:baseline; gap:10px; flex-wrap:wrap}
.brand b{font-size:15px; letter-spacing:.02em}
.brand span{color:var(--ink-muted); font-size:13px}
h1{font-size:clamp(24px,4.2vw,36px); line-height:1.15; margin:18px 0 6px; font-weight:650}
.sub{color:var(--ink-2); margin:0 0 4px; max-width:60ch}
.stamp{color:var(--ink-muted); font-size:13px; margin-top:10px}

.card{
  background:var(--surface); border:1px solid var(--ring);
  border-radius:12px; padding:20px; margin:16px 0;
}
h2{font-size:17px; margin:0 0 4px; font-weight:620}
.hint{color:var(--ink-muted); font-size:13px; margin:0 0 16px; max-width:72ch}

/* шапка-вердикт */
.verdict{display:flex; gap:24px; flex-wrap:wrap; align-items:flex-start}
.hero{flex:1 1 220px; min-width:200px}
.hero .n{font-size:clamp(40px,8vw,60px); line-height:1; font-weight:680; letter-spacing:-.02em}
.hero .n small{font-size:.42em; font-weight:500; color:var(--ink-2); margin-left:4px}
.hero .lbl{color:var(--ink-2); font-size:13px; margin-top:6px}
.chip{
  display:inline-flex; align-items:center; gap:8px; padding:7px 13px;
  border-radius:999px; border:1px solid var(--ring);
  font-weight:600; font-size:14px; background:var(--plane);
}
.chip .dot{width:9px;height:9px;border-radius:50%;flex:0 0 auto}
.vnote{flex:2 1 340px; color:var(--ink-2); font-size:14px}
.caveat{
  margin-top:12px; padding:11px 13px; border-radius:8px;
  border-left:3px solid var(--status-warning); background:var(--plane);
  color:var(--ink-2); font-size:13px;
}

/* матрица сигналов */
.sig{display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px}
.sigbox{border:1px solid var(--ring); border-radius:9px; padding:13px; background:var(--plane)}
.sigbox .k{color:var(--ink-muted); font-size:12px; text-transform:uppercase; letter-spacing:.05em}
.sigbox .v{font-size:25px; font-weight:650; margin-top:5px; font-variant-numeric:tabular-nums}
.sigbox .d{color:var(--ink-2); font-size:12.5px; margin-top:3px}

table{width:100%; border-collapse:collapse; font-size:14px}
th{
  text-align:left; font-weight:600; color:var(--ink-2); font-size:12px;
  text-transform:uppercase; letter-spacing:.05em;
  padding:0 16px 8px 0; border-bottom:1px solid var(--grid);
}
td{padding:10px 16px 10px 0; border-bottom:1px solid var(--grid); vertical-align:top}
/* Числовые колонки выравниваются вправо, но отступ справа сохраняется:
   без него число слипается с соседней колонкой. */
td.num,th.num{text-align:right; font-variant-numeric:tabular-nums}
td:last-child,th:last-child{padding-right:0}
tr:last-child td{border-bottom:none}
.st{display:inline-flex; align-items:center; gap:7px; font-weight:560; white-space:nowrap}
.st .ic{font-size:11px; line-height:1}
.ev{color:var(--ink-muted); font-size:12.5px; margin-top:4px}
.conf{font-size:12px; color:var(--ink-muted); white-space:nowrap}

/* закрытая часть */
.locked td{color:var(--ink-muted)}
.locked .mask{letter-spacing:.18em; user-select:none}
/* Размытая ячейка маршрута захода. Размывается МАСКА из точек:
   прятать текст через blur нельзя, он остаётся в исходном коде. */
.blur{
  filter:blur(3.4px); color:var(--ink-2); letter-spacing:.1em;
  user-select:none; white-space:nowrap;
}
.gate{
  margin-top:14px; padding:18px; border-radius:10px; text-align:center;
  border:1px dashed var(--axis); background:var(--plane);
}
.gate b{display:block; font-size:15px; margin-bottom:4px}
.gate p{color:var(--ink-2); font-size:13.5px; margin:0 auto 14px; max-width:46ch}

/* график */
.chart{display:flex; align-items:flex-end; gap:2px; height:150px; margin-top:6px}
.bar{
  flex:1 1 0; min-width:6px; background:var(--seq-450);
  border-radius:4px 4px 0 0; position:relative; cursor:default;
  transition:background .12s;
}
.bar:hover{background:var(--seq-400)}
/* Сутки без измерения: тонкая штриховка у самой оси. Видно, что день
   есть, но данных за него нет — это не ноль. */
.bar.gap{
  height:6px; background:transparent; min-width:3px;
  border-bottom:2px dotted var(--axis); border-radius:0;
}
.bar.gap:hover{background:transparent}
.bar:hover::after{
  content:attr(data-tip); position:absolute; bottom:calc(100% + 7px); left:50%;
  transform:translateX(-50%); white-space:nowrap; z-index:5;
  background:var(--ink); color:var(--surface); font-size:12px;
  padding:5px 9px; border-radius:6px; font-variant-numeric:tabular-nums;
}
.axis{
  display:flex; justify-content:space-between; color:var(--ink-muted);
  font-size:11.5px; margin-top:7px; border-top:1px solid var(--axis); padding-top:6px;
}

/* форма */
/* Строка связи вместо формы. Ссылки крупнее обычного текста и с
   запасом по вертикали: по ним будут попадать пальцем на телефоне. */
.cta-line{
  margin:0; text-align:center; line-height:2;
  font-size:1.02rem; color:var(--ink);
}
.cta-line a{
  font-weight:600; white-space:nowrap; padding:2px 0;
}
.btn{
  padding:11px 20px; font:inherit; font-weight:600; cursor:pointer;
  border:none; border-radius:8px; background:var(--accent); color:#fff;
  text-decoration:none; display:inline-block;
}
.btn:hover{filter:brightness(1.08)}
.btn.ghost{background:transparent; color:var(--accent); border:1px solid var(--accent)}

/* карусель снимков */
/* ролик-объяснитель */
.vid{width:100%; max-width:860px; margin:0 auto; display:block;
  border-radius:10px; border:1px solid var(--axis); background:#000}

.car{position:relative}
.car-track{display:flex; overflow-x:auto; scroll-snap-type:x mandatory;
  gap:14px; scroll-behavior:smooth; padding-bottom:4px;
  scrollbar-width:thin}
.car-slide{flex:0 0 100%; scroll-snap-align:start; min-width:0}
.car-slide img{width:100%; height:auto; display:block; border-radius:9px;
  border:1px solid var(--ring); background:var(--plane)}
.car-noimg{width:100%; aspect-ratio:3/2; border-radius:9px;
  border:1px dashed var(--axis); background:var(--plane);
  display:flex; align-items:center; justify-content:center;
  color:var(--ink-muted); font-size:13.5px; text-align:center; padding:20px}
.car-cap{display:flex; flex-wrap:wrap; gap:6px 16px; align-items:baseline;
  margin-top:10px}
.car-cap b{font-size:15px}
.car-cap .d{color:var(--ink-muted); font-size:13px;
  font-variant-numeric:tabular-nums}
.car-stat{display:flex; gap:18px; flex-wrap:wrap; margin-top:8px}
.car-stat div{font-size:13px; color:var(--ink-2)}
.car-stat b{font-size:19px; color:var(--ink); display:block;
  font-variant-numeric:tabular-nums}
.car-nav{display:flex; gap:8px; align-items:center; margin-top:14px;
  flex-wrap:wrap}
.car-btn{border:1px solid var(--axis); background:var(--surface);
  color:var(--ink); border-radius:8px; padding:7px 14px; cursor:pointer;
  font:inherit; font-size:13px}
.car-btn:hover{border-color:var(--accent); color:var(--accent)}
.car-btn:focus-visible{outline:2px solid var(--accent); outline-offset:2px}
.car-dots{display:flex; gap:6px; margin-left:auto}
.car-dot{width:8px; height:8px; border-radius:50%; border:none; padding:0;
  background:var(--axis); cursor:pointer}
.car-dot[aria-current="true"]{background:var(--accent)}

.hbar{height:9px;border-radius:5px;background:var(--grid);overflow:hidden}.hbar i{display:block;height:100%;border-radius:5px}.hna{color:var(--ink-muted);font-size:12.5px}.meth{columns:2 260px; column-gap:28px; font-size:13.5px; color:var(--ink-2)}
.meth p{margin:0 0 11px; break-inside:avoid}
.meth b{color:var(--ink)}
footer{
  margin-top:30px; padding-top:18px; border-top:1px solid var(--grid);
  color:var(--ink-muted); font-size:13px;
}
footer a{color:var(--accent)}
.toggle{
  position:absolute; top:16px; right:16px; background:var(--surface);
  border:1px solid var(--ring); color:var(--ink-2); border-radius:8px;
  padding:7px 11px; font:inherit; font-size:13px; cursor:pointer;
}
@media (max-width:560px){
  .meth{columns:1}
  .hide-s{display:none}
}
"""

ANALYTICS_JS = """
/* Счётчик и поведение посетителя.

   Страница лежит на GitHub Pages, а он отдаёт файлы и ничего не
   записывает. Поэтому считает сама страница и отправляет результат
   на ваш сборщик.

   Отправок ровно две на визит. Первая сразу: она нужна, чтобы
   посещение не потерялось, если человек закроет вкладку через
   секунду. Вторая при уходе: в ней поведение — сколько секунд, до
   какого места долистал, какие разделы читал и на каком ушёл.

   Уход ловится по pagehide и по скрытию вкладки, а не по unload:
   на телефонах unload часто не срабатывает вовсе. sendBeacon
   отправляет данные, даже когда вкладка уже закрывается.

   Без cookie. Идентификатор визита случайный и живёт в
   sessionStorage, то есть умирает вместе с вкладкой: узнать по нему
   человека нельзя, он нужен только чтобы склеить две отправки
   одного визита. */
(function () {
  var EP = "__ENDPOINT__";
  if (!EP) { return; }
  var SITE = "__SITE__";
  var ORG_URL = "__ORG_URL__";

  function rnd() {
    return Math.random().toString(36).slice(2) + Date.now().toString(36);
  }
  var sid;
  try {
    sid = sessionStorage.getItem("rl-sid");
    if (!sid) { sid = rnd(); sessionStorage.setItem("rl-sid", sid); }
  } catch (e) { sid = rnd(); }

  var t0 = Date.now();
  var maxScroll = 0;
  var clicks = [];
  var sections = [];
  var current = null;
  var since = 0;
  var sent = false;

  /* Разделы берём по заголовкам: имя раздела — это его h2. */
  var cards = document.querySelectorAll("section.card");
  for (var i = 0; i < cards.length; i++) {
    var h = cards[i].querySelector("h2");
    sections.push({ el: cards[i], name: h ? h.textContent.trim().slice(0, 60)
                                          : "section " + (i + 1),
                    ms: 0, seen: false });
  }

  function switchTo(idx) {
    var now = Date.now();
    if (current !== null && since) { sections[current].ms += now - since; }
    current = idx;
    since = now;
    if (idx !== null) { sections[idx].seen = true; }
  }

  /* Какой раздел человек читает сейчас — определяем по тому, что
     попадает в ЦЕНТР экрана, а не по тому, что занимает больше места.
     Разница существенная: высокий раздел со снимком иначе забирал бы
     себе время коротких разделов, которые человек на самом деле
     читал. Опрос раз в полсекунды: дёшево и точнее, чем ловить
     пересечения границ. */
  function atCentre() {
    var mid = window.innerHeight / 2;
    var best = null, bestDist = 1e9;
    for (var i = 0; i < sections.length; i++) {
      var r = sections[i].el.getBoundingClientRect();
      if (r.bottom < 0 || r.top > window.innerHeight) { continue; }
      var dist = (r.top <= mid && r.bottom >= mid)
        ? 0 : Math.min(Math.abs(r.top - mid), Math.abs(r.bottom - mid));
      if (dist < bestDist) { bestDist = dist; best = i; }
    }
    return best;
  }

  setInterval(function () {
    if (document.visibilityState === "hidden") { return; }
    var idx = atCentre();
    if (idx !== null && idx !== current) { switchTo(idx); }
  }, 500);

  window.addEventListener("scroll", function () {
    var h = document.documentElement;
    var total = h.scrollHeight - h.clientHeight;
    if (total > 0) {
      var pct = Math.round(((h.scrollTop || document.body.scrollTop) / total) * 100);
      if (pct > maxScroll) { maxScroll = Math.min(100, pct); }
    }
  }, { passive: true });

  /* Клики по тому, что означает интерес: форма, звонок, почта. */
  document.addEventListener("click", function (e) {
    var a = e.target.closest ? e.target.closest("a,button") : null;
    if (!a) { return; }
    var what = (a.getAttribute("href") || "") + " " + (a.textContent || "");
    if (/calendly/i.test(what)) { clicks.push("book a call"); }
    else if (/mailto:/i.test(what)) { clicks.push("email"); }
    else if (/submit/i.test(a.type || "")) { clicks.push("form submit"); }
  }, true);

  function params() {
    var out = {};
    try {
      var q = new URLSearchParams(location.search);
      ["utm_source", "utm_medium", "utm_campaign", "utm_content"].forEach(
        function (k) { if (q.get(k)) { out[k] = q.get(k).slice(0, 60); } });
    } catch (e) { /* старый браузер — просто без меток */ }
    return out;
  }

  function post(payload) {
    payload.site = SITE;
    payload.sid = sid;
    var body = JSON.stringify(payload);
    try {
      /* text/plain — чтобы браузер не слал предварительный запрос
         OPTIONS: Apps Script на него не отвечает. */
      var blob = new Blob([body], { type: "text/plain;charset=UTF-8" });
      if (navigator.sendBeacon && navigator.sendBeacon(EP, blob)) { return; }
    } catch (e) { /* ниже запасной путь */ }
    try {
      fetch(EP, { method: "POST", body: body, keepalive: true,
                  headers: { "Content-Type": "text/plain;charset=UTF-8" },
                  mode: "no-cors" });
    } catch (e) { /* не вышло — визит просто не попадёт в отчёт */ }
  }

  var view = {
    event: "view",
    path: location.pathname,
    ref: document.referrer ? document.referrer.slice(0, 200) : "",
    lang: (navigator.language || "").slice(0, 12),
    screen: (window.screen ? screen.width + "x" + screen.height : ""),
    mobile: /Mobi|Android/i.test(navigator.userAgent) ? 1 : 0,
    tz: ""
  };
  try { view.tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; }
  catch (e) { /* не критично */ }
  var utm = params();
  for (var key in utm) { if (utm.hasOwnProperty(key)) { view[key] = utm[key]; } }

  /* Организация по сети. Нужна, чтобы отличить визит из сети
     подрядчика от визита с домашнего интернета. Работает не всегда:
     у человека с телефона видно оператора связи, а не работодателя.
     Если адрес справочника не задан, шаг пропускается. */
  function go() {
    post(view);
    switchTo(0);
  }
  if (ORG_URL) {
    try {
      fetch(ORG_URL).then(function (r) { return r.json(); }).then(function (d) {
        /* Разные справочники называют поля по-разному, и даже у
           одного ipinfo два формата: старый отдаёт org строкой
           "AS8075 Fluor Corp", новый Lite — отдельно asn и as_name.
           Берём то, что есть, и склеиваем. */
        var org = d.org || d.isp || "";
        if (!org && (d.as_name || d.asn)) {
          org = ((d.asn || "") + " " + (d.as_name || "")).trim();
        }
        view.org = org.toString().slice(0, 80);
        view.country = (d.country || d.country_name || d.countryName || "")
          .toString().slice(0, 40);
        view.city = (d.city || d.cityName || "").toString().slice(0, 40);
        go();
      }).catch(go);
      setTimeout(function () { if (!view.sentOnce) { view.sentOnce = 1; } }, 1500);
    } catch (e) { go(); }
  } else { go(); }

  function finish() {
    if (sent) { return; }
    sent = true;
    switchTo(current);
    var read = [];
    for (var i2 = 0; i2 < sections.length; i2++) {
      if (sections[i2].ms > 900) {
        read.push(sections[i2].name + ":" + Math.round(sections[i2].ms / 1000));
      }
    }
    var last = current !== null ? sections[current].name : "";
    post({
      event: "exit",
      seconds: Math.round((Date.now() - t0) / 1000),
      scroll: maxScroll,
      exit_section: last,
      read: read.join(" | ").slice(0, 900),
      clicks: clicks.join(",").slice(0, 120)
    });
  }

  window.addEventListener("pagehide", finish);
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") { finish(); }
  });
})();
"""


JS = """
(function(){
  var k='rl-theme', b=document.getElementById('tg');
  function cur(){
    var s=localStorage.getItem(k);
    if(s) return s;
    return matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light';
  }
  function put(t){
    document.documentElement.setAttribute('data-theme',t);
    try{localStorage.setItem(k,t)}catch(e){}
    b.textContent = t==='dark' ? 'Light theme' : 'Dark theme';
  }
  try{ put(cur()); }catch(e){}
  b.addEventListener('click',function(){
    put(document.documentElement.getAttribute('data-theme')==='dark'?'light':'dark');
  });
})();

/* Карусель снимков. Прокрутка нативная, скрипт только двигает её
   кнопками и подсвечивает точку. Без скрипта карусель всё равно
   листается пальцем и колесом — ничего не ломается. */
(function(){
  var t = document.getElementById('cartrack');
  if(!t) return;
  var slides = t.querySelectorAll('.car-slide');
  var dots = document.querySelectorAll('.car-dot');
  function cur(){ var w = t.clientWidth || 1; return Math.round(t.scrollLeft / w); }
  function go(i){
    i = Math.max(0, Math.min(slides.length - 1, i));
    if(slides[i]) t.scrollTo({left: slides[i].offsetLeft - t.offsetLeft,
                              behavior:'smooth'});
  }
  function mark(){
    var c = cur();
    dots.forEach(function(d,i){
      if(i===c) d.setAttribute('aria-current','true');
      else d.removeAttribute('aria-current');
    });
  }
  document.querySelectorAll('[data-step]').forEach(function(b){
    b.addEventListener('click', function(){ go(cur() + parseInt(b.dataset.step,10)); });
  });
  dots.forEach(function(d){
    d.addEventListener('click', function(){ go(parseInt(d.dataset.go,10)); });
  });
  t.addEventListener('scroll', function(){
    clearTimeout(t._tm); t._tm = setTimeout(mark, 80);
  });
  t.addEventListener('keydown', function(e){
    if(e.key==='ArrowRight'){ e.preventDefault(); go(cur()+1); }
    if(e.key==='ArrowLeft'){ e.preventDefault(); go(cur()-1); }
  });
  mark();
})();
"""


def _esc(s):
    """Экранирует текст, чтобы он не сломал вёрстку."""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _analytics_js() -> str:
    """
    Скрипт счётчика с подставленными настройками.

    Пустая строка, если сборщик не задан: тогда на странице нет ни
    строчки слежения. Это важно — витрина должна работать и без
    аналитики, а посетителю нечего грузить впустую.
    """
    ep = (getattr(config, "ANALYTICS_ENDPOINT", "") or "").strip()
    if not ep:
        return ""

    # Ловим адрес-пример, не заменённый на настоящий. Без этой
    # проверки сайт публикуется со счётчиком, который никуда не
    # пишет, и обнаруживается это через неделю по пустой таблице.
    bad = None
    if "..." in ep or "…" in ep:
        bad = "в адресе остались три точки — это сокращение из примера"
    elif "ВАШ" in ep.upper() or "YOUR" in ep.upper():
        bad = "в адресе осталось слово-заглушка"
    elif not ep.startswith("https://"):
        bad = "адрес должен начинаться с https://"
    elif "script.google.com" in ep and not ep.endswith("/exec"):
        bad = ("адрес веб-приложения Apps Script заканчивается на /exec; "
               "если у вас /dev — это черновик, он работает только для вас")

    if bad:
        print(f"[!] Счётчик посещений НЕ включён: {bad}.")
        print(f"    ANALYTICS_ENDPOINT = {ep}")
        print("    Нужен адрес вашего развёртывания из script.google.com:")
        print("    длинный, без многоточий, заканчивается на /exec.")
        return ""

    return (ANALYTICS_JS
            .replace("__ENDPOINT__", ep)
            .replace("__SITE__", getattr(config, "ANALYTICS_SITE_ID", "site"))
            .replace("__ORG_URL__",
                     getattr(config, "ANALYTICS_ORG_LOOKUP", "") or ""))


def _privacy_line() -> str:
    """
    Строка о сборе данных в подвале.

    Нужна не для красоты: мы считаем поведение посетителей, и об этом
    полагается сказать. Формулировка описывает ровно то, что
    происходит, без общих слов про «улучшение качества сервиса».
    """
    if not getattr(config, "ANALYTICS_ENDPOINT", ""):
        return ""
    org = (" The network operator your connection belongs to is looked up "
           "to tell a corporate visit from a home one."
           if getattr(config, "ANALYTICS_ORG_LOOKUP", "") else "")
    return ('<p>This page counts visits and measures which sections are '
            'read, how far the page is scrolled and where a visit ends. '
            'No cookies, no advertising networks, and nothing that '
            f'identifies you personally.{org}</p>')


def _strip_comments(code: str) -> str:
    """
    Убирает блочные комментарии из CSS и JS перед выдачей на страницу.

    Комментарии в исходниках написаны по-русски и нужны для
    сопровождения кода. На странице они не нужны: страница английская,
    а исходный код любой желающий открывает по Ctrl+U. В файле
    site_builder.py комментарии остаются на месте — вырезается только
    копия, которая уходит в HTML.

    Комментариев вида // в скрипте нет намеренно: такой шаблон
    встречается внутри ссылок (https://), и вырезать его регулярным
    выражением опасно.
    """
    import re as _re
    return _re.sub(r"/\*.*?\*/", "", code, flags=_re.S)


def _chip(status, text):
    color = STATUS_COLOR.get(status, STATUS_COLOR["none"])
    return (f'<span class="chip"><span class="dot" style="background:{color}">'
            f'</span>{_esc(text)}</span>')


# ---------------------------------------------------------------------------
# Сборка блоков
# ---------------------------------------------------------------------------
def _signals_block(cx):
    sig = cx["signals"]
    boxes = []

    thermal = sig.get("thermal")
    boxes.append(("Thermal activity",
                  "yes" if thermal else "no",
                  f"hot trains: {sig.get('hot_trains', 0)}"
                  if thermal else "no flaring detected"))

    # «Погрузок нет» и «про погрузки ничего не известно» — разные
    # утверждения, и прочерк читался как первое. Погрузка считается по
    # AIS: судно пришло, постояло у причала нужное время, ушло. Если
    # AIS по региону не идёт, число не ноль, а неизвестно — и плитка
    # обязана это сказать словом, иначе она спорит с плиткой
    # «LNG carrier alongside», где судно у причала видно на снимке.
    lng = sig.get("lng_loadings")
    pct = sig.get("export_pct_of_norm")
    img_lng_n = sig.get("lng_alongside_img")
    if lng is None:
        hint = "AIS not received, so loadings cannot be counted"
        if img_lng_n:
            hint += " — a carrier is alongside on the image, but whether " \
                    "it loaded is not visible from one frame"
        boxes.append(("LNG loadings", "unknown", hint))
    else:
        boxes.append(("LNG loadings", str(lng),
                      f"{pct}% of the pre-conflict norm" if pct is not None
                      else "over the observation window"))

    liq = sig.get("liquid_loadings")
    boxes.append(("Liquid products",
                  "unknown" if liq is None else str(liq),
                  "AIS not received" if liq is None
                  else "condensate, LPG, GTL"))

    dry = sig.get("drycargo_calls")
    proj = sig.get("project_cargo_calls") or 0
    img_dry = sig.get("drycargo_alongside_img")
    if dry is None and img_dry is not None:
        # Без AIS направление груза неизвестно, и плитка обязана это
        # говорить: судно у сухогрузного причала может и грузить серу.
        filling = sig.get("yards_filling") or []
        boxes.append(("At dry cargo berths",
                      str(img_dry),
                      "equipment arriving (yards filling)" if filling and img_dry
                      else ("direction unknown from the image"
                            if img_dry else "no vessel")))
    else:
        boxes.append(("Inbound cargo",
                      "unknown" if dry is None else str(dry),
                      "AIS not received" if dry is None
                      else f"of which project cargo: {proj}"))

    # Суда у причалов, увиденные на снимке. Отдельная плитка, потому
    # что это независимый от AIS источник и в Заливе — единственный
    # работающий.
    img_lng = sig.get("lng_alongside_img")
    if img_lng is not None:
        unchecked = sig.get("berths_unchecked") or 0
        boxes.append(("LNG carrier alongside",
                      str(img_lng),
                      "seen on the image, not via AIS" if img_lng
                      else (f"{unchecked} berths not yet checked"
                            if unchecked else "no vessel at an LNG berth")))

    frp = sig.get("frp_sum")
    boxes.append(("Flare power",
                  f"{frp:g}" if frp else "0",
                  "MW, VIIRS total"))

    out = ['<div class="sig">']
    for k, v, d in boxes:
        out.append(f'<div class="sigbox"><div class="k">{_esc(k)}</div>'
                   f'<div class="v">{_esc(v)}</div>'
                   f'<div class="d">{_esc(d)}</div></div>')
    out.append("</div>")
    return "\n".join(out)


def _trains_block(trains, teaser):
    rows, locked_n = [], 0
    for i, t in enumerate(trains):
        meta = TRAIN_STATUS.get(t.get("status"), TRAIN_STATUS["NO_DATA"])
        color = STATUS_COLOR.get(meta["status"], STATUS_COLOR["none"])
        gated = teaser is not None and i >= teaser

        if gated:
            locked_n += 1
            rows.append(
                f'<tr class="locked"><td><b>{_esc(t["name"])}</b></td>'
                f'<td class="hide-s">{_esc(t["venture"])}</td>'
                f'<td class="num mask hide-s">•••</td>'
                f'<td><span class="mask">••••••••</span></td>'
                f'<td class="conf mask">•••</td></tr>')
            continue

        conf = {"high": "high", "medium": "medium",
                "low": "low", "none": "—"}.get(t.get("confidence"), "—")
        ev = "<br>".join(_esc(e) for e in (t.get("evidence") or [])[:3])
        rows.append(
            f'<tr><td><b>{_esc(t["name"])}</b></td>'
            f'<td class="hide-s">{_esc(t["venture"])}</td>'
            f'<td class="num hide-s">{t.get("mtpa", "—")}</td>'
            f'<td><span class="st"><span class="ic" style="color:{color}">'
            f'{meta["icon"]}</span>{_esc(meta["label"])}</span>'
            f'{f"<div class=ev>{ev}</div>" if ev else ""}</td>'
            f'<td class="conf">{_esc(conf)}</td></tr>')

    table = (
        '<table><thead><tr><th>Train</th>'
        '<th class="hide-s">Venture</th><th class="num hide-s">mtpa</th>'
        '<th>Status and evidence</th><th>Conf.</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>')
    return table, locked_n


def _ai_verdict_by_object(ai):
    """
    Расхождения ИИ, разложенные по объектам.

    Нужно, чтобы поставить отметку о расхождении В САМУ таблицу
    причалов. Раньше таблица говорила «судно у причала» без оговорок,
    а через два экрана ниже ИИ-сверка возражала «судна не вижу» — и
    читатель находил спор сам, вместо того чтобы увидеть его сразу.
    Утверждение и возражение к нему должны стоять в одном месте.
    """
    out = {}
    if not ai or not ai.get("enabled"):
        return out
    for c in ai.get("checks") or []:
        if c.get("kind") == "berth" and c.get("object"):
            out[c["object"]] = {"agree": c.get("agree"),
                                "ai": c.get("ai"),
                                "provider": c.get("provider", "")}
    return out


def _berths_image_block(berths, ai=None):
    """
    Причалы по СНИМКУ — независимо от AIS.

    Этого блока раньше на сайте не было вовсе: занятость причалов
    считалась, но показывалась только в отчёте в Telegram. На витрине
    же в разделе про суда стояло «данных AIS нет», и выходило, что
    продукт обещает считать суда по снимку, а показывает пустоту.
    """
    if not berths:
        return ""

    cargo = [b for b in berths if b.get("kind") in ("lng", "liquid",
                                                    "drycargo")]
    if not cargo:
        return ""

    verdicts = _ai_verdict_by_object(ai)
    rows = ""
    for b in sorted(cargo, key=lambda x: (x.get("occupied") is not True,
                                          x["berth"])):
        occ = b.get("occupied")
        if occ is True:
            mark = ('<span class="st"><span class="ic" '
                    'style="color:var(--status-good)">&#9679;</span>'
                    'vessel alongside</span>')
        elif occ is False:
            mark = '<span class="conf">empty</span>'
        else:
            mark = '<span class="conf">not checked yet</span>'
        area = b.get("vessel_area_m2")
        extra = (f'<div class="ev">{area:,} m&sup2; of bright deck</div>'
                 .replace(",", " ") if occ and area else "")

        # Возражение ИИ — здесь же, а не только в разделе ниже.
        v = verdicts.get(b["berth"])
        if v and v.get("agree") is False:
            extra += ('<div class="ev" style="color:var(--status-bad)">'
                      '&#9888; the AI check disagrees — worth opening by eye'
                      + (f' ({_esc(v["provider"])})' if v.get("provider")
                         else '')
                      + '</div>')
        elif v and v.get("agree") is True:
            extra += ('<div class="ev">confirmed by the AI check</div>')

        rows += (f'<tr><td><b>{_esc(b["berth"])}</b>'
                 f'<div class="ev">{_esc(b.get("venture", ""))}</div></td>'
                 f'<td>{_esc(b["kind"])}</td>'
                 f'<td>{mark}{extra}</td></tr>')

    return ('<p class="hint" style="margin-top:4px"><b>From the image '
            '(independent of AIS).</b> Read from the satellite image itself: the deck '
            'of a moored vessel reflects near-infrared light, the water next '
            'to it does not. Static structures are removed by comparing with '
            'a baseline frame of the same berth. This works when AIS does '
            'not.</p>'
            '<table><thead><tr><th>Berth</th><th>Cargo</th>'
            '<th>On the image</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>')


def _shipping_block(ais):
    if not ais:
        return '<p class="hint">No AIS data for the period.</p>'

    now = ais.get("at_berth_now") or []
    done = [e for e in (ais.get("recent") or [])
            if e.get("departed") and (e.get("operation_done")
                                      or e.get("likely_loaded"))]

    out = []
    if now:
        rows = "".join(
            f'<tr><td><b>{_esc(e["vessel"])}</b></td>'
            f'<td>{_esc(VESSEL_LABEL.get(e.get("category"), "—"))}</td>'
            f'<td>{_esc(e["berth"])}</td>'
            f'<td class="hide-s">{_esc(e.get("venture", "—"))}</td></tr>'
            for e in now[:12])
        out.append('<table><thead><tr><th>Alongside now</th>'
                   '<th>Type</th><th>Berth</th>'
                   '<th class="hide-s">Cargo direction</th></tr></thead>'
                   f'<tbody>{rows}</tbody></table>')
    else:
        out.append('<p class="hint">The cargo berths are empty right now.</p>')

    if done:
        rows = "".join(
            f'<tr><td><b>{_esc(e["vessel"])}</b></td>'
            f'<td>{_esc(VESSEL_LABEL.get(e.get("category"), "—"))}</td>'
            f'<td>{_esc(e["berth"])}</td>'
            f'<td class="num">{e.get("hours", "—")}</td>'
            f'<td class="hide-s">{_esc(e.get("destination") or "not declared")}</td>'
            f'</tr>' for e in done[:18])
        out.append('<h2 style="margin-top:22px">Completed cargo operations</h2>'
                   '<p class="hint">A loaded carrier that has sailed is the '
                   'strongest proof that LNG is actually being produced and '
                   'not merely flared.</p>'
                   '<table><thead><tr><th>Vessel</th><th>Type</th>'
                   '<th>Berth</th><th class="num">Hours</th>'
                   '<th class="hide-s">Bound for</th></tr></thead>'
                   f'<tbody>{rows}</tbody></table>')
    return "\n".join(out)


def _video_block(out_dir):
    """
    Ролик-объяснитель на странице.

    Файл копируется в папку сайта: GitHub Pages отдаёт его как обычный
    файл, никакой YouTube не нужен и реклама чужих роликов рядом не
    появится.

    preload="metadata" — браузер скачивает только заголовок файла, а не
    весь ролик. Иначе каждый заход на страницу стоил бы посетителю
    полмегабайта трафика ещё до того, как он решит смотреть.

    Если ролика нет на диске, блок просто не появляется — страница
    собирается как раньше.
    """
    # Ищем ролик в двух местах: штатном и в корне проекта. Второе —
    # потому что присланный файл проще всего положить рядом с bot.py,
    # и молча его не заметить было бы обидно.
    candidates = [os.path.join("data", "video", "raslaffan_epc.mp4"),
                  "raslaffan_epc.mp4"]
    src = next((c for c in candidates if os.path.exists(c)), None)
    if not src:
        # Молчать нельзя: раздел просто не появлялся, и причина была
        # невидима. Теперь при сборке сайта бот прямо говорит, чего
        # ему не хватает и что сделать.
        print("[!] Ролик на сайт не попал: нет файла "
              f"{os.path.abspath(candidates[0])}")
        print("    Соберите его командой  python bot.py video")
        print("    или положите присланный raslaffan_epc.mp4 в эту папку.")
        return ""

    shutil.copy(src, os.path.join(out_dir, "raslaffan_epc.mp4"))

    # Заставка: первый слайд. Без неё до нажатия виден чёрный прямоугольник.
    poster_src = os.path.join(os.path.dirname(src) or ".", "slide_00.png")
    poster = ""
    if os.path.exists(poster_src):
        shutil.copy(poster_src, os.path.join(out_dir, "img", "poster.png"))
        poster = ' poster="img/poster.png"'

    return (f'<video class="vid" controls preload="metadata"{poster}>'
            f'<source src="raslaffan_epc.mp4" type="video/mp4">'
            f'Your browser cannot play this video. '
            f'<a href="raslaffan_epc.mp4">Download it instead.</a>'
            f'</video>')


def _berth_tally(berths):
    """
    Сколько судов стоит у причалов — по снимку.

    Нужно, чтобы поставить это число РЯДОМ с числом судов в открытой
    воде. Раньше под снимком стояло «0 vessels on the image», а ниже
    в разделе причалов — «2 судна у причала», и читателю оставалось
    думать, какому из двух чисел верить.

    На самом деле противоречия не было: это два разных измерения.
    Широкий детектор ищет суда на ВОДЕ — он требует, чтобы вокруг
    яркого пятна была вода, и отбрасывает пятна, слипшиеся с
    постройками. Судно у причала прижато к эстакаде и под это условие
    не попадает по построению. Поэтому у причалов считает отдельный
    метод, с вычитанием опорного кадра того же причала.

    Два числа об одном и том же, посчитанные по-разному, — это
    противоречие. Два числа о разном, подписанные точно, — это
    полнота. Разница только в подписи, и она здесь.
    """
    if not berths:
        return None
    cargo = [b for b in berths
             if b.get("kind") in ("lng", "liquid", "drycargo")]
    if not cargo:
        return None
    return {
        "alongside": sum(1 for b in cargo if b.get("occupied") is True),
        "checked": sum(1 for b in cargo if b.get("occupied") is not None),
        "total": len(cargo),
        "unchecked": sum(1 for b in cargo if b.get("occupied") is None),
    }


def _vessel_note(a, tally):
    """
    Пояснение к числам судов — то, что занимает место под снимком.

    Под картинкой оставалось пустое место, а в нём стояла строка
    «0 large, 200 m and over», которая ничего не сообщала. Здесь
    вместо неё четыре вещи, каждая из которых меняет чтение цифр:
    почему чисел два, с какой длины судно видно, что AIS не
    участвует, и чего по этому снимку сказать нельзя.

    Это не украшение страницы. Читатель — инженер, и он всё равно
    задаст эти вопросы; дешевле ответить до того, как он решит, что
    цифры не сходятся.
    """
    p = []

    if tally:
        p.append(
            'The two counts are different measurements, not two answers to '
            'one question. The open-water count needs water around a bright '
            'object and rejects blobs merged with structures, so a vessel '
            'moored against a jetty falls outside it by construction. '
            'Berths are read by a second method, against a baseline frame '
            'of the same berth. Neither number includes the other.')

    floor = a.get("reliable_min_len_m")
    px = a.get("px_size_m")
    if floor and px:
        p.append(
            f'A vessel is a few bright pixels on dark water. At {px:g} m per '
            f'pixel anything shorter than about {floor:g} m is not counted '
            f'reliably: tugs, pilot boats and barges come and go unseen. '
            f'An LNG carrier is 290-345 m and is never missed, but length '
            f'alone does not tell a carrier from a bulker — what the berth '
            f'is for does.')

    p.append(
        'None of this uses AIS. The count comes from the image, which is '
        'why it still works in a region where transponders are switched '
        'off or spoofed. The price of that independence: the image shows '
        'that a vessel is there, never its name, its cargo or which way '
        'the cargo is moving.')

    if not p:
        return ""
    return "".join(f'<p class="hint" style="margin:6px 0 0">{t}</p>'
                   for t in p)


def _freshness_note(a):
    """
    Почему снимок не сегодняшний.

    Без этого объяснения «5 days ago» читается как поломка. А это
    нормальная работа: Sentinel-2 — два спутника, и пригодный пролёт
    над Заливом выходит раз в несколько суток. Отдельно говорим про
    пролёты, которые БЫЛИ, но закрыты облаками: «спутник не пролетал»
    и «пролетал, но сквозь облака» — разные новости.
    """
    bits = []
    gap = a.get("typical_gap_days")
    if gap:
        bits.append(f"a usable pass over this box comes about every "
                    f"{gap} days")
    nxt = a.get("next_pass_estimate")
    if nxt:
        bits.append(f"next one expected around {nxt}")

    cloudy = a.get("newer_cloudy") or []
    if cloudy:
        lst = ", ".join(f"{c['date']} ({c['cloud']}% cloud)"
                        for c in cloudy[:3])
        bits.append(f"there were newer passes, but too cloudy to use: {lst}")
    elif a.get("date"):
        bits.append("no newer pass has been published yet")

    return "; ".join(bits)


def _carousel_block(areas, berths=None):
    """
    Карусель снимков: Ras Laffan и проливы.

    У каждого слайда СВОЯ дата съёмки — области разнесены на тысячи
    километров и попадают в разные витки спутника, поэтому единой даты
    не существует.
    """
    if not areas:
        return ""

    order = ["ras-laffan", "hormuz", "bab-el-mandeb"]
    items = [areas[k] for k in order if k in areas]
    items += [v for k, v in areas.items() if k not in order]
    if not items:
        return ""

    slides, dots = "", ""
    for i, a in enumerate(items):
        img_name = os.path.basename(a["image"]) if a.get("image") else None
        if img_name:
            pic = (f'<img src="img/{_esc(img_name)}" loading="lazy" '
                   f'alt="Satellite image: {_esc(a["title"])}, '
                   f'{_esc(a.get("date") or "date unknown")}">')
        else:
            pic = (f'<div class="car-noimg">{_esc(a.get("error") or "image unavailable")}'
                   f'</div>')

        date_txt = a.get("date") or "—"
        age = a.get("age_days")
        age_txt = ""
        if age is not None:
            age_txt = ("today" if age == 0 else
                       "yesterday" if age == 1 else f"{age} days ago")
            age_txt = f" · {age_txt}"
        cloud_txt = (f" · cloud {a['cloud']}%"
                     if a.get("cloud") is not None else "")

        stat = ""
        if not a.get("error"):
            # Подпись к числу судов обязана называть ОБЛАСТЬ ПОИСКА.
            # «Vessels on the image» — заявка на весь кадр, и она
            # вступала в спор с разделом причалов. Правильная заявка
            # узкая: суда в открытой воде, то есть идущие и на рейде.
            tally = _berth_tally(berths) if a["key"] == "ras-laffan" else None
            # «Large, 200 m and over» отсюда убрано: число ничего не
            # добавляло. У LNG-газовоза длина 290-345 м, то есть почти
            # каждое судно в этих водах и так «крупное», а отличить
            # газовоз от балкера по длине всё равно нельзя. Место
            # занято тем, что действительно помогает читать цифры:
            # с какой длины судно вообще видно на этом разрешении.
            floor = a.get("reliable_min_len_m")
            stat = (f'<div class="car-stat">'
                    f'<div><b>{a.get("count", 0)}</b>under way or at anchor, '
                    f'in open water</div>'
                    + (f'<div><b>{tally["alongside"]}</b>alongside berths '
                       f'(counted separately, see Berths)</div>'
                       if tally else '')
                    + f'<div><b>{a.get("px_size_m", "—")}</b>metres per pixel</div>'
                    + (f'<div><b>{floor:g}</b>metres: shortest vessel '
                       f'reliably seen</div>' if floor else '')
                    + (f'<div><b>{a["fixed_count"]}</b>fixed: platforms, buoys</div>'
                       if a.get("fixed_count") else '')
                    + f'</div>')
            stat += _vessel_note(a, tally)

        warn = ""
        if a.get("warning"):
            warn = f'<div class="caveat">{_esc(a["warning"])}</div>'

        fresh = _freshness_note(a)
        if fresh and not a.get("error"):
            warn += (f'<p class="hint" style="margin:6px 0 0">Why this date: '
                     f'{_esc(fresh)}.</p>')

        slides += (
            f'<div class="car-slide" role="group" aria-roledescription="slide" '
            f'aria-label="{i + 1} of {len(items)}: {_esc(a["title"])}">'
            f'{pic}'
            f'<div class="car-cap"><b>{_esc(a["title"])}</b>'
            f'<span class="d">image {_esc(date_txt)}{age_txt}{cloud_txt}</span>'
            f'</div>'
            f'<p class="hint" style="margin:4px 0 0">{_esc(a.get("note", ""))}</p>'
            f'{stat}{warn}</div>')

        dots += (f'<button class="car-dot" type="button" data-go="{i}" '
                 f'aria-label="Show: {_esc(a["title"])}"'
                 f'{" aria-current=&quot;true&quot;" if i == 0 else ""}></button>')

    return (
        '<div class="car">'
        f'<div class="car-track" id="cartrack" tabindex="0" '
        f'aria-label="Satellite images of the watch areas">{slides}</div>'
        '<div class="car-nav">'
        '<button class="car-btn" type="button" data-step="-1">&larr; Back</button>'
        '<button class="car-btn" type="button" data-step="1">Next &rarr;</button>'
        f'<div class="car-dots">{dots}</div>'
        '</div></div>')


# ---------------------------------------------------------------------------
# Блоки для подрядчиков
# ---------------------------------------------------------------------------
def _heat_block(h):
    """Индекс активности с разбором составляющих."""
    if not h or h.get("index") is None:
        return ('<p class="hint">Index not computed: '
                + _esc((h or {}).get("note", "no data")) + "</p>")

    lvl = h["level"]
    color = STATUS_COLOR.get(lvl["status"], STATUS_COLOR["none"])

    rows = ""
    for c in h["components"].values():
        if c["score"] is None:
            bar = '<div class="hbar"><i style="width:0"></i></div>'
            val = '<span class="hna">no data</span>'
        else:
            bar = (f'<div class="hbar"><i style="width:{c["score"]:.0f}%;'
                   f'background:{color}"></i></div>')
            val = f'{c["score"]:g}'
        rows += (f'<tr><td><b>{_esc(c["label"])}</b>'
                 f'<div class="ev">{_esc(c["why"])}</div></td>'
                 f'<td class="num">{int(c["weight"] * 100)}%</td>'
                 f'<td style="width:38%">{bar}</td>'
                 f'<td class="num">{val}</td></tr>')

    miss = ""
    if h.get("missing"):
        miss = ('<div class="caveat">Components unavailable: '
                + _esc(", ".join(h["missing"]))
                + f'. They are excluded and the remaining weights '
                  f're-normalised. Data coverage: '
                  f'{h.get("coverage_pct", "—")}%.</div>')

    return (
        '<div class="verdict"><div class="hero">'
        f'<div class="n">{h["index"]:g}<small>/100</small></div>'
        f'<div class="lbl">activity index<br>{_esc(lvl["label"])} activity</div>'
        '</div><div class="vnote">'
        f'<span class="chip"><span class="dot" style="background:{color}"></span>'
        f'{_esc(lvl["what"])}</span>'
        '<p style="margin:12px 0 0">The index measures how close the work is, '
        'not how the owner is doing. It rises deliberately with idle capacity '
        'and inbound equipment: a plant running at full rate has nothing to '
        'offer a contractor.</p>' + miss + '</div></div>'
        '<table style="margin-top:18px"><thead><tr><th>Component</th>'
        '<th class="num">Weight</th><th>Level</th><th class="num">Score</th>'
        f'</tr></thead><tbody>{rows}</tbody></table>'
        # Сверка кода и модели раньше стояла здесь же, под индексом.
        # Перенесена в конец страницы: она отвечает на вопрос «можно ли
        # верить прочитанному», а такой вопрос возникает после того,
        # как прочитано, а не посреди чтения. Ссылку оставляем, чтобы
        # связь между цифрой и её проверкой не потерялась.
        + ('<p class="hint" style="margin-top:12px">These readings are '
           'checked a second time, by a different method, at the end of '
           'the page.</p>' if (h.get("ai") or {}).get("enabled") else ''))


def _ai_block(ai):
    """
    Сверка: что нашёл код и что нашла модель.

    Главное здесь — не ответ модели сам по себе, а СОВПАДЕНИЕ. Два
    независимых метода, сошедшихся на одном выводе, стоят много;
    разошедшиеся — прямое указание, какую зону открыть глазами.
    Поэтому строки с расхождением стоят первыми.

    Модель никогда не переписывает вывод кода: оба ответа показаны
    рядом, и читатель видит, на чём стоит цифра индекса.
    """
    # Заголовок одинаковый во всех случаях: раздел стоит отдельным
    # блоком в конце страницы, и если проверка не прошла, читатель
    # должен это ВИДЕТЬ, а не обнаруживать пропавший раздел. Молча
    # исчезнувшая проверка выглядит как пройденная.
    head = ('<h2>Second opinion: is a vessel really there, are works '
            'really under way</h2>')
    if not ai:
        return ""
    if not ai.get("enabled"):
        return (head
                + f'<p class="hint" style="margin-top:14px">Independent AI '
                f'check: not run. {_esc(ai.get("note", ""))}</p>')

    checks = ai.get("checks") or []
    if not checks:
        return (head + '<p class="hint" style="margin-top:14px">Independent '
                'AI check: nothing to verify.</p>')

    order = {False: 0, None: 1, True: 2}
    rows = ""
    for c in sorted(checks, key=lambda x: order.get(x.get("agree"), 1)):
        agree = c.get("agree")
        if agree is False:
            mark = _chip("critical", "disagree")
        elif agree is True:
            mark = _chip("good", "agree")
        else:
            mark = _chip("none", "not comparable")

        ai_txt = c.get("ai")
        if ai_txt is None:
            ai_txt = f'no answer — {c.get("error", "")}'
            prov = ", ".join(c.get("tried") or [])[:130]
        else:
            prov = c.get("provider", "")
            if c.get("ai_confidence"):
                prov += f' · {c["ai_confidence"]} confidence'

        note = (f'<div class="ev">{_esc(c["ai_note"])}</div>'
                if c.get("ai_note") else "")
        clues = c.get("ai_direction_clues") or []
        if clues:
            note += (f'<div class="ev">clues: '
                     f'{_esc(", ".join(str(x) for x in clues))}</div>')
        rows += (f'<tr><td><b>{_esc(c["object"])}</b>'
                 f'<div class="ev">{_esc(c.get("kind", ""))}</div></td>'
                 f'<td>{_esc(c.get("code", ""))}</td>'
                 f'<td>{_esc(ai_txt)}{note}'
                 f'<div class="ev">{_esc(prov)}</div></td>'
                 f'<td>{mark}</td></tr>')

    used = (f'{ai.get("calls", 0)} checks via '
            f'{", ".join(ai.get("providers") or []) or "no provider"}')
    return (
        head
        + '<p class="hint">The same two questions asked twice, by two methods '
        'that fail differently. The code counts bright pixels: reliable, but '
        'blind to meaning — it cannot tell an LNG carrier from a barge. A '
        'vision model reads the scene but will invent detail. Neither '
        'overrides the other. Where they disagree, that zone is worth '
        f'opening by eye. {_esc(used)}.</p>'
        '<table><thead><tr><th>Object</th><th>What the code found</th>'
        '<th>What the AI found</th><th>Verdict</th></tr></thead>'
        f'<tbody>{rows}</tbody></table>')


def _intel_block(intel):
    """Новости, ролики и обсуждения за сутки."""
    if not intel:
        return '<p class="hint">No intelligence collected for the period.</p>'

    out = ""

    news = intel.get("news") or []
    if news:
        # Если за сутки новостей не нашлось и окно расширялось, надо
        # сказать об этом вслух. Иначе заголовок месячной давности под
        # шапкой «за последние сутки» выглядит как свежий.
        span, want = intel.get("news_span"), intel.get("news_span_requested")
        if span and want and span != want:
            human = {"7d": "seven days", "1m": "month", "3m": "three months",
                     "12m": "year", "30d": "30 days"}.get(span, span)
            out += ('<div class="caveat">Nothing was published about the '
                    'asset in the last 24 hours. Showing the most recent '
                    f'items from the past {_esc(human)} — each with its own '
                    'date.</div>')

        rows = ""
        for a in news[:12]:
            d = a.get("date", "")
            d = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) >= 8 else d
            rows += (f'<tr><td><a href="{_esc(a["url"])}" target="_blank" '
                     f'rel="noopener">{_esc(a["title"][:140])}</a></td>'
                     f'<td class="hide-s">{_esc(a.get("domain", ""))}</td>'
                     f'<td class="num">{_esc(d)}</td></tr>')
        out += ('<table><thead><tr><th>Asset in the news</th>'
                '<th class="hide-s">Source</th><th class="num">Published</th>'
                f'</tr></thead><tbody>{rows}</tbody></table>')
    else:
        # Пустой раздел должен объяснять себя: «новостей нет» и
        # «поиск не отработал» — разные вещи.
        err = (intel or {}).get("news_error")
        if err:
            out += (f'<div class="caveat">News search did not run: '
                    f'{_esc(err)}</div>')
        else:
            out += ('<p class="hint">Nothing published about the asset in '
                    'the past year that the news index covers.</p>')

    tone = intel.get("tone")
    if tone:
        direction = ("deteriorating" if tone.get("trend", 0) < -0.5
                     else "improving" if tone.get("trend", 0) > 0.5
                     else "broadly unchanged")
        out += (f'<p class="hint" style="margin-top:14px">Tone of coverage: '
                f'{tone.get("avg")} (scale roughly −10 to +10), '
                f'{direction} over two weeks. Negative values mean incidents, '
                f'delays and sanctions — for a contractor that is a sign of '
                f'work ahead.</p>')

    vids = intel.get("videos") or []
    if vids:
        rows = ""
        for v in vids[:6]:
            rows += (f'<tr><td><a href="{_esc(v["url"])}" target="_blank" '
                     f'rel="noopener">{_esc(v["title"][:120])}</a></td>'
                     f'<td class="hide-s">{_esc(v.get("channel", ""))}</td>'
                     f'<td class="num">{_esc(v.get("date", ""))}</td></tr>')
        out += ('<h2 style="margin-top:24px">Video</h2>'
                '<table><thead><tr><th>Clip</th>'
                '<th class="hide-s">Channel</th><th class="num">Date</th>'
                f'</tr></thead><tbody>{rows}</tbody></table>')

    disc = intel.get("discussions") or []
    if disc:
        rows = ""
        for d in disc[:6]:
            rows += (f'<tr><td><a href="{_esc(d["url"])}" target="_blank" '
                     f'rel="noopener">{_esc(d["title"][:120])}</a></td>'
                     f'<td class="hide-s">{_esc(d.get("subreddit", ""))}</td>'
                     f'<td class="num">{d.get("comments", 0)}</td></tr>')
        out += ('<h2 style="margin-top:24px">Discussions</h2>'
                '<table><thead><tr><th>Thread</th>'
                '<th class="hide-s">Board</th><th class="num">Comments</th>'
                f'</tr></thead><tbody>{rows}</tbody></table>')

    return out


def _contractors_block(intel, fin):
    """Подрядчики: подряды, набор персонала, отчётность."""
    out = ""

    # Сначала то, что известно точно. Статистика упоминаний отвечает на
    # вопрос «кто может получить работу», а назначенный генподрядчик —
    # это уже ответ, и держать его в общем списке наравне с совпадениями
    # в прессе значит прятать главное.
    known = getattr(config, "EPC_KNOWN_ROLES", {})
    if known:
        items = "".join(
            f'<p style="margin:0 0 8px"><b>{_esc(n)}</b> — {_esc(role)}</p>'
            for n, role in known.items())
        out += (f'<div class="verdict" style="display:block">{items}'
                f'<p class="hint" style="margin:4px 0 0">Confirmed from the '
                f'companies\' own announcements, not inferred from press '
                f'volume.</p></div>')

    cons = (intel or {}).get("contractors") or {}
    if cons:
        floor = getattr(config, "EPC_MIN_MENTIONS", 2)
        rows = ""
        for name, c in sorted(cons.items(),
                              key=lambda kv: -(kv[1]["award_mentions"]
                                               + kv[1]["hiring_mentions"])):
            # Отсев шума: одно упоминание без подрядов и без набора
            # людей — это случайное соседство в обзорной статье про
            # отрасль. Такие строки изображают данные там, где их нет.
            # В таблицу идут только подрядчики, у которых есть ПОДРЯД
            # или НАБОР ЛЮДЕЙ. Одни упоминания в прессе ничего не
            # доказывают: подрядчик мог попасть в обзорную статью про
            # отрасль, ни разу не приблизившись к объекту. Строка
            # «14 упоминаний, 0 подрядов, 0 найма» — это не данные, а
            # видимость данных.
            if not (c["award_mentions"] or c["hiring_mentions"]):
                continue
            if c["articles_total"] < floor:
                continue
            rows += (f'<tr><td><b>{_esc(name)}</b></td>'
                     f'<td class="num">{c["articles_total"]}</td>'
                     f'<td class="num">{c["award_mentions"]}</td>'
                     f'<td class="num">{c["hiring_mentions"]}</td></tr>')
        if rows:
            out += ('<table><thead><tr><th>Contractor</th>'
                    '<th class="num">Mentions</th>'
                    '<th class="num">Awards</th>'
                    '<th class="num">Hiring</th></tr></thead>'
                    f'<tbody>{rows}</tbody></table>'
                    '<p class="hint" style="margin-top:10px">Mass hiring of '
                    'fitters and welders is the earliest sign that a '
                    'contractor has won the work and is mobilising. It shows '
                    'months before anything changes on the image.</p>')

    # Финансовая отчётность по умолчанию с витрины убрана: см.
    # SHOW_CONTRACTOR_FILINGS в config.py.
    if not getattr(config, "SHOW_CONTRACTOR_FILINGS", False):
        return out or ('<p class="hint">No contractor has an announced '
                       'award or visible hiring around this asset yet, '
                       'beyond the lead contractor above.</p>')

    us = (fin or {}).get("us_listed") or []
    rows = ""
    for c in us:
        if c.get("error") or not c.get("revenue"):
            continue
        last = c["revenue"][-1]
        chg = c.get("revenue_change_pct")
        # Рядом с процентом указываем, с чем сравнивали: без этого
        # «+8%» читается как угодно — к прошлому кварталу, к году.
        base = c.get("revenue_compared_to", "")
        chg_txt = (f'{chg:+.1f}%<div class="ev">vs {_esc(base)}</div>'
                   if chg is not None else "—")
        bl = (f'{c["backlog"][-1]["val"] / 1e9:.1f}' if c.get("backlog") else "—")
        rows += (f'<tr><td><b>{_esc(c["name"])}</b></td>'
                 f'<td class="num">{last["val"] / 1e9:.2f}</td>'
                 f'<td class="num">{chg_txt}</td>'
                 f'<td class="num hide-s">{bl}</td>'
                 f'<td class="num hide-s">{_esc(last["end"])}</td></tr>')
    if rows:
        out += ('<h2 style="margin-top:24px">Contractor filings</h2>'
                '<table><thead><tr><th>Company</th>'
                '<th class="num">Revenue, $bn</th>'
                '<th class="num">Year on year</th>'
                '<th class="num hide-s">Backlog, $bn</th>'
                '<th class="num hide-s">Period</th></tr></thead>'
                f'<tbody>{rows}</tbody></table>')

    if fin and fin.get("note"):
        out += f'<div class="caveat">{_esc(fin["note"])}</div>'

    return out or '<p class="hint">No contractor data collected for the period.</p>'


def _ais_notice_block(ais):
    """Честное объяснение, почему нет данных AIS."""
    if ais and (ais.get("atBerthNow") or ais.get("at_berth_now")
                or ais.get("recent")):
        return ""
    return (f'<div class="caveat" style="border-left-color:var(--status-serious)">'
            f'<b>{_esc(config.AIS_UNAVAILABLE_TITLE)}</b><br>'
            f'{_esc(config.AIS_UNAVAILABLE_TEXT)}</div>')


def _roles_block():
    """
    Карта принятия решений: кто решает и когда к нему заходить.

    Фамилии и должности — только опубликованные самими компаниями, с
    ссылкой на первоисточник в каждой строке.

    Про размытую ячейку. В HTML уходит МАСКА из точек, а не спрятанный
    текст: размытие через CSS ничего не скрывает, исходный код страницы
    открывается по Ctrl+U. Поэтому размывать можно только то, чего в
    странице нет. Личных контактов нет ни здесь, ни в config.
    """
    rows = ""
    for r in config.DECISION_MAP:
        if r.get("name"):
            who = (f'<b>{_esc(r["name"])}</b>'
                   f'<div class="ev">{_esc(r["title"])}</div>')
        else:
            who = (f'<b>{_esc(r["role"])}</b>'
                   f'<div class="ev">name not published by the company</div>')

        src = r.get("source", "")
        src_html = ""
        if src.startswith("http"):
            src_html = (f'<div class="ev"><a href="{_esc(src)}" target="_blank" '
                        f'rel="noopener">source</a></div>')
        elif src:
            src_html = f'<div class="ev">{_esc(src)}</div>'

        rows += (f'<tr><td>{who}'
                 f'<div class="ev">{_esc(r.get("org", ""))}</div>{src_html}</td>'
                 f'<td>{_esc(r.get("owns", ""))}</td>'
                 f'<td class="hide-s">{_esc(r.get("when", ""))}</td>'
                 f'<td class="blur" aria-label="approach route available on '
                 f'request">&bull;&bull;&bull;&bull;&bull;&bull;&bull;&bull;'
                 f'&bull;&bull;&bull;</td></tr>')

    return ('<table><thead><tr><th>Who</th><th>Owns</th>'
            '<th class="hide-s">When to engage</th>'
            '<th>Route</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>'
            f'<div class="caveat">{_esc(config.DECISION_MAP_NOTE)}</div>')


def _chart_block(trend, note=""):
    """
    График тепловой мощности.

    Когда ряд пуст, обязательно пишем ПОЧЕМУ. «Факелы не горят» и
    «данные не пришли» — противоположные новости, а выглядели
    одинаково: «нет данных VIIRS».
    """
    if not trend:
        if note:
            return f'<div class="caveat">VIIRS: {_esc(note)}</div>'
        return ('<p class="hint">No VIIRS data for the period, and no '
                'reason reported — check the run log.</p>')

    vals = [v for _, v in trend if v is not None]
    if not vals:
        return ('<p class="hint">The flare history holds no measured days '
                'yet. It fills up one run at a time.</p>')
    top = max(vals) or 1

    # Сутки без запроса рисуются пустой рамкой, а не нулевым столбиком.
    # Ноль означает «смотрели, факел не горел»; пропуск — «не
    # смотрели». На графике это обязано выглядеть по-разному, иначе
    # неделя простоя бота читается как неделя остановки завода.
    bars = ""
    for d, v in trend:
        if v is None:
            bars += (f'<div class="bar gap" data-tip="{_esc(d)}: not '
                     f'measured" role="img" '
                     f'aria-label="{_esc(d)}: not measured"></div>')
        else:
            bars += (f'<div class="bar" '
                     f'style="height:{max(3, v / top * 100):.1f}%" '
                     f'data-tip="{_esc(d)}: {v:g} MW" '
                     f'role="img" '
                     f'aria-label="{_esc(d)}: {v:g} megawatts"></div>')

    measured = [(d, v) for d, v in trend if v is not None]
    gaps = sum(1 for _, v in trend if v is None)
    avg = sum(vals) / len(vals)

    sub = (f'{len(measured)} days measured, '
           f'average {avg:.0f} MW, peak {top:g} MW')
    if gaps:
        sub += f', {gaps} days not measured (shown as gaps)'

    return (f'<div class="chart">{bars}</div>'
            f'<div class="axis"><span>{_esc(measured[0][0])}</span>'
            f'<span>peak {top:g} MW</span>'
            f'<span>{_esc(measured[-1][0])}</span></div>'
            f'<p class="hint" style="margin:6px 0 0">{_esc(sub)}. '
            f'The history is kept locally and grows with every run: '
            f'VIIRS itself only serves the most recent days.</p>')


def _form_block():
    """
    Призыв к действию — одной строкой, без формы.

    Форма с полем «work email» убрана намеренно. Посетитель этой
    страницы — специалист, который пришёл смотреть данные; требовать
    у него адрес, прежде чем он что-то получил, значит ставить
    шлагбаум там, где нужна дверь. Три прямых способа связи работают
    лучше: он выберет тот, которым пользуется сам.
    """
    mail = config.BRAND_CONTACT_EMAIL
    subject = "Ras%20Laffan%20monitor%20%E2%80%94%20access%20request"
    return (
        f'<p class="cta-line">Would you like to get the full report on '
        f'Ras Laffan or other projects? '
        f'<a href="{_esc(config.BRAND_CALENDLY)}">Book a call</a> '
        f'&middot; <a href="mailto:{_esc(mail)}?subject={subject}">'
        f'{_esc(mail)}</a> '
        f'&middot; <a href="{_esc(config.BRAND_LINKEDIN)}" '
        f'rel="noopener">LinkedIn</a></p>')


# ---------------------------------------------------------------------------
# Главная функция
# ---------------------------------------------------------------------------
def build(sat: dict, ais: dict = None, public: bool = True,
          out_dir: str = None, areas: dict = None,
          intel: dict = None, fin: dict = None, heat: dict = None) -> str:
    """
    Собирает сайт в папку out_dir (по умолчанию docs/).

    public=True  — публичная версия: часть линий закрыта формой захвата.
    public=False — полная версия для себя и для клиентов по подписке.
    areas        — результат areas.run(): снимки Ras Laffan и проливов
                   для карусели, у каждого своя дата съёмки.
    """
    out_dir = out_dir or config.SITE_DIR
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(os.path.join(out_dir, "img"), exist_ok=True)

    fused = fusion.run(sat, ais)
    cx, cap = fused["complex"], fused["capacity"]
    trains = sat.get("trains", [])
    teaser = config.PUBLIC_TEASER_TRAINS if public else None

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    period = sat.get("period", {})
    util = cap.get("utilisation_pct")

    # Дата снимка — важная для читателя величина: он должен понимать,
    # на какой момент относятся выводы по теплу. Судовые данные при
    # этом свежие, и об этом тоже стоит сказать.
    scene = sat.get("scene") or {}
    if scene.get("date"):
        age = scene.get("age_days", 0)
        age_txt = ("today" if age == 0 else
                   "yesterday" if age == 1 else f"{age} days ago")
        scene_line = (f"image of {_esc(scene['date'])} ({age_txt}), "
                      f"cloud {scene.get('cloud', 0)}%")
    else:
        scene_line = (f"imagery for {_esc(period.get('from', '?'))} — "
                      f"{_esc(period.get('to', '?'))}")

    carousel = _carousel_block(areas or {}, sat.get("berths") or [])
    video_html = _video_block(out_dir)
    heat_html = _heat_block(heat)
    # Сверка кода и модели — отдельным разделом в конце страницы.
    ai_html = _ai_block((heat or {}).get("ai"))
    intel_html = _intel_block(intel)
    contractors_html = _contractors_block(intel, fin)
    ais_notice = _ais_notice_block(ais)
    roles_html = _roles_block()
    trains_table, locked_n = _trains_block(trains, teaser)

    gate = ""
    if locked_n:
        gate = (f'<div class="gate"><b>{locked_n} of {len(trains)} trains '
                f'locked</b>'
                f'<p>The full version: every train of the complex, week-by-week '
                f'history, CSV export and an alert when a status changes.</p>'
                f'{_form_block()}</div>')

    # --- таблица вспомогательных площадок (индикатор стройки) ---
    sites = sat.get("sites") or []
    site_rows_list = []
    for s in sites:
        chg = s.get("changed")
        chg_txt = "—" if chg is None else "{:.0%}".format(chg)
        direction = s.get("direction")
        if s.get("construction_signal"):
            verdict_txt = ("filling up — works" if direction == "filling"
                           else "signs of works")
        elif direction == "clearing":
            verdict_txt = "emptying — material leaving"
        else:
            verdict_txt = "unchanged"
        site_rows_list.append(
            f'<tr><td><b>{_esc(s["site"])}</b></td>'
            f'<td class="hide-s">{_esc(s.get("note", ""))}</td>'
            f'<td class="num">{chg_txt}</td>'
            f'<td>{verdict_txt}</td></tr>')
    sites_rows = "".join(site_rows_list)
    sites_block = (
        '<table><thead><tr><th>Site</th>'
        '<th class="hide-s">What it is</th><th class="num">Changed</th>'
        '<th>Reading</th></tr></thead>'
        f'<tbody>{sites_rows}</tbody></table>') if sites_rows else \
        '<p class="hint">No imagery of the support sites for the period.</p>'

    caveats = "".join(f'<div class="caveat">{_esc(c)}</div>'
                      for c in cx.get("caveats", []))

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ras Laffan — contractor activity monitor</title>
<meta name="description" content="Where the contractor work will be: the Ras Laffan activity
index from satellite imagery, vessels, press and mobilisation signals. {config.BRAND_NAME}.">
<meta property="og:title" content="Ras Laffan — where the contractor work will be">
<meta property="og:description" content="Activity index, satellite imagery, vessels, tenders and
contractor mobilisation signals. From open data.">
<style>{_strip_comments(CSS)}</style>
</head>
<body>
<button class="toggle" id="tg" type="button">Dark theme</button>
<div class="wrap">

<header>
  <div class="brand">
    <b>{_esc(config.BRAND_NAME)}</b>
    <span>{_esc(config.BRAND_TAGLINE)}</span>
  </div>
  <h1>Ras Laffan: where the contractor work will be</h1>
  <p class="sub">The world's largest LNG complex is being rebuilt after the
  March 2026 strike. Two liquefaction trains were hit, roughly 17% of export
  capacity, and QatarEnergy's CEO has put the repair at three years. We
  measure how close that work is — from satellite imagery, vessels, press and
  mobilisation signals. No insider information, no paid terminals.</p>
  <p class="stamp">Updated {_esc(stamp)} &middot; {scene_line}</p>
</header>

<section class="card">
  <h2>Satellite imagery of the watch areas</h2>
  <p class="hint">Vessels are counted on the image itself: water absorbs
  near-infrared light and reads almost black, while a deck reflects it — a
  hull shows as a bright dash on dark water. A transponder can be switched
  off; a ship cannot hide from the image. Each area carries its own
  acquisition date: they are thousands of kilometres apart and fall into
  different satellite orbits.</p>
  {carousel}
</section>

<section class="card">
  <h2>Flaring: total VIIRS power by day, MW</h2>
  <p class="hint">The VIIRS instrument measures radiative power. A rise
  means more gas being burned, which means higher throughput.</p>
  {_chart_block(sat.get("firms_trend"), sat.get("firms_note", ""))}
</section>

<section class="card">
  <h2>Berths: who is alongside</h2>
  <p class="hint">First what the image shows, then what AIS says. The two
  are independent, and in this region only the first can be trusted
  unconditionally.</p>
  {_berths_image_block(sat.get("berths"), (heat or {}).get("ai"))}
  {ais_notice}
  {_shipping_block(ais)}
</section>

<section class="card">
  <h2>Capacity in service, and the verdict</h2>
  <div class="verdict">
    <div class="hero">
      <div class="n">{"—" if util is None else f"{util:g}"}<small>%</small></div>
      <div class="lbl">capacity running<br>
        {cap.get("producing_mtpa", 0):g} of
        {cap.get("producing_mtpa", 0) + cap.get("down_mtpa", 0):g} mtpa</div>
    </div>
    <div class="vnote">
      {_chip(cx["status"], cx["label"])}
      <p style="margin:12px 0 0">{_esc(cx["note"])}</p>
      {caveats}
    </div>
  </div>
</section>

{f'''<section class="card">
  <h2>What this is, in 48 seconds</h2>
  {video_html}
</section>''' if video_html else ''}

<section class="card">
  <h2>Contractor activity index</h2>
  <p class="hint">One number answering one question: is this the moment to
  engage. Every component and its weight is shown below — this is a weighted
  sum, not a measurement, and you should see what it is made of.</p>
  {heat_html}
</section>

<section class="card">
  <h2>The three signals behind the verdict</h2>
  <p class="hint">A flare proves gas is burning, not that the plant is
  producing: a pre-maintenance purge and commissioning burn the same way.
  Production is proved by a loading. And cargo arriving with nothing leaving
  means reconstruction.</p>
  {_signals_block(cx)}
</section>

<section class="card">
  <h2>Liquefaction train status</h2>
  <p class="hint">Every zone is read from one single image — {scene_line} —
  so the zones are comparable with each other. Status comes from the thermal
  signature in the short-wave infrared band, the burn index, and the change
  in the site's appearance against the
  {_esc(scene.get("baseline_date", "February 2026"))} baseline image, taken
  before production stopped.</p>
  {trains_table}
  {gate}
</section>

<section class="card">
  <h2>Latest news on the asset</h2>
  <p class="hint">The image shows what is happening today. News and hiring
  signals show what is planned for tomorrow: a tender notice appears weeks
  before anything on the site changes. Every item carries its publication
  date — nothing here is presented as fresher than it is.</p>
  {intel_html}
</section>

<section class="card">
  <h2>Contractors on this asset</h2>
  <p class="hint">Who is confirmed on the work, and who shows an award or
  hiring signal around it. Press mentions alone are not listed: a company
  can appear in an industry round-up without ever approaching the site.</p>
  {contractors_html}
</section>

<section class="card">
  <h2>Who decides, and when to reach them</h2>
  <p class="hint">Names and titles below are published by the companies
  themselves and each row links to its source. What is masked is the approach
  route: which official channel reaches that desk, in what order, and what
  the supplier portal will ask for.</p>
  {roles_html}
</section>

<section class="card">
  <h2>Signs of works onshore</h2>
  <p class="hint">The module laydown yard is a direct construction
  indicator: under active works it fills with equipment blocks, and new
  hardstanding and haul roads appear.</p>
  {sites_block}
</section>

<section class="card">
  <h2>Method, and the limits we state openly</h2>
  <div class="meth">
    <p><b>Sentinel-2</b>, European Union. Imagery every 5 days at 10 metres
    per pixel. Running equipment and flares glow brightly in band B12
    (2190 nm), which is what allows individual trains to be told apart.</p>
    <p><b>VIIRS</b>, NASA. Thermal anomalies several times a day with
    radiative power in megawatts. At 375 m it cannot separate individual
    trains, but it does show the overall level of flaring.</p>
    <p><b>AIS</b>. The open broadcast from ship transponders: arrivals,
    departures, time alongside, declared destination.</p>
    <p><b>A vessel cannot be tied to a train.</b> LNG from every train goes
    into shared storage and out over shared berths. The link is lost inside
    the plant. Nobody does this, paid terminals included. The attribution
    runs as far as berth and venture.</p>
    <p><b>A ship at a quay does not show which way the cargo moves.</b>
    Ras Laffan both receives project equipment and exports sulphur, and
    demolition debris leaves the same quays. On the image all of these are
    a hull alongside. We call it equipment arriving only when something
    else says so: AIS arrival and departure records, or a laydown yard
    that is visibly filling. Otherwise the direction is reported as
    unknown.</p>
    <p><b>Inbound cargo does not identify the project.</b> The train repair,
    the Barzan restoration, Pearl GTL maintenance and the North Field East
    expansion all run at once. Project cargo may belong to any of them.</p>
    <p><b>Trucks are invisible on the image.</b> A lorry covers 1–2 pixels.
    Equipment delivery is tracked by ship, and onshore work by the change in
    the yards.</p>
    <p><b>AIS draught is unreliable.</b> The field is filled in by hand and
    is often stale. The primary loading signal is time alongside a cargo
    berth.</p>
    <p><b>The conclusions are automatic.</b> Ten-metre resolution gives a
    hypothesis, not a fact. Each train carries a confidence level, and
    disputed cases are checked against the image by eye.</p>
  </div>
</section>

{f'<section class="card">{ai_html}</section>' if ai_html else ''}

<section class="card" style="text-align:center">
  <h2>Want the same monitor for your asset?</h2>
  <p class="hint" style="margin:6px auto 18px">The same method works on any
  refinery, terminal or LNG plant in the world: asset condition, throughput,
  loadings, signs of works. Set-up for a specific asset takes days, not
  months.</p>
  {_form_block()}
</section>

<footer>
  <p>{_esc(config.BRAND_SIGNATURE)} &middot; {_esc(config.BRAND_NAME)} &middot;
  <a href="mailto:{_esc(config.BRAND_CONTACT_EMAIL)}">{_esc(config.BRAND_CONTACT_EMAIL)}</a>
  &middot; <a href="{_esc(config.BRAND_LINKEDIN)}">LinkedIn</a></p>
  {_privacy_line()}
  <p>Sources: Copernicus Sentinel-2, NASA FIRMS VIIRS, AIS, GDELT, SEC EDGAR.
  Machine-readable version: <a href="data.json">data.json</a>.
  This is not investment advice.</p>
</footer>

</div>
<script>{_strip_comments(JS)}</script>
<script>{_strip_comments(_analytics_js())}</script>
</body>
</html>
"""

    index = os.path.join(out_dir, "index.html")
    with open(index, "w", encoding="utf-8") as fh:
        fh.write(html)

    # Машиночитаемая версия — её можно отдавать клиентам как мини-API.
    payload = {
        "generated_utc": stamp,
        "period": period,
        "complex": cx,
        "capacity": cap,
        "trains": [{k: v for k, v in t.items() if k != "chip"} for t in trains],
        "berths": sat.get("berths", []),
        "sites": sat.get("sites", []),
        "firms_trend": sat.get("firms_trend", []),
        "shipping": (ais or {}).get("signals", {}),
        "heat": heat,
        "intel": {k: v for k, v in (intel or {}).items()
                  if k in ("collected", "news_count", "tone", "volume",
                           "hiring_total", "award_total")},
        "finance": fin,
        "areas": {k: {kk: vv for kk, vv in v.items() if kk != "vessels"}
                  for k, v in (areas or {}).items()},
        "disclaimer": ("Automatic conclusions from open data. Tying a "
                       "vessel to a specific liquefaction train is "
                       "physically impossible."),
    }
    with open(os.path.join(out_dir, "data.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    # Чтобы GitHub Pages не пытался обрабатывать папку через Jekyll.
    open(os.path.join(out_dir, ".nojekyll"), "w").close()

    # Картинки областей для карусели.
    for a in (areas or {}).values():
        src = a.get("image")
        if src and os.path.exists(src):
            shutil.copy(src, os.path.join(out_dir, "img",
                                          os.path.basename(src)))

    # Картинки зон — пригодятся как доказательство на странице и в письмах.
    for t in trains:
        chip = t.get("chip")
        if chip and os.path.exists(chip):
            shutil.copy(chip, os.path.join(out_dir, "img",
                                           os.path.basename(chip)))
    return index
