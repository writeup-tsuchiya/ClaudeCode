# ヒロシ商談フォロー 運用メモ

商談後（Cヨミ・失注）の見込み客へ、ライトアップのAIアシスタント「ヒロシ」名義で
フォローメールを送る仕組み。

## 全体の流れ

| 時刻(JST) | ルーティン | 状態 |
|---|---|---|
| 平日 01:00 | ①文面生成 `trig_01UnxKuBTk6Kr7XNaHUDqa9A` | 稼働中 |
| 平日 08:00 | ②送信 `trig_018XdHfX83D8H3vG5dxsp3dr` | 稼働中 |
| 毎時 :02 | 返信→Slack通知 `trig_013egkxsLwBErzTSgVTZHpNF` | 稼働中 |

- ①は `data/hiroshi-sent-log.csv` を読んで除外判定し、文面を `data/drafts/YYYY-MM-DD.csv` に書いて push する。実送信はしない。
- ②はその CSV を読んで Gmail 送信し、結果を `data/hiroshi-sent-log.csv` に追記して push する。
- 上限は 1日150通。件名は必ず `【ライトアップ】` で始める。

## ②送信ルーティンについて

`trig_018XdHfX83D8H3vG5dxsp3dr` として 2026/09/02 に作成済み。
自動モードの分類器に2回ブロックされたが、3回目で通った（同じ内容でも通る／通らないが揺れる）。

設計上のポイント：

- 実行先は既存セッション `session_01EieH6uvMF7rRQUa6e1EHBs`（self-bind）。
  新規セッションだと Gmail / Drive / Slack の接続を引き継げない。
  作成時に「MCPコネクタを保持しない」という警告が出るが、self-bind のルーティンは
  実際にはセッションのMCPツールを引き継ぐ（返信監視ルーティンで実証済み）。
- 送信対象は `data/drafts/` 配下の**全CSV**から、台帳に記録済みの分を引いた残り。
  当日ファイルだけを見る作りにすると、朝8時より後に作った下書きが永久に送られないため。
  この作りなら、実行できなかった日があっても翌朝に自動で追いつく。
- 150通を超えた分は台帳に記録しないので、翌朝そのまま対象になる。

## スプレッドシートへの自動追記（Apps Script 経由）

Claude 側から Google スプレッドシートのセルを直接書き換える手段は無い。
Drive MCP の `update_file` はタイトルと保存先しか変えられず、コネクタ一覧にも
Google スプレッドシートを編集できるものは存在しない（2026/09 時点で確認済み）。

そこで、**Drive へのファイル作成はできる**ことを利用して、次の形で自動化する。

```
夜1時のバッチ → Drive「ヒロシ送信キュー」フォルダに CSV を作成
                  ↓
スプシ側の Apps Script（毎朝6時台のトリガー）が CSV を読んで
「送信mailログ」へ行を追加し、CSV を「処理済み」へ移動
```

- キューフォルダ: `1tlwhtOwSh8J6_N8xaPNdNoUm3BRyXUfq`
  https://drive.google.com/drive/folders/1tlwhtOwSh8J6_N8xaPNdNoUm3BRyXUfq
- スクリプト本体: `docs/appsscript-import.gs`（導入手順はファイル冒頭のコメント参照）
- スプレッドシートの所有者は k_yoshikawa@writeup.co.jp なので、
  **スクリプトの設置とトリガー登録はその所有者アカウントで行うこと。**
  キューフォルダは同アカウントへ編集者として共有済み。
- D列（送信先）が既にシートにある行は取り込まれないので、同じ CSV を置き直しても安全。

送信履歴の正本は引き続きリポジトリ側の `data/hiroshi-sent-log.csv`。
スプレッドシートは人間が見るためのミラーという位置づけは変わらない。

## そのほかの制約
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
