"""
firms.py — тепловые аномалии от NASA FIRMS.

FIRMS раздаёт бесплатно данные приборов VIIRS: каждую ночь и каждый день
спутник фиксирует точки, где поверхность заметно горячее окружения.
Факел работающей линии СПГ — именно такая точка.

Разрешение VIIRS — 375 метров. Это НЕ позволяет разделить соседние линии
(они стоят ближе друг к другу), но отлично показывает:
  - работает ли площадка вообще;
  - на какой из двух площадок (северной или южной) идёт сжигание;
  - как менялась суммарная тепловая мощность по месяцам.

FRP (Fire Radiative Power) — мощность теплового излучения в мегаваттах.
Чем выше суммарный FRP, тем больше газа сжигается, тем выше загрузка.
"""

import csv
import io
import json
import os
import datetime as dt
from collections import defaultdict

import requests

import config

BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
# Три спутника с прибором VIIRS. Берём все — больше проходов в сутки.
SOURCES = ["VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT"]

# Что ответил каждый источник в последнем запросе.
LAST_STATUS = {}

# Максимальный диапазон одного запроса к FIRMS, суток. Жёсткое
# ограничение сервиса: на 6 и больше приходит 400.
MAX_RANGE = 5


def _hide_key(text: str) -> str:
    """Убирает ключ из текста ошибки: он уходит в журнал и на сайт."""
    key = (config.FIRMS_MAP_KEY or "").strip()
    return text.replace(key, "<КЛЮЧ>") if key else text


