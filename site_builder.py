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
    "PRODUCING":  {"ru": "работает",      "icon": "▲", "status": "good"},
    "REBUILDING": {"ru": "идут работы",   "icon": "◆", "status": "serious"},
    "DESTROYED":  {"ru": "разрушена",     "icon": "✕", "status": "critical"},
    "IDLE":       {"ru": "стоит, цела",   "icon": "■", "status": "warning"},
    "NO_DATA":    {"ru": "нет данных",    "icon": "–", "status": "none"},
    "ERROR":      {"ru": "ошибка",        "icon": "!", "status": "none"},
}

CATEGORY_RU = {
    "gas_carrier": "газовоз СПГ",
    "product_tanker": "танкер продуктов",
    "cargo": "сухогруз",
    "tug": "буксир",
    "dredger": "земснаряд",
    "support": "вспомогательное",
    "unknown": "неопознанное",
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
form{display:flex; gap:8px; flex-wrap:wrap; justify-content:center}
input[type=email]{
  flex:1 1 230px; max-width:320px; padding:11px 13px; font:inherit;
  border:1px solid var(--axis); border-radius:8px;
  background:var(--surface); color:var(--ink);
}
input[type=email]:focus{outline:2px solid var(--accent); outline-offset:1px}
.btn{
  padding:11px 20px; font:inherit; font-weight:600; cursor:pointer;
  border:none; border-radius:8px; background:var(--accent); color:#fff;
  text-decoration:none; display:inline-block;
}
.btn:hover{filter:brightness(1.08)}
.btn.ghost{background:transparent; color:var(--accent); border:1px solid var(--accent)}

/* карусель снимков */
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

.meth{columns:2 260px; column-gap:28px; font-size:13.5px; color:var(--ink-2)}
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
    b.textContent = t==='dark' ? 'Светлая тема' : 'Тёмная тема';
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
    boxes.append(("Тепловая активность",
                  "есть" if thermal else "нет",
                  f"горячих линий: {sig.get('hot_trains', 0)}"
                  if thermal else "факелов не зафиксировано"))

    lng = sig.get("lng_loadings")
    pct = sig.get("export_pct_of_norm")
    boxes.append(("Отгрузки СПГ",
                  "—" if lng is None else str(lng),
                  f"{pct}% от доконфликтной нормы" if pct is not None
                  else "за период наблюдения"))

    liq = sig.get("liquid_loadings")
    boxes.append(("Жидкие продукты",
                  "—" if liq is None else str(liq),
                  "конденсат, СПБТ, GTL"))

    dry = sig.get("drycargo_calls")
    proj = sig.get("project_cargo_calls") or 0
    boxes.append(("Завоз грузов",
                  "—" if dry is None else str(dry),
                  f"из них проектных: {proj}"))

    frp = sig.get("frp_sum")
    boxes.append(("Мощность факелов",
                  f"{frp:g}" if frp else "0",
                  "МВт, суммарно по VIIRS"))

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

        conf = {"high": "высокая", "medium": "средняя",
                "low": "низкая", "none": "—"}.get(t.get("confidence"), "—")
        ev = "<br>".join(_esc(e) for e in (t.get("evidence") or [])[:3])
        rows.append(
            f'<tr><td><b>{_esc(t["name"])}</b></td>'
            f'<td class="hide-s">{_esc(t["venture"])}</td>'
            f'<td class="num hide-s">{t.get("mtpa", "—")}</td>'
            f'<td><span class="st"><span class="ic" style="color:{color}">'
            f'{meta["icon"]}</span>{_esc(meta["ru"])}</span>'
            f'{f"<div class=ev>{ev}</div>" if ev else ""}</td>'
            f'<td class="conf">{_esc(conf)}</td></tr>')

    table = (
        '<table><thead><tr><th>Линия</th>'
        '<th class="hide-s">Венчур</th><th class="num hide-s">млн т/год</th>'
        '<th>Статус и обоснование</th><th>Уверен.</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>')
    return table, locked_n


def _shipping_block(ais):
    if not ais:
        return '<p class="hint">AIS за период не собран.</p>'

    now = ais.get("at_berth_now") or []
    done = [e for e in (ais.get("recent") or [])
            if e.get("departed") and (e.get("operation_done")
                                      or e.get("likely_loaded"))]

    out = []
    if now:
        rows = "".join(
            f'<tr><td><b>{_esc(e["vessel"])}</b></td>'
            f'<td>{_esc(CATEGORY_RU.get(e.get("category"), "—"))}</td>'
            f'<td>{_esc(e["berth"])}</td>'
            f'<td class="hide-s">{_esc(e.get("venture", "—"))}</td></tr>'
            for e in now[:12])
        out.append('<table><thead><tr><th>Судно у причала сейчас</th>'
                   '<th>Тип</th><th>Причал</th>'
                   '<th class="hide-s">Направление груза</th></tr></thead>'
                   f'<tbody>{rows}</tbody></table>')
    else:
        out.append('<p class="hint">У грузовых причалов сейчас пусто.</p>')

    if done:
        rows = "".join(
            f'<tr><td><b>{_esc(e["vessel"])}</b></td>'
            f'<td>{_esc(CATEGORY_RU.get(e.get("category"), "—"))}</td>'
            f'<td>{_esc(e["berth"])}</td>'
            f'<td class="num">{e.get("hours", "—")}</td>'
            f'<td class="hide-s">{_esc(e.get("destination") or "не заявлено")}</td>'
            f'</tr>' for e in done[:18])
        out.append('<h2 style="margin-top:22px">Завершённые грузовые операции</h2>'
                   '<p class="hint">Ушедший гружёный газовоз — главное '
                   'доказательство того, что СПГ реально производят, а не '
                   'просто жгут газ в факеле.</p>'
                   '<table><thead><tr><th>Судно</th><th>Тип</th>'
                   '<th>Причал</th><th class="num">Часов</th>'
                   '<th class="hide-s">Ушло на</th></tr></thead>'
                   f'<tbody>{rows}</tbody></table>')
    return "\n".join(out)


def _carousel_block(areas):
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
                   f'alt="Снимок: {_esc(a["title"])}, '
                   f'{_esc(a.get("date") or "дата неизвестна")}">')
        else:
            pic = (f'<div class="car-noimg">{_esc(a.get("error") or "снимок недоступен")}'
                   f'</div>')

        date_txt = a.get("date") or "—"
        age = a.get("age_days")
        age_txt = ""
        if age is not None:
            age_txt = ("сегодня" if age == 0 else
                       "вчера" if age == 1 else f"{age} дн. назад")
            age_txt = f" · {age_txt}"
        cloud_txt = (f" · облачность {a['cloud']}%"
                     if a.get("cloud") is not None else "")

        stat = ""
        if not a.get("error"):
            stat = (f'<div class="car-stat">'
                    f'<div><b>{a.get("count", 0)}</b>судов на снимке</div>'
                    f'<div><b>{a.get("large_count", 0)}</b>крупных, от 200 м</div>'
                    f'<div><b>{a.get("px_size_m", "—")}</b>метров в пикселе</div>'
                    f'</div>')

        warn = ""
        if a.get("warning"):
            warn = f'<div class="caveat">{_esc(a["warning"])}</div>'

        slides += (
            f'<div class="car-slide" role="group" aria-roledescription="слайд" '
            f'aria-label="{i + 1} из {len(items)}: {_esc(a["title"])}">'
            f'{pic}'
            f'<div class="car-cap"><b>{_esc(a["title"])}</b>'
            f'<span class="d">снимок {_esc(date_txt)}{age_txt}{cloud_txt}</span>'
            f'</div>'
            f'<p class="hint" style="margin:4px 0 0">{_esc(a.get("note", ""))}</p>'
            f'{stat}{warn}</div>')

        dots += (f'<button class="car-dot" type="button" data-go="{i}" '
                 f'aria-label="Показать: {_esc(a["title"])}"'
                 f'{" aria-current=&quot;true&quot;" if i == 0 else ""}></button>')

    return (
        '<div class="car">'
        f'<div class="car-track" id="cartrack" tabindex="0" '
        f'aria-label="Снимки областей наблюдения">{slides}</div>'
        '<div class="car-nav">'
        '<button class="car-btn" type="button" data-step="-1">&larr; Назад</button>'
        '<button class="car-btn" type="button" data-step="1">Вперёд &rarr;</button>'
        f'<div class="car-dots">{dots}</div>'
        '</div></div>')


