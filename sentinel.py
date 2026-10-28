"""
sentinel.py — получение бесплатных снимков Sentinel-2 через Copernicus.

Sentinel-2 — пара европейских спутников, которые снимают всю Землю
каждые 5 дней и бесплатно раздают снимки. Разрешение 10 метров на пиксель:
этого хватает, чтобы увидеть, стоит ли здание, идёт ли стройка, горит ли
факел, но недостаточно, чтобы разглядеть отдельную трубу.

Мы используем Sentinel Hub Process API — он позволяет попросить вырезку
только по нужной зоне вместо скачивания гигабайтного файла на всю сцену.
"""

import os
import time
import datetime as dt
from typing import Optional

import requests

import config

TOKEN_URL = ("https://identity.dataspace.copernicus.eu/auth/realms/CDSE"
             "/protocol/openid-connect/token")
PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"
# Адрес каталога у разных установок Copernicus отличается.
# Пробуем по очереди, пока один не ответит.
CATALOG_URLS = [
    "https://sh.dataspace.copernicus.eu/api/v1/catalog/1.0.0/search",
    "https://sh.dataspace.copernicus.eu/catalog/v1/search",
]
CATALOG_URL = CATALOG_URLS[0]   # оставлено для совместимости

_token_cache = {"value": None, "expires_at": 0.0}


# ---------------------------------------------------------------------------
# Авторизация
# ---------------------------------------------------------------------------
def get_token() -> str:
    """Получает токен доступа. Токен живёт ~10 минут, поэтому кэшируем."""
    now = time.time()
    if _token_cache["value"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["value"]

    if not config.CDSE_CLIENT_ID or not config.CDSE_CLIENT_SECRET:
        raise RuntimeError(
            "Не заданы CDSE_CLIENT_ID / CDSE_CLIENT_SECRET в файле .env. "
            "Создайте OAuth-клиент на dataspace.copernicus.eu."
        )

    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": config.CDSE_CLIENT_ID,
            "client_secret": config.CDSE_CLIENT_SECRET,
        },
        timeout=60,
    )
    resp.raise_for_status()
    payload = resp.json()
    _token_cache["value"] = payload["access_token"]
    _token_cache["expires_at"] = now + payload.get("expires_in", 600)
    return _token_cache["value"]


# ---------------------------------------------------------------------------
# Поиск доступных дат съёмки
# ---------------------------------------------------------------------------
def find_scenes(bbox, date_from: str, date_to: str, max_cloud: int = None):
    """
    Возвращает список дат, в которые есть снимок нужной зоны
    с облачностью не выше max_cloud. Новые даты идут первыми.

    Про фильтр облачности. Каталог умеет отсеивать облачные снимки на
    своей стороне, но для этого нужен особый язык запросов, и его
    синтаксис у разных установок Copernicus отличается. Поэтому мы
    просим ВСЕ снимки за период и отсеиваем облачные уже у себя:
    так запрос остаётся простым и не ломается при смене версии API.
    Снимков за две недели по одной точке единицы, лишнего трафика нет.
    """
    max_cloud = max_cloud if max_cloud is not None else config.MAX_CLOUD_COVER
    body = {
        "collections": ["sentinel-2-l2a"],
        "datetime": f"{date_from}T00:00:00Z/{date_to}T23:59:59Z",
        "bbox": bbox,
        "limit": 100,
    }
    headers = {"Authorization": f"Bearer {get_token()}",
               "Content-Type": "application/json"}

    # У разных установок Copernicus каталог живёт по разным адресам.
    # Пробуем известные по очереди, запоминая, какой сработал.
    last_err = None
    for url in CATALOG_URLS:
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=120)
        except requests.RequestException as exc:
            last_err = f"{url}: {exc}"
            continue

        if resp.status_code == 200:
            features = resp.json().get("features", [])
            break

        # Сервер объясняет в теле ответа, что именно ему не понравилось.
        # Без этого текста ошибку 400 чинить вслепую.
        last_err = (f"{url} вернул {resp.status_code}: "
                    f"{resp.text[:400]}")
    else:
        raise RuntimeError("Каталог Copernicus не отвечает корректно. "
                           f"{last_err}")

    scenes = []
    for f in features:
        props = f.get("properties", {})
        stamp = props.get("datetime", "")[:10]
        cloud = props.get("eo:cloud_cover")
        if cloud is None:
            cloud = props.get("cloudCover", 0)
        # отсеиваем облачные здесь, а не на сервере
        if cloud is not None and cloud >= max_cloud:
            continue
        if stamp:
            scenes.append({"date": stamp, "cloud": cloud or 0})
    # убираем дубликаты дат, оставляя самый чистый снимок за день
    best = {}
    for s in scenes:
        if s["date"] not in best or s["cloud"] < best[s["date"]]["cloud"]:
            best[s["date"]] = s
    return sorted(best.values(), key=lambda x: x["date"], reverse=True)


