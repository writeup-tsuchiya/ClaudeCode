#!/usr/bin/env python3
"""⑥slides の Manim エンジン（既定）。

出口は必ず runs/<slug>/slides.mp4（不透過・1080x1920・30fps）。
だから ⑦compose と ⑧verify は、どちらのエンジンで焼いても1行も変えなくてよい（§7-1）。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import REPO_ROOT, die, info, probe, require_cmd, run, warn  # noqa: E402

VENV = REPO_ROOT / ".venv-manim"


def available() -> bool:
    return (VENV / "bin" / "manim").is_file()


def setup_hint() -> str:
    return "bash tools/setup_manim.sh"


def render(run_dir: Path, style_path: Path, theme_path: Path, *,
           quality: str = "h", keep_intermediate: bool = False) -> Path:
    manim = VENV / "bin" / "manim"
    if not manim.is_file():
        die(f"Manim 専用環境がありません: {manim}\n  → {setup_hint()}")

    timeline_path = run_dir / "timeline.json"
    if not timeline_path.is_file():
        die(f"timeline.json がありません: {timeline_path}\n"
            f"  → ④voice を通してください（尺は実測してから埋めます）")

    media_dir = run_dir / "manim_media"
    if media_dir.exists():
        shutil.rmtree(media_dir)

    env = dict(os.environ)
    env["AVATAR_SHORT_TIMELINE"] = str(timeline_path)
    env["AVATAR_SHORT_STYLE"] = str(style_path)
    env["AVATAR_SHORT_THEME"] = str(theme_path)
    # 決定論のため、キャッシュ由来の差を消す（§7-4）
    env["PYTHONHASHSEED"] = "0"

    scene_file = REPO_ROOT / "tools" / "manim_scene.py"
    # 🔴 -q（品質プリセット）を渡さないこと。
    #    -qh は 1920x1080（横向き）を意味し、--resolution を上書きする。
    #    実際にそれで横向きに焼かれ、上下に黒帯が入った状態で
    #    「1080x1920 である」という契約だけは満たしてしまった。
    #    解像度と fps は --resolution / --fps で明示する。
    cmd = [
        str(manim), "render",
        "--resolution", "1080,1920",
        "--fps", "30",
        "--format", "mp4",
        "--media_dir", str(media_dir),
        "--disable_caching",
        "-o", "slides_raw",
        str(scene_file), "AvatarShortSlides",
    ]
    run(cmd, cwd=REPO_ROOT, env=env)

    produced = sorted(media_dir.rglob("slides_raw*.mp4"))
    if not produced:
        die("Manim が mp4 を出しませんでした。exit 0 でも成果物が無いことは実際に起きます。\n"
            f"  中身を見てください: {media_dir}")
    raw = produced[0]

    # 不透過・1080x1920・30fps・yuv420p に正規化する。
    # Manim の出力がすでにその形でも、後段の前提を固定するために必ず通す。
    require_cmd("ffmpeg", "brew install ffmpeg")
    out = run_dir / "slides.mp4"
    run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=c=black:s=1080x1920:r=30",
        "-i", str(raw),
        "-filter_complex",
        "[1:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[fg];"
        "[0:v][fg]overlay=shortest=1[v]",
        "-map", "[v]",
        "-an",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "18",
        "-movflags", "+faststart",
        str(out),
    ])

    if not keep_intermediate:
        shutil.rmtree(media_dir, ignore_errors=True)

    p = probe(out)
    info(f"slides.mp4: {p['width']}x{p['height']} / {p['fps']}fps / "
         f"{p['duration_sec']:.3f}秒 / 音声={'あり' if p['has_audio'] else 'なし'}")
    if (p["width"], p["height"]) != (1080, 1920):
        warn(f"1080x1920 になっていません: {p['width']}x{p['height']}")
    return out
