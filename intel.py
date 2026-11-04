"""
intel.py — ежедневная разведка по открытым источникам.

Собирает то, что говорят об объекте и о подрядчиках: новости, ролики
на YouTube, обсуждения, признаки набора персонала.

ЗАЧЕМ ЭТО ПОДРЯДЧИКУ. Снимок показывает, что происходит на площадке
сегодня. Новости и наём показывают, что СОБИРАЮТСЯ делать завтра.
Объявление о тендере появляется в прессе за недели до того, как на
площадке что-то меняется, а массовый набор сварщиков и монтажников —
самый ранний признак того, что подрядчик выиграл работу и мобилизуется.

ВСЕ ИСТОЧНИКИ БЕСПЛАТНЫЕ И БЕЗ КЛЮЧЕЙ:
  GDELT    — всемирный мониторинг новостей с оценкой тональности
  YouTube  — ленты каналов в формате RSS, ключ не нужен
  Reddit   — публичный поиск в формате JSON

ЧЕГО ЗДЕСЬ НЕТ И ПОЧЕМУ.
Glassdoor запрещает автоматический сбор своими правилами и активно
блокирует его. Вместо отзывов сотрудников мы смотрим на признаки
набора персонала — для разведки по подрядчикам это полезнее: отзыв
говорит о прошлом, вакансия о будущем.
LinkedIn и X закрыли бесплатный доступ, их здесь нет.
"""

import datetime as dt
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET

import requests

import config

UA = {"User-Agent": "Fuels24-AssetMonitor/1.0 (+https://fuels24.com)"}

GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"


# ---------------------------------------------------------------------------
# Новости через GDELT
# ---------------------------------------------------------------------------
# Что пошло не так при последнем обращении к GDELT.
LAST_NEWS_ERROR = []


def latest_news(maxrecords=60):
    """
    Самые свежие новости об объекте, какие вообще есть.

    Ищем по расширяющемуся окну: сутки, неделя, месяц, три месяца,
    год. Как только что-то нашлось — останавливаемся. Пустой раздел
    на витрине хуже старой новости: посетитель видит пустоту и
    делает вывод, что продукт ничего не знает.

    Второй уровень отступления — сам запрос. Основной собран из
    нескольких условий, и GDELT на сложном синтаксисе иногда
    отвечает ошибкой. Тогда пробуем простое «Ras Laffan»: лучше
    грубый поиск, чем никакого.

    Возвращает (статьи, за_какой_период, каким_запросом).
    """
    LAST_NEWS_ERROR.clear()
    queries = [config.INTEL_ASSET_QUERY, '"Ras Laffan"', "Ras Laffan"]
    windows = ["1d", "7d", "1m", "3m", "12m"]

    for qi, q in enumerate(queries):
        for w in windows:
            got = gdelt_articles(q, w, maxrecords)
            if got:
                return got, w, ("основной" if qi == 0 else "упрощённый")
    return [], None, None


def gdelt_articles(query, timespan="1d", maxrecords=50):
    """
    Статьи по запросу за период.

    GDELT следит за новостями всего мира на сотне языков и отдаёт их
    бесплатно без регистрации. Для каждой статьи известна тональность:
    отрицательные значения — плохие новости, положительные — хорошие.
    """
    params = {"query": query, "mode": "artlist", "format": "json",
              "timespan": timespan, "maxrecords": maxrecords,
              "sort": "datedesc"}
    try:
        r = requests.get(GDELT, params=params, headers=UA, timeout=60)
        if r.status_code != 200:
            LAST_NEWS_ERROR.append(f"GDELT returned {r.status_code}")
            return []
        data = r.json()
    except json.JSONDecodeError:
        # GDELT на кривой запрос отвечает не JSON, а текстом с
        # объяснением. Раньше это молча превращалось в «новостей нет».
        LAST_NEWS_ERROR.append(
            f"GDELT rejected the query: {r.text[:120].strip()}")
        return []
    except (requests.RequestException, ValueError) as exc:
        LAST_NEWS_ERROR.append(f"GDELT unavailable: {str(exc)[:100]}")
        return []

    out = []
    for a in data.get("articles", []):
        out.append({
            "title": (a.get("title") or "").strip(),
            "url": a.get("url", ""),
            "domain": a.get("domain", ""),
            "date": (a.get("seendate") or "")[:8],
            "language": a.get("language", ""),
        })
    return out


