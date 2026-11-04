"""
finance.py — финансовые показатели публичных подрядчиков.

ОТКУДА БЕРУТСЯ ДАННЫЕ. Комиссия по ценным бумагам США обязывает
компании, торгующиеся на американских биржах, сдавать отчётность в
машиночитаемом виде. Эти данные выложены бесплатно, без ключа и без
регистрации. Единственное требование — представляться в заголовке
запроса: иначе отказывают в обслуживании.

ЧЕСТНОЕ ОГРАНИЧЕНИЕ, КОТОРОЕ НАДО ПОНИМАТЬ СРАЗУ.
Так доступны только компании с листингом в США: KBR, Fluor, Jacobs,
Baker Hughes, Air Products. Technip Energies торгуется в Париже,
Saipem в Милане, Chiyoda и JGC в Токио — их отчётность в этот источник
НЕ попадает, и строить по ней анализ отсюда нельзя.

Для таких компаний модуль показывает только то, что известно из
новостей об их результатах, и прямо помечает это как сведения из
прессы, а не из отчётности. Выдавать одно за другое нельзя: заголовок
о «рекордном портфеле заказов» и цифра из отчёта — вещи разного веса.

И ещё: QatarEnergy — государственная компания, полную отчётность она
не публикует вовсе. По владельцу актива финансового анализа не будет
ни отсюда, ни откуда-либо ещё.
"""

import datetime as dt
import json

import requests

import config

# Комиссия требует указывать, кто обращается, и контакт для связи.
SEC_UA = {"User-Agent": f"Fuels24 Asset Monitor {config.BRAND_CONTACT_EMAIL}",
          "Accept-Encoding": "gzip, deflate"}

FACTS = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{tag}.json"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"


def _get(url):
    try:
        r = requests.get(url, headers=SEC_UA, timeout=60)
        if r.status_code != 200:
            return None
        return r.json()
    except (requests.RequestException, json.JSONDecodeError, ValueError):
        return None


import re as _re

# Календарные рамки SEC: CY2025 — год, CY2025Q3 — квартал,
# CY2025Q3I — мгновенное значение на конец квартала (для остатков
# вроде портфеля заказов).
_FRAME_YEAR = _re.compile(r"^CY(\d{4})$")
_FRAME_QTR = _re.compile(r"^CY(\d{4})Q(\d)I?$")


def concept(cik, tag, unit="USD", period="quarter", limit=8):
    """
    Выдёргивает один показатель из отчётности.

    tag — название статьи по стандарту отчётности США. Например,
    Revenues — выручка, а RevenueFromContractWithCustomerExcludingAssessedTax
    то же самое по более новой редакции стандарта. Компании используют
    разные варианты, поэтому выше по коду мы перебираем несколько.

    ГЛАВНАЯ ТОНКОСТЬ, из-за которой раньше получалась чепуха. В
    отчётности лежат вперемешку значения за разные по длине периоды:
    за квартал, за полугодие, за девять месяцев и за год. Плюс
    пересчёты прошлых лет, поданные вместе со свежим отчётом. Если
    просто взять два последних значения и поделить, можно сравнить
    квартал с годом и получить «рост выручки на 103%», которого не
    было. Ровно это и показывал сайт: у KBR +103%, у Baker Hughes
    +60%, а у Fluor вообще значение за 2018 год.

    Лечение: берём только те значения, у которых SEC проставил
    календарную рамку frame. Такие значения уже приведены к единому
    календарю и очищены от дублей, и по рамке видно, за какой период
    величина — за год или за квартал. Период выбирается один и тот
    же для всего ряда.

    period — "quarter" или "year".
    """
    data = _get(FACTS.format(cik=str(cik).zfill(10), tag=tag))
    if not data:
        return []

    out = []
    for r in data.get("units", {}).get(unit, []):
        frame = r.get("frame")
        if not frame or r.get("val") is None or not r.get("end"):
            continue
        is_year = bool(_FRAME_YEAR.match(frame))
        is_qtr = bool(_FRAME_QTR.match(frame))
        if period == "year" and not is_year:
            continue
        if period == "quarter" and not is_qtr:
            continue
        out.append({"end": r["end"], "val": r["val"], "frame": frame,
                    "form": r.get("form"), "fy": r.get("fy"),
                    "fp": r.get("fp")})

    out.sort(key=lambda r: r["end"])
    return out[-limit:]


