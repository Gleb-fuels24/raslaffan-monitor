"""
heat.py — индекс активности объекта для подрядчика.

ЧТО ЭТОТ ИНДЕКС ИЗМЕРЯЕТ. Не «хорошо или плохо идут дела у владельца»,
а другое: насколько близко к объекту работа, которую можно получить.
Для подрядчика это разные вопросы. Завод, работающий на полную
мощность, подрядчику неинтересен: там всё построено. Интересен завод,
где половина мощности стоит, идёт завоз оборудования и в прессе
мелькают слова «тендер» и «подряд».

Поэтому индекс намеренно растёт от ПЛОХИХ для владельца новостей.
Это не ошибка знака, это суть продукта.

ИЗ ЧЕГО СКЛАДЫВАЕТСЯ:

  Стройка        40%  завоз грузов, проектные грузы, земснаряды,
                      изменение облика площадок складирования
  Простой        25%  доля мощности, которая не работает — это
                      объём предстоящего восстановления
  Пресса         20%  упоминания тендеров и подрядов, тональность
  Мобилизация    15%  признаки массового набора персонала

ПОЧЕМУ ВСЕ ВЕСА ВИДНЫ И НАСТРАИВАЮТСЯ. Единая цифра удобна, но
опасна: её легко принять за объективную величину. На деле это
взвешенная сумма, где веса выбрал человек. Поэтому каждая
составляющая показывается отдельно, веса лежат в настройках, а в
отчёте всегда написано, из чего цифра собралась.

Если какой-то составляющей нет (не собрались новости, не работает
AIS), она не обнуляется, а ИСКЛЮЧАЕТСЯ, и веса оставшихся
пересчитываются. Иначе сбой одного источника ронял бы индекс и
выглядел бы как затишье на объекте.
"""

import config


