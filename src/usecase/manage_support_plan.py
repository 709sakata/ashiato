#!/usr/bin/env python3
"""
あしあとプロジェクト - 個別支援計画の作成・更新

個別支援計画は最初に作成し、四半期ごとに蓄積データをもとに更新する。

使い方:
  # 初回計画作成（保護者面談の文字起こしCSVから）
  PYTHONPATH=src python3 src/usecase/manage_support_plan.py --init --child 太郎 --intake intake_mapped.csv

  # 初回計画作成（対話式入力）
  PYTHONPATH=src python3 src/usecase/manage_support_plan.py --init --child 太郎

  # 四半期更新（蓄積セッションデータをもとに改定版を生成）
  PYTHONPATH=src python3 src/usecase/manage_support_plan.py --update --child 太郎

  # 現行計画の表示
  PYTHONPATH=src python3 src/usecase/manage_support_plan.py --show --child 太郎

  # 登録済み児童と計画状況の一覧
  PYTHONPATH=src python3 src/usecase/manage_support_plan.py --list
"""

import csv
import json
import logging
import sys
import argparse
from pathlib import Path
from datetime import datetime, date
from config import MAX_SESSIONS
from domain.viewpoints import VIEWPOINTS

logger = logging.getLogger(__name__)
from infra.db import Connection, get_connection
from usecase.store_session import upsert_child
from infra.llm import call_ollama


# =============================================================================
# DB操作
# =============================================================================

def get_active_plan(conn: Connection, child_id: str) -> dict | None:
    return conn.execute(
        "SELECT * FROM support_plans WHERE child_id = %s AND status = 'active' ORDER BY version DESC LIMIT 1",
        (child_id,)
    ).fetchone()


def archive_plan(conn: Connection, plan_id: str) -> None:
    conn.execute("UPDATE support_plans SET status = 'archived' WHERE id = %s", (plan_id,))


def save_plan(
    conn: Connection,
    child_id: str,
    version: int,
    content: str,
    goals_dict: dict,
    period_start: str,
    period_end: str,
) -> str:
    """支援計画を保存し、goals_dict を support_plan_goals テーブルに正規化して挿入する。UUID を返す。"""
    row = conn.execute(
        """INSERT INTO support_plans
           (child_id, version, period_start, period_end, content, status, created_at)
           VALUES (%s, %s, %s, %s, %s, 'active', %s)
           RETURNING id""",
        (child_id, version, period_start, period_end,
         content, datetime.now().isoformat()),
    ).fetchone()
    plan_id = str(row["id"])

    for sort_order, vp_code in enumerate(VIEWPOINTS):
        goal_text = goals_dict.get(vp_code, "")
        if not goal_text:
            continue
        vp_row = conn.execute("SELECT id FROM viewpoints WHERE code = %s", (vp_code,)).fetchone()
        if not vp_row:
            continue
        conn.execute(
            """INSERT INTO support_plan_goals (support_plan_id, viewpoint_id, goal_text, sort_order)
               VALUES (%s, %s, %s, %s)""",
            (plan_id, str(vp_row["id"]), goal_text, sort_order),
        )

    return plan_id


def get_plan_goals_dict(conn: Connection, plan_id: str) -> dict:
    """support_plan_goals から観点コード→目標テキストの dict を返す"""
    rows = conn.execute(
        """SELECT vp.code, spg.goal_text
           FROM support_plan_goals spg
           JOIN viewpoints vp ON vp.id = spg.viewpoint_id
           WHERE spg.support_plan_id = %s
           ORDER BY vp.sort_order""",
        (plan_id,),
    ).fetchall()
    return {r["code"]: r["goal_text"] for r in rows}


def _get_child_school_type(conn: Connection, child_id: str) -> str:
    """児童の学校種別コードを返す（未登録なら '小学校'）"""
    row = conn.execute(
        """SELECT COALESCE(st.code, '小学校') AS school_type
           FROM children c
           LEFT JOIN schools      sc ON sc.id = c.school_id
           LEFT JOIN school_types st ON st.id = sc.school_type_id
           WHERE c.id = %s""",
        (child_id,),
    ).fetchone()
    return row["school_type"] if row else "小学校"


