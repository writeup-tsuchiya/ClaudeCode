#!/bin/bash
# ⑥slides の退避エンジン（HyperFrames）の準備。
#
# 🔴 GSAP はローカルに置いたファイルを読む（§7-4）。
#    CDN は落ちたとき黙って動きが消える。「動きが無い動画」が
#    エラーも出さずに焼き上がるので、いちばん気づきにくい壊れ方になる。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/vendor/gsap"

command -v npm >/dev/null 2>&1 || { echo "npm がありません → brew install node" >&2; exit 1; }

mkdir -p "$DEST"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "[setup] GSAP をローカルに置きます"
cd "$TMP"
npm install --silent --no-audit --no-fund gsap

SRC="$TMP/node_modules/gsap/dist/gsap.min.js"
[ -f "$SRC" ] || { echo "gsap.min.js が見つかりません: $SRC" >&2; exit 1; }
cp "$SRC" "$DEST/gsap.min.js"
node -e "console.log('[setup] gsap', require('$TMP/node_modules/gsap/package.json').version)"
echo "[setup] 置きました: $DEST/gsap.min.js"

echo
echo "[setup] HyperFrames CLI を確認します"
npx --yes hyperframes@0.7.107 doctor || true

echo
echo "[setup] 済みました。"
echo "  python3 tools/make_slides.py --run runs/<slug> --engine hyperframes"
echo
echo "  ⚠️ レンダが page.goto で固まる場合は要件定義書 §7-6 を見てください。"
echo "     HyperFrames は内部で localhost にサーバを立て、そこへブラウザを繋いで撮影します。"
echo "     まず 'lsof -nP -iTCP -sTCP:LISTEN' で相手が待ち受けているかを確かめること（§10-3）。"
echo "     居ないものに繋がらないのは当然で、それを環境の制約と読むと"
echo "     存在しない壁を記録に残すことになります。"