def latest_scene(bbox=None, days_back: int = 14, max_cloud: int = None):
    """
    Находит САМЫЙ СВЕЖИЙ пригодный снимок и возвращает сведения о нём.

    Зачем это нужно. Раньше бот просил «что-нибудь за последние две
    недели» и сервер отдавал самый чистый кадр из окна. Получалось два
    неприятных следствия: картина могла быть двухнедельной давности,
    и у разных зон могли оказаться снимки за РАЗНЫЕ даты — то есть
    зоны было некорректно сравнивать между собой.

    Теперь дата выбирается один раз на весь обход, и все зоны
    анализируются по одному и тому же пролёту спутника.

    Возвращает {"date", "cloud", "age_days", "prev_date", "available"}
    либо None, если за период пригодных снимков нет.
    """
    bbox = bbox or config.RLIC_BBOX
    today = dt.date.today()
    date_from = (today - dt.timedelta(days=days_back)).isoformat()
    scenes = find_scenes(bbox, date_from, today.isoformat(), max_cloud)
    if not scenes:
        return None

    newest = scenes[0]
    age = (today - dt.date.fromisoformat(newest["date"])).days
    return {
        "date": newest["date"],
        "cloud": round(newest.get("cloud") or 0, 1),
        "age_days": age,
        "prev_date": scenes[1]["date"] if len(scenes) > 1 else None,
        "available": [s["date"] for s in scenes],
    }


def best_scene(bbox, date_from: str, date_to: str, max_cloud: int = None):
    """
    Находит самый ЧИСТЫЙ снимок за период.

    Используется для опорного периода (февраль 2026): это эталон, с
    которым сравниваются все новые снимки, и здесь важна не свежесть,
    а качество кадра.
    """
    scenes = find_scenes(bbox, date_from, date_to, max_cloud)
    if not scenes:
        return None
    return min(scenes, key=lambda s: s.get("cloud") or 0)


# ---------------------------------------------------------------------------
# Скрипты обработки (evalscript) — выполняются на сервере Copernicus
# ---------------------------------------------------------------------------

# Многоканальный «научный» снимок для анализа.
# Отдаёт сырые значения отражения в 6 каналах + маску валидных пикселей.
EVALSCRIPT_ANALYTIC = """
//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B02","B03","B04","B08","B11","B12","dataMask"] }],
    output: { bands: 7, sampleType: "FLOAT32" }
  };
}
function evaluatePixel(s) {
  return [s.B02, s.B03, s.B04, s.B08, s.B11, s.B12, s.dataMask];
}
"""

# Обычная цветная картинка «как видит глаз» — для визуальной проверки.
EVALSCRIPT_TRUECOLOR = """
//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B02","B03","B04"] }],
    output: { bands: 3, sampleType: "AUTO" }
  };
}
function evaluatePixel(s) {
  // множитель 3.0 делает тёмный промышленный снимок читаемым
  return [s.B04 * 3.0, s.B03 * 3.0, s.B02 * 3.0];
}
"""

# Подсветка горячих точек: факелы и раскалённое оборудование светятся
# красным поверх серой подложки. Удобно показывать человеку.
EVALSCRIPT_HOTSPOT = """
//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B04","B08","B11","B12"] }],
    output: { bands: 3, sampleType: "AUTO" }
  };
}
function evaluatePixel(s) {
  var hot = (s.B12 > 0.15) && (s.B12 > 1.4 * s.B11) && (s.B12 > 1.4 * s.B08);
  var grey = s.B04 * 2.5;
  if (hot) return [1.0, 0.25, 0.0];   // яркий оранжевый
  return [grey, grey, grey];
}
"""


# ---------------------------------------------------------------------------
# Загрузка вырезки
# ---------------------------------------------------------------------------
def fetch_chip(bbox, date_from: str, date_to: str, out_path: str,
               evalscript: str = EVALSCRIPT_ANALYTIC,
               fmt: str = "image/tiff",
               width: int = 512, height: int = 512,
               max_cloud: int = None) -> Optional[str]:
    """
    Скачивает вырезку (chip) по зоне bbox за период и сохраняет в out_path.

    fmt: "image/tiff" для анализа, "image/png" для картинки.
    Если за период нет подходящего снимка, возвращает None.
    """
    max_cloud = max_cloud if max_cloud is not None else config.MAX_CLOUD_COVER
    body = {
        "input": {
            "bounds": {
                "bbox": bbox,
                "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"},
            },
            "data": [{
                "type": "sentinel-2-l2a",
                "dataFilter": {
                    "timeRange": {"from": f"{date_from}T00:00:00Z",
                                  "to": f"{date_to}T23:59:59Z"},
                    "maxCloudCoverage": max_cloud,
                    # mostRecent, а не leastCC: нам нужна свежая картина,
                    # а не идеально чистая. Фильтр по облачности стоит
                    # строкой выше, так что мусор и так отсеян.
                    # Когда даты from и to совпадают, выбор не нужен
                    # вовсе — запрашивается конкретный пролёт.
                    "mosaickingOrder": "mostRecent",
                },
            }],
        },
        "output": {
            "width": width,
            "height": height,
            "responses": [{"identifier": "default", "format": {"type": fmt}}],
        },
        "evalscript": evalscript,
    }

    resp = requests.post(
        PROCESS_URL,
        headers={"Authorization": f"Bearer {get_token()}",
                 "Accept": fmt,
                 "Content-Type": "application/json"},
        json=body,
        timeout=180,
    )

    if resp.status_code == 404 or (resp.status_code == 200
                                   and len(resp.content) < 1000):
        return None   # за эту дату зона не покрыта снимком — это норма

    if resp.status_code != 200:
        # Показываем ответ сервера: без него ошибку не починить.
        # Самая частая причина 400 здесь — перепутанные местами
        # широта и долгота в рамке зоны.
        raise RuntimeError(f"Copernicus вернул {resp.status_code}: "
                           f"{resp.text[:400]}")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as fh:
        fh.write(resp.content)
    return out_path


