#!/usr/bin/env python3
"""⑦compose — スライド＋アバターのワイプ＋音声を1本にする。

    背景（スライド動画）
      ＋ 右上に四角のワイプ（アバターを正方形に切って縮小・枠線つき）
      ＋ 音声（アバター動画から取る。別に足すと二重になる）
      → 1080x1920 / 30fps / H.264 + AAC

・ワイプの位置とサイズは引数で変えられる（必ず調整が入る）
・枠線は drawbox で描く。drawtext と違い、たいていのビルドに入っている
  （このプロジェクトは ffmpeg に文字を描かせない。文字組みは⑥の仕事・§10-4）

使い方:
    python3 tools/compose.py --run runs/<slug>
    python3 tools/compose.py --run runs/<slug> --wipe-size 360 --wipe-margin-x 40
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    REPO_ROOT,
    audio_duration,
    die,
    info,
    load_json,
    probe,
    require_cmd,
    resolve_run,
    run,
    strip_comments,
    warn,
)


def hex_to_ff(color: str) -> str:
    """#RRGGBB → ffmpeg の色指定。"""
    c = color.strip()
    return "0x" + c[1:] if c.startswith("#") else c


def main() -> int:
    theme = strip_comments(load_json(REPO_ROOT / "config" / "telop_theme.json"))
    w_def = theme["wipe"]

    ap = argparse.ArgumentParser(description="⑦compose 1本にまとめる")
    ap.add_argument("--run", required=True)
    ap.add_argument("--wipe-size", type=int, default=w_def["size"])
    ap.add_argument("--wipe-margin-x", type=int, default=w_def["margin_x"])
    ap.add_argument("--wipe-margin-y", type=int, default=w_def["margin_y"])
    ap.add_argument("--wipe-border", type=int, default=w_def["border_width"])
    ap.add_argument("--wipe-border-color", default=w_def["border_color"])
    ap.add_argument("--corner", default=w_def["corner"],
                    choices=["top_right", "top_left", "bottom_right", "bottom_left"])
    ap.add_argument("--audio-from", default="avatar", choices=["avatar", "voice"],
                    help="音声をどちらから取るか。既定はアバター動画（二重になるのを防ぐ）")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    require_cmd("ffmpeg", "brew install ffmpeg")
    run_dir = resolve_run(args.run)

    slides = run_dir / "slides.mp4"
    avatar = run_dir / "avatar.mp4"
    voice = run_dir / "voice" / "out.wav"
    out = Path(args.out) if args.out else run_dir / "final.mp4"

    for p, stage in ((slides, "⑥slides"), (avatar, "⑤avatar")):
        if not p.is_file():
            die(f"{stage} がまだです: {p}")

    ps, pa = probe(slides), probe(avatar)
    info(f"slides.mp4: {ps['width']}x{ps['height']} / {ps['fps']}fps / {ps['duration_sec']:.3f}秒")
    info(f"avatar.mp4: {pa['width']}x{pa['height']} / {pa['fps']}fps / {pa['duration_sec']:.3f}秒 "
         f"/ 音声={'あり' if pa['has_audio'] else 'なし'}")

    canvas_w = theme["canvas"]["width"]
    canvas_h = theme["canvas"]["height"]
    fps = theme["canvas"]["fps"]

    if (ps["width"], ps["height"]) != (canvas_w, canvas_h):
        warn(f"slides.mp4 が {ps['width']}x{ps['height']} です。"
             f"{canvas_w}x{canvas_h} に引き伸ばします。")

    # 音声の出どころ
    audio_source = args.audio_from
    if audio_source == "avatar" and not pa["has_audio"]:
        warn("avatar.mp4 に音声がありません。voice/out.wav から取ります。")
        audio_source = "voice"
    if audio_source == "voice" and not voice.is_file():
        die(f"音声がありません: {voice}")
    info(f"音声の出どころ: {audio_source}"
         + ("（別に足すと二重になるので、片方だけを使う）" if audio_source == "avatar" else ""))

    size = args.wipe_size
    bw = args.wipe_border
    mx, my = args.wipe_margin_x, args.wipe_margin_y
    if args.corner.endswith("right"):
        x_expr = f"{canvas_w - size - mx}"
    else:
        x_expr = f"{mx}"
    if args.corner.startswith("top"):
        y_expr = f"{my}"
    else:
        y_expr = f"{canvas_h - size - my}"

    # ワイプ: 正方形に切って縮小し、枠線を描く
    wipe_chain = (
        f"[1:v]crop='min(iw,ih)':'min(iw,ih)',"
        f"scale={size}:{size}:flags=lanczos,"
        f"setsar=1,fps={fps}"
    )
    if bw > 0:
        wipe_chain += (f",drawbox=x=0:y=0:w={size}:h={size}:"
                       f"color={hex_to_ff(args.wipe_border_color)}@1.0:t={bw}")
    wipe_chain += "[wipe]"

    bg_chain = (f"[0:v]scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=increase,"
                f"crop={canvas_w}:{canvas_h},setsar=1,fps={fps}[bg]")

    filter_complex = (
        f"{bg_chain};{wipe_chain};"
        f"[bg][wipe]overlay=x={x_expr}:y={y_expr}:shortest=0[v]"
    )

    cmd = ["ffmpeg", "-y", "-i", str(slides), "-i", str(avatar)]
    if audio_source == "voice":
        cmd += ["-i", str(voice)]
        a_map = "2:a:0"
    else:
        a_map = "1:a:0"

    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", a_map,
        "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-preset", "medium", "-crf", "18",
        "-r", str(fps),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        "-shortest",
        str(out),
    ]
    run(cmd)

    if not out.is_file() or out.stat().st_size == 0:
        die("final.mp4 が出ていません。exit 0 でも成果物が無いことは実際に起きます。")

    # ---- 実物を測ってから報告する ----
    pf = probe(out)
    info(f"final.mp4: {pf['width']}x{pf['height']} / {pf['fps']}fps / "
         f"{pf['duration_sec']:.3f}秒 / 音声={'あり' if pf['has_audio'] else 'なし'} "
         f"({pf['video_codec']} + {pf['audio_codec']})")

    voice_sec = audio_duration(voice) if voice.is_file() else None
    if voice_sec is not None:
        drift = abs(pf["duration_sec"] - voice_sec)
        info(f"台本音声との尺の差 {drift:.3f}秒（判定基準 1秒以内・§2）"
             + ("" if drift <= 1.0 else "  ⚠️ 基準を超えています"))

    info("次: python3 tools/verify_short.py --run " + str(run_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
