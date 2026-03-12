#!/bin/bash
# record.sh - セッション録音
# 使い方: bash record.sh [分数]
# 出力: 録音ファイルのパスをstdoutに出力

MINUTES="${1:-60}"
SECONDS_DURATION=$((MINUTES * 60))
OUTPUT_DIR="$HOME/ashiato/recordings"
mkdir -p "$OUTPUT_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
OUTPUT_FILE="${OUTPUT_DIR}/session_${TIMESTAMP}.wav"

echo "=== 録音開始: ${MINUTES}分 ===" >&2
echo "ファイル: ${OUTPUT_FILE}" >&2
echo "停止するには Ctrl+C" >&2
echo "" >&2

# 録音実行（Ctrl+Cで停止可能）
command ffmpeg -f avfoundation -i ":0" \
  -ar 16000 -ac 1 \
  -t "$SECONDS_DURATION" \
  "$OUTPUT_FILE" 2>/dev/null

echo "" >&2
echo "=== 録音完了: $OUTPUT_FILE ===" >&2

# ファイルパスをstdoutに出力（パイプ用）
echo "$OUTPUT_FILE"
