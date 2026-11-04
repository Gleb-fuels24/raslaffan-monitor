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


def _match_levels(before, after, valid, bands=("red", "green", "nir", "swir1")):
    """
    Приводит яркость второго снимка к яркости первого.

    ЗАЧЕМ ЭТО НУЖНО. Опорный снимок сделан в феврале, свежий — в
    октябре. За восемь месяцев меняется высота солнца, прозрачность
    воздуха и влажность. Отражение всей сцены уезжает целиком, на
    десятые доли, — и если сравнивать напрямую, «изменившимся»
    оказывается всё подряд.

    Это не теория: контрольный резервуарный парк, который физически
    не менялся, показывал 80% изменений. Контрольные площадки ровно
    для того и заведены, чтобы такую поломку было видно.

    Лечение простое и устойчивое: по каждому каналу второй снимок
    линейно подгоняется к первому по медиане и среднему абсолютному
    отклонению. Общий сдвиг освещённости при этом сокращается, а
    локальные изменения — новое здание, залитая площадка, вырытый
    котлован — остаются, потому что они занимают малую долю кадра и
    на медиану почти не влияют.
    """
    out = {}
    for b in bands:
        x, y = before[b][valid], after[b][valid]
        if x.size == 0:
            out[b] = after[b]
            continue
        mx, my = float(np.median(x)), float(np.median(y))
        sx = float(np.median(np.abs(x - mx))) or 1e-6
        sy = float(np.median(np.abs(y - my))) or 1e-6
        # Коэффициент ограничен: при почти однородном кадре отношение
        # отклонений взлетает и подгонка превращается в усилитель шума.
        k = min(max(sx / sy, 0.25), 4.0)
        out[b] = (after[b] - my) * k + mx
    return out


def change_detail(before, after):
    """
    Что изменилось на площадке и В КАКУЮ СТОРОНУ.

    Доли мало. «Изменилось 24%» одинаково описывает площадку, которую
    застроили модулями, и площадку, с которой всё вывезли. А для
    подрядчика это противоположные новости: в первом случае стройка
    разворачивается, во втором сворачивается.

    Направление берём по знаку изменения яркости среди изменившихся
    пикселей. Завоз на склад — это металл, бетон и новое покрытие
    поверх голого грунта: отражение растёт. Вывоз — обратное.

    Признак грубый, и называть его доказательством нельзя: мокрый
    грунт после дождя тоже темнеет. Поэтому направление ставится,
    только когда перевес заметный, иначе честное «не разобрать».
    """
    valid = (before["mask"] > 0) & (after["mask"] > 0)
    if valid.sum() == 0:
        return None

    adj = _match_levels(before, after, valid)
    diff = np.sqrt(
        (adj["red"] - before["red"]) ** 2 +
        (adj["green"] - before["green"]) ** 2 +
        (adj["nir"] - before["nir"]) ** 2 +
        (adj["swir1"] - before["swir1"]) ** 2
    )
    changed = (diff > 0.08) & valid
    n = int(changed.sum())
    frac = float(n / valid.sum())

    if n == 0:
        return {"frac": frac, "up": 0.0, "down": 0.0, "direction": None}

    # Яркость в видимом диапазоне: по ней и судим о появлении или
    # исчезновении материала.
    bright_before = (before["red"] + before["green"]) / 2.0
    bright_after = (adj["red"] + adj["green"]) / 2.0
    delta = (bright_after - bright_before)[changed]
    up = float((delta > 0.02).sum()) / n
    down = float((delta < -0.02).sum()) / n

    if up > down * 1.5 and up > 0.4:
        direction = "filling"
    elif down > up * 1.5 and down > 0.4:
        direction = "clearing"
    else:
        direction = None

    return {"frac": frac, "up": round(up, 2), "down": round(down, 2),
            "direction": direction}


def change_fraction(before, after):
    """Только доля изменений — для мест, где направление не нужно."""
    d = change_detail(before, after)
    return None if d is None else d["frac"]


