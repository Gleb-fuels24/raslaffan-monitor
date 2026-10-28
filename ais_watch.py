"""
ais_watch.py — слежение за судами в порту Ras Laffan по открытым данным AIS.

AIS (Automatic Identification System) — система, по которой каждое крупное
судно непрерывно вещает в эфир свои координаты, скорость, название и пункт
назначения. Это открытый и легальный источник: суда обязаны вещать по
требованию ИМО ради безопасности мореплавания.

Мы слушаем бесплатный поток aisstream.io по прямоугольнику вокруг
Ras Laffan и раскладываем все суда по категориям, потому что РАЗНЫЕ суда
означают разные вещи:

  газовоз СПГ у причала СПГ        -> завод реально сжижает и отгружает
  танкер у причала жидких продуктов -> работает газопереработка/GTL
  судно у причала серы             -> работает газопереработка
  сухогруз у сухогрузного причала  -> ЗАВОЗ оборудования, признак стройки
  буксиры и земснаряды             -> морские работы, обслуживание

Именно в этом главная мысль: факел показывает, что что-то горит.
Отгрузка показывает, что продукт реально производят и вывозят.
Завоз без отгрузки показывает, что идёт восстановление.

=========================== ВАЖНОЕ ОГРАНИЧЕНИЕ ===========================
Привязать судно к КОНКРЕТНОЙ линии сжижения физически невозможно, и ни
один коммерческий сервис этого не делает. Причина в устройстве завода:

    линии 1..7  ->  общие резервуары хранения СПГ  ->  общие причалы

СПГ со всех работающих линий смешивается в хранилищах. Поэтому максимум,
что даёт AIS, — привязка «судно -> причал -> венчур (северная/южная
площадка)». Эту привязку бот и строит.

Второе ограничение, про стройку. Завоз оборудования подтверждает, что на
комплексе идут работы, но НЕ говорит, на каком именно объекте. В
Ras Laffan одновременно идут: ремонт линий 4 и 6, ремонт завода Pearl GTL
(до Q1 2027), восстановление Barzan после взрыва и стройка расширения
North Field East (пуск сдвинут на первую половину 2027). Проектный груз
может идти на любой из них. Бот это честно оговаривает в отчёте.
==========================================================================
"""

import asyncio
import json
import os
import datetime as dt
from collections import defaultdict

import config

STATE_FILE = os.path.join(config.STATE_DIR, "ais_state.json")
EVENTS_FILE = os.path.join(config.STATE_DIR, "berth_events.json")
WS_URL = "wss://stream.aisstream.io/v0/stream"


