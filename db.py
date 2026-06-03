"""
db.py

共用資料庫連線池模組
app_v2.py 與 admin_routes.py 都從這裡 import，避免重複定義 DB_CONFIG
"""

from psycopg2 import pool
from psycopg2.extras import RealDictCursor
import os
# ──────────────────────────────────────────
# DB 設定（只需維護這一份）
# ──────────────────────────────────────────
DB_CONFIG = {
    'host':     os.environ.get('PGHOST',     'localhost'),
    'port':     int(os.environ.get('PGPORT', 5432)),
    'database': os.environ.get('PGDATABASE', 'shadow'),
    'user':     os.environ.get('PGUSER',     'postgres'),
    'password': os.environ.get('PGPASSWORD', '123456'),
}
# minconn=1, maxconn=10，依伺服器負載調整
db_pool = pool.SimpleConnectionPool(1, 10, **DB_CONFIG)


def get_conn():
    """從 pool 取得連線"""
    return db_pool.getconn()


def release_conn(conn):
    """歸還連線到 pool"""
    if conn is not None:
        db_pool.putconn(conn)
