#!/usr/bin/env python3
"""
あしあとプロジェクト - 個別支援計画生成

蓄積されたセッション横断データをもとに、児童ごとの個別支援計画を生成する。

使い方:
  python3 generate_support_plan.py --child 太郎
  python3 generate_support_plan.py --child 太郎 --sessions 6   # 直近6セッション分
  python3 generate_support_plan.py --list                      # 登録済み児童一覧
"""

import json
import sqlite3
import sys
import argparse
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime
from store_session import DEFAULT_DB, get_connection

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:7b"
VIEWPOINTS = ["知識・技能", "思考・判断・表現", "主体的に学習に取り組む態度"]

# ===== DBクエリ =====

def fetch_child_history(conn: sqlite3.Connection, child: str, max_sessions: int) -> list[dict]:
    """
    指定した児童の過去セッションデータを新しい順で取得する。
    戻り値: [{"date": ..., "activity": ..., "evidence": {"観点": ["発言", ...], ...}}, ...]
    """
    row = conn.execute("SELECT id FROM children WHERE name = ?", (child,)).fetchone()
    if not row:
        return []
    child_id = row["id"]

    sessions = conn.execute(
        """
        SELECT DISTINCT s.id, s.date, s.activity, s.location, s.school_type
        FROM sessions s
        JOIN session_evidence se ON se.session_id = s.id
        WHERE se.child_id = ?
        ORDER BY s.date DESC
        LIMIT ?
        """,
        (child_id, max_sessions),
    ).fetchall()

    history = []
    for s in sessions:
        utterances = conn.execute(
            """
            SELECT viewpoint, utterance
            FROM session_evidence
            WHERE session_id = ? AND child_id = ?
            ORDER BY viewpoint
            """,
            (s["id"], child_id),
        ).fetchall()

        evidence: dict[str, list[str]] = {v: [] for v in VIEWPOINTS}
        for u in utterances:
            if u["viewpoint"] in evidence:
                evidence[u["viewpoint"]].append(u["utterance"])

        history.append({
            "date": s["date"],
            "activity": s["activity"],
            "location": s["location"],
            "school_type": s["school_type"],
            "evidence": evidence,
        })

    return list(reversed(history))  # 古い順に並べ直す


def call_ollama(prompt: str, system: str = "", num_predict: int = 2000) -> str:
    body = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": num_predict, "repeat_penalty": 1.1},
    }
    if system:
        body["system"] = system

    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("response", "").strip()
    except urllib.error.URLError as e:
        print(f"[ERROR] Ollama接続失敗: {e}", file=sys.stderr)
        sys.exit(1)


# ===== 成長分析・計画生成 =====

def build_history_text(history: list[dict], child: str) -> str:
    """セッション履歴を時系列テキストに変換"""
    lines = []
    for i, s in enumerate(history, 1):
        total = sum(len(v) for v in s["evidence"].values())
        lines.append(f"### セッション{i}（{s['date']}）| {s['activity']}")
        for viewpoint in VIEWPOINTS:
            utterances = s["evidence"][viewpoint]
            if utterances:
                lines.append(f"  ■ {viewpoint}（{len(utterances)}件）")
                for u in utterances:
                    lines.append(f"    ・「{u}」")
        if total == 0:
            lines.append("  ※ 根拠発言なし")
        lines.append("")
    return "\n".join(lines)


def build_trend_summary(history: list[dict]) -> str:
    """観点別件数の推移をテキスト化"""
    lines = ["観点別根拠発言数の推移:"]
    header = "セッション        | " + " | ".join(f"{v[:4]}" for v in VIEWPOINTS)
    lines.append(header)
    lines.append("-" * len(header))
    for s in history:
        row = f"{s['date']} | " + " | ".join(
            f"{len(s['evidence'][v]):>4}件" for v in VIEWPOINTS
        )
        lines.append(row)
    return "\n".join(lines)