def fetch_child_history(conn: Connection, child_id: str, max_sessions: int = MAX_SESSIONS) -> list[dict]:
    school_type = _get_child_school_type(conn, child_id)

    sessions = conn.execute(
        """SELECT DISTINCT s.id, s.date,
                  COALESCE(at.name, s.activity_detail, '') AS activity,
                  COALESCE(l.name, '')                     AS location
           FROM sessions s
           JOIN session_evidence se ON se.session_id = s.id
           LEFT JOIN activity_types at ON at.id = s.activity_type_id
           LEFT JOIN locations      l  ON l.id  = s.location_id
           WHERE se.child_id = %s
           ORDER BY s.date DESC LIMIT %s""",
        (child_id, max_sessions),
    ).fetchall()

    history = []
    for s in sessions:
        utterances = conn.execute(
            """SELECT vp.code AS viewpoint, se.utterance
               FROM session_evidence se
               JOIN viewpoints vp ON vp.id = se.viewpoint_id
               WHERE se.session_id = %s AND se.child_id = %s""",
            (str(s["id"]), child_id),
        ).fetchall()
        evidence: dict[str, list[str]] = {v: [] for v in VIEWPOINTS}
        for u in utterances:
            if u["viewpoint"] in evidence:
                evidence[u["viewpoint"]].append(u["utterance"])
        history.append({
            "date": str(s["date"]),
            "activity": s["activity"],
            "location": s["location"],
            "school_type": school_type,
            "evidence": evidence,
        })
    return list(reversed(history))


def load_intake_csv(path: str) -> tuple[list[str], str]:
    """保護者面談の文字起こしCSVを読み込む。
    戻り値: (話者リスト, 全発言テキスト)
    """
    rows: list[dict] = []
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    speakers = list(dict.fromkeys(r["speaker"] for r in rows if r.get("speaker")))
    lines = [
        f"{r['speaker']}: {r['text']}"
        for r in rows
        if r.get("text") and r["text"] != "[聞き取り不明]"
    ]
    return speakers, "\n".join(lines)


def extract_goals_json(content: str) -> dict[str, str]:
    """生成された計画書から観点別目標を抽出してdict化（ベストエフォート）。

    各観点の見出し行（# を含む行）を探し、直後の非空行を目標テキストとして採用する。
    抽出できなかった観点は空文字で補完する。
    """
    goals: dict[str, str] = {}
    lines = content.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        for vp in VIEWPOINTS:
            if vp in stripped:
                for j in range(i + 1, min(i + 6, len(lines))):
                    candidate = lines[j].strip().lstrip("・- ")
                    if candidate and not candidate.startswith("#"):
                        goals[vp] = candidate
                        break
                break
    for vp in VIEWPOINTS:
        goals.setdefault(vp, "")
    return goals


# =============================================================================
# 初回計画作成
# =============================================================================

def _build_init_prompt(child: str, school_type: str, period_start: str, period_end: str, info_section: str) -> str:
    return f"""# 個別支援計画（初回）作成依頼

## 対象児童
名前: {child}
学校種別: {school_type}
計画期間: {period_start} ～ {period_end}

{info_section}

---

# 作成する計画書の構成

以下の構成で個別支援計画書を作成してください。

## 1. 現状把握（アセスメント）
提供された情報をもとに、{child}の現在の姿を2〜3文で整理する。

## 2. 支援目標（{period_start} ～ {period_end}）

### 知識・技能
（具体的な到達目標を1〜2文で）

### 思考・判断・表現
（具体的な到達目標を1〜2文で）

### 主体的に学習に取り組む態度
（具体的な到達目標を1〜2文で）

## 3. 支援方針・手立て
（2〜4項目。里山・野外体験という場の特性を活かした内容で）

## 4. 連携・共有事項
（学校・保護者と共有すべき観察の視点を2〜3項目）

## 5. 評価の観点（セッションごとの観察ポイント）
（毎回の記録で何を見るかを2〜3項目。報告書作成時の指針となる）

---

# 制約ルール
1. 人称は「{child}は」で統一し、「彼/彼女」は使わない
2. 提供された情報に根拠のない事実を補完しない
3. 前置き・後書きは不要、計画書の本文のみ出力する
"""


