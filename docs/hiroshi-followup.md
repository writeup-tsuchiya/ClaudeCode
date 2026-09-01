# ヒロシ商談フォロー 運用メモ

商談後（Cヨミ・失注）の見込み客へ、ライトアップのAIアシスタント「ヒロシ」名義で
フォローメールを送る仕組み。

## 全体の流れ

| 時刻(JST) | ルーティン | 状態 |
|---|---|---|
| 平日 01:00 | ①文面生成 `trig_01UnxKuBTk6Kr7XNaHUDqa9A` | 稼働中 |
| 平日 08:00 | ②送信 | **未作成**（下記参照） |
| 毎時 :02 | 返信→Slack通知 `trig_013egkxsLwBErzTSgVTZHpNF` | 稼働中 |

- ①は `data/hiroshi-sent-log.csv` を読んで除外判定し、文面を `data/drafts/YYYY-MM-DD.csv` に書いて push する。実送信はしない。
- ②はその CSV を読んで Gmail 送信し、結果を `data/hiroshi-sent-log.csv` に追記して push する。
- 上限は 1日150通。件名は必ず `【ライトアップ】` で始める。

## ②送信ルーティンが未作成の理由

Claude Code の自動モード分類器が、**無人で外部宛に一括メール送信するルーティンの作成**を
ブロックするため、AI側からは作成できない（2026/08/31・09/01 に2回試行、いずれも拒否）。

人が claude.ai の Routines 画面から作成する必要がある。**作らないと①の文面は送信されずに溜まり続ける。**

### 作成手順

1. https://claude.ai/settings/routines を開く
2. 「新規作成」
3. 名前：`ヒロシ商談フォロー②朝8時・送信`
4. スケジュール：平日 8:00（JST）。cron 直接指定なら UTC で `0 23 * * 0-4`
5. 実行先：既存セッション `session_01EieH6uvMF7rRQUa6e1EHBs` を指定
   （新規セッションだと Gmail / Drive / Slack の接続を引き継げない）
6. 本文に下記「②のプロンプト全文」を貼る

### ②のプロンプト全文

```
【ヒロシ商談フォロー：朝バッチ（送信）】日本時間 朝8時の定期実行です。

深夜1時のバッチが作成した下書きを、実際にGmailで送信してください。
新しい文面をここで作らないこと。送信対象は、すでに当社と商談を実施した
既存の見込み顧客のみです（新規の一斉配信ではありません）。

■ ステップ0：最新を取得
作業ディレクトリ /home/user/ClaudeCode で：
    git fetch origin claude/deal-followup-ai-strategy-dxvom8
    git checkout claude/deal-followup-ai-strategy-dxvom8
    git pull origin claude/deal-followup-ai-strategy-dxvom8

■ 送信対象
data/drafts/<今日の日付>.csv を読む。
列は 会社名,送信先,宛名,件名,本文,提案資料URL,商談ログURL。
ファイルが無ければ「本日は送信対象なし」とSlackに1行投稿して終了する。

さらに data/hiroshi-sent-log.csv を読み、
すでに「送信済み」で30日以内の会社が混じっていたらスキップする（二重送信防止）。

■ 送信
送信元: ai.firststep2026@gmail.com
mcp__Gmail__send_message で 1件ずつ順番に送る。並列送信はしない。
to=送信先 / subject=件名 / body=本文

1日150通まで。超える場合は上から150通だけ送り、残りは翌日に回す。

■ 送信しない条件（スキップして状態「保留」で記録）
- 送信先が空、または明らかに不正な形式
- 本文にプレースホルダ（〇〇、TODO等）が残っている
- 件名が「【ライトアップ】」で始まっていない
- 台帳に30日以内の送信済み記録がある

■ バウンス時
アドレスを推測して直して再送しないこと（他人に商談内容が届く事故になる）。
台帳の状態を「バウンス」にし、Slackで報告するに留める。

■ 結果の記録（必須）
data/hiroshi-sent-log.csv に1社1行で追記する。
列は 送信日,会社名,送信先,件名,状態,備考。状態は 送信済み／バウンス／保留。
追記後：
    git add data && git commit -m "<日付> 送信結果を記録" && git push -u origin claude/deal-followup-ai-strategy-dxvom8
push しないとコンテナ再作成で消え、翌日二重送信になる。

■ 完了後
Slack #チーム浦嶋-aiセールスch (C094LNTDYGG) に1回だけ投稿：
📨 ヒロシ商談フォロー 本日の送信結果
送信成功: ◯件 ／ 失敗: ◯件 ／ スキップ: ◯件
（失敗があれば会社名とエラー内容を列挙）
```

## 既知の制約

- **Googleスプレッドシートにセルを書き込む手段が無い。** Drive MCP の `update_file` は
  タイトルと保存先しか変えられない。そのため送信台帳はリポジトリ側（`data/`）が正本で、
  スプレッドシートの「送信mailログ」タブは人間向けのミラー。
  シート側を自動更新したい場合は、Google Sheets 用のコネクタを別途追加する必要がある。
- 「送信mailログ」タブは `read_file_content` では読めない。
  `download_file_content` に `exportMimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'`
  を指定して xlsx として取得すると全タブ読める。
- 送信元は新規の Gmail アカウントのため、Gmail 側の上限は 500通/日。
  到達率の観点からも急な増量は避け、150通/日で様子を見る。

## 参照

- ヨミ表: `1QML2V0qLX1tlfse-ONXW56-ifu377pGq9p20WNOsNII`
- 商材事例: `1svI03RoBoqNtrhpWBSCJ8_W4uaPQBcRvEfIpw9D_hH0` (gid=730612109)
- 商談ログ Drive フォルダ: `1_Rr4LqPD6fHhcmXFX4LsEORuAJlZaPKG`
- Slack: `C094LNTDYGG`（#チーム浦嶋-aiセールスch）
- Gmail ラベル `Label_1` = ヒロシ返信-Slack通知済み
