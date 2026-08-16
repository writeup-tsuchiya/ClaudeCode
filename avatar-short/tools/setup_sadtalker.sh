#!/bin/bash
# ⑤avatar の環境を作る。
#
# §8-2 のパッチ 2/3（Conv3D is not supported on MPS）は**コードの改修ではない**。
# 指定された torch(2.2.2) が Conv3D の MPS 実装を持っていないだけで、
# 新しい torch では動く。だから「新しい torch の別環境」を作ることが対処になる。
#
# ⚠️ PYTORCH_ENABLE_MPS_FALLBACK=1 は効かない。
#    Conv3D は明示的にエラーを投げるため、fallback の対象外。
#
# §10-1 古いソフトには古い依存がある。新しくすれば安全、ではない:
#   ・Python は 3.10 でなければ動かない
#     依存の中でいちばん狭い制約が scikit-image==0.19.3 で、
#     arm64 macOS 向けのビルドが 3.10 用しか存在しない
#   ・setuptools を最新に上げてはいけない
#     バージョン81以降は pkg_resources を同梱しなくなり、古いライブラリが起動時に落ちる
#   ・torch は先に入れる
#     一部のパッケージはビルド時に torch を読み込むため、同時に入れると「torch が無い」で失敗する

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/vendor/SadTalker"
VENV="$ROOT/.venv-sadtalker"

# ---- Python 3.10 を探す --------------------------------------------------
PY310=""
for cand in /opt/homebrew/opt/python@3.10/bin/python3.10 python3.10; do
  if command -v "$cand" >/dev/null 2>&1; then PY310="$(command -v "$cand")"; break; fi
done
if [ -z "$PY310" ]; then
  echo "Python 3.10 がありません → brew install python@3.10" >&2
  echo "  🔴 新しい Python では動きません。scikit-image==0.19.3 の arm64 macOS 向けビルドが" >&2
  echo "     3.10 用しか存在しないためです（§10-1）。" >&2
  exit 1
fi
echo "[setup] Python: $PY310 ($("$PY310" -V))"

mkdir -p "$ROOT/vendor"

# ---- 本体 ---------------------------------------------------------------
if [ -d "$DEST/.git" ]; then
  echo "[setup] すでにあります: $DEST"
else
  echo "[setup] 取得します: $DEST"
  git clone https://github.com/OpenTalker/SadTalker.git "$DEST"
fi

# ---- 専用環境 -----------------------------------------------------------
if [ ! -x "$VENV/bin/python" ]; then
  echo "[setup] 専用環境を作ります: $VENV"
  "$PY310" -m venv "$VENV"
fi
PY="$VENV/bin/python"

echo "[setup] pip / setuptools（setuptools は 81 未満に固定・§10-1）"
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet "setuptools<81" wheel

echo "[setup] torch を**先に**入れます（§10-1）"
echo "        §8-2 パッチ2/3: 新しい torch でないと Conv3D が MPS で動きません"
"$PY" -m pip install --quiet "torch>=2.4" "torchvision>=0.19" "torchaudio>=2.4"

echo "[setup] 残りの依存"
# torch 系は入れ終わっているので requirements から外す
grep -viE '^(torch|torchvision|torchaudio)([=<>~!]|$)' "$DEST/requirements.txt" > /tmp/sadtalker-req.txt
"$PY" -m pip install --quiet -r /tmp/sadtalker-req.txt

# ---- basicsr の既知の破損 -------------------------------------------------
# ⚠️ これは要件定義書には無い項目。実在の依存の問題なのでここで直す。
#    basicsr==1.4.2 は torchvision.transforms.functional_tensor を読むが、
#    torchvision 0.17 以降でこのモジュールが消えた。import 時に必ず落ちる。
BASICSR_DEG="$("$PY" -c "import basicsr,os;print(os.path.join(os.path.dirname(basicsr.__file__),'data','degradations.py'))" 2>/dev/null || true)"
if [ -n "$BASICSR_DEG" ] && [ -f "$BASICSR_DEG" ] && grep -q "functional_tensor" "$BASICSR_DEG"; then
  echo "[setup] basicsr の import を直します（torchvision 0.17+ で functional_tensor が消えたため）"
  cp -n "$BASICSR_DEG" "$BASICSR_DEG.orig"
  sed -i.bak 's/from torchvision\.transforms\.functional_tensor import rgb_to_grayscale/from torchvision.transforms.functional import rgb_to_grayscale/' "$BASICSR_DEG"
fi

# ---- モデルの重み（約2.5GB・§12 待ち時間が長いので最初に） ------------------
cd "$DEST"
echo "[setup] モデルの重みを取得します（約2.5GB・時間がかかります）"
if command -v wget >/dev/null 2>&1; then
  bash scripts/download_models.sh
else
  echo "wget がありません → brew install wget" >&2
  exit 1
fi

# ---- §8-2 のパッチを当てる -------------------------------------------------
echo
echo "[setup] §8-2 のパッチを当てます"
cd "$ROOT"
python3 tools/patch_sadtalker.py --apply

# ---- 確認 -----------------------------------------------------------------
echo
echo "[setup] 確認します"
"$PY" - <<'EOF'
import torch
print("torch", torch.__version__)
mps = getattr(torch.backends, "mps", None)
avail = bool(mps and mps.is_available())
print("mps available:", avail)
if avail:
    # §8-2 パッチ2/3 が効いているか＝この torch で Conv3D が MPS で動くか
    try:
        m = torch.nn.Conv3d(2, 2, 3, padding=1).to("mps")
        y = m(torch.randn(1, 2, 4, 8, 8, device="mps"))
        print("Conv3d on MPS: OK", tuple(y.shape))
    except Exception as e:
        print("Conv3d on MPS: NG", e)
        print("  → torch をもっと新しくしてください（§8-2 パッチ2/3）")
else:
    print("  Apple GPU が使えません。CPU だと実時間の104.7倍（60秒の動画で1時間45分）です。")
EOF

echo
echo "[setup] 済みました。"
echo "  次: python3 tools/check_face.py --run runs/<slug>"