def classify(hot_count, dnbr, changed_frac, firms_hits):
    """Превращает числа в статус и оценку уверенности."""
    evidence = []

    thermally_active = hot_count >= config.HOT_PIXEL_MIN_COUNT or firms_hits > 0
    if hot_count:
        evidence.append(f"hot SWIR pixels: {hot_count}")
    if firms_hits:
        evidence.append(f"VIIRS thermal hits: {firms_hits}")

    if thermally_active:
        conf = "high" if (hot_count >= 4 and firms_hits > 0) else "medium"
        return "PRODUCING", conf, evidence

    if dnbr is not None and dnbr >= config.DNBR_DAMAGE_THRESHOLD:
        evidence.append(f"dNBR = {dnbr:.2f} (severe burn signature)")
        if changed_frac is not None and changed_frac > 0.30:
            evidence.append(f"{changed_frac:.0%} of the area changed — looks like works")
            return "REBUILDING", "low", evidence
        return "DESTROYED", "medium", evidence

    if changed_frac is not None and changed_frac > 0.25:
        evidence.append(f"{changed_frac:.0%} of the area changed, no burn signature")
        return "REBUILDING", "low", evidence

    evidence.append("no heat, site geometry unchanged")
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
        result["evidence"].append(f"zone not covered by the image of {fresh_to}")
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
            "CONFLICT: open sources say this train is damaged — check by eye")
    if known == "operating" and status in ("DESTROYED",):
        result["evidence"].append(
            "CONFLICT: open sources say this train was running — check by eye")
    return result


# ---------------------------------------------------------------------------
# Обнаружение судов прямо на снимке
# ---------------------------------------------------------------------------
def vessel_pixels(bands, baseline=None, want_mask=False):
    """
    Считает пиксели, похожие на судно у причала.

    Физика простая: вода почти полностью поглощает ближний инфракрасный
    свет, поэтому на снимке в канале B08 море выглядит практически
    чёрным (отражение 0.01-0.03). Палуба, надстройка и борт судна
    отражают заметно больше (0.15-0.40).

    ПОЧЕМУ ПРОСТОГО ПОРОГА НЕ ХВАТАЕТ. В рамку причала попадает не
    только вода, но и сам пирс, эстакада, мол и берег. В ближнем ИК
    они светятся так же ярко, как судно. Прежняя версия считала долю
    ярких пикселей от всей рамки — а значит мерила в основном бетон,
    и доля была высокой независимо от того, стоит там судно или нет.
    Порог «больше 3% ярких» при этом не срабатывал как надо: у причала
    с большой эстакадой он давал судно всегда, у причала в воде —
    почти никогда.

    КАК СЕЙЧАС. Два отсева подряд:

    1. По воде. Вода почти не отражает коротковолновый ИК, бетон и
       песок отражают. Поэтому за воду считаются пиксели с низким
       SWIR — и судно ищется только среди них и рядом с ними.

    2. По опорному снимку. Если передан снимок того же причала за
       спокойную дату, из счёта вычитается всё, что светилось и
       тогда: пирс, эстакада, берег. Остаётся то, чего раньше не
       было, то есть судно. Это надёжнее любого порога, потому что
       сооружения стоят на месте, а судно приходит и уходит.

    Возвращает (число пикселей судна, доля от водной части рамки).
    """
    valid = bands["mask"] > 0
    if valid.sum() == 0:
        return 0, 0.0

    bright = (bands["nir"] > 0.12) & valid
    swir = bands.get("swir1")
    water = ((swir < 0.12) & valid) if swir is not None else None

    base_nir = (baseline or {}).get("nir")
    have_base = (base_nir is not None
                 and base_nir.shape == bands["nir"].shape)

    if have_base:
        # Опорный снимок уже убирает всё неподвижное — и пирс, и берег,
        # — поэтому ограничивать поиск водой не нужно. Это важно: судно
        # шире тридцати метров, и маска воды отрезала бы ему середину,
        # потому что в SWIR корпус яркий и водой не считается.
        #
        # Размытие опоры на ОДИН пиксель, а не больше: оно страхует от
        # сдвига снимков на пиксель, но каждый лишний пиксель срезает
        # борт судна, стоящего вплотную к причалу.
        bright = bright & ~_dilate(base_nir > 0.12, 1)
        area = water if water is not None and water.sum() else valid
    elif water is not None and water.sum() > 0:
        # Без опорного снимка отделить судно от пирса порогом
        # НЕЛЬЗЯ: в ближнем ИК бетон и палуба светятся одинаково, а
        # пирс вплотную к воде, то есть любой разумный запас вокруг
        # воды захватывает и его. Поэтому здесь считается честное
        # «ярко и прямо на воде» — число получится заниженным, и
        # analyse_berth по нему вердикт не выносит, а помечает причал
        # как непроверенный.
        bright = bright & water
        area = water
    else:
        area = valid

    denom = max(int(area.sum()), 1)
    if want_mask:
        return (int(bright.sum()), float(bright.sum() / denom), bright,
                bool(have_base))
    return int(bright.sum()), float(bright.sum() / denom)


