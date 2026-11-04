"""
outreach.py — готовые письма и сообщения по сегодняшним данным.

Зачем отдельный модуль. Холодное письмо работает, когда в первой
строке стоит факт, которого у получателя нет. Такой факт бот уже
посчитал — осталось вставить его в текст вместо общих слов.
Поэтому письма здесь не шаблоны «вставьте название компании», а
текст, собранный из сегодняшнего отчёта: индекс, его уровень, дата
снимка, что именно изменилось.

Запуск:
    python bot.py outreach

Результат печатается в окно и складывается в data/outreach/ одним
текстовым файлом: открываете, копируете нужное, вставляете в почту
или в LinkedIn. Ничего никуда не отправляется автоматически —
отправку вы контролируете сами.

Язык писем английский: адресаты — EPC-подрядчики и их отделы
развития бизнеса.
"""

import datetime as dt
import os
import textwrap

import config

OUT_DIR = os.path.join("data", "outreach")

# Лимит длины у запроса на контакт в LinkedIn — 300 знаков вместе с
# пробелами. Если не следить, площадка обрежет текст на полуслове.
LINKEDIN_NOTE_LIMIT = 300


def _site_url():
    """Адрес опубликованной витрины, если он задан в настройках."""
    # Заглушка намеренно бросается в глаза и написана по-английски:
    # если она случайно уйдёт в письмо, это видно сразу, а не после
    # отправки. Команда outreach отдельно предупреждает о пустом
    # SITE_URL в окне.
    return getattr(config, "SITE_URL", "") or "[PASTE SITE_URL FROM config.py]"


def _signature():
    return (f"{config.BRAND_SIGNATURE}\n"
            f"{config.BRAND_NAME}\n"
            f"{config.OUTREACH_SENDER}\n"
            f"{config.BRAND_CALENDLY}")


def facts(sat=None, intel=None, heat=None, fin=None):
    """
    Вытаскивает из отчёта то, на что можно сослаться в первой строке.

    Ничего не выдумывает: если данных нет, соответствующий факт
    просто не появится, и письмо соберётся без него.
    """
    sat = sat or {}
    f = {}

    scene = sat.get("scene") or {}
    if scene.get("date"):
        f["scene_date"] = scene["date"]
        f["scene_age"] = scene.get("age_days")

    if heat and heat.get("index") is not None:
        f["index"] = heat["index"]
        f["level"] = heat["level"]["label"]
        f["level_what"] = heat["level"]["what"]
        # Самая сильная составляющая — то, из-за чего индекс высокий.
        scored = [(c["score"], c["label"]) for c in heat["components"].values()
                  if c["score"] is not None]
        if scored:
            f["top_component"] = max(scored)[1]
            f["top_score"] = max(scored)[0]

    if intel:
        if intel.get("hiring_total"):
            f["hiring"] = intel["hiring_total"]
        if intel.get("award_total"):
            f["awards"] = intel["award_total"]
        news = intel.get("news") or []
        if news:
            f["headline"] = news[0]["title"][:120]

    sites = [s for s in (sat.get("sites") or [])
             if s.get("construction_signal")]
    if sites:
        f["site_changed"] = sites[0]["site"]
        f["site_pct"] = sites[0].get("changed")

    trains = sat.get("trains") or []
    down = [t for t in trains if t.get("status") in ("REBUILDING", "DESTROYED",
                                                     "IDLE")]
    if trains:
        f["down_trains"] = len(down)
        f["total_trains"] = len(trains)

    return f


