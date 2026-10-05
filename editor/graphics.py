"""그림 만들기: 인트로 제목 카드, 아웃트로 카드, 자료화면 카드(기사 제목/사이트), 출처 표기."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .common import asset, hex_to_rgba

FALLBACK_FONT = "/usr/share/fonts/truetype/nanum/NanumGothicExtraBold.ttf"


def font(path_or_rule: str | None, size: int) -> ImageFont.FreeTypeFont:
    for cand in (path_or_rule, "assets/fonts/Pretendard-ExtraBold.otf", FALLBACK_FONT):
        if cand and asset(cand).exists():
            return ImageFont.truetype(str(asset(cand)), size)
    return ImageFont.load_default()


def _wrap(draw, text, fnt, max_w):
    lines, cur = [], ""
    for ch in text:
        if draw.textlength(cur + ch, font=fnt) > max_w and cur:
            # 단어 중간에서 끊기지 않게 마지막 공백에서 줄바꿈
            cut = cur.rfind(" ")
            if cut > 0:
                lines.append(cur[:cut])
                cur = cur[cut + 1:] + ch
            else:
                lines.append(cur)
                cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def title_card(text: str, rules: dict, out: Path) -> Path:
    o, c = rules["output"], rules["intro"]
    W, H = o["width"], o["height"]
    img = Image.new("RGB", (W, H), hex_to_rgba(c["background"])[:3])
    d = ImageDraw.Draw(img)
    f = font(rules["emphasis"]["font_file"], c["title_size_px"])
    lines = _wrap(d, text, f, W * 0.82)
    lh = int(c["title_size_px"] * 1.25)
    y = H / 2 - lh * len(lines) / 2
    for i, ln in enumerate(lines):
        col = hex_to_rgba(c["title_accent"] if i == len(lines) - 1 and len(lines) > 1 else c["title_color"])
        w = d.textlength(ln, font=f)
        d.text(((W - w) / 2, y), ln, font=f, fill=col, stroke_width=4, stroke_fill=(0, 0, 0))
        y += lh
    bar_w = int(W * 0.12)
    d.rectangle([(W - bar_w) / 2, y + 20, (W + bar_w) / 2, y + 30], fill=hex_to_rgba(c["title_accent"]))
    img.save(out)
    return out


def outro_card(rules: dict, out: Path) -> Path:
    o, c = rules["output"], rules["outro"]
    W, H = o["width"], o["height"]
    img = Image.new("RGB", (W, H), hex_to_rgba(c["background"])[:3])
    d = ImageDraw.Draw(img)
    f1 = font(rules["emphasis"]["font_file"], 100)
    f2 = font(rules["subtitle"]["font_file"], 56)
    for txt, f, y, col in [(c["text"], f1, H * 0.40, rules["intro"]["title_accent"]), (c["sub_text"], f2, H * 0.56, "#FFFFFF")]:
        w = d.textlength(txt, font=f)
        d.text(((W - w) / 2, y), txt, font=f, fill=hex_to_rgba(col), stroke_width=3, stroke_fill=(0, 0, 0))
    img.save(out)
    return out


def article_card(title: str, source: str, url: str, kind: str, rules: dict, out: Path, sub: str = "") -> Path:
    """인터넷 화면을 캡처할 수 없을 때: 브라우저 창 모양의 '기사 제목/사이트' 카드를 만든다."""
    W, H = 1600, 900
    img = Image.new("RGB", (W, H), (245, 246, 248))
    d = ImageDraw.Draw(img)
    # 브라우저 상단바
    d.rectangle([0, 0, W, 86], fill=(222, 225, 230))
    for i, col in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([28 + i * 34, 30, 50 + i * 34, 52], fill=col)
    d.rounded_rectangle([150, 20, W - 40, 66], radius=22, fill=(255, 255, 255))
    d.text((176, 28), url or source, font=font(rules["subtitle"]["font_file"], 26), fill=(90, 90, 90))
    pad = 110
    y = 150
    fs = font(rules["subtitle"]["font_file"], 34)
    d.text((pad, y), source, font=fs, fill=(30, 100, 220))
    y += 70
    if kind == "article":
        d.rectangle([pad, y, pad + 120, y + 8], fill=(220, 40, 40))
        y += 40
    ft = font(rules["subtitle"]["font_file"], 74)
    for ln in _wrap(d, title, ft, W - pad * 2)[:4]:
        d.text((pad, y), ln, font=ft, fill=(20, 20, 20))
        y += 98
    if sub:
        y += 20
        fsub = font("assets/fonts/Pretendard-Bold.otf", 38)
        for ln in _wrap(d, sub, fsub, W - pad * 2)[:3]:
            d.text((pad, y), ln, font=fsub, fill=(90, 90, 90))
            y += 54
    img.save(out)
    return out


def finish_broll(src: Path, source: str, rules: dict, out: Path) -> Path:
    """자료화면을 영상 규격에 맞추고 모서리를 둥글게, 아래에 작은 출처를 붙인다."""
    b, o = rules["broll"], rules["output"]
    W, H = o["width"], o["height"]
    im = Image.open(src).convert("RGB")
    label = b["source_label"].format(source=source) if source else ""
    fs = font("assets/fonts/Pretendard-Bold.otf", b["source_size_px"])
    if b["mode"] == "full":
        canvas = Image.new("RGBA", (W, H), (0, 0, 0, 255))
        im.thumbnail((W, H))
        canvas.paste(im, ((W - im.width) // 2, (H - im.height) // 2))
        d = ImageDraw.Draw(canvas)
        if label:
            tw = d.textlength(label, font=fs)
            d.text((W - tw - 36, H - b["source_size_px"] - 30), label, font=fs, fill=hex_to_rgba(b["source_color"]),
                   stroke_width=2, stroke_fill=(0, 0, 0))
        canvas.save(out)
        return out
    cw = int(W * b["pip_width_ratio"])
    ch = int(cw * im.height / im.width)
    ch = min(ch, int(H * 0.62))
    im = im.resize((cw, int(cw * im.height / im.width))).crop((0, 0, cw, ch))
    r = b["corner_radius_px"]
    mask = Image.new("L", (cw, ch), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, cw, ch], radius=r, fill=255)
    lab_h = b["source_size_px"] + 16 if label else 0
    pad = 24
    canvas = Image.new("RGBA", (cw + pad * 2, ch + pad * 2 + lab_h), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([pad, pad + 8, pad + cw, pad + ch + 8], radius=r, fill=(0, 0, 0, 150))
    canvas = Image.alpha_composite(canvas, shadow.filter(ImageFilter.GaussianBlur(12)))
    canvas.paste(im, (pad, pad), mask)
    if label:
        d = ImageDraw.Draw(canvas)
        tw = d.textlength(label, font=fs)
        d.text((pad + cw - tw, pad + ch + 8), label, font=fs, fill=hex_to_rgba(b["source_color"]),
               stroke_width=2, stroke_fill=(0, 0, 0))
    canvas.save(out)
    return out