def _chart_block(trend):
    if not trend:
        return '<p class="hint">Данные VIIRS за период отсутствуют.</p>'
    trend = trend[-14:]
    top = max(v for _, v in trend) or 1
    bars = "".join(
        f'<div class="bar" style="height:{max(3, v / top * 100):.1f}%" '
        f'data-tip="{_esc(d)}: {v:g} МВт" '
        f'role="img" aria-label="{_esc(d)}: {v:g} мегаватт"></div>'
        for d, v in trend)
    return (f'<div class="chart">{bars}</div>'
            f'<div class="axis"><span>{_esc(trend[0][0])}</span>'
            f'<span>максимум {top:g} МВт</span>'
            f'<span>{_esc(trend[-1][0])}</span></div>')


def _form_block():
    mail = config.BRAND_CONTACT_EMAIL
    if config.LEAD_FORM_ENDPOINT:
        return (f'<form action="{_esc(config.LEAD_FORM_ENDPOINT)}" method="POST">'
                f'<input type="email" name="email" required '
                f'placeholder="рабочая почта"><input type="hidden" '
                f'name="_subject" value="Ras Laffan monitor — запрос доступа">'
                f'<button class="btn" type="submit">Получить полный отчёт</button>'
                f'</form>')
    subject = "Ras%20Laffan%20monitor%20%E2%80%94%20%D0%B7%D0%B0%D0%BF%D1%80%D0%BE%D1%81"
    return (f'<form action="mailto:{_esc(mail)}" method="GET" '
            f'enctype="text/plain">'
            f'<input type="hidden" name="subject" value="Ras Laffan monitor">'
            f'<input type="email" name="body" required '
            f'placeholder="рабочая почта">'
            f'<button class="btn" type="submit">Получить полный отчёт</button>'
            f'</form>'
            f'<p class="hint" style="margin-top:10px;text-align:center">'
            f'или сразу <a href="{_esc(config.BRAND_CALENDLY)}">'
            f'выбрать время разговора</a> &middot; '
            f'<a href="mailto:{_esc(mail)}?subject={subject}">{_esc(mail)}</a></p>')


