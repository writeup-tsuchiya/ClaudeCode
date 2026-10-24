# Routine（スケジュールタスク）指示文の正本

Routine の指示文は claude.ai 上にしか保存されないため、ここに正本を置く。変更するときは **このファイルを先に直し、同じ文面を Routine に貼る**。

| Routine | ID | 実行 | 編集できる場所 |
|---|---|---|---|
| Google Meet 商談アーカイブ（日次追記・カレンダー起点） | trig_01MTgR9GEssa4qHtef3AUpRL | 平日 7:30 JST、固定セッション session_0145y11Mrows4nBJJHe8nf56 | そのセッション内の Claude から update_trigger、または https://claude.ai/code/routines/trig_01MTgR9GEssa4qHtef3AUpRL |
| Google Meet 商談ログ分析（1時間ごと・カレンダー起点） | trig_01PQpcrQWwnwR45R7c6Ez675 | 毎時40分、毎回新規セッション | 人が https://claude.ai/code/routines/trig_01PQpcrQWwnwR45R7c6Ez675 で編集（http_api 作成のため Claude からは変更不可） |

---

## 1. Google Meet 商談アーカイブ（日次追記・カレンダー起点）

```
【定期実行】Google Meet 商談ログ集約の日次更新（2026-10-25 全面改訂版）。
やること：7名のカレンダーから「Gemini によるメモ」が添付された商談を見つけ、scripts/meet_archive/archive.py で集約ドキュメントに追記し、結果（読めなかった商談を含む）をSlackに報告する。手順の正本は .claude/skills/meet-collector/SKILL.md、背景と障害対応は docs/meet-archive-ops.md。必ず SKILL.md の手順どおりに進める。

■ 0. 最新を取得
作業ディレクトリ /home/user/ClaudeCode で：
    git fetch origin claude/dreamy-ritchie-6spk4d && git checkout claude/dreamy-ritchie-6spk4d && git pull origin claude/dreamy-ritchie-6spk4d

■ 1. 環境確認
SP=/tmp/claude-0/-home-user-ClaudeCode/d430197c-d785-59e6-87e2-5ac2ca906b8d/scratchpad
- 鍵 $SP/secrets/service-account.json が無ければ処理せず Slack に「⚠️ 商談ログ集約：鍵なし。再発行が必要（docs/meet-archive-ops.md 7章）」と1行投稿して終了。
- $SP/venv が無ければ python3 -m venv $SP/venv。毎回 $SP/venv/bin/pip install -q -r scripts/meet_archive/requirements.txt（冪等）。

■ 2. カレンダーから候補JSONを作る（サブエージェントに委任）
config/meet-collector.json の members[] 7名について Google Calendar コネクタ list_events を calendarId=<email>、startTime=（今日−3日）00:00、endTime=今日 23:59（日本時間）、orderBy=startTime、pageSize=100 で呼び、attachments[].title に「Gemini によるメモ」を含む予定だけを次の形式の配列にする：
  {"fileId":"<fileUrl の /d/ と /edit の間>","member":"<担当名>","memberEmail":"<email>","eventTitle":"<summary>","eventStart":"<start.dateTime>"}
config/team.json の exclude.titleContains / titleExact に当たるタイトル（全件壁打ち会、営業MTG等）は入れなくてよい。同じfileIdが複数人に出るのは正常。
この工程はサブエージェントに任せ、返り値は候補JSONだけにする（予定の生データを親に流さない）。結果を $SP/candidates-YYYY-MM-DD.json に保存（0件でも []）。

■ 3. スクリプト実行
    cd /home/user/ClaudeCode && MEET_ARCHIVE_KEY=$SP/secrets/service-account.json $SP/venv/bin/python scripts/meet_archive/archive.py --candidates $SP/candidates-YYYY-MM-DD.json 2>&1 | tail -40
最終行のJSONサマリ（processed / unreadableCount / unreadableByMember / unreadable[] / chunksWritten / placeholdersFree / ok）を読む。終了コード2=プレースホルダ枯渇、3=鍵なし。

■ 4. 記録
config/meet-archive-manifest.json に差分があれば：
    git add config/meet-archive-manifest.json && git commit -m "Meet archive: YYYY-MM-DD 取り込み N件 / 未取得 M件" && git push -u origin claude/dreamy-ritchie-6spk4d

■ 5. Slack報告（#チーム浦嶋-aiセールスch / C094LNTDYGG）に毎回1回だけ投稿。0件でも投稿する：
📚 商談ログ集約（YYYY-MM-DD）
取り込み: N件（チャンク _NN に追記 / 累計 T件） ／ 候補: C件 ／ 読めず: M件
読めなかった商談（共有が必要）:
　・<担当> <月/日> <商談タイトル>（全件。0なら段落ごと省く）
→ 該当メンバーはマイドライブ「Google Meet」フォルダ（複数あれば全部）を ai.firststep2026@gmail.com と meet-archive-bot@meet-archive-tool.iam.gserviceaccount.com に編集者で共有してください（手順: docs/meet-archive-ops.md 5-a）
プレースホルダ残: P
鍵なし・枯渇・異常終了は先頭を ⚠️ にして理由を1行。

■ 禁止
元ファイルの移動・削除・編集／読めなかった商談を報告から落とす／manifest を手で処理済みにする／Driveの read_file_content で本文を読んでチャットに流す／鍵の表示・コミット。
```

