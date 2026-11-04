"""
areas.py — обработка областей наблюдения для карусели на сайте.

Область — это Ras Laffan или один из проливов. По каждой делаем одно
и то же: находим последний снимок, считаем на нём суда, сохраняем
картинку для сайта.

ПОЧЕМУ У КАЖДОЙ ОБЛАСТИ СВОЯ ДАТА. Ras Laffan, Ормуз и Баб-эль-Мандеб
разнесены на тысячи километров и попадают в разные витки спутника.
Над Ormuz он проходит в один день, над Баб-эль-Мандебом в другой,
и облачность у них разная. Поэтому единой «даты снимка» не существует,
и каждая область носит свою — она подписана прямо на слайде.
"""

import os
import datetime as dt

import config
import sentinel
import ships


def _read_bands(path):
    """Читает многоканальный снимок. Вынесено, чтобы не тянуть analyze."""
    import rasterio
    import numpy as np
    with rasterio.open(path) as src:
        arr = src.read().astype("float32")
    return {
        "blue": arr[0], "green": arr[1], "red": arr[2],
        "nir": arr[3], "swir1": arr[4], "swir2": arr[5],
        "mask": arr[6],
    }


def analyse_area(key, meta, days_back=14, want_image=True):
    """
    Полный разбор одной области.

    Возвращает словарь с датой снимка, числом судов и путём к картинке.
    Если снимка нет, поля с данными будут пустыми, но ключ и название
    останутся — чтобы на сайте было видно, что область наблюдается,
    просто сегодня её закрыли облака.
    """
    result = {
        "key": key,
        "title": meta.get("title", key),
        "note": meta.get("note", ""),
        "bbox": meta["bbox"],
        "date": None, "age_days": None, "cloud": None, "prev_date": None,
        "newer_cloudy": [], "typical_gap_days": None,
        "next_pass_estimate": None,
        "count": 0, "large_count": 0, "fixed_count": 0, "raw_count": 0,
        "vessels": [],
        "px_size_m": None, "reliable_min_len_m": None,
        "image": None, "error": None, "warning": None,
    }

    # 1. Своя дата снимка для этой области
    try:
        scene = sentinel.latest_scene(meta["bbox"], days_back)
    except Exception as exc:                          # noqa: BLE001
        result["error"] = f"catalogue unavailable: {str(exc)[:160]}"
        return result

    if not scene:
        result["error"] = (f"no image with cloud below "
                           f"{config.MAX_CLOUD_COVER}% in {days_back} days")
        return result

    result.update(date=scene["date"], age_days=scene["age_days"],
                  cloud=scene["cloud"], prev_date=scene["prev_date"],
                  newer_cloudy=scene.get("newer_cloudy") or [],
                  typical_gap_days=scene.get("typical_gap_days"),
                  next_pass_estimate=scene.get("next_pass_estimate"))
    snap = scene["date"]
    # Размер картинки берётся по пропорциям зоны, иначе снимок
    # растягивается, а длина судна считается по неверному масштабу.
    px_w, px_h = sentinel.chip_size(meta["bbox"], meta.get("px", 1024))

    # 2. Считаем суда
    tif = os.path.join(config.CHIPS_DIR, f"area_{key}_{snap}.tif")
    try:
        got = sentinel.fetch_chip(meta["bbox"], snap, snap, tif,
                                  width=px_w, height=px_h)
    except Exception as exc:                          # noqa: BLE001
        result["error"] = f"image download failed: {str(exc)[:160]}"
        return result

    if got:
        try:
            bands = _read_bands(tif)
            found = ships.detect(
                bands, meta["bbox"],
                nir_threshold=config.SHIP_NIR_THRESHOLD,
                min_len_m=meta.get("min_vessel_len_m", config.SHIP_MIN_LEN_M),
                max_len_m=config.SHIP_MAX_LEN_M,
            )
            # Отсеиваем стационарные объекты: буровые платформы,
            # выносные причалы и буи стоят на месте и попадали бы
            # в счёт каждый раз, завышая трафик.
            hist_path = os.path.join(config.STATE_DIR, f"seen_{key}.json")
            history = ships.load_history(hist_path)
            moving, fixed = ships.split_fixed(
                found["vessels"], history, snap,
                radius_m=config.FIXED_OBJECT_RADIUS_M,
                min_other_dates=config.FIXED_OBJECT_MIN_DATES)
            ships.save_history(hist_path,
                               ships.remember(history, found["vessels"], snap))

            large_moving = [v for v in moving if v["length_m"] >= 200]

            result.update(count=len(moving),
                          large_count=len(large_moving),
                          fixed_count=len(fixed),
                          raw_count=found["count"],
                          px_size_m=found["px_size_m"],
                          reliable_min_len_m=found["reliable_min_len_m"],
                          vessels=moving[:40])

            # Если зона велика, а картинка мала, разрешение грубеет и
            # мелкие суда теряются. Молчать об этом нельзя: цифра
            # «судов N» выглядела бы полной, не будучи таковой.
            want = meta.get("min_vessel_len_m", config.SHIP_MIN_LEN_M)
            if found["threshold_too_low"]:
                result["warning"] = (
                    f"resolution {found['px_size_m']} m/pixel: vessels are "
                    f"reliably visible from {found['reliable_min_len_m']} m, "
                    f"while the threshold is set at {want} m — smaller "
                    f"vessels are not all counted")
        except Exception as exc:                      # noqa: BLE001
            result["error"] = f"analysis failed: {str(exc)[:160]}"
    else:
        result["error"] = f"area not covered by the image of {snap}"

    # 3. Картинка для сайта
    if want_image:
        result["image"] = _make_image(key, meta, snap)

    return result


