"""
analyze.py — определение статуса каждой линии по снимкам.

Логика в двух словах. Для каждой линии мы берём два снимка одной и той же
зоны: «до удара» (февраль 2026) и свежий. Дальше считаем три вещи.

1) Горячие точки (hot pixels).
   Работающее оборудование СПГ и его факел очень ярко светят в канале B12
   (коротковолновый инфракрасный, 2190 нм). Если в зоне есть такие пиксели —
   линия почти наверняка в работе.

2) dNBR — индекс выгорания.
   NBR = (ближний ИК - SWIR) / (ближний ИК + SWIR). При пожаре и разрушении
   NBR резко падает. dNBR = NBR_до - NBR_сейчас. Чем больше, тем сильнее
   повреждение.

3) Доля изменённых пикселей.
   Насколько вообще картинка отличается от февральской. Это ловит стройку:
   снесённые конструкции, новые площадки складирования, краны, техника.

Дальше простое правило:
   горячо                       -> PRODUCING  (производит)
   холодно + сильное выгорание  -> DESTROYED  (разрушена)
   холодно + сильные изменения  -> REBUILDING (идёт восстановление)
   холодно + всё как было       -> IDLE       (стоит, но цела)

ЧЕСТНОЕ ОГРАНИЧЕНИЕ. Разрешение 10 м и автоматика дают гипотезу, а не факт.
Поэтому бот ВСЕГДА прикладывает к выводу картинку зоны, чтобы вы могли
посмотреть глазами. Для спорных линий поле `confidence` будет "low".
"""

import os
import datetime as dt

import numpy as np
import rasterio

import config
import sentinel

# Номера каналов в скачанном GeoTIFF (нумерация rasterio с единицы)
B02, B03, B04, B08, B11, B12, MASK = 1, 2, 3, 4, 5, 6, 7


def _read(path):
    """Читает GeoTIFF и возвращает словарь каналов как массивы чисел."""
    with rasterio.open(path) as src:
        arr = src.read().astype("float32")
    return {
        "blue": arr[B02 - 1], "green": arr[B03 - 1], "red": arr[B04 - 1],
        "nir": arr[B08 - 1], "swir1": arr[B11 - 1], "swir2": arr[B12 - 1],
        "mask": arr[MASK - 1],
    }


def hot_pixels(bands):
    """Считает «горячие» пиксели — признак работающего факела/оборудования."""
    eps = 1e-6
    hot = (
        (bands["swir2"] > config.HOT_B12_MIN) &
        (bands["swir2"] > config.HOT_B12_B11_RATIO * (bands["swir1"] + eps)) &
        (bands["swir2"] > config.HOT_B12_B08_RATIO * (bands["nir"] + eps)) &
        (bands["mask"] > 0)
    )
    return int(hot.sum())


def nbr(bands):
    """Индекс выгорания. Возвращает средний по зоне."""
    eps = 1e-6
    valid = bands["mask"] > 0
    if valid.sum() == 0:
        return None
    val = (bands["nir"] - bands["swir2"]) / (bands["nir"] + bands["swir2"] + eps)
    return float(np.nanmean(val[valid]))


def change_fraction(before, after):
    """
    Доля пикселей, которые заметно изменились между двумя снимками.
    Считаем расстояние по четырём каналам и смотрим, где оно велико.
    """
    valid = (before["mask"] > 0) & (after["mask"] > 0)
    if valid.sum() == 0:
        return None
    diff = np.sqrt(
        (after["red"] - before["red"]) ** 2 +
        (after["green"] - before["green"]) ** 2 +
        (after["nir"] - before["nir"]) ** 2 +
        (after["swir1"] - before["swir1"]) ** 2
    )
    changed = (diff > 0.08) & valid
    return float(changed.sum() / valid.sum())


def classify(hot_count, dnbr, changed_frac, firms_hits):
    """Превращает числа в статус и оценку уверенности."""
    evidence = []

    thermally_active = hot_count >= config.HOT_PIXEL_MIN_COUNT or firms_hits > 0
    if hot_count:
        evidence.append(f"горячих пикселей SWIR: {hot_count}")
    if firms_hits:
        evidence.append(f"тепловых точек VIIRS: {firms_hits}")

    if thermally_active:
        conf = "high" if (hot_count >= 4 and firms_hits > 0) else "medium"
        return "PRODUCING", conf, evidence

    if dnbr is not None and dnbr >= config.DNBR_DAMAGE_THRESHOLD:
        evidence.append(f"dNBR = {dnbr:.2f} (сильное выгорание)")
        if changed_frac is not None and changed_frac > 0.30:
            evidence.append(f"изменено {changed_frac:.0%} площади — похоже на работы")
            return "REBUILDING", "low", evidence
        return "DESTROYED", "medium", evidence

    if changed_frac is not None and changed_frac > 0.25:
        evidence.append(f"изменено {changed_frac:.0%} площади без следов пожара")
        return "REBUILDING", "low", evidence

    evidence.append("тепла нет, геометрия площадки не изменилась")
    return "IDLE", "medium", evidence


