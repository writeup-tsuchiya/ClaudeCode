#!/usr/bin/env python3
"""③の前に、§3 の前提環境を「測って」確認する。

🔴 exit 0 を完了と見なさない。このコマンドは測れたかどうかを報告し、
   足りないものがあれば非ゼロで終わる。推測値は一切書かない。

使い方:
    python3 tools/check_env.py
    python3 tools/check_env.py --json runs/<slug>/env.json
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import REPO_ROOT, save_json  # noqa: E402

OK, NG, WARN = "OK  ", "NG  ", "WARN"


def _out(cmd: list[str]) -> str | None:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    return (p.stdout or p.stderr).strip()


def check_machine() -> list[dict]:
    rows = []
    system = platform.system()
    machine = platform.machine()
    rows.append({
        "id": "os",
        "status": OK if (system == "Darwin" and machine == "arm64") else WARN,
        "want": "macOS (Apple Silicon / arm64)",
        "got": f"{system} {machine}",
        "note": "" if (system == "Darwin" and machine == "arm64") else
                "要件定義書 §3-1 の検証環境は macOS (Apple Silicon)。"
                "ここ以外では ⑤avatar の Apple GPU 改修（§8-2）が効かず、"
                "実行時間の実測値（17.8倍）も当てはまりません。",
    })

    mem = _out(["sysctl", "-n", "hw.memsize"])
    if mem and mem.isdigit():
        gb = int(mem) / (1024 ** 3)
        rows.append({"id": "memory", "status": OK if gb >= 15.5 else NG,
                     "want": "16GB 以上", "got": f"{gb:.1f}GB", "note": ""})
    else:
        rows.append({"id": "memory", "status": WARN, "want": "16GB 以上",
                     "got": "測れませんでした", "note": "sysctl hw.memsize が使えません"})

    free = shutil.disk_usage(REPO_ROOT).free / (1024 ** 3)
    rows.append({"id": "disk_free", "status": OK if free >= 20 else NG,
                 "want": "20GB 以上", "got": f"{free:.1f}GB",
                 "note": "AIモデルの重みが約2.5GB＋作業領域"})
    return rows


def check_tools() -> list[dict]:
    rows = []

    ff = _out(["ffmpeg", "-version"])
    rows.append({"id": "ffmpeg", "status": OK if ff else NG,
                 "want": "導入済み", "got": ff.splitlines()[0] if ff else "見つかりません",
                 "note": "" if ff else "brew install ffmpeg"})

    fp = _out(["ffprobe", "-version"])
    rows.append({"id": "ffprobe", "status": OK if fp else NG,
                 "want": "導入済み（§2 の判定は全部これで測る）",
                 "got": fp.splitlines()[0] if fp else "見つかりません",
                 "note": "" if fp else "brew install ffmpeg"})

    node = _out(["node", "--version"])
    rows.append({"id": "node", "status": OK if node else WARN,
                 "want": "導入済み", "got": node or "見つかりません",
                 "note": "⑥slides を HyperFrames エンジンで焼くときだけ必要"})

    py310 = None
    for cand in ("/opt/homebrew/opt/python@3.10/bin/python3.10", "python3.10"):
        got = _out([cand, "-V"])
        if got:
            py310 = f"{got} ({shutil.which(cand) or cand})"
            break
    rows.append({
        "id": "python3.10",
        "status": OK if py310 else NG,
        "want": "Python 3.10（⑤avatar 専用）",
        "got": py310 or "見つかりません",
        "note": "" if py310 else
                "brew install python@3.10 / 新しい Python では動きません。"
                "依存の中でいちばん狭い制約が scikit-image==0.19.3 で、"
                "arm64 macOS 向けのビルドが 3.10 用しか存在しません（§10-1）",
    })

    uv = _out(["uv", "--version"])
    rows.append({"id": "uv", "status": OK if uv else NG,
                 "want": "導入済み（④voice の環境構築）", "got": uv or "見つかりません",
                 "note": "" if uv else "brew install uv"})

    try:
        import numpy  # noqa: PLC0415

        numpy_ver = numpy.__version__
    except ImportError:
        numpy_ver = None
    rows.append({"id": "numpy", "status": OK if numpy_ver else NG,
                 "want": "導入済み（⑧verify の口の動きの測定に使う）",
                 "got": numpy_ver or "見つかりません",
                 "note": "" if numpy_ver else
                         "python3 -m pip install numpy / "
                         "無いと『口が動いているか』を測れません。"
                         "測っていないことと動いていないことは別なので、"
                         "verify.json には measured=false と記録されます"})

    pkgconf = _out(["pkg-config", "--version"]) or _out(["pkgconf", "--version"])
    rows.append({"id": "pkg-config", "status": OK if pkgconf else WARN,
                 "want": "導入済み（Manim エンジンの pycairo ビルド）",
                 "got": pkgconf or "見つかりません",
                 "note": "" if pkgconf else
                         "brew install pkgconf / pycairo は macOS 向けの完成品が無く、"
                         "必ずソースから組み立てるので pkg-config が要ります（§7-7）",
                 })
    return rows


def check_ffmpeg_filters() -> list[dict]:
    """§3-3 🔴 最初に必ず測ること — ffmpeg に文字を描く機能があるか。"""
    out = _out(["ffmpeg", "-hide_banner", "-filters"])
    if out is None:
        return [{"id": "ffmpeg_text_filters", "status": NG,
                 "want": "drawtext / subtitles / ass の有無を知る",
                 "got": "ffmpeg が無いので測れませんでした", "note": ""}]

    found = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] in ("drawtext", "subtitles", "ass"):
            found.append(parts[1])
    found = sorted(set(found))

    # 🔴 どちらでも合格。本書はどちらでも動く前提で設計してある。
    if found:
        note = ("drawtext も使えますが、本プロジェクトは使いません。"
                "文字組みはブラウザ(CSS)か Manim に任せます（§10-4）。")
    else:
        note = ("ffmpeg だけでは画面に文字を1文字も置けません。"
                "本プロジェクトはその前提で設計してあるので、このままで問題ありません。"
                "⑦compose が使う drawbox は別枠でほぼ必ず入っています。")

    drawbox = any(len(p.split()) >= 2 and p.split()[1] == "drawbox" for p in out.splitlines())
    rows = [{"id": "ffmpeg_text_filters", "status": OK,
             "want": "有無を知ること（どちらでも設計は成立する）",
             "got": ", ".join(found) if found else "なし", "note": note}]
    rows.append({"id": "ffmpeg_drawbox", "status": OK if drawbox else NG,
                 "want": "drawbox（⑦compose のワイプ枠線に使う）",
                 "got": "あり" if drawbox else "なし",
                 "note": "" if drawbox else "枠線が描けません。config/telop_theme.json の "
                                            "wipe.border_width を 0 にすれば回避できます"})
    return rows


def check_vendor() -> list[dict]:
    rows = []
    for key, path, setup in (
        ("Irodori-TTS", REPO_ROOT / "vendor" / "Irodori-TTS", "bash tools/setup_irodori.sh"),
        ("SadTalker", REPO_ROOT / "vendor" / "SadTalker", "bash tools/setup_sadtalker.sh"),
    ):
        exists = path.is_dir()
        rows.append({"id": f"vendor/{key}", "status": OK if exists else WARN,
                     "want": "取得済み", "got": "あり" if exists else "未取得",
                     "note": "" if exists else f"{setup} で取得します（§12: 待ち時間が長いので最初に開始）"})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="§3 前提環境を測る")
    ap.add_argument("--json", default=None, help="結果を JSON で書き出す")
    args = ap.parse_args()

    groups = [
        ("マシン (§3-1)", check_machine()),
        ("道具 (§3-2)", check_tools()),
        ("ffmpeg の文字描画 (§3-3)", check_ffmpeg_filters()),
        ("AIモデル本体", check_vendor()),
    ]

    all_rows = []
    for title, rows in groups:
        print(f"\n=== {title} ===")
        for r in rows:
            print(f"[{r['status']}] {r['id']:<22} want={r['want']}")
            print(f"{'':7}{'':22} got ={r['got']}")
            if r["note"]:
                for line in _wrap(r["note"], 78):
                    print(f"{'':7}{'':22}  → {line}")
        all_rows.extend(rows)

    ng = [r for r in all_rows if r["status"] == NG]
    warns = [r for r in all_rows if r["status"] == WARN]

    print("\n" + "=" * 74)
    print(f"測定完了: OK={len([r for r in all_rows if r['status'] == OK])} "
          f"WARN={len(warns)} NG={len(ng)}")
    if ng:
        print("NG があります。以下を用意してから次に進んでください:")
        for r in ng:
            print(f"  - {r['id']}: {r['note'] or r['want']}")

    if args.json:
        save_json(args.json, {"rows": all_rows,
                              "ng": [r["id"] for r in ng],
                              "warn": [r["id"] for r in warns]})
        print(f"\n書き出しました: {args.json}")

    return 1 if ng else 0


def _wrap(text: str, width: int) -> list[str]:
    lines, cur = [], ""
    for ch in text:
        cur += ch
        if len(cur) >= width:
            lines.append(cur)
            cur = ""
    if cur:
        lines.append(cur)
    return lines


if __name__ == "__main__":
    raise SystemExit(main())
