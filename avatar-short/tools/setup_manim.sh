#!/bin/bash
# ⑥slides の既定エンジン（Manim）の環境を作る。
#
# 🔴 導入で1箇所だけ詰まる（§7-7）。
#    pycairo は macOS 向けの完成品を配布しておらず必ずソースから組み立てるので、
#    pkg-config が要る。
#
#      brew install pkgconf     # cairo は既に入っていることが多い。LaTeX は不要
#
#    ⚠️ macOS 以外（Linux 等）では manimpango もソースから組み立てになることがあり、
#       その場合は pango の開発ファイルが要る（例: apt install libpango1.0-dev）。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/.venv-manim"

if ! command -v pkg-config >/dev/null 2>&1 && ! command -v pkgconf >/dev/null 2>&1; then
  echo "pkg-config がありません → brew install pkgconf" >&2
  echo "  🔴 pycairo は macOS 向けの完成品が無く、必ずソースから組み立てます（§7-7）。" >&2
  exit 1
fi

PY="$(command -v python3)"
echo "[setup] Python: $PY ($("$PY" -V))"

if [ ! -x "$VENV/bin/python" ]; then
  echo "[setup] 専用環境を作ります: $VENV"
  "$PY" -m venv "$VENV"
fi

"$VENV/bin/python" -m pip install --quiet --upgrade pip
echo "[setup] manim を入れます（pycairo のビルドに数分かかることがあります）"
"$VENV/bin/python" -m pip install --quiet manim

echo
echo "[setup] 確認します"
"$VENV/bin/python" - <<'EOF'
import manim, manimpango
print("manim", manim.__version__, "/ manimpango", manimpango.__version__)

# 日本語が出せるフォントがあるか。無いと別の書体で焼き上がる（静かな劣化）。
want = ["Hiragino Sans", "Hiragino Kaku Gothic ProN", "Hiragino Mincho ProN",
        "YuMincho", "Noto Sans JP", "Noto Serif JP", "IPAGothic"]
installed = set(manimpango.list_fonts())
found = [w for w in want if w in installed]
print("使える日本語フォント:", ", ".join(found) if found else "見つかりません")
if not found:
    print("  ⚠️ config/telop_theme.json の font_stack にある実名が1つも入っていません。")
    print("     別の書体で焼き上がります。")

# §7-7 の訂正の確認: MarkupText は Pango 経由で日本語を折り返す
from manim import MarkupText
m = MarkupText("社内ツールの定着と、読まれるレポートの作り方。この二つはいつでも話せます。",
               font=(found[0] if found else "sans-serif"), font_size=48)
print(f"MarkupText 折り返しの確認: 幅={m.width:.2f} 高さ={m.height:.2f}")
EOF

echo
echo "[setup] 済みました。"
echo "  python3 tools/make_slides.py --run runs/<slug>"