def analyse_train(name, meta, firms_zone_stats, fresh_from, fresh_to,
                  baseline_date):
    """
    Полный разбор одной линии. Возвращает словарь с результатом.

    fresh_from и fresh_to здесь СОВПАДАЮТ: это одна конкретная дата
    пролёта спутника, выбранная в run() один раз на весь обход. Так
    все зоны разбираются по одному и тому же снимку и их можно
    сравнивать между собой.
    """
    safe = name.replace("/", "_")
    base_tif = os.path.join(config.CHIPS_DIR, f"{safe}_baseline_{baseline_date}.tif")
    now_tif = os.path.join(config.CHIPS_DIR, f"{safe}_{fresh_to}.tif")
    now_png = os.path.join(config.CHIPS_DIR, f"{safe}_{fresh_to}_hot.png")

    result = {
        "name": name, "venture": meta["venture"], "mtpa": meta["mtpa"],
        "known_status": meta.get("known_status"),
        "status": "NO_DATA", "confidence": "none", "evidence": [],
        "chip": None, "hot_pixels": 0, "dnbr": None, "changed": None,
        "snapshot_date": fresh_to,
    }

    # Опорный снимок качаем один раз и переиспользуем: февраль 2026
    # уже не изменится. Дата в имени файла — чтобы при смене опорного
    # периода старый кэш не подсунул чужую картинку.
    if not os.path.exists(base_tif):
        sentinel.fetch_chip(meta["bbox"], baseline_date, baseline_date, base_tif,
                            width=256, height=256)

    got_now = sentinel.fetch_chip(meta["bbox"], fresh_from, fresh_to, now_tif,
                                  width=256, height=256)
    if not got_now:
        result["evidence"].append(f"зона не попала в снимок от {fresh_to}")
        return result

    # цветная картинка с подсветкой горячих точек — для глаз
    sentinel.fetch_chip(meta["bbox"], fresh_from, fresh_to, now_png,
                        evalscript=sentinel.EVALSCRIPT_HOTSPOT,
                        fmt="image/png", width=512, height=512)
    result["chip"] = now_png if os.path.exists(now_png) else None

    after = _read(now_tif)
    result["hot_pixels"] = hot_pixels(after)

    dnbr_val, changed = None, None
    if os.path.exists(base_tif):
        before = _read(base_tif)
        nbr_b, nbr_a = nbr(before), nbr(after)
        if nbr_b is not None and nbr_a is not None:
            dnbr_val = round(nbr_b - nbr_a, 3)
        changed = change_fraction(before, after)
    result["dnbr"] = dnbr_val
    result["changed"] = round(changed, 3) if changed is not None else None

    firms_hits = firms_zone_stats.get(name, {}).get("count", 0)
    status, conf, ev = classify(result["hot_pixels"], dnbr_val, changed, firms_hits)
    result["status"], result["confidence"] = status, conf
    result["evidence"] = ev

    # сверка с тем, что известно из открытых источников
    known = meta.get("known_status")
    if known == "damaged" and status == "PRODUCING":
        result["evidence"].append(
            "РАСХОЖДЕНИЕ: по открытым данным линия повреждена — проверьте глазами")
    if known == "operating" and status in ("DESTROYED",):
        result["evidence"].append(
            "РАСХОЖДЕНИЕ: по открытым данным линия работала — проверьте глазами")
    return result


# ---------------------------------------------------------------------------
# Обнаружение судов прямо на снимке
# ---------------------------------------------------------------------------
def vessel_pixels(bands):
    """
    Считает пиксели, похожие на судно у причала.

    Физика простая: вода почти полностью поглощает ближний инфракрасный
    свет, поэтому на снимке в канале B08 море выглядит практически чёрным
    (отражение 0.01-0.03). Палуба, надстройка и борт судна отражают
    заметно больше (0.15-0.40). Значит яркое пятно на тёмной воде —
    это корпус судна.

    Зачем это нужно, если есть AIS: во-первых, независимая проверка —
    два источника лучше одного. Во-вторых, AIS можно выключить, а со
    снимка судно не спрячешь. Если у причала судно видно, а в AIS его
    нет, это само по себе интересная информация.

    Ограничение: газовоз длиной 315 м — это примерно 31 пиксель в длину
    и 5 в ширину, то есть около 150 пикселей. Отличить газовоз от
    буксира по снимку можно по размеру пятна, но различить два судна,
    стоящих борт к борту, уже нельзя.
    """
    valid = bands["mask"] > 0
    if valid.sum() == 0:
        return 0, 0.0
    bright = (bands["nir"] > 0.12) & valid
    return int(bright.sum()), float(bright.sum() / valid.sum())


