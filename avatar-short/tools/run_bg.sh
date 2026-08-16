#!/bin/bash
# §10-5 長時間の処理は、切り離して実行する。
#
# 10分を超える処理をバックグラウンドに投げると、途中で止められることがある。
#
#   ・python3 -u（バッファなし）で走らせる
#     付けないと途中で止まったときログが 0バイト になる
#   ・完了判定はプロセスの生死ではなく **ファイルの出現** で行う
#
# 使い方:
#   bash tools/run_bg.sh <ログ名> <コマンド...>
#   → logs/<ログ名>.log   に標準出力と標準エラー
#     logs/<ログ名>.exit  に EXIT=<終了コード>（これが出たら完了）

set -u

if [ $# -lt 2 ]; then
  echo "usage: bash tools/run_bg.sh <log-name> <command...>" >&2
  exit 2
fi

LOG_NAME="$1"; shift
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

LOG="$LOG_DIR/$LOG_NAME.log"
EXITFILE="$LOG_DIR/$LOG_NAME.exit"
SCRIPT="$LOG_DIR/$LOG_NAME.sh"

# 前回の完了印を消す（残っていると「もう終わっている」と誤読する）
rm -f "$EXITFILE"

# コマンドを安全に引用してスクリプトに落とす
{
  echo '#!/bin/bash'
  echo "cd $(printf '%q' "$ROOT")"
  printf 'PYTHONUNBUFFERED=1 '
  for a in "$@"; do printf '%q ' "$a"; done
  printf '> %q 2>&1\n' "$LOG"
  printf 'echo "EXIT=$?" > %q\n' "$EXITFILE"
} > "$SCRIPT"
chmod +x "$SCRIPT"

nohup "$SCRIPT" > /dev/null 2>&1 &
disown 2>/dev/null || true

echo "[run_bg] 切り離して実行しました"
echo "[run_bg]   ログ: $LOG"
echo "[run_bg]   完了印: $EXITFILE  ← このファイルの出現で完了を判定してください"
echo "[run_bg]   （プロセスの生死で判定しないこと）"