def _save_and_write(
    conn: Connection,
    child_id: str,
    existing: dict | None,
    content: str,
    goals_dict: dict,
    period_start: str,
    period_end: str,
    child: str,
    school_type: str,
    source_note: str = "",
) -> None:
    if existing:
        archive_plan(conn, str(existing["id"]))
    new_version = (existing["version"] + 1) if existing else 1
    save_plan(conn, child_id, new_version, content, goals_dict, period_start, period_end)
    conn.commit()

    date_slug = datetime.today().strftime("%Y%m%d")
    out = Path(f"support_plan_{child}_v{new_version}_{date_slug}.md")
    header_lines = [
        f"# 個別支援計画 — {child}（v{new_version}）",
        f"",
        f"**作成日**: {datetime.today().strftime('%Y年%m月%d日')}  ",
        f"**計画期間**: {period_start} ～ {period_end}  ",
        f"**学校種別**: {school_type}  ",
    ]
    if source_note:
        header_lines.append(f"**作成根拠**: {source_note}  ")
    header_lines += ["", "---", ""]
    out.write_text("\n".join(header_lines) + "\n" + content + "\n", encoding="utf-8")
    print(f"\n✅ 完了: {out}")
    print(f"   DBに保存しました（v{new_version}）")


def cmd_init(
    conn: Connection,
    child: str,
    school_type: str,
    intake_csv: str | None = None,
) -> None:
    child_id = upsert_child(conn, child)
    existing = get_active_plan(conn, child_id)
    if existing:
        print(f"⚠️  {child}の個別支援計画はすでに存在します（v{existing['version']}）。")
        ans = input("新規作成して既存をアーカイブしますか？（y/N）: ").strip().lower()
        if ans not in ("y", "yes"):
            print("中断しました。更新の場合は --update を使用してください。")
            return

    system = (
        f"あなたは{school_type}の特別支援教育コーディネーターであり、"
        "フリースクール「あしあと」（太子遊び冒険の森ASOBO）での体験学習を通じた"
        "個別支援計画の作成専門家である。"
        "提供された情報のみに基づき、具体的・行動観察可能な計画書を作成する。"
    )

    if intake_csv:
        print(f"\n{'─'*50}")
        print(f"  {child} の個別支援計画 — 面談記録から初回作成")
        print(f"{'─'*50}")
        print(f"📂 面談記録を読み込み中: {intake_csv}")
        speakers, transcript_text = load_intake_csv(intake_csv)
        print(f"   話者: {', '.join(speakers)}（{len(speakers)}名）")
        print(f"   発言数: {transcript_text.count(chr(10)) + 1}行")

        period_start = input(f"\n計画期間（開始）（例：{datetime.today().strftime('%Y年%m月')}）: ").strip() \
                       or datetime.today().strftime("%Y年%m月")
        period_end   = input(f"計画期間（終了）（例：3ヶ月後）: ").strip()

        supporter_name = input(f"\n支援者の名前（面談記録内の表記）: ").strip()
        child_name_in_csv = input(
            f"面談記録内での{child}の呼び方（例: 太郎、本人、お子さん / Enterで「{child}」）: "
        ).strip() or child

        info_section = f"""## 面談記録（保護者・本人・支援者）
話者: {', '.join(speakers)}
支援者: {supporter_name}
対象: {child_name_in_csv}

### 発言記録（全文）
{transcript_text}

---

# 抽出指示（計画書作成前に実行すること）
以下の順で面談記録を分析してから計画書を作成すること:
1. {child_name_in_csv}についての発言・様子を通読する
2. 現状・背景として語られた内容を抽出する（学校との関係、フリースクールに来た経緯、日常の様子等）
3. 得意なこと・関心の高い領域として語られた内容を抽出する
4. 課題・支援が必要な領域として語られた内容を抽出する
5. 保護者・本人が希望・期待として述べた内容を抽出する
6. 抽出した内容のみを根拠に計画書を作成する（記録にない情報の補完・創作禁止）"""

        source_note = f"保護者面談記録（{Path(intake_csv).name}）"

    else:
        print(f"\n{'─'*50}")
        print(f"  {child} の個別支援計画 — 対話式初回作成")
        print(f"{'─'*50}")
        print("（Enterで項目をスキップできます）\n")

        background     = input("現在の状況・背景（学校との関係、フリースクールに来た経緯など）:\n> ").strip()
        strengths      = input("\n得意なこと・関心が高い領域:\n> ").strip()
        challenges     = input("\n苦手なこと・支援が必要な領域:\n> ").strip()
        goal_kn        = input("\n【知識・技能】の目標:\n> ").strip()
        goal_th        = input("\n【思考・判断・表現】の目標:\n> ").strip()
        goal_at        = input("\n【主体的に学習に取り組む態度】の目標:\n> ").strip()
        support_policy = input("\n支援の方針・手立て:\n> ").strip()
        period_start   = input(f"\n計画期間（開始）（例：{datetime.today().strftime('%Y年%m月')}）: ").strip() \
                         or datetime.today().strftime("%Y年%m月")
        period_end     = input(f"計画期間（終了）: ").strip()

        info_section = f"""## 提供された情報
### 現在の状況・背景
{background or "（未入力）"}

### 得意なこと・関心が高い領域
{strengths or "（未入力）"}

### 苦手なこと・支援が必要な領域
{challenges or "（未入力）"}

### 設定目標
- 【知識・技能】: {goal_kn or "（未入力）"}
- 【思考・判断・表現】: {goal_th or "（未入力）"}
- 【主体的に学習に取り組む態度】: {goal_at or "（未入力）"}

### 支援の方針・手立て
{support_policy or "（未入力）"}"""

        source_note = "対話式入力"

    print(f"\n✍️  個別支援計画を生成中...")
    prompt = _build_init_prompt(child, school_type, period_start, period_end, info_section)
    content = call_ollama(prompt, system=system, num_predict=3000, extra_options={"repeat_penalty": 1.1})

    goals_dict = extract_goals_json(content)
    _save_and_write(
        conn, child_id, existing, content, goals_dict,
        period_start, period_end, child, school_type, source_note,
    )


