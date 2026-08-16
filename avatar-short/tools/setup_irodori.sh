#!/bin/bash
# ④voice の環境を作る。
#
# Irodori-TTS は torch 2.10 系を要求する（pyproject.toml: torch>=2.10.0 で確認済み）。
# 他の工程（⑤avatar は torch を新しめ・Python 3.10）と共存できないので、専用の環境を作る。
#
# 🔴 モデルのダウンロードは待ち時間が長い。段階0のうちに ④と⑤の両方を走らせておくと、
#    待ち時間が重ならない（§12）。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/vendor/Irodori-TTS"

command -v uv >/dev/null 2>&1 || { echo "uv がありません → brew install uv" >&2; exit 1; }

mkdir -p "$ROOT/vendor"

if [ -d "$DEST/.git" ]; then
  echo "[setup] すでにあります: $DEST"
else
  echo "[setup] 取得します: $DEST"
  git clone https://github.com/Aratako/Irodori-TTS.git "$DEST"
fi

cd "$DEST"
echo "[setup] 依存を入れます（数分かかります）"
uv sync

echo
echo "[setup] 確認します"
uv run python -c "
import torch
print('torch', torch.__version__)
mps = getattr(torch.backends, 'mps', None)
print('mps available:', bool(mps and mps.is_available()))
import irodori_tts.inference_runtime as r
print('default device:', r.default_runtime_device())
print('max_seconds (1回の生成の上限):', r.SamplingRequest.__dataclass_fields__['max_seconds'].default)
"

echo
echo "[setup] 済みました。"
echo "  チェックポイントは初回実行時に HuggingFace から取得されます:"
echo "    $(python3 -c "import json,sys;print(json.load(open('$ROOT/config/voice.json'))['hf_checkpoint'])")"
echo "  先に取っておくなら:"
echo "    cd $DEST && uv run python -c \"from irodori_tts.inference_runtime import download_hf_checkpoint as d; print(d('Aratako/Irodori-TTS-500M-v3'))\""
