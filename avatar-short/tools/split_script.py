#!/usr/bin/env python3
"""§6-4 台本を検査し、生成単位（チャンク）に分ける。

🔴 1回の生成は 30.0秒が上限。
   実装側にも SamplingRequest.max_seconds = 30.0 として存在する（実物で確認済み）。
   193字を一括で渡すと予測31.4秒が30.000秒に切り詰められた。
   さらに一括は約17%早口になる（分割 5.50字/秒 に対し一括 6.43字/秒）。
   **壊れないので気づきにくい。**

ここで使う字/秒はあくまで「どこで割るか」を決めるための推定であり、
timeline.json の start/end には**使わない**。尺は④で実測してから埋める。

使い方:
    python3 tools/split_script.py --run runs/<slug>            # 検査して chunks の下書きを出す
    python3 tools/split_script.py --run runs/<slug> --strict   # 警告も不合格にする
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CONFIG_DIR, die, info, load_json, resolve_run, warn  # noqa: E402

SENTENCE_END = "。"
KANJI = re.compile(r"[一-鿿]")
# 文末に置いてはいけない記号（§6-4）
BANG = re.compile(r"[!！]")


def load_cfg() -> dict:
    return load_json(CONFIG_DIR / "voice.json")


def split_sentences(text: str) -> list[str]:
    """「。」で切る。「。」は残す。"""
    text = text.strip()
    if not text:
        return []
    parts = [s + SENTENCE_END for s in text.split(SENTENCE_END) if s.strip()]
    # 末尾に「。」が無かった場合の取りこぼしを戻す
    if not text.endswith(SENTENCE_END) and parts:
        parts[-1] = parts[-1][: -len(SENTENCE_END)]
    return parts


def lint_text(text: str, cfg: dict, *, where: str) -> list[dict]:
    """§6-4 のルールに機械で当てる。"""
    rules = cfg["script_rules"]
    issues: list[dict] = []

    if rules.get("forbid_sentence_final_bang") and BANG.search(text):
        issues.append({
            "level": "error", "where": where, "rule": "sentence_final_bang",
            "message": "「！」が入っています。文末「！」で声が 227Hz→526Hz"
                       "（ほぼ1オクターブ）跳ねる事故が報告されています。「。」にしてください。",
        })

    if rules.get("warn_all_hiragana"):
        min_chars = int(rules.get("min_chars_for_hiragana_warn", 12))
        for sent in split_sentences(text):
            body = sent.rstrip(SENTENCE_END)
            if len(body) >= min_chars and not KANJI.search(body):
                issues.append({
                    "level": "warn", "where": where, "rule": "all_hiragana",
                    "message": f"漢字が1文字もありません: {sent!r} / "
                               f"全ひらがなにすると助詞「は」が ha と読まれます。"
                               f"漢字仮名交じりのまま入れてください。",
                })

    if "\n\n" not in text and len(text) > 200:
        issues.append({
            "level": "warn", "where": where, "rule": "long_block",
            "message": "1つの段が長すぎます。段落・文単位で分けてください。",
        })
    return issues


def chunk_sentences(sentences: list[str], cfg: dict) -> list[str]:
    """推定尺が target_max_sec を超えない範囲で文をまとめる。

    文が1つで hard_cap を超える場合は、読点で割ることはせずエラーにする。
    （無音を差し込むのではなく読点で間を作るのが §6-4 の方針だが、
      機械が勝手に文を割ると意味の切れ目を壊すため、人に直させる）
    """
    ch = cfg["chunking"]
    cps = float(ch["chars_per_sec_estimate"])
    target = float(ch["target_max_sec"])
    hard = float(ch["hard_cap_sec"])

    chunks: list[str] = []
    cur = ""
    for s in sentences:
        est_alone = len(s) / cps
        if est_alone > hard:
            die(f"1文が単独で上限を超えます（推定 {est_alone:.1f}秒 > {hard}秒）: {s!r}\n"
                f"  → 文を分けてください。一括で渡すと 30.000秒に切り詰められ、"
                f"しかも壊れないので気づきません（§6-4）。")
        if cur and (len(cur) + len(s)) / cps > target:
            chunks.append(cur)
            cur = s
        else:
            cur += s
    if cur:
        chunks.append(cur)
    return chunks


def build_chunks(script: dict, cfg: dict) -> tuple[list[dict], list[dict]]:
    """script.json の segments から生成単位を作る。

    1 segment が長ければ複数チャンクに割れる。
    どのチャンクがどの segment に属するかは残す（④で実測尺を segment に配り直すため）。
    """
    issues: list[dict] = []
    chunks: list[dict] = []
    for seg in script.get("segments", []):
        seg_id = seg.get("id") or f"s{len(chunks) + 1}"
        text = (seg.get("narration") or "").strip()
        if not text:
            issues.append({"level": "error", "where": seg_id, "rule": "empty_narration",
                           "message": "narration が空です"})
            continue
        issues.extend(lint_text(text, cfg, where=seg_id))
        for i, body in enumerate(chunk_sentences(split_sentences(text), cfg)):
            chunks.append({
                "chunk_id": f"{seg_id}-{i}",
                "segment_id": seg_id,
                "text": body,
                "chars": len(body),
                "estimated_sec": round(len(body) / float(cfg["chunking"]["chars_per_sec_estimate"]), 2),
                "_estimated_sec_note": "分割判断のための推定。timeline の尺には使わない",
            })
    return chunks, issues


def main() -> int:
    ap = argparse.ArgumentParser(description="§6-4 台本を検査して生成単位に分ける")
    ap.add_argument("--run", required=True)
    ap.add_argument("--strict", action="store_true", help="warn も不合格にする")
    args = ap.parse_args()

    run_dir = resolve_run(args.run)
    cfg = load_cfg()
    script = load_json(run_dir / "script.json")
    chunks, issues = build_chunks(script, cfg)

    errors = [i for i in issues if i["level"] == "error"]
    warns = [i for i in issues if i["level"] == "warn"]
    for i in issues:
        print(f"[{i['level']}] {i['where']}: {i['message']}", flush=True)

    total_chars = sum(c["chars"] for c in chunks)
    gap = float(cfg["chunking"]["gap_sec"])
    total_est = sum(c["estimated_sec"] for c in chunks) + gap * max(0, len(chunks) - 1)

    target = script.get("target_sec")
    if target:
        cps = float(cfg["chunking"]["chars_per_sec_estimate"])
        budget = int(target * cps)
        if total_est > target * 1.15:
            warns.append({"level": "warn", "where": "script", "rule": "over_target",
                          "message": f"推定 {total_est:.1f}秒 が target_sec={target}秒 を超えています "
                                     f"（目安 {target}秒 ≒ {budget}字 に対し {total_chars}字）。"
                                     f"台本を削ってください。"})
            warn(warns[-1]["message"])
        elif total_est < target * 0.7:
            warns.append({"level": "warn", "where": "script", "rule": "under_target",
                          "message": f"推定 {total_est:.1f}秒 が target_sec={target}秒 に対して短すぎます "
                                     f"（目安 {budget}字 に対し {total_chars}字）。"})
            warn(warns[-1]["message"])

    print()
    info(f"チャンク {len(chunks)} 個 / 合計 {total_chars}字 / 推定 {total_est:.1f}秒"
         f"（チャンク間の無音 {gap}秒 × {max(0, len(chunks) - 1)} を含む）")
    info("推定は 5.15字/秒。実効は約5.0〜5.3字/秒（§6-4）。"
         "他社TTSの 6.0字/秒 を流用すると1割ずれます。")
    info("🔴 これは推定です。timeline.json の start/end は ④で実測してから埋めます。")
    for c in chunks:
        print(f"  {c['chunk_id']:<10} {c['chars']:>4}字  推定{c['estimated_sec']:>6.2f}秒  {c['text'][:34]}")

    if errors:
        print()
        die(f"error が {len(errors)} 件あります。台本を直してから ④に進んでください。")
    if args.strict and warns:
        die(f"--strict: warn が {len(warns)} 件あります。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
