# あしあと（Ashiato）

学校外の支援活動セッションを、音声記録から教育的エビデンスへ自動変換するパイプラインです。

---

## なぜこのツールが必要か

学校外の居場所（フリースクール・自然体験活動など）では、子どもたちの成長が現場の実感として確かに存在します。しかし、それを「学校の言葉」で記録・共有するコストが、支援の継続的な課題になっています。

現職教員が子どもへの伴走に全力を注ぐほど、記録業務は後回しになります。その結果：

- 学校側は出席認定の判断に必要な根拠を受け取れない
- 保護者は子の微細な成長が見えないまま孤立感を深める
- 支援現場・学校・家庭の間に**情報の分断**が生まれる

このツールはその構造的な問題をAIで解消します。音声記録だけを残せばあとは自動処理が進み、AIが一次記録を整理した上で、現職教員が教育的な観点から内容を確認・承認して「指導要録」準拠の報告書を出力します。

**AIが評価・判断するのではなく、根拠を提示し、人間（教員）が判断する** という Human-in-the-loop 設計が核心です。

---

## アーキテクチャ

### ハードウェア構成

```
MacBook Air（録音・操作端末）
      ↕ SSH / SCP
Mac mini M1 8GB（ローカル推論サーバー）
├── Whisper large-v3         # 日本語音声認識
├── pyannote 3.1             # 話者分離（diarization）
└── Ollama: qwen2.5:7b       # 報告書生成・活動内容抽出
```

すべてローカル環境で完結します。音声データや発言記録は外部サーバーへ送信されません。

### 処理パイプライン

```
① ICレコーダー録音（MP3）
      ↓ USB → MacBook Air
② scp → Mac mini 転送
      ↓
③ Whisper + pyannote
      → 文字起こし CSV（start, end, speaker, text）
      ↓
④ map_speakers.py（対話式）
      → SPEAKER_00 等を実名にマッピング
      → mapped.csv + _meta.txt（日付・場所・活動内容）
      ↓
⑤ generate_report.py
      → 児童ごとの観点別学習状況を生成
      → report_校長向け_YYYYMMDD.md
```

### データの流れと責任分界

```
現場音声（一次ソース）
      ↓ AI処理（Whisper・pyannote・Ollama）
文字起こし・話者分離・観点別記述の草稿
      ↓ 人間によるレビュー・承認（現職教員）
正式な連携支援レポート（校長向け・保護者向け）
```

AIは「一次記録の整理」と「教育的フォーマットへの翻訳補助」を担います。最終確認・承認は必ず支援者（教員）が行います。

---

## ファイル構成

```
ashiato/
├── README.md
├── ashiato.sh          # 全工程を統括するメインコマンド
├── record.sh           # 録音補助
├── transcribe.sh       # Whisper + pyannote による文字起こし
├── map_speakers.py     # 話者名マッピング・活動内容抽出（対話式）
└── generate_report.py  # 観点別学習状況レポート生成
```

---

## 使い方

### ステップ1: 文字起こし

```bash
bash ~/scripts/ashiato/transcribe.sh <音声ファイル.mp3>
# → output.csv が生成される
```

### ステップ2: 話者マッピング

```bash
python3 ~/scripts/ashiato/map_speakers.py output.csv
# 対話式で SPEAKER_00 等に名前を割り当てる
# → mapped.csv と _meta.txt が生成される
```

### ステップ3: 報告書生成

```bash
python3 ~/scripts/ashiato/generate_report.py mapped.csv \
  --date "2025年11月23日" \
  --location "里山フィールド（兵庫県姫路市）" \
  --activity "自然探索・虫採り・川遊び・昼食調理・火起こし体験" \
  --supporter "塚原"
# → report_校長向け_YYYYMMDD.md が生成される
```

### 全工程一括実行

```bash
bash ~/scripts/ashiato/ashiato.sh run [録音分数]
```

---

## セットアップ（Mac mini）

```bash
git clone git@github.com:709sakata/ashiato.git ~/scripts/ashiato

# audio-diarization-transcript の初回セットアップ
cd ~/bin/audio-diarization-transcript
source ~/.local/bin/env
uv sync
```

## 更新方法

```bash
# MacBook Air: 修正後
git add . && git commit -m "変更内容" && git push

# Mac mini: 反映
cd ~/scripts/ashiato && git pull
```

---

## プライバシーとデータ管理

- 音声データ・文字起こし CSV は個人情報のため `.gitignore` で管理対象外
- すべての推論処理はローカル完結（外部 API 不使用）
- 報告書の最終確認・承認は支援者（教員）が実施
