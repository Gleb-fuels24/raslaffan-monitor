"""
bot.py — главный файл. Запускает всё и собирает отчёт.

Команды (вводятся в терминале, в папке проекта):

    python bot.py test       — проверка, что все ключи работают
    python bot.py calibrate  — один раз: снимок с сеткой для настройки координат
    python bot.py dates      — когда спутник снимал объект и с какой облачностью
    python bot.py aistest    — быстрая проверка AIS: сырые ответы за 30 сек
    python bot.py straits    — калибровочные снимки проливов с сеткой
    python bot.py video      — собрать ролик о продукте
    python bot.py schedule   — поставить ежедневный запуск в полночь
    python bot.py satellite  — только спутниковый анализ линий
    python bot.py ais        — только слежение за судами (15 минут)
    python bot.py report     — полный цикл: спутник + суда + отчёт в Telegram
    python bot.py site       — полный цикл + сборка сайта в папку docs/
    python bot.py publish    — то же, что site, плюс git push на GitHub Pages
"""

import os
import re
import subprocess
import sys
import datetime as dt

import requests

import config


# ---------------------------------------------------------------------------
# Показать пользователю готовый файл
# ---------------------------------------------------------------------------
def show_file(path: str, open_it: bool = True) -> str:
    """
    Печатает ПОЛНЫЙ путь к файлу и открывает его.

    Раньше печаталось `data\\chips\\calibration_grid.png` — путь
    относительно папки, из которой запущен бот. Такой путь понятен
    программе, но человеку он не говорит, куда смотреть, и файл
    приходится искать вручную. Теперь печатается полный путь, который
    можно скопировать и вставить в адресную строку Проводника, а сам
    файл открывается сразу.

    open_it=False — для ночного запуска: открывать окна на машине,
    за которой никто не сидит, незачем.
    """
    full = os.path.abspath(path)
    print(f"\nГотово: {full}")
    if not os.path.exists(full):
        print("[!] Но файла по этому пути нет — что-то пошло не так.")
        return full

    size_kb = os.path.getsize(full) / 1024
    print(f"Размер: {size_kb:.0f} КБ")
    print(f"Открыть папку с ним:  explorer \"{os.path.dirname(full)}\"")

    # Предупреждение о двойнике.
    #
    # Бот пишет результаты в подпапки (data\chips, docs и так далее).
    # Если файл с тем же именем лежит ещё и в корне проекта — это
    # копия из прошлых запусков, оставленная вручную. Бот её не
    # обновляет, и открыв её, человек видит старые данные и считает,
    # что программа не сработала. Ошибка незаметная: оба файла
    # называются одинаково.
    root = os.path.abspath(os.path.dirname(__file__) or ".")
    twin = os.path.join(root, os.path.basename(full))
    if os.path.exists(twin) and os.path.abspath(twin) != full:
        age = dt.date.fromtimestamp(os.path.getmtime(twin)).isoformat()
        print()
        print(f"[!] Внимание: в корне проекта лежит СТАРАЯ копия с тем же")
        print(f"    именем, от {age}:")
        print(f"      {twin}")
        print(f"    Бот её не обновляет. Если открыть её, увидите старые")
        print(f"    данные. Удалить:  del \"{os.path.basename(twin)}\"")

    if open_it:
        try:
            os.startfile(full)                        # только Windows
            print("Файл открыт в программе просмотра.")
        except AttributeError:
            # На Linux и macOS os.startfile отсутствует.
            import subprocess as _sp
            import platform as _pl
            opener = "open" if _pl.system() == "Darwin" else "xdg-open"
            try:
                _sp.Popen([opener, full],
                          stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
            except OSError:
                pass
        except OSError as exc:
            print(f"(открыть автоматически не вышло: {exc})")
    return full


# ---------------------------------------------------------------------------
# Отправка в Telegram
# ---------------------------------------------------------------------------
def _net_hint(exc) -> str:
    """
    Переводит сетевую ошибку на человеческий язык.

    getaddrinfo failed означает, что система не смогла преобразовать имя
    сайта в адрес. Если это происходит сразу со всеми сервисами, дело не
    в них, а в сети на вашей стороне.
    """
    s = str(exc)
    if "getaddrinfo" in s or "NameResolutionError" in s:
        return ("нет связи с интернетом или не отвечает DNS. "
                "Проверьте: ping dataspace.copernicus.eu")
    if "timed out" in s.lower() or "timeout" in s.lower():
        return "сервис не ответил вовремя — возможно, перегружен"
    return s[:200]


def tg_text(text: str) -> bool:
    """
    Отправляет отчёт в Telegram.

    Никогда не бросает исключение наружу: уведомление — не главная
    работа бота, и обрыв связи с Telegram не должен ронять весь
    обход вместе с уже собранными данными.
    """
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        return False
    try:
        for i in range(0, len(text), 4000):
            requests.post(
                f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                data={"chat_id": config.TELEGRAM_CHAT_ID,
                      "text": text[i:i + 4000],
                      "parse_mode": "HTML"},
                timeout=60,
            )
        return True
    except Exception as exc:                          # noqa: BLE001
        print(f"[!] Telegram недоступен: {_net_hint(exc)}")
        return False


def tg_photo(path: str, caption: str = "") -> bool:
    """Отправляет картинку. Как и tg_text, ошибку наружу не выпускает."""
    if not config.TELEGRAM_BOT_TOKEN or not os.path.exists(path):
        return False
    try:
        with open(path, "rb") as fh:
            requests.post(
                f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendPhoto",
                data={"chat_id": config.TELEGRAM_CHAT_ID,
                      "caption": caption[:1000]},
                files={"photo": fh},
                timeout=120,
            )
        return True
    except Exception as exc:                          # noqa: BLE001
        print(f"[!] Telegram не принял картинку: {_net_hint(exc)}")
        return False


# ---------------------------------------------------------------------------
# Сборка отчёта
# ---------------------------------------------------------------------------
STATUS_RU = {
    "PRODUCING": "producing",
    "DESTROYED": "destroyed",
    "REBUILDING": "works under way",
    "IDLE": "idle, intact",
    "NO_DATA": "no data",
    "ERROR": "error",
}
STATUS_ICON = {
    "PRODUCING": "[+]", "DESTROYED": "[X]", "REBUILDING": "[~]",
    "IDLE": "[o]", "NO_DATA": "[?]", "ERROR": "[!]",
}
VESSEL_LABEL = {
    "gas_carrier": "LNG carrier", "product_tanker": "product tanker",
    "cargo": "dry cargo", "tug": "tug", "dredger": "dredger",
    "support": "support", "unknown": "unidentified",
}


def build_report(sat: dict, ais: dict = None, areas: dict = None,
                 intel: dict = None, fin: dict = None,
                 heat_res: dict = None) -> str:
    import fusion

    fused = fusion.run(sat, ais)
    cx, cap = fused["complex"], fused["capacity"]
    sat = fused["sat"]

    today = dt.date.today().isoformat()
    lines = [
        "<b>RAS LAFFAN LNG — ASSET MONITOR</b>",
        f"Report as of {today}",
    ]

    # --- Сведения о снимке: что именно мы смотрим ---
    scene = sat.get("scene")
    if scene:
        age = scene["age_days"]
        if age == 0:
            age_txt = "today"
        elif age == 1:
            age_txt = "yesterday"
        else:
            age_txt = f"{age} days ago"
        lines.append(f"<b>Image of {scene['date']}</b> ({age_txt}), "
                     f"cloud {scene['cloud']}%")
        if scene.get("prev_date"):
            lines.append(f"Previous usable image: {scene['prev_date']}")
        lines.append(f"Compared with the baseline image of "
                     f"{scene['baseline_date']}")
        if age >= 7:
            lines.append("! NOTE: the image is over a week old. The satellite "
                         "revisits this point about every 5 days, so the last "
                         "passes were covered by cloud")
    elif sat.get("scene_error"):
        lines.append(f"! {sat['scene_error']}")
    else:
        lines.append(f"Acquisition window: {sat['period']['from']} .. {sat['period']['to']}")

    lines += [
        "",
        f"<b>COMPLEX VERDICT: {cx['label'].upper()}</b>",
        cx["note"],
    ]
    for c in cx.get("caveats", []):
        lines.append(f"  ! {c}")

    sig = cx["signals"]
    lines += [
        "",
        "<b>THE THREE SIGNALS</b>",
        f"  Heat on site: {'yes' if sig.get('thermal') else 'no'}"
        f" (hot trains: {sig.get('hot_trains', 0)}, "
        f"total FRP: {sig.get('frp_sum', 0)} MW)",
    ]
    if sig.get("lng_loadings") is None:
        lines.append("  LNG loadings: no AIS data")
    else:
        pct = sig.get("export_pct_of_norm")
        lines.append(
            f"  LNG loadings: {sig['lng_loadings']}"
            + (f" ({pct}% of the pre-conflict norm of "
               f"~{sig.get('norm_cargoes')} cargoes)" if pct is not None else ""))
        lines.append(f"  Liquid products: {sig.get('liquid_loadings', 0)}, "
                     f"sulphur: {sig.get('sulphur_loadings', 0)}")
        lines.append(f"  Dry cargo calls: {sig.get('drycargo_calls', 0)}, "
                     f"of which project cargo: {sig.get('project_cargo_calls', 0)}")

    if heat_res:
        import heat as heat_mod
        lines += ["", "<b>CONTRACTOR ACTIVITY INDEX</b>"]
        lines += heat_mod.explain(heat_res)

    if intel:
        lines += ["", "<b>LAST 24 HOURS OF INTELLIGENCE</b>",
                  f"  News on the asset: {intel.get('news_count', 0)}",
                  f"  Award mentions: {intel.get('award_total', 0)}, "
                  f"hiring mentions: {intel.get('hiring_total', 0)}"]
        t = intel.get("tone")
        if t:
            lines.append(f"  Tone of coverage: {t.get('avg')} "
                         f"(2-week change: {t.get('trend'):+})")
        for a in (intel.get("news") or [])[:5]:
            lines.append(f"    · {a['title'][:110]}")

    if fin:
        import finance as fin_mod
        rows = fin_mod.summarize(fin)
        if rows:
            lines += ["", "<b>CONTRACTOR FILINGS</b>"]
            lines += [f"  {r}" for r in rows]

    if areas:
        lines += ["", "<b>VESSELS ON THE IMAGERY</b>",
                  "<i>Each area carries its own date: they are thousands of "
                  "kilometres apart and fall into different orbits.</i>"]
        for a in areas.values():
            if a.get("error"):
                lines.append(f"  {a['title']}: {a['error']}")
            else:
                lines.append(
                    f"  {a['title']} — image {a['date']} "
                    f"({a['age_days']} days ago): {a['count']} vessels, "
                    f"{a['large_count']} of them large "
                    f"({a['px_size_m']} m/pixel)")

    lines.append("")
    lines.append("<b>LIQUEFACTION TRAIN STATUS</b>")

    for venture in ("QE LNG South", "QE LNG North"):
        rows = [t for t in sat["trains"] if t["venture"] == venture]
        if not rows:
            continue
        lines.append(f"\n<u>{venture}</u>")
        for t in rows:
            icon = STATUS_ICON.get(t["status"], "[?]")
            ru = STATUS_RU.get(t["status"], t["status"])
            conf = {"high": "high", "medium": "medium",
                    "low": "low", "none": "—"}.get(t["confidence"], "—")
            lines.append(
                f"{icon} <b>{t['name']}</b> ({t['mtpa']} mtpa) — {ru}"
                f" · confidence: {conf}")
            for ev in t["evidence"]:
                lines.append(f"      · {ev}")

    if cap.get("utilisation_pct") is not None:
        lines += [
            "",
            "<b>CAPACITY ESTIMATE</b>",
            f"Running:     {cap['producing_mtpa']} mtpa",
            f"Out of service: {cap['down_mtpa']} mtpa",
            f"Site utilisation: {cap['utilisation_pct']}%",
        ]

    # --- Площадки: признаки работ ---
    sites = sat.get("sites") or []
    flagged = [s for s in sites if s.get("construction_signal")]
    if sites:
        lines += ["", "<b>SIGNS OF WORKS ONSHORE</b>"]
        for s in sites:
            chg = s.get("changed")
            chg_txt = "no image" if chg is None else f"{chg:.0%} changed"
            mark = " <- signs of works" if s.get("construction_signal") else ""
            lines.append(f"  {s['site']}: {chg_txt}{mark}")
        if flagged:
            lines.append("  (trucks are invisible on the image — equipment "
                         "delivery is tracked by ship)")

    # --- Причалы по снимку: независимая проверка AIS ---
    berths = sat.get("berths") or []
    occupied = [b for b in berths if b.get("occupied")]
    if berths:
        lines += ["", "<b>BERTHS ON THE IMAGE (independent of AIS)</b>",
                  f"  Berths occupied: {len(occupied)} of {len(berths)}"]
        for b in occupied:
            lines.append(f"  · {b['berth']} ({b['kind']}): "
                         f"vessel visible, {b['vessel_px']} pixels")

    trend = sat.get("firms_trend") or []
    if trend:
        lines += ["", "<b>THERMAL ACTIVITY (VIIRS, total FRP, MW)</b>"]
        for day, frp in trend[-14:]:
            # Пропуск и ноль различаются: «не смотрели» против
            # «смотрели, не горело».
            if frp is None:
                lines.append(f"{day}  no data")
                continue
            bar = "#" * min(int(frp / 5), 30)
            lines.append(f"{day}  {frp:7.1f}  {bar}")

    if not ais or not (ais.get("at_berth_now") or ais.get("recent")):
        lines += ["", f"<b>{config.AIS_UNAVAILABLE_TITLE}</b>",
                  config.AIS_UNAVAILABLE_TEXT]

    if ais:
        lines += ["", "<b>VESSELS</b>",
                  f"At cargo berths now: {len(ais['at_berth_now'])}"]
        for ev in ais["at_berth_now"]:
            cat = VESSEL_LABEL.get(ev.get("category"), "—")
            lines.append(f"  · {ev['vessel']} ({cat}) — "
                         f"{ev['berth']} ({ev['venture']})")

        done = [e for e in ais["recent"]
                if e.get("departed") and (e.get("operation_done")
                                          or e.get("likely_loaded"))]
        if done:
            lines.append(f"\nCompleted cargo operations in {ais['days']} d:")
            for e in done[:20]:
                cat = VESSEL_LABEL.get(e.get("category"), "—")
                dest = e.get("destination") or "destination not declared"
                lines.append(
                    f"  · {e['vessel']} ({cat}) — {e['berth']}, "
                    f"{e['hours']} h, bound for: {dest}")

        by_kind = ais.get("by_kind", {})
        if by_kind:
            lines.append("\nBy berth type:")
            kind_en = {"lng": "LNG", "liquid": "liquid products",
                       "sulphur": "sulphur", "drycargo": "dry cargo"}
            for k, st in sorted(by_kind.items()):
                lines.append(f"  {kind_en.get(k, k)}: {st['calls']} calls, "
                             f"{st['completed']} operations completed")

        lines += [
            "",
            "<i>Tying vessels to individual trains is impossible: LNG from "
            "every train goes into shared storage and out over shared berths. "
            "What is shown is vessel -> berth -> venture.</i>",
        ]

    lines += [
        "",
        "<i>Sources: Sentinel-2 (10 m, Copernicus), VIIRS (375 m, NASA FIRMS), "
        "AIS (aisstream.io), GDELT, SEC EDGAR. Conclusions are automatic — "
        "check them against the imagery.</i>",
    ]
    return "\n".join(lines)


def save_report(text: str) -> str:
    os.makedirs(config.REPORTS_DIR, exist_ok=True)
    path = os.path.join(config.REPORTS_DIR,
                        f"raslaffan_{dt.date.today().isoformat()}.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"<html><meta charset='utf-8'><body><pre>{text}</pre></body></html>")
    return path


def plain(text: str) -> str:
    """Убирает HTML-теги, чтобы печатать отчёт в терминале."""
    for tag in ("<b>", "</b>", "<u>", "</u>", "<i>", "</i>"):
        text = text.replace(tag, "")
    return text


# ---------------------------------------------------------------------------
# Команды
# ---------------------------------------------------------------------------
def cmd_calibrate():
    import sentinel
    print("Скачиваю общий снимок Ras Laffan с координатной сеткой ...")
    path = sentinel.make_calibration_image()
    show_file(path)
    print()
    print("Найдите на снимке реальные линии и причалы и впишите их")
    print("координаты в config.py (словари TRAINS и BERTHS).")
    tg_photo(path, "Калибровочная сетка Ras Laffan")


def cmd_dates(days=40):
    """
    Показывает, когда спутник реально снимал Ras Laffan.

    Полезно, чтобы понимать темп обновления данных по вашему объекту:
    видно и сами даты, и облачность каждого кадра, и промежутки между
    пролётами.
    """
    import sentinel
    import config as cfg

    print(f"Ищу снимки за последние {days} дн. "
          f"с облачностью ниже {cfg.MAX_CLOUD_COVER}% ...\n")
    scenes = sentinel.find_scenes(cfg.RLIC_BBOX,
                                  (dt.date.today() - dt.timedelta(days=days)).isoformat(),
                                  dt.date.today().isoformat())
    if not scenes:
        print("Пригодных снимков не нашлось. Попробуйте поднять "
              "MAX_CLOUD_COVER в config.py или увеличить период.")
        return []

    print(f"{'Дата':<12} {'Облачность':>11}  {'Возраст':>9}  Промежуток")
    print("-" * 52)
    today = dt.date.today()
    prev = None
    for s in scenes:
        d = dt.date.fromisoformat(s["date"])
        gap = f"{(prev - d).days} дн." if prev else "—"
        print(f"{s['date']:<12} {round(s.get('cloud') or 0, 1):>10}%  "
              f"{(today - d).days:>6} дн.  {gap}")
        prev = d

    gaps = [(dt.date.fromisoformat(scenes[i]["date"])
             - dt.date.fromisoformat(scenes[i + 1]["date"])).days
            for i in range(len(scenes) - 1)]
    if gaps:
        print(f"\nВсего снимков: {len(scenes)}. "
              f"Средний промежуток: {sum(gaps) / len(gaps):.1f} дн., "
              f"самый большой: {max(gaps)} дн.")
    print(f"Бот возьмёт самый верхний: {scenes[0]['date']}")

    # Отброшенные по облачности — обязательно показать. Без них
    # непонятно главное: спутник не пролетал или пролетал, но сквозь
    # облака. Это разные ответы на вопрос «почему снимок не свежий».
    rejected = [r for r in sentinel.LAST_REJECTED]
    if rejected:
        print(f"\nБыли пролёты, забракованные облачностью "
              f"(порог {cfg.MAX_CLOUD_COVER}%):")
        for r in rejected[:12]:
            newer = " — СВЕЖЕЕ выбранного" if r["date"] > scenes[0]["date"] else ""
            print(f"  {r['date']}  облачность {r['cloud']}%{newer}")
        print("\nЕсли свежий пролёт забракован, а посмотреть надо, "
              "поднимите\nMAX_CLOUD_COVER в config.py — но помните, "
              "что под облаком\nне видно ни судов, ни стройки.")
    else:
        print("\nПролётов, забракованных облачностью, за период не было:")
        print("даты выше — это все пролёты, какие были.")
    return scenes


def schedule_time(explicit: str = None):
    """
    Считает, на какое время ставить ежедневный запуск.

    Логика: запускаться надо тогда, когда снимок уже опубликован, плюс
    полчаса запаса. Время публикации не угадывается, а измеряется по
    открытому каталогу Copernicus: в нём у каждого продукта есть и
    момент съёмки, и момент выкладки.

    Дальше время переводится из Гринвича в часы ВАШЕГО компьютера —
    Планировщик заданий Windows понимает только местное время.

    explicit — если передать "14:30", расчёт не делается и берётся оно.
    """
    import sentinel

    if explicit:
        return {"local": explicit, "info": None,
                "why": "время задано вручную"}

    info = sentinel.publication_timing()
    ready_utc = info["ready_utc_h"] + 0.5          # +30 минут запаса

    # Смещение часов этого компьютера относительно Гринвича.
    now = dt.datetime.now()
    offset_h = (now.astimezone().utcoffset().total_seconds() / 3600.0)

    local_h = (ready_utc + offset_h) % 24
    hh = int(local_h)
    mm = int(round((local_h - hh) * 60))
    if mm >= 60:                                   # 13:60 -> 14:00
        hh, mm = (hh + 1) % 24, 0

    return {
        "local": f"{hh:02d}:{mm:02d}",
        "info": info,
        "offset_h": offset_h,
        "why": (f"съёмка ~{info['overpass_utc_h']:.1f} UTC + публикация "
                f"{info['lag_h']:.1f} ч + 30 мин запаса"),
    }


def cmd_schedule(when: str = None):
    """
    Ставит ежедневный запуск на момент публикации снимка плюс 30 минут.

    На Windows пользуется Планировщиком заданий — штатным средством
    системы. Задача называется RasLaffanMonitor; её видно в
    Планировщике и там же можно отключить.

    Время считается, а не задаётся: бот спрашивает у каталога
    Copernicus, когда снимки этой зоны реально выкладываются, и ставит
    запуск через полчаса после этого. Так в отчёт всегда попадает
    снимок СЕГОДНЯШНЕГО дня, а не вчерашнего.

    Можно задать время вручную:  python bot.py schedule 14:30
    """
    import subprocess as sp
    import platform

    folder = os.path.abspath(os.path.dirname(__file__) or ".")
    bat = os.path.join(folder, "run_daily.bat")

    # Время можно передать вторым словом: python bot.py schedule 14:30
    if when is None and len(sys.argv) > 2 and ":" in sys.argv[2]:
        when = sys.argv[2]

    plan = schedule_time(when)
    at = plan["local"]
    info = plan["info"]

    print("Расчёт времени запуска")
    if info:
        kind = "измерено" if info["measured"] else "оценка"
        print(f"  съёмка зоны:        {info['overpass_utc_h']:.2f} UTC")
        print(f"  задержка публикации: {info['lag_h']:.2f} ч ({kind}: "
              f"{info['note']})")
        print(f"  данные готовы:      {info['ready_utc_h']:.2f} UTC")
        print(f"  плюс запас:         30 мин")
        print(f"  часовой пояс этого компьютера: UTC"
              f"{plan['offset_h']:+.1f}")
    print(f"  ИТОГО запуск в {at} по часам этого компьютера")
    print()

    if platform.system() != "Windows":
        hh, mm = at.split(":")
        print("Не Windows. Добавьте строку в планировщик cron:")
        print(f"  {int(mm)} {int(hh)} * * * cd {folder} && "
              f"python3 bot.py publish")
        return

    if not os.path.exists(bat):
        print(f"[!] Не найден файл {bat}")
        return

    cmd = ["schtasks", "/Create", "/TN", "RasLaffanMonitor",
           "/TR", f'"{bat}"', "/SC", "DAILY", "/ST", at, "/F"]
    try:
        r = sp.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        print("[!] Не нашёл schtasks — это штатная команда Windows.")
        return

    if r.returncode == 0:
        print(f"Ежедневный запуск поставлен на {at}.")
        print(f"Почему именно это время: {plan['why']}.")
        print("Задача называется RasLaffanMonitor — найдёте её в")
        print("Планировщике заданий Windows, там же можно отключить.")
        print()
        print("Проверить, что задача создалась:")
        print("  schtasks /Query /TN RasLaffanMonitor")
        print("Запустить прямо сейчас, не дожидаясь этого часа:")
        print("  schtasks /Run /TN RasLaffanMonitor")
        print("Удалить:")
        print("  schtasks /Delete /TN RasLaffanMonitor /F")
        print()
        print("Журнал каждого запуска: data\\logs\\daily.log")
        print()
        print("Учтите: компьютер должен быть включён в это время.")
        print("Если он ночью спит, задача не сработает — в свойствах")
        print("задачи есть галочка «Выполнить при следующем включении».")
    else:
        print("[!] Не удалось создать задачу.")
        print((r.stderr or r.stdout)[:400])
        print()
        print("Чаще всего это нехватка прав: запустите командную строку")
        print("от имени администратора и повторите.")


def cmd_video():
    """Собирает ролик, объясняющий продукт."""
    import make_video
    path = make_video.render()
    if path:
        show_file(path)
        tg_photo(path, "Ролик о продукте")
    return path


def cmd_outreach():
    """
    Собирает заготовки писем по сегодняшним данным.

    Делает полный цикл (снимки, суда, пресса, отчётность, индекс), а
    потом подставляет свежие цифры в тексты писем. Ничего не
    отправляет: письма складываются в файл, отправку вы делаете сами.
    """
    import outreach as outreach_mod
    import heat as heat_mod
    import fusion

    sat, ais, areas_res, intel_res, fin_res = _full_cycle()
    if not usable(sat):
        print("Нечего писать: данных за сегодня нет.")
        explain_empty(sat)
        return None

    heat_res = heat_mod.compute(sat=sat, ais=ais, intel=intel_res,
                                fused=fusion.run(sat, ais), areas=areas_res,
                                ai=sat.get("ai"))
    text, path = outreach_mod.run(sat, ais, intel_res, fin_res, heat_res)
    print(text)
    print()
    print(f"Сохранено: {path}")
    if not getattr(config, "SITE_URL", ""):
        print()
        print("[!] В config.py не заполнен SITE_URL — в письмах вместо")
        print("    ссылки стоит заглушка. Впишите адрес витрины после")
        print("    того, как включите GitHub Pages.")
    return path


def cmd_contacts():
    """
    Контакты лиц, принимающих решения, через Lusha.

    Две команды вместо одной — сознательно:

      python bot.py contacts          ищет людей, показывает список.
                                      Стоит 1 кредит за 25 найденных.
      python bot.py contacts reveal   раскрывает почты у найденных.
                                      Стоит 1 кредит за человека.

    Разделено потому, что раскрытие необратимо: кредит списывается
    сразу, и вернуть его нельзя. Сначала вы смотрите, те ли это люди,
    и только потом платите за их почты.

    Найденное складывается в data/contacts/ и в Telegram, если он
    настроен. На сайт не идёт: это персональные данные.
    """
    import lusha

    do_reveal = len(sys.argv) > 2 and sys.argv[2].lower() == "reveal"
    cache = lusha.load_cache()

    if do_reveal:
        people = [p for p in cache["contacts"].values()
                  if not p.get("revealed")]
        if not people:
            print("Раскрывать нечего: сначала выполните "
                  "python bot.py contacts")
            return None

        limit = config.LUSHA_MAX_CREDITS_PER_RUN
        ids = [p["id"] for p in people][:limit]
        print(f"Найдено нераскрытых: {len(people)}. "
              f"Раскрываю {len(ids)} — это {len(ids)} кредитов.")
        try:
            res = lusha.reveal(ids, cache=cache)
        except RuntimeError as exc:
            print(f"[!] {exc}")
            return None

        if res.get("note"):
            print(res["note"])
        if not res["results"]:
            return None
        print(f"Списано кредитов: {res['credits_charged']}")
        text = lusha.render(res["results"], "DECISION MAKERS — CONTACTS")
    else:
        print("Ищу компании ...")
        try:
            comps = lusha.find_companies(cache=cache)
            for c in comps:
                print(f"  {c['name']} ({c['domain']})")
            print("Ищу людей ...")
            res = lusha.find_contacts([c["id"] for c in comps], cache=cache)
        except RuntimeError as exc:
            print(f"[!] {exc}")
            return None

        if not res["results"]:
            print("Никого не нашлось. Проверьте LUSHA_TARGET_DOMAINS и "
                  "LUSHA_JOB_TITLES в config.py — возможно, фильтры "
                  "слишком узкие.")
            return None

        print(f"Найдено: {len(res['results'])} из {res['total']} "
              f"подходящих. Списано кредитов: {res['credits_charged']}")
        text = lusha.render(res["results"], "DECISION MAKERS — SHORTLIST")
        text += ("\nКонтакты пока НЕ раскрыты. Чтобы раскрыть почты:\n"
                 f"  python bot.py contacts reveal\n"
                 f"Это спишет по 1 кредиту за человека, не больше "
                 f"{config.LUSHA_MAX_CREDITS_PER_RUN} за запуск.\n")

    print()
    print(text)
    path = lusha.save(text)
    print(f"Сохранено: {path}")
    print(f"Всего потрачено кредитов за всё время: "
          f"{lusha.load_cache()['credits_spent']}")
    tg_text("<b>DECISION MAKERS</b>\n<pre>" + text[:3500] + "</pre>")
    return path


def cmd_aicheck():
    """
    Разовая проверка снимков моделью, без полного цикла.

    Нужна, чтобы убедиться, что ключи работают и модель отвечает, не
    дожидаясь ночного запуска. Делает спутниковый анализ и сверяет
    его результат с тем, что видит модель.
    """
    import analyze
    import ai_check

    print("Спутниковый анализ ...")
    try:
        sat = analyze.run(days_back=14)
    except Exception as exc:                          # noqa: BLE001
        print(f"[!] {_net_hint(exc)}")
        return None

    print("Спрашиваю модель ...")
    res = ai_check.run(sat)
    if not res.get("enabled"):
        print(f"[!] Проверка не сделана: {res.get('note')}")
        return None

    print()
    print(f"Опрошено объектов: {len(res['checks'])}, "
          f"ответил: {', '.join(res['providers']) or 'никто'}")
    print(f"Совпало: {res['agreed']}, разошлось: {res['disputed']}")
    print()
    for c in res["checks"]:
        mark = {True: "=", False: "x", None: "?"}.get(c.get("agree"), "?")
        print(f"[{mark}] {c['object']}")
        print(f"      код:   {c.get('code')}")
        print(f"      модель: {c.get('ai') or c.get('error')}")
        if c.get("ai_note"):
            print(f"      «{c['ai_note']}»")
        if c.get("tried"):
            print(f"      попытки: {'; '.join(c['tried'])}")
    print()
    print("Знак x — расхождение. Это не поломка, а место, куда стоит")
    print("посмотреть глазами: код и модель видят зону по-разному.")
    return res


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_args(argv):
    """
    Разбирает хвост команды: что дата, что название объекта.

    Нужно потому, что и дата, и ключ объекта содержат дефис, и
    различать их по этому признаку нельзя: «ras-laffan» не дата.
    Поэтому дата узнаётся по форме ГГГГ-ММ-ДД, всё прочее считается
    названием объекта, в каком бы порядке его ни написали.
    """
    dates = [a for a in argv if _DATE_RE.match(a)]
    rest = [a for a in argv if not _DATE_RE.match(a)]
    return (rest[0] if rest else None,
            dates[0] if dates else None,
            dates[1] if len(dates) > 1 else None)


def _pick_asset(name):
    """Объект из списка наблюдения с понятной ошибкой, если не нашёлся."""
    import incidents

    asset = incidents.find_asset(name)
    if asset is None:
        print(f"[!] Объект «{name}» в списке наблюдения не найден.")
        print("    Есть такие:")
        for a in config.INCIDENT_WATCHLIST:
            print(f"      {a['key']:<14} {a['name']}")
        print("    Добавить новый — в config.py, INCIDENT_WATCHLIST.")
    return asset


def cmd_incidents():
    """
    Ежедневная проверка: не случилось ли чего на объектах.

    Первый шаг продукта оценки повреждений. Пока тихо — ничего
    делать не надо, и это нормальный, самый частый результат.
    Как только проходит сообщение об ударе, пожаре или форс-мажоре,
    команда скажет, по какому объекту и что делать дальше.

    Окно поиска в сутках можно задать: python bot.py incidents 7
    """
    import incidents

    days = 1
    if len(sys.argv) > 2 and sys.argv[2].isdigit():
        days = int(sys.argv[2])

    print(f"Проверяю объекты, окно {days} сут. ...")
    try:
        res = incidents.run(days)
    except Exception as exc:                          # noqa: BLE001
        print(f"[!] {_net_hint(exc)}")
        return None

    text = incidents.render(res)
    print()
    print(text)
    path = incidents.save(text)
    print(f"Сохранено: {path}")

    # В Telegram отправляем только когда есть что сказать: ежедневное
    # «всё тихо» в телефоне — лучший способ отучить себя читать
    # уведомления и пропустить настоящее событие.
    #
    # Но про сломанный источник сообщаем обязательно: отсутствие
    # данных — это не отсутствие происшествий, и об этом надо узнать
    # сразу, а не через неделю.
    if res["total"] or res.get("errors"):
        tg_text("<pre>" + text[:3500] + "</pre>")
    return res


def cmd_tasking():
    """
    Коммерческая съёмка: что даст и сколько стоит для этого объекта.

    Отдельный блок, потому что всё остальное в продукте бесплатно.
    Считает площадь рамки, применяет минимальные заказы поставщиков
    и показывает вилку цен — плюс список случаев, когда платить не
    надо.

        python bot.py tasking            — по Рас-Лаффану
        python bot.py tasking jubail     — по другому объекту
    """
    import tasking as tsk

    name, _, _ = _parse_args(sys.argv[2:])
    asset = _pick_asset(name) if name else config.INCIDENT_WATCHLIST[0]
    if asset is None:
        return None

    text = tsk.report(asset)
    print()
    print(text)
    path = tsk.save(text, asset["key"])
    print(f"Сохранено: {path}")
    return text


def cmd_damage():
    """
    Повреждения по снимкам «до и после» удара.

    Берёт последний чистый снимок до события и первый чистый после,
    сравнивает по зонам и считает ПЛОЩАДЬ видимых изменений.
    Отдельно показывает порядок величины работ по опубликованным
    ориентирам стоимости — и столь же подробно объясняет, чего по
    снимку определить нельзя.

        python bot.py damage
        python bot.py damage 2026-02-22 2026-03-27
        python bot.py damage jubail 2026-02-22 2026-03-27
    """
    import damage as dmg

    name, a, b = _parse_args(sys.argv[2:])
    asset = _pick_asset(name) if name else config.INCIDENT_WATCHLIST[0]
    if asset is None:
        return None

    print(f"Ищу снимки до и после события — {asset['name']} ...")
    try:
        res = dmg.run(a, b, asset=asset)
    except Exception as exc:                          # noqa: BLE001
        print(f"[!] {_net_hint(exc)}")
        return None

    text = dmg.render(res)
    print()
    print(text)
    if not res.get("error"):
        path = dmg.save(text, asset["key"])
        print(f"Сохранено: {path}")
        tg_text("<pre>" + text[:3500] + "</pre>")
        print()
        print("Если десяти метров не хватило — посчитать платную съёмку:")
        print(f"  python bot.py tasking {asset['key']}")
    return res


def cmd_flare():
    """
    История тепловой мощности факела по дням.

    Добирает из FIRMS всё, что он отдаёт (это недавние сутки), и
    дописывает в историю. Потом печатает весь накопленный ряд.

    Зачем отдельная команда: FIRMS в прошлое пускает недалеко, поэтому
    историю бот копит сам, файлом. Эта команда наполняет её разом и
    показывает, что уже есть.

        python bot.py flare        — добрать и показать
        python bot.py flare 60     — показать последние 60 суток
    """
    import firms

    days = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() \
        else config.FLARE_HISTORY_DAYS

    want = min(30, config.FLARE_FETCH_DAYS * 3)
    print(f"Запрашиваю у FIRMS последние {want} сут. ...")
    try:
        pts = firms.fetch_hotspots(days=want)
        note = firms.status_note()
        if note:
            print(f"  {note}")
        firms.remember_days(pts, want)
        print(f"  точек получено: {len(pts)}")
    except Exception as exc:                          # noqa: BLE001
        print(f"[!] {_net_hint(exc)}")
        print("    История остаётся прежней — ничего не испорчено.")

    trend = firms.history_trend(days)
    span = firms.history_span()
    if not trend:
        print("\nИстория пуста. Запустите команду при работающей сети.")
        return None

    print(f"\n{'Дата':<12}{'МВт':>9}  график")
    print("-" * 60)
    vals = [v for _, v in trend if v is not None]
    top = max(vals) or 1
    for d, v in trend:
        if v is None:
            # Пропуск и ноль обязаны выглядеть по-разному: первое —
            # «не смотрели», второе — «смотрели, не горело».
            print(f"{d:<12}{'нет данных':>9}")
            continue
        bar = "#" * int(round(v / top * 36))
        print(f"{d:<12}{v:>9.1f}  {bar}")

    print("-" * 60)
    if span:
        print(f"Опрошено суток: {span['days_covered']}, "
              f"с факелом: {span['days_with_fire']}")
        print(f"Период: {span['from']} — {span['to']}")
    print(f"Средняя мощность: {sum(vals) / len(vals):.1f} МВт, "
          f"максимум: {top:g} МВт")
    gaps = sum(1 for _, v in trend if v is None)
    if gaps:
        print(f"Суток без данных: {gaps}. Это дни, когда бот не "
              f"запускался:\nFIRMS их уже не отдаст, добрать нельзя.")
    print(f"\nФайл истории: {firms.HISTORY_FILE}")
    print("Он копится сам при каждом запуске report — чем чаще "
          "запускаете,\nтем длиннее история.")
    return trend


def cmd_straits():
    """Калибровка проливов: снимок с координатной сеткой."""
    import areas as areas_mod
    import config as cfg
    for key in cfg.STRAITS:
        print(f"Готовлю калибровочный снимок: {cfg.STRAITS[key]['title']} ...")
        try:
            path, scene = areas_mod.calibrate(key)
            show_file(path)
            print(f"  снимок {scene['date']}, облачность {scene['cloud']}%")
            tg_photo(path, f"{cfg.STRAITS[key]['title']} — {scene['date']}")
        except Exception as exc:                      # noqa: BLE001
            print(f"  [!] {_net_hint(exc)}")
    print("\nОткройте картинки, сверьте рамки с судоходным коридором")
    print("и при необходимости поправьте STRAITS в config.py.")


def cmd_aistest():
    """Быстрая проверка AIS: показывает сырые ответы сервиса за 30 сек."""
    import ais_watch
    ais_watch.diagnose(30)


def cmd_satellite():
    import analyze
    print("Спутниковый анализ ...")
    sat = analyze.run(days_back=14)
    if not usable(sat):
        print(f"\n[!] Данных нет: {explain_empty(sat)}")
        return sat
    print("\n" + plain(build_report(sat)))
    return sat


def cmd_ais(duration=900):
    import ais_watch
    print("Слушаю AIS ...")
    summary = ais_watch.run(duration)
    sig = summary["signals"]
    print(f"\nУ грузовых причалов сейчас: {len(summary['at_berth_now'])}")
    for ev in summary["at_berth_now"]:
        cat = VESSEL_LABEL.get(ev.get("category"), "—")
        print(f"  {ev['vessel']} ({cat}) — {ev['berth']}")
    print(f"\nОтгрузок СПГ: {sig['lng_loadings']}, "
          f"жидких продуктов: {sig['liquid_loadings']}, "
          f"завоз сухогрузов: {sig['drycargo_calls']} "
          f"(проектных: {sig['project_cargo_calls']})")
    return summary


def usable(sat: dict) -> bool:
    """
    Есть ли в результате хоть что-то, кроме ошибок.

    Нужно затем, чтобы не собирать отчёт и особенно не публиковать сайт
    по пустому результату. Если при обрыве связи выложить на GitHub
    страницу без данных, живая витрина затрётся пустышкой, и об этом
    никто не узнает до следующего удачного запуска.
    """
    trains = sat.get("trains") or []
    if not trains:
        return False
    good = [t for t in trains if t.get("status") not in ("NO_DATA", "ERROR")]
    return len(good) > 0


def explain_empty(sat: dict) -> str:
    """Объясняет, почему данных нет."""
    if sat.get("scene_error"):
        return sat["scene_error"]
    trains = sat.get("trains") or []
    errs = [t for t in trains if t.get("status") == "ERROR"]
    if errs:
        first = (errs[0].get("evidence") or ["неизвестная ошибка"])[0]
        return f"все зоны вернули ошибку. Первая из них: {_net_hint(first)}"
    if trains:
        return ("ни одна зона не попала в снимок. Скорее всего координаты "
                "зон не совпадают с реальным положением объекта")
    return "обход не дал результатов"


def _full_cycle(ais_seconds=900, with_areas=True):
    """
    Спутник + области + AIS. Возвращает (sat, ais, areas_res).

    Области — это Ras Laffan и два пролива. Суда там считаются прямо по
    снимку, поэтому эта часть работает даже когда AIS молчит.
    """
    import analyze
    import ais_watch

    try:
        sat = analyze.run(days_back=14)
    except Exception as exc:                          # noqa: BLE001
        print(f"[!] Спутниковый анализ не выполнен: {_net_hint(exc)}")
        sat = {"period": {"from": None, "to": None}, "scene": None,
               "scene_error": _net_hint(exc), "trains": [], "berths": [],
               "sites": [], "firms_trend": []}

    areas_res = {}
    if with_areas:
        try:
            import areas as areas_mod
            print("Считаю суда по снимкам ...")
            areas_res = areas_mod.run(days_back=14)
        except Exception as exc:                      # noqa: BLE001
            print(f"[!] Разбор областей не выполнен: {_net_hint(exc)}")

    try:
        ais = ais_watch.run(ais_seconds)
    except Exception as exc:                          # noqa: BLE001
        print(f"[!] AIS недоступен: {_net_hint(exc)}")
        ais = None
    # Разведка по открытым источникам: новости, ролики, обсуждения,
    # признаки найма у подрядчиков. Всё бесплатно и без ключей.
    intel_res = None
    try:
        import intel as intel_mod
        print("Собираю разведку ...")
        intel_res = intel_mod.run(days=1)
    except Exception as exc:                          # noqa: BLE001
        print(f"[!] Разведка не собрана: {_net_hint(exc)}")

    # Отчётность публичных подрядчиков из реестра Комиссии США.
    fin_res = None
    try:
        import finance as fin_mod
        print("Отчётность подрядчиков ...")
        fin_res = fin_mod.run()
    except Exception as exc:                          # noqa: BLE001
        print(f"[!] Отчётность не собрана: {_net_hint(exc)}")

    # Проверка снимков зрячей моделью. Идёт последней: ей нужен уже
    # посчитанный результат, чтобы было что сверять. Если ключей нет
    # или модель не ответила, возвращается пометка об этом, и цикл
    # продолжается — проверка необязательная.
    ai_res = None
    try:
        import ai_check
        print("Сверяю снимки с моделью ...")
        ai_res = ai_check.run(sat)
        print(f"  {ai_res.get('note', '')}")
    except Exception as exc:                          # noqa: BLE001
        print(f"[!] Проверка моделью не выполнена: {_net_hint(exc)}")
    sat["ai"] = ai_res

    return sat, ais, areas_res, intel_res, fin_res


def cmd_report():
    print("=== Полный цикл ===")
    sat, ais, areas_res, intel_res, fin_res = _full_cycle()

    if not usable(sat):
        print(f"\n[!] Отчёт не собран: {explain_empty(sat)}")
        print("Данные не получены, старый отчёт оставлен нетронутым.")
        return None

    import heat as heat_mod
    import fusion as fusion_mod
    heat_res = heat_mod.compute(sat=sat, ais=ais, intel=intel_res,
                                fused=fusion_mod.run(sat, ais),
                                areas=areas_res, ai=sat.get("ai"))
    text = build_report(sat, ais, areas_res, intel_res, fin_res, heat_res)
    path = save_report(text)
    print(f"\nОтчёт сохранён: {path}")

    tg_text(text)
    for t in sat["trains"]:
        if t.get("chip") and t["status"] in ("DESTROYED", "REBUILDING", "NO_DATA"):
            tg_photo(t["chip"],
                     f"{t['name']} — {STATUS_RU.get(t['status'], t['status'])}")
    return path


def cmd_site():
    # Модуль называется site_builder, а не site: "site" — это имя
    # встроенного модуля Python, и файл с таким именем в папке проекта
    # перекрыл бы системный.
    import site_builder

    print("=== Полный цикл + сайт ===")
    sat, ais, areas_res, intel_res, fin_res = _full_cycle()

    # Сайт по пустому результату НЕ собираем. Иначе при обрыве связи
    # прошлая, рабочая версия витрины затрётся пустой страницей —
    # и об этом никто не узнает до следующего удачного запуска.
    if not usable(sat):
        print(f"\n[!] Сайт не собран: {explain_empty(sat)}")
        print("Прошлая версия сайта оставлена без изменений.")
        tg_text("Ras Laffan монитор: обход не удался.\n" + explain_empty(sat))
        return None

    import heat as heat_mod
    import fusion as fusion_mod
    heat_res = heat_mod.compute(sat=sat, ais=ais, intel=intel_res,
                                fused=fusion_mod.run(sat, ais),
                                areas=areas_res, ai=sat.get("ai"))
    index = site_builder.build(sat, ais, public=True, areas=areas_res,
                               intel=intel_res, fin=fin_res, heat=heat_res)
    # open_it=False: при ночном запуске publish окно браузера на пустой
    # машине не нужно. Путь при этом печатается полный.
    show_file(index, open_it=(len(sys.argv) > 1 and sys.argv[1] == "site"))

    text = build_report(sat, ais, areas_res, intel_res, fin_res, heat_res)
    save_report(text)
    tg_text(text)
    return index


def cmd_publish():
    """
    Собирает сайт и отправляет его на GitHub.

    Два режима, переключаются в config.py параметром PUBLISH_SITE_ONLY.

    True (по умолчанию) — на GitHub уходит ТОЛЬКО папка docs/, то есть
    готовая страница. Код, ключи и снимки остаются у вас на машине и на
    GitHub не попадают вообще. Репозиторий при этом всё равно публичный
    — иначе бесплатный GitHub Pages не работает, — но в нём лежит
    только то, что и так показывается посетителям.

    False — на GitHub уходит весь проект целиком, как было раньше.
    Так удобно, если нужна резервная копия кода, и неудобно, если код
    показывать не хочется.
    """
    index = cmd_site()
    if not index:
        print("Публикация отменена: нечего публиковать.")
        return None

    site_only = getattr(config, "PUBLISH_SITE_ONLY", True)
    where = config.SITE_DIR if site_only else "."
    msg = f"monitor update {dt.date.today().isoformat()}"

    if site_only and not os.path.isdir(os.path.join(config.SITE_DIR, ".git")):
        print()
        print(f"[!] Папка {config.SITE_DIR} ещё не привязана к репозиторию.")
        print("    Один раз выполните (подставьте свой логин GitHub):")
        print()
        print(f"      cd {config.SITE_DIR}")
        print("      git init")
        print("      git add .")
        print('      git commit -m "site"')
        print("      git branch -M main")
        print("      git remote add origin "
              "https://github.com/ВАШ-ЛОГИН/raslaffan.git")
        print("      git push -u origin main")
        print("      cd ..")
        print()
        print("    В настройках репозитория: Settings -> Pages -> ветка")
        print("    main, папка / (root), Save.")
        return index

    try:
        # cwd=where — команды выполняются внутри нужной папки. Для
        # режима «только сайт» это docs/ со своим отдельным учётом
        # версий, не связанным с кодом.
        run = lambda args: subprocess.run(args, cwd=where, check=True)
        run(["git", "add", "."])
        run(["git", "commit", "-m", msg])
        run(["git", "push"])
        print("\nОпубликовано. Через 1-2 минуты GitHub Pages обновит сайт.")
        if site_only:
            print("На GitHub ушла только папка сайта. Код остался у вас.")
    except subprocess.CalledProcessError as exc:
        print(f"\n[!] git не отработал: {exc}")
        print("Если ошибка в commit — скорее всего, менять было нечего.")
        print("Если в push — проверьте, что репозиторий привязан:")
        print(f"  cd {where} и затем git remote -v")
    return index


def cmd_test():
    print("Проверка ключей:\n")

    try:
        import sentinel
        sentinel.get_token()
        print("  [OK] Copernicus — токен получен")
    except Exception as exc:                          # noqa: BLE001
        print(f"  [FAIL] Copernicus: {_net_hint(exc)}")

    try:
        import firms
        pts = firms.fetch_hotspots(days=3)
        print(f"  [OK] NASA FIRMS — получено точек: {len(pts)}")
    except Exception as exc:                          # noqa: BLE001
        print(f"  [FAIL] FIRMS: {_net_hint(exc)}")

    if config.AISSTREAM_API_KEY:
        print("  [OK] AISStream — ключ задан (проверится при запуске ais)")
    else:
        print("  [FAIL] AISStream — ключ не задан")

    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
        tg_text("Проверка связи от монитора Ras Laffan.")
        print("  [OK] Telegram — сообщение отправлено")
    else:
        print("  [--] Telegram не настроен (необязательно)")


if __name__ == "__main__":
    for d in (config.CHIPS_DIR, config.REPORTS_DIR, config.STATE_DIR):
        os.makedirs(d, exist_ok=True)

    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    {
        "calibrate": cmd_calibrate,
        "dates": cmd_dates,
        "aistest": cmd_aistest,
        "straits": cmd_straits,
        "schedule": cmd_schedule,
        "video": cmd_video,
        "outreach": cmd_outreach,
        "contacts": cmd_contacts,
        "aicheck": cmd_aicheck,
        "damage": cmd_damage,
        "flare": cmd_flare,
        "incidents": cmd_incidents,
        "tasking": cmd_tasking,
        "satellite": cmd_satellite,
        "ais": cmd_ais,
        "report": cmd_report,
        "site": cmd_site,
        "publish": cmd_publish,
        "test": cmd_test,
    }.get(cmd, cmd_report)()
