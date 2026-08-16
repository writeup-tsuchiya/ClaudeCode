#!/usr/bin/env python3
"""⑥slides の Manim エンジン本体（既定）。

🔴 このファイルの構造は固定する。LLM に毎回書かせない（§7-5）。
   変わるのは timeline.json の中身と config/ の値だけ。
   毎回自由に書かせると、焼くたびに壊れ方が変わって原因を追えなくなる。

レイアウトの原則（§10-4 で3回同じ形の失敗をしている）:
  ・折り返しを自分で計算しない → MarkupText に渡し、Pango に折らせる
    （実測: MarkupText は日本語を折り返す。Text は折り返さない）
  ・要素の位置を「文字サイズ×係数」で計算しない
    → VGroup.arrange(DOWN) で縦積みさせる。CSS の flex に当たる。
    見出しが2行になった瞬間に重なる、という事故はこれで消える
  ・🔴 文字数は台本次第で変わるので、レンダ前に高さは決まらない。
    計算しようとすること自体が誤り。

決定論（§7-4）:
  ・時計・乱数・ネットワークを使わない
  ・背景の振り分けは hashlib ベースの rotation()（プール長と互いに素な歩幅）

環境変数で受け取る（manim CLI から起動されるため）:
  AVATAR_SHORT_TIMELINE  timeline.json のパス
  AVATAR_SHORT_STYLE     config/styles/<name>.json のパス
  AVATAR_SHORT_THEME     config/telop_theme.json のパス
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from manim import (  # type: ignore[import-not-found]
    DOWN,
    LEFT,
    UP,
    FadeIn,
    FadeOut,
    ManimColor,
    MarkupText,
    Rectangle,
    RoundedRectangle,
    Scene,
    VGroup,
    config,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import rotation, stable_digest  # noqa: E402


def _load(env_key: str) -> dict:
    path = os.environ.get(env_key)
    if not path:
        raise SystemExit(f"環境変数がありません: {env_key}")
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _strip(obj):
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [_strip(v) for v in obj]
    return obj


def pick_font(candidates: list[str]) -> str:
    """フォントスタックのうち、実際に入っているものを選ぶ。

    🔴 入っていないフォントを指定すると、別の書体で焼き上がる（静かな劣化）。
       だから「選べたか」を必ず出力する。
    """
    try:
        import manimpango

        installed = set(manimpango.list_fonts())
    except Exception:  # noqa: BLE001
        installed = set()
    for name in candidates:
        if name in installed:
            print(f"[slides] font={name}", flush=True)
            return name
    fallback = candidates[0] if candidates else "sans-serif"
    print(f"[slides] ⚠️ フォントスタックのどれも入っていません "
          f"({', '.join(candidates)})。{fallback} を指定しますが、"
          f"別の書体で焼き上がる可能性があります。", flush=True)
    return fallback


def _fit_frame_to_pixels() -> None:
    """🔴 Manim は --resolution を渡してもシーンの座標系を追随させない。

    実測: --resolution 1080,1920 を渡すと pixel は 1080x1920 になるが、
    frame_width / frame_height は 14.222 x 8.000（16:9）のまま。
    その結果、横向きの絵が縦の画面の中央に置かれ、上下に黒帯が入った状態で
    「1080x1920 である」という契約だけは満たしてしまう（＝検査をすり抜ける）。

    ピクセル比に合わせて frame_width を決め直す。
    """
    want = config.frame_height * config.pixel_width / config.pixel_height
    if abs(config.frame_width - want) > 1e-6:
        print(f"[slides] frame_width を {config.frame_width:.3f} → {want:.3f} に直します "
              f"(pixel {config.pixel_width}x{config.pixel_height} に合わせる)", flush=True)
        config.frame_width = want


# 🔴 Scene（＝カメラ）が組み立てられる**前**に直すこと。
#    setup() の中で直しても、カメラは古い frame_width で倍率を決めたあとなので、
#    絵が画面の3割の大きさで中央に置かれる（実測: 1080/14.222 = 75.9px/単位 のまま）。
#    manim CLI は「config を解釈 → このモジュールを import → Scene を作る」の順に動くので、
#    モジュールの読み込み時に直せば間に合う。
_fit_frame_to_pixels()


class AvatarShortSlides(Scene):
    def setup(self) -> None:
        self.timeline = _strip(_load("AVATAR_SHORT_TIMELINE"))
        self.style = _strip(_load("AVATAR_SHORT_STYLE"))
        self.theme = _strip(_load("AVATAR_SHORT_THEME"))

        pal = self.style["palette"]
        self.c_ink = ManimColor(pal["ink"])
        self.c_ink_weak = ManimColor(pal["ink_weak"])
        self.c_accent = ManimColor(pal["accent"])

        stack = self.style.get("font_stack_override") or self.theme["font_stack"]
        self.font = pick_font([f for f in stack if f not in ("sans-serif", "serif")])

        # px → manim の単位
        self.px = config.frame_height / self.theme["canvas"]["height"]

        # 🔴 font_size の換算係数を**推測しない。測る。**
        #    Manim の font_size は px でも pt でもない独自の単位で、
        #    書体によって同じ font_size でも字面の高さが変わる。
        #    定数を掛けて済ませると、書体を変えた瞬間に文字の大きさが狂う。
        #    → 基準の文字を1つ実際に組んで、その高さから係数を求める。
        probe_size = 100.0
        probe = MarkupText("国", font=self.font, font_size=probe_size)
        units_per_font_size = float(probe.height) / probe_size
        self.font_size_per_px = self.px / units_per_font_size
        print(f"[slides] font_size 換算を実測しました: "
              f"1px = font_size {self.font_size_per_px:.4f} "
              f"(基準文字『国』font_size={probe_size:.0f} → 高さ {probe.height:.4f} 単位)",
              flush=True)
        safe = self.theme["safe_area"]
        self.safe_w = (self.theme["canvas"]["width"] - safe["left"] - safe["right"]) * self.px
        self.safe_left = -config.frame_width / 2 + safe["left"] * self.px
        self.safe_top = config.frame_height / 2 - safe["top"] * self.px
        self.safe_bottom = -config.frame_height / 2 + safe["bottom"] * self.px

        # 背景プールの振り分け（§10-4b 互いに素な歩幅で回す）
        pool = self.style["background_pool"]
        n = len(self.timeline["segments"])
        self.bg_index = rotation(n, len(pool), key=self.timeline.get("person_id", "run"),
                                 salt="background")
        print(f"[slides] 背景の割り当て: {self.bg_index} (プール {len(pool)}種)", flush=True)

    # ------------------------------------------------------------------
    def _bg(self, spec: dict) -> Rectangle:
        rect = Rectangle(width=config.frame_width, height=config.frame_height,
                         stroke_width=0)
        if spec["kind"] == "solid":
            rect.set_fill(ManimColor(spec["color"]), opacity=1.0)
        else:
            rect.set_fill(color=[ManimColor(spec["from"]), ManimColor(spec["to"])],
                          opacity=1.0)
            # 角度は上下方向の2色で近似する（Manim の塗りは軸方向のみ）
        return rect

    def _text(self, body: str, key: str, color) -> MarkupText:
        t = self.theme["type"][key]
        m = MarkupText(
            _escape(body),
            font=self.font,
            font_size=t["size"] * self.font_size_per_px,   # 実測で較正した係数（setup 参照）
            weight=_weight(t.get("weight", 400)),
            color=color,
            line_spacing=t.get("line_height", 1.25) - 0.75,
        )
        # 折り返しは Pango がやる。ここでは「はみ出したら縮める」だけ。
        if m.width > self.safe_w:
            m.scale_to_fit_width(self.safe_w)
        return m

    def _rule(self) -> Rectangle:
        s = self.style["shapes"]
        return Rectangle(width=s["rule_width"] * self.px,
                         height=s["rule_thickness"] * self.px,
                         stroke_width=0).set_fill(self.c_accent, opacity=1.0)

    def _card(self, card: dict) -> VGroup:
        s = self.style["shapes"]
        parts = [self._text(card["t"], "card_title", self.c_ink)]
        if card.get("d"):
            parts.append(self._text(card["d"], "card_desc", self.c_ink_weak))
        inner = VGroup(*parts).arrange(DOWN, aligned_edge=LEFT, buff=10 * self.px)
        pad = 28 * self.px
        box = RoundedRectangle(
            corner_radius=s["card_radius"] * self.px,
            width=min(self.safe_w, inner.width + pad * 2),
            height=inner.height + pad * 2,
            stroke_width=1.5,
        )
        box.set_fill(ManimColor(self.style["palette"]["paper"]), opacity=0.55)
        box.set_stroke(self.c_ink_weak, opacity=0.25)
        inner.move_to(box.get_center())
        return VGroup(box, inner)

    def _panel(self, telop: dict) -> VGroup:
        """1段ぶんの文字の層。**位置は計算せず arrange(DOWN) に積ませる。**"""
        rows: list = []
        if telop.get("kicker"):
            rows.append(VGroup(self._rule(),
                               self._text(telop["kicker"], "kicker", self.c_accent))
                        .arrange(LEFT * -1, buff=16 * self.px))
        if telop.get("headline"):
            rows.append(self._text(telop["headline"], "headline", self.c_ink))
        if telop.get("sub"):
            rows.append(self._text(telop["sub"], "sub", self.c_ink_weak))
        if telop.get("meta"):
            rows.append(self._text(telop["meta"], "meta", self.c_ink_weak))
        for card in (telop.get("cards") or [])[:3]:
            if card.get("t"):
                rows.append(self._card(card))

        if not rows:
            return VGroup()

        group = VGroup(*rows).arrange(DOWN, aligned_edge=LEFT, buff=26 * self.px)
        # 縦にはみ出したら、全体を縮める（切らない・重ねない）
        max_h = self.safe_top - self.safe_bottom
        if group.height > max_h:
            group.scale(max_h / group.height)
        group.to_edge(LEFT, buff=0).shift(
            (self.safe_left - group.get_left()[0]) * __import__("numpy").array([1.0, 0.0, 0.0]))
        group.shift(__import__("numpy").array(
            [0.0, self.safe_bottom + group.height / 2 - group.get_center()[1], 0.0]))
        return group

    # ------------------------------------------------------------------
    def construct(self) -> None:
        pool = self.style["background_pool"]
        motion = self.theme["motion"]
        t_in, t_out = float(motion["in"]), float(motion["out"])
        segments = self.timeline["segments"]

        cursor = 0.0
        for i, seg in enumerate(segments):
            start, end = float(seg["start"]), float(seg["end"])
            # 前の段の終わりから次の段の始まりまでの隙間も背景で埋める
            if start > cursor:
                self.wait(start - cursor)
                cursor = start

            bg = self._bg(pool[self.bg_index[i]])
            panel = self._panel(seg.get("telop", {}))
            self.add(bg)

            dur = max(0.0, end - start)
            body = max(0.0, dur - t_in - t_out)
            if panel.submobjects or len(panel) > 0:
                self.play(FadeIn(panel, shift=UP * 24 * self.px), run_time=min(t_in, dur))
                if body > 0:
                    self.wait(body)
                self.play(FadeOut(panel), run_time=min(t_out, max(0.0, dur - t_in)))
            else:
                self.wait(dur)
            self.remove(bg, panel)
            cursor = end

        total = float(self.timeline["total_sec"])
        if total > cursor:
            self.add(self._bg(pool[self.bg_index[-1]]))
            self.wait(total - cursor)


def _escape(s: str) -> str:
    """MarkupText は Pango マークアップを解釈するので、素の文字はエスケープする。"""
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _weight(w: int) -> str:
    if w >= 800:
        return "HEAVY"
    if w >= 700:
        return "BOLD"
    if w >= 500:
        return "MEDIUM"
    return "NORMAL"
