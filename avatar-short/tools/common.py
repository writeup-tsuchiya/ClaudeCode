#!/usr/bin/env python3
"""共通ユーティリティ。

要件定義書 §10-4b / §11 / §2 に対応する。

🔴 Python 組み込みの hash() をこのプロジェクトで使わないこと。
   文字列の hash() はプロセスごとに違う値を返す（PYTHONHASHSEED）。
   乱数を1行も使っていないのに焼くたびに結果が変わる、という
   いちばん原因を追いにくい形になる。振り分け・キャッシュキーは hashlib。
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"
ASSETS_DIR = REPO_ROOT / "assets"
RUNS_DIR = REPO_ROOT / "runs"
VENDOR_DIR = REPO_ROOT / "vendor"


# --------------------------------------------------------------------------
# 出力
# --------------------------------------------------------------------------

def info(msg: str) -> None:
    print(f"[info] {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"[warn] {msg}", flush=True)


def die(msg: str, code: int = 1) -> "NoReturn":  # type: ignore[valid-type]
    print(f"[error] {msg}", file=sys.stderr, flush=True)
    raise SystemExit(code)


# --------------------------------------------------------------------------
# JSON
# --------------------------------------------------------------------------

def load_json(path: str | Path) -> Any:
    p = Path(path)
    if not p.is_file():
        die(f"見つかりません: {p}")
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str | Path, obj: Any) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return p


def strip_comments(obj: Any) -> Any:
    """設定ファイルの "_comment" / "_xxx_note" キーを落とした写しを返す。"""
    if isinstance(obj, dict):
        return {
            k: strip_comments(v)
            for k, v in obj.items()
            if not (k.startswith("_") and (k == "_comment" or k.endswith("_note") or k.endswith("_comment")))
        }
    if isinstance(obj, list):
        return [strip_comments(v) for v in obj]
    return obj


# --------------------------------------------------------------------------
# 決定論的な振り分け（§10-4b）
# --------------------------------------------------------------------------

def stable_digest(*parts: str) -> int:
    """プロセスをまたいで一定の整数を返す。hash() の代わり。"""
    h = hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8"))
    return int.from_bytes(h.digest()[:8], "big")


def stable_index(key: str, length: int, *, salt: str = "") -> int:
    """key から [0, length) の添字を決定論的に得る。"""
    if length <= 0:
        raise ValueError("length must be positive")
    return stable_digest(salt, key) % length


def rotation(count: int, pool_len: int, *, key: str, salt: str = "") -> list[int]:
    """プールへの添字列を返す。隣が必ず異なり、プールを1周する。

    ⚠️ 独立したハッシュで段ごとに振り分けると「たまたま3段続けて同じ背景」が
    普通に起きる。プールの長さと互いに素な歩幅で回すと、
    隣が必ず異なることと、pool_len 段で一巡することが構造的に保証される。

    >>> rotation(6, 4, key="demo")            # doctest: +ELLIPSIS
    [...]
    >>> idx = rotation(12, 5, key="demo")
    >>> all(a != b for a, b in zip(idx, idx[1:]))
    True
    >>> sorted(set(idx[:5])) == [0, 1, 2, 3, 4]
    True
    """
    if pool_len <= 0:
        raise ValueError("pool_len must be positive")
    if pool_len == 1:
        return [0] * count

    steps = [s for s in range(1, pool_len) if math.gcd(s, pool_len) == 1]
    d = stable_digest(salt, key)
    start = d % pool_len
    step = steps[(d // pool_len) % len(steps)]
    return [(start + i * step) % pool_len for i in range(count)]


# --------------------------------------------------------------------------
# 外部コマンド
# --------------------------------------------------------------------------

def require_cmd(name: str, hint: str = "") -> str:
    path = shutil.which(name)
    if not path:
        die(f"コマンドが見つかりません: {name}" + (f"\n  → {hint}" if hint else ""))
    return path


def run(cmd: list[str], *, cwd: Path | None = None, check: bool = True,
        capture: bool = False, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    printable = " ".join(str(c) for c in cmd)
    info(f"$ {printable}")
    proc = subprocess.run(
        [str(c) for c in cmd],
        cwd=str(cwd) if cwd else None,
        check=False,
        text=True,
        capture_output=capture,
        env=env,
    )
    if check and proc.returncode != 0:
        if capture:
            sys.stderr.write(proc.stdout or "")
            sys.stderr.write(proc.stderr or "")
        die(f"コマンドが失敗しました (exit={proc.returncode}): {printable}")
    return proc


# --------------------------------------------------------------------------
# ffprobe（§2 の判定はすべてここを通す）
# --------------------------------------------------------------------------

def ffprobe_streams(path: str | Path) -> list[dict]:
    require_cmd("ffprobe", "brew install ffmpeg")
    proc = run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format",
         "-of", "json", str(path)],
        capture=True,
    )
    data = json.loads(proc.stdout)
    return data.get("streams", [])


def probe(path: str | Path) -> dict:
    """解像度・fps・尺・音声の有無をまとめて返す。測れない項目は None。"""
    p = Path(path)
    if not p.is_file():
        die(f"見つかりません: {p}")
    if p.stat().st_size == 0:
        die(f"中身が空です（exit 0 でも成果物が無いことは実際に起きる）: {p}")

    require_cmd("ffprobe", "brew install ffmpeg")
    proc = run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(p)],
        capture=True,
    )
    data = json.loads(proc.stdout)
    streams = data.get("streams", [])
    fmt = data.get("format", {})

    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)

    out: dict[str, Any] = {
        "path": str(p),
        "size_bytes": p.stat().st_size,
        "duration_sec": float(fmt["duration"]) if fmt.get("duration") else None,
        "has_video": v is not None,
        "has_audio": a is not None,
        "width": int(v["width"]) if v and v.get("width") else None,
        "height": int(v["height"]) if v and v.get("height") else None,
        "fps": None,
        "video_codec": v.get("codec_name") if v else None,
        "audio_codec": a.get("codec_name") if a else None,
        "sample_rate": int(a["sample_rate"]) if a and a.get("sample_rate") else None,
        "channels": int(a["channels"]) if a and a.get("channels") else None,
    }
    if v and v.get("r_frame_rate") and "/" in v["r_frame_rate"]:
        num, den = v["r_frame_rate"].split("/")
        out["fps"] = round(float(num) / float(den), 4) if float(den) else None
    return out


def audio_duration(path: str | Path) -> float:
    p = probe(path)
    if p["duration_sec"] is None:
        die(f"尺が測れませんでした: {path}")
    return float(p["duration_sec"])


# --------------------------------------------------------------------------
# run ディレクトリ
# --------------------------------------------------------------------------

def resolve_run(run_arg: str) -> Path:
    p = Path(run_arg)
    if not p.is_absolute() and not p.exists():
        cand = RUNS_DIR / run_arg
        if cand.exists():
            p = cand
    if not p.is_dir():
        die(f"run ディレクトリがありません: {run_arg}\n"
            f"  → python3 tools/new_run.py --person <person-id> --slug <slug> で作れます")
    return p.resolve()


# --------------------------------------------------------------------------
# 同意（§11）— 技術の問題とは別に、必ず守る
# --------------------------------------------------------------------------

REQUIRED_CONSENT_KEYS = ("person_id", "granted_by", "granted_at", "scope")


def require_consent(person_id: str, *, need: str) -> dict:
    """consent.json が無い person-id では生成しない。コード側で止める。

    need: "voice_clone" / "face_animate" / "publish" のいずれか。
    🔴 顔とは別に、声の同意が要る。「写真をもらった」は「声を複製してよい」ではない。
    """
    path = ASSETS_DIR / "people" / person_id / "consent.json"
    if not path.is_file():
        die(
            f"同意の記録がありません: {path}\n"
            f"  この person-id では生成しません（要件定義書 §11）。\n"
            f"  誰が・いつ・どこまでの公開を承諾したかを記録してから実行してください。"
        )
    consent = load_json(path)
    missing = [k for k in REQUIRED_CONSENT_KEYS if not consent.get(k)]
    if missing:
        die(f"consent.json に必須項目がありません: {', '.join(missing)} ({path})")
    if consent.get("person_id") != person_id:
        die(f"consent.json の person_id が一致しません: "
            f"{consent.get('person_id')!r} != {person_id!r}")

    scope = consent.get("scope") or {}
    if not scope.get(need):
        die(
            f"この操作の同意がありません: scope.{need} が false または未記載です ({path})\n"
            f"  → 顔の同意と声の同意は別です（§11）。"
        )
    return consent


if __name__ == "__main__":
    import doctest

    failed, attempted = doctest.testmod(verbose=False)
    print(f"doctest: {attempted - failed}/{attempted} passed")
    raise SystemExit(1 if failed else 0)
