#!/bin/bash
# ashiato.sh - あしあとプロジェクト メインコマンド
# 使い方:
#   bash ashiato.sh record [分数]     # 録音開始
#   bash ashiato.sh transcribe [file] # 文字起こし
#   bash ashiato.sh report [file]     # 報告書生成
#   bash ashiato.sh run [分数]        # 録音→文字起こし→報告書 一括実行

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CMD="${1:-help}"
shift

case "$CMD" in
  record)
    bash "${SCRIPT_DIR}/record.sh" "$@"
    ;;
  transcribe)
    bash "${SCRIPT_DIR}/transcribe.sh" "$@"
    ;;
  report)
    bash "${SCRIPT_DIR}/report.sh" "$@"
    ;;
  run)
    MINUTES="${1:-60}"
    echo "=== あしあと 一括実行 ==="
    echo "① 録音 (${MINUTES}分)"
    AUDIO_FILE=$(bash "${SCRIPT_DIR}/record.sh" "$MINUTES")
    echo "② 文字起こし"
    TRANSCRIPT_FILE=$(bash "${SCRIPT_DIR}/transcribe.sh" "$AUDIO_FILE")
    echo "③ 報告書生成"
    bash "${SCRIPT_DIR}/report.sh" "$TRANSCRIPT_FILE"
    ;;
  help|*)
    echo "使い方:"
    echo "  bash ashiato.sh record [分数]          # 録音（デフォルト60分）"
    echo "  bash ashiato.sh transcribe [wavファイル] # 文字起こし"
    echo "  bash ashiato.sh report [txtファイル]    # 報告書生成"
    echo "  bash ashiato.sh run [分数]             # 全工程一括実行"
    ;;
esac