# =============================================================================
# 四半期更新
# =============================================================================

def cmd_update(conn: Connection, child: str, max_sessions: int) -> None:
    row = conn.execute("SELECT id FROM children WHERE name = %s", (child,)).fetchone()
    if not row:
        logger.error("「%s」はDBに存在しません。--list で確認してください。", child)
        sys.exit(1)
    child_id = str(row["id"])

    current_plan = get_active_plan(conn, child_id)
    if not current_plan:
        logger.error("%sの個別支援計画がありません。先に --init で作成してください。", child)
        sys.exit(1)

    history = fetch_child_history(conn, child_id, max_sessions)
    if not history:
        logger.error("%sのセッションデータがDBにありません。store_session.py で蓄積してください。", child)
        sys.exit(1)

    school_type = history[-1]["school_type"]
    date_range = f"{history[0]['date']} ～ {history[-1]['date']}"
    session_count = len(history)

    trend_lines = ["観点別根拠発言数の推移:"]
    for s in history:
        row_text = f"  {s['date']} | " + " / ".join(
            f"{v[:4]}:{len(s['evidence'][v])}件" for v in VIEWPOINTS
        )
        trend_lines.append(row_text)
    trend_text = "\n".join(trend_lines)

    history_lines = []
    for i, s in enumerate(history, 1):
        history_lines.append(f"### セッション{i}（{s['date']}）{s['activity']}")
        for vp in VIEWPOINTS:
            utts = s["evidence"][vp]
            if utts:
                history_lines.append(f"  ■ {vp}（{len(utts)}件）")
                for u in utts[:3]:
                    history_lines.append(f"    ・「{u}」")
        history_lines.append("")
    history_text = "\n".join(history_lines)

    print(f"📊 {child}のデータを読み込みました: {session_count}セッション")
    print(f"   期間: {date_range}")
    print(f"   現行計画: v{current_plan['version']}（{current_plan['period_start']} ～ {current_plan['period_end']}）")

    new_period_start = input(f"\n新しい計画期間（開始）（例：{datetime.today().strftime('%Y年%m月')}）: ").strip() or datetime.today().strftime("%Y年%m月")
    new_period_end   = input(f"新しい計画期間（終了）（例：3ヶ月後）: ").strip()

    print(f"\n✍️  個別支援計画を更新中...")

    system = (
        f"あなたは{school_type}の特別支援教育コーディネーターであり、"
        "フリースクール「あしあと」の体験学習記録をもとに"
        "個別支援計画を定期的に改定する専門家である。"
        "記録にある発言のみを根拠に成長を評価し、次期目標を設定する。"
    )

    prompt = f"""# 個別支援計画 四半期改定依頼

## 対象児童
名前: {child}
学校種別: {school_type}
評価期間: {date_range}（{session_count}セッション）
新計画期間: {new_period_start} ～ {new_period_end}

## 現行計画（v{current_plan['version']}）
{current_plan['content']}

---

## 今期のセッション記録（根拠発言サンプル）
{history_text}

## 観点別成長の数値推移
{trend_text}

---

# 作成する改定計画書の構成

## 1. 今期の評価（振り返り）
現行計画の目標に対して観察されたことを観点ごとに評価する。
- 根拠発言の数値推移と具体的発言を引用して評価すること
- 目標に対して「達成された・進展が見られた・継続課題」の判断を示す

## 2. 支援目標（{new_period_start} ～ {new_period_end}）

### 知識・技能
（今期の評価を踏まえた次期目標を1〜2文で）

### 思考・判断・表現
（今期の評価を踏まえた次期目標を1〜2文で）

### 主体的に学習に取り組む態度
（今期の評価を踏まえた次期目標を1〜2文で）

## 3. 支援方針・手立て（次期）
（今期の観察をもとに調整した支援アプローチ、2〜4項目）

## 4. 連携・共有事項
（学校・保護者と共有すべき今期の成長・次期の方針、2〜3項目）

## 5. 評価の観点（次期のセッションで見るポイント）

---

# 制約ルール
1. 人称は「{child}は」で統一し、「彼/彼女」は使わない
2. セッション記録に根拠のない事実は記述しない
3. 前置き・後書きは不要、計画書の本文のみ出力する
"""

    content = call_ollama(prompt, system=system, num_predict=3000, extra_options={"repeat_penalty": 1.1})

    new_version = current_plan["version"] + 1
    archive_plan(conn, str(current_plan["id"]))
    goals_dict = extract_goals_json(content)
    save_plan(conn, child_id, new_version, content, goals_dict, new_period_start, new_period_end)
    conn.commit()

    date_slug = datetime.today().strftime("%Y%m%d")
    out = Path(f"support_plan_{child}_v{new_version}_{date_slug}.md")
    header = "\n".join([
        f"# 個別支援計画 — {child}（v{new_version}）",
        f"",
        f"**作成日**: {datetime.today().strftime('%Y年%m月%d日')}  ",
        f"**計画期間**: {new_period_start} ～ {new_period_end}  ",
        f"**前バージョン**: v{current_plan['version']}  ",
        f"**学校種別**: {school_type}  ",
        f"",
        f"---",
        f"",
    ])
    out.write_text(header + content + "\n", encoding="utf-8")
    print(f"\n✅ 完了: {out}")
    print(f"   DBに保存しました（v{new_version}、旧v{current_plan['version']}はアーカイブ）")


