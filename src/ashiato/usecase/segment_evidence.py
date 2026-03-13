#!/usr/bin/env python3
"""
あしあとプロジェクト - Stage1 切片化スクリプト
入力: mapped.csv（話者マッピング済みCSV）
出力: evidence_YYYYMMDD.json
"""

import json
import logging
import argparse
import sys
from datetime import datetime
from pathlib import Path

from ashiato.config import MAX_SESSIONS
from ashiato.domain.viewpoints import VIEWPOINTS
from ashiato.core.agents.extractor import EvidenceExtractor
from ashiato.infra.csv_reader import load_csv

logger = logging.getLogger(__name__)


def load_meta_txt(path: str) -> dict:
    """map_speakers.py が生成する _meta.txt を読み込んで session_info dict に変換"""
    meta: dict = {}
    key_map = {"活動日": "date", "場所": "location", "活動内容": "activity", "学校種別": "school_type"}
    with open(path, encoding="utf-8") as f:
        for line in f:
            for jp, en in key_map.items():
                if line.startswith(jp + ": "):
                    meta[en] = line[len(jp) + 2:].strip()
    return meta


def get_children(rows: list[dict], supporter: str) -> list[str]:
    """支援者以外の話者を児童として返す"""
    speakers = set(r["speaker"] for r in rows)
    excludes = {supporter, "全員", "不明", "[不明]", ""}
    return sorted(s for s in speakers if s not in excludes and not s.startswith("["))


def build_transcript_per_child(rows: list[dict], child: str) -> str:
    """児童の発言と直前の支援者発言をセットで抽出（文脈つき）"""
    lines = []
    prev_supporter_line = ""
    for row in rows:
        if row["speaker"] not in ("全員", "") and not row["speaker"].startswith("["):
            if row["speaker"] != child:
                prev_supporter_line = f"  支援者: {row['text']}"
            else:
                if prev_supporter_line:
                    lines.append(prev_supporter_line)
                    prev_supporter_line = ""
                lines.append(f"  {child}: {row['text']}")
    return "\n".join(lines)


def build_full_transcript(rows: list[dict]) -> str:
    lines = []
    for row in rows:
        if row["text"] and row["text"] != "[聞き取り不明]":
            lines.append(f"[{row['start']}] {row['speaker']}: {row['text']}")
    return "\n".join(lines)


def extract_evidence_per_viewpoint(child: str, transcript: str, session_info: dict | None = None) -> dict[str, list[str]]:
    """Stage 1 - 切片化: 発言記録を観点別に分類し、根拠発言をそのまま抜き出す"""
    return EvidenceExtractor().run(child, transcript, session_info)


