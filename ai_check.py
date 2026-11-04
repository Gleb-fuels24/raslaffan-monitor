"""
ai_check.py — вторая пара глаз: проверка снимков зрячей моделью.

ЗАЧЕМ. Код считает яркие пиксели. Он надёжен в том, что умеет, но
слеп ко всему остальному: не отличит баржу от газовоза, не заметит
кран, не поймёт, что площадка разрыта. Модель, которая видит
картинку, замечает именно это — но склонна домысливать и может
уверенно описать то, чего нет.

Поэтому они здесь НЕ заменяют друг друга. Код и модель отвечают на
один вопрос независимо, и в отчёт идут ОБА ответа вместе с тем,
сошлись они или нет. Расхождение — это не сбой, а самый полезный
результат: оно показывает место, куда надо посмотреть глазами.

Модель НИКОГДА не переписывает вывод кода. Она добавляет строку
«модель видит то-то» и, при расхождении, понижает уверенность.

ОЧЕРЕДЬ ПОСТАВЩИКОВ. Сначала Gemini, при отказе Grok, при отказе
Claude. Отказом считается всё, что помешало получить ответ: нет
ключа, сеть, лимит, ошибка сервиса или пустой ответ. Переход к
следующему молчаливый, в отчёте указано, кто в итоге ответил.

ДЕНЬГИ. Каждый вопрос — платный запрос к чужому API. Поэтому стоит
жёсткий потолок вызовов за запуск (AI_MAX_CALLS_PER_RUN), проверяются
только причалы и площадки, где есть что проверять, и в ежедневный
цикл это входит ровно один раз.

Ключи в .env:
    GEMINI_API_KEY      ключ Google AI Studio
    XAI_API_KEY         ключ xAI (Grok)
    ANTHROPIC_API_KEY   ключ Anthropic (Claude)
Достаточно любого одного. Если нет ни одного, проверка просто не
делается, и бот об этом пишет.
"""

import base64
import json
import os
import re

import requests

import config

TIMEOUT = 90


# ---------------------------------------------------------------------------
# Поставщики
# ---------------------------------------------------------------------------
def _gemini(img_b64, mime, prompt):
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("нет ключа GEMINI_API_KEY")
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{config.AI_MODEL_GEMINI}:generateContent")
    body = {"contents": [{"parts": [
        {"text": prompt},
        {"inline_data": {"mime_type": mime, "data": img_b64}},
    ]}]}
    r = requests.post(url, params={"key": key}, json=body, timeout=TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini {r.status_code}: {r.text[:160]}")
    cand = (r.json().get("candidates") or [{}])[0]
    parts = (cand.get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts)


def _grok(img_b64, mime, prompt):
    key = os.getenv("XAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("нет ключа XAI_API_KEY")
    body = {
        "model": config.AI_MODEL_GROK,
        "messages": [{"role": "user", "content": [
            {"type": "image_url",
             "image_url": {"url": f"data:{mime};base64,{img_b64}",
                           "detail": "high"}},
            {"type": "text", "text": prompt},
        ]}],
    }
    r = requests.post("https://api.x.ai/v1/chat/completions",
                      headers={"Authorization": f"Bearer {key}"},
                      json=body, timeout=TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"Grok {r.status_code}: {r.text[:160]}")
    ch = (r.json().get("choices") or [{}])[0]
    return (ch.get("message") or {}).get("content", "")


def _claude(img_b64, mime, prompt):
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise RuntimeError("нет ключа ANTHROPIC_API_KEY")
    body = {
        "model": config.AI_MODEL_CLAUDE,
        "max_tokens": 600,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64",
                                         "media_type": mime,
                                         "data": img_b64}},
            {"type": "text", "text": prompt},
        ]}],
    }
    r = requests.post("https://api.anthropic.com/v1/messages",
                      headers={"x-api-key": key,
                               "anthropic-version": "2023-06-01",
                               "content-type": "application/json"},
                      json=body, timeout=TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"Claude {r.status_code}: {r.text[:160]}")
    blocks = r.json().get("content") or []
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


PROVIDERS = {"gemini": _gemini, "grok": _grok, "claude": _claude}


# ---------------------------------------------------------------------------
# Один вопрос по одной картинке
# ---------------------------------------------------------------------------
def ask(image_path, prompt):
    """
    Задаёт вопрос по картинке, перебирая поставщиков по очереди.

    Возвращает {"provider", "text", "tried"} либо {"error", "tried"}.
    tried — что произошло с каждым поставщиком; нужно, чтобы было
    видно, почему дошло до третьего, а не гадать.
    """
    if not os.path.exists(image_path):
        return {"error": "картинки нет на диске", "tried": []}

    with open(image_path, "rb") as fh:
        raw = fh.read()
    img_b64 = base64.b64encode(raw).decode("ascii")
    mime = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"

    tried = []
    for name in config.AI_PROVIDER_ORDER:
        fn = PROVIDERS.get(name)
        if not fn:
            tried.append(f"{name}: неизвестный поставщик")
            continue
        try:
            text = fn(img_b64, mime, prompt)
        except Exception as exc:                      # noqa: BLE001
            tried.append(f"{name}: {str(exc)[:110]}")
            continue
        if not (text or "").strip():
            tried.append(f"{name}: пустой ответ")
            continue
        return {"provider": name, "text": text, "tried": tried}

    return {"error": "ни один поставщик не ответил", "tried": tried}


