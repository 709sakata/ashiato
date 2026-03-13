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

from config import MAX_SESSIONS
from domain.viewpoints import VIEWPOINTS
from infra.db import get_connection
from infra.llm import call_ollama
from usecase.segment_evidence import load_evidence_json

logger = logging.getLogger(__name__)


def load_child_context_from_db(child: str, exclude_date: str, max_sessions: int = MAX_SESSIONS) -> dict:
    """
    Supabase DBから児童の過去セッション履歴と現行支援計画を取得する。
    exclude_date: 今回のセッション日付（重複参照を避ける）
    戻り値: {"plan_goals": dict | None, "history": list[dict]}
    """
    try:
        conn = get_connection()
    except RuntimeError:
        return {"plan_goals": None, "history": []}

    child_row = conn.execute("SELECT id FROM children WHERE name = %s", (child,)).fetchone()
    if not child_row:
        conn.close()
        return {"plan_goals": None, "history": []}
    child_id = str(child_row["id"])

    plan_goals = None
    plan_row = conn.execute(
        "SELECT id, period_start, period_end FROM support_plans WHERE child_id = %s AND status = 'active' ORDER BY version DESC LIMIT 1",
        (child_id,),
    ).fetchone()
    if plan_row:
        goal_rows = conn.execute(
            """SELECT vp.code, spg.goal_text
               FROM support_plan_goals spg
               JOIN viewpoints vp ON vp.id = spg.viewpoint_id
               WHERE spg.support_plan_id = %s
               ORDER BY vp.sort_order""",
            (str(plan_row["id"]),),
        ).fetchall()
        if goal_rows:
            goals_dict = {r["code"]: r["goal_text"] for r in goal_rows}
            plan_goals = {
                "goals": goals_dict,
                "period": f"{plan_row['period_start']} ～ {plan_row['period_end']}",
            }

    sessions = conn.execute(
        """SELECT DISTINCT s.id, s.date,
                  COALESCE(at.name, s.activity_detail, '') AS activity
           FROM sessions s
           JOIN session_evidence se ON se.session_id = s.id
           LEFT JOIN activity_types at ON at.id = s.activity_type_id
           WHERE se.child_id = %s AND s.date::TEXT != %s
           ORDER BY s.date DESC LIMIT %s""",
        (child_id, exclude_date, max_sessions),
    ).fetchall()

    history = []
    for s in sessions:
        counts = conn.execute(
            """SELECT vp.code AS viewpoint, COUNT(*) AS cnt
               FROM session_evidence se
               JOIN viewpoints vp ON vp.id = se.viewpoint_id
               WHERE se.session_id = %s AND se.child_id = %s
               GROUP BY vp.code""",
            (str(s["id"]), child_id),
        ).fetchall()
        count_map = {r["viewpoint"]: r["cnt"] for r in counts}

        samples = {}
        for vp in VIEWPOINTS:
            row = conn.execute(
                """SELECT se.utterance FROM session_evidence se
                   JOIN viewpoints vp ON vp.id = se.viewpoint_id
                   WHERE se.session_id = %s AND se.child_id = %s AND vp.code = %s
                   LIMIT 1""",
                (str(s["id"]), child_id, vp),
            ).fetchone()
            if row:
                samples[vp] = row["utterance"]

        history.append({
            "date": str(s["date"]),
            "activity": s["activity"],
            "counts": count_map,
            "samples": samples,
        })

    conn.close()
    history.reverse()
    return {"plan_goals": plan_goals, "history": history}


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
    school_type = session_info.get("school_type", "小学校")

    system = (
        f"あなたは{school_type}指導要録の観点別学習状況記述を専門とする教育記録作成者である。"
        "提供された根拠発言リストのみを根拠に、支援者が観察した事実として記述する。"
        "リストに存在しない事実の創作・推測・補完は絶対に行わない。"
        "この記録は学校への出席扱い申請に使用される公式文書の下書きであり、"
        "教員が内容を確認・承認した上で正式記録となる。"
    )

    evidence_counts = {v: len(evidence[v]) for v in VIEWPOINTS}
    evidence_parts = []
    for v in VIEWPOINTS:
        utterances = evidence[v]
        evidence_parts.append(f"### {v}（{evidence_counts[v]}件）")
        if utterances:
            for u in utterances:
                evidence_parts.append(f"- 「{u}」")
        else:
            evidence_parts.append("- （根拠発言なし）")
    evidence_text = "\n".join(evidence_parts)

    context_section = ""
    continuity_instruction = ""
    if db_context and (db_context.get("plan_goals") or db_context.get("history")):
        context_section = "# 参考情報（記述の文脈として使用）\n" + build_context_section(child, db_context)
        continuity_instruction = (
            "8. 参考情報（過去履歴・支援計画）は記述の「文脈」として活用し、"
            "今回の根拠発言と自然につながる流れで書くこと。"
            "ただし参考情報の内容を根拠として使ってはならない（今回の根拠発言が唯一の根拠）。"
            "成長の継続が今回の発言からも確認できる場合は「引き続き」「継続して」等の表現を使ってよい。"
        )

    prompt = f"""# 前提条件（Context）
目的: NPO法人姫路YMCAが運営するフリースクール「あしあと」の太子遊び冒険の森ASOBO（兵庫県揖保郡太子町の里山）での体験セッションにおける{child}さんの根拠発言リスト（Stage1切片化済み）をもとに、
{school_type}への出席扱い申請用の指導要録「観点別学習状況」の記述を作成する。

# セッション情報
日付: {session_info['date']}
活動場所: {session_info['location']}
活動内容: {session_info['activity']}

{context_section}
# 観点別根拠発言リスト（今回のセッション・これのみが根拠）
{evidence_text}

# 記述例（Output: このスタイルで書くこと）
### 知識・技能（記述例）
{child}は「カブトムシはクヌギの木の汁を吸うんだって！」と発言し、
里山生物の生態について新たな知識を得た様子が見られた。
また「火起こし、できた！」と述べ、道具の扱いを習得する場面も確認された。
（根拠発言数: 2件）

# 思考ステップ（この順で実行すること）
1. 各観点の根拠発言リストを読み込む
2. 根拠発言から読み取れる事実（何をしたか・何と発言したか）を整理する
3. 各観点について2〜3文の記述を作成する
   - 1文目: 根拠発言に基づく具体的な行動・発言の事実（「〜と発言した」「〜に取り組んだ」）
   - 2文目: その事実から観察できること（「〜する様子が見られた」「〜が認められた」）
   - 3文目（任意）: 追加の根拠発言がある場合、補足として記述
4. 根拠発言が「なし」の観点は「本セッションの記録からは確認できなかった」と記述する
5. 全観点の記述が完了したら、出力前に以下を確認する
   【出力前チェックリスト（全て□をチェックしてから出力）】
   □ 根拠発言リストにない事実を記述していないか？
   □ 人称は「{child}は」で統一されているか？（「彼/彼女」禁止）
   □ 各観点末尾に（根拠発言数: N件）を括弧書きで記載したか？
   □ 前置き・後書きを出力していないか？
6. チェック完了後、成果物を出力する

# 制約ルール（すべて厳守する）
1. 根拠発言リストに存在しない事実は絶対に記述しない（創作・推測・補完禁止）
   NG例: 「{child}は楽しんでいた」（感情の推測であり、根拠発言なし）
2. 人称は「{child}は」で統一し、「彼は」「彼女は」は使わない
3. 支援者が実際に観察した事実として書く（「〜した」「〜と発言した」「〜と述べた」等）
4. 体言止めや箇条書きは使わず、文章で書く
5. 各観点の末尾に（根拠発言数: N件）を括弧書きで添える。Nは以下の確定値を使うこと:
   知識・技能: {evidence_counts['知識・技能']}件 / 思考・判断・表現: {evidence_counts['思考・判断・表現']}件 / 主体的に学習に取り組む態度: {evidence_counts['主体的に学習に取り組む態度']}件
6. 明らかな音声認識の変換誤り（例：固有名詞の当て字）は自然な語に修正してよいが、発言の意図・内容・ニュアンスを変える解釈は行わないこと
7. 前置きや後書きは出力しない
{continuity_instruction}

# 成果物のフォーマット（この通りに出力すること）

## {child}

### 知識・技能
（ここに記述）（根拠発言数: {evidence_counts['知識・技能']}件）

### 思考・判断・表現
（ここに記述）（根拠発言数: {evidence_counts['思考・判断・表現']}件）

### 主体的に学習に取り組む態度
（ここに記述）（根拠発言数: {evidence_counts['主体的に学習に取り組む態度']}件）
"""
    return normalize_child_report(child, call_ollama(
        prompt, system=system, num_predict=2000,
        extra_options={"repeat_penalty": 1.1, "top_k": 20},
    ))