def gdelt_tone(query, timespan="7d"):
    """
    Средняя тональность освещения за период и её динамика.

    Полезно не само значение, а изменение: резкий уход в минус обычно
    означает аварию, забастовку, срыв сроков или санкции.
    """
    params = {"query": query, "mode": "timelinetone", "format": "json",
              "timespan": timespan}
    try:
        r = requests.get(GDELT, params=params, headers=UA, timeout=60)
        if r.status_code != 200:
            return None
        series = r.json().get("timeline", [])
    except (requests.RequestException, json.JSONDecodeError, ValueError):
        return None

    if not series:
        return None
    points = series[0].get("data", [])
    vals = [p.get("value") for p in points if p.get("value") is not None]
    if not vals:
        return None
    return {
        "avg": round(sum(vals) / len(vals), 2),
        "last": round(vals[-1], 2),
        "trend": round(vals[-1] - vals[0], 2),
        "points": [(p.get("date", "")[:8], round(p.get("value", 0), 2))
                   for p in points][-14:],
    }


def gdelt_volume(query, timespan="7d"):
    """Сколько статей выходило по дням — мера внимания прессы."""
    params = {"query": query, "mode": "timelinevolraw", "format": "json",
              "timespan": timespan}
    try:
        r = requests.get(GDELT, params=params, headers=UA, timeout=60)
        if r.status_code != 200:
            return []
        series = r.json().get("timeline", [])
    except (requests.RequestException, json.JSONDecodeError, ValueError):
        return []
    if not series:
        return []
    return [(p.get("date", "")[:8], p.get("value", 0))
            for p in series[0].get("data", [])][-14:]


# ---------------------------------------------------------------------------
# YouTube — ленты каналов
# ---------------------------------------------------------------------------
def youtube_channel(channel_id, limit=5):
    """
    Свежие ролики канала.

    YouTube отдаёт ленту канала в формате RSS без всякого ключа — это
    официальная возможность, а не обход. Нужен идентификатор канала,
    он начинается на UC и виден в адресе страницы канала.
    """
    url = ("https://www.youtube.com/feeds/videos.xml?channel_id="
           + urllib.parse.quote(channel_id))
    try:
        r = requests.get(url, headers=UA, timeout=45)
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.content)
    except (requests.RequestException, ET.ParseError):
        return []

    ns = {"a": "http://www.w3.org/2005/Atom",
          "m": "http://search.yahoo.com/mrss/"}
    out = []
    for e in root.findall("a:entry", ns)[:limit]:
        title = e.findtext("a:title", default="", namespaces=ns)
        link_el = e.find("a:link", ns)
        published = e.findtext("a:published", default="", namespaces=ns)
        author = e.findtext("a:author/a:name", default="", namespaces=ns)
        out.append({
            "title": title.strip(),
            "url": link_el.get("href") if link_el is not None else "",
            "date": published[:10],
            "channel": author.strip(),
        })
    return out


def youtube_search_rss(query, limit=5):
    """
    Ролики по теме без ключа.

    У YouTube нет бесплатного поиска в RSS, поэтому ищем ролики через
    GDELT: он индексирует в том числе видео, попавшие в новости.
    Это не полная замена поиску, но даёт самые заметные ролики.
    """
    arts = gdelt_articles(f'{query} domain:youtube.com', "7d", limit * 3)
    out = []
    for a in arts:
        if "youtube.com" in a.get("url", "") and a.get("title"):
            out.append({"title": a["title"], "url": a["url"],
                        "date": a.get("date", ""), "channel": "YouTube"})
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Reddit — обсуждения
# ---------------------------------------------------------------------------
def reddit_search(query, limit=10, subreddits=None):
    """
    Свежие обсуждения.

    Reddit отдаёт результаты поиска в формате JSON публично. Обязательно
    представляться в заголовке запроса, иначе он отвечает отказом.
    """
    q = query
    if subreddits:
        q += " (" + " OR ".join(f"subreddit:{s}" for s in subreddits) + ")"
    url = "https://www.reddit.com/search.json"
    try:
        r = requests.get(url, params={"q": q, "sort": "new", "limit": limit,
                                      "t": "week"},
                         headers=UA, timeout=45)
        if r.status_code != 200:
            return []
        data = r.json()
    except (requests.RequestException, json.JSONDecodeError, ValueError):
        return []

    out = []
    for c in data.get("data", {}).get("children", []):
        d = c.get("data", {})
        out.append({
            "title": (d.get("title") or "").strip(),
            "url": "https://www.reddit.com" + d.get("permalink", ""),
            "subreddit": d.get("subreddit", ""),
            "score": d.get("score", 0),
            "comments": d.get("num_comments", 0),
            "date": dt.datetime.utcfromtimestamp(
                d.get("created_utc", 0)).strftime("%Y-%m-%d")
            if d.get("created_utc") else "",
        })
    return out


