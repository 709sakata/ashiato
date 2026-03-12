#!/bin/bash
# ashiato.sh - あしあとプロジェクト メインコマンド
# 使い方:
#   bash ashiato.sh record [分数]                  # 録音開始
#   bash ashiato.sh transcribe [file]             # 文字起こし
#   bash ashiato.sh segment [mapped.csv]          # Stage1: 切片化のみ → evidence.json
#   bash ashiato.sh report [evidence.json]        # Stage2: 報告書生成のみ
#   bash ashiato.sh store [evidence.json]         # DBに蓄積
#   bash ashiato.sh plan --init --child <名前>    # 個別支援計画の初回作成
#   bash ashiato.sh plan --update --child <名前>  # 個別支援計画の四半期更新
#   bash ashiato.sh run [分数]                     # 録音→文字起こし→切片化→報告書 一括実行

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB_PATH="${ASHIATO_DB:-${SCRIPT_DIR}/db/ashiato.db}"
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
    # Stage1: 切片化のみ
    CSV_FILE="${1:?使い方: bash ashiato.sh segment <mapped.csv>}"
    echo "Mac miniで切片化実行中..." >&2
    # Mac miniにCSVを転送
    scp -q "$CSV_FILE" "${MAC_MINI}:/tmp/mapped_to_segment.csv"
    ssh "$MAC_MINI" "cd ~/scripts/ashiato && python3 generate_report.py /tmp/mapped_to_segment.csv --stage 1"
    # 生成された evidence.json を取得
    scp -q "${MAC_MINI}:~/scripts/ashiato/evidence_*.json" ./
    ;;
  report)
    # Stage2: 報告書生成のみ（Mac miniで実行）
    EVIDENCE_FILE="${1:?使い方: bash ashiato.sh report <evidence_YYYYMMDD.json>}"
    echo "Mac miniで報告書生成中..." >&2
    scp -q "$EVIDENCE_FILE" "${MAC_MINI}:/tmp/evidence_to_report.json"
    ssh "$MAC_MINI" "cd ~/scripts/ashiato && python3 generate_report.py --stage 2 --evidence /tmp/evidence_to_report.json"
    # 生成された報告書（Markdown）を取得
    scp -q "${MAC_MINI}:~/scripts/ashiato/report_*.md" ./
    ;;
  store)
    # DBに蓄積（Mac miniで実行）
    EVIDENCE_FILE="${1:?使い方: bash ashiato.sh store <evidence_YYYYMMDD.json>}"
    echo "Mac miniのDBへ保存中..." >&2
    scp -q "$EVIDENCE_FILE" "${MAC_MINI}:/tmp/evidence_to_store.json"
    ssh "$MAC_MINI" "cd ~/scripts/ashiato && python3 store_session.py /tmp/evidence_to_store.json"
    ;;
  plan)
    # 個別支援計画を生成
    shift  # 'plan' を消費
    python3 "${SCRIPT_DIR}/generate_support_plan.py" "$@"
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
    echo "  bash ashiato.sh record [分数]                   # 録音（デフォルト60分）"
    echo "  bash ashiato.sh transcribe [wavファイル]          # 文字起こし"
    echo "  bash ashiato.sh segment <mapped.csv>             # Stage1: 切片化 → evidence.json"
    echo "  bash ashiato.sh report <evidence.json>           # Stage2: 報告書生成（DB参照自動）"
    echo "  bash ashiato.sh store <evidence.json>            # DBに蓄積（成長記録の永続化）"
    echo "  bash ashiato.sh plan --init --child <名前>       # 個別支援計画の初回作成"
    echo "  bash ashiato.sh plan --update --child <名前>     # 個別支援計画の四半期更新"
    echo "  bash ashiato.sh plan --show --child <名前>       # 現行計画の表示"
    echo "  bash ashiato.sh plan --list                      # 登録済み児童と計画状況"
    echo "  bash ashiato.sh run [分数]                       # 全工程一括実行"
    echo ""
    echo "  ★ 初回セットアップ（新しい児童）:"
    echo "     # 保護者面談の録音から作成（推奨）:"
    echo "     1. bash ashiato.sh transcribe 面談録音.mp3"
    echo "     2. python3 map_speakers.py output.csv         # 話者マッピング（保護者/本人/支援者）"
    echo "     3. bash ashiato.sh plan --init --child 太郎 --intake 面談_mapped.csv"
    echo "     # または対話式入力:"
    echo "     bash ashiato.sh plan --init --child 太郎"
    echo ""
    echo "  ★ 毎セッションのフロー:"
    echo "     1. bash ashiato.sh segment mapped.csv         # 切片化 → evidence.json (Mac miniで実行)"
    echo "     2. (evidence.json を確認・必要なら修正)"
    echo "     3. bash ashiato.sh report evidence_XXX.json   # 報告書生成 (Mac miniで実行)"
    echo "     4. bash ashiato.sh store evidence_XXX.json    # DBに蓄積 (Mac miniのDBへ)"
    echo ""
    echo "  ★ 四半期ごとの計画更新:"
    echo "     bash ashiato.sh plan --update --child 太郎"
    echo ""
    echo "  DB パス: ${DB_PATH}"
    echo "  （環境変数 ASHIATO_DB で変更可）"
    ;;
esac
