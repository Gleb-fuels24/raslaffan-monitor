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
        "label": "producing and exporting",
        "status": "good",
        "note": "thermal activity confirmed by LNG loadings — "
                "two independent sources agree",
    },
    "FLARING_NO_EXPORT": {
        "label": "flaring, not exporting",
        "status": "warning",
        "note": "heat present, no LNG loadings. Possible causes: "
                "product going to storage, commissioning or pre-maintenance "
                "purge flaring, or exports blocked",
    },
    "EXPORTING_FROM_STORAGE": {
        "label": "exporting from storage",
        "status": "warning",
        "note": "loadings continue with no thermal activity — "
                "stored product is leaving, liquefaction is down",
    },
    "REBUILDING": {
        "label": "reconstruction under way",
        "status": "serious",
        "note": "no product leaving, cargo arriving — "
                "the signature of restoration works",
    },
    "DORMANT": {
        "label": "site not operating",
        "status": "critical",
        "note": "no heat, no loadings, no inbound cargo",
    },
    "NO_DATA": {
        "label": "not enough data",
        "status": "none",
        "note": "not enough imagery, or no AIS collected for the period",
    },
}


def _imagery_only_verdict(sat, thermal, hot_trains, frp_sum, have_sat,
                          lng_alongside, liquid_alongside, cargo_alongside,
                          filling, img_signals, caveats):
    """
    Вердикт, когда судовых данных нет и опереться можно только на снимок.

    Вызывается в двух случаях: AIS не собирался вовсе и AIS собирался,
    но не увидел ни одного судна. Второй случай раньше обрабатывался
    как «отгрузок ноль», и витрина сама себе противоречила.
    """
    exporting_img = bool(lng_alongside or liquid_alongside)
    # Завозом считаем только подтверждённый случай: судно у
    # сухогрузного причала И заполняющаяся площадка складирования.
    incoming_img = bool(cargo_alongside and filling)

    if not have_sat:
        verdict = "NO_DATA"
    elif thermal and exporting_img:
        verdict = "PRODUCING_EXPORTING"
    elif not thermal and exporting_img:
        verdict = "EXPORTING_FROM_STORAGE"
    elif thermal:
        verdict = "FLARING_NO_EXPORT"
    elif incoming_img:
        verdict = "REBUILDING"
    else:
        verdict = "DORMANT"

    return {
        "verdict": verdict,
        "label": VERDICTS[verdict]["label"],
        "status": VERDICTS[verdict]["status"],
        "note": VERDICTS[verdict]["note"],
        # None, а не 0: «неизвестно» и «ноль» — разные утверждения.
        "signals": dict({"thermal": thermal, "hot_trains": len(hot_trains),
                         "frp_sum": frp_sum,
                         "lng_loadings": None, "liquid_loadings": None,
                         "sulphur_loadings": None, "drycargo_calls": None,
                         "project_cargo_calls": None,
                         "export_pct_of_norm": None}, **img_signals),
        "caveats": caveats,
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
    # Пропуски в ряду (сутки, по которым запроса не было) приходят как
    # None и должны выпадать из суммы, а не считаться нулём. Иначе
    # неделя, когда бот не запускался, занижала бы мощность факела —
    # то есть простой бота выглядел бы как простой завода.
    frp_sum = round(sum(frp for _, frp in (sat.get("firms_trend") or [])
                        if frp is not None), 1)

    # --- Суда у причалов ПО СНИМКУ, независимо от AIS ----------------------
    #
    # Это важнейшая правка во всей логике. Раньше отгрузка СПГ считалась
    # ИСКЛЮЧИТЕЛЬНО по AIS. А в Персидском заливе AIS подменяется, у нас
    # он молчит — и значит «отгрузок ноль» выставлялось всегда, при
    # любом положении дел. Вердикт намертво застревал на «горит, но не
    # вывозит», даже когда у причала стоял газовоз, прекрасно видный на
    # снимке.
    #
    # Теперь занятость причала со снимка идёт в сигналы наравне с AIS.
    # Оговорка существенная и вынесена в caveats: судно у причала —
    # это МОМЕНТ съёмки, а не завершённая погрузка. Оно доказывает, что
    # судно пришло под погрузку, но не что оно ушло гружёным.
    berths = sat.get("berths") or []
    lng_alongside = [b for b in berths
                     if b.get("kind") == "lng" and b.get("occupied")]
    liquid_alongside = [b for b in berths
                        if b.get("kind") == "liquid" and b.get("occupied")]
    cargo_alongside = [b for b in berths
                       if b.get("kind") == "drycargo" and b.get("occupied")]
    unchecked = [b for b in berths if b.get("occupied") is None]

    # НАПРАВЛЕНИЕ ГРУЗА СО СНИМКА НЕ ВИДНО. Это ограничение надо
    # держать в голове всё время. Судно у сухогрузного причала с
    # равной вероятностью:
    #   - привезло модули и технику (стройка разворачивается);
    #   - грузит СЕРУ, побочный продукт газопереработки, которой
    #     Ras Laffan отгружает миллионы тонн в год;
    #   - вывозит строительный мусор и демонтированные конструкции
    #     (тогда это тоже работы, но на другой стадии);
    #   - делает обычный рейс снабжения.
    # На снимке все четыре выглядят одинаково: корпус у причала.
    #
    # Раньше код молча считал такое судно завозом оборудования и
    # выносил вердикт «идёт восстановление». Это была догадка,
    # выданная за наблюдение.
    #
    # Теперь факт стоянки отделён от вывода о направлении. Направление
    # берётся только из того, что его действительно показывает:
    #   - AIS, если работает: приход и уход, осадка, тип судна;
    #   - площадка складирования: заполняется она или пустеет.
    # Если ни того, ни другого нет, пишем «направление неизвестно» и
    # вердикт о восстановлении по одному снимку НЕ выносим.
    yards = [s for s in (sat.get("sites") or [])
             if not s.get("control") and s.get("direction")]
    filling = [s["site"] for s in yards if s["direction"] == "filling"]
    clearing = [s["site"] for s in yards if s["direction"] == "clearing"]

    img_signals = {
        "lng_alongside_img": len(lng_alongside),
        "liquid_alongside_img": len(liquid_alongside),
        "drycargo_alongside_img": len(cargo_alongside),
        "berths_unchecked": len(unchecked),
        "yards_filling": filling,
        "yards_clearing": clearing,
    }

    img_caveats = []
    if lng_alongside:
        img_caveats.append(
            "LNG carrier alongside on the image of "
            f"{(sat.get('scene') or {}).get('date', 'the latest pass')}: "
            f"{', '.join(b['berth'] for b in lng_alongside)}. A vessel at "
            f"the berth proves it came to load; it does not prove it "
            f"sailed loaded.")
    if cargo_alongside:
        if filling:
            img_caveats.append(
                f"Vessel at a dry cargo berth "
                f"({', '.join(b['berth'] for b in cargo_alongside)}) and the "
                f"laydown area is filling ({', '.join(filling)}) — together "
                f"that reads as equipment arriving.")
        elif clearing:
            img_caveats.append(
                f"Vessel at a dry cargo berth "
                f"({', '.join(b['berth'] for b in cargo_alongside)}), but the "
                f"yards are emptying ({', '.join(clearing)}) — this looks "
                f"like material leaving, not arriving.")
        else:
            img_caveats.append(
                f"Vessel at a dry cargo berth "
                f"({', '.join(b['berth'] for b in cargo_alongside)}), "
                f"direction unknown. The image shows a hull at the quay, not "
                f"which way the cargo moves: it may be modules arriving, "
                f"sulphur loading out, or demolition debris leaving. "
                f"AIS or a filling laydown yard is what settles it.")

    if unchecked and not (lng_alongside or liquid_alongside or cargo_alongside):
        img_caveats.append(
            f"{len(unchecked)} berths could not be checked on the image: no "
            f"baseline frame yet. Occupancy will be read from the next run.")

    if not ais:
        return _imagery_only_verdict(sat, thermal, hot_trains, frp_sum,
                                     have_sat, lng_alongside,
                                     liquid_alongside, cargo_alongside,
                                     filling, img_signals,
                                     ["No AIS for the period — the verdict "
                                      "rests on imagery alone"] + img_caveats)

    sig = ais.get("signals", {})

    # Если AIS за период не увидел НИ ОДНОГО судна, его цифры — не
    # ноль, а отсутствие данных. Разница принципиальная: «ноль
    # отгрузок» читается как «завод не вывозит», а на самом деле это
    # «мы не знаем». Именно отсюда брались противоречия на витрине:
    # плитка «отгрузок СПГ 0, это 0% нормы» стояла рядом с плиткой
    # «газовоз у причала: 2».
    # «Молчит» — это когда AIS не увидел ни судов, ни единого события.
    # Проверяем и списки судов, и сами счётчики: сводка может прийти
    # без списков, но с заполненными цифрами, и тогда данные есть.
    counters = ("lng_loadings", "liquid_loadings", "sulphur_loadings",
                "drycargo_calls", "project_cargo_calls", "dredgers")
    saw_any = bool(ais.get("at_berth_now") or ais.get("recent")
                   or any(sig.get(k) for k in counters))
    if not saw_any:
        return _imagery_only_verdict(sat, thermal, hot_trains, frp_sum,
                                     have_sat, lng_alongside,
                                     liquid_alongside, cargo_alongside,
                                     filling, img_signals,
                                     ["AIS collected no vessels at all — its "
                                      "figures are unknown, not zero. The "
                                      "verdict below rests on imagery."]
                                     + img_caveats)

    lng = sig.get("lng_loadings", 0)
    liquid = sig.get("liquid_loadings", 0)
    sulphur = sig.get("sulphur_loadings", 0)
    dry = sig.get("drycargo_calls", 0)
    project = sig.get("project_cargo_calls", 0)

    dredgers = sig.get("dredgers", 0) or 0

    # Снимок равноправен с AIS: судно, видимое у причала, считается
    # признаком вывоза даже когда транспондеры молчат или врут.
    exporting = lng > 0 or bool(lng_alongside or liquid_alongside)
    # Завоз грузов и морские работы — оба признака стройки. Судно у
    # сухогрузного причала добавляется сюда ТОЛЬКО вместе с
    # заполняющейся площадкой: само по себе оно направления не
    # доказывает. AIS направление знает, поэтому dry и project
    # засчитываются как раньше.
    incoming = (dry > 0 or project > 0 or dredgers > 0
                or bool(cargo_alongside and filling))

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

    caveats = list(img_caveats)
    if verdict == "REBUILDING":
        caveats.append(
            "Inbound cargo confirms works at the complex but does NOT "
            "identify the project: the Train 4 and 6 repair, Pearl GTL "
            "maintenance, Barzan restoration and the North Field East "
            "expansion all run at once. Project cargo may belong to any"
        )
    if dredgers:
        caveats.append(
            f"Dredgers in the port area: {dredgers}. Dredging is done "
            f"either for construction or to receive larger vessels")
    if thermal and not exporting and (liquid or sulphur):
        caveats.append(
            f"No LNG loadings, but liquid product ({liquid}) and sulphur "
            f"({sulphur}) are leaving — gas processing runs, liquefaction does not"
        )
    if ais.get("days", 7) < 3:
        caveats.append("The AIS observation window is short — a loading is easy to miss")

    # Данные по снимку и по судам относятся к разному времени: снимок
    # один и датирован, суда наблюдаются непрерывно. Если снимок старый,
    # об этом надо сказать прямо — иначе легко принять его за свежий.
    scene = sat.get("scene") or {}
    if scene.get("age_days", 0) >= 7:
        caveats.append(
            f"Image dated {scene['date']}, {scene['age_days']} d old. "
            f"Thermal conclusions refer to that date; vessel data is current")

    return {
        "verdict": verdict,
        "label": VERDICTS[verdict]["label"],
        "status": VERDICTS[verdict]["status"],
        "note": VERDICTS[verdict]["note"],
        "signals": {
            "thermal": thermal,
            "hot_trains": len(hot_trains),
            "frp_sum": frp_sum,
            **img_signals,
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
    # Судно у причала СПГ, увиденное на снимке, — такое же
    # подтверждение вывоза, как запись в AIS. Без этого при молчащем
    # AIS каждая горячая линия получала приписку «отгрузок нет» и
    # падала до низкой уверенности, даже когда газовоз стоял у пирса.
    alongside = [b["berth"] for b in (sat.get("berths") or [])
                 if b.get("kind") == "lng" and b.get("occupied")]

    lng = (ais or {}).get("signals", {}).get("lng_loadings", 0)
    days = (ais or {}).get("days", 7)
    if not ais and not alongside:
        return sat

    for t in sat.get("trains", []):
        if t.get("status") != "PRODUCING":
            continue

        if lng > 0:
            note = (f"LNG loadings across the complex in {days} d: {lng} — "
                    f"thermal signal confirmed by product leaving")
            better = True
        elif alongside:
            note = (f"LNG carrier alongside on the image: "
                    f"{', '.join(alongside)} — the thermal signal is backed "
                    f"by a vessel at the berth, though a completed loading "
                    f"is not proven")
            better = True
        else:
            note = (f"NOTE: not one LNG loading across the complex in "
                    f"{days} d. The heat may be repair or purge flaring, "
                    f"not liquefaction")
            better = False

        # Защита от повтора. fusion.run вызывается и при сборке сайта,
        # и при сборке отчёта, причём над ОДНИМ словарём sat. Без этой
        # проверки одна и та же приписка появлялась дважды — что и было
        # видно на странице.
        if note not in t["evidence"]:
            t["evidence"].append(note)

        if better:
            if t.get("confidence") == "medium":
                t["confidence"] = "high"
        else:
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
