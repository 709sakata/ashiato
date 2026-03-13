"""
あしあとプロジェクト - Supabase/PostgreSQL 接続ラッパー

環境変数:
  SUPABASE_DB_URL  PostgreSQL 接続文字列
                   例: postgresql://postgres:[PASSWORD]@db.[REF].supabase.co:5432/postgres

使い方:
  from ashiato.infra.db import get_connection
  conn = get_connection()
  row  = conn.execute("SELECT id FROM children WHERE name = %s", ("太郎",)).fetchone()
  rows = conn.execute("SELECT * FROM sessions ORDER BY date").fetchall()
"""

import psycopg2
import psycopg2.extras

from ashiato.config import SUPABASE_DB_URL


class _Cursor:
    """psycopg2 カーソルをラップし、lastrowid を RETURNING id で提供する"""

    def __init__(self, cur: psycopg2.extensions.cursor) -> None:
        self._cur = cur

    def fetchone(self) -> dict | None:
        return self._cur.fetchone()

    def fetchall(self) -> list[dict]:
        return self._cur.fetchall()

    @property
    def lastrowid(self) -> str | None:
        """最後の INSERT で RETURNING id した UUID を返す（fetchone 済みの場合は None）"""
        # RETURNING id を使った INSERT の場合、fetchone() で取得する。
        # lastrowid が必要なケースでは INSERT ... RETURNING id + fetchone()["id"] を使うこと。
        raise AttributeError(
            "psycopg2 では lastrowid は使用できません。"
            "INSERT ... RETURNING id を使用してください。"
        )


class Connection:
    """
    psycopg2 接続を sqlite3.Connection に近いインターフェースでラップする。

    主な違い:
    - プレースホルダは %s（sqlite3 の ? ではない）
    - INSERT の戻り値 ID は RETURNING id + fetchone()["id"] で取得
    - row_factory は RealDictCursor（row["column_name"] でアクセス可能）
    """

    def __init__(self, conn: psycopg2.extensions.connection) -> None:
        self._conn = conn

    def execute(self, sql: str, params: tuple = ()) -> _Cursor:
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        return _Cursor(cur)

    def executemany(self, sql: str, params_seq: list[tuple]) -> None:
        cur = self._conn.cursor()
        cur.executemany(sql, params_seq)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


def get_connection() -> Connection:
    """
    SUPABASE_DB_URL を使って PostgreSQL に接続し、Connection を返す。

    スキーマは migrations/001_initial_schema.sql を Supabase SQL Editor で
    事前に適用しておくこと。
    """
    if not SUPABASE_DB_URL:
        raise RuntimeError(
            "環境変数 SUPABASE_DB_URL が設定されていません。\n"
            "例: export SUPABASE_DB_URL='postgresql://postgres:[PASSWORD]"
            "@db.[PROJECT_REF].supabase.co:5432/postgres'"
        )

    raw = psycopg2.connect(SUPABASE_DB_URL)
    raw.autocommit = False
    return Connection(raw)
