# avatar-short — 縦型ショート動画 自動生成エージェント

顔写真1枚と自己紹介ドキュメントと参照音声から、**その人のアバターが本人の声で喋る縦型ショート動画**を1本作ります。

```
入力:  assets/people/<person-id>/photo.jpg      顔写真1枚（正面・バストアップ・口を閉じたもの）
                                 intro.md       自己紹介
                                 voice_ref.wav  本人の声 20〜30秒
                                 consent.json   同意の記録（§11・無いと生成しない）

出力:  runs/<slug>/final.mp4                    1080x1920 / 30fps / 音声つき
```

正典は [`docs/REQUIREMENTS_avatar-short.md`](docs/REQUIREMENTS_avatar-short.md)。
運用の決まりは [`CLAUDE.md`](CLAUDE.md)。

---

## 採用構成（2026-08-16 決定）

**自己紹介動画（30秒）／ 0円構成**

| 層 | 使うもの | 費用 |
|---|---|---|
| 音声 | Irodori-TTS（声の複製つき・ローカル・MIT） | 0円 |
| アバター | SadTalker（Apache 2.0） | 0円 |
| 資料 | **Manim（既定）** / HyperFrames（退避） | 0円 |

有料の置き換え（HeyGen / MiniMax H3）は**採用していません**。
必要になったときの判断材料は要件定義書 §9 にあります。

---

## 動かす順番

```bash
# 0. 環境を測る（推測しない）
python3 tools/check_env.py

# 0. モデルの取得は待ち時間が長いので最初にまとめて開始する
bash tools/setup_irodori.sh &      # ④voice
bash tools/setup_sadtalker.sh &    # ⑤avatar（約2.5GB・§8-2 のパッチもここで当たる）
bash tools/setup_manim.sh          # ⑥slides
wait

# 1. 素材を置いて、1本ぶんの作業場を作る
mkdir -p assets/people/taro
cp samples/person-template/intro.md samples/person-template/consent.json assets/people/taro/
#   さらに photo.jpg と voice_ref.wav を置き、consent.json を本人の内容に書き換える
python3 tools/new_run.py --person taro --slug taro-01

# 2. ①brief と ②script はエージェントの仕事
#    runs/taro-01/script.json を作る（見本: samples/script.sample.json）

# 3. あとは1工程ずつ。各工程で実物を測ってから次へ進む
python3 tools/check_face.py    --run runs/taro-01     # ③ 写真の検品＋縮小
python3 tools/make_voice.py    --run runs/taro-01     # ④ 本人の声で読ませる
python3 tools/make_avatar.py   --run runs/taro-01     # ⑤ 写真の口を動かす
python3 tools/make_slides.py   --run runs/taro-01     # ⑥ 資料の層
python3 tools/compose.py       --run runs/taro-01     # ⑦ 1本にまとめる
python3 tools/verify_short.py  --run runs/taro-01     # ⑧ 機械で測る
```

⑧のあと、`.claude/agents/video-evaluator.md` のエージェントに
`runs/taro-01/verify.json` と `verify_frames/*.png` を読ませて合否を言わせます。

### 時間が足りないとき

**削るのは⑥slides です。** 資料の層を単色背景＋見出しだけに落とします。
④⑤⑦が通っていれば「写真が自分の声で喋る動画」は完成します。資料は後から足せます。
逆に④や⑤を飛ばすと何も残りません。

---

## 設計の要点

**判断はエージェント、処理は単機能のコマンド。**

- 失敗した工程だけやり直せる（1本 = 1ディレクトリ）
- 同じ入力なら同じ出力になる（`hashlib` ベース・乱数と時計を使わない）
- ⑥は**入口1つ・エンジン2つ・出口1つ**。エンジンを替えても後段は1行も変わらない
- ⑧は**測るだけ**で合否を言わない。合否は判断であり、判断はエージェントの仕事

### 特に踏み抜きやすいところ

| | |
|---|---|
| ④→⑤の順序 | 逆にすると本人の声でなくなる。`make_avatar.py` がコードで拒否する |
| SadTalker の MPS パッチ | 当たっていないと**黙って CPU で走る**（5.9倍遅い）。`make_avatar.py` が実行前に確認する |
| 入力画像の大きさ | 貼り戻しが画素数に比例し全体の2/3。1024x1024 で 7.1倍、800x1200 で 17.8倍 |
| `hash()` | プロセスごとに変わる。乱数を使っていないのに再現しなくなる。`hashlib` を使う |
| 1回の生成 30秒上限 | 超えると**壊れずに切り詰められる**ので気づかない。`make_voice.py` が止める |
| `exit 0` | 完了ではない。成果物を `ffprobe` で測ってから報告する |

---

## ディレクトリ

```
avatar-short/
├── CLAUDE.md                     プロジェクトの憲法（エージェントが最初に読む）
├── config/
│   ├── telop_theme.json          文字と色（見た目の値はここだけ）
│   ├── voice.json                ④の設定（§6-5 の基準値）
│   ├── verify.json               ⑧の測定条件（合否の基準ではない）
│   ├── yomi_dict.json            読み間違いの辞書（approved のみ効く）
│   └── styles/*.json             スタイルパック
├── tools/                        単機能コマンド群
│   └── engines/                  ⑥のエンジン（manim / hyperframes）
├── schemas/                      JSON の契約
├── samples/
│   ├── script.sample.json        ②script の見本
│   └── person-template/          intro.md / consent.json のひな形
├── assets/people/<person-id>/    photo / voice_ref / intro / consent  ← git 対象外
├── runs/<slug>/                  1本ぶんの作業場                      ← git 対象外
├── vendor/                       AIモデル本体（数GB）                 ← git 対象外
└── wiki/                         判例集（失敗と対処の記録）
```

🔴 **`assets/people/` を git に入れないでください。** 他人の顔写真と声が履歴に残ります。
一度コミットすると `.gitignore` に足しても履歴からは消えません。

---

## 他人の顔と声を扱うとき

このエージェントは**実在の人の顔と声を複製します。** 技術の問題とは別に、必ず守ってください。

- **顔とは別に、声の同意が要ります。** 「写真をもらった」は「声を複製してよい」ではありません
- `consent.json` が無い person-id では生成しません（コードで止めてあります）
- **自動で公開しません。** 外に出す操作は毎回確認を取ってください
- **自己紹介ドキュメントに書いていないことを喋らせないでください**（経歴を盛らない）
