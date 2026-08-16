#!/usr/bin/env python3
"""⑥slides の HyperFrames エンジン（退避）。

Manim が合わない日に戻すための、**同じ契約のもう1つの実装**（§7-1）。
出口は必ず runs/<slug>/slides.mp4（不透過・1080x1920・30fps）。

🔴 HTML を LLM に毎回書かせない（§7-5）。
   構造はこのファイルに固定し、変わるのは timeline.json の中身と設定ファイルの値だけ。
   毎回自由に HTML/CSS を書かせると、焼くたびに壊れ方が変わって原因を追えなくなる。

🔴 焼く前に必ず lint を通す（§7-3）。
   レンダは1回10秒〜数分かかるが、lint は数秒で確定した答えを返す。
   実際に、書式の誤り5件が1回の lint で全部出た。レンダで試行錯誤しない。
"""

from __future__ import annotations

import html
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import REPO_ROOT, die, info, load_json, probe, require_cmd, run, warn  # noqa: E402

HYPERFRAMES_VERSION = "0.7.107"
VENDOR_GSAP = REPO_ROOT / "vendor" / "gsap" / "gsap.min.js"


def available() -> bool:
    return shutil.which("npx") is not None


def setup_hint() -> str:
    return ("Node.js を入れてください（brew install node）。"
            "GSAP は bash tools/setup_hyperframes.sh でローカルに置きます。")


# --------------------------------------------------------------------------
# HTML の生成（構造は固定）
# --------------------------------------------------------------------------

def _font_faces(stack: list[str]) -> str:
    """🔴 フォントは OS 同梱でも宣言が要る。しかもフォントスタックに書いた実名フォント全部に。

    宣言を忘れると別の書体で焼き上がる（静かな劣化・§7-2）。
    """
    lines = []
    for name in stack:
        if name in ("sans-serif", "serif", "monospace"):
            continue
        lines.append(f'@font-face{{font-family:"{name}";src:local("{name}");}}')
    return "\n".join(lines)


def _bg_css(spec: dict) -> str:
    if spec["kind"] == "solid":
        return spec["color"]
    return f"linear-gradient({spec.get('angle', 180)}deg, {spec['from']}, {spec['to']})"


def _telop_html(telop: dict, shapes: dict) -> str:
    parts = []
    if telop.get("kicker"):
        parts.append(f'<div class="kicker"><span class="rule"></span>'
                     f'<span>{html.escape(telop["kicker"])}</span></div>')
    if telop.get("headline"):
        parts.append(f'<div class="headline">{html.escape(telop["headline"])}</div>')
    if telop.get("sub"):
        parts.append(f'<div class="sub">{html.escape(telop["sub"])}</div>')
    if telop.get("meta"):
        parts.append(f'<div class="meta">{html.escape(telop["meta"])}</div>')
    cards = [c for c in (telop.get("cards") or []) if c.get("t")][:3]
    if cards:
        inner = "".join(
            f'<div class="card"><div class="card-t">{html.escape(c["t"])}</div>'
            + (f'<div class="card-d">{html.escape(c["d"])}</div>' if c.get("d") else "")
            + "</div>"
            for c in cards
        )
        parts.append(f'<div class="cards">{inner}</div>')
    return "".join(parts)


