#!/bin/bash
# ashiato.sh - あしあとプロジェクト メインコマンド
# 使い方:
#   bash ashiato.sh record [分数]           # 録音開始
#   bash ashiato.sh transcribe [file]      # 文字起こし
#   bash ashiato.sh segment [mapped.csv]   # Stage1: 切片化のみ → evidence.json
#   bash ashiato.sh report [evidence.json] # Stage2: 報告書生成のみ
#   bash ashiato.sh run [分数]              # 録音→文字起こし→切片化→報告書 一括実行

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
  segment)
    # Stage1: 切片化のみ。evidence.json を生成して終了（人間が確認後にreportを実行）
    CSV_FILE="${1:?使い方: bash ashiato.sh segment <mapped.csv>}"
    python3 "${SCRIPT_DIR}/generate_report.py" "$CSV_FILE" --stage 1
    ;;
  report)
    # Stage2: evidence.json から報告書を生成
    EVIDENCE_FILE="${1:?使い方: bash ashiato.sh report <evidence_YYYYMMDD.json>}"
    python3 "${SCRIPT_DIR}/generate_report.py" --stage 2 --evidence "$EVIDENCE_FILE"
    ;;
  run)
    MINUTES="${1:-60}"
    echo "=== あしあと 一括実行 ==="
    echo "① 録音 (${MINUTES}分)"
    AUDIO_FILE=$(bash "${SCRIPT_DIR}/record.sh" "$MINUTES")
    echo "② 文字起こし"
    TRANSCRIPT_FILE=$(bash "${SCRIPT_DIR}/transcribe.sh" "$AUDIO_FILE")
    echo "③ 切片化 + 報告書生成（一括）"
    python3 "${SCRIPT_DIR}/generate_report.py" "$TRANSCRIPT_FILE"
    ;;
  help|*)
    echo "使い方:"
    echo "  bash ashiato.sh record [分数]             # 録音（デフォルト60分）"
    echo "  bash ashiato.sh transcribe [wavファイル]   # 文字起こし"
    echo "  bash ashiato.sh segment <mapped.csv>      # Stage1: 切片化 → evidence.json"
    echo "  bash ashiato.sh report <evidence.json>    # Stage2: 報告書生成"
    echo "  bash ashiato.sh run [分数]                # 全工程一括実行"
    echo ""
    echo "  ★ 推奨フロー（確認しながら）:"
    echo "     1. bash ashiato.sh segment mapped.csv  # 切片化して evidence.json を生成"
    echo "     2. (evidence.json を確認・必要なら修正)"
    echo "     3. bash ashiato.sh report evidence_YYYYMMDD.json  # 報告書生成"
    ;;
esac
