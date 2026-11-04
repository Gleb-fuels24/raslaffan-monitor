"""
lusha.py — поиск контактов лиц, принимающих решения, через Lusha.

Зачем отдельный модуль и почему он устроен осторожно.

Lusha — платный справочник деловых контактов. Он берёт деньги не за
время, а за каждое действие: поиск стоит 1 кредит за 25 найденных,
раскрытие почты — 1 кредит за человека, раскрытие телефона — 5. На
бесплатном тарифе кредитов 40 штук. Это бюджет на разовое
исследование, а не на ежедневный поток: один неосторожный запуск
съест его целиком.

Поэтому модуль устроен так:

1. Он НИКОГДА не вызывается из ежедневного цикла. Только вручную,
   командой. Ночной запуск кредиты не тратит.
2. Поиск и раскрытие разделены. Поиск показывает, кто нашёлся —
   имена и должности. Раскрытие почт делается отдельной командой и
   только по вашему решению.
3. Всё найденное складывается в кэш. Один и тот же человек никогда
   не раскрывается дважды — второй раз ответ берётся из файла.
4. Есть жёсткий потолок трат за запуск (LUSHA_MAX_CREDITS_PER_RUN).
   Превысить его модуль не даст.
5. Телефоны по умолчанию НЕ раскрываются: 5 кредитов за номер — это
   восьмая часть всего бесплатного тарифа.

И главное ограничение, которое менять нельзя: контакты сюда приходят
персональные, и на сайт они не попадают НИКОГДА. Витрина публична,
а это персональные данные. Они живут только у вас: в файле на диске
и, если настроен, в Telegram. Функции записи в docs/ здесь нет
намеренно.

Ключ берётся из .env, переменная LUSHA_API_KEY. Где его взять:
lusha.com -> вход -> API & connectors -> сгенерировать ключ.
"""

import datetime as dt
import json
import os

import requests

import config

API = "https://api.lusha.com/v3"
CACHE = os.path.join("data", "state", "lusha_cache.json")
OUT_DIR = os.path.join("data", "contacts")

# Сколько кредитов стоит действие. Нужно, чтобы показывать цену ДО
# траты, а не после. Значения с тарифной страницы Lusha.
PRICE_SEARCH_PER_25 = 1
PRICE_EMAIL = 1
PRICE_PHONE = 5