def _opening_line(f):
    """
    Первая строка письма. Выбирается та, которая сильнее: конкретное
    наблюдение бьёт абстрактный индекс.
    """
    if f.get("site_changed") and f.get("site_pct"):
        return (f"The {f['site_changed'].lower()} at Ras Laffan changed by "
                f"{f['site_pct']:.0%} on the satellite image of "
                f"{f.get('scene_date', 'this week')} — equipment is being "
                f"laid down.")
    if f.get("hiring"):
        return (f"We counted {f['hiring']} hiring signals around the Ras "
                f"Laffan restoration in the past week. Mobilisation starts "
                f"before the tender is announced.")
    if f.get("awards"):
        return (f"There were {f['awards']} award and tender mentions around "
                f"the Ras Laffan restoration in the past week.")
    if f.get("index") is not None:
        return (f"Our Ras Laffan activity index stands at {f['index']} of "
                f"100 — {f['level']} activity.")
    return ("Two liquefaction trains at Ras Laffan are out of service and "
            "the repair is scheduled over three years.")


def build(sat=None, ais=None, intel=None, fin=None, heat=None):
    """Собирает все заготовки. Возвращает словарь {название: текст}."""
    f = facts(sat, intel, heat, fin)
    # Переносим по 72 знака: иначе подставленная строка вытягивается
    # в одну длинную, и письмо выглядит собранным машиной.
    opening = textwrap.fill(_opening_line(f), 72)
    url = _site_url()
    sig = _signature()

    idx_line = ""
    if f.get("index") is not None:
        idx_line = textwrap.fill(
            f"Today it reads {f['index']}/100 ({f['level']}), driven mostly "
            f"by {f.get('top_component', 'site activity').lower()}.", 72)

    out = {}

    # --- 1. Холодное письмо подрядчику --------------------------------
    out["email_cold"] = f"""Subject: Ras Laffan — {f.get('index', 'activity')} /100 activity index, updated daily

Hi {{first_name}},

{opening}

I run a daily open-data monitor on the Ras Laffan restoration for
contractors chasing the work: satellite imagery of the site, vessel
movements, tender and hiring signals, and contractor filings, combined
into one activity index.

{idx_line}

It is built to answer one question — is this the moment for {{company}}
to engage, and which desk opens the door.

The live page is here, no sign-up: {url}

If it is useful, I can set the same monitor up for the assets {{company}}
is actually bidding on. Worth 20 minutes?

{sig}
"""

    # --- 2. Письмо тому, кто уже скачал отчёт -------------------------
    out["email_followup"] = f"""Subject: Re: Ras Laffan monitor — what changed this week

Hi {{first_name}},

Quick update on the asset you looked at.

{opening}

{idx_line}

The full page is live here: {url}

The index, the train status and the decision map all refresh
automatically each morning, within half an hour of the new satellite
image being published.

If {{company}} wants this running on a different asset — a refinery, a
terminal, an LNG plant anywhere in the world — set-up takes days. Happy
to show you the working version first.

{sig}
"""

    # --- 3. Запрос на контакт в LinkedIn ------------------------------
    note = (f"Hi {{first_name}} — I publish a daily open-data activity index "
            f"on the Ras Laffan restoration for contractors chasing the "
            f"work. Today it reads {f.get('index', 'high')}/100. Thought it "
            f"might be useful to {{company}}.")
    if len(note) > LINKEDIN_NOTE_LIMIT:
        note = note[:LINKEDIN_NOTE_LIMIT - 1].rsplit(" ", 1)[0] + "."
    out["linkedin_note"] = note

    # --- 4. Сообщение после принятия заявки ---------------------------
    out["linkedin_message"] = f"""Thanks for connecting, {{first_name}}.

{opening}

I track the Ras Laffan restoration daily from open sources — satellite
imagery, vessel movements, tender and hiring signals — and publish it as
one activity index.

{idx_line}

Live page, nothing to sign up for: {url}

If it is useful I can point the same monitor at whatever {{company}} is
bidding on next. Open to a short call?

{config.BRAND_SIGNATURE}
"""

    # --- 5. Короткий пост -------------------------------------------
    out["post"] = f"""{opening}

Two liquefaction trains at Ras Laffan are down — around 17% of Qatar's
export capacity — and QatarEnergy's CEO has put the repair at three
years. For a contractor that is three years of packages.

We track it daily from open data only: Sentinel-2 imagery, vessel
movements, press and hiring signals, contractor filings. One index,
updated every morning within half an hour of the new image.

{url}

#LNG #EPC #construction #OSINT
"""

    return out


