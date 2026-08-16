#!/usr/bin/env python3
"""§8-2 SadTalker を Apple GPU (MPS) で動かすための改修。

そのまま入れると **CPU だけで動き、5.9倍遅くなる**。
3つとも「MPS が非対応」に見えるエラーを出すが、**Apple GPU 側の問題ではない**。

  # | 症状                            | 実際の原因                          | 対処
  --+---------------------------------+-------------------------------------+------------------------
  1 | GPUが使われない（遅いだけで動く） | inference.py の分岐に CUDA しか無い | 分岐に mps を足す      ← このコマンド
  2 | Conv3D is not supported on MPS  | 指定された torch(2.2.2) が未実装    | 新しい torch の別環境  ← setup_sadtalker.sh
  3 | invalid type: 'torch.mps....'   | 旧API .type(文字列) が MPS を知らない| 型文字列を dtype/device に翻訳 ← このコマンド

🔴 1番が当たっていないと、mps を指定しても黙って CPU で走る。
   「GPUにしても速くならなかった」という誤った結論になるので、
   make_avatar.py は実行前に必ず --check を通す。

⚠️ PYTORCH_ENABLE_MPS_FALLBACK=1 は効かない。
   Conv3D は明示的にエラーを投げるため fallback の対象外。

使い方:
    python3 tools/patch_sadtalker.py --apply
    python3 tools/patch_sadtalker.py --check     # 当たっているか（非ゼロで未適用）
    python3 tools/patch_sadtalker.py --revert
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import VENDOR_DIR, die, info, warn  # noqa: E402

SADTALKER = VENDOR_DIR / "SadTalker"
MARK = "# [avatar-short patch]"

# --------------------------------------------------------------------------
# パッチ1: inference.py の device 分岐に mps を足す
# --------------------------------------------------------------------------
P1_FIND = '''    if torch.cuda.is_available() and not args.cpu:
        args.device = "cuda"
    else:
        args.device = "cpu"'''

P1_REPLACE = f'''    {MARK} 1/3 device 分岐に mps を足す（要件定義書 §8-2）
    # 🔴 これが無いと、mps を指定しても黙って CPU で走る。
    #    「GPUにしても速くならなかった」という誤った結論になる。
    import os as _os
    _forced = _os.environ.get("SADTALKER_DEVICE", "").strip().lower()
    if _forced:
        args.device = _forced
    elif args.cpu:
        args.device = "cpu"
    elif torch.cuda.is_available():
        args.device = "cuda"
    elif getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        args.device = "mps"
    else:
        args.device = "cpu"
    print(f"[avatar-short] device={{args.device}}", flush=True)
    {MARK} end 1/3'''

# --------------------------------------------------------------------------
# パッチ3: 型文字列を dtype/device に翻訳する
#
# 原因の連鎖（実物で確認済み）:
#   dense_motion.py:36   make_coordinate_grid((d,h,w), type=kp_source['value'].type())
#   util.py:18           make_coordinate_grid(spatial_size, mean.type())
#   keypoint_detector.py make_coordinate_grid(shape[2:], heatmap.type())
#     Tensor.type() は **文字列** を返す。MPS では 'torch.mps.FloatTensor'。
#   util.py:40,56-58     torch.arange(w).type(type)
#     旧API .type(文字列) は 'torch.mps.*' を知らない → invalid type
# --------------------------------------------------------------------------
P3_HELPER = f'''

{MARK} 3/3 型文字列を dtype/device に翻訳する（要件定義書 §8-2）
# Tensor.type() は文字列を返す。MPS では 'torch.mps.FloatTensor' になり、
# 旧API .type(文字列) がこれを知らないので invalid type で落ちる。
# 「MPS が非対応」に見えるが、原因は API の世代が古いこと。
_AS_TENSOR_DTYPES = {{
    "FloatTensor": torch.float32,
    "DoubleTensor": torch.float64,
    "HalfTensor": torch.float16,
    "BFloat16Tensor": torch.bfloat16,
    "LongTensor": torch.int64,
    "IntTensor": torch.int32,
    "ShortTensor": torch.int16,
    "CharTensor": torch.int8,
    "ByteTensor": torch.uint8,
    "BoolTensor": torch.bool,
}}


def parse_tensor_type_string(spec):
    """'torch.mps.FloatTensor' -> ('mps', torch.float32)"""
    parts = str(spec).split(".")
    if len(parts) < 2 or parts[0] != "torch":
        raise ValueError(f"unsupported tensor type string: {{spec!r}}")
    name = parts[-1]
    device = "cpu" if len(parts) == 2 else parts[1]
    if name not in _AS_TENSOR_DTYPES:
        raise ValueError(f"unknown tensor class: {{spec!r}}")
    return device, _AS_TENSOR_DTYPES[name]


def as_type(tensor, spec):
    """.type(spec) の置き換え。文字列でも dtype でも受ける。"""
    if isinstance(spec, str):
        device, dtype = parse_tensor_type_string(spec)
        return tensor.to(device=device, dtype=dtype)
    return tensor.type(spec)
{MARK} end 3/3

'''

P3_SITES = [
    ("    x = torch.arange(w).type(type)\n    y = torch.arange(h).type(type)\n"
     "    z = torch.arange(d).type(type)",
     "    x = as_type(torch.arange(w), type)\n    y = as_type(torch.arange(h), type)\n"
     "    z = as_type(torch.arange(d), type)"),
    ("    x = torch.arange(w).type(type)\n    y = torch.arange(h).type(type)",
     "    x = as_type(torch.arange(w), type)\n    y = as_type(torch.arange(h), type)"),
]


def _backup(path: Path) -> Path:
    bak = path.with_suffix(path.suffix + ".orig")
    if not bak.exists():
        shutil.copy2(path, bak)
    return bak


def apply_patches() -> list[dict]:
    results = []

    # ---- 1/3 -------------------------------------------------------------
    inf = SADTALKER / "inference.py"
    if not inf.is_file():
        die(f"見つかりません: {inf}\n  → bash tools/setup_sadtalker.sh")
    text = inf.read_text(encoding="utf-8")
    if f"{MARK} 1/3" in text:
        results.append({"id": "1/3 device分岐にmpsを足す", "state": "適用済み（変更なし）"})
    elif P1_FIND in text:
        _backup(inf)
        inf.write_text(text.replace(P1_FIND, P1_REPLACE, 1), encoding="utf-8")
        results.append({"id": "1/3 device分岐にmpsを足す", "state": "適用しました"})
    else:
        results.append({"id": "1/3 device分岐にmpsを足す", "state": "❌ 当てる場所が見つかりません",
                        "detail": "SadTalker の版が想定と違います。inference.py の "
                                  "torch.cuda.is_available() の分岐を手で直してください。"})

    # ---- 3/3 -------------------------------------------------------------
    util = SADTALKER / "src" / "facerender" / "modules" / "util.py"
    if not util.is_file():
        die(f"見つかりません: {util}")
    text = util.read_text(encoding="utf-8")
    if f"{MARK} 3/3" in text:
        results.append({"id": "3/3 型文字列をdtype/deviceに翻訳", "state": "適用済み（変更なし）"})
    else:
        hits = 0
        new = text
        for find, repl in P3_SITES:
            if find in new:
                new = new.replace(find, repl)
                hits += 1
        if hits == 0:
            results.append({"id": "3/3 型文字列をdtype/deviceに翻訳",
                            "state": "❌ 当てる場所が見つかりません",
                            "detail": "util.py の make_coordinate_grid が想定と違います。"})
        else:
            _backup(util)
            # import 直後にヘルパーを差し込む
            lines = new.splitlines(keepends=True)
            insert_at = 0
            for i, line in enumerate(lines):
                if line.startswith("import ") or line.startswith("from "):
                    insert_at = i + 1
            lines.insert(insert_at, P3_HELPER)
            util.write_text("".join(lines), encoding="utf-8")
            results.append({"id": "3/3 型文字列をdtype/deviceに翻訳",
                            "state": f"適用しました（{hits}箇所）"})

    return results


def check_patches() -> list[dict]:
    rows = []
    inf = SADTALKER / "inference.py"
    util = SADTALKER / "src" / "facerender" / "modules" / "util.py"
    for label, path, mark in (
        ("1/3 device分岐にmpsを足す", inf, f"{MARK} 1/3"),
        ("3/3 型文字列をdtype/deviceに翻訳", util, f"{MARK} 3/3"),
    ):
        if not path.is_file():
            rows.append({"id": label, "ok": False, "detail": f"ファイルがありません: {path}"})
            continue
        ok = mark in path.read_text(encoding="utf-8")
        rows.append({"id": label, "ok": ok,
                     "detail": "" if ok else f"未適用: {path}"})
    return rows


def revert() -> None:
    for path in (SADTALKER / "inference.py",
                 SADTALKER / "src" / "facerender" / "modules" / "util.py"):
        bak = path.with_suffix(path.suffix + ".orig")
        if bak.is_file():
            shutil.copy2(bak, path)
            info(f"戻しました: {path}")
        else:
            warn(f"控えがありません: {bak}")


def main() -> int:
    ap = argparse.ArgumentParser(description="§8-2 SadTalker の MPS 改修")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--apply", action="store_true")
    g.add_argument("--check", action="store_true")
    g.add_argument("--revert", action="store_true")
    args = ap.parse_args()

    if not SADTALKER.is_dir():
        die(f"SadTalker がありません: {SADTALKER}\n  → bash tools/setup_sadtalker.sh")

    if args.revert:
        revert()
        return 0

    if args.apply:
        for r in apply_patches():
            print(f"  {r['state']:<24} {r['id']}")
            if r.get("detail"):
                print(f"    → {r['detail']}")
        print()
        info("パッチ2/3（Conv3D）はコードの改修ではありません。"
             "新しい torch の別環境を作ることで解決します → bash tools/setup_sadtalker.sh")
        print()

    rows = check_patches()
    ng = [r for r in rows if not r["ok"]]
    for r in rows:
        print(f"[{'OK' if r['ok'] else 'NG'}] {r['id']}"
              + (f"  {r['detail']}" if r["detail"] else ""))
    if ng:
        print()
        die("パッチが当たっていません。このまま実行すると、mps を指定しても "
            "黙って CPU で走ります（実時間の104.7倍 / 60秒の動画で1時間45分）。")
    info("すべて当たっています（Apple GPU で 17.8倍 / 60秒の動画で約18分）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