def build_html(timeline: dict, style: dict, theme: dict, gsap_src: str) -> str:
    canvas = theme["canvas"]
    safe = theme["safe_area"]
    type_ = theme["type"]
    pal = style["palette"]
    shapes = style["shapes"]
    motion = theme["motion"]
    stack = style.get("font_stack_override") or theme["font_stack"]
    total = float(timeline["total_sec"])
    segments = timeline["segments"]

    sys.path.insert(0, str(REPO_ROOT / "tools"))
    from common import rotation  # noqa: PLC0415

    pool = style["background_pool"]
    bg_idx = rotation(len(segments), len(pool),
                      key=timeline.get("person_id", "run"), salt="background")

    clips, tl_lines = [], []
    for i, seg in enumerate(segments):
        start = float(seg["start"])
        dur = max(0.001, float(seg["end"]) - start)
        # 🔴 class="clip" と data-start/data-duration が無いと、
        #    その要素が全編ずっと表示される（§7-2）
        clips.append(
            f'  <div class="clip seg" id="seg{i}" '
            f'data-start="{start:.3f}" data-duration="{dur:.3f}" '
            f'style="background:{_bg_css(pool[bg_idx[i]])}">\n'
            f'    <div class="panel" id="panel{i}">{_telop_html(seg.get("telop", {}), shapes)}</div>\n'
            f'  </div>'
        )
        tl_lines.append(
            f'  tl.fromTo("#panel{i} > *", '
            f'{{opacity:0, y:24}}, '
            f'{{opacity:1, y:0, duration:{motion["in"]}, ease:"{motion["ease"]}", '
            f'stagger:{motion["stagger"]}}}, {start:.3f});\n'
            f'  tl.to("#panel{i} > *", '
            f'{{opacity:0, duration:{motion["out"]}, ease:"{motion["ease"]}"}}, '
            f'{max(start, start + dur - motion["out"]):.3f});'
        )

    return f"""<div id="root"
     data-composition-id="main"
     data-width="{canvas['width']}" data-height="{canvas['height']}"
     data-fps="{canvas['fps']}"
     data-start="0" data-duration="{total:.3f}">
{chr(10).join(clips)}
</div>

<style>
{_font_faces(stack)}

#root {{
  position: relative;
  width: {canvas['width']}px;
  height: {canvas['height']}px;
  overflow: hidden;
  background: {pal['paper']};
  font-family: {', '.join(f'"{f}"' if f not in ('sans-serif', 'serif') else f for f in stack)};
  color: {pal['ink']};
  -webkit-font-smoothing: antialiased;
}}

.seg {{
  position: absolute; inset: 0;
}}

/* 🔴 位置を「文字サイズ×係数」で計算しない。flex に縦積みさせる（§10-4）。
   見出しが2行になった瞬間に重なる、という事故はこれで消える。 */
.panel {{
  position: absolute;
  left: {safe['left']}px;
  right: {safe['right']}px;
  bottom: {safe['bottom']}px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 26px;
}}

.kicker {{
  display: flex; align-items: center; gap: 16px;
  font-size: {type_['kicker']['size']}px;
  font-weight: {type_['kicker']['weight']};
  letter-spacing: {type_['kicker']['tracking']}em;
  color: {pal['accent']};
}}
.rule {{
  display: block;
  width: {shapes['rule_width']}px; height: {shapes['rule_thickness']}px;
  background: {pal['accent']}; border-radius: {shapes['rule_thickness']}px;
}}

/* 折り返しは CSS に任せる。自分で N文字ごとに折らない（§10-4）。 */
.headline {{
  font-size: {type_['headline']['size']}px;
  font-weight: {type_['headline']['weight']};
  line-height: {type_['headline']['line_height']};
  word-break: normal;
  overflow-wrap: anywhere;
  line-break: strict;
}}
.sub  {{ font-size: {type_['sub']['size']}px;  font-weight: {type_['sub']['weight']};
        letter-spacing: {type_['sub']['tracking']}em; color: {pal['ink_weak']}; }}
.meta {{ font-size: {type_['meta']['size']}px; font-weight: {type_['meta']['weight']};
        color: {pal['ink_weak']}; }}

.cards {{ display: flex; flex-direction: column; gap: 18px; width: 100%; }}
.card {{
  background: {pal['paper']}8C;
  border: 1.5px solid {pal['ink_weak']}40;
  border-radius: {shapes['card_radius']}px;
  padding: 28px 32px;
}}
.card-t {{ font-size: {type_['card_title']['size']}px;
          font-weight: {type_['card_title']['weight']}; }}
.card-d {{ font-size: {type_['card_desc']['size']}px;
          line-height: {type_['card_desc']['line_height']};
          color: {pal['ink_weak']}; margin-top: 8px; }}
</style>

<script src="{gsap_src}"></script>
<script>
  // §7-4 決定論: 時計・乱数・ネットワーク・無限リピートを使わない。
  // paused: true のタイムラインを1本だけ作る。
  window.__timelines = window.__timelines || {{}};
  var tl = gsap.timeline({{paused: true}});
{chr(10).join(tl_lines)}
  // 🔴 キーは data-composition-id と一致させる。ずれるとタイムラインと結びつかない。
  window.__timelines["main"] = tl;
</script>
"""