def _make_image(key, meta, snap):
    """
    Скачивает цветную картинку области и готовит её для сайта.

    Отдаём обычный цветной снимок: на нём человек сам видит суда как
    светлые чёрточки на тёмной воде и может проверить, что бот не
    выдумывает. Картинка ужимается до разумной ширины, иначе страница
    будет весить десятки мегабайт.
    """
    png = os.path.join(config.CHIPS_DIR, f"area_{key}_{snap}.png")
    # Размер по пропорциям зоны — та же причина, что и для снимка под
    # анализ. Эта картинка идёт прямо на сайт, и квадратный запрос на
    # прямоугольной зоне растягивал её на глазах у посетителя.
    w_px, h_px = sentinel.chip_size(meta["bbox"], meta.get("px", 1024))
    try:
        got = sentinel.fetch_chip(meta["bbox"], snap, snap, png,
                                  evalscript=sentinel.EVALSCRIPT_TRUECOLOR,
                                  fmt="image/png",
                                  width=w_px, height=h_px)
        if not got:
            return None

        # Самопроверка пропорций. Если картинка пришла не в тех
        # пропорциях, что зона на местности, снимок растянут — и это
        # надо увидеть сразу, а не разглядывать на сайте.
        try:
            from PIL import Image
            with Image.open(got) as im:
                iw, ih = im.size
            import math
            gw = ((meta["bbox"][2] - meta["bbox"][0]) * 111320.0
                  * math.cos(math.radians((meta["bbox"][1]
                                           + meta["bbox"][3]) / 2)))
            gh = (meta["bbox"][3] - meta["bbox"][1]) * 110540.0
            want, got_ratio = gw / gh, iw / ih
            print(f"    картинка {iw}x{ih}, пропорция {got_ratio:.2f} "
                  f"при нужной {want:.2f}")
            if abs(got_ratio - want) / want > 0.05:
                print(f"    [!] снимок растянут в "
                      f"{max(got_ratio, want) / min(got_ratio, want):.2f} раза")
        except Exception:                             # noqa: BLE001
            pass

        return _shrink(png)
    except Exception:                                 # noqa: BLE001
        return None


def _shrink(path):
    """Уменьшает картинку до ширины из настроек и сжимает."""
    try:
        from PIL import Image
        img = Image.open(path).convert("RGB")
        w = config.CAROUSEL_WIDTH_PX
        if img.width > w:
            h = int(img.height * w / img.width)
            img = img.resize((w, h), Image.LANCZOS)
        out = path.replace(".png", "_web.jpg")
        import sentinel
        return sentinel.save_safely(
            lambda p: img.save(p, "JPEG", quality=82, optimize=True), out)
    except Exception:                                 # noqa: BLE001
        return path


