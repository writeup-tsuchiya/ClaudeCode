#!/usr/bin/env python3
"""1本ぶんの作業場 runs/<slug>/ を作る。

1本 = 1ディレクトリ。途中で失敗しても、その工程だけ再実行できる（§4-3）。

使い方:
    python3 tools/new_run.py --person sample-taro --slug sample-taro-01
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ASSETS_DIR, REPO_ROOT, RUNS_DIR, die, info, load_json, save_json  # noqa: E402

REQUIRED_ASSETS = {
    "photo.jpg": "顔写真1枚（正面・バストアップ・口を閉じたもの）",
    "voice_ref.wav": "本人の声 20〜30秒（§6-2 の手順で用意）",
    "intro.md": "自己紹介（箇条書きでも可）",
    "consent.json": "同意の記録（§11・これが無いと生成しない）",
}


def main() -> int:
    ap = argparse.ArgumentParser(description="runs/<slug>/ を作る")
    ap.add_argument("--person", required=True, help="assets/people/<person-id>")
    ap.add_argument("--slug", required=True, help="この1本の名前")
    ap.add_argument("--force", action="store_true", help="既存の run を作り直す")
    args = ap.parse_args()

    person_dir = ASSETS_DIR / "people" / args.person
    if not person_dir.is_dir():
        die(f"人物のディレクトリがありません: {person_dir}\n"
            f"  → mkdir -p {person_dir} して、次を置いてください:\n"
            + "\n".join(f"      {k:<16} {v}" for k, v in REQUIRED_ASSETS.items())
            + f"\n\n  intro.md と consent.json のひな形: "
              f"{(REPO_ROOT / 'samples' / 'person-template')}\n"
              f"    cp samples/person-template/*.md samples/person-template/*.json {person_dir}/\n"
              f"  🔴 assets/people/ は git 対象外です。他人の顔と声を履歴に残さないため（§5）。")

    missing = [k for k in REQUIRED_ASSETS if not (person_dir / k).is_file()]
    # photo は .png でも可
    if "photo.jpg" in missing and (person_dir / "photo.png").is_file():
        missing.remove("photo.jpg")
    if missing:
        die("素材が足りません:\n"
            + "\n".join(f"  - {k}: {REQUIRED_ASSETS[k]}" for k in missing))

    # 同意の記録は「あるか」だけでなく「中身が要件を満たすか」を見る
    consent = load_json(person_dir / "consent.json")
    for key in ("person_id", "granted_by", "granted_at", "scope"):
        if not consent.get(key):
            die(f"consent.json に {key} がありません（§11）: {person_dir / 'consent.json'}")
    scope = consent["scope"]
    for need, label in (("voice_clone", "声の複製"), ("face_animate", "顔を動かすこと")):
        if not scope.get(need):
            die(f"{label}の同意がありません（scope.{need} が false）。\n"
                f"  🔴 顔とは別に、声の同意が要ります。"
                f"「写真をもらった」は「声を複製してよい」ではありません（§11）。")

    run_dir = RUNS_DIR / args.slug
    if run_dir.exists():
        if not args.force:
            die(f"すでにあります: {run_dir}（作り直すなら --force）")
        shutil.rmtree(run_dir)

    for sub in ("", "voice", "slides_src", "verify_frames", "logs"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)

    save_json(run_dir / "run.json", {
        "slug": args.slug,
        "person_id": args.person,
        "assets_dir": str(person_dir.relative_to(person_dir.parents[2])),
        "stages": {k: "todo" for k in
                   ("brief", "script", "face", "voice", "avatar", "slides", "compose", "verify")},
    })

    info(f"作りました: {run_dir}")
    print(f"""
次にやること（1工程ずつ、実物を測ってから次へ）:

  ③ face    python3 tools/check_face.py   --run {run_dir}
  ④ voice   python3 tools/make_voice.py   --run {run_dir}
  ⑤ avatar  python3 tools/make_avatar.py  --run {run_dir}
  ⑥ slides  python3 tools/make_slides.py  --run {run_dir}
  ⑦ compose python3 tools/compose.py      --run {run_dir}
  ⑧ verify  python3 tools/verify_short.py --run {run_dir}

①brief と ②script はエージェントの仕事です。
{run_dir}/script.json を作ってから ④に進んでください。
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
