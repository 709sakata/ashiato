# あしあと（Ashiato）

ASOBOプロジェクトの活動セッションを自動記録・報告書化するパイプラインです。

## 背景・目的

**ASOBO**は、不登校の子どもたちを対象とした里山体験型の学校外支援活動です。
現職教員のらぁゆが主導し、月1〜2回のセッションで自然探索・調理・火起こしなどの体験を提供しています。

活動の成果を学校（校長）や保護者に伝えるために、セッションの記録を「指導要録」に準じた言語で報告書化する必要があります。
このパイプラインは、ICレコーダーの音声から報告書生成までを自動化し、支援者の記録業務を大幅に削減することを目的としています。

## システム構成

```
Mac mini（M1, 8GB）
├── Whisper large-v3     # 日本語文字起こし
├── pyannote 3.1         # 話者分離（diarization）
└── Ollama qwen2.5:7b    # 報告書生成・活動内容抽出
```

## パイプライン全体像

```
① ICレコーダー録音（DEXION, MP3）
      ↓ USBでMacBook Airに取り込み
② scp → Mac mini転送
      ↓
③ audio-diarization-transcript（Whisper + pyannote）
      → 文字起こしCSV（start, end, speaker, text）
      ↓
④ map_speakers.py
      → SPEAKER_00等を実名にマッピング
      → mapped.csv + _meta.txt
      ↓
⑤ generate_report.py
      → Ollamaで報告書生成
      → report_校長向け_YYYYMMDD.md
```

## ファイル構成

```
ashiato/
├── README.md
├── ashiato.sh          # パイプライン全体を実行するシェルスクリプト
├── record.sh           # 録音補助スクリプト
├── transcribe.sh       # 文字起こし実行スクリプト
├── map_speakers.py     # 話者名マッピング + 活動内容抽出（対話式）
└── generate_report.py  # 報告書生成（校長向けMarkdown）
```

## 使い方

### ステップ1: 文字起こし

```bash
bash ~/scripts/ashiato/transcribe.sh <音声ファイル.mp3>
# → output.csv が生成される
```

### ステップ2: 話者マッピング

```bash
python3 ~/scripts/ashiato/map_speakers.py output.csv
# → 対話式でSPEAKER_00等に名前を割り当て
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

## 重要な設計思想

```
現場記録（一次ソース）≠ 校長向け報告書

現場記録: 子どもの成長が主語、AIは文字起こし・整理のみ
報告書:   指導要録の言語への「翻訳」、AIが翻訳を補助
```

- **AIが判断・評価するのではなく、現職教員（らぁゆ）が判断し、AIが根拠を提供する**
- 報告書の最終確認・承認は必ず支援者が行う
- 音声データ・CSVは個人情報のためGitHub管理対象外（`.gitignore`）

## 開発環境のセットアップ（Mac mini）

```bash
# リポジトリクローン
git clone git@github.com:709sakata/ashiato.git ~/scripts/ashiato

# audio-diarization-transcriptのセットアップ（初回のみ）
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

## 関係者

| 役割 | 名前 |
|------|------|
| プロジェクト責任者 | 阪田直樹（株式会社和平） |
| 現場リーダー・教員 | らぁゆ |
| 技術担当 | 阪田 |
| 受益者 | 不登校児童 5名 |