# ---------------------------------------------------------------------------
# Главная функция
# ---------------------------------------------------------------------------
def build(sat: dict, ais: dict = None, public: bool = True,
          out_dir: str = None, areas: dict = None) -> str:
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
        age_txt = ("сегодня" if age == 0 else
                   "вчера" if age == 1 else f"{age} дн. назад")
        scene_line = (f"снимок от {_esc(scene['date'])} ({age_txt}), "
                      f"облачность {scene.get('cloud', 0)}%")
    else:
        scene_line = (f"снимки за {_esc(period.get('from', '?'))} — "
                      f"{_esc(period.get('to', '?'))}")

    carousel = _carousel_block(areas or {})
    trains_table, locked_n = _trains_block(trains, teaser)

    gate = ""
    if locked_n:
        gate = (f'<div class="gate"><b>Закрыто {locked_n} из {len(trains)} линий</b>'
                f'<p>Полная версия: все линии комплекса, история по неделям, '
                f'выгрузка в CSV и оповещение при смене статуса.</p>'
                f'{_form_block()}</div>')

    # --- таблица вспомогательных площадок (индикатор стройки) ---
    sites = sat.get("sites") or []
    site_rows_list = []
    for s in sites:
        chg = s.get("changed")
        chg_txt = "—" if chg is None else "{:.0%}".format(chg)
        verdict_txt = ("признаки работ" if s.get("construction_signal")
                       else "без изменений")
        site_rows_list.append(
            f'<tr><td><b>{_esc(s["site"])}</b></td>'
            f'<td class="hide-s">{_esc(s.get("note", ""))}</td>'
            f'<td class="num">{chg_txt}</td>'
            f'<td>{verdict_txt}</td></tr>')
    sites_rows = "".join(site_rows_list)
    sites_block = (
        '<table><thead><tr><th>Площадка</th>'
        '<th class="hide-s">Что это</th><th class="num">Изменено</th>'
        '<th>Вывод</th></tr></thead>'
        f'<tbody>{sites_rows}</tbody></table>') if sites_rows else \
        '<p class="hint">Снимки вспомогательных площадок за период недоступны.</p>'

    caveats = "".join(f'<div class="caveat">{_esc(c)}</div>'
                      for c in cx.get("caveats", []))

    html = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ras Laffan LNG — монитор</title>
