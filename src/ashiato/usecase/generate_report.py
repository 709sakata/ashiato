#!/usr/bin/env python3
"""
あしあとプロジェクト - Stage2 報告書生成スクリプト
入力: evidence_YYYYMMDD.json（Stage1切片化済み）
出力: report_校長向け_YYYYMMDD.md
"""

import logging
import re
import argparse
from datetime import datetime
from pathlib import Path

from ashiato.config import MAX_SESSIONS
from ashiato.domain.viewpoints import VIEWPOINTS
from ashiato.core.agents.reporter import ReportGenerator
from ashiato.core.services.child_context_service import load_context_for_report
from ashiato.usecase.segment_evidence import load_evidence_json

logger = logging.getLogger(__name__)


def build_context_section(child: str, context: dict) -> str:
    """過去履歴と支援計画をプロンプト用テキストに変換"""
    parts = []

    if context.get("plan_goals"):
        pg = context["plan_goals"]
        parts.append(f"# 個別支援計画（現行 / {pg['period']}）の目標")
        for vp, goal in pg["goals"].items():
            if goal:
                parts.append(f"  ■ {vp}: {goal}")
        parts.append("")

    if context.get("history"):
        parts.append(f"# {child}の過去セッション履歴（直近{len(context['history'])}件）")
        for s in context["history"]:
            count_str = " / ".join(
                f"{vp[:4]}:{s['counts'].get(vp, 0)}件" for vp in VIEWPOINTS
            )
            parts.append(f"  【{s['date']} {s['activity'][:20]}】{count_str}")
            for vp, sample in s.get("samples", {}).items():
                parts.append(f"    ・{vp[:4]}:「{sample}」")
        parts.append("")

    return "\n".join(parts)


def normalize_child_report(child: str, raw: str) -> str:
    """LLMの出力を強制的に ## 児童名 / ### 観点 形式に正規化する"""
    raw = re.sub(r'^#{1,6}\s*' + re.escape(child) + r'\s*$', f'## {child}', raw, flags=re.MULTILINE)

    for viewpoint in ["知識・技能", "思考・判断・表現", "主体的に学習に取り組む態度"]:
        raw = re.sub(r'^#{1,6}\s*' + re.escape(viewpoint) + r'\s*$', f'### {viewpoint}', raw, flags=re.MULTILINE)

    if not re.search(r'^## ' + re.escape(child), raw, flags=re.MULTILINE):
        raw = f'## {child}\n\n' + raw

    raw = re.sub(r'彼女(?=[はがのをにへもと])', child, raw)
    raw = re.sub(r'彼(?=[はがのをにへもと])', child, raw)

    return raw.strip()


def generate_child_report(
    child: str,
    evidence: dict[str, list[str]],
    session_info: dict,
    *,
    db_context: dict | None = None,
) -> str:
    """Stage 2 - 記述生成: 切片化済みの根拠発言リストをもとに観点別記述を作成"""
    raw = ReportGenerator().generate_child_report(
        child,
        evidence,
        session_info,
        db_context=db_context,
        build_context_section_fn=build_context_section,
    )
    return normalize_child_report(child, raw)


def generate_session_summary(
    children: list[str],
    session_info: dict,
    *,
    child_counts: dict[str, int] | None = None,
    utterances_sample: str | None = None,
) -> str:
    """セッション全体サマリーを生成。child_counts と utterances_sample が必要"""
    return ReportGenerator().generate_session_summary(
        children,
        session_info,
        child_counts=child_counts,
        utterances_sample=utterances_sample,
    )


def _run_stage2(
    evidence_by_child: dict[str, dict[str, list[str]]],
    children: list[str],
    session_info: dict,
    supporter: str,
    output_path: str,
    *,
    child_counts: dict[str, int] | None = None,
    utterances_sample: str | None = None,
    db_path: str | None = None,
) -> None:
    """evidence dictから報告書Markdownを生成して書き出す"""
    lines = []
    lines.append(f"# あしあと（太子遊び冒険の森ASOBO）活動報告書（校長向け）")
    lines.append(f"")
    lines.append(f"**日付**: {session_info['date']}  ")
    lines.append(f"**場所**: {session_info['location']}  ")
    lines.append(f"**活動**: {session_info['activity']}  ")
    lines.append(f"**参加児童**: {', '.join(children)}（{len(children)}名）  ")
    lines.append(f"**支援者**: {supporter}  ")
    lines.append(f"**記録生成日**: {datetime.today().strftime('%Y年%m月%d日')}  ")
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"")

    print("📝 セッション総括を生成中...")
    summary = generate_session_summary(
        children, session_info,
        child_counts=child_counts, utterances_sample=utterances_sample,
    )
    lines.append(f"## セッション総括")
    lines.append(f"")
    lines.append(summary)
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"")

    lines.append(f"## 観点別学習状況（児童別）")
    lines.append(f"")

    for i, child in enumerate(children, 1):
        evidence = evidence_by_child.get(child, {v: [] for v in VIEWPOINTS})
        evidence_count = sum(len(evidence[v]) for v in VIEWPOINTS)

        if evidence_count == 0:
            lines.append(f"### {child}")
            lines.append(f"※ 本セッションの記録から観点別の根拠発言を確認できなかった")
            lines.append(f"")
            continue

        db_context = None
        if db_path:
            db_context = load_context_for_report(child, exclude_date=session_info["date"])
            if db_context.get("plan_goals") or db_context.get("history"):
                history_count = len(db_context.get("history", []))
                has_plan = bool(db_context.get("plan_goals"))
                print(f"  📚 {child}: 過去{history_count}件の履歴{'・支援計画' if has_plan else ''}を参照")

        print(f"✍️  [{i}/{len(children)}] {child}: Stage2 記述生成中...")
        report = generate_child_report(child, evidence, session_info, db_context=db_context)
        lines.append(report)
        lines.append(f"")
        lines.append(f"---")
        lines.append(f"")

    lines.append(f"## 備考")
    lines.append(f"")
    lines.append(f"本報告書は、音声記録をWhisper（自動文字起こし）およびAI言語モデル（Ollama）で補助処理したものです。")
    lines.append(f"記述内容は担当支援者（{supporter}）が確認・承認したものを正式記録とします。")
    lines.append(f"")

    content = "\n".join(lines)
    Path(output_path).write_text(content, encoding="utf-8")
    print(f"\n✅ 完了: {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="あしあと Stage2 報告書生成",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
実行例:
  PYTHONPATH=src python3 src/ashiato/usecase/generate_report.py --evidence evidence_20261018.json
  PYTHONPATH=src python3 src/ashiato/usecase/generate_report.py --evidence evidence_20261018.json --db ./db
""",
    )
    parser.add_argument("--evidence", required=True, help="切片化結果JSONファイルのパス")
    parser.add_argument("--output", default=None, help="出力ファイルパス")
    parser.add_argument("--db", default=None, metavar="DB_PATH",
                        help="DBパスを指定すると過去履歴・支援計画を報告書に反映")
    args = parser.parse_args()

    date_slug = datetime.today().strftime("%Y%m%d")
    session_info, supporter, children, child_counts, utterances_sample, evidence_by_child = load_evidence_json(args.evidence)
    output_path = args.output or f"report_校長向け_{date_slug}.md"
    print(f"📂 切片化結果を読み込み中: {args.evidence}")
    _run_stage2(
        evidence_by_child, children, session_info, supporter, output_path,
        child_counts=child_counts, utterances_sample=utterances_sample,
        db_path=args.db,
    )


if __name__ == "__main__":
    main()
