---
name: meet-collector
description: 第一営業部7名の商談（Google Meet）で Gemini が生成した「Gemini によるメモ」を、カレンダー起点で発見し、土屋所有の集約フォルダ内ドキュメント「商談文字起こし集約（全期間）_N」に連結して蓄積するスキル。「Meetのデータ集めて」「商談データ集約して」「商談ログを集約して」と言われたとき、および毎日7:30のRoutine「Google Meet 商談アーカイブ（日次追記）」から起動されたときに必ず使用すること。
---

# Google Meet 商談ログ集約（カレンダー起点）

設計と運用の全体は `docs/meet-archive-ops.md`。ここは日次実行の手順だけを書く。

## 前提（毎回確認）

- 作業ディレクトリ `/home/user/ClaudeCode`。ブランチは `config/meet-collector.json` の `schedule.branch`。
- 鍵はスクラッチパッド `secrets/service-account.json`。無ければ **処理せず** Slack に「鍵なし」を報告して終わる（`docs/meet-archive-ops.md` 7章）。
- Python 環境はスクラッチパッドの `venv`（無ければ `python3 -m venv venv && ./venv/bin/pip install -r scripts/meet_archive/requirements.txt`）。

## 手順

### 1. 最新を取得

```
git fetch origin <branch> && git checkout <branch> && git pull origin <branch>
```

### 2. カレンダーから候補を抜く（サブエージェントに委任）

`config/meet-collector.json` の `members[]` 7名について、Google Calendar コネクタ `list_events` を
`calendarId=<email>`、`startTime=今日-lookbackDays日 00:00`、`endTime=今日 23:59`（日本時間）、`orderBy=startTime`、`pageSize=100` で呼ぶ。

各予定の `attachments[]` のうち `title` に「Gemini によるメモ」を含むものだけを取り、次の1行JSONの配列にする:

```json
{"fileId":"<fileUrl の /d/ と /edit の間>","member":"<担当名>","memberEmail":"<email>","eventTitle":"<summary>","eventStart":"<start.dateTime>"}
```

- 添付が無い予定、`conferenceData` が無い予定は無視する。
- `config/team.json` の `exclude.titleContains` / `titleExact` に当たるタイトル（全件壁打ち会、営業MTG 等）は**候補に入れなくてよい**（入れてもスクリプト側で落とす）。
- 同じ fileId が複数人のカレンダーに出るのは正常（スクリプトで重複排除）。
- **この工程はサブエージェントに任せ、返り値は候補JSONだけにする。** 予定の生データを親セッションに流さない。
- 結果をスクラッチパッドに `candidates-YYYY-MM-DD.json` として保存する。候補0件でも空配列 `[]` を保存する。

### 3. スクリプト実行

```
cd /home/user/ClaudeCode
MEET_ARCHIVE_KEY=<scratchpad>/secrets/service-account.json \
  <scratchpad>/venv/bin/python scripts/meet_archive/archive.py --candidates <scratchpad>/candidates-YYYY-MM-DD.json 2>&1 | tail -40
```

標準出力の最終行が JSON サマリ。`processed`（取り込んだ件数）、`unreadableCount`、`unreadableByMember`、`chunksWritten`、`placeholdersFree`、`ok` を見る。
終了コード 2 はプレースホルダ枯渇（`docs/meet-archive-ops.md` 6章）、3 は鍵なし。

### 4. 記録

`config/meet-archive-manifest.json` に差分があれば（毎回 `lastRun` が更新されるので通常ある）:

```
git add config/meet-archive-manifest.json && git commit -m "Meet archive: YYYY-MM-DD 取り込み N件 / 未取得 M件" && git push -u origin <branch>
```

### 5. Slack 報告（#チーム浦嶋-aiセールスch / C094LNTDYGG）

**毎回1回だけ投稿する。0件でも投稿する**（「0件＝正常」で黙らない。黙ったせいで1か月気づけなかった）。

```
📚 商談ログ集約（YYYY-MM-DD）
取り込み: N件（チャンク _NN に追記 / 累計 T件） ／ 候補: C件 ／ 読めず: M件
読めなかった商談（共有が必要）:
　・浦島 10/14 有限会社中嶋設備様
　・土屋 10/16 ★元気でんき株式会社 ...
→ 該当メンバーはマイドライブ「Google Meet」フォルダを ai.firststep2026@gmail.com と meet-archive-bot@meet-archive-tool.iam.gserviceaccount.com に共有してください（手順: docs/meet-archive-ops.md 5-a）
プレースホルダ残: P
```

- 読めずが0件なら「読めなかった商談」の段落は省く。
- 読めずが続いている間は毎日載せる（消えたら共有されたということ）。
- 鍵なし・プレースホルダ枯渇・スクリプト異常終了は先頭を ⚠️ にして理由を1行書く。

## やってはいけないこと

- 元ファイル（各メンバーのメモ）を移動・削除・編集すること。読むだけ。
- 読めなかった商談を報告から落とすこと。
- manifest を手で書き換えて「処理済み」にすること（取り込んだことにならない）。
- Drive の `read_file_content` で本文を読んでチャットに流すこと（トークンを浪費する。本文はスクリプトが扱う）。
- 鍵の中身を表示・コミットすること。