def analyse_berth(name, meta, fresh_from, fresh_to):
    """Проверяет по снимку, стоит ли судно у причала."""
    safe = name.replace("/", "_")
    tif = os.path.join(config.CHIPS_DIR, f"berth_{safe}_{fresh_to}.tif")

    result = {"berth": name, "kind": meta["kind"], "venture": meta["venture"],
              "vessel_px": 0, "vessel_frac": None, "occupied": None,
              "snapshot_date": fresh_to}

    got = sentinel.fetch_chip(meta["bbox"], fresh_from, fresh_to, tif,
                              width=128, height=128)
    if not got:
        return result

    bands = _read(tif)
    px, frac = vessel_pixels(bands)
    result["vessel_px"] = px
    result["vessel_frac"] = round(frac, 3)
    # Больше 3% площади водной зоны занято ярким объектом — там судно.
    result["occupied"] = frac > 0.03
    return result


# ---------------------------------------------------------------------------
# Индикатор строительных работ
# ---------------------------------------------------------------------------
def analyse_site(name, meta, fresh_from, fresh_to, baseline_date):
    """
    Смотрит, меняется ли облик вспомогательной площадки.

    Для площадки складирования модулей (laydown yard) это прямой
    индикатор стройки: когда идут работы, она заполняется блоками
    оборудования, появляются новые твёрдые покрытия и проезды.

    Что видно на снимке 10 м/пиксель, а что нет. Видно: заполнение
    площадок складирования, новое покрытие, крупные модули и колонны,
    крупную технику. НЕ видно: отдельные грузовики и легковые машины
    (фура занимает 1-2 пикселя, этого мало для надёжного вывода).
    Поэтому «подвоз техники» мы ловим по судам через AIS, а стройку на
    берегу — по изменению облика площадок.
    """
    safe = name.replace("/", "_").replace(" ", "_")
    base = os.path.join(config.CHIPS_DIR, f"site_{safe}_baseline_{baseline_date}.tif")
    now = os.path.join(config.CHIPS_DIR, f"site_{safe}_{fresh_to}.tif")

    result = {"site": name, "note": meta.get("note", ""),
              "changed": None, "hot_pixels": 0, "construction_signal": None,
              "snapshot_date": fresh_to}

    if not os.path.exists(base):
        sentinel.fetch_chip(meta["bbox"], baseline_date, baseline_date,
                            base, width=256, height=256)

    got = sentinel.fetch_chip(meta["bbox"], fresh_from, fresh_to, now,
                              width=256, height=256)
    if not got:
        return result

    after = _read(now)
    result["hot_pixels"] = hot_pixels(after)

    if os.path.exists(base):
        before = _read(base)
        chg = change_fraction(before, after)
        result["changed"] = round(chg, 3) if chg is not None else None
        if chg is not None:
            result["construction_signal"] = chg > 0.20
    return result


