#!/usr/bin/env python3
"""
あしあとプロジェクト - セッションデータをDBに蓄積

使い方:
  python3 store_session.py evidence_20261018.json
  python3 store_session.py evidence_20261018.json --db ./db/ashiato.db

DBは初回実行時に自動作成される（デフォルト: ./db/ashiato.db）
"""

import json
import sqlite3
import sys
import argparse
from pathlib import Path
from datetime import datetime

from config import DEFAULT_DB

# ===== スキーマ定義 =====

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    location    TEXT NOT NULL,
    activity    TEXT NOT NULL,
    school_type TEXT NOT NULL DEFAULT '小学校',
    supporter   TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    UNIQUE(date, supporter)
);

CREATE TABLE IF NOT EXISTS children (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS session_evidence (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    child_id   INTEGER NOT NULL REFERENCES children(id),
    viewpoint  TEXT NOT NULL,
    utterance  TEXT NOT NULL
);

-- 個別支援計画（バージョン管理付き）
-- status: active（現行） / archived（過去版）
CREATE TABLE IF NOT EXISTS support_plans (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id     INTEGER NOT NULL REFERENCES children(id),
    version      INTEGER NOT NULL DEFAULT 1,
    period_start TEXT,
    period_end   TEXT,
    content      TEXT NOT NULL,  -- Markdown 本文
    goals_json   TEXT,           -- JSON: {"知識・技能": "目標...", ...} （報告書参照用）
    status       TEXT NOT NULL DEFAULT 'active',
    created_at   TEXT NOT NULL,
    UNIQUE(child_id, version)
);

CREATE INDEX IF NOT EXISTS idx_evidence_child ON session_evidence(child_id);
CREATE INDEX IF NOT EXISTS idx_evidence_session ON session_evidence(session_id);
CREATE INDEX IF NOT EXISTS idx_plans_child ON support_plans(child_id);
"""


def get_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def upsert_child(conn: sqlite3.Connection, name: str) -> int:
    conn.execute("INSERT OR IGNORE INTO children (name) VALUES (?)", (name,))
    return conn.execute("SELECT id FROM children WHERE name = ?", (name,)).fetchone()["id"]


def store(evidence_path: str, db_path: Path) -> None:
    data = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
    info = data["session_info"]
    supporter = data["supporter"]
    evidence_by_child: dict = data["evidence"]

    conn = get_connection(db_path)

    # セッションの重複チェック
    existing = conn.execute(
        "SELECT id FROM sessions WHERE date = ? AND supporter = ?",
        (info["date"], supporter)
    ).fetchone()
    if existing:
        print(f"⚠️  このセッションはすでにDBに存在します（date={info['date']}, supporter={supporter}）")
        print("   再インポートする場合は --force オプションを使用してください")
        conn.close()
        return

    # セッション登録
    cur = conn.execute(
        """INSERT INTO sessions (date, location, activity, school_type, supporter, imported_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            info["date"],
            info["location"],
            info["activity"],
            info.get("school_type", "小学校"),
            supporter,
            datetime.now().isoformat(),
        ),
    )
    session_id = cur.lastrowid

    total_utterances = 0
    for child, evidence in evidence_by_child.items():
        child_id = upsert_child(conn, child)
        for viewpoint, utterances in evidence.items():
            for utterance in utterances:
                if utterance:
                    conn.execute(
                        """INSERT INTO session_evidence (session_id, child_id, viewpoint, utterance)
                           VALUES (?, ?, ?, ?)""",
                        (session_id, child_id, viewpoint, utterance),
                    )
                    total_utterances += 1

    conn.commit()
    conn.close()

    print(f"✅ DBに保存しました: {db_path}")
    print(f"   セッション: {info['date']} / {info['activity']}")
    print(f"   児童: {', '.join(evidence_by_child.keys())}（{len(evidence_by_child)}名）")
    print(f"   根拠発言: {total_utterances}件")


def show_summary(db_path: Path) -> None:
    """蓄積状況を表示"""
    if not db_path.exists():
        print("DBがまだ作成されていません")
        return

    conn = get_connection(db_path)

    sessions = conn.execute("SELECT date, activity, supporter FROM sessions ORDER BY date").fetchall()
    print(f"\n📦 蓄積済みセッション: {len(sessions)}件")
    for s in sessions:
        print(f"  {s['date']} | {s['activity'][:30]} | 支援者: {s['supporter']}")

    children = conn.execute("""
        SELECT c.name, COUNT(DISTINCT se.session_id) as sessions, COUNT(se.id) as utterances
        FROM children c
        JOIN session_evidence se ON se.child_id = c.id
        GROUP BY c.id
        ORDER BY c.name
    """).fetchall()

    print(f"\n👶 登録済み児童: {len(children)}名")
    for c in children:
        print(f"  {c['name']}: {c['sessions']}セッション / 根拠発言 {c['utterances']}件")

    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="evidence.json をDBに蓄積")
    parser.add_argument("evidence", nargs="?", help="evidence_YYYYMMDD.json のパス")
    parser.add_argument("--db", default=str(DEFAULT_DB), help=f"DBファイルのパス（デフォルト: {DEFAULT_DB}）")
    parser.add_argument("--summary", action="store_true", help="蓄積状況を表示")
    parser.add_argument("--force", action="store_true", help="既存セッションを上書き（未実装）")
    args = parser.parse_args()

    db_path = Path(args.db)

    if args.summary:
        show_summary(db_path)
        return

    if not args.evidence:
        parser.print_help()
        sys.exit(1)

    store(args.evidence, db_path)


if __name__ == "__main__":
    main()
