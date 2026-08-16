#!/usr/bin/env python3
"""§6-7 読み間違いを二度と直さない仕組み。

漢字の読み間違いは必ず起きる。その場で台本を書き換えて済ませると、
同じ発見を何度も繰り返す。④の直前にこの辞書を適用する。

🔴 status: approved にするのは、人が実音声を聴いて直ったと確かめたときだけ。
   推測で足した規則を自動で効かせると、誤った規則が静かに積み上がる。
   このコマンドは status != "approved" のエントリを**適用しない**。

使い方:
    python3 tools/apply_yomi.py --text "話者の紹介です。"
    python3 tools/apply_yomi.py --run runs/<slug>        # script.json に適用
    python3 tools/apply_yomi.py --list                   # 適用される規則を見る
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CONFIG_DIR, info, load_json, resolve_run, save_json, warn  # noqa: E402

DICT_PATH = CONFIG_DIR / "yomi_dict.json"


def approved_entries() -> list[dict]:
    data = load_json(DICT_PATH)
    out, skipped = [], []
    for e in data.get("entries", []):
        if e.get("status") == "approved":
            if not e.get("surface") or not e.get("replace"):
                warn(f"surface/replace が空のエントリを飛ばします: {e.get('id')}")
                continue
            out.append(e)
        else:
            skipped.append(e)
    if skipped:
        info(f"未承認のため適用しない規則が {len(skipped)} 件あります: "
             + ", ".join(f"{e.get('id')}({e.get('status')})" for e in skipped))
    # 長い surface から先に置換する（部分一致で短い規則に食われないように）
    out.sort(key=lambda e: len(e["surface"]), reverse=True)
    return out


def apply(text: str, entries: list[dict] | None = None) -> tuple[str, list[dict]]:
    """置換した文字列と、実際に効いた規則の一覧を返す。"""
    entries = approved_entries() if entries is None else entries
    hits = []
    for e in entries:
        n = text.count(e["surface"])
        if n:
            text = text.replace(e["surface"], e["replace"])
            hits.append({"id": e["id"], "surface": e["surface"],
                         "replace": e["replace"], "count": n})
    return text, hits


def main() -> int:
    ap = argparse.ArgumentParser(description="読み辞書を適用する（approved のみ）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--text", help="1行に適用して標準出力に出す")
    g.add_argument("--run", help="runs/<slug>/script.json の narration に適用する")
    g.add_argument("--list", action="store_true", help="適用される規則を見る")
    args = ap.parse_args()

    entries = approved_entries()

    if args.list:
        if not entries:
            print("適用される規則はありません（approved が0件）")
            return 0
        print(f"適用される規則 {len(entries)} 件:")
        for e in entries:
            print(f"  {e['id']:<14} {e['surface']} → {e['replace']}    理由: {e.get('reason', '')}")
            if not e.get("verified_wav"):
                warn(f"  {e['id']}: approved なのに verified_wav が空です。"
                     f"人が実音声を聴いて確かめた記録を残してください")
        return 0

    if args.text:
        out, hits = apply(args.text, entries)
        print(out)
        for h in hits:
            info(f"{h['id']}: {h['surface']} → {h['replace']} ({h['count']}箇所)")
        return 0

    run_dir = resolve_run(args.run)
    script_path = run_dir / "script.json"
    script = load_json(script_path)

    total = []
    for seg in script.get("segments", []):
        original = seg.get("narration", "")
        fixed, hits = apply(original, entries)
        if hits:
            seg["narration_source"] = seg.get("narration_source", original)
            seg["narration"] = fixed
            total.extend(hits)

    if not total:
        info("置換はありませんでした")
    else:
        for h in total:
            info(f"{h['id']}: {h['surface']} → {h['replace']} ({h['count']}箇所)")
    save_json(script_path, script)
    info(f"更新しました: {script_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