# ---------------------------------------------------------------------------
# Вспомогательное
# ---------------------------------------------------------------------------
def _utcnow():
    """Текущее время UTC без часового пояса (чтобы даты сравнивались между собой)."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _load(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            try:
                return json.load(fh)
            except json.JSONDecodeError:
                return default
    return default


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Привязка к причалу
# ---------------------------------------------------------------------------
def which_berth(lat, lon):
    """
    Возвращает имя причала, в рамку которого попала точка, или None.
    Рейд (Anchorage) проверяется последним, он самый большой.
    """
    ordered = sorted(config.BERTHS.items(),
                     key=lambda kv: kv[1]["kind"] == "anchorage")
    for name, meta in ordered:
        w, s, e, n = meta["bbox"]
        if w <= lon <= e and s <= lat <= n:
            return name
    return None


def berth_kind(berth_name):
    """Тип причала: lng / liquid / sulphur / drycargo / support / anchorage."""
    if not berth_name:
        return None
    return config.BERTHS.get(berth_name, {}).get("kind")


# ---------------------------------------------------------------------------
# Классификация судов
# ---------------------------------------------------------------------------
def classify_vessel(vessel):
    """
    Определяет категорию судна по коду типа AIS и длине.

    Возвращает одно из: gas_carrier, product_tanker, cargo, tug,
    dredger, support, unknown.

    Напоминание: отдельного кода AIS для газовоза не существует, все
    газовозы вещают как танкеры. Отличаем по длине (от 280 м) — такие
    размеры среди танкеров в Ras Laffan имеют практически только
    газовозы Q-Flex и Q-Max и обычные СПГ-газовозы.
    """
    vtype = vessel.get("type")
    length = vessel.get("length") or 0

    if vtype is None:
        return "unknown"

    for cat, rule in config.VESSEL_CATEGORIES.items():
        types = rule["types"]
        if vtype not in types:
            continue
        if length < rule.get("min_len", 0):
            continue
        if "max_len" in rule and length >= rule["max_len"]:
            continue
        return cat
    return "unknown"


def category_ru(cat):
    """Название категории по-русски для отчёта."""
    if cat in config.VESSEL_CATEGORIES:
        return config.VESSEL_CATEGORIES[cat]["ru"]
    return "неопознанное судно"


def is_project_cargo(vessel):
    """
    Похоже ли судно на завоз крупного оборудования (модулей, колонн).

    Два признака:
      1) крупный сухогруз (от 140 м);
      2) имя содержит название известного оператора тяжеловозов.
    """
    if classify_vessel(vessel) != "cargo":
        return False
    name = (vessel.get("name") or "").upper()
    if any(k in name for k in config.HEAVY_LIFT_KEYWORDS):
        return True
    return (vessel.get("length") or 0) >= config.PROJECT_CARGO_MIN_LEN_M


def is_gas_carrier(vessel) -> bool:
    """Оставлено для совместимости: только газовозы СПГ."""
    return classify_vessel(vessel) == "gas_carrier"


# ---------------------------------------------------------------------------
# Сбор потока AIS
# ---------------------------------------------------------------------------
async def collect(duration_sec: int = 900):
    """
    Слушает поток AIS указанное число секунд и обновляет состояние судов.
    По умолчанию 15 минут — достаточно, чтобы собрать все суда в районе.
    """
    import websockets

    if not config.AISSTREAM_API_KEY:
        raise RuntimeError(
            "Не задан AISSTREAM_API_KEY в .env. Бесплатный ключ: https://aisstream.io"
        )

    w, s, e, n = config.RLIC_BBOX
    # расширяем рамку на рейд и подходы к порту
    area = [[[s - 0.15, w - 0.20], [n + 0.25, e + 0.30]]]

    subscribe = {
        "APIKey": config.AISSTREAM_API_KEY,
        "BoundingBoxes": area,
        "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
    }

    vessels = _load(STATE_FILE, {})
    deadline = _utcnow() + dt.timedelta(seconds=duration_sec)

    received = 0
    first_reply = None

    async with websockets.connect(WS_URL, ping_interval=20) as ws:
        await ws.send(json.dumps(subscribe))
        print(f"  слушаю AIS {duration_sec} сек ...")

        while _utcnow() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=30)
            except asyncio.TimeoutError:
                continue

            received += 1
            if first_reply is None:
                first_reply = str(raw)[:300]

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                # Сервис отвечает не-JSON только когда недоволен запросом:
                # чаще всего это сообщение о неверном ключе.
                print(f"  [!] AIS ответил не по формату: {str(raw)[:200]}")
                break

            # Сервис сообщает об ошибке обычным полем, и без этой
            # проверки мы молча слушали бы тишину весь срок.
            if isinstance(msg, dict) and msg.get("error"):
                print(f"  [!] AIS отказал: {msg['error']}")
                break

            meta = msg.get("MetaData", {})
            mmsi = str(meta.get("MMSI") or "")
            if not mmsi:
                continue

            rec = vessels.setdefault(mmsi, {"mmsi": mmsi})
            name = (meta.get("ShipName") or "").strip()
            if name:
                rec["name"] = name

            mtype = msg.get("MessageType")

            if mtype == "ShipStaticData":
                d = msg["Message"]["ShipStaticData"]
                rec["type"] = d.get("Type")
                dim = d.get("Dimension", {}) or {}
                rec["length"] = (dim.get("A", 0) or 0) + (dim.get("B", 0) or 0)
                rec["beam"] = (dim.get("C", 0) or 0) + (dim.get("D", 0) or 0)
                rec["imo"] = d.get("ImoNumber")
                rec["destination"] = (d.get("Destination") or "").strip()
                rec["draught"] = d.get("MaximumStaticDraught")

            elif mtype == "PositionReport":
                d = msg["Message"]["PositionReport"]
                rec["lat"] = d.get("Latitude")
                rec["lon"] = d.get("Longitude")
                rec["sog"] = d.get("Sog")        # скорость в узлах
                rec["ts"] = _utcnow().isoformat(timespec="seconds")

    _save(STATE_FILE, vessels)
    print(f"  получено сообщений: {received}, судов в базе: {len(vessels)}")

    if received == 0:
        print("  [!] За всё время не пришло ни одного сообщения.")
        print("      Обычно это значит одно из двух:")
        print("      1) неверный AISSTREAM_API_KEY — проверьте на aisstream.io;")
        print("      2) сервис временно не работает (он в состоянии беты).")
        if first_reply:
            print(f"      Первый ответ сервиса: {first_reply}")
    elif not vessels:
        print("  [!] Сообщения приходят, но ни одно судно не распознано.")
        print("      Проверьте рамку RLIC_BBOX в config.py: возможно, "
              "перепутаны широта и долгота.")

    return vessels


# ---------------------------------------------------------------------------
# Разбор стоянок у причалов
# ---------------------------------------------------------------------------
def update_berth_events(vessels=None):
    """
    Сопоставляет текущие позиции судов с причалами и ведёт журнал стоянок.

    Для каждой стоянки запоминаем категорию судна и тип причала — без них
    невозможно отличить отгрузку СПГ от завоза стройматериалов.

    Про осадку (draught). В AIS это поле заполняется экипажем ВРУЧНУЮ и
    часто остаётся несвежим. Мы записываем осадку на приходе и на уходе,
    но делаем вывод «ушёл гружёным» только если рост осадки реально
    наблюдали. Основной признак погрузки всё равно другой — длительность
    стоянки у грузового причала.
    """
    vessels = vessels if vessels is not None else _load(STATE_FILE, {})
    events = _load(EVENTS_FILE, [])
    now = _utcnow()
    now_s = now.isoformat(timespec="seconds")

    open_by_mmsi = {ev["mmsi"]: ev for ev in events if ev.get("departed") is None}

    for mmsi, v in vessels.items():
        if v.get("lat") is None or v.get("lon") is None:
            continue

        cat = classify_vessel(v)
        if cat == "unknown":
            continue

        berth = which_berth(v["lat"], v["lon"])
        kind = berth_kind(berth)

        # Внимание на ноль: судно у причала почти всегда передаёт скорость
        # ровно 0.0, а ноль в Python считается «ложью». Поэтому проверяем
        # именно на None, а не через `or`, иначе стоящее судно будет
        # выглядеть идущим и ни одна стоянка не зафиксируется.
        sog = v.get("sog")
        moored = sog is not None and sog <= config.MOORED_SPEED_KTS

        # Стоянка «под операцией» — только у рабочего причала, только стоя
        # и только если тип судна соответствует грузу этого причала.
        # Иначе буксир, подошедший к газовозу, был бы записан как отгрузка.
        allowed = config.BERTH_CARGO_CATEGORIES.get(kind or "", [])
        at_berth = bool(allowed) and cat in allowed and moored

        existing = open_by_mmsi.get(mmsi)

        if at_berth and not existing:
            events.append({
                "mmsi": mmsi,
                "vessel": v.get("name", "?"),
                "imo": v.get("imo"),
                "length_m": v.get("length"),
                "category": cat,
                "project_cargo": is_project_cargo(v),
                "berth": berth,
                "berth_kind": kind,
                "venture": config.BERTHS[berth]["venture"],
                "arrived": now_s,
                "departed": None,
                "hours": None,
                "destination": v.get("destination", ""),
                "draught_in": v.get("draught"),
                "draught_out": None,
            })
        elif at_berth and existing:
            existing["last_seen"] = now_s
            if v.get("destination"):
                existing["destination"] = v["destination"]
        elif not at_berth and existing:
            arrived = dt.datetime.fromisoformat(existing["arrived"])
            hours = round((now - arrived).total_seconds() / 3600, 1)
            existing["departed"] = now_s
            existing["hours"] = hours
            existing["draught_out"] = v.get("draught")
            if v.get("destination"):
                existing["destination"] = v["destination"]

            # Порог зависит от того, грузили или выгружали.
            threshold = (config.MIN_DISCHARGE_HOURS
                         if existing.get("berth_kind") == "drycargo"
                         else config.MIN_LOADING_HOURS)
            existing["operation_done"] = hours >= threshold
            # Старое имя поля — чтобы не ломать уже написанные отчёты.
            existing["likely_loaded"] = existing["operation_done"]

            d_in, d_out = existing.get("draught_in"), existing.get("draught_out")
            if isinstance(d_in, (int, float)) and isinstance(d_out, (int, float)):
                existing["draught_rose"] = d_out > d_in + 0.3

    _save(EVENTS_FILE, events)
    return events


# ---------------------------------------------------------------------------
# Сводка и сигналы для анализа
# ---------------------------------------------------------------------------
def marine_works(vessels=None):
    """
    Считает суда морских работ в районе порта: земснаряды и буксиры.

    Земснаряд в порту почти всегда означает дноуглубление, а это делают
    под стройку или под приём более крупных судов. Такие суда не встают
    «под груз», поэтому в журнал стоянок они не попадают и их надо
    считать отдельно.
    """
    vessels = vessels if vessels is not None else _load(STATE_FILE, {})
    w, s, e, n = config.RLIC_BBOX
    out = {"dredgers": [], "tugs": []}

    for v in vessels.values():
        lat, lon = v.get("lat"), v.get("lon")
        if lat is None or lon is None:
            continue
        if not (w <= lon <= e and s <= lat <= n):
            continue
        cat = classify_vessel(v)
        if cat == "dredger":
            out["dredgers"].append(v.get("name", "?"))
        elif cat == "tug":
            out["tugs"].append(v.get("name", "?"))

    return {
        "dredger_count": len(out["dredgers"]),
        "tug_count": len(out["tugs"]),
        "dredger_names": out["dredgers"][:10],
    }


def summarize(events=None, days: int = 7, vessels=None):
    """Сводка по причалам, категориям судов и венчурам за последние `days` дней."""
    events = events if events is not None else _load(EVENTS_FILE, [])
    cutoff = _utcnow() - dt.timedelta(days=days)

    recent = []
    for ev in events:
        try:
            if dt.datetime.fromisoformat(ev["arrived"]) >= cutoff:
                recent.append(ev)
        except (KeyError, ValueError):
            continue

    by_berth = defaultdict(lambda: {"calls": 0, "loaded": 0, "vessels": []})
    by_venture = defaultdict(lambda: {"calls": 0, "loaded": 0})
    by_kind = defaultdict(lambda: {"calls": 0, "completed": 0})
    by_category = defaultdict(lambda: {"calls": 0, "completed": 0})

    for ev in recent:
        done = bool(ev.get("operation_done") or ev.get("likely_loaded"))

        b = by_berth[ev["berth"]]
        b["calls"] += 1
        b["vessels"].append(ev["vessel"])
        if done:
            b["loaded"] += 1

        v = by_venture[ev.get("venture", "-")]
        v["calls"] += 1
        if done:
            v["loaded"] += 1

        k = by_kind[ev.get("berth_kind", "-")]
        k["calls"] += 1
        if done:
            k["completed"] += 1

        c = by_category[ev.get("category", "unknown")]
        c["calls"] += 1
        if done:
            c["completed"] += 1

    # --- Сигналы, которые пойдут в матрицу слияния ---
    lng_loadings = by_kind.get("lng", {}).get("completed", 0)
    liquid_loadings = by_kind.get("liquid", {}).get("completed", 0)
    sulphur_loadings = by_kind.get("sulphur", {}).get("completed", 0)
    drycargo_calls = by_kind.get("drycargo", {}).get("calls", 0)
    project_cargo = sum(1 for ev in recent if ev.get("project_cargo"))

    # Процент от нормы по отгрузкам СПГ.
    norm = config.PREWAR_LNG_CARGOES_PER_WEEK * days / 7.0
    export_pct = round(100.0 * lng_loadings / norm, 1) if norm else None

    works = marine_works(vessels)

    return {
        "days": days,
        "marine_works": works,
        "at_berth_now": [e for e in events if e.get("departed") is None],
        "recent": sorted(recent, key=lambda x: x["arrived"], reverse=True),
        "by_berth": dict(by_berth),
        "by_venture": dict(by_venture),
        "by_kind": dict(by_kind),
        "by_category": dict(by_category),
        "signals": {
            "lng_loadings": lng_loadings,
            "liquid_loadings": liquid_loadings,
            "sulphur_loadings": sulphur_loadings,
            "drycargo_calls": drycargo_calls,
            "project_cargo_calls": project_cargo,
            "export_pct_of_norm": export_pct,
            "norm_cargoes": round(norm, 1),
            "dredgers": works["dredger_count"],
            "tugs": works["tug_count"],
        },
    }


def run(duration_sec: int = 900):
    """Один цикл: послушать эфир, обновить журнал, вернуть сводку."""
    vessels = asyncio.run(collect(duration_sec))
    events = update_berth_events(vessels)
    return summarize(events, vessels=vessels)