# ---------------------------------------------------------------------------
# Признаки мобилизации подрядчика
# ---------------------------------------------------------------------------
HIRING_WORDS = re.compile(
    r"\b(hiring|recruit|recruitment|vacanc|job fair|mobilis|mobiliz|"
    r"manpower|workforce|staffing|welders|fitters|повыш|наб(ор|ира))",
    re.I)

AWARD_WORDS = re.compile(
    r"\b(award|contract|tender|bid|EPC|FEED|letter of intent|LOI|"
    r"notice to proceed|NTP|тендер|контракт|подряд)", re.I)


def hiring_signal(entity, timespan="7d"):
    """
    Ищет признаки набора персонала и выданных подрядов.

    Это самый ранний сигнал для подрядчика. Массовый набор монтажников
    и сварщиков в регионе означает, что кто-то выиграл работу и
    разворачивает площадку — за месяцы до того, как это станет видно
    на снимке.
    """
    arts = gdelt_articles(f'"{entity}"', timespan, 100)
    hiring = [a for a in arts if HIRING_WORDS.search(a["title"])]
    awards = [a for a in arts if AWARD_WORDS.search(a["title"])]
    return {
        "articles_total": len(arts),
        "hiring_mentions": len(hiring),
        "award_mentions": len(awards),
        "hiring_examples": hiring[:5],
        "award_examples": awards[:5],
    }


# ---------------------------------------------------------------------------
# Общий сбор
# ---------------------------------------------------------------------------
def run(days=1):
    """
    Полный ежедневный сбор по объекту и подрядчикам.

    Возвращает словарь, который дальше идёт и в индекс, и на сайт.
    """
    span = f"{max(1, days)}d"
    asset = config.INTEL_ASSET_QUERY

    print("  новости по объекту ...")
    # Расширяем окно, пока новости не найдутся.
    #
    # Раньше спрашивали строго за сутки, и в тихий день раздел
    # показывал «новостей не найдено» — как будто про объект вообще
    # ничего нет. Для подрядчика это хуже, чем старая новость: он
    # видит пустоту и уходит. Теперь если за сутки пусто, смотрим
    # неделю, потом месяц, а дату каждой публикации показываем явно,
    # чтобы свежее не путалось со старым.
    news, news_span, which = latest_news(60)
    if news:
        if news_span != span:
            print(f"    за {span} пусто, взял за {news_span} "
                  f"({which} запрос)")
    else:
        print("    новостей не нашлось ни за год: "
              + ("; ".join(LAST_NEWS_ERROR[:2]) or "GDELT ответил пусто"))
    tone = gdelt_tone(asset, "14d")
    volume = gdelt_volume(asset, "14d")

    print("  ролики на YouTube ...")
    videos = []
    for ch in config.INTEL_YOUTUBE_CHANNELS:
        videos += youtube_channel(ch, 3)
    videos += youtube_search_rss(config.INTEL_VIDEO_QUERY, 5)
    # убираем повторы по адресу
    seen, uniq = set(), []
    for v in videos:
        if v["url"] and v["url"] not in seen:
            seen.add(v["url"])
            uniq.append(v)
    videos = sorted(uniq, key=lambda v: v.get("date", ""), reverse=True)[:8]

    print("  обсуждения ...")
    discussions = reddit_search(config.INTEL_DISCUSSION_QUERY, 10,
                                config.INTEL_SUBREDDITS)

    print("  подрядчики ...")
    contractors = {}
    for name in config.EPC_CONTRACTORS:
        contractors[name] = hiring_signal(name, "7d")

    hiring_total = sum(c["hiring_mentions"] for c in contractors.values())
    award_total = sum(c["award_mentions"] for c in contractors.values())

    return {
        "collected": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "news": news[:25],
        "news_count": len(news),
        # За какой период нашлись новости. Нужно, чтобы на витрине
        # честно подписать: «за сутки ничего, показаны последние».
        "news_span": news_span,
        "news_span_requested": span,
        "news_query": which,
        "news_error": "; ".join(LAST_NEWS_ERROR[:2]),
        "tone": tone,
        "volume": volume,
        "videos": videos,
        "discussions": discussions,
        "contractors": contractors,
        "hiring_total": hiring_total,
        "award_total": award_total,
    }