def _clip(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


def _scale(value, full):
    """
    Переводит величину в баллы от 0 до 100.

    full — значение, которое считается «полностью горячо». Например,
    восемь заходов сухогрузов в неделю дают 100 баллов по стройке.
    Эти опорные величины — договорённость, а не измерение, и лежат
    в настройках, чтобы их можно было поправить по опыту.
    """
    if value is None or not full:
        return None
    return _clip(100.0 * float(value) / float(full))


def construction_score(ais, sat):
    """Стройка: завоз грузов и изменение площадок."""
    parts = []

    if ais:
        s = ais.get("signals", {})
        parts.append(_scale(s.get("drycargo_calls"),
                            config.HEAT_FULL_DRYCARGO))
        parts.append(_scale(s.get("project_cargo_calls"),
                            config.HEAT_FULL_PROJECT_CARGO))
        parts.append(_scale(s.get("dredgers"), config.HEAT_FULL_DREDGERS))

    # Площадки: какая доля из них показывает признаки работ.
    #
    # Контрольные площадки (резервуарные парки) из расчёта исключены.
    # Они нужны, чтобы ловить ошибки алгоритма: резервуар не
    # застраивается, и если на нём «обнаружились работы», значит
    # сбоит обнаружение. Но в знаменателе они занижали бы долю: с
    # двумя контролями из пяти площадок составляющая не могла бы
    # подняться выше 60 баллов, даже если работы идут везде.
    sites = [x for x in (sat.get("sites") or []) if not x.get("control")]
    if sites:
        flagged = sum(1 for x in sites if x.get("construction_signal"))
        parts.append(_scale(flagged, max(1, len(sites))))

    vals = [p for p in parts if p is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def idle_capacity_score(sat, fused):
    """
    Простой мощности: сколько предстоит восстановить.

    Чем больше линий стоит, тем больше работы впереди. Для владельца
    это убыток, для подрядчика — рынок.
    """
    cap = (fused or {}).get("capacity") or {}
    util = cap.get("utilisation_pct")
    if util is None:
        return None
    return round(_clip(100.0 - util), 1)


def press_score(intel):
    """Пресса: упоминания тендеров и подрядов плюс тональность."""
    if not intel:
        return None
    parts = []

    parts.append(_scale(intel.get("award_total"), config.HEAT_FULL_AWARDS))
    parts.append(_scale(intel.get("news_count"), config.HEAT_FULL_NEWS))

    # Тональность: отрицательная поднимает индекс. Для подрядчика
    # плохие новости на объекте означают предстоящую работу.
    tone = intel.get("tone")
    if tone and tone.get("avg") is not None:
        # Шкала GDELT примерно от -10 до +10. Переводим так, чтобы
        # -5 и ниже давали 100 баллов, +5 и выше — ноль.
        t = _clip((5.0 - tone["avg"]) * 10.0)
        parts.append(round(t, 1))

    vals = [p for p in parts if p is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def mobilisation_score(intel):
    """Мобилизация: признаки массового набора у подрядчиков."""
    if not intel:
        return None
    return _scale(intel.get("hiring_total"), config.HEAT_FULL_HIRING)


def compute(sat=None, ais=None, intel=None, fused=None, areas=None, ai=None):
    """
    Считает индекс и возвращает его вместе со всеми составляющими.

    Ничего не выдумывает: если данных по составляющей нет, она
    помечается отсутствующей и в расчёт не идёт.
    """
    sat = sat or {}
    comps = {
        "construction": {
            "label": "Site works",
            "weight": config.HEAT_WEIGHTS["construction"],
            "score": construction_score(ais, sat),
            "why": "inbound cargo, project cargo, dredgers, "
                   "changes in the laydown yards",
        },
        "idle": {
            "label": "Idle capacity",
            "weight": config.HEAT_WEIGHTS["idle"],
            "score": idle_capacity_score(sat, fused),
            "why": "share of capacity out of service — the volume of "
                   "restoration work ahead",
        },
        "press": {
            "label": "Press",
            "weight": config.HEAT_WEIGHTS["press"],
            "score": press_score(intel),
            "why": "tender and award mentions, tone of coverage",
        },
        "mobilisation": {
            "label": "Mobilisation",
            "weight": config.HEAT_WEIGHTS["mobilisation"],
            "score": mobilisation_score(intel),
            "why": "signs of large-scale hiring at the contractors",
        },
    }

    present = {k: v for k, v in comps.items() if v["score"] is not None}
    missing = [v["label"] for k, v in comps.items() if v["score"] is None]

    if not present:
        # ai отдаём и здесь. Иначе при непосчитанном индексе результаты
        # сверки терялись, и раздел проверки пропадал со страницы
        # целиком — хотя проверка прошла. Исчезнувшая проверка
        # выглядит как пройденная, а это худший вид молчания.
        return {"ai": ai, "index": None, "components": comps,
                "missing": missing,
                "note": "no component available — the index is not computed"}

    # Веса пересчитываются по тем составляющим, которые есть.
    total_w = sum(v["weight"] for v in present.values())
    index = sum(v["score"] * v["weight"] for v in present.values()) / total_w

    # Блок сверки с моделью кладётся в индекс как есть. На саму
    # цифру он НЕ влияет: модель — независимая проверка, а не ещё
    # один источник, который можно подмешать в сумму. Но если код и
    # модель расходятся, читатель должен увидеть это рядом с цифрой,
    # а не искать в другом разделе.
    return {
        "ai": ai,
        "index": round(index, 1),
        "level": level_of(index),
        "components": comps,
        "missing": missing,
        "coverage_pct": round(100.0 * total_w / sum(
            v["weight"] for v in comps.values()), 0),
        "note": ("computed from the available components; missing ones "
                 "are excluded and the remaining weights re-normalised"
                 if missing else "all components available"),
    }


def level_of(index):
    """Словесная оценка, чтобы цифру не приходилось толковать."""
    if index >= 70:
        return {"label": "high", "status": "critical",
                "what": "work is under way or about to start — time to engage"}
    if index >= 45:
        return {"label": "elevated", "status": "serious",
                "what": "signs of work being prepared — worth watching"}
    if index >= 25:
        return {"label": "moderate", "status": "warning",
                "what": "background activity, nothing moving sharply"}
    return {"label": "low", "status": "good",
            "what": "the asset is steady, no new work in sight"}


def explain(h):
    """Строки для отчёта: как сложилась цифра."""
    if h.get("index") is None:
        return [h.get("note", "index not computed")]
    out = [f"Activity index: {h['index']} of 100 — "
           f"{h['level']['label']}. {h['level']['what']}"]
    for key, c in h["components"].items():
        if c["score"] is None:
            out.append(f"  {c['label']}: no data (excluded from the index)")
        else:
            out.append(f"  {c['label']}: {c['score']} "
                       f"(weight {int(c['weight'] * 100)}%) — {c['why']}")
    if h.get("missing"):
        out.append(f"  Data coverage: {h.get('coverage_pct')}% of the full set")

    ai = h.get("ai") or {}
    if ai.get("enabled"):
        out.append(f"  AI cross-check: {ai.get('agreed', 0)} agree, "
                   f"{ai.get('disputed', 0)} disagree "
                   f"({', '.join(ai.get('providers') or []) or 'no provider'})")
        for c in ai.get("checks", []):
            if c.get("agree") is False:
                out.append(f"    DISAGREE {c['object']}: "
                           f"code says {c.get('code')}, "
                           f"AI says {c.get('ai')}")
    elif ai:
        out.append(f"  AI cross-check: not run — {ai.get('note', '')}")
    return out
