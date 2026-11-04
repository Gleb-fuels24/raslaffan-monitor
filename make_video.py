"""
make_video.py — ролик, объясняющий продукт подрядчику.

Собирает слайды картинками и склеивает их в MP4 через ffmpeg.
Голоса нет: озвучку можно наложить отдельно, а текст на слайдах
читается и без звука — в ленте соцсетей ролики всё равно смотрят
с выключенным звуком.

Запуск:  python make_video.py

Нужен ffmpeg. Проверить, установлен ли он:  ffmpeg -version
Если нет, на Windows проще всего поставить через winget:
    winget install ffmpeg
После установки закройте и откройте командную строку заново.
"""

import os
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080
OUT_DIR = "data/video"
FPS = 30

# Те же цвета, что на сайте — ролик и витрина должны выглядеть одним целым.
BG = (13, 13, 13)
SURFACE = (26, 26, 25)
INK = (240, 239, 236)
INK2 = (195, 194, 183)
MUTED = (137, 135, 129)
ACCENT = (57, 135, 229)
GOOD = (12, 163, 12)
WARN = (250, 178, 25)
SERIOUS = (236, 131, 90)
CRIT = (208, 59, 59)


def _font(size, bold=False):
    """
    Ищет системный шрифт. На разных системах они лежат по-разному,
    поэтому перебираем обычные места и в крайнем случае берём
    встроенный — он мелкий, но ролик всё равно соберётся.
    """
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold
        else "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _wrap(draw, text, font, max_w):
    """Переносит текст по словам, чтобы он влезал в ширину."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def slide(kicker, title, body=None, bullets=None, big=None, big_label=None,
          footer=None, accent=ACCENT):
    """Рисует один слайд."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # Полоса-акцент слева
    d.rectangle([0, 0, 10, H], fill=accent)

    f_kick = _font(34, True)
    f_title = _font(78, True)
    f_body = _font(42)
    f_big = _font(190, True)
    f_small = _font(30)

    x = 120
    y = 120

    if kicker:
        d.text((x, y), kicker.upper(), font=f_kick, fill=accent)
        y += 70

    for line in _wrap(d, title, f_title, W - 2 * x)[:3]:
        d.text((x, y), line, font=f_title, fill=INK)
        y += 92
    y += 20

    if big:
        d.text((x, y), big, font=f_big, fill=accent)
        bw = d.textlength(big, font=f_big)
        if big_label:
            d.text((x + bw + 24, y + 110), big_label, font=f_body, fill=INK2)
        y += 230

    if body:
        for line in _wrap(d, body, f_body, W - 2 * x - 40)[:5]:
            d.text((x, y), line, font=f_body, fill=INK2)
            y += 60

    if bullets:
        for b in bullets[:5]:
            d.ellipse([x + 4, y + 22, x + 20, y + 38], fill=accent)
            for i, line in enumerate(_wrap(d, b, f_body, W - 2 * x - 60)[:2]):
                d.text((x + 44, y), line, font=f_body, fill=INK2)
                y += 58
            y += 16

    if footer:
        d.text((x, H - 90), footer, font=f_small, fill=MUTED)

    return img


def build_slides(out_dir):
    """
    Сценарий ролика. Каждый слайд — одна мысль.

    Ролик только о выгоде. Ни методики, ни ограничений здесь нет
    намеренно: ограничения — предмет честного разговора на сайте и на
    встрече, а в ролике они гасят интерес ровно там, где он должен
    расти. Про то, как это считается, подрядчик спросит сам.
    """
    os.makedirs(out_dir, exist_ok=True)
    S = []

    S.append((slide(
        "Fuels24 · Asset Intelligence",
        "Know where the work is before the tender",
        body="Daily intelligence on LNG and refining assets under "
             "reconstruction — so you arrive before the invitation list "
             "closes.",
        footer="Built from open data. No insider information.",
    ), 5.0))

    S.append((slide(
        "The prize",
        "Three years of contracts at one site",
        bullets=[
            "Ras Laffan: two liquefaction trains hit, 17% of export capacity",
            "QatarEnergy's CEO puts the repair at three years",
            "Technip Energies is mobilised — the packages are being split now",
        ],
        accent=WARN,
    ), 7.5))

    S.append((slide(
        "What you get",
        "One number, every morning",
        big="71",
        big_label="/ 100 — high activity",
        body="The activity index tells you whether this is the moment to "
             "engage. It rises with idle capacity, equipment arriving, "
             "tender mentions and mass hiring.",
        footer="Every component and weight is shown. Not a black box.",
    ), 7.5))

    S.append((slide(
        "Your advantage",
        "Weeks of lead time on the competition",
        bullets=[
            "Hiring of welders and fitters shows months before the site changes",
            "Tender mentions surface weeks before the official notice",
            "Equipment arriving by ship is visible the day it docks",
        ],
        accent=GOOD,
    ), 7.0))

    S.append((slide(
        "Who to approach",
        "A decision map, not a contact list",
        bullets=[
            "The named people who sign off, from the companies' own filings",
            "Which desk opens the door, and in what order",
            "What the supplier portal will ask you for",
        ],
    ), 7.0))

    S.append((slide(
        "How it reaches you",
        "Updated within 30 minutes of the new image",
        body="The satellite passes, the image is published, the report is on "
             "your screen half an hour later. Every day, automatically, with "
             "nothing for you to run.",
    ), 6.5))

    S.append((slide(
        "Next",
        "The same monitor for your asset",
        body="Any refinery, terminal or LNG plant in the world. Set-up for a "
             "specific asset takes days, not months.",
        footer="Gleb N. Zamyatin, MSc · Fuels24 Inc · z@fuels24.com",
        accent=GOOD,
    ), 7.0))

    paths = []
    for i, (img, dur) in enumerate(S):
        p = os.path.join(out_dir, f"slide_{i:02d}.png")
        img.save(p)
        paths.append((p, dur))
    return paths


def render(out_path="data/video/raslaffan_epc.mp4"):
    """Склеивает слайды в ролик с плавными переходами."""
    if not shutil.which("ffmpeg"):
        print("ffmpeg не найден. Поставьте его и повторите:")
        print("  Windows:  winget install ffmpeg")
        print("  macOS:    brew install ffmpeg")
        return None

    slides = build_slides(OUT_DIR)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    # Список кадров для ffmpeg: каждый слайд держится заданное время.
    listfile = os.path.join(OUT_DIR, "slides.txt")
    with open(listfile, "w", encoding="utf-8") as fh:
        for p, dur in slides:
            fh.write(f"file '{os.path.abspath(p)}'\n")
            fh.write(f"duration {dur}\n")
        # Последний кадр нужно указать дважды — иначе ffmpeg его обрежет.
        fh.write(f"file '{os.path.abspath(slides[-1][0])}'\n")

    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listfile,
        "-vf", f"fps={FPS},format=yuv420p,"
               f"fade=t=in:st=0:d=0.6",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-movflags", "+faststart",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("ffmpeg не справился:")
        print(r.stderr[-1500:])
        return None

    size_mb = os.path.getsize(out_path) / 1e6
    total = sum(d for _, d in slides)
    print(f"Готово: {out_path}")
    print(f"  длительность {total:.0f} сек, размер {size_mb:.1f} МБ")
    return out_path


if __name__ == "__main__":
    render(sys.argv[1] if len(sys.argv) > 1 else "data/video/raslaffan_epc.mp4")
