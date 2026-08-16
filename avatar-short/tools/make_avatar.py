#!/usr/bin/env python3
"""⑤avatar — ④で作った音声で、写真の口を動かす。

🔴 ④→⑤ の順序は絶対に変えない（§4-2）。
   先に声を作り、**その音声で口を動かす**。
   逆にするとアバター側の合成音声になり、本人の声でなくなる。
   → voice/out.wav が無ければ、このコマンドは実行を拒否する。

🔴 §8-2 のパッチが当たっていなければ実行しない。
   当たっていないと、mps を指定しても黙って CPU で走り、
   「GPUにしても速くならなかった」という誤った結論になる。

使い方:
    python3 tools/make_avatar.py --run runs/<slug>
    python3 tools/make_avatar.py --run runs/<slug> --background   # 切り離して実行（§10-5）
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    REPO_ROOT,
    VENDOR_DIR,
    audio_duration,
    die,
    info,
    load_json,
    probe,
    require_consent,
    resolve_run,
    run,
    warn,
)

SADTALKER = VENDOR_DIR / "SadTalker"
SADTALKER_VENV = REPO_ROOT / ".venv-sadtalker"


def main() -> int:
    ap = argparse.ArgumentParser(description="⑤avatar 写真の口を動かす")
    ap.add_argument("--run", required=True)
    ap.add_argument("--size", type=int, default=256, choices=[256, 512],
                    help="facerender の解像度")
    ap.add_argument("--pose-style", type=int, default=0)
    ap.add_argument("--batch-size", type=int, default=2)
    ap.add_argument("--still", action="store_true", default=True,
                    help="頭の動きを抑える（ワイプでは動きすぎると見づらい）")
    ap.add_argument("--no-still", dest="still", action="store_false")
    ap.add_argument("--preprocess", default="crop",
                    choices=["crop", "extcrop", "resize", "full", "extfull"])
    ap.add_argument("--device", default=None, help="mps / cuda / cpu（既定は自動判定）")
    ap.add_argument("--background", action="store_true",
                    help="切り離して実行する（§10-5・10分を超える処理向け）")
    args = ap.parse_args()

    run_dir = resolve_run(args.run)
    meta = load_json(run_dir / "run.json")
    require_consent(meta["person_id"], need="face_animate")

    # ---- ④が済んでいるか（順序の強制） --------------------------------
    voice_wav = run_dir / "voice" / "out.wav"
    if not voice_wav.is_file():
        die("④voice がまだです: " + str(voice_wav) + "\n"
            "  🔴 ④→⑤ の順序は絶対に変えないでください（§4-2）。\n"
            "     先に声を作り、その音声で口を動かします。\n"
            "     逆にするとアバター側の合成音声になり、本人の声でなくなります。\n"
            "  → python3 tools/make_voice.py --run " + str(run_dir))
    voice_sec = audio_duration(voice_wav)
    info(f"④の音声: {voice_sec:.3f}秒（実測）")

    # ---- ③が済んでいるか ----------------------------------------------
    face_json = run_dir / "face.json"
    if not face_json.is_file():
        die("③face がまだです。→ python3 tools/check_face.py --run " + str(run_dir))
    face = load_json(face_json)
    image = face.get("prepared_image")
    if not image or not Path(image).is_file():
        die("⑤へ渡す画像がありません。③face をやり直してください。")
    info(f"入力画像: {image} {face['prepared_size'][0]}x{face['prepared_size'][1]}")

    # ---- §8-2 パッチの確認（実行前に必ず） ------------------------------
    check = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "patch_sadtalker.py"), "--check"],
        capture_output=True, text=True)
    sys.stdout.write(check.stdout)
    if check.returncode != 0:
        sys.stderr.write(check.stderr)
        die("§8-2 のパッチが当たっていません。\n"
            "  このまま実行すると、mps を指定しても黙って CPU で走ります。\n"
            "  → python3 tools/patch_sadtalker.py --apply")

    venv_python = SADTALKER_VENV / "bin" / "python"
    if not venv_python.is_file():
        die(f"SadTalker 専用環境がありません: {venv_python}\n  → bash tools/setup_sadtalker.sh")

    result_dir = run_dir / "sadtalker_out"
    if result_dir.exists():
        shutil.rmtree(result_dir)
    result_dir.mkdir(parents=True)

    cmd = [
        str(venv_python), "-u", str(SADTALKER / "inference.py"),
        "--driven_audio", str(voice_wav),
        "--source_image", str(image),
        "--result_dir", str(result_dir),
        "--checkpoint_dir", str(SADTALKER / "checkpoints"),
        "--size", str(args.size),
        "--pose_style", str(args.pose_style),
        "--batch_size", str(args.batch_size),
        "--preprocess", args.preprocess,
    ]
    if args.still:
        cmd.append("--still")

    env = dict(os.environ)
    if args.device:
        env["SADTALKER_DEVICE"] = args.device
    # ⚠️ PYTORCH_ENABLE_MPS_FALLBACK=1 は効かない（Conv3D は明示的にエラーを投げるため
    #    fallback の対象外）。ここで設定しても無意味なので設定しない。

    est_min = voice_sec * 7.1 / 60.0
    info(f"実行します。Apple GPU で実時間の約7〜18倍かかります"
         f"（1024x1024 入力なら 7.1倍 ≒ 約{est_min:.1f}分の見込み）。")
    info("※ この倍率は要件定義書 §8-2/§8-3 の実測値です。"
         "自分のマシンで測り直したら wiki/ に記録してください。")

    if args.background:
        # §10-5 10分を超える処理は切り離す。完了判定はファイルの出現で行う。
        log_name = f"avatar-{run_dir.name}"
        run(["bash", str(REPO_ROOT / "tools" / "run_bg.sh"), log_name, *cmd])
        info(f"切り離して実行中です。完了は logs/{log_name}.exit の出現で判定してください。")
        info(f"  tail -f {REPO_ROOT / 'logs' / (log_name + '.log')}")
        info(f"完了後に: python3 tools/make_avatar.py --run {run_dir} --collect")
        return 0

    run(cmd, cwd=SADTALKER, env=env)
    return collect(run_dir, result_dir, voice_sec)


def collect(run_dir: Path, result_dir: Path, voice_sec: float) -> int:
    """SadTalker の出力から mp4 を拾い、実物を測る。"""
    mp4s = sorted(result_dir.rglob("*.mp4"), key=lambda p: p.stat().st_size, reverse=True)
    # ..._full.mp4（貼り戻し済み）があればそれを使う
    full = [p for p in mp4s if p.name.endswith("_full.mp4")]
    picked = (full or mp4s)[0] if mp4s else None
    if picked is None:
        die("mp4 が出ていません。exit 0 でも成果物が無いことは実際に起きます。\n"
            f"  中身を見てください: {result_dir}")

    dest = run_dir / "avatar.mp4"
    shutil.copy2(picked, dest)

    p = probe(dest)
    info(f"avatar.mp4: {p['width']}x{p['height']} / {p['fps']}fps / "
         f"{p['duration_sec']:.3f}秒 / 音声={'あり' if p['has_audio'] else 'なし'}")

    drift = abs((p["duration_sec"] or 0) - voice_sec)
    if drift > 1.0:
        warn(f"音声の尺 {voice_sec:.3f}秒 と動画の尺 {p['duration_sec']:.3f}秒 が "
             f"{drift:.3f}秒 ずれています（判定基準は1秒以内・§2）。")
    else:
        info(f"音声との尺の差 {drift:.3f}秒（判定基準 1秒以内・§2）")

    if not p["has_audio"]:
        warn("avatar.mp4 に音声がありません。⑦compose は音声をアバター動画から取ります。"
             "voice/out.wav を使うよう --audio-from voice を指定してください。")

    info("次: python3 tools/make_slides.py --run " + str(run_dir))
    return 0


if __name__ == "__main__":
    # --collect だけの呼び出しに対応する（--background の後始末）
    if "--collect" in sys.argv:
        sys.argv.remove("--collect")
        _ap = argparse.ArgumentParser()
        _ap.add_argument("--run", required=True)
        _known, _ = _ap.parse_known_args()
        _run_dir = resolve_run(_known.run)
        raise SystemExit(collect(_run_dir, _run_dir / "sadtalker_out",
                                 audio_duration(_run_dir / "voice" / "out.wav")))
    raise SystemExit(main())