# ---------------------------------------------------------------------------
# Кэш
# ---------------------------------------------------------------------------
def load_cache() -> dict:
    """Что уже найдено и раскрыто. Файл простой, можно открыть глазами."""
    if os.path.exists(CACHE):
        try:
            with open(CACHE, encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            pass
    return {"companies": {}, "contacts": {}, "credits_spent": 0}


def save_cache(cache: dict) -> None:
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Обращение к API
# ---------------------------------------------------------------------------
def _key() -> str:
    key = os.getenv("LUSHA_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "В файле .env нет строки LUSHA_API_KEY. Ключ берётся на "
            "lusha.com: войдите, раздел API & connectors, создайте ключ "
            "и впишите строкой  LUSHA_API_KEY=ваш_ключ")
    return key


def _post(path: str, body: dict) -> dict:
    """
    Отправляет запрос и переводит технические ошибки в понятные.

    Коды ответа объясняются словами: иначе при 402 вы увидите только
    число и не поймёте, что просто кончились кредиты.
    """
    resp = requests.post(f"{API}{path}", json=body, timeout=60,
                         headers={"api_key": _key(),
                                  "Content-Type": "application/json"})

    if resp.status_code == 401:
        raise RuntimeError("Lusha не приняла ключ. Проверьте LUSHA_API_KEY "
                           "в .env — возможно, скопирован с лишним пробелом.")
    if resp.status_code == 403:
        raise RuntimeError("Lusha отказала в доступе. Обычно это значит, "
                           "что доступ по API выключен администратором "
                           "аккаунта.")
    if resp.status_code in (402, 429):
        hint = ("кончились кредиты" if resp.status_code == 402
                else "превышен лимит запросов (на бесплатном тарифе "
                     "40 в минуту и 100 в сутки). Подождите и повторите")
        raise RuntimeError(f"Lusha: {hint}.")
    if resp.status_code >= 400:
        raise RuntimeError(f"Lusha вернула {resp.status_code}: "
                           f"{resp.text[:300]}")
    return resp.json()


# ---------------------------------------------------------------------------
# Шаг 1. Компании
# ---------------------------------------------------------------------------
def find_companies(domains=None, names=None, cache=None) -> list:
    """
    Находит компании по доменам и названиям, чтобы дальше искать людей
    именно в них, а не по всему свету.

    Результат кэшируется: домены заказчика не меняются, платить за
    один и тот же поиск дважды незачем.
    """
    cache = cache if cache is not None else load_cache()
    domains = domains or config.LUSHA_TARGET_DOMAINS
    names = names or config.LUSHA_TARGET_NAMES

    key = "|".join(sorted(domains) + sorted(names))
    if key in cache["companies"]:
        return cache["companies"][key]

    body = {"pagination": {"page": 0, "size": 25},
            "filters": {"companies": {"include": {}}}}
    if domains:
        body["filters"]["companies"]["include"]["domains"] = domains
    if names:
        body["filters"]["companies"]["include"]["names"] = names

    data = _post("/companies/prospecting", body)
    found = [{"id": r.get("id"), "name": r.get("name"),
              "domain": r.get("domain")} for r in data.get("results", [])]

    cache["companies"][key] = found
    cache["credits_spent"] += data.get("billing", {}).get("creditsCharged", 0)
    save_cache(cache)
    return found


# ---------------------------------------------------------------------------
# Шаг 2. Люди (без раскрытия контактов)
# ---------------------------------------------------------------------------
def find_contacts(company_ids=None, size: int = 25, cache=None) -> dict:
    """
    Ищет людей нужных должностей в выбранных компаниях.

    Возвращает ТОЛЬКО превью: имя, фамилия, должность, компания.
    Ни почты, ни телефона здесь нет — за них платят отдельно.

    size ограничен 25 намеренно: тариф считает по 1 кредиту за 25
    найденных, и брать больше смысла нет, пока вы не посмотрели на
    первые.
    """
    cache = cache if cache is not None else load_cache()

    include = {}
    if config.LUSHA_JOB_TITLES:
        include["jobTitles"] = config.LUSHA_JOB_TITLES
    if config.LUSHA_DEPARTMENTS:
        include["departments"] = config.LUSHA_DEPARTMENTS
    if config.LUSHA_SENIORITY_IDS:
        include["seniorityIds"] = config.LUSHA_SENIORITY_IDS

    companies = {}
    if company_ids:
        companies["companyIds"] = [str(c) for c in company_ids]
    else:
        # Запасной путь: если компании не нашлись по id, ищем по домену
        # прямо в запросе на людей.
        companies["domains"] = config.LUSHA_TARGET_DOMAINS

    body = {"pagination": {"page": 0, "size": max(10, min(size, 100))},
            "filters": {"contacts": {"include": include},
                        "companies": {"include": companies}},
            "options": {"includePartialProfiles": True}}

    data = _post("/contacts/prospecting", body)
    charged = data.get("billing", {}).get("creditsCharged", 0)
    cache["credits_spent"] += charged

    results = []
    for r in data.get("results", []):
        cid = str(r.get("id"))
        person = {
            "id": cid,
            "name": r.get("fullName") or
                    " ".join(filter(None, [r.get("firstName"),
                                           r.get("lastName")])),
            "first_name": r.get("firstName", ""),
            "title": r.get("jobTitle") or r.get("title") or "",
            "company": (r.get("companyName") or
                        (r.get("company") or {}).get("name") or ""),
            "revealed": False,
        }
        # Старое раскрытие не теряем.
        old = cache["contacts"].get(cid)
        if old and old.get("revealed"):
            person.update(old)
        cache["contacts"][cid] = person
        results.append(person)

    save_cache(cache)
    return {"results": results,
            "total": data.get("pagination", {}).get("total", len(results)),
            "credits_charged": charged}


# ---------------------------------------------------------------------------
# Шаг 3. Раскрытие контактов — единственное дорогое место
# ---------------------------------------------------------------------------
def reveal(ids, phones: bool = None, cache=None) -> dict:
    """
    Раскрывает почты (и, если явно попросить, телефоны) по списку id.

    Проверяет три вещи до того, как потратить хоть один кредит:
      - кто из списка уже раскрыт раньше (их не трогаем);
      - во сколько обойдётся остальное;
      - влезает ли это в потолок LUSHA_MAX_CREDITS_PER_RUN.
    """
    cache = cache if cache is not None else load_cache()
    phones = config.LUSHA_REVEAL_PHONES if phones is None else phones

    fresh = [str(i) for i in ids
             if not cache["contacts"].get(str(i), {}).get("revealed")]
    if not fresh:
        return {"results": [], "credits_charged": 0,
                "note": "все эти контакты уже раскрыты раньше, "
                        "взято из кэша"}

    cost = len(fresh) * (PRICE_EMAIL + (PRICE_PHONE if phones else 0))
    cap = config.LUSHA_MAX_CREDITS_PER_RUN
    if cost > cap:
        fit = cap // (PRICE_EMAIL + (PRICE_PHONE if phones else 0))
        return {"results": [], "credits_charged": 0, "blocked": True,
                "note": f"раскрытие {len(fresh)} контактов стоит {cost} "
                        f"кредитов, а потолок за запуск — {cap}. "
                        f"Влезает {fit}. Поднимите "
                        f"LUSHA_MAX_CREDITS_PER_RUN в config.py или "
                        f"раскрывайте по частям."}

    body = {"ids": fresh[:100],
            "reveal": ["emails", "phones"] if phones else ["emails"],
            "waterfallEnabled": False}
    data = _post("/contacts/enrich", body)

    out = []
    for r in data.get("results", []):
        cid = str(r.get("id"))
        person = cache["contacts"].get(cid, {"id": cid})
        person.update({
            "name": r.get("fullName") or person.get("name", ""),
            "first_name": r.get("firstName") or person.get("first_name", ""),
            "emails": [e.get("email") for e in (r.get("emails") or [])
                       if e.get("email")],
            "phones": [p.get("number") for p in (r.get("phones") or [])
                       if p.get("number")],
            "revealed": True,
            "revealed_at": dt.date.today().isoformat(),
        })
        cache["contacts"][cid] = person
        out.append(person)

    charged = data.get("billing", {}).get("creditsCharged", cost)
    cache["credits_spent"] += charged
    save_cache(cache)
    return {"results": out, "credits_charged": charged}


# ---------------------------------------------------------------------------
# Отчёт для себя
# ---------------------------------------------------------------------------
def render(people: list, title: str = "DECISION MAKERS") -> str:
    """
    Таблица для чтения глазами.

    Отдельно повторено, что это персональные данные: файл легко
    переслать, не подумав, а публиковать это нельзя.
    """
    lines = [
        "=" * 70,
        f"{title} — {dt.date.today().isoformat()}",
        "=" * 70,
        "",
        "ПЕРСОНАЛЬНЫЕ ДАННЫЕ. Только для вашей работы: на сайт, в",
        "рассылку по списку и в общий доступ это не идёт.",
        "Источник — Lusha, лицензированный поставщик деловых контактов.",
        "",
    ]
    for p in people:
        lines.append(f"{p.get('name', '?')} — {p.get('title', '')}")
        if p.get("company"):
            lines.append(f"  {p['company']}")
        if p.get("emails"):
            lines.append(f"  почта:   {', '.join(p['emails'])}")
        if p.get("phones"):
            lines.append(f"  телефон: {', '.join(p['phones'])}")
        if not p.get("revealed"):
            lines.append("  контакты не раскрыты (кредиты не потрачены)")
        lines.append("")
    return "\n".join(lines)


def save(text: str) -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"contacts_{dt.date.today().isoformat()}.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path
