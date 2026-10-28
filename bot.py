"""
bot.py — главный файл. Запускает всё и собирает отчёт.

Команды (вводятся в терминале, в папке проекта):

    python bot.py test       — проверка, что все ключи работают
    python bot.py calibrate  — один раз: снимок с сеткой для настройки координат
    python bot.py dates      — когда спутник снимал объект и с какой облачностью
    python bot.py aistest    — быстрая проверка AIS: сырые ответы за 30 сек
    python bot.py straits    — калибровочные снимки проливов с сеткой
    python bot.py satellite  — только спутниковый анализ линий
    python bot.py ais        — только слежение за судами (15 минут)
    python bot.py report     — полный цикл: спутник + суда + отчёт в Telegram
    python bot.py site       — полный цикл + сборка сайта в папку docs/
    python bot.py publish    — то же, что site, плюс git push на GitHub Pages
"""

import os
import subprocess
import sys
import datetime as dt

import requests

import config


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
    "PRODUCING": "работает",
    "DESTROYED": "разрушена",
    "REBUILDING": "идут работы",
    "IDLE": "стоит, цела",
    "NO_DATA": "нет данных",
    "ERROR": "ошибка",
}
STATUS_ICON = {
    "PRODUCING": "[+]", "DESTROYED": "[X]", "REBUILDING": "[~]",
    "IDLE": "[o]", "NO_DATA": "[?]", "ERROR": "[!]",
}
CATEGORY_RU = {
    "gas_carrier": "газовоз СПГ", "product_tanker": "танкер продуктов",
    "cargo": "сухогруз", "tug": "буксир", "dredger": "земснаряд",
    "support": "вспомогательное", "unknown": "неопознанное",
}