ドメイン全体の委任（docs/meet-archive-ops.md 5-b）が入ったら、■2 を削除し ■3 を次に置き換える：

```
    cd /home/user/ClaudeCode && MEET_ARCHIVE_KEY=$SP/secrets/service-account.json MEET_ARCHIVE_IMPERSONATE=1 $SP/venv/bin/python scripts/meet_archive/archive.py --lookback-days 3 2>&1 | tail -40
```

---

## 2. Google Meet 商談ログ分析（1時間ごと・カレンダー起点）

旧版は浦島さんの Drive フォルダ「Meet Recordings」(1fiLWtWQgMFJaIDgMoYelhwBbZDBzkWib) を監視していたが、Gemini in Meet の保存先変更で 2026/09/10 以降は何も拾えていなかった。以下に差し替える。

```
【定期実行・1時間ごと】直近に終わった商談の Gemini メモを分析し、スプレッドシートに追記して Slack に通知する（2026-10-25 改訂：Drive フォルダ監視からカレンダー起点に変更）。

■ 対象メンバー（7名）
urashima@writeup.co.jp（浦島）, k_tsuchiya@writeup.co.jp（土屋）, t_kanno@writeup.co.jp（菅野）, murata@writeup.co.jp（村田）, k_yoshikawa@writeup.co.jp（吉川）, enomoto@writeup.co.jp（榎本）, atsushi_suzuki@writeup.co.jp（鈴木）

■ 手順
1. 各メンバーのカレンダーを Google Calendar の list_events で取得する。calendarId=<email>、startTime=今から3時間前、endTime=今（日本時間）、orderBy=startTime、pageSize=50。
2. attachments[].title に「Gemini によるメモ」を含む予定だけを対象にする。次は対象外：タイトルが 全件壁打ち会／第一の営業MTG／第一営業部の営業MTG／振り返りMTG／AIセールスチームMTG／ドジャースチームMTG／ランチMTG／勉強会 を含むもの、参加者が @writeup.co.jp と会議室リソースだけで説明文にゲスト情報が無いもの（社内MTG）。
3. 重複防止：分析結果シート https://docs.google.com/spreadsheets/d/1t7pVp895kKB0vWWHbq-h_hfDAp0HkW5oc1cW20HeCtQ/edit?gid=608567082 を読み、同じメモURL（またはファイルID）が既に記録されていれば分析しない。同じメモが複数人のカレンダーに載ることがあるので、ファイルIDで1回だけ扱う。
4. 対象が0件なら何もせず静かに終了する（Slack 投稿もしない）。
5. 対象があれば、各メモを Google Drive の read_file_content で読む。読めない（not found / 権限なし）場合は、そのメモは分析せず、シートにも書かず、Slack に「🔒 <担当> <商談名> のメモが未共有のため分析できませんでした。マイドライブ「Google Meet」フォルダを ai.firststep2026@gmail.com に共有してください」と1行だけ投稿する（同じ商談について2回目以降は投稿しない）。
6. 読めたメモについて以下を分析する：
   ・Aヨミ（受注見込み）
   ・失注ステータス
   ・商談評価（点数・改善点）
   ・次回アクション
7. 分析結果を上記スプレッドシートに追記する（既存の列構成に合わせる。担当者名・商談日時・商談名・メモURLを必ず含める）。
8. 分析結果を Slack チャンネル「チーム浦嶋-aiセールスch」(C094LNTDYGG) に投稿する（1商談につき1投稿）。

■ 禁止
元のメモを編集・移動・削除しないこと。シートの既存行を書き換えないこと（追記のみ）。
```
