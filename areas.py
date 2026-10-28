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
        "count": 0, "large_count": 0, "vessels": [],
        "px_size_m": None, "reliable_min_len_m": None,
        "image": None, "error": None, "warning": None,
    }

    # 1. Своя дата снимка для этой области
    try:
        scene = sentinel.latest_scene(meta["bbox"], days_back)
    except Exception as exc:                          # noqa: BLE001
        result["error"] = f"каталог недоступен: {str(exc)[:160]}"
        return result

    if not scene:
        result["error"] = (f"за {days_back} дн. нет снимков с облачностью "
                           f"ниже {config.MAX_CLOUD_COVER}%")
        return result

    result.update(date=scene["date"], age_days=scene["age_days"],
                  cloud=scene["cloud"], prev_date=scene["prev_date"])
    snap = scene["date"]
    px = meta.get("px", 1024)

    # 2. Считаем суда
    tif = os.path.join(config.CHIPS_DIR, f"area_{key}_{snap}.tif")
    try:
        got = sentinel.fetch_chip(meta["bbox"], snap, snap, tif,
                                  width=px, height=px)
    except Exception as exc:                          # noqa: BLE001
        result["error"] = f"снимок не скачался: {str(exc)[:160]}"
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
            result.update(count=found["count"],
                          large_count=found["large_count"],
                          px_size_m=found["px_size_m"],
                          reliable_min_len_m=found["reliable_min_len_m"],
                          vessels=found["vessels"][:40])

            # Если зона велика, а картинка мала, разрешение грубеет и
            # мелкие суда теряются. Молчать об этом нельзя: цифра
            # «судов N» выглядела бы полной, не будучи таковой.
            want = meta.get("min_vessel_len_m", config.SHIP_MIN_LEN_M)
            if found["threshold_too_low"]:
                result["warning"] = (
                    f"разрешение {found['px_size_m']} м/пиксель: надёжно "
                    f"видны суда от {found['reliable_min_len_m']} м, "
                    f"а порог стоит {want} м — мелкие суда посчитаны не все")
        except Exception as exc:                      # noqa: BLE001
            result["error"] = f"разбор не удался: {str(exc)[:160]}"
    else:
        result["error"] = f"зона не покрыта снимком от {snap}"

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
    try:
        got = sentinel.fetch_chip(meta["bbox"], snap, snap, png,
                                  evalscript=sentinel.EVALSCRIPT_TRUECOLOR,
                                  fmt="image/png",
                                  width=meta.get("px", 1024),
                                  height=meta.get("px", 1024))
        if not got:
            return None
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
        img.save(out, "JPEG", quality=82, optimize=True)
        return out
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
        "note": "крупнейший в мире комплекс сжижения газа",
        "min_vessel_len_m": config.SHIP_MIN_LEN_M,
        "px": 2048,
    }

    for key, meta in [("ras-laffan", rl_meta)] + list(config.STRAITS.items()):
        print(f"  область {meta.get('title', key)} ...")
        res = analyse_area(key, meta, days_back, with_images)
        if res.get("error"):
            print(f"    [!] {res['error']}")
        else:
            print(f"    снимок {res['date']} ({res['age_days']} дн. назад), "
                  f"судов {res['count']} (крупных {res['large_count']}), "
                  f"{res['px_size_m']} м/пиксель")
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
              f"{meta['title']} — снимок {scene['date']}, "
              f"облачность {scene['cloud']}%", fill=(255, 255, 0))

    img.save(out)
    return out, scene
