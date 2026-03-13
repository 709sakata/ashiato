"""
あしあとプロジェクト - 児童コンテキスト取得サービス

generate_report.py と manage_support_plan.py で共通して必要な
「DB から児童の過去セッション履歴・支援計画を取得する」ロジックを集約。
"""

import logging

from ashiato.config import MAX_SESSIONS
from ashiato.domain.viewpoints import VIEWPOINTS
from ashiato.infra.db import Connection, get_connection

logger = logging.getLogger(__name__)


def load_context_for_report(
    child: str,
    exclude_date: str,
    max_sessions: int = MAX_SESSIONS,
) -> dict:
    """
    報告書生成用: 児童の過去セッション履歴と現行支援計画を取得する。

    Args:
        child: 児童名
        exclude_date: 除外するセッション日付（今回分の重複参照を避ける）
        max_sessions: 取得する過去セッション数

    Returns:
        {"plan_goals": dict | None, "history": list[dict]}
        history の各要素: {"date", "activity", "counts", "samples"}
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


def load_history_for_plan(
    conn: Connection,
    child_id: str,
    max_sessions: int = MAX_SESSIONS,
) -> list[dict]:
    """
    支援計画更新用: 児童の過去セッション履歴を全発言付きで取得する。

    Args:
        conn: DB接続（呼び出し元で管理）
        child_id: 児童UUID
        max_sessions: 取得するセッション数

    Returns:
        list of {"date", "activity", "location", "school_type", "evidence": {viewpoint: [utterances]}}
    """
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
