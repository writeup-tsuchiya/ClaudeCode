#!/usr/bin/env python3
"""④voice — 本人の声を複製して台本を読ませる。

入口はこのコマンド。実処理は tools/voice_worker.py が
Irodori-TTS 専用環境（vendor/Irodori-TTS）の中で走る。
Irodori-TTS は torch 2.10 系を要求し、他の工程と共存できないため（§6-3）。

やること:
  script.json → §6-7 読み辞書 → §6-4 検査と分割 → 合成 → **実測尺で timeline.json を埋める**

🔴 timeline.json の start / end は、ここで測った値だけを入れる。
   台本の文字数から推測しない（§4-3）。

使い方:
    python3 tools/make_voice.py --run runs/<slug>
    python3 tools/make_voice.py --run runs/<slug> --dry-run   # 合成せず検査だけ
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import apply_yomi  # noqa: E402
import split_script  # noqa: E402
from common import (  # noqa: E402
    ASSETS_DIR,
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
    save_json,
    strip_comments,
    warn,
)

IRODORI_DIR = VENDOR_DIR / "Irodori-TTS"


def check_reference(ref_wav: Path, cfg: dict) -> dict:
    """§6-2 参照音声の検品。ここで品質が決まる。"""
    p = probe(ref_wav)
    want = cfg["reference"]
    issues = []
    if p["duration_sec"] is None:
        die(f"参照音声の尺が測れません: {ref_wav}")
    d = p["duration_sec"]
    if not (want["expect_sec_min"] - 3 <= d <= want["expect_sec_max"] + 8):
        issues.append(f"尺が {d:.1f}秒 です。"
                      f"{want['expect_sec_min']:.0f}〜{want['expect_sec_max']:.0f}秒 を推奨します（§6-2）")
    if p["channels"] and p["channels"] != want["expect_channels"]:
        issues.append(f"{p['channels']}ch です。モノラルを推奨します（§6-2）")
    if p["sample_rate"] and p["sample_rate"] < 24000:
        issues.append(f"{p['sample_rate']}Hz です。48kHz を推奨します（§6-2）")

    info(f"参照音声: {d:.2f}秒 / {p['sample_rate']}Hz / {p['channels']}ch")
    for i in issues:
        warn(i)
    warn("🔴 合成音声を参照に使わないでください。声の複製は参照の声を写します。"
         "機械の声を写せば機械の声になります（§6-2）。")
    return {"probe": p, "issues": issues}


def fill_timeline(run_dir: Path, script: dict, chunks_data: dict, person_id: str) -> Path:
    """chunks.json の実測尺から timeline.json を組み立てる。

    セグメントが複数チャンクに割れていた場合は、そのセグメントの
    最初のチャンクの開始から最後のチャンクの終了までを1区間にする。
    """
    gap = float(chunks_data["gap_sec"])
    rows = chunks_data["chunks"]

    cursor = 0.0
    spans: dict[str, list[float]] = {}
    for i, row in enumerate(rows):
        if i:
            cursor += gap
        start = cursor
        cursor += float(row["measured_sec"])
        end = cursor
        seg_id = row["segment_id"]
        if seg_id not in spans:
            spans[seg_id] = [start, end]
        else:
            spans[seg_id][1] = end

    segments = []
    for seg in script["segments"]:
        sid = seg["id"]
        if sid not in spans:
            die(f"セグメント {sid} に対応する音声がありません")
        start, end = spans[sid]
        out = {
            "id": sid,
            "start": round(start, 3),
            "end": round(end, 3),
            "narration": seg["narration"],
            "telop": seg.get("telop", {}),
        }
        if seg.get("diagram"):
            out["diagram"] = seg["diagram"]
        segments.append(out)

    total = audio_duration(chunks_data["out_wav"])
    timeline = {
        "person_id": person_id,
        "total_sec": round(total, 3),
        "measured": True,
        "_measured_note": "start / end / total_sec は voice/out.wav の実測値。"
                          "台本の文字数からの推測ではない（§4-3）。",
        "segments": segments,
    }

    # 実測どうしの突き合わせ。ずれていたら組み立てのバグ。
    drift = abs(segments[-1]["end"] - total)
    if drift > 0.15:
        warn(f"区間の合計と out.wav の尺が {drift:.3f}秒 ずれています。"
             f"（区間 {segments[-1]['end']:.3f}秒 / 実測 {total:.3f}秒）")

    path = save_json(run_dir / "timeline.json", timeline)
    info(f"書きました: {path}")
    info(f"総尺 {total:.3f}秒（実測）/ 区間 {len(segments)}個")
    for s in segments:
        info(f"  {s['id']}: {s['start']:.2f} → {s['end']:.2f} ({s['end'] - s['start']:.2f}秒)")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="④voice 本人の声で台本を読ませる")
    ap.add_argument("--run", required=True)
    ap.add_argument("--dry-run", action="store_true", help="合成せず、検査と分割だけ")
    ap.add_argument("--python", default=None,
                    help="Irodori 環境の python（既定: uv run --project vendor/Irodori-TTS）")
    args = ap.parse_args()

    run_dir = resolve_run(args.run)
    meta = load_json(run_dir / "run.json")
    person_id = meta["person_id"]

    # §11 声の同意（顔とは別に要る）
    require_consent(person_id, need="voice_clone")

    person_dir = ASSETS_DIR / "people" / person_id
    ref_wav = person_dir / "voice_ref.wav"
    if not ref_wav.is_file():
        die(f"参照音声がありません: {ref_wav}（§6-2 の手順で用意してください）")

    cfg = strip_comments(load_json(REPO_ROOT / "config" / "voice.json"))
    check_reference(ref_wav, cfg)

    # §6-7 読み辞書（approved のみ）
    script_path = run_dir / "script.json"
    script = load_json(script_path)
    entries = apply_yomi.approved_entries()
    changed = False
    for seg in script.get("segments", []):
        fixed, hits = apply_yomi.apply(seg.get("narration", ""), entries)
        if hits:
            seg.setdefault("narration_source", seg["narration"])
            seg["narration"] = fixed
            changed = True
            for h in hits:
                info(f"読み辞書 {h['id']}: {h['surface']} → {h['replace']} ({h['count']}箇所)")
    if changed:
        save_json(script_path, script)

    # §6-4 検査と分割
    chunks, issues = split_script.build_chunks(script, load_json(REPO_ROOT / "config" / "voice.json"))
    errors = [i for i in issues if i["level"] == "error"]
    for i in issues:
        print(f"[{i['level']}] {i['where']}: {i['message']}")
    if errors:
        die(f"台本に error が {len(errors)} 件あります。直してから実行してください（§6-4）。")
    info(f"チャンク {len(chunks)} 個 / 合計 {sum(c['chars'] for c in chunks)}字")

    out_dir = run_dir / "voice"
    job = {
        "out_dir": str(out_dir),
        "ref_wav": str(ref_wav),
        "config": cfg,
        "chunks": chunks,
    }
    job_path = save_json(run_dir / "voice" / "job.json", job)

    if args.dry_run:
        info("--dry-run: 合成はしません。job.json まで作りました。")
        return 0

    if not IRODORI_DIR.is_dir():
        die(f"Irodori-TTS がありません: {IRODORI_DIR}\n"
            f"  → bash tools/setup_irodori.sh")

    worker = REPO_ROOT / "tools" / "voice_worker.py"
    if args.python:
        cmd = [args.python, "-u", str(worker), "--job", str(job_path)]
    else:
        cmd = ["uv", "run", "--project", str(IRODORI_DIR),
               "python", "-u", str(worker), "--job", str(job_path)]

    env = dict(os.environ)
    env["PYTHONPATH"] = str(IRODORI_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    run(cmd, cwd=IRODORI_DIR, env=env)

    chunks_path = out_dir / "chunks.json"
    if not chunks_path.is_file():
        die("chunks.json が出ていません。exit 0 でも成果物が無いことは実際に起きます。")
    chunks_data = load_json(chunks_path)
    if not chunks_data.get("out_wav"):
        die("out.wav が出ていません。voice/chunks.json を見てください。")

    # 実物を測ってから次に進む
    p = probe(chunks_data["out_wav"])
    info(f"out.wav: {p['duration_sec']:.3f}秒 / {p['sample_rate']}Hz / {p['channels']}ch")

    fill_timeline(run_dir, script, chunks_data, person_id)

    # 実測の字/秒を出す。次回の台本づくりで使える資産になる（§10-7）。
    rows = chunks_data["chunks"]
    if rows:
        cps = sum(r["chars"] for r in rows) / sum(r["measured_sec"] for r in rows)
        info(f"実測 {cps:.2f}字/秒（config の推定値は "
             f"{cfg['chunking']['chars_per_sec_estimate']}字/秒）")

    info("次: python3 tools/make_avatar.py --run " + str(run_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
