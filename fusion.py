"""
fusion.py — слияние трёх независимых сигналов в один вывод.

Главная мысль, из-за которой этот модуль существует.

Факел сам по себе НЕ доказывает производство. Газ горит и когда завод
работает, и когда его продувают перед ремонтом, и когда идёт пусконаладка,
и когда продукт некуда девать. Производство доказывает ОТГРУЗКА: если от
причала ушёл гружёный газовоз, значит СПГ реально сделали.

И обратное. Если отгрузок нет, а сухогрузы идут один за другим — значит
на площадку завозят оборудование, то есть идёт восстановление.

Отсюда матрица:

  тепло | отгрузка СПГ | завоз | вывод
  ------+--------------+-------+-----------------------------------------
   есть |     есть     |   -   | производит и вывозит (подтверждено)
   есть |      нет     |   -   | горит, но не вывозит: копит в хранилище,
         |              |       | либо факел ремонта, либо экспорт закрыт
    нет |     есть     |   -   | вывозят из запасов, сжижение стоит
    нет |      нет     |  есть | идёт восстановление
    нет |      нет     |  нет  | площадка мертва

Отгрузка привязывается к КОМПЛЕКСУ, а не к линии: СПГ со всех линий идёт
в общие хранилища и на общие причалы. Поэтому модуль делает две вещи:
  1) выносит вердикт по комплексу целиком — там отгрузка работает;
  2) поправляет уверенность по каждой линии — например, помечает линию,
     которая «горит» на фоне полного отсутствия отгрузок: скорее всего
     это факел ремонта, а не сжижение.
"""

import config

# --- Вердикты по комплексу -------------------------------------------------
VERDICTS = {
    "PRODUCING_EXPORTING": {
        "ru": "производит и вывозит",
        "status": "good",
        "note": "тепловая активность подтверждена отгрузками СПГ — "
                "два независимых источника сходятся",
    },
    "FLARING_NO_EXPORT": {
        "ru": "горит, но не вывозит",
        "status": "warning",
        "note": "тепло есть, отгрузок СПГ нет. Возможные причины: "
                "накопление в хранилище, факел пусконаладки или продувки "
                "перед ремонтом, либо экспорт заблокирован",
    },
    "EXPORTING_FROM_STORAGE": {
        "ru": "вывозит из запасов",
        "status": "warning",
        "note": "отгрузки идут, но тепловой активности нет — "
                "вывозят накопленное, сжижение стоит",
    },
    "REBUILDING": {
        "ru": "идёт восстановление",
        "status": "serious",
        "note": "вывоза продукта нет, идёт завоз грузов — "
                "характерная картина восстановительных работ",
    },
    "DORMANT": {
        "ru": "площадка не работает",
        "status": "critical",
        "note": "ни тепла, ни отгрузок, ни завоза",
    },
    "NO_DATA": {
        "ru": "недостаточно данных",
        "status": "none",
        "note": "не хватает снимков или не собран AIS за период",
    },
}