def personalise(messages: dict, person: dict) -> dict:
    """
    Подставляет в заготовки имя и компанию конкретного человека.

    Берёт данные из того, что нашла Lusha. Если имени нет, письмо не
    собирается вовсе: «Hi ,» хуже, чем отсутствие письма.
    """
    first = (person.get("first_name")
             or (person.get("name") or "").split(" ")[0]).strip()
    if not first:
        return {}
    company = (person.get("company") or "").strip() or "your team"
    return {k: v.replace("{first_name}", first).replace("{company}", company)
            for k, v in messages.items()}


def for_contacts(messages: dict, people: list) -> str:
    """Готовые письма по конкретным людям, по одному блоку на каждого."""
    if not people:
        return ""
    parts = ["", "=" * 70,
             "ПИСЬМА ПО КОНКРЕТНЫМ ЛЮДЯМ (контакты из Lusha)",
             "=" * 70,
             "",
             "Имена и компании уже подставлены. Персональные данные:",
             "рассылать по списку эти письма нельзя, они для адресного",
             "обращения.",
             ""]
    for p in people:
        m = personalise(messages, p)
        if not m:
            continue
        mail = ", ".join(p.get("emails") or []) or "почта не раскрыта"
        parts += ["-" * 70,
                  f"{p.get('name', '')} — {p.get('title', '')}",
                  f"{p.get('company', '')} · {mail}",
                  "-" * 70, "",
                  m["email_cold"], ""]
    return "\n".join(parts)


def render(messages: dict) -> str:
    """Складывает всё в один текстовый файл, удобный для копирования."""
    titles = {
        "email_cold": "ХОЛОДНОЕ ПИСЬМО (первое касание)",
        "email_followup": "ПИСЬМО ПОСЛЕ СКАЧИВАНИЯ ОТЧЁТА",
        "linkedin_note": f"ЗАПРОС В LINKEDIN (лимит {LINKEDIN_NOTE_LIMIT} знаков)",
        "linkedin_message": "СООБЩЕНИЕ ПОСЛЕ ПРИНЯТИЯ ЗАЯВКИ",
        "post": "ПОСТ В ЛЕНТУ",
    }
    parts = [
        "=" * 70,
        f"ЗАГОТОВКИ ДЛЯ РАССЫЛКИ — {dt.date.today().isoformat()}",
        "=" * 70,
        "",
        "Подставьте вместо {first_name} имя адресата, вместо {company} —",
        "название его компании. Больше ничего менять не нужно: цифры и",
        "наблюдения уже взяты из сегодняшнего отчёта.",
        "",
        "Отправляется вручную. Бот ничего сам не рассылает.",
        "",
    ]
    for key, title in titles.items():
        if key not in messages:
            continue
        body = messages[key]
        parts += ["", "-" * 70, title, "-" * 70, ""]
        if key == "linkedin_note":
            parts.append(f"[длина: {len(body)} знаков]")
            parts.append("")
        parts.append(body)
    return "\n".join(parts)


def save(text: str) -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"outreach_{dt.date.today().isoformat()}.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _revealed_contacts():
    """
    Люди с раскрытыми контактами, если Lusha подключена.

    Модуль lusha может отсутствовать или быть не настроен — тогда
    просто работаем без адресных писем, а не падаем.
    """
    try:
        import lusha
        return [p for p in lusha.load_cache()["contacts"].values()
                if p.get("revealed")]
    except Exception:                                 # noqa: BLE001
        return []


def run(sat=None, ais=None, intel=None, fin=None, heat=None):
    messages = build(sat, ais, intel, fin, heat)
    text = render(messages)
    text += for_contacts(messages, _revealed_contacts())
    path = save(text)
    return text, path