# --------------------------------------------------------------------------
def _npx(*args: str) -> list[str]:
    return ["npx", "--yes", f"hyperframes@{HYPERFRAMES_VERSION}", *args]


def render(run_dir: Path, style_path: Path, theme_path: Path, *,
           quality: str = "h", keep_intermediate: bool = False) -> Path:
    require_cmd("npx", "brew install node")

    timeline = load_json(run_dir / "timeline.json")
    style = load_json(style_path)
    theme = load_json(theme_path)

    src = run_dir / "slides_src"
    src.mkdir(parents=True, exist_ok=True)

    # 🔴 GSAP はローカルに置いたファイルを読む。
    #    CDN は落ちたとき黙って動きが消える（§7-4）。
    if VENDOR_GSAP.is_file():
        shutil.copy2(VENDOR_GSAP, src / "gsap.min.js")
        gsap_src = "gsap.min.js"
    else:
        die(f"GSAP がローカルにありません: {VENDOR_GSAP}\n"
            f"  → bash tools/setup_hyperframes.sh\n"
            f"  🔴 CDN は使いません。落ちたとき黙って動きが消えます（§7-4）。")

    (src / "index.html").write_text(
        build_html(timeline, style, theme, gsap_src), encoding="utf-8")
    info(f"書きました: {src / 'index.html'}")

    # ---- §7-3 焼く前に必ず lint ------------------------------------------
    info("lint を通します（レンダは数分、lint は数秒で確定した答えを返す・§7-3）")
    proc = subprocess.run(_npx("lint", str(src), "--json"),
                          capture_output=True, text=True)
    findings = []
    try:
        findings = json.loads(proc.stdout).get("findings", [])
    except Exception:  # noqa: BLE001
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
    errs = [f for f in findings if f.get("severity") in ("error", "fatal")]
    warns = [f for f in findings if f.get("severity") == "warning"]
    for f in errs + warns:
        print(f"  [{f.get('severity')}] {f.get('rule', '')}: {f.get('message', '')}")
    if errs:
        die(f"lint に error が {len(errs)} 件あります。レンダで試行錯誤しないでください（§7-3）。")
    info(f"lint 通過（warning {len(warns)} 件）")

    # ---- レンダ -----------------------------------------------------------
    raw = run_dir / "slides_raw.mp4"
    q = {"l": "draft", "m": "standard", "h": "high", "p": "high", "k": "high"}.get(quality, "high")
    env = dict(os.environ)
    try:
        run(_npx("render", str(src), "-o", str(raw), "--fps",
                 str(theme["canvas"]["fps"]), "--quality", q, "--strict"), env=env)
    except SystemExit:
        warn("🔴 レンダが失敗しました。page.goto で固まった場合は §7-6 を見てください。\n"
             "   HyperFrames は内部で localhost にサーバを立て、そこへブラウザを繋いで撮影します。\n"
             "   実行環境によっては、この接続が通らずタイムアウトします。\n"
             "   確認: 同じコマンドを**普通のターミナル**から実行してみてください。\n"
             "        そこで通るなら composition は正しく、実行している環境の制約です。\n"
             "   対処: レンダだけを別の立場のプロセス（launchd の LaunchAgent）に任せます。\n"
             "        キューはファイルの有無だけで管理し、request は最後に置きます。")
        raise

    if not raw.is_file():
        die("mp4 が出ていません。exit 0 でも成果物が無いことは実際に起きます。")

    # 出口の契約に正規化する（Manim エンジンと同じ形にそろえる）
    require_cmd("ffmpeg", "brew install ffmpeg")
    out = run_dir / "slides.mp4"
    run([
        "ffmpeg", "-y", "-i", str(raw),
        "-vf", f"scale={theme['canvas']['width']}:{theme['canvas']['height']}:"
               f"force_original_aspect_ratio=decrease,"
               f"pad={theme['canvas']['width']}:{theme['canvas']['height']}:(ow-iw)/2:(oh-ih)/2,"
               f"setsar=1,fps={theme['canvas']['fps']}",
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "medium", "-crf", "18", "-movflags", "+faststart",
        str(out),
    ])
    if not keep_intermediate:
        raw.unlink(missing_ok=True)

    p = probe(out)
    info(f"slides.mp4: {p['width']}x{p['height']} / {p['fps']}fps / {p['duration_sec']:.3f}秒")
    return out