def complex_verdict(sat: dict, ais: dict = None) -> dict:
    """
    Вердикт по комплексу целиком на основе трёх сигналов.

    sat — результат analyze.run()
    ais — результат ais_watch.summarize(); если None, работаем без судов.
    """
    trains = sat.get("trains", [])
    hot_trains = [t for t in trains if t.get("status") == "PRODUCING"]
    have_sat = any(t.get("status") not in ("NO_DATA", "ERROR") for t in trains)

    thermal = len(hot_trains) > 0
    frp_sum = round(sum(frp for _, frp in (sat.get("firms_trend") or [])), 1)

    if not ais:
        verdict = "NO_DATA" if not have_sat else (
            "FLARING_NO_EXPORT" if thermal else "DORMANT")
        return {
            "verdict": verdict,
            "ru": VERDICTS[verdict]["ru"],
            "status": VERDICTS[verdict]["status"],
            "note": VERDICTS[verdict]["note"],
            "signals": {"thermal": thermal, "frp_sum": frp_sum,
                        "lng_loadings": None, "drycargo_calls": None,
                        "project_cargo_calls": None, "export_pct_of_norm": None},
            "caveats": ["AIS за период не собран — вывод только по снимкам"],
        }

    sig = ais.get("signals", {})
    lng = sig.get("lng_loadings", 0)
    liquid = sig.get("liquid_loadings", 0)
    sulphur = sig.get("sulphur_loadings", 0)
    dry = sig.get("drycargo_calls", 0)
    project = sig.get("project_cargo_calls", 0)

    dredgers = sig.get("dredgers", 0) or 0

    exporting = lng > 0
    # Завоз грузов и морские работы — оба признака стройки.
    incoming = dry > 0 or project > 0 or dredgers > 0

    if thermal and exporting:
        verdict = "PRODUCING_EXPORTING"
    elif thermal and not exporting:
        verdict = "FLARING_NO_EXPORT"
    elif not thermal and exporting:
        verdict = "EXPORTING_FROM_STORAGE"
    elif incoming:
        verdict = "REBUILDING"
    elif not have_sat:
        verdict = "NO_DATA"
    else:
        verdict = "DORMANT"

    caveats = []
    if verdict == "REBUILDING":
        caveats.append(
            "Завоз грузов подтверждает работы на комплексе, но НЕ говорит, "
            "на каком объекте: одновременно идут ремонт линий 4 и 6, ремонт "
            "Pearl GTL, восстановление Barzan и стройка расширения North "
            "Field East. Проектный груз может идти на любой из них"
        )
    if dredgers:
        caveats.append(
            f"В районе порта земснарядов: {dredgers}. Дноуглубление ведут "
            f"под стройку или под приём более крупных судов")
    if thermal and not exporting and (liquid or sulphur):
        caveats.append(
            f"Отгрузок СПГ нет, но есть отгрузки жидких продуктов ({liquid}) "
            f"и серы ({sulphur}) — газопереработка работает, сжижение нет"
        )
    if ais.get("days", 7) < 3:
        caveats.append("Период наблюдения AIS короткий — отгрузку легко пропустить")

    # Данные по снимку и по судам относятся к разному времени: снимок
    # один и датирован, суда наблюдаются непрерывно. Если снимок старый,
    # об этом надо сказать прямо — иначе легко принять его за свежий.
    scene = sat.get("scene") or {}
    if scene.get("age_days", 0) >= 7:
        caveats.append(
            f"Снимок датирован {scene['date']} — ему {scene['age_days']} дн. "
            f"Тепловые выводы относятся к этой дате, судовые данные свежие")

    return {
        "verdict": verdict,
        "ru": VERDICTS[verdict]["ru"],
        "status": VERDICTS[verdict]["status"],
        "note": VERDICTS[verdict]["note"],
        "signals": {
            "thermal": thermal,
            "hot_trains": len(hot_trains),
            "frp_sum": frp_sum,
            "lng_loadings": lng,
            "liquid_loadings": liquid,
            "sulphur_loadings": sulphur,
            "drycargo_calls": dry,
            "project_cargo_calls": project,
            "dredgers": dredgers,
            "tugs": sig.get("tugs", 0),
            "export_pct_of_norm": sig.get("export_pct_of_norm"),
            "norm_cargoes": sig.get("norm_cargoes"),
        },
        "caveats": caveats,
    }


def adjust_trains(sat: dict, ais: dict = None) -> dict:
    """
    Поправляет выводы по линиям с учётом судовых данных.

    Главная поправка: линия «горит», а отгрузок СПГ по всему комплексу нет.
    Значит с высокой вероятностью это не сжижение, а факел ремонта или
    продувки. Уверенность понижается, в обоснование добавляется пояснение.

    Меняется ТОЛЬКО уверенность и пояснения, сам статус не переписывается:
    судовые данные относятся к комплексу, а не к линии, и переопределять
    ими вывод по снимку было бы подлогом.
    """
    if not ais:
        return sat

    lng = ais.get("signals", {}).get("lng_loadings", 0)
    days = ais.get("days", 7)

    for t in sat.get("trains", []):
        if t.get("status") != "PRODUCING":
            continue
        if lng > 0:
            t["evidence"].append(
                f"отгрузки СПГ по комплексу за {days} дн.: {lng} — "
                f"тепловой сигнал подтверждён вывозом продукта")
            if t.get("confidence") == "medium":
                t["confidence"] = "high"
        else:
            t["evidence"].append(
                f"ВНИМАНИЕ: за {days} дн. ни одной отгрузки СПГ по комплексу. "
                f"Тепло может быть факелом ремонта или продувки, а не сжижением")
            t["confidence"] = "low"
    return sat


def capacity_estimate(sat: dict) -> dict:
    """Сколько мощности в работе по линиям, в млн т/год."""
    producing = sum(t["mtpa"] for t in sat.get("trains", [])
                    if t.get("status") == "PRODUCING")
    down = sum(t["mtpa"] for t in sat.get("trains", [])
               if t.get("status") in ("DESTROYED", "REBUILDING", "IDLE"))
    total = producing + down
    return {
        "producing_mtpa": round(producing, 1),
        "down_mtpa": round(down, 1),
        "utilisation_pct": round(100.0 * producing / total, 1) if total else None,
    }


def run(sat: dict, ais: dict = None) -> dict:
    """Полное слияние: вердикт по комплексу + поправки по линиям + мощность."""
    sat = adjust_trains(sat, ais)
    return {
        "complex": complex_verdict(sat, ais),
        "capacity": capacity_estimate(sat),
        "sat": sat,
        "ais": ais,
    }