# ---------------------------------------------------------------------------
# Калибровка: общий снимок с координатной сеткой
# ---------------------------------------------------------------------------
def make_calibration_image(out_path: str = None) -> str:
    """
    Скачивает снимок всего Ras Laffan и рисует поверх координатную сетку
    с подписями широты и долготы, а также текущие рамки из config.py.

    Откройте получившийся PNG, найдите реальные линии и причалы и
    перепишите координаты в config.py. Делается один раз.
    """
    from PIL import Image, ImageDraw

    out_path = out_path or os.path.join(config.CHIPS_DIR, "calibration_grid.png")
    raw = os.path.join(config.CHIPS_DIR, "calibration_raw.png")

    today = dt.date.today().isoformat()
    start = (dt.date.today() - dt.timedelta(days=60)).isoformat()

    got = fetch_chip(config.RLIC_BBOX, start, today, raw,
                     evalscript=EVALSCRIPT_TRUECOLOR, fmt="image/png",
                     width=2048, height=1400, max_cloud=35)
    if not got:
        raise RuntimeError("За последние 60 дней не нашлось снимка без облаков. "
                           "Увеличьте период или порог облачности.")

    img = Image.open(raw).convert("RGB")
    W, H = img.size
    draw = ImageDraw.Draw(img)
    w, s, e, n = config.RLIC_BBOX

    def to_px(lon, lat):
        x = (lon - w) / (e - w) * W
        y = (n - lat) / (n - s) * H
        return x, y

    # сетка через каждые 0.01 градуса
    step = 0.01
    lon = w
    while lon <= e:
        x, _ = to_px(lon, n)
        draw.line([(x, 0), (x, H)], fill=(0, 255, 255), width=1)
        draw.text((x + 3, 5), f"{lon:.3f}E", fill=(0, 255, 255))
        lon += step
    lat = s
    while lat <= n:
        _, y = to_px(w, lat)
        draw.line([(0, y), (W, y)], fill=(0, 255, 255), width=1)
        draw.text((5, y + 3), f"{lat:.3f}N", fill=(0, 255, 255))
        lat += step

    # текущие рамки из конфига
    def box(bbox, color, label):
        x0, y0 = to_px(bbox[0], bbox[3])
        x1, y1 = to_px(bbox[2], bbox[1])
        draw.rectangle([x0, y0, x1, y1], outline=color, width=2)
        draw.text((x0 + 3, y0 + 3), label, fill=color)

    # Технологические площадки — малиновым.
    for name, meta in config.TRAINS.items():
        box(meta["bbox"], (255, 60, 220), name)

    # Причалы — ВСЕ, а не только СПГ. Цвет по типу груза, чтобы сразу
    # было видно, где ждём вывоз продукта, а где завоз оборудования.
    # Рейд не рисуем: он занимает пол-картинки и только мешает.
    berth_colors = {
        "lng":      (0, 255, 90),     # ярко-зелёный: отгрузка СПГ
        "liquid":   (0, 200, 255),    # голубой: жидкие продукты
        "sulphur":  (255, 255, 0),    # жёлтый: сера
        "drycargo": (255, 140, 0),    # оранжевый: завоз грузов
        "support":  (170, 170, 170),  # серый: вспомогательные
    }
    for name, meta in config.BERTHS.items():
        kind = meta.get("kind", "")
        if kind == "anchorage":
            continue
        box(meta["bbox"], berth_colors.get(kind, (200, 200, 200)),
            f"{name} [{kind}]")

    # Вспомогательные площадки — белым, чтобы не путались с причалами.
    for name, meta in config.OTHER_SITES.items():
        box(meta["bbox"], (255, 255, 255), name)

    img.save(out_path)
    return out_path