def _parse_json(text):
    """
    Достаёт JSON из ответа модели.

    Модели любят обрамлять ответ пояснениями и тройными кавычками,
    поэтому берём первый блок в фигурных скобках, а не весь текст.
    """
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Вопросы
# ---------------------------------------------------------------------------
BERTH_PROMPT = (
    "This is a Sentinel-2 satellite image of a single berth at the Ras "
    "Laffan LNG port in Qatar, about {m} metres per pixel. Look only at "
    "what is visible. Is a ship moored at the quay or jetty?\n\n"
    "If a ship is there, also look for clues about which way the cargo "
    "moves. Ras Laffan both receives project equipment and EXPORTS "
    "sulphur, a by-product of gas processing, so a bulk carrier at a dry "
    "cargo quay may be loading out, not delivering. Useful clues: a fixed "
    "ship loader or conveyor on the quay suggests bulk export; yellow "
    "stockpiles nearby suggest sulphur; heavy-lift derricks on the ship "
    "or large modules laid out on the quay suggest equipment arriving; "
    "an empty quay with a deeply loaded ship suggests the ship arrived "
    "loaded. Report only clues you can actually see.\n\n"
    "Answer with JSON only, no other text:\n"
    '{{"vessel": true or false, '
    '"type": "LNG carrier" or "tanker" or "bulk carrier" or "general cargo" '
    'or "heavy lift" or "tug" or "barge" or "unclear" or "none", '
    '"count": integer, '
    '"direction": "loading out" or "discharging in" or "unknown", '
    '"direction_clues": ["short items you actually see"], '
    '"confidence": "high" or "medium" or "low", '
    '"note": "one short sentence on what you actually see"}}\n\n'
    "If you cannot tell, say vessel false and confidence low. "
    "Direction is usually unknown from a single image — say so rather "
    "than guessing."
)

WORKS_PROMPT = (
    "This is a Sentinel-2 satellite image of part of an industrial site at "
    "Ras Laffan, Qatar, about {m} metres per pixel. Two liquefaction trains "
    "there were damaged in March 2026 and are being rebuilt. Look only at "
    "what is visible. Are there signs of construction or reconstruction "
    "work?\n\n"
    "Answer with JSON only, no other text:\n"
    '{{"works": true or false, '
    '"signs": ["short items you actually see, e.g. cranes, laydown of '
    'modules, excavation, new hardstanding, scaffolding, burn damage"], '
    '"confidence": "high" or "medium" or "low", '
    '"note": "one short sentence"}}\n\n'
    "An ordinary operating plant with no works is a normal answer. "
    "Do not invent activity."
)


def _picture(name, bbox, snap, px=640):
    """
    Цветная картинка зоны для показа модели.

    Отдельно от рабочего снимка: модели нужен обычный вид, а не
    инфракрасные каналы, по которым считает код.
    """
    import sentinel
    path = os.path.join(config.CHIPS_DIR, f"ai_{name}_{snap}.png")
    if os.path.exists(path):
        return path
    w, h = sentinel.chip_size(bbox, px)
    try:
        return sentinel.fetch_chip(bbox, snap, snap, path,
                                   evalscript=sentinel.EVALSCRIPT_TRUECOLOR,
                                   fmt="image/png", width=w, height=h)
    except Exception:                                 # noqa: BLE001
        return None


def _px_size(bbox, px=640):
    import sentinel
    w, _ = sentinel.chip_size(bbox, px)
    return round((bbox[2] - bbox[0]) * 100100.0 / max(w, 1), 1)