def _dilate(mask, radius=1):
    """
    Расширяет маску на radius пикселей во все стороны.

    Нужно по двум причинам: судно стоит ВПЛОТНУЮ к причалу, и без
    запаса его край срезается; а опорный снимок может быть сдвинут
    на пиксель-другой, и без запаса кромка пирса засчиталась бы
    как новое судно.
    """
    import numpy as np
    out = mask.copy()
    for _ in range(max(0, int(radius))):
        grown = out.copy()
        grown[1:, :] |= out[:-1, :]
        grown[:-1, :] |= out[1:, :]
        grown[:, 1:] |= out[:, :-1]
        grown[:, :-1] |= out[:, 1:]
        out = grown
    return out


def analyse_berth(name, meta, fresh_from, fresh_to, baseline_date=None):
    """
    Проверяет по снимку, стоит ли судно у причала.

    Размер картинки берётся по пропорциям причала, а не квадратом:
    причалы вытянутые, и квадратная картинка растягивала бы их.
    Длинная сторона 256 точек — это 5-7 метров на пиксель, то есть
    газовоз занимает около 50 пикселей в длину и уверенно виден.

    baseline_date — дата спокойного снимка. По нему вычитаются
    неподвижные сооружения: пирс, эстакада, мол. Без него причал
    с большой эстакадой выглядит занятым всегда.
    """
    safe = name.replace("/", "_")
    tif = os.path.join(config.CHIPS_DIR, f"berth_{safe}_{fresh_to}.tif")

    result = {"berth": name, "kind": meta["kind"], "venture": meta["venture"],
              "vessel_px": 0, "vessel_frac": None, "occupied": None,
              "snapshot_date": fresh_to, "baseline_used": False}

    w_px, h_px = sentinel.chip_size(meta["bbox"], 256)
    got = sentinel.fetch_chip(meta["bbox"], fresh_from, fresh_to, tif,
                              width=w_px, height=h_px)
    if not got:
        return result

    bands = _read(tif)

    # Опорный снимок причала. Качается один раз и дальше лежит на диске.
    baseline = None
    if baseline_date:
        # Размер в имени файла — чтобы опорный снимок, скачанный
        # прежней версией в другом разрешении, не подхватывался.
        # Именно это и случилось: старые файлы 128x128 читались, по
        # размеру не совпадали с новыми 235x256, молча не применялись
        # — а причал всё равно объявлялся проверенным. В итоге
        # эстакада засчитывалась как судно.
        base = os.path.join(
            config.CHIPS_DIR,
            f"berth_{safe}_baseline_{baseline_date}_{w_px}x{h_px}.tif")
        if not os.path.exists(base):
            try:
                sentinel.fetch_chip(meta["bbox"], baseline_date, baseline_date,
                                    base, width=w_px, height=h_px)
            except Exception:                         # noqa: BLE001
                pass
        if os.path.exists(base):
            try:
                baseline = _read(base)
            except Exception:                         # noqa: BLE001
                baseline = None

    px, frac, mask, used_base = vessel_pixels(bands, baseline,
                                              want_mask=True)
    # ВАЖНО: признак ставится по тому, что опора ДЕЙСТВИТЕЛЬНО
    # применилась, а не по тому, что файл нашёлся на диске.
    result["baseline_used"] = used_base
    result["vessel_px"] = px
    result["vessel_frac"] = round(frac, 3)

    # Считаем не «сколько ярких пикселей», а СКОЛЬКО ОБЪЕКТОВ и
    # какого они размера.
    #
    # Зачем. Сумма ярких пикселей одинаково набирается одним судном в
    # триста метров и десятком случайных пятен по сорок метров: новый
    # контейнер на причале, передвинутый кран, блик на мокром бетоне.
    # По сумме бот насчитывал суда там, где их не было.
    #
    # Судно отличается формой: это одно СВЯЗНОЕ и ВЫТЯНУТОЕ пятно
    # длиной от восьмидесяти метров. Поэтому пятна собираются в
    # объекты, и каждый проверяется по длине и вытянутости.
    import ships
    px_x = ((meta["bbox"][2] - meta["bbox"][0]) * 100100.0 / max(w_px, 1))
    px_y = ((meta["bbox"][3] - meta["bbox"][1]) * 110540.0 / max(h_px, 1))
    px_m = (px_x + px_y) / 2.0

    vessels = []
    if px:
        labels, count = ships.label_blobs(mask)
        for i in range(1, count + 1):
            ys, xs = np.nonzero(labels == i)
            if ys.size < 4:
                continue
            length_px = max(ys.max() - ys.min(), xs.max() - xs.min()) + 1
            width_px = min(ys.max() - ys.min(), xs.max() - xs.min()) + 1
            length_m = length_px * px_m
            elong = length_px / max(width_px, 1)
            if length_m < config.SHIP_MIN_LEN_M:
                continue
            # Вытянутость: судно длиннее своей ширины минимум вдвое.
            # Квадратное пятно такого размера — скорее штабель или
            # здание, чем корпус.
            if elong < 1.8:
                continue
            vessels.append({"length_m": int(length_m),
                            "area_m2": int(ys.size * px_x * px_y)})

    vessels.sort(key=lambda v: -v["length_m"])
    result["vessels"] = vessels[:6]
    result["vessel_count"] = len(vessels)

    # Порог по ПЛОЩАДИ В МЕТРАХ, а не по доле кадра: доля зависит от
    # размера рамки, и один и тот же порог означал бы разное на
    # большом и маленьком причале. Судно от 80 м длиной и 15 м
    # шириной — это около 1200 кв. м.
    result["vessel_area_m2"] = sum(v["area_m2"] for v in vessels)

    # Вердикт выносится ТОЛЬКО при наличии опорного снимка. Без него
    # яркое пятно у причала с равным успехом и судно, и эстакада, и
    # «занято» было бы догадкой, выданной за измерение. None означает
    # «не проверено» и так и показывается.
    #
    # Занято — когда найден хотя бы один объект, прошедший проверку по
    # длине и вытянутости. Не «набралось столько-то ярких пикселей».
    result["occupied"] = (bool(vessels) if result["baseline_used"] else None)
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
              # control=True — площадка-контроль (резервуарный парк).
              # Она не должна меняться, и в индекс активности не идёт:
              # иначе контрольные площадки разбавляли бы долю тех, где
              # работы действительно видны.
              "control": bool(meta.get("control")),
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
        d = change_detail(before, after)
        if d is not None:
            result["changed"] = round(d["frac"], 3)
            result["direction"] = d["direction"]
            result["changed_up"] = d["up"]
            result["changed_down"] = d["down"]
            # Признаком РАБОТ считается только заполнение площадки.
            # Площадка, с которой вывезли всё подчистую, тоже даёт
            # большую долю изменений, но означает обратное: работы
            # сворачиваются. Раньше оба случая шли в индекс как
            # стройка.
            result["construction_signal"] = (d["frac"] > 0.20
                                             and d["direction"] != "clearing")
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
    # Берём с запасом, а не ровно по возрасту снимка: лишние сутки
    # ложатся в историю и график растёт быстрее. Сутки, уже лежащие в
    # истории, просто перезапишутся теми же значениями.
    firms_days = max(config.FLARE_FETCH_DAYS,
                     min(scene["age_days"] + 1, 10))
    try:
        points = firms.fetch_hotspots(days=firms_days)
        firms_note = firms.status_note()
    except Exception as exc:                      # noqa: BLE001
        print(f"  [!] FIRMS недоступен: {exc}")
        points = []
        firms_note = f"VIIRS data was not received: {str(exc)[:140]}"
    if firms_note:
        print(f"  VIIRS: {firms_note}")

    # Дописываем сутки в историю. Делаем это только если запрос прошёл:
    # иначе дни, по которым данных не получили, попали бы в историю
    # нулями — то есть поломка записалась бы как «факелы не горели».
    flare_trend, flare_span = [], None
    if points or not firms_note:
        firms.remember_days(points, firms_days)
    try:
        flare_trend = firms.history_trend(config.FLARE_HISTORY_DAYS)
        flare_span = firms.history_span()
    except Exception as exc:                      # noqa: BLE001
        print(f"  [!] история факела не прочиталась: {exc}")
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
                berths.append(analyse_berth(name, meta, fresh_from, fresh_to,
                                            baseline_date))
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
        # Ряд берётся из НАКОПЛЕННОЙ истории, а не только из нынешнего
        # окна. Раньше на графике стояло пять-шесть столбиков — ровно
        # то, что успели скачать в этот запуск, — и сравнить сегодня с
        # прошлым месяцем было нечем. Теперь каждый запуск дописывает
        # свои сутки, и история живёт дольше, чем FIRMS её отдаёт.
        "firms_trend": flare_trend,
        "firms_history": flare_span,
        # Почему ряд пуст: «факелы не горят» и «данные не пришли» —
        # разные вещи, и витрина обязана их различать.
        "firms_note": firms_note,
    }