def fetch_hotspots(days: int = 7, bbox=None):
    """
    Скачивает тепловые точки за последние `days` суток (максимум 10).
    Возвращает список словарей с полями lat, lon, frp, date, time, sat.
    """
    if not config.FIRMS_MAP_KEY:
        raise RuntimeError(
            "Не задан FIRMS_MAP_KEY в .env. Бесплатный ключ выдаётся сразу: "
            "https://firms.modaps.eosdis.nasa.gov/api/map_key/"
        )

    bbox = bbox or config.RLIC_BBOX
    # FIRMS ждёт порядок: запад,юг,восток,север — он у нас такой же
    area = ",".join(str(round(v, 4)) for v in bbox)

    # FIRMS принимает диапазон от 1 до 5 СУТОК, не больше. Запрос на
    # 6 и дальше отвергается с ошибкой 400, причём по всем спутникам
    # сразу — именно это и выглядело как «данные VIIRS пропали».
    # Раньше здесь стояло min(days, 10), то есть при снимке старше
    # пяти дней бот гарантированно получал отказ.
    #
    # Поэтому длинный период режем на отрезки по пять суток и
    # запрашиваем их по очереди, указывая дату начала отрезка.
    days = max(1, min(days, 30))
    chunks = []
    remaining, end = days, dt.date.today()
    while remaining > 0:
        length = min(MAX_RANGE, remaining)
        start = end - dt.timedelta(days=length - 1)
        chunks.append((start.isoformat(), length))
        end = start - dt.timedelta(days=1)
        remaining -= length

    points = []
    # Что случилось с каждым источником. Нужно, чтобы отличать «факелов
    # нет» от «данные не пришли». Раньше любая неудача молча
    # пропускалась, и на витрине в обоих случаях писалось «нет данных
    # VIIRS» — то есть поломка выглядела как тишина на объекте.
    LAST_STATUS.clear()

    for src in SOURCES:
        before_src = len(points)
        failed = None

        for start, length in chunks:
            url = (f"{BASE}/{config.FIRMS_MAP_KEY}/{src}/{area}/"
                   f"{length}/{start}")
            try:
                resp = requests.get(url, timeout=90)
                resp.raise_for_status()
            except requests.RequestException as exc:
                # Ключ из текста ошибки вырезаем: сообщение попадает
                # и в журнал, и на витрину, а ключ — это доступ.
                msg = _hide_key(str(exc))
                # Сначала человеческое объяснение, потом техническая
                # часть: иначе длинный адрес съедает подсказку при
                # обрезке, и остаётся бесполезное "400 Bad Request".
                if "400" in msg:
                    failed = ("FIRMS rejected the request (400). Usual "
                              "causes: a day range over 5, or stray spaces "
                              "in the key")
                elif "401" in msg or "403" in msg:
                    failed = "FIRMS refused the key (401/403)"
                else:
                    failed = msg[:150]
                break

            text = resp.text.strip()
            low = text.lower()
            if not text:
                failed = "empty response"
                break
            if low.startswith("invalid"):
                failed = "key rejected (Invalid MAP_KEY)"
                break
            if "transaction limit" in low or "rate limit" in low:
                failed = "FIRMS request limit exceeded — wait 10 minutes"
                break
            if not low.startswith("country_id") and "latitude" not in low:
                failed = f"unexpected response: {_hide_key(text[:90])}"
                break

            for row in csv.DictReader(io.StringIO(text)):
                try:
                    points.append({
                        "lat": float(row["latitude"]),
                        "lon": float(row["longitude"]),
                        "frp": float(row.get("frp") or 0.0),
                        "date": row.get("acq_date", ""),
                        "time": row.get("acq_time", ""),
                        "daynight": row.get("daynight", ""),
                        "sat": src,
                    })
                except (KeyError, ValueError):
                    continue

        LAST_STATUS[src] = (failed if failed
                            else f"ok, {len(points) - before_src} points")

    # Отрезки не пересекаются, но дубли всё равно отсечём: один и тот
    # же пролёт иногда попадает в выдачу дважды.
    seen, uniq = set(), []
    for p in points:
        key = (p["lat"], p["lon"], p["date"], p["time"], p["sat"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def status_note():
    """
    Словами о том, что ответили источники VIIRS.

    Пусто, если всё в порядке и точки получены. Строка с причиной,
    если ни один источник не отдал данные.
    """
    if not LAST_STATUS:
        return "VIIRS was not queried"
    good = [s for s, v in LAST_STATUS.items() if v.startswith("ok")]
    if good and any(not v.endswith("0 points") for s, v in LAST_STATUS.items()
                    if v.startswith("ok")):
        return ""
    if good:
        return ("the satellites answered, but there are no thermal hits "
                "in the zone — the flares are not burning")
    return "VIIRS data was not received: " + "; ".join(
        f"{s.replace('VIIRS_', '').replace('_NRT', '')} — {v}"
        for s, v in LAST_STATUS.items())


def in_bbox(point, bbox, pad_deg: float = 0.004) -> bool:
    """
    Попадает ли точка в рамку. pad_deg расширяет рамку примерно на 400 м,
    чтобы учесть грубое разрешение VIIRS (пиксель 375 м «размазывается»).
    """
    w, s, e, n = bbox
    return (w - pad_deg) <= point["lon"] <= (e + pad_deg) and \
           (s - pad_deg) <= point["lat"] <= (n + pad_deg)


def summarize_by_zone(points, zones: dict):
    """
    Раскладывает тепловые точки по зонам (линиям, площадкам).
    Возвращает {имя_зоны: {"count": N, "frp_sum": X, "frp_max": Y, "last": дата}}.
    """
    out = {}
    for name, meta in zones.items():
        hits = [p for p in points if in_bbox(p, meta["bbox"])]
        out[name] = {
            "count": len(hits),
            "frp_sum": round(sum(h["frp"] for h in hits), 1),
            "frp_max": round(max((h["frp"] for h in hits), default=0.0), 1),
            "last": max((h["date"] for h in hits), default=None),
        }
    return out


def venture_trend(points):
    """
    Суммарный FRP по дням для всей площадки — показывает динамику загрузки.
    Возвращает список (дата, суммарный_FRP), от старых к новым.
    """
    per_day = defaultdict(float)
    for p in points:
        per_day[p["date"]] += p["frp"]
    return sorted(per_day.items())


# --- Накопление истории по дням ---------------------------------------
#
# FIRMS отдаёт только недавние сутки, а каждый запуск бота брал окно в
# несколько дней — поэтому на графике и стояло пять-шесть столбиков.
# История при этом терялась: данные, скачанные на прошлой неделе,
# нигде не оставались.
#
# Теперь каждый запуск дописывает свои сутки в файл, и график растёт
# сам. Через месяц работы там месяц, через год — год, независимо от
# того, как глубоко FIRMS пускает в прошлое сегодня.
#
# ГЛАВНОЕ В ЭТОМ ФАЙЛЕ — поле covered. День, по которому запрос БЫЛ и
# факелов не нашлось, и день, по которому запроса не было, дают одно
# и то же: отсутствие строки. Но это разные вещи: первое — ноль,
# второе — неизвестно. Поэтому опрошенные сутки перечисляются явно, и
# на графике неопрошенные остаются пропуском, а не нулевым столбиком.

HISTORY_FILE = os.path.join("data", "state", "flare_history.json")


def load_flare_history(path: str = None) -> dict:
    path = path or HISTORY_FILE
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and "days" in data:
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"days": {}, "covered": []}


def save_flare_history(hist: dict, path: str = None) -> str:
    path = path or HISTORY_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(hist, fh, ensure_ascii=False, indent=1, sort_keys=True)
    return path


def remember_days(points, days: int, hist: dict = None,
                  path: str = None) -> dict:
    """
    Дописывает в историю сутки, которые только что опрашивались.

    days — глубина запроса: именно эти сутки считаются опрошенными,
    включая те, в которые ничего не нашлось.

    Сегодняшний день НЕ записывается как окончательный: спутник за
    сутки проходит несколько раз, и утренняя сумма меньше вечерней.
    Записываем, но он будет перезаписан следующим запуском — и это
    правильно, пока сутки не закончились.
    """
    hist = hist if hist is not None else load_flare_history(path)
    fresh = venture_trend(points)
    for d, v in fresh:
        hist["days"][d] = round(float(v), 1)

    today = dt.date.today()
    covered = set(hist.get("covered") or [])
    for i in range(max(1, days)):
        d = (today - dt.timedelta(days=i)).isoformat()
        covered.add(d)
        # Опрошен и пуст — это ноль, и он должен быть виден как ноль.
        hist["days"].setdefault(d, 0.0)
    hist["covered"] = sorted(covered)
    save_flare_history(hist, path)
    return hist


def history_trend(days: int = 90, hist: dict = None, path: str = None):
    """
    Ряд по дням для графика, от старых к новым.

    Возвращает [(дата, мощность_или_None)]. None — сутки, по которым
    запроса не было: на графике это пропуск, а не ноль.
    """
    hist = hist if hist is not None else load_flare_history(path)
    covered = set(hist.get("covered") or [])
    vals = hist.get("days") or {}
    if not covered and not vals:
        return []

    today = dt.date.today()
    out = []
    for i in range(max(1, days) - 1, -1, -1):
        d = (today - dt.timedelta(days=i)).isoformat()
        if d in covered or d in vals:
            out.append((d, float(vals.get(d, 0.0))))
        else:
            out.append((d, None))

    # Обрезаем пустое начало: показывать месяц пропусков до первого
    # наблюдения незачем.
    while out and out[0][1] is None:
        out.pop(0)
    return out


def history_span(hist: dict = None, path: str = None):
    """Сколько суток в истории и за какой период. Для подписи графика."""
    hist = hist if hist is not None else load_flare_history(path)
    covered = sorted(hist.get("covered") or [])
    vals = hist.get("days") or {}
    if not covered:
        return None
    return {"from": covered[0], "to": covered[-1],
            "days_covered": len(covered),
            "days_with_fire": sum(1 for d in covered
                                  if float(vals.get(d, 0) or 0) > 0)}