# ---------------------------------------------------------------------------
# Проверка причалов и площадок
# ---------------------------------------------------------------------------
def run(sat, budget=None):
    """
    Проверяет моделью то, что уже посчитал код, и сравнивает.

    Возвращает блок, который кладётся в sat["ai"] и показывается в
    индексе: по каждому объекту — что увидел код, что увидела модель
    и сошлись ли они.
    """
    if not getattr(config, "AI_CHECK_ENABLED", True):
        return {"enabled": False, "note": "проверка моделью выключена "
                                          "в config.py"}

    if not any(os.getenv(k, "").strip() for k in
               ("GEMINI_API_KEY", "XAI_API_KEY", "ANTHROPIC_API_KEY")):
        return {"enabled": False,
                "note": "нет ни одного ключа модели в .env — "
                        "проверка не делалась"}

    snap = (sat.get("scene") or {}).get("date")
    if not snap:
        return {"enabled": False, "note": "нет снимка для проверки"}

    budget = budget if budget is not None else config.AI_MAX_CALLS_PER_RUN
    checks, used, providers = [], 0, set()

    # 1. Причалы. Берём те, где код увидел судно, плюс непроверенные:
    #    и подтверждение, и опровержение одинаково полезны.
    berths = [b for b in (sat.get("berths") or [])
              if b.get("kind") in ("lng", "liquid", "drycargo")]
    berths.sort(key=lambda b: (b.get("occupied") is False,
                               b.get("occupied") is not None))

    for b in berths:
        if used >= budget:
            break
        meta = config.BERTHS.get(b["berth"])
        if not meta:
            continue
        pic = _picture(f"berth_{b['berth']}", meta["bbox"], snap)
        if not pic:
            continue
        res = ask(pic, BERTH_PROMPT.format(m=_px_size(meta["bbox"])))
        used += 1
        if res.get("error"):
            checks.append({"object": b["berth"], "kind": "berth",
                           "code": _code_berth(b), "ai": None,
                           "error": res["error"], "tried": res["tried"]})
            continue
        providers.add(res["provider"])
        data = _parse_json(res["text"]) or {}
        checks.append({
            "object": b["berth"], "kind": "berth",
            "code": _code_berth(b),
            "ai": (("vessel: " + (data.get("type") or "yes")
                    + (f", {data['direction']}"
                       if data.get("direction")
                       and data["direction"] != "unknown" else
                       ", direction unknown"))
                   if data.get("vessel") else "no vessel"),
            "ai_confidence": data.get("confidence"),
            "ai_note": (data.get("note") or "")[:160],
            "provider": res["provider"],
            "agree": _agree_berth(b.get("occupied"), data.get("vessel")),
            # Направление и подсказки к нему — то, чего код не знает в
            # принципе. Даже «unknown» от модели полезно: оно
            # подтверждает, что по снимку это и не определить.
            "ai_direction": data.get("direction"),
            "ai_direction_clues": (data.get("direction_clues") or [])[:3],
        })

    # 2. Площадки: идут ли работы. Контрольные пропускаем — на них
    #    работ быть не должно, и спрашивать про них незачем.
    sites = [s for s in (sat.get("sites") or []) if not s.get("control")]
    for s in sites:
        if used >= budget:
            break
        meta = config.OTHER_SITES.get(s["site"])
        if not meta:
            continue
        pic = _picture(f"site_{s['site'].replace(' ', '_')}",
                       meta["bbox"], snap)
        if not pic:
            continue
        res = ask(pic, WORKS_PROMPT.format(m=_px_size(meta["bbox"])))
        used += 1
        if res.get("error"):
            checks.append({"object": s["site"], "kind": "site",
                           "code": _code_site(s), "ai": None,
                           "error": res["error"], "tried": res["tried"]})
            continue
        providers.add(res["provider"])
        data = _parse_json(res["text"]) or {}
        signs = data.get("signs") or []
        checks.append({
            "object": s["site"], "kind": "site",
            "code": _code_site(s),
            "ai": ("works: " + ", ".join(str(x) for x in signs[:3])
                   if data.get("works") else "no works visible"),
            "ai_confidence": data.get("confidence"),
            "ai_note": (data.get("note") or "")[:160],
            "provider": res["provider"],
            "agree": _agree_site(s.get("construction_signal"),
                                 data.get("works")),
        })

    agreed = [c for c in checks if c.get("agree") is True]
    disputed = [c for c in checks if c.get("agree") is False]
    return {
        "enabled": True,
        "checks": checks,
        "calls": used,
        "providers": sorted(providers),
        "agreed": len(agreed),
        "disputed": len(disputed),
        "note": (f"проверено объектов: {len(checks)}, совпало "
                 f"{len(agreed)}, разошлось {len(disputed)}"),
    }


def _code_berth(b):
    occ = b.get("occupied")
    if occ is True:
        return f"vessel ({b.get('vessel_area_m2', 0)} m2 of bright deck)"
    if occ is False:
        return "no vessel"
    return "not checked (no baseline frame)"


def _code_site(s):
    chg = s.get("changed")
    if chg is None:
        return "no image"
    return (f"works ({chg:.0%} changed)" if s.get("construction_signal")
            else f"unchanged ({chg:.0%})")


def _agree_berth(code_occupied, ai_vessel):
    """None — если одна из сторон промолчала: это не согласие и не спор."""
    if code_occupied is None or ai_vessel is None:
        return None
    return bool(code_occupied) == bool(ai_vessel)


def _agree_site(code_works, ai_works):
    if code_works is None or ai_works is None:
        return None
    return bool(code_works) == bool(ai_works)
