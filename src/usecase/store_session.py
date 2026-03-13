#!/usr/bin/env python3
"""
あしあとプロジェクト - セッションデータをSupabase DBに蓄積

使い方:
  PYTHONPATH=src python3 src/usecase/store_session.py evidence_20261018.json
  PYTHONPATH=src python3 src/usecase/store_session.py --summary

前提:
  - 環境変数 SUPABASE_DB_URL が設定されていること
  - migrations/001_initial_schema.sql を Supabase SQL Editor で適用済みであること
"""

import json
import logging
import sys
import argparse
from datetime import datetime
from pathlib import Path

from domain.viewpoints import VIEWPOINTS

logger = logging.getLogger(__name__)
from infra.db import Connection, get_connection


# =============================================================================
# マスタ upsert ヘルパー
# =============================================================================

def _upsert_by_name(conn: Connection, table: str, name: str) -> str:
    """name UNIQUE制約のあるテーブルに upsert して UUID を返す"""
    conn.execute(
        f"INSERT INTO {table} (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
        (name,),
    )
    row = conn.execute(f"SELECT id FROM {table} WHERE name = %s", (name,)).fetchone()
    return str(row["id"])


def upsert_location(conn: Connection, name: str) -> str:
    return _upsert_by_name(conn, "locations", name)


def upsert_activity_type(conn: Connection, name: str) -> str:
    return _upsert_by_name(conn, "activity_types", name)


def upsert_supporter(conn: Connection, name: str) -> str:
    return _upsert_by_name(conn, "supporters", name)


def upsert_child(conn: Connection, name: str) -> str:
    return _upsert_by_name(conn, "children", name)


def get_viewpoint_id(conn: Connection, code: str) -> str:
    row = conn.execute("SELECT id FROM viewpoints WHERE code = %s", (code,)).fetchone()
    if not row:
        raise ValueError(f"観点 '{code}' がDBに存在しません。migrations/001_initial_schema.sql を適用してください。")
    return str(row["id"])


# =============================================================================
# セッション保存
# =============================================================================

def store(evidence_path: str) -> None:
    data = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
    info = data["session_info"]
    supporter_name: str = data["supporter"]
    evidence_by_child: dict = data["evidence"]

    conn = get_connection()

    try:
        existing = conn.execute(
            """SELECT s.id FROM sessions s
               JOIN session_supporters ss ON ss.session_id = s.id
               JOIN supporters sup        ON sup.id = ss.supporter_id
               WHERE s.date = %s AND sup.name = %s
               LIMIT 1""",
            (info["date"], supporter_name),
        ).fetchone()

        if existing:
            logger.warning(
                "このセッションはすでにDBに存在します（date=%s, supporter=%s）。再インポートする場合は --force オプションを使用してください。",
                info['date'], supporter_name,
            )
            conn.close()
            return

        location_id      = upsert_location(conn, info["location"])
        activity_type_id = upsert_activity_type(conn, info["activity"])
        supporter_id     = upsert_supporter(conn, supporter_name)

        row = conn.execute(
            """INSERT INTO sessions (date, location_id, activity_type_id, imported_at)
               VALUES (%s, %s, %s, %s)
               RETURNING id""",
            (
                info["date"],
                location_id,
                activity_type_id,
                datetime.now().isoformat(),
            ),
        ).fetchone()
        session_id = str(row["id"])

        conn.execute(
            "INSERT INTO session_supporters (session_id, supporter_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (session_id, supporter_id),
        )

        viewpoint_ids = {vp: get_viewpoint_id(conn, vp) for vp in VIEWPOINTS}

        total_utterances = 0
        for child_name, evidence in evidence_by_child.items():
            child_id = upsert_child(conn, child_name)

            conn.execute(
                "INSERT INTO session_children (session_id, child_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (session_id, child_id),
            )

            for viewpoint, utterances in evidence.items():
                viewpoint_id = viewpoint_ids.get(viewpoint)
                if not viewpoint_id:
                    continue
                for utterance in utterances:
                    if utterance:
                        conn.execute(
                            """INSERT INTO session_evidence (session_id, child_id, viewpoint_id, utterance)
                               VALUES (%s, %s, %s, %s)""",
                            (session_id, child_id, viewpoint_id, utterance),
                        )
                        total_utterances += 1

        conn.commit()

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(f"✅ DBに保存しました（Supabase）")
    print(f"   セッション: {info['date']} / {info['activity']}")
    print(f"   児童: {', '.join(evidence_by_child.keys())}（{len(evidence_by_child)}名）")
    print(f"   根拠発言: {total_utterances}件")


# =============================================================================
# 蓄積状況サマリー
# =============================================================================

def show_summary() -> None:
    conn = get_connection()

    sessions = conn.execute(
        """SELECT s.date,
                  COALESCE(at.name, s.activity_detail, '（活動名なし）') AS activity,
                  STRING_AGG(DISTINCT sup.name, ', ')                    AS supporter
           FROM sessions s
           LEFT JOIN activity_types     at  ON at.id  = s.activity_type_id
           LEFT JOIN session_supporters ss  ON ss.session_id = s.id
           LEFT JOIN supporters         sup ON sup.id = ss.supporter_id
           GROUP BY s.id, s.date, at.name, s.activity_detail
           ORDER BY s.date"""
    ).fetchall()

    print(f"\n📦 蓄積済みセッション: {len(sessions)}件")
    for s in sessions:
        print(f"  {s['date']} | {str(s['activity'])[:30]} | 支援者: {s['supporter']}")

    children = conn.execute(
        """SELECT c.name,
                  COUNT(DISTINCT sc.session_id) AS sessions,
                  COUNT(se.id)                  AS utterances
           FROM children c
           JOIN session_children  sc ON sc.child_id  = c.id
           JOIN session_evidence  se ON se.child_id  = c.id
           GROUP BY c.id, c.name
           ORDER BY c.name"""
    ).fetchall()

    print(f"\n👶 登録済み児童: {len(children)}名")
    for c in children:
        print(f"  {c['name']}: {c['sessions']}セッション / 根拠発言 {c['utterances']}件")

    conn.close()


# =============================================================================
# エントリポイント
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="evidence.json をSupabase DBに蓄積")
    parser.add_argument("evidence", nargs="?", help="evidence_YYYYMMDD.json のパス")
    parser.add_argument("--summary", action="store_true", help="蓄積状況を表示")
    parser.add_argument("--force",   action="store_true", help="既存セッションを上書き（未実装）")
    args = parser.parse_args()

    if args.summary:
        show_summary()
        return

    if not args.evidence:
        parser.print_help()
        sys.exit(1)

    store(args.evidence)


if __name__ == "__main__":
    main()