def _year_over_year(rows):
    """
    Изменение к тому же периоду прошлого года, а не к предыдущему.

    У подрядчиков выручка сезонная: сравнение с прошлым кварталом
    показывало бы сезон, а не динамику бизнеса. Поэтому ищем
    значение ровно годичной давности по календарной рамке SEC:
    CY2026Q2 сравнивается с CY2025Q2.
    """
    if len(rows) < 2:
        return None, None
    last = rows[-1]
    m = _FRAME_QTR.match(last["frame"]) or _FRAME_YEAR.match(last["frame"])
    if not m:
        return None, None

    if _FRAME_QTR.match(last["frame"]):
        year, q = int(m.group(1)), m.group(2)
        want = [f"CY{year - 1}Q{q}", f"CY{year - 1}Q{q}I"]
    else:
        want = [f"CY{int(m.group(1)) - 1}"]

    prev = next((r for r in rows if r["frame"] in want), None)
    if not prev or not prev["val"]:
        return None, None
    return (round(100.0 * (last["val"] - prev["val"]) / abs(prev["val"]), 1),
            prev)


REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
]
BACKLOG_TAGS = [
    "RevenueRemainingPerformanceObligation",
]


def company(name, cik):
    """
    Собирает картину по одной компании.

    Портфель заказов (backlog) для подрядчика важнее выручки: он
    показывает, сколько работы законтрактовано вперёд. По стандарту
    отчётности он проходит как «невыполненные обязательства по
    договорам» — не все компании его раскрывают.
    """
    res = {"name": name, "cik": cik, "revenue": [], "backlog": [],
           "latest_filing": None, "error": None}

    sub = _get(SUBMISSIONS.format(cik=str(cik).zfill(10)))
    if sub:
        recent = sub.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        for f, d in zip(forms, dates):
            if f in ("10-K", "10-Q", "8-K"):
                res["latest_filing"] = {"form": f, "date": d}
                break
    else:
        res["error"] = "filings unavailable"
        return res

    for tag in REVENUE_TAGS:
        rows = concept(cik, tag)
        if rows:
            res["revenue"] = rows
            res["revenue_tag"] = tag
            break

    for tag in BACKLOG_TAGS:
        rows = concept(cik, tag)
        if rows:
            res["backlog"] = rows
            break

    # Динамика считается к тому же периоду прошлого года. Если пары
    # не нашлось, поле просто не появляется: лучше пусто, чем число,
    # полученное сравнением квартала с годом.
    chg, prev = _year_over_year(res["revenue"])
    if chg is not None:
        res["revenue_change_pct"] = chg
        res["revenue_compared_to"] = prev["frame"]
    chg, prev = _year_over_year(res["backlog"])
    if chg is not None:
        res["backlog_change_pct"] = chg
        res["backlog_compared_to"] = prev["frame"]

    return res


def run():
    """
    Разбирает всех подрядчиков из настроек.

    Те, у кого нет листинга в США, помечаются отдельно: по ним
    отчётности здесь не будет, и делать вид, что она есть, нельзя.
    """
    out = {"us_listed": [], "not_us_listed": [], "note": (
        "Filings are available only for companies listed in the US. "
        "Technip Energies, Saipem, Chiyoda, JGC and others are not "
        "covered here. QatarEnergy is state-owned and publishes no "
        "full accounts.")}

    for name, cik in config.EPC_SEC_CIK.items():
        print(f"  отчётность: {name} ...")
        c = company(name, cik)
        if c.get("error"):
            print(f"    [!] {c['error']}")
        else:
            rev = c["revenue"][-1]["val"] if c["revenue"] else None
            print(f"    выручка за период: "
                  f"{(rev / 1e9):.2f} млрд USD" if rev else "    нет данных")
        out["us_listed"].append(c)

    out["not_us_listed"] = list(config.EPC_NON_US)
    out["collected"] = dt.datetime.now(dt.timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC")
    return out


def summarize(fin):
    """Короткая сводка для отчёта и сайта."""
    lines = []
    for c in fin.get("us_listed", []):
        if c.get("error") or not c.get("revenue"):
            continue
        last = c["revenue"][-1]
        s = (f"{c['name']}: revenue USD {last['val'] / 1e9:.2f}bn "
             f"for {last.get('frame', last['end'])}")
        if c.get("revenue_change_pct") is not None:
            s += (f" ({c['revenue_change_pct']:+.1f}% vs "
                  f"{c.get('revenue_compared_to', 'a year earlier')})")
        if c.get("backlog"):
            b = c["backlog"][-1]
            s += f"; backlog USD {b['val'] / 1e9:.1f}bn"
            if c.get("backlog_change_pct") is not None:
                s += f" ({c['backlog_change_pct']:+.1f}%)"
        lines.append(s)
    return lines
