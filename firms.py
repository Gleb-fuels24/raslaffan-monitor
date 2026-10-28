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
import datetime as dt
from collections import defaultdict

import requests

import config

BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
# Три спутника с прибором VIIRS. Берём все — больше проходов в сутки.
SOURCES = ["VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT"]


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
    days = max(1, min(days, 10))

    points = []
    for src in SOURCES:
        url = f"{BASE}/{config.FIRMS_MAP_KEY}/{src}/{area}/{days}"
        try:
            resp = requests.get(url, timeout=90)
            resp.raise_for_status()
        except requests.RequestException:
            continue

        text = resp.text.strip()
        if not text or text.lower().startswith("invalid"):
            continue

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
    return points


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