def build_report(sat: dict, ais: dict = None, areas: dict = None) -> str:
    import fusion

    fused = fusion.run(sat, ais)
    cx, cap = fused["complex"], fused["capacity"]
    sat = fused["sat"]

    today = dt.date.today().isoformat()
    lines = [
        "<b>RAS LAFFAN LNG — МОНИТОР</b>",
        f"Отчёт на {today}",
    ]

    # --- Сведения о снимке: что именно мы смотрим ---
    scene = sat.get("scene")
    if scene:
        age = scene["age_days"]
        if age == 0:
            age_txt = "сегодняшний"
        elif age == 1:
            age_txt = "вчерашний"
        else:
            age_txt = f"{age} дн. назад"
        lines.append(f"<b>Снимок от {scene['date']}</b> ({age_txt}), "
                     f"облачность {scene['cloud']}%")
        if scene.get("prev_date"):
            lines.append(f"Предыдущий пригодный снимок: {scene['prev_date']}")
        lines.append(f"Сравнение с опорным снимком от {scene['baseline_date']}")
        if age >= 7:
            lines.append("! ВНИМАНИЕ: снимку больше недели. Спутник снимает "
                         "эту точку примерно раз в 5 дней, значит последние "
                         "пролёты были закрыты облаками")
    elif sat.get("scene_error"):
        lines.append(f"! {sat['scene_error']}")
    else:
        lines.append(f"Период съёмки: {sat['period']['from']} .. {sat['period']['to']}")

    lines += [
        "",
        f"<b>ВЕРДИКТ ПО КОМПЛЕКСУ: {cx['ru'].upper()}</b>",
        cx["note"],
    ]
    for c in cx.get("caveats", []):
        lines.append(f"  ! {c}")

    sig = cx["signals"]
    lines += [
        "",
        "<b>ТРИ СИГНАЛА</b>",
        f"  Тепло на площадке: {'есть' if sig.get('thermal') else 'нет'}"
        f" (горячих линий: {sig.get('hot_trains', 0)}, "
        f"суммарный FRP: {sig.get('frp_sum', 0)} МВт)",
    ]
    if sig.get("lng_loadings") is None:
        lines.append("  Отгрузки СПГ: нет данных AIS")
    else:
        pct = sig.get("export_pct_of_norm")
        lines.append(
            f"  Отгрузки СПГ: {sig['lng_loadings']}"
            + (f" ({pct}% от доконфликтной нормы "
               f"~{sig.get('norm_cargoes')} партий)" if pct is not None else ""))
        lines.append(f"  Жидкие продукты: {sig.get('liquid_loadings', 0)}, "
                     f"сера: {sig.get('sulphur_loadings', 0)}")
        lines.append(f"  Завоз сухогрузов: {sig.get('drycargo_calls', 0)}, "
                     f"из них проектных: {sig.get('project_cargo_calls', 0)}")

    if areas:
        lines += ["", "<b>СУДА ПО СНИМКАМ</b>",
                  "<i>У каждой области своя дата: они разнесены на тысячи "
                  "километров и попадают в разные витки спутника.</i>"]
        for a in areas.values():
            if a.get("error"):
                lines.append(f"  {a['title']}: {a['error']}")
            else:
                lines.append(
                    f"  {a['title']} — снимок {a['date']} "
                    f"({a['age_days']} дн. назад): судов {a['count']}, "
                    f"из них крупных {a['large_count']} "
                    f"({a['px_size_m']} м/пиксель)")

    lines.append("")
    lines.append("<b>СТАТУС ЛИНИЙ СЖИЖЕНИЯ</b>")

    for venture in ("QE LNG South", "QE LNG North"):
        rows = [t for t in sat["trains"] if t["venture"] == venture]
        if not rows:
            continue
        lines.append(f"\n<u>{venture}</u>")
        for t in rows:
            icon = STATUS_ICON.get(t["status"], "[?]")
            ru = STATUS_RU.get(t["status"], t["status"])
            conf = {"high": "высокая", "medium": "средняя",
                    "low": "низкая", "none": "—"}.get(t["confidence"], "—")
            lines.append(
                f"{icon} <b>{t['name']}</b> ({t['mtpa']} млн т/год) — {ru}"
                f" · уверенность: {conf}")
            for ev in t["evidence"]:
                lines.append(f"      · {ev}")

    if cap.get("utilisation_pct") is not None:
        lines += [
            "",
            "<b>ОЦЕНКА МОЩНОСТИ</b>",
            f"В работе:    {cap['producing_mtpa']} млн т/год",
            f"Не в работе: {cap['down_mtpa']} млн т/год",
            f"Загрузка площадки: {cap['utilisation_pct']}%",
        ]

    # --- Площадки: признаки работ ---
    sites = sat.get("sites") or []
    flagged = [s for s in sites if s.get("construction_signal")]
    if sites:
        lines += ["", "<b>ПРИЗНАКИ РАБОТ НА БЕРЕГУ</b>"]
        for s in sites:
            chg = s.get("changed")
            chg_txt = "нет снимка" if chg is None else f"изменено {chg:.0%}"
            mark = " <- признаки работ" if s.get("construction_signal") else ""
            lines.append(f"  {s['site']}: {chg_txt}{mark}")
        if flagged:
            lines.append("  (грузовики со снимка не видны — подвоз техники "
                         "определяется по судам)")

    # --- Причалы по снимку: независимая проверка AIS ---
    berths = sat.get("berths") or []
    occupied = [b for b in berths if b.get("occupied")]
    if berths:
        lines += ["", "<b>ПРИЧАЛЫ ПО СНИМКУ (независимо от AIS)</b>",
                  f"  Занято причалов: {len(occupied)} из {len(berths)}"]
        for b in occupied:
            lines.append(f"  · {b['berth']} ({b['kind']}): "
                         f"судно видно, {b['vessel_px']} пикселей")

    trend = sat.get("firms_trend") or []
    if trend:
        lines += ["", "<b>ТЕПЛОВАЯ АКТИВНОСТЬ (VIIRS, суммарный FRP, МВт)</b>"]
        for day, frp in trend[-10:]:
            bar = "#" * min(int(frp / 5), 30)
            lines.append(f"{day}  {frp:7.1f}  {bar}")

    if ais:
        lines += ["", "<b>СУДА</b>",
                  f"Сейчас у грузовых причалов: {len(ais['at_berth_now'])}"]
        for ev in ais["at_berth_now"]:
            cat = CATEGORY_RU.get(ev.get("category"), "—")
            lines.append(f"  · {ev['vessel']} ({cat}) — "
                         f"{ev['berth']} ({ev['venture']})")

        done = [e for e in ais["recent"]
                if e.get("departed") and (e.get("operation_done")
                                          or e.get("likely_loaded"))]
        if done:
            lines.append(f"\nЗавершённые грузовые операции за {ais['days']} дн.:")
            for e in done[:20]:
                cat = CATEGORY_RU.get(e.get("category"), "—")
                dest = e.get("destination") or "направление не заявлено"
                lines.append(
                    f"  · {e['vessel']} ({cat}) — {e['berth']}, "
                    f"{e['hours']} ч, ушёл на: {dest}")

        by_kind = ais.get("by_kind", {})
        if by_kind:
            lines.append("\nПо типам причалов:")
            kind_ru = {"lng": "СПГ", "liquid": "жидкие продукты",
                       "sulphur": "сера", "drycargo": "сухой груз"}
            for k, st in sorted(by_kind.items()):
                lines.append(f"  {kind_ru.get(k, k)}: заходов {st['calls']}, "
                             f"операций завершено {st['completed']}")

        lines += [
            "",
            "<i>Привязка судов к конкретным линиям невозможна: СПГ со всех "
            "линий идёт в общие хранилища и далее на общие причалы. "
            "Показана привязка судно -> причал -> венчур.</i>",
        ]

    lines += [
        "",
        "<i>Источники: Sentinel-2 (10 м, Copernicus), VIIRS (375 м, NASA FIRMS), "
        "AIS (aisstream.io). Выводы автоматические — сверяйте со снимками.</i>",
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
    print(f"\nГотово: {path}")
    print("Откройте файл, найдите реальные линии и причалы, впишите их")
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
    return scenes


def cmd_straits():
    """Калибровка проливов: снимок с координатной сеткой."""
    import areas as areas_mod
    import config as cfg
    for key in cfg.STRAITS:
        print(f"Готовлю калибровочный снимок: {cfg.STRAITS[key]['title']} ...")
        try:
            path, scene = areas_mod.calibrate(key)
            print(f"  готово: {path}")
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
        cat = CATEGORY_RU.get(ev.get("category"), "—")
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
    return sat, ais, areas_res


def cmd_report():
    print("=== Полный цикл ===")
    sat, ais, areas_res = _full_cycle()

    if not usable(sat):
        print(f"\n[!] Отчёт не собран: {explain_empty(sat)}")
        print("Данные не получены, старый отчёт оставлен нетронутым.")
        return None

    text = build_report(sat, ais, areas_res)
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
    sat, ais, areas_res = _full_cycle()

    # Сайт по пустому результату НЕ собираем. Иначе при обрыве связи
    # прошлая, рабочая версия витрины затрётся пустой страницей —
    # и об этом никто не узнает до следующего удачного запуска.
    if not usable(sat):
        print(f"\n[!] Сайт не собран: {explain_empty(sat)}")
        print("Прошлая версия сайта оставлена без изменений.")
        tg_text("Ras Laffan монитор: обход не удался.\n" + explain_empty(sat))
        return None

    index = site_builder.build(sat, ais, public=True, areas=areas_res)
    print(f"\nСайт собран: {index}")
    print("Проверьте локально, открыв этот файл в браузере.")

    text = build_report(sat, ais, areas_res)
    save_report(text)
    tg_text(text)
    return index


def cmd_publish():
    """Собирает сайт и отправляет его на GitHub."""
    index = cmd_site()
    if not index:
        print("Публикация отменена: нечего публиковать.")
        return None

    msg = f"monitor update {dt.date.today().isoformat()}"
    try:
        subprocess.run(["git", "add", config.SITE_DIR], check=True)
        subprocess.run(["git", "commit", "-m", msg], check=True)
        subprocess.run(["git", "push"], check=True)
        print("\nОпубликовано. Через 1-2 минуты GitHub Pages обновит сайт.")
    except subprocess.CalledProcessError as exc:
        print(f"\n[!] git не отработал: {exc}")
        print("Если ошибка в commit — скорее всего, менять было нечего.")
        print("Если в push — проверьте, что репозиторий привязан: git remote -v")
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
        "satellite": cmd_satellite,
        "ais": cmd_ais,
        "report": cmd_report,
        "site": cmd_site,
        "publish": cmd_publish,
        "test": cmd_test,
    }.get(cmd, cmd_report)()
