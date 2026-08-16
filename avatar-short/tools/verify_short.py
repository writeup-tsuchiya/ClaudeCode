#!/usr/bin/env python3
"""⑧verify — 尺・解像度・音量・口の動きを機械で測る。

🔴 検査もエージェントにやらせたくなるが、**測定はコマンドに寄せる**（§8-6）。

   実際に起きたこと: 検査担当のエージェントが3回続けて、判定を出す前に turn 上限で止まった。
   測定自体は毎回正しいのに、ffprobe → 音量測定 → 黒フレーム検出 → 口元の切り出し →
   フレーム抽出… という**配管に turn を使い切っていた**。

   上限を上げても、ターン配分を指示しても直らなかった。理由は単純で、
   **エージェントは自分の残りターン数を知らない。**
   「残り5ターンになったら切り上げろ」は、本人が知り得ない情報に依存した指示だった。

   直し方: 配管を1本のコマンドに移し、エージェントの仕事を「測定」から「判断」に変える。

⚠️ 🔴 このコマンドは合否で終了コードを変えない。
   「測れた／測れなかった」だけを終了コードにする。
   合否は判断であり、判断はエージェントの仕事。
   ここを混ぜると、不合格のたびに自動化が異常終了する。

使い方:
    python3 tools/verify_short.py --run runs/<slug>
    python3 tools/verify_short.py --run runs/<slug> --psnr-against runs/<slug>/final_2nd.mp4
    → runs/<slug>/verify.json ＋ runs/<slug>/verify_frames/*.png
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    REPO_ROOT,
    die,
    info,
    load_json,
    probe,
    require_cmd,
    resolve_run,
    strip_comments,
    warn,
)


# --------------------------------------------------------------------------
def _ff(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["ffmpeg", "-hide_banner", *args],
                          capture_output=True, text=True)


def measure_loudness(path: Path) -> dict:
    """EBU R128。測れなければ None を返す（推測しない）。"""
    p = _ff(["-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"])
    err = p.stderr
    out: dict = {"integrated_lufs": None, "true_peak_dbtp": None, "lra": None,
                 "measured": False}

    # 🔴 ebur128 は再生中の途中経過も同じ書式で出す。
    #    re.search（＝最初の一致）を使うと、冒頭の -70 LUFS を拾って
    #    「無音に近い」という誤った測定値になる（実際に踏んだ）。
    #    末尾の Summary 部分だけを見る。
    tail = err
    idx = err.rfind("Summary:")
    if idx >= 0:
        tail = err[idx:]

    def last(pattern: str) -> float | None:
        hits = re.findall(pattern, tail)
        return float(hits[-1]) if hits else None

    out["integrated_lufs"] = last(r"I:\s*(-?[\d.]+)\s*LUFS")
    out["true_peak_dbtp"] = last(r"Peak:\s*(-?[\d.]+)\s*dBFS")
    out["lra"] = last(r"LRA:\s*(-?[\d.]+)\s*LU")
    out["_source"] = "Summary" if idx >= 0 else "全出力の末尾（Summary が見つからず）"
    out["measured"] = out["integrated_lufs"] is not None
    if not out["measured"]:
        out["why"] = "ebur128 フィルタの出力を読めませんでした（このビルドに無い可能性）"
    return out


def measure_silence(path: Path, noise_db: float, min_dur: float) -> dict:
    """無音区間。§10-6 文の切れ目で口を閉じているのは正しい挙動なので除外する。"""
    p = _ff(["-i", str(path),
             "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}",
             "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start:\s*(-?[\d.]+)", p.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([\d.]+)", p.stderr)]
    spans = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None
        spans.append([round(max(0.0, s), 3), round(e, 3) if e is not None else None])
    return {"measured": True, "noise_db": noise_db, "min_duration_sec": min_dur,
            "spans": spans, "count": len(spans)}


def measure_mouth(avatar: Path, face: dict | None, cfg: dict,
                  silence: dict, engine: str) -> dict:
    """§10-6 口元だけを切り出して画素差を測る。

    🔴 顔全体の画素差で測らない。口しか動かない方式では差が出ず、
       正常なのに「静止」と誤判定する。
    """
    result: dict = {"measured": False, "engine": engine}
    try:
        import numpy as np
    except ImportError:
        result["why"] = ("numpy がありません。口の動きは**測っていません**。"
                         "（測っていないことと、動いていないことは別です）")
        return result

    p = probe(avatar)
    vw, vh, fps = p["width"], p["height"], p["fps"] or 30.0
    if not vw or not vh:
        result["why"] = "アバター動画の寸法が読めませんでした"
        return result

    roi = cfg["mouth"]["roi_in_face"]
    prepared = (face or {}).get("prepared_size")
    fip = (face or {}).get("face_in_prepared")

    if fip and prepared and [vw, vh] == list(prepared):
        # 貼り戻し済みの動画。face.json の座標がそのまま使える。
        fx1, fy1 = fip["x1"] * vw, fip["y1"] * vh
        fx2, fy2 = fip["x2"] * vw, fip["y2"] * vh
        basis = "face.json の顔位置（貼り戻し済みの動画）"
    else:
        # 切り抜きのみの動画。顔がほぼ全面を占める前提で口元を取る。
        fx1, fy1, fx2, fy2 = 0.0, 0.0, float(vw), float(vh)
        basis = ("顔が全面を占める前提（動画の寸法が prepared_size と違うため）"
                 f" video={vw}x{vh} prepared={prepared}")

    fw, fh = fx2 - fx1, fy2 - fy1
    x = int(round(fx1 + roi["x1"] * fw))
    y = int(round(fy1 + roi["y1"] * fh))
    w = max(8, int(round((roi["x2"] - roi["x1"]) * fw)) // 2 * 2)
    h = max(8, int(round((roi["y2"] - roi["y1"]) * fh)) // 2 * 2)
    x = max(0, min(vw - w, x))
    y = max(0, min(vh - h, y))

    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(avatar),
         "-vf", f"crop={w}:{h}:{x}:{y},format=gray", "-f", "rawvideo", "-"],
        capture_output=True)
    buf = proc.stdout
    n = len(buf) // (w * h)
    if n < 2:
        result["why"] = f"口元のフレームが取れませんでした（{n} フレーム）"
        return result

    frames = np.frombuffer(buf[: n * w * h], dtype=np.uint8).reshape(n, h, w).astype(np.int16)
    diffs = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))  # 長さ n-1

    # 無音区間のフレームを除外する
    times = (np.arange(len(diffs)) + 0.5) / fps
    keep = np.ones(len(diffs), dtype=bool)
    for s, e in silence["spans"]:
        e = e if e is not None else float(times[-1]) + 1.0
        keep &= ~((times >= s) & (times <= e))
    kept = diffs[keep]
    if kept.size < 2:
        kept = diffs
        excluded_note = "無音区間を除くとフレームが残らなかったので、全フレームで測った"
    else:
        excluded_note = ""

    th = cfg["mouth"]["thresholds"].get(engine, {})
    min_diff = th.get("min_mean_abs_diff")

    result.update({
        "measured": True,
        "roi": {"x": x, "y": y, "w": w, "h": h, "basis": basis},
        "frames_total": int(len(diffs)),
        "frames_used": int(kept.size),
        "frames_excluded_as_silence": int(len(diffs) - kept.size),
        "mean_abs_diff": round(float(kept.mean()), 4),
        "median_abs_diff": round(float(np.median(kept)), 4),
        "p90_abs_diff": round(float(np.percentile(kept, 90)), 4),
        "max_abs_diff": round(float(kept.max()), 4),
        "moving_frame_ratio": (round(float((kept >= min_diff).mean()), 4)
                               if min_diff is not None else None),
        "threshold_used": th or None,
        "_threshold_note": ("この方式（%s）用の閾値。動き方の違う方式にそのまま当てないこと（§10-6）"
                            % engine) if th else
                           ("方式 %s の閾値が未測定です。config/verify.json に実測値を入れてください"
                            % engine),
        "note": excluded_note,
    })
    return result


def export_frames(final: Path, timeline: dict, out_dir: Path, cfg: dict) -> dict:
    """§2-5 各場面のフレームを1枚ずつ出す。文字の重なりは機械では見えない。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.png"):
        old.unlink()
    ratio = float(cfg["frames"]["offset_ratio"])
    written = []
    for seg in timeline["segments"]:
        t = float(seg["start"]) + (float(seg["end"]) - float(seg["start"])) * ratio
        dest = out_dir / f"{seg['id']}_{t:07.3f}s.png"
        p = _ff(["-ss", f"{t:.3f}", "-i", str(final), "-frames:v", "1",
                 "-y", str(dest)])
        if dest.is_file() and dest.stat().st_size > 0:
            written.append({"segment": seg["id"], "t": round(t, 3),
                            "png": str(dest),
                            "telop": seg.get("telop", {})})
        else:
            written.append({"segment": seg["id"], "t": round(t, 3), "png": None,
                            "why": (p.stderr or "").strip()[-200:]})
    return {"measured": True, "count": len([w for w in written if w.get("png")]),
            "frames": written,
            "_note": "🔴 文字が重なっていないかは、この png を人が見て判断する。機械では測れない。"}