<meta name="description" content="Статус линий сжижения Ras Laffan по снимкам
Sentinel-2, тепловым данным VIIRS и трекингу судов AIS. {config.BRAND_NAME}.">
<meta property="og:title" content="Ras Laffan LNG — монитор состояния">
<meta property="og:description" content="Какие линии работают, какие
восстанавливают, сколько СПГ реально вывозят. По открытым данным.">
<style>{CSS}</style>
</head>
<body>
<button class="toggle" id="tg" type="button">Тёмная тема</button>
<div class="wrap">

<header>
  <div class="brand">
    <b>{_esc(config.BRAND_NAME)}</b>
    <span>{_esc(config.BRAND_TAGLINE)}</span>
  </div>
  <h1>Ras Laffan LNG: что работает, что восстанавливают</h1>
  <p class="sub">Крупнейший в мире комплекс сжижения газа после удара
  в марте 2026. Состояние каждой линии — по трём независимым открытым
  источникам, без инсайда и без подписки на платные терминалы.</p>
  <p class="stamp">Обновлено {_esc(stamp)} &middot; {scene_line}</p>
</header>

<section class="card">
  <div class="verdict">
    <div class="hero">
      <div class="n">{"—" if util is None else f"{util:g}"}<small>%</small></div>
      <div class="lbl">мощности в работе<br>
        {cap.get("producing_mtpa", 0):g} из
        {cap.get("producing_mtpa", 0) + cap.get("down_mtpa", 0):g} млн т/год</div>
    </div>
    <div class="vnote">
      {_chip(cx["status"], cx["ru"])}
      <p style="margin:12px 0 0">{_esc(cx["note"])}</p>
      {caveats}
    </div>
  </div>
</section>

<section class="card">
  <h2>Три сигнала, из которых складывается вывод</h2>
  <p class="hint">Факел доказывает, что газ горит, а не что завод работает:
  так же горит продувка перед ремонтом и пусконаладка. Производство
  доказывает отгрузка. А завоз грузов без отгрузки — это восстановление.</p>
  {_signals_block(cx)}
</section>

<section class="card">
  <h2>Статус линий сжижения</h2>
  <p class="hint">Все зоны разобраны по одному снимку — {scene_line} — и
  поэтому сопоставимы между собой. Статус определяется по тепловой
  сигнатуре в коротковолновом инфракрасном канале, индексу выгорания и
  изменению облика площадки относительно опорного снимка
  {_esc(scene.get("baseline_date", "февраля 2026"))}, сделанного до
  остановки производства.</p>
  {trains_table}
  {gate}
</section>

<section class="card">
  <h2>Снимки областей наблюдения</h2>
  <p class="hint">Суда считаются прямо по снимку: вода поглощает ближний
  инфракрасный свет и выглядит почти чёрной, а палуба отражает — корпус
  виден светлой чёрточкой на тёмной воде. Транспондер можно выключить,
  со снимка судно не спрячешь. У каждой области своя дата съёмки: они
  разнесены на тысячи километров и попадают в разные витки спутника.</p>
  {carousel}
</section>

<section class="card">
  <h2>Движение судов</h2>
  <p class="hint">Данные AIS, который суда обязаны передавать по требованию
  ИМО. Тип судна и тип причала вместе показывают, что именно происходит:
  вывоз продукта или завоз оборудования.</p>
  {_shipping_block(ais)}
