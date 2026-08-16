#!/usr/bin/env python3
"""⑥slides — 資料の層を作る。

🔴 入口は1つ、エンジンは2つ（§7-1）。
   出口は必ず runs/<slug>/slides.mp4（不透過・1080x1920・30fps）。
   だから ⑦compose と ⑧verify は、どちらのエンジンで焼いても1行も変えなくてよい。

   同じ契約の実装を2つ持つのが設計の要点。片方が期待外れでも、
   後段（合成・検査）を触らずに切り替えられる。
   新しい道具を試すときは、戻り道を先に作ってから試す。

  エンジン       書くもの              得意                          立ち位置
  ------------- -------------------- ----------------------------- --------
  Manim（既定）  Python               文字・図・段階的な組み上げ      本線
  HyperFrames   HTML / CSS / GSAP    凝ったレイアウト・写真          退避

🔴 画面に出すのは要点テロップだけ。ナレーション逐語の字幕帯は出さない（2026-08-14 決定）。
   変えるなら .claude/agents/video-evaluator.md の must_pass 7 も同時に変えること。

使い方:
    python3 tools/make_slides.py --run runs/<slug>
    python3 tools/make_slides.py --run runs/<slug> --style night-tech
    python3 tools/make_slides.py --run runs/<slug> --engine hyperframes
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CONFIG_DIR, REPO_ROOT, die, info, load_json, resolve_run  # noqa: E402
from engines import hyperframes_engine, manim_engine  # noqa: E402

ENGINES = {"manim": manim_engine, "hyperframes": hyperframes_engine}


def list_styles() -> list[str]:
    return sorted(p.stem for p in (CONFIG_DIR / "styles").glob("*.json"))


def main() -> int:
    ap = argparse.ArgumentParser(description="⑥slides 資料の層を焼く")
    ap.add_argument("--run", required=True)
    ap.add_argument("--engine", default="manim", choices=sorted(ENGINES))
    ap.add_argument("--style", default="pop-maru")
    ap.add_argument("--quality", default="h", choices=["l", "m", "h", "p", "k"],
                    help="Manim の品質（h=1080p 相当）")
    ap.add_argument("--keep-intermediate", action="store_true")
    ap.add_argument("--list-styles", action="store_true")
    args = ap.parse_args()

    if args.list_styles:
        for s in list_styles():
            meta = load_json(CONFIG_DIR / "styles" / f"{s}.json")
            print(f"  {s:<16} {meta.get('description', '')}")
        return 0

    run_dir = resolve_run(args.run)
    style_path = CONFIG_DIR / "styles" / f"{args.style}.json"
    if not style_path.is_file():
        die(f"スタイルがありません: {args.style}\n  ある: {', '.join(list_styles())}")
    theme_path = CONFIG_DIR / "telop_theme.json"

    timeline_path = run_dir / "timeline.json"
    if not timeline_path.is_file():
        die(f"timeline.json がありません: {timeline_path}\n"
            f"  → ④voice を通してください。start/end は実測してから埋めます（§4-3）。")
    timeline = load_json(timeline_path)
    if not timeline.get("measured"):
        die("timeline.json が実測で埋まっていません（measured != true）。\n"
            "  台本の文字数から推測した尺で焼くと、音と絵がずれます（§4-3）。")

    engine = ENGINES[args.engine]
    if not engine.available():
        die(f"{args.engine} エンジンが使えません。\n  → {engine.setup_hint()}")

    info(f"エンジン: {args.engine}（既定は manim。退避は hyperframes・§7-1）")
    info(f"スタイル: {args.style}")
    info(f"段数: {len(timeline['segments'])} / 総尺 {timeline['total_sec']:.3f}秒（実測）")

    out = engine.render(run_dir, style_path, theme_path,
                        quality=args.quality,
                        keep_intermediate=args.keep_intermediate)

    # 出口の契約を守れているか（後段はここに依存している）
    from common import probe  # noqa: PLC0415

    p = probe(out)
    problems = []
    if (p["width"], p["height"]) != (1080, 1920):
        problems.append(f"解像度が {p['width']}x{p['height']} です（1080x1920 が契約）")
    if p["fps"] and abs(p["fps"] - 30) > 0.1:
        problems.append(f"fps が {p['fps']} です（30 が契約）")
    drift = abs((p["duration_sec"] or 0) - float(timeline["total_sec"]))
    if drift > 0.5:
        problems.append(f"尺が timeline と {drift:.3f}秒 ずれています")
    if problems:
        die("出口の契約を満たしていません:\n" + "\n".join(f"  - {x}" for x in problems))

    info(f"契約どおりです: {out} "
         f"({p['width']}x{p['height']} / {p['fps']}fps / {p['duration_sec']:.3f}秒)")
    info("次: python3 tools/compose.py --run " + str(run_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