def measure_black(final: Path, cfg: dict) -> dict:
    p = _ff(["-i", str(final), "-vf", "blackdetect=d=0.2:pix_th=0.10",
             "-f", "null", "-"])
    spans = re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", p.stderr)
    return {"measured": True,
            "spans": [[float(a), float(b)] for a, b in spans],
            "count": len(spans)}


def measure_psnr(a: Path, b: Path) -> dict:
    """§2-6 同じ入力で2回焼いて 40dB 以上か。"""
    p = _ff(["-i", str(a), "-i", str(b), "-lavfi", "psnr", "-f", "null", "-"])
    m = re.search(r"average:([\d.]+|inf)", p.stderr)
    if not m:
        return {"measured": False, "why": "psnr の出力を読めませんでした"}
    val = m.group(1)
    return {"measured": True,
            "average_psnr_db": (float("inf") if val == "inf" else float(val)),
            "against": str(b)}


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description="⑧verify 機械で測る（合否は言わない・§8-6）")
    ap.add_argument("--run", required=True)
    ap.add_argument("--engine", default="sadtalker",
                    help="アバターの方式。口の動きの閾値の選択に使う（§10-6）")
    ap.add_argument("--psnr-against", default=None,
                    help="2回目に焼いた mp4。再現性の判定に使う（§2-6）")
    ap.add_argument("--skip-frames", action="store_true")
    args = ap.parse_args()

    require_cmd("ffmpeg", "brew install ffmpeg")
    require_cmd("ffprobe", "brew install ffmpeg")
    run_dir = resolve_run(args.run)
    cfg = strip_comments(load_json(REPO_ROOT / "config" / "verify.json"))

    final = run_dir / "final.mp4"
    avatar = run_dir / "avatar.mp4"
    voice = run_dir / "voice" / "out.wav"
    timeline_p = run_dir / "timeline.json"

    report: dict = {"run": str(run_dir), "engine": args.engine, "measurements": {}}
    M = report["measurements"]

    # ---- 1. 出力 ---------------------------------------------------------
    if not final.is_file():
        die(f"final.mp4 がありません: {final}\n  → python3 tools/compose.py --run {run_dir}")
    pf = probe(final)
    M["output"] = {
        "measured": True, "path": str(final), "size_bytes": pf["size_bytes"],
        "width": pf["width"], "height": pf["height"], "fps": pf["fps"],
        "duration_sec": pf["duration_sec"],
        "video_codec": pf["video_codec"], "audio_codec": pf["audio_codec"],
        "has_audio": pf["has_audio"],
        "expected": {"width": cfg["canvas"]["width"], "height": cfg["canvas"]["height"],
                     "fps": cfg["canvas"]["fps"]},
    }

    # ---- 2. 尺の一致 -----------------------------------------------------
    if voice.is_file():
        pv = probe(voice)
        drift = abs((pf["duration_sec"] or 0) - (pv["duration_sec"] or 0))
        M["duration"] = {"measured": True,
                         "video_sec": pf["duration_sec"],
                         "audio_sec": pv["duration_sec"],
                         "drift_sec": round(drift, 3),
                         "max_drift_sec": cfg["duration"]["max_drift_sec"]}
    else:
        M["duration"] = {"measured": False, "why": f"{voice} がありません"}

    if timeline_p.is_file():
        tl = load_json(timeline_p)
        M["timeline"] = {"measured": True,
                         "total_sec": tl.get("total_sec"),
                         "segments": len(tl.get("segments", [])),
                         "filled_from_measurement": bool(tl.get("measured"))}
    else:
        tl = None
        M["timeline"] = {"measured": False, "why": f"{timeline_p} がありません"}

    # ---- 3. 音量 ---------------------------------------------------------
    M["loudness"] = measure_loudness(final)
    M["loudness"]["target_lufs"] = cfg["loudness"]["target_lufs"]
    M["loudness"]["tolerance_lu"] = cfg["loudness"]["tolerance_lu"]

    # ---- 4. 口の動き -----------------------------------------------------
    if avatar.is_file() and voice.is_file():
        sil = measure_silence(voice, cfg["mouth"]["silence"]["noise_db"],
                              cfg["mouth"]["silence"]["min_duration_sec"])
        M["silence"] = sil
        face = load_json(run_dir / "face.json") if (run_dir / "face.json").is_file() else None
        M["mouth"] = measure_mouth(avatar, face, cfg, sil, args.engine)
    else:
        M["silence"] = {"measured": False, "why": "avatar.mp4 か voice/out.wav がありません"}
        M["mouth"] = {"measured": False, "why": "avatar.mp4 か voice/out.wav がありません"}

    # ---- 5. 黒フレーム ---------------------------------------------------
    M["black"] = measure_black(final, cfg)

    # ---- 6. 各場面のフレーム ---------------------------------------------
    if tl and not args.skip_frames:
        M["frames"] = export_frames(final, tl, run_dir / "verify_frames", cfg)
    else:
        M["frames"] = {"measured": False,
                       "why": "timeline.json がない、または --skip-frames"}

    # ---- 7. 再現性 -------------------------------------------------------
    if args.psnr_against:
        M["determinism"] = measure_psnr(final, Path(args.psnr_against))
        M["determinism"]["min_psnr_db"] = cfg["determinism"]["min_psnr_db"]
    else:
        M["determinism"] = {"measured": False,
                            "why": "--psnr-against が指定されていません。"
                                   "同じ入力で2回焼いて渡してください（§2-6）"}

    # ---- 書き出し --------------------------------------------------------
    out = run_dir / "verify.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 人が読む用の要約（合否は言わない）
    print()
    print("=" * 70)
    print("測定値（合否はここでは言いません。判断はエージェントの仕事・§8-6）")
    print("=" * 70)
    o = M["output"]
    print(f"  解像度      {o['width']}x{o['height']}  (期待 "
          f"{o['expected']['width']}x{o['expected']['height']})")
    print(f"  fps         {o['fps']}  (期待 {o['expected']['fps']})")
    print(f"  尺          {o['duration_sec']:.3f}秒")
    print(f"  音声        {'あり' if o['has_audio'] else 'なし'} ({o['audio_codec']})")
    d = M["duration"]
    if d["measured"]:
        print(f"  音との差    {d['drift_sec']:.3f}秒  (基準 {d['max_drift_sec']}秒以内)")
    ld = M["loudness"]
    if ld["measured"]:
        print(f"  音量        {ld['integrated_lufs']} LUFS  (目標 {ld['target_lufs']}±"
              f"{ld['tolerance_lu']} LU)")
    else:
        print(f"  音量        測れませんでした ({ld.get('why', '')})")
    mo = M["mouth"]
    if mo["measured"]:
        print(f"  口の動き    平均画素差 {mo['mean_abs_diff']}  "
              f"中央値 {mo['median_abs_diff']}  "
              f"動きフレーム率 {mo['moving_frame_ratio']}")
        print(f"              口元 ROI {mo['roi']['w']}x{mo['roi']['h']} "
              f"@({mo['roi']['x']},{mo['roi']['y']})  根拠: {mo['roi']['basis']}")
        print(f"              無音として除外 {mo['frames_excluded_as_silence']}/"
              f"{mo['frames_total']} フレーム")
    else:
        print(f"  口の動き    測っていません ({mo.get('why', '')})")
    print(f"  黒フレーム  {M['black']['count']} 区間")
    fr = M["frames"]
    if fr["measured"]:
        print(f"  場面フレーム {fr['count']} 枚 → {run_dir / 'verify_frames'}")
        print("              🔴 文字が重なっていないかは、この png を人が見て判断します")
    dt = M["determinism"]
    if dt["measured"]:
        print(f"  再現性      PSNR {dt['average_psnr_db']} dB  "
              f"(基準 {dt['min_psnr_db']} dB 以上)")
    print()
    info(f"書きました: {out}")
    info("エージェントはこの JSON を読んで合否を言ってください（測定は済んでいます）。")

    # 🔴 終了コードは「測れた／測れなかった」だけ。合否では変えない。
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