def _pick_representative_utterances(rows: list[dict], children: list[str], n: int = 2) -> str:
    """各児童の代表的な発言をn件ずつ抽出してテキスト化"""
    lines = []
    for child in children:
        utterances = [r["text"] for r in rows if r["speaker"] == child and r.get("text") and r["text"] != "[聞き取り不明]"]
        candidates = [u for u in utterances if len(u) >= 8]
        if not candidates:
            candidates = utterances
        step = max(1, len(candidates) // (n + 1))
        seen: set[str] = set()
        picks: list[str] = []
        for i in range(1, n + 1):
            u = candidates[min(i * step, len(candidates) - 1)]
            if u not in seen:
                seen.add(u)
                picks.append(u)
        for p in picks:
            lines.append(f"  {child}:「{p}」")
    return "\n".join(lines)


def save_evidence_json(
    evidence_by_child: dict[str, dict[str, list[str]]],
    session_info: dict,
    supporter: str,
    children: list[str],
    rows: list[dict],
    path: str,
) -> None:
    child_counts = {child: sum(1 for r in rows if r["speaker"] == child) for child in children}
    utterances_sample = _pick_representative_utterances(rows, children, n=4)
    data = {
        "schema_version": 1,
        "session_info": session_info,
        "supporter": supporter,
        "children": children,
        "child_counts": child_counts,
        "utterances_sample": utterances_sample,
        "evidence": evidence_by_child,
    }
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_evidence_json(path: str) -> tuple[dict, str, list[str], dict, str, dict[str, dict[str, list[str]]]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return (
        data["session_info"],
        data["supporter"],
        data["children"],
        data.get("child_counts", {}),
        data.get("utterances_sample", ""),
        data["evidence"],
    )


def _run_stage1(rows: list[dict], children: list[str], session_info: dict, supporter: str, date_slug: str) -> tuple[str, dict[str, dict[str, list[str]]]]:
    """切片化を実行してevidence.jsonに保存し、パスとevidence dictを返す"""
    evidence_by_child: dict[str, dict[str, list[str]]] = {}

    for i, child in enumerate(children, 1):
        child_count = sum(1 for r in rows if r["speaker"] == child)
        if child_count == 0:
            evidence_by_child[child] = {v: [] for v in VIEWPOINTS}
            continue

        transcript = build_transcript_per_child(rows, child)
        print(f"\n📋 [{i}/{len(children)}] {child}（{child_count}発言）: Stage1 切片化中...")
        evidence = extract_evidence_per_viewpoint(child, transcript, session_info)
        evidence_by_child[child] = evidence

        print(f"  ┌─ 切片化結果（確認してください）")
        for v in VIEWPOINTS:
            utterances = evidence[v]
            print(f"  │ 【{v}】{len(utterances)}件")
            for u in utterances:
                print(f"  │   ・{u}")
        print(f"  └─────────────────")

    evidence_path = f"evidence_{date_slug}.json"
    save_evidence_json(evidence_by_child, session_info, supporter, children, rows, evidence_path)
    print(f"\n💾 切片化結果を保存しました: {evidence_path}", file=sys.stderr)
    print(f"   → 内容を確認・修正後、Stage2を実行してください:", file=sys.stderr)
    print(f"   PYTHONPATH=src python3 src/ashiato/usecase/generate_report.py --evidence {evidence_path}", file=sys.stderr)
    return evidence_path, evidence_by_child


def main() -> None:
    parser = argparse.ArgumentParser(
        description="あしあと Stage1 切片化",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
実行例:
  PYTHONPATH=src python3 src/ashiato/usecase/segment_evidence.py mapped.csv
  PYTHONPATH=src python3 src/ashiato/usecase/segment_evidence.py mapped.csv --supporter 山田
""",
    )
    parser.add_argument("csv", help="入力CSVファイル（mapped_*.csv）")
    parser.add_argument("--date", default=datetime.today().strftime("%Y年%m月%d日"), help="セッション日付")
    parser.add_argument("--location", default="太子遊び冒険の森ASOBO（兵庫県揖保郡太子町）", help="活動場所")
    parser.add_argument("--activity", default="自然観察・昼食調理・火起こし体験", help="活動内容")
    parser.add_argument("--supporter", default="山田", help="支援者名（除外用）")
    parser.add_argument("--school-type", choices=["小学校", "中学校"], default="小学校", help="学校種別")
    args = parser.parse_args()

    date_slug = datetime.today().strftime("%Y%m%d")

    meta_path = str(Path(args.csv).with_suffix("")) + "_meta.txt"
    meta_from_file: dict = {}
    if Path(meta_path).exists():
        meta_from_file = load_meta_txt(meta_path)
        print(f"📋 メタ情報を読み込みました: {meta_path}")

    default_date        = datetime.today().strftime("%Y年%m月%d日")
    default_location    = "太子遊び冒険の森ASOBO（兵庫県揖保郡太子町）"
    default_activity    = "自然観察・昼食調理・火起こし体験"
    default_school_type = "小学校"

    session_info = {
        "date":        args.date        if args.date        != default_date        else meta_from_file.get("date",        default_date),
        "location":    args.location    if args.location    != default_location    else meta_from_file.get("location",    default_location),
        "activity":    args.activity    if args.activity    != default_activity    else meta_from_file.get("activity",    default_activity),
        "school_type": args.school_type if args.school_type != default_school_type else meta_from_file.get("school_type", default_school_type),
    }

    print(f"📂 読み込み中: {args.csv}")
    rows = load_csv(args.csv)
    children = get_children(rows, args.supporter)
    print(f"👶 検出された児童: {', '.join(children)}")
    print(f"📊 総発言数: {len(rows)}件\n")

    evidence_path, _ = _run_stage1(rows, children, session_info, args.supporter, date_slug)
    # 機械可読なパスを stdout に出力（シェルスクリプトが $(…) でキャプチャできる）
    print(evidence_path)


if __name__ == "__main__":
    main()