def generate_support_plan(child: str, history: list[dict], school_type: str) -> str:
    """過去セッション履歴から個別支援計画を生成"""
    history_text = build_history_text(history, child)
    trend_text = build_trend_summary(history)
    session_count = len(history)
    date_range = f"{history[0]['date']} ～ {history[-1]['date']}" if history else "—"

    system = (
        f"あなたは{school_type}の特別支援教育コーディネーターであり、"
        "フリースクール「あしあと」（太子遊び冒険の森ASOBO）での体験学習記録をもとに"
        "個別支援計画を作成する専門家である。"
        "提供されたセッション記録の根拠発言のみに基づいて記述し、"
        "記録に存在しない事実の創作・推測・補完は絶対に行わない。"
    )

    prompt = f"""# 個別支援計画 作成依頼

## 対象児童
名前: {child}
対象期間: {date_range}（{session_count}セッション分）
学校種別: {school_type}

## セッション横断の根拠発言記録（時系列）
{history_text}

## 観点別成長の数値推移
{trend_text}

---

# 作成する書類の構成

以下の構成で個別支援計画を作成してください。

## 1. 現状把握（アセスメント）
各観点について「記録から読み取れる現在の姿」を2〜3文で記述する。
- 数値推移（増減）を根拠として引用すること
- 具体的な発言を1〜2件引用して根拠を示すこと

## 2. セッションを通じた成長の特記事項
複数セッションをまたいで観察された変化・傾向を記述する（2〜4項目、箇条書き可）。
記録に変化が見られない場合は「継続して観察中」と記述する。

## 3. 支援目標（今後3ヶ月）
観点ごとに1つずつ、具体的・行動観察可能な目標を設定する。
例：「川遊びや火起こしの際に、自分から手順を言語化する場面を増やす」

## 4. 支援方針・手立て
目標達成のための具体的な支援アプローチを2〜4項目記述する。
体験学習（里山・野外活動）という場の特性を活かした内容にすること。

## 5. 連携・共有事項
学校・保護者と共有すべき観察事実を簡潔に記述する（1〜3項目）。

---

# 制約ルール
1. セッション記録に存在しない事実は記述しない（創作・推測禁止）
2. 人称は「{child}は」で統一し、「彼/彼女」は使わない
3. 根拠がない場合は「本記録期間では確認できなかった」と記述する
4. 前置き・後書きは不要、計画書の本文のみ出力する
"""

    return call_ollama(prompt, system=system, num_predict=3000)


# ===== メイン =====

def list_children(db_path: Path) -> None:
    if not db_path.exists():
        print("DBがまだ作成されていません。store_session.py でセッションを登録してください。")
        return
    conn = get_connection(db_path)
    children = conn.execute("""
        SELECT c.name, COUNT(DISTINCT se.session_id) as sessions, COUNT(se.id) as utterances,
               MIN(s.date) as first_date, MAX(s.date) as last_date
        FROM children c
        JOIN session_evidence se ON se.child_id = c.id
        JOIN sessions s ON s.id = se.session_id
        GROUP BY c.id
        ORDER BY c.name
    """).fetchall()
    conn.close()

    if not children:
        print("登録済みの児童はいません")
        return

    print(f"\n👶 登録済み児童: {len(children)}名\n")
    for c in children:
        print(f"  {c['name']}: {c['sessions']}セッション / 根拠発言 {c['utterances']}件 "
              f"（{c['first_date']} ～ {c['last_date']}）")


def main():
    parser = argparse.ArgumentParser(description="個別支援計画を生成する")
    parser.add_argument("--child", help="対象児童の名前")
    parser.add_argument("--sessions", type=int, default=12, help="使用する最大セッション数（デフォルト: 12）")
    parser.add_argument("--db", default=str(DEFAULT_DB), help=f"DBファイルのパス（デフォルト: {DEFAULT_DB}）")
    parser.add_argument("--output", default=None, help="出力ファイルパス（省略時は自動生成）")
    parser.add_argument("--list", action="store_true", help="登録済み児童の一覧を表示")
    args = parser.parse_args()

    db_path = Path(args.db)

    if args.list:
        list_children(db_path)
        return

    if not args.child:
        parser.print_help()
        sys.exit(1)

    if not db_path.exists():
        print(f"[ERROR] DBファイルが見つかりません: {db_path}")
        print("store_session.py でセッションを先に登録してください。")
        sys.exit(1)

    conn = get_connection(db_path)
    history = fetch_child_history(conn, args.child, args.sessions)
    conn.close()

    if not history:
        print(f"[ERROR] 「{args.child}」のデータがDBに存在しません。")
        print("python3 generate_support_plan.py --list  で登録済み児童を確認できます。")
        sys.exit(1)

    school_type = history[-1]["school_type"]
    print(f"📊 {args.child}のデータを読み込みました: {len(history)}セッション（{school_type}）")
    print(f"   期間: {history[0]['date']} ～ {history[-1]['date']}")
    for viewpoint in VIEWPOINTS:
        total = sum(len(s["evidence"][viewpoint]) for s in history)
        print(f"   {viewpoint}: 累計{total}件")

    print(f"\n✍️  個別支援計画を生成中...")
    plan = generate_support_plan(args.child, history, school_type)

    date_slug = datetime.today().strftime("%Y%m%d")
    output_path = args.output or f"support_plan_{args.child}_{date_slug}.md"

    header = "\n".join([
        f"# 個別支援計画 — {args.child}",
        f"",
        f"**作成日**: {datetime.today().strftime('%Y年%m月%d日')}  ",
        f"**対象期間**: {history[0]['date']} ～ {history[-1]['date']}（{len(history)}セッション）  ",
        f"**学校種別**: {school_type}  ",
        f"",
        f"---",
        f"",
    ])

    Path(output_path).write_text(header + plan + "\n", encoding="utf-8")
    print(f"\n✅ 完了: {output_path}")


if __name__ == "__main__":
    main()