# =============================================================================
# 表示・一覧
# =============================================================================

def cmd_show(conn: Connection, child: str) -> None:
    row = conn.execute("SELECT id FROM children WHERE name = %s", (child,)).fetchone()
    if not row:
        logger.error("「%s」はDBに存在しません。", child)
        sys.exit(1)
    plan = get_active_plan(conn, str(row["id"]))
    if not plan:
        print(f"{child}の個別支援計画はまだ作成されていません。--init で作成してください。")
        return
    print(f"\n{'─'*60}")
    print(f"  {child} の個別支援計画（v{plan['version']} / {plan['period_start']} ～ {plan['period_end']}）")
    print(f"{'─'*60}\n")
    print(plan["content"])


def cmd_list(conn: Connection) -> None:
    children = conn.execute(
        """SELECT c.name,
                  COUNT(DISTINCT sc.session_id) AS sessions,
                  COUNT(se.id)                  AS utterances,
                  MIN(s.date::TEXT)              AS first_date,
                  MAX(s.date::TEXT)              AS last_date
           FROM children c
           LEFT JOIN session_children sc ON sc.child_id  = c.id
           LEFT JOIN session_evidence se ON se.child_id  = c.id
           LEFT JOIN sessions         s  ON s.id         = sc.session_id
           GROUP BY c.id, c.name
           ORDER BY c.name"""
    ).fetchall()

    if not children:
        print("登録済みの児童はいません")
        return

    print(f"\n👶 登録済み児童: {len(children)}名\n")
    for c in children:
        child_row = conn.execute("SELECT id FROM children WHERE name = %s", (c["name"],)).fetchone()
        plan = get_active_plan(conn, str(child_row["id"]))
        plan_info = f"計画v{plan['version']}（{plan['period_start']}〜）" if plan else "計画未作成"
        print(f"  {c['name']}: {c['sessions'] or 0}セッション / 根拠発言 {c['utterances'] or 0}件 | {plan_info}")
        if c["first_date"]:
            print(f"    記録期間: {c['first_date']} ～ {c['last_date']}")