</section>

<section class="card">
  <h2>Признаки работ на берегу</h2>
  <p class="hint">Площадка складирования модулей — прямой индикатор стройки:
  при активных работах она заполняется блоками оборудования, появляются
  новые покрытия и проезды.</p>
  {sites_block}
</section>

<section class="card">
  <h2>Суммарная мощность факелов по дням, МВт</h2>
  <p class="hint">Прибор VIIRS измеряет мощность теплового излучения.
  Рост означает увеличение сжигания газа, то есть рост загрузки.</p>
  {_chart_block(sat.get("firms_trend"))}
</section>

<section class="card">
  <h2>Методика и честные ограничения</h2>
  <div class="meth">
    <p><b>Sentinel-2</b>, Евросоюз. Съёмка каждые 5 дней, 10 метров на
    пиксель. Работающее оборудование и факел ярко светятся в канале B12
    (2190 нм) — это позволяет различать линии по отдельности.</p>
    <p><b>VIIRS</b>, NASA. Тепловые аномалии несколько раз в сутки с
    измерением мощности в мегаваттах. Разрешение 375 м: отдельные линии
    не разделяет, общий уровень сжигания показывает.</p>
    <p><b>AIS</b>. Открытый эфир судовых транспондеров. Приходы, уходы,
    длительность стоянки, заявленное направление.</p>
    <p><b>Судно нельзя привязать к линии.</b> СПГ со всех линий поступает
    в общие резервуары, оттуда на общие причалы. Связь теряется внутри
    завода. Этого не делает никто, включая платные терминалы. Привязка
    идёт до уровня причала и венчура.</p>
    <p><b>Завоз не указывает объект.</b> На комплексе одновременно идут
    ремонт линий, восстановление Barzan, ремонт Pearl GTL и стройка
    расширения North Field East. Проектный груз может идти на любой
    из них.</p>
    <p><b>Грузовики со снимка не видны.</b> Фура занимает 1–2 пикселя.
    Подвоз техники определяется по судам, работы на берегу — по
    изменению облика площадок.</p>
    <p><b>Осадка в AIS ненадёжна.</b> Поле заполняется экипажем вручную
    и часто остаётся несвежим. Основной признак погрузки — длительность
    стоянки у грузового причала.</p>
    <p><b>Выводы автоматические.</b> Разрешение 10 м даёт гипотезу, а не
    факт. У каждой линии указана уверенность; спорные случаи проверяются
    по снимку глазами.</p>
  </div>
</section>

<section class="card" style="text-align:center">
  <h2>Нужен такой же монитор по вашему активу?</h2>
  <p class="hint" style="margin:6px auto 18px">Тот же метод работает по
  любому НПЗ, терминалу или заводу СПГ в мире: состояние объекта,
  загрузка, отгрузки, признаки работ. Настройка под конкретный актив —
  от нескольких дней.</p>
  {_form_block()}
  <p class="hint" style="margin-top:18px">
    <a class="btn ghost" href="{_esc(config.BRAND_CALENDLY)}">Поговорить 30 минут</a>
  </p>
</section>

<footer>
  <p>{_esc(config.BRAND_SIGNATURE)} &middot; {_esc(config.BRAND_NAME)} &middot;
  <a href="mailto:{_esc(config.BRAND_CONTACT_EMAIL)}">{_esc(config.BRAND_CONTACT_EMAIL)}</a>
  &middot; <a href="{_esc(config.BRAND_LINKEDIN)}">LinkedIn</a></p>
  <p>Источники: Copernicus Sentinel-2, NASA FIRMS VIIRS, AIS.
  Машиночитаемая версия: <a href="data.json">data.json</a>.
  Это не инвестиционная рекомендация.</p>
</footer>

</div>
<script>{JS}</script>
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
        "areas": {k: {kk: vv for kk, vv in v.items() if kk != "vessels"}
                  for k, v in (areas or {}).items()},
        "disclaimer": ("Автоматические выводы по открытым данным. "
                       "Привязка судна к конкретной линии сжижения "
                       "физически невозможна."),
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
