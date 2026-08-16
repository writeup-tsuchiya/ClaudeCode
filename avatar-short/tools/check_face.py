#!/usr/bin/env python3
"""③face — 写真の検品と、⑤へ渡す画像の用意。

判定はここでする（測定は face_worker.py）:
  - 顔が1つだけ写っているか
  - 確度が十分か
  - 顔が画像の端で切れていないか
  - 顔が小さすぎないか

そして §8-3 の縮小をかける。

🔴 元がきれいだから大きいまま渡す、は誤り。
   最後の「元サイズへ貼り戻す」工程（cv2.seamlessClone を全フレームに実行）が
   画素数に比例し、全体の3分の2を占める。
   800x1200 で 17.8倍 → 1024x1024 で 7.1倍（28秒の動画が 3分19秒）。

使い方:
    python3 tools/check_face.py --run runs/<slug>
    python3 tools/check_face.py --run runs/<slug> --face-size 768
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    ASSETS_DIR,
    REPO_ROOT,
    VENDOR_DIR,
    die,
    info,
    load_json,
    require_consent,
    resolve_run,
    run,
    warn,
)

SADTALKER = VENDOR_DIR / "SadTalker"
SADTALKER_VENV = REPO_ROOT / ".venv-sadtalker"


def sadtalker_python() -> str:
    p = SADTALKER_VENV / "bin" / "python"
    if not p.is_file():
        die(f"SadTalker 専用環境がありません: {p}\n  → bash tools/setup_sadtalker.sh")
    return str(p)


def find_photo(person_dir: Path) -> Path:
    for name in ("photo.jpg", "photo.jpeg", "photo.png"):
        p = person_dir / name
        if p.is_file():
            return p
    die(f"顔写真がありません: {person_dir}/photo.jpg")


def main() -> int:
    ap = argparse.ArgumentParser(description="③face 写真の検品")
    ap.add_argument("--run", required=True)
    ap.add_argument("--face-size", type=int, default=1024,
                    help="⑤に渡す画像の1辺（既定 1024・§8-3）")
    ap.add_argument("--margin", type=float, default=0.6)
    ap.add_argument("--min-face-ratio", type=float, default=0.12,
                    help="顔の幅が画像の短辺に占める割合の下限")
    args = ap.parse_args()

    run_dir = resolve_run(args.run)
    meta = load_json(run_dir / "run.json")
    person_id = meta["person_id"]

    # §11 顔を動かすことの同意（声とは別）
    require_consent(person_id, need="face_animate")

    person_dir = ASSETS_DIR / "people" / person_id
    photo = find_photo(person_dir)

    out_json = run_dir / "face.json"
    out_image = run_dir / "face_prepared.png"

    env = dict(os.environ)
    env["PYTHONPATH"] = str(SADTALKER) + os.pathsep + env.get("PYTHONPATH", "")
    run([
        sadtalker_python(), "-u", str(REPO_ROOT / "tools" / "face_worker.py"),
        "--image", str(photo),
        "--out-json", str(out_json),
        "--out-image", str(out_image),
        "--face-size", str(args.face_size),
        "--margin", str(args.margin),
        "--weights-root", str(SADTALKER / "gfpgan" / "weights"),
    ], cwd=SADTALKER, env=env)

    if not out_json.is_file():
        die("face.json が出ていません。exit 0 でも成果物が無いことは実際に起きます。")
    face = load_json(out_json)

    # ---------------- 判定 ----------------
    n = face["face_count"]
    info(f"検出器: {face['detector']}")
    info(f"検出 {n} 件 / 画像 {face['image_size'][0]}x{face['image_size'][1]}")
    for f in face["faces"]:
        info(f"  確度 {f['confidence']:.3f}  bbox={f['bbox']}  "
             f"{f['width']:.0f}x{f['height']:.0f}px"
             + ("  ⚠️ 端で切れています" if f["touches_edge"] else ""))

    if n == 0:
        die("顔が検出できませんでした。正面・バストアップの写真を使ってください。")
    if n > 1:
        die(f"顔が {n} 件あります。1人だけが写っている写真にしてください。\n"
            f"  （複数写っていると、どれを動かすかを機械が決めることになります）")

    f = face["faces"][0]
    if f["confidence"] < 0.9:
        warn(f"確度が {f['confidence']:.3f} と低めです。写真を変えることを勧めます。")
    if f["touches_edge"]:
        warn("顔が画像の端に接しています。余白のある写真のほうが安定します。")

    short_side = min(face["image_size"])
    ratio = f["width"] / short_side
    if ratio < args.min_face_ratio:
        warn(f"顔が小さすぎます（短辺の {ratio * 100:.1f}%）。"
             f"バストアップで撮り直すと安定します。")

    if not face.get("prepared_image"):
        die("⑤へ渡す画像が作れませんでした。")

    info(f"⑤へ渡す画像: {face['prepared_image']} "
         f"{face['prepared_size'][0]}x{face['prepared_size'][1]} "
         f"（元の画素数の {face['pixel_ratio'] * 100:.1f}%）")
    info("🔴 ワイプに映る実寸より大きい解像度で計算した分は、丸ごと捨てています（§8-3）。"
         "貼り戻し工程が画素数に比例し、全体の3分の2を占めます。")
    info("次: python3 tools/make_voice.py --run " + str(run_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
