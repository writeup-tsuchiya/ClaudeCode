#!/usr/bin/env python3
"""④voice の実処理。**Irodori-TTS 専用環境の中で走る**（uv run から呼ばれる）。

このファイルを直接叩かないこと。入口は tools/make_voice.py。
理由: Irodori-TTS は torch 2.10 系を要求し、他の工程と共存できない（§6-3）。

ここでやること:
  1. モデルを1回だけ読む
  2. 🔴 参照音声のエンコードを1回だけやって .pt に落とし、以後は使い回す（§6-6）
     チャンクごとに毎回やると約1.7秒×チャンク数を捨てる（実測 18.98秒 → 13.06秒 / 31%短縮）
     出力は完全に同一。
  3. チャンクごとに合成し、**実測の尺**を記録する
  4. チャンク間に無音を挟んで1本に繋ぎ、out.wav を書く

入出力はすべて JSON ファイル経由（プロセス間の約束をファイルにしておくと、
どちらが落ちても状態が壊れない）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import torch

from irodori_tts.inference_runtime import (  # type: ignore[import-not-found]
    InferenceRuntime,
    RuntimeKey,
    SamplingRequest,
    default_runtime_device,
    download_hf_checkpoint,
    save_wav,
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description="④voice（Irodori-TTS 環境内で実行）")
    ap.add_argument("--job", required=True, help="make_voice.py が書いた job.json")
    args = ap.parse_args()

    job = json.loads(Path(args.job).read_text(encoding="utf-8"))
    out_dir = Path(job["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    ref_wav = Path(job["ref_wav"])
    cfg = job["config"]
    samp = cfg["sampling"]
    chunks = job["chunks"]

    model_device = cfg["device"]["model_device"]
    codec_device = cfg["device"]["codec_device"]
    if model_device == "auto":
        model_device = default_runtime_device()
    if codec_device == "auto":
        codec_device = default_runtime_device()
    print(f"[voice] model_device={model_device} codec_device={codec_device}", flush=True)

    checkpoint = download_hf_checkpoint(cfg["hf_checkpoint"])
    runtime = InferenceRuntime.from_key(
        RuntimeKey(
            checkpoint=checkpoint,
            model_device=model_device,
            codec_repo=cfg["codec_repo"],
            codec_device=codec_device,
        )
    )

    # ------------------------------------------------------------------
    # §6-6 参照音声のエンコードは1回だけ
    # ------------------------------------------------------------------
    ref_hash = sha256_file(ref_wav)
    latent_path = out_dir / "ref.latent.pt"
    meta_path = out_dir / "ref.latent.json"
    reuse = False
    if cfg["reference"]["cache_latent"] and latent_path.is_file() and meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        # 参照音声・コーデック・正規化のどれかが変わったら作り直す
        reuse = (
            meta.get("ref_sha256") == ref_hash
            and meta.get("codec_repo") == cfg["codec_repo"]
            and meta.get("normalize_db") == cfg["reference"]["normalize_db"]
        )

    if reuse:
        print(f"[voice] 参照音声のエンコード結果を再利用します: {latent_path}", flush=True)
    else:
        print("[voice] 参照音声を1回だけエンコードします（以後のチャンクで使い回す）", flush=True)
        wav, sr = _load_audio_compat(str(ref_wav))
        latent = runtime.codec.encode_waveform(
            wav.unsqueeze(0),
            sample_rate=int(sr),
            normalize_db=cfg["reference"]["normalize_db"],
            ensure_max=True,
        ).cpu()
        if latent.shape[1] == 0:
            print("[error] 参照音声から空の latent が出ました。参照音声が短すぎます。",
                  file=sys.stderr)
            return 2
        # _coerce_latent_shape が (T, D) を期待する
        torch.save(latent[0].contiguous(), latent_path)
        meta_path.write_text(json.dumps({
            "ref_sha256": ref_hash,
            "ref_wav": str(ref_wav),
            "codec_repo": cfg["codec_repo"],
            "normalize_db": cfg["reference"]["normalize_db"],
            "latent_steps": int(latent.shape[1]),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[voice] 書きました: {latent_path} (latent_steps={int(latent.shape[1])})", flush=True)

    # ------------------------------------------------------------------
    # チャンクごとに合成し、実測の尺を記録する
    # ------------------------------------------------------------------
    hard_cap = float(cfg["chunking"]["hard_cap_sec"])
    results = []
    sample_rate = None
    audios = []

    for i, ch in enumerate(chunks):
        req = SamplingRequest(
            text=ch["text"],
            ref_latent=str(latent_path),
            num_steps=int(samp["num_steps"]),
            seed=int(samp["seed"]),
            cfg_scale_text=float(samp["cfg_scale_text"]),
            cfg_scale_speaker=float(samp["cfg_scale_speaker"]),
            cfg_guidance_mode=str(samp["cfg_guidance_mode"]),
        )
        print(f"[voice] ({i + 1}/{len(chunks)}) {ch['chunk_id']} "
              f"{ch['chars']}字 : {ch['text'][:30]}", flush=True)
        res = runtime.synthesize(req)
        audio = res.audio.detach().to("cpu", dtype=torch.float32)
        if audio.ndim == 1:
            audio = audio.unsqueeze(0)
        sample_rate = int(res.sample_rate)
        measured = audio.shape[-1] / float(sample_rate)

        chunk_wav = out_dir / f"chunk_{ch['chunk_id']}.wav"
        save_wav(chunk_wav, audio, sample_rate)

        row = {
            "chunk_id": ch["chunk_id"],
            "segment_id": ch["segment_id"],
            "text": ch["text"],
            "chars": ch["chars"],
            "estimated_sec": ch["estimated_sec"],
            "measured_sec": round(measured, 3),
            "wav": str(chunk_wav),
            "used_seed": int(res.used_seed),
            "sample_rate": sample_rate,
            "chars_per_sec_measured": round(ch["chars"] / measured, 3) if measured else None,
            "truncated_at_cap": measured >= hard_cap - 0.05,
        }
        results.append(row)
        audios.append(audio)
        print(f"[voice]   実測 {measured:.3f}秒 / {row['chars_per_sec_measured']}字秒", flush=True)

        # 🔴 30秒に切り詰められても壊れないので気づかない。ここで必ず止める。
        if row["truncated_at_cap"]:
            print(f"[error] チャンク {ch['chunk_id']} が上限 {hard_cap}秒 に張り付きました "
                  f"（実測 {measured:.3f}秒）。切り詰められています。"
                  f"台本をもっと短い文に分けてください（§6-4）。", file=sys.stderr)
            _dump(out_dir, results, None, sample_rate, cfg)
            return 3

    # ------------------------------------------------------------------
    # 繋ぐ（チャンク間に無音）
    # ------------------------------------------------------------------
    gap_sec = float(cfg["chunking"]["gap_sec"])
    gap = torch.zeros((audios[0].shape[0], int(round(gap_sec * sample_rate))), dtype=torch.float32)
    pieces = []
    for i, a in enumerate(audios):
        if i:
            pieces.append(gap)
        pieces.append(a)
    joined = torch.cat(pieces, dim=-1)
    out_wav = out_dir / "out.wav"
    save_wav(out_wav, joined, sample_rate)
    total = joined.shape[-1] / float(sample_rate)
    print(f"[voice] 書きました: {out_wav} 実測 {total:.3f}秒", flush=True)

    _dump(out_dir, results, str(out_wav), sample_rate, cfg)
    return 0


def _dump(out_dir: Path, results: list[dict], out_wav: str | None,
          sample_rate: int | None, cfg: dict) -> None:
    (out_dir / "chunks.json").write_text(json.dumps({
        "out_wav": out_wav,
        "sample_rate": sample_rate,
        "gap_sec": cfg["chunking"]["gap_sec"],
        "measured": True,
        "_measured_note": "measured_sec は生成された波形のサンプル数から算出した実測値。推定ではない。",
        "chunks": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_audio_compat(path: str):
    """Irodori 内部の _load_audio と同じことを、公開APIだけでやる。"""
    try:
        import torchaudio

        wav, sr = torchaudio.load(path)
    except Exception:  # noqa: BLE001
        import soundfile as sf

        data, sr = sf.read(path, dtype="float32", always_2d=True)
        wav = torch.from_numpy(data.T)
    if wav.shape[0] > 1:  # モノラルに落とす
        wav = wav.mean(dim=0, keepdim=True)
    return wav, sr


if __name__ == "__main__":
    raise SystemExit(main())