def run(days_back=14, with_images=True):
    """
    Разбирает все области: Ras Laffan и проливы.

    Ras Laffan берётся из общей рамки комплекса — на ней видно и порт,
    и подходы, и рейд ожидания.
    """
    areas = {}

    rl_meta = {
        "title": "Ras Laffan",
        "bbox": config.RLIC_BBOX,
        "note": "the largest LNG liquefaction complex in the world",
        "min_vessel_len_m": config.SHIP_MIN_LEN_M,
        "px": 2048,
    }

    for key, meta in [("ras-laffan", rl_meta)] + list(config.STRAITS.items()):
        print(f"  область {meta.get('title', key)} ...")
        res = analyse_area(key, meta, days_back, with_images)
        if res.get("error"):
            print(f"    [!] {res['error']}")
        else:
            fx = res.get("fixed_count") or 0
            fx_txt = f", стационарных объектов {fx} (платформы, буи)" if fx else ""
            print(f"    снимок {res['date']} ({res['age_days']} дн. назад), "
                  f"судов {res['count']} (крупных {res['large_count']})"
                  f"{fx_txt}, {res['px_size_m']} м/пиксель")
            if res.get("warning"):
                print(f"    [!] {res['warning']}")
        areas[key] = res

    return areas


# ---------------------------------------------------------------------------
# Калибровка проливов
# ---------------------------------------------------------------------------
def calibrate(key):
    """
    Снимок пролива с координатной сеткой — чтобы проверить рамку.

    Тот же приём, что и для Ras Laffan: поверх снимка рисуется сетка
    с подписями широты и долготы. Открываете картинку и смотрите,
    попадает ли рамка на судоходный коридор, а не на берег.
    """
    from PIL import Image, ImageDraw

    meta = config.STRAITS.get(key)
    if not meta:
        raise ValueError(f"Неизвестный пролив: {key}. "
                         f"Доступны: {', '.join(config.STRAITS)}")

    today = dt.date.today()
    scene = sentinel.latest_scene(meta["bbox"], 60, max_cloud=40)
    if not scene:
        raise RuntimeError("За последние 60 дней нет подходящих снимков. "
                           "Поднимите MAX_CLOUD_COVER или подождите.")

    raw = os.path.join(config.CHIPS_DIR, f"calib_{key}_raw.png")
    out = os.path.join(config.CHIPS_DIR, f"calib_{key}_grid.png")
    sentinel.fetch_chip(meta["bbox"], scene["date"], scene["date"], raw,
                        evalscript=sentinel.EVALSCRIPT_TRUECOLOR,
                        fmt="image/png", width=1800, height=1800,
                        max_cloud=40)

    img = Image.open(raw).convert("RGB")
    W, H = img.size
    draw = ImageDraw.Draw(img)
    w, s, e, n = meta["bbox"]

    def to_px(lon, lat):
        return ((lon - w) / (e - w) * W, (n - lat) / (n - s) * H)

    # Сетка через 0.05 градуса — примерно каждые 5 километров
    step = 0.05
    lon = round(w, 2)
    while lon <= e:
        x, _ = to_px(lon, n)
        draw.line([(x, 0), (x, H)], fill=(0, 255, 255), width=1)
        draw.text((x + 3, 5), f"{lon:.2f}E", fill=(0, 255, 255))
        lon += step
    lat = round(s, 2)
    while lat <= n:
        _, y = to_px(w, lat)
        draw.line([(0, y), (W, y)], fill=(0, 255, 255), width=1)
        draw.text((5, y + 3), f"{lat:.2f}N", fill=(0, 255, 255))
        lat += step

    draw.text((10, H - 24),
              f"{meta['title']} — image {scene['date']}, "
              f"cloud {scene['cloud']}%", fill=(255, 255, 0))

    import sentinel
    # Через save_safely: калибровочную картинку бот открывает сразу
    # после создания, и на следующем запуске она может быть занята
    # просмотрщиком.
    return sentinel.save_safely(img.save, out), scene