# =============================================================================
# メイン
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="個別支援計画の作成・更新")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--init",   action="store_true", help="初回計画を作成（対話式）")
    group.add_argument("--update", action="store_true", help="四半期更新（蓄積データをもとに改定）")
    group.add_argument("--show",   action="store_true", help="現行計画を表示")
    group.add_argument("--list",   action="store_true", help="登録済み児童と計画状況を一覧表示")
    parser.add_argument("--child", help="対象児童の名前（--list 以外で必須）")
    parser.add_argument("--intake", default=None, metavar="MAPPED_CSV",
                        help="--init 専用: 保護者面談の文字起こしCSV（map_speakers.py 出力）を指定すると自動抽出")
    parser.add_argument("--school-type", choices=["小学校", "中学校"], default="小学校")
    parser.add_argument("--sessions", type=int, default=12, help="更新時に参照するセッション数（デフォルト: 12）")
    parser.add_argument("--db", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()

    conn = get_connection()

    if args.list:
        cmd_list(conn)
        conn.close()
        return

    if not args.child:
        parser.error("--child <名前> が必要です")

    if args.init:
        if args.intake and not Path(args.intake).exists():
            parser.error(f"--intake で指定したファイルが見つかりません: {args.intake}")
        cmd_init(conn, args.child, args.school_type, intake_csv=args.intake)
    elif args.update:
        cmd_update(conn, args.child, args.sessions)
    elif args.show:
        cmd_show(conn, args.child)

    conn.close()


if __name__ == "__main__":
    main()