def run(days_back: int = 14, with_berths: bool = True):
    """
    Разбирает все зоны по ОДНОМУ самому свежему снимку.

    Порядок такой:
      1) спрашиваем у каталога Copernicus, когда был последний пролёт
         спутника над Ras Laffan с приемлемой облачностью;
      2) все зоны разбираем по этой одной дате.

    Почему так, а не «что-нибудь за последние две недели». Во-первых,
    монитор должен показывать текущее положение дел, а не самую
    красивую картинку из окна. Во-вторых, при поиске по окну разные
    зоны могли получить снимки за разные даты — и сравнивать их между
    собой было уже некорректно.

    days_back здесь — это глубина поиска свежего кадра, а не период
    анализа. Если за это время пролётов не было, обход не начнётся.
    """
    import firms

    today = dt.date.today()

    # --- 1. Находим последний пригодный снимок ---
    # Это первое обращение к сети. Если связи нет, выходим сразу, а не
    # повторяем одну и ту же ошибку два десятка раз по каждой зоне.
    print("  ищу последний снимок ...")
    try:
        scene = sentinel.latest_scene(config.RLIC_BBOX, days_back)
    except Exception as exc:                          # noqa: BLE001
        s = str(exc)
        if "getaddrinfo" in s or "NameResolutionError" in s:
            msg = ("нет связи с интернетом или не отвечает DNS. "
                   "Проверьте: ping dataspace.copernicus.eu")
        else:
            msg = f"Copernicus недоступен: {s[:200]}"
        print(f"  [!] {msg}")
        return {
            "period": {"from": None, "to": None},
            "scene": None, "scene_error": msg,
            "trains": [], "berths": [], "sites": [], "firms_trend": [],
        }

    if not scene:
        msg = (f"За последние {days_back} дн. нет снимков с облачностью "
               f"ниже {config.MAX_CLOUD_COVER}%. Увеличьте days_back или "
               f"MAX_CLOUD_COVER в config.py")
        print(f"  [!] {msg}")
        return {
            "period": {"from": None, "to": None},
            "scene": None, "scene_error": msg,
            "trains": [], "berths": [], "sites": [], "firms_trend": [],
        }

    snap = scene["date"]
    print(f"  снимок от {snap}: {scene['age_days']} дн. назад, "
          f"облачность {scene['cloud']}%")
    if scene["prev_date"]:
        print(f"  предыдущий пригодный снимок: {scene['prev_date']}")

    # --- 2. Опорный снимок: самый чистый кадр февраля ---
    base = sentinel.best_scene(config.RLIC_BBOX,
                               config.BASELINE_DATE_FROM,
                               config.BASELINE_DATE_TO)
    baseline_date = base["date"] if base else config.BASELINE_DATE_TO
    if base:
        print(f"  опорный снимок: {baseline_date}, "
              f"облачность {round(base.get('cloud') or 0, 1)}%")

    # --- 3. Тепловые точки: берём за столько же дней, сколько прошло ---
    firms_days = max(1, min(scene["age_days"] + 1, 10))
    try:
        points = firms.fetch_hotspots(days=firms_days)
    except Exception as exc:                      # noqa: BLE001
        print(f"  [!] FIRMS недоступен: {exc}")
        points = []
    zone_stats = firms.summarize_by_zone(points, config.TRAINS) if points else {}

    # Дальше везде передаём snap и как начало, и как конец периода:
    # это и означает «ровно один снимок за эту дату».
    fresh_from = fresh_to = snap

    results = []
    for name, meta in config.TRAINS.items():
        print(f"  разбираю {name} ...")
        try:
            results.append(analyse_train(name, meta, zone_stats,
                                         fresh_from, fresh_to, baseline_date))
        except Exception as exc:                  # noqa: BLE001
            print(f"  [!] {name}: {exc}")
            results.append({"name": name, "venture": meta["venture"],
                            "mtpa": meta["mtpa"], "status": "ERROR",
                            "confidence": "none", "evidence": [str(exc)],
                            "chip": None, "hot_pixels": 0,
                            "dnbr": None, "changed": None,
                            "snapshot_date": snap,
                            "known_status": meta.get("known_status")})

    # --- Причалы: смотрим, стоит ли судно, независимо от AIS ---
    berths = []
    if with_berths:
        for name, meta in config.BERTHS.items():
            if meta["kind"] in ("anchorage", "support"):
                continue          # рейд слишком большой, вспомогательные неинтересны
            print(f"  смотрю причал {name} ...")
            try:
                berths.append(analyse_berth(name, meta, fresh_from, fresh_to))
            except Exception as exc:                  # noqa: BLE001
                print(f"  [!] причал {name}: {exc}")

    # --- Вспомогательные площадки: индикатор стройки ---
    sites = []
    for name, meta in config.OTHER_SITES.items():
        print(f"  смотрю площадку {name} ...")
        try:
            sites.append(analyse_site(name, meta, fresh_from, fresh_to,
                                      baseline_date))
        except Exception as exc:                      # noqa: BLE001
            print(f"  [!] площадка {name}: {exc}")

    return {
        "period": {"from": fresh_from, "to": fresh_to},
        # Всё, что нужно знать о снимке: дата, возраст, облачность,
        # дата предыдущего пролёта и опорная дата.
        "scene": {
            "date": snap,
            "age_days": scene["age_days"],
            "cloud": scene["cloud"],
            "prev_date": scene["prev_date"],
            "baseline_date": baseline_date,
            "available": scene["available"][:10],
        },
        "trains": results,
        "berths": berths,
        "sites": sites,
        "firms_trend": firms.venture_trend(points) if points else [],
    }
