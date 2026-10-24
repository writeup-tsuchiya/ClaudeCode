# Google Meet 商談ログ集約：運用手順（2026-10-25 改訂）

第一営業部7名の商談（Google Meet）で Gemini in Meet が自動生成する「Gemini によるメモ」（メモ＋文字起こし）を、
土屋所有の集約フォルダ「Google Meet」内の Googleドキュメント `商談文字起こし集約（全期間）_N` に連結して蓄積する。
下流の「ヒロシ商談フォロー」バッチがこのドキュメントを全文検索して商談ログURLを引いている。

## 1. なぜ作り直したか（2026-10 の障害）

- 2026-09 以降、Gemini in Meet がメモの保存先を「マイドライブ/Meet Recordings（1フォルダに平置き）」から
  「マイドライブ/Google Meet/<商談名>/」に変え、さらに「Google Meet」フォルダ自体を作り直すことがある。
- 以前共有してもらったフォルダには新しいメモが入らなくなり、ボット（ai.firststep2026 とサービスアカウント）から見えなくなった。
  見えないので「新規0件」と判定され、毎朝「追加0件・正常」で終わっていた。
- 結果、浦島・村田・菅野は 9/10 以降、土屋は 9/10 以降、吉川は 10/5 以降の商談ログが集約されていなかった（約1.5か月）。
- 対策として **発見をカレンダー起点に変更**（Drive のフォルダ構造に依存しない）し、**読めなかった商談を必ず報告する**ようにした。

## 2. 構成

```
scripts/meet_archive/archive.py        実行本体（Drive API を直接叩く。AIのトークンをほぼ使わない）
scripts/meet_archive/requirements.txt  依存ライブラリ
config/meet-collector.json             設定（対象メンバー・集約先・チャンク上限・認証の環境変数名）
config/meet-archive-manifest.json      状態（処理済みID・チャンク一覧・プレースホルダ・直近実行結果 lastRun）
config/team.json                       exclude.* を社内MTG除外に流用
.claude/skills/meet-collector/SKILL.md 日次ルーティンがこの手順で動く
docs/meet-archive-ops.md               本書
```

鍵（サービスアカウントの JSON）は **リポジトリに置かない**。実行セッションのスクラッチパッド
`secrets/service-account.json` に置き、環境変数 `MEET_ARCHIVE_KEY` でパスを渡す。

## 3. 実行モード

| モード | コマンド | いつ使う |
|---|---|---|
| A. カレンダー起点（現行の日次） | `python archive.py --candidates candidates.json` | 毎朝。Claude が MCP で7名のカレンダーから候補JSONを作って渡す |
| B. Drive 走査 | `python archive.py --scan-drive --lookback-days 0` | メンバーがフォルダを共有した直後の一括回収、バックフィル |
| C. 委任モード（恒久策） | `MEET_ARCHIVE_IMPERSONATE=1 python archive.py --lookback-days 3` | Workspace 管理者がドメイン全体の委任を設定した後。カレンダーも Drive も各メンバーとして読むので共有依頼が不要になる |

- A と B は同時指定できる（候補を合算して重複排除）。
- `--dry-run` で書き込みなしに「候補件数・読めるか読めないか」だけ確認できる。
- `--limit N` で処理件数を絞れる（テスト用）。
- 終了コード: 0=正常 / 2=プレースホルダ枯渇で途中停止 / 3=鍵なし。

### 候補JSON（モードA）の形式

```json
[
  {"fileId": "1F8zZn9F0qk-...", "member": "浦島", "memberEmail": "urashima@writeup.co.jp",
   "eventTitle": "有限会社中嶋設備様", "eventStart": "2026-10-14T15:00:00+09:00"}
]
```

- `fileId` はカレンダー予定の `attachments[].fileUrl`（`.../document/d/<ID>/edit?usp=meet_tnfm_calendar`）から取る。URLをそのまま `fileUrl` に入れてもよい。
- `member` はカレンダーの持ち主。`eventTitle` は予定のタイトル（社内MTG除外に使う）。
- 同じメモが複数人のカレンダーに載る（全件壁打ち会など）が、スクリプト側で fileId で重複排除する。

## 4. 日次ルーティンの流れ（モードA）