def generate_session_summary(
    children: list[str],
    session_info: dict,
    *,
    child_counts: dict[str, int] | None = None,
    utterances_sample: str | None = None,
) -> str:
    """セッション全体サマリーを生成。child_counts と utterances_sample が必要"""
    stats_text = "\n".join(f"  {k}: {v}件" for k, v in (child_counts or {}).items())
    representative = utterances_sample or ""

    system = (
        "あなたはNPO法人姫路YMCAが運営するフリースクール「あしあと」（太子遊び冒険の森ASOBO）の公式記録補助者である。"
        "校長先生への活動報告書（出席扱い申請書添付用）の冒頭総括文を作成することを専門とする。"
        "与えられた【各児童の発言抜粋】に存在する事実のみを根拠に記述する。"
        "記録にない活動・様子・発言の創作・推測・補完は絶対に行わない。"
    )

    child_slot_lines = "\n".join(
        f"{c}は「（{c}の発言抜粋から1つ引用）」と発言するなど、（観察した事実を1文で）"
        for c in children
    )

    prompt = f"""以下は、NPO法人姫路YMCAが運営するフリースクール「あしあと」（太子遊び冒険の森ASOBO・兵庫県揖保郡太子町の里山）での体験セッションの記録です。

【セッション情報】
日付: {session_info['date']}
活動場所: {session_info['location']}
活動内容: {session_info['activity']}
参加児童: {', '.join(children)}（{len(children)}名）

【児童別発言件数】
{stats_text}

【各児童の発言抜粋（これが根拠となる唯一の記録）】
{representative}

【出力フォーマット（このテンプレートの全スロットを必ず埋めること）】
以下のテンプレートの（　）内を埋めて出力すること。スロットの順序・数は絶対に変えないこと。

{session_info['date']}、{session_info['location']}にて{session_info['activity']}を実施した。（活動全体の概況を補足する場合はここに続ける）
{child_slot_lines}
活動全体を通じ、（この体験の教育的意義・参加児童の様子を1文で締めくくる）

【制約ルール（必ず全て遵守すること）】
1. テンプレートの全スロットを埋めること（対象: {', '.join(children)} — 計{len(children)}名、一人も省略・統合禁止）
2. 【各児童の発言抜粋】に記載された内容のみを根拠に記述すること（創作・推測禁止）
3. 活動内容（{session_info['activity']}）の範囲内で述べること
4. 「不登校」という言葉は使わず「学校外での学びの場」として表現
5. 支援者が観察した事実として書くこと（「〜する様子が見られた」「〜と発言した」等）
6. 前置き・後書きは不要、総括の文章のみ出力すること
"""
    return call_ollama(prompt, system=system, num_predict=700)


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
            db_context = load_child_context_from_db(child, exclude_date=session_info["date"])
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
  PYTHONPATH=src python3 src/usecase/generate_report.py --evidence evidence_20261018.json
  PYTHONPATH=src python3 src/usecase/generate_report.py --evidence evidence_20261018.json --db ./db
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
