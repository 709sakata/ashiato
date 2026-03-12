#!/bin/bash
# transcribe.sh - Whisperで文字起こし（Mac mini経由）
# 使い方: bash transcribe.sh [wavファイル]
# 出力: 文字起こしファイルのパスをstdoutに出力

AUDIO_FILE="${1}"
MAC_MINI="${ASHIATO_MAC_MINI:-mac-mini-ollama}"
OUTPUT_DIR="$HOME/ashiato/transcripts"
mkdir -p "$OUTPUT_DIR"

if [ -z "$AUDIO_FILE" ] || [ ! -f "$AUDIO_FILE" ]; then
  echo "エラー: 音声ファイルが見つかりません: $AUDIO_FILE" >&2
  exit 1
fi

BASENAME=$(basename "$AUDIO_FILE" .wav)
TRANSCRIPT_FILE="${OUTPUT_DIR}/${BASENAME}.txt"

echo "=== 文字起こし開始 ===" >&2
echo "ファイル: $AUDIO_FILE" >&2
echo "Mac miniに転送中（${MAC_MINI}）..." >&2

# Mac miniに転送
scp -q "$AUDIO_FILE" "${MAC_MINI}:/tmp/ashiato_audio.wav" \
  || { echo "エラー: Mac miniへの音声ファイル転送失敗（${MAC_MINI}）" >&2; exit 1; }

# Mac miniで話者分離実行 (uv run main.py)
echo "話者分離 + 文字起こし処理中（large-v3, しばらくお待ちください）..." >&2

ssh "$MAC_MINI" "cd ~/bin/audio-diarization-transcript && \
  uv run main.py /tmp/ashiato_audio.wav --output_csv_path /tmp/ashiato_audio.csv" \
  || { echo "エラー: Mac mini上で話者分離実行失敗" >&2; exit 1; }

# 結果をMacBook Airに取得 (CSV)
TRANSCRIPT_FILE="${OUTPUT_DIR}/${BASENAME}.csv"
scp -q "${MAC_MINI}:/tmp/ashiato_audio.csv" "$TRANSCRIPT_FILE" \
  || { echo "エラー: 文字起こし結果の取得失敗（${MAC_MINI}:/tmp/ashiato_audio.csv）" >&2; exit 1; }

echo "=== 文字起こし完了: $TRANSCRIPT_FILE ===" >&2
echo "" >&2
echo "--- 内容プレビュー ---" >&2
head -5 "$TRANSCRIPT_FILE" >&2
echo "" >&2

echo "$TRANSCRIPT_FILE"