1. リポジトリを最新にする（`config/meet-collector.json` の `schedule.branch`）。
2. 7名のカレンダー（`members[].email`）を Google Calendar コネクタで `list_events`（直近 `discovery.lookbackDays` 日、`orderBy=startTime`）し、
   `attachments[].title` が「Gemini によるメモ」の予定だけ抜き出して候補JSONを作る。**サブエージェントに任せ、JSONだけ受け取る**（予定の生データを親の文脈に流さない）。
3. `MEET_ARCHIVE_KEY=... python scripts/meet_archive/archive.py --candidates <JSON>` を実行する。
4. manifest が変わっていれば commit & push。
5. Slack に報告する。**読めなかった商談（`lastRun.unreadable`）は必ず載せる。** 書式は SKILL.md。

## 5. 読めない商談が出たときの対処（モードA/B の限界）

カレンダーで添付が見えても、ファイル自体がボットに共有されていなければ中身は読めない。
`lastRun.unreadable` に `member` ごとの件数が出る。対処は次のどちらか。

### 5-a. 応急処置：メンバー本人がフォルダを共有する

1. Google ドライブ → マイドライブ → 「Google Meet」フォルダ（複数あれば**全部**。作成日が新しいものほど重要）。
2. 右クリック → 共有 → 次の2つを「編集者」で追加（通知メールは不要）:
   - `ai.firststep2026@gmail.com`
   - `meet-archive-bot@meet-archive-tool.iam.gserviceaccount.com`
3. 共有後、ボット側で `--scan-drive --lookback-days 0` を1回流すと過去分も回収される。

**注意:** Google が「Google Meet」フォルダを作り直すとまた切れる。切れたことは日次の `unreadable` 報告で分かる。

### 5-b. 恒久策：ドメイン全体の委任（Workspace 管理者作業・1回）

1. Google Cloud コンソール → プロジェクト `meet-archive-tool` → IAM → サービスアカウント `meet-archive-bot@...` → 「詳細」→ **クライアントID**（数字）を控える。
2. Google Workspace 管理コンソール → セキュリティ → アクセスとデータ管理 → **API の制御** → 「ドメイン全体の委任を管理」→「新しく追加」。
3. クライアントID を貼り、OAuth スコープに次の2つをカンマ区切りで入力して承認:
   ```
   https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/calendar.readonly
   ```
4. ボット側で `MEET_ARCHIVE_IMPERSONATE=1 python archive.py --dry-run --lookback-days 3` を実行し、
   `unreadable` が 0 になることを確認したら、ルーティンをモードCに切り替える（候補JSON作成の工程が不要になる）。

この設定で読めるのは `config/meet-collector.json` の `members[]` に列挙した7名のファイルだけ（スクリプトがその7名としてしか振る舞わない）。
書き込みは従来どおりサービスアカウント自身の権限（集約フォルダの編集者）で行う。

## 6. プレースホルダの補充

サービスアカウントは Drive 容量を持たないため新規ドキュメントを作れない。
`manifest.placeholders` の `status: free` が残り2以下になったら、ai.firststep2026 で集約フォルダに空の Googleドキュメント
`商談文字起こし集約（全期間）_N`（N は既存最大+1）を作り、manifest に `{"title","fileId","status":"free"}` を追記する。
`_62` は 2026-10-25 時点で中身があり（2025-06 菅野分の文字起こしが単独で入っていた）、`reserved-unknown` として使用停止中。

## 7. 鍵が消えたとき（コンテナ再作成）

スクリプトと設定はリポジトリにあるので、失われるのは鍵だけ。
Google Cloud コンソール → サービスアカウント → キー → 新しい鍵（JSON）を作成し、スクラッチパッド `secrets/service-account.json` に `chmod 600` で置く。
古い鍵は削除する。Drive 上のデータと manifest は無傷なので、そのまま日次を再開できる。

## 8. 既知の制約

- 委任（5-b）が入るまでは、共有が切れた分は `unreadable` として毎日報告され続ける。共有依頼が済むまで消えない。
- モードA の候補取得は Claude の MCP 呼び出しなのでトークンを消費する。7名×3日で予定100件前後が目安。サブエージェントで圧縮すること。
- 1チャンク=80万文字。Googleドキュメントの上限（約102万文字）に余裕を持たせている。
- 同じ商談に「Gemini によるメモ」が2つ添付されることがある（録画のやり直し等）。両方を別エントリとして取り込む（fileId が違うため）。
