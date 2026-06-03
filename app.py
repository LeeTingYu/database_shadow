"""
陰影導航後端 API v2（修正版）
修正項目：
  1. DB 連線池改從 db.py 統一管理，移除重複的 DB_CONFIG
  2. 使用 flask.g + teardown_appcontext 自動歸還連線
  3. pgr_dijkstra SQL 字串直接用 f-string 嵌入
  4. 外層 JOIN edge_shadow 加上 sun_id 子查詢，避免多列問題
  5. 移除不存在的 shadow_score，改用 cost/length 換算 shadow_ratio
  6. bbox padding 動態計算
  7. admin_routes import 加上 try/except 保護

執行方式：python app_v2.py
"""

from flask import Flask, request, jsonify, g
from flask_cors import CORS
from psycopg2.extras import RealDictCursor
from datetime import datetime

from db import get_conn, release_conn   # ← 統一使用共用 pool

# ──────────────────────────────────────────
# App 初始化
# ──────────────────────────────────────────
app = Flask(__name__)
CORS(app)

try:
    from admin_routes import admin_bp
    app.register_blueprint(admin_bp)
    print("✅ admin_routes 載入成功")
except ImportError:
    print("⚠️  admin_routes 不存在，略過")


# ──────────────────────────────────────────
# DB 連線：綁定到 flask.g，request 結束自動歸還
# ──────────────────────────────────────────
def get_db_connection():
    if "db_conn" not in g:
        g.db_conn = get_conn()
    return g.db_conn


@app.teardown_appcontext
def release_db_connection(exception=None):
    conn = g.pop("db_conn", None)
    release_conn(conn)


# ──────────────────────────────────────────
# 時間工具
# ──────────────────────────────────────────
TARGET_DATE = "2026-06-21"
HOUR_MIN    = 9
HOUR_MAX    = 17


def get_current_hour() -> int:
    now  = datetime.now()
    hour = now.hour + (1 if now.minute >= 30 else 0)
    return max(HOUR_MIN, min(HOUR_MAX, hour))


# ──────────────────────────────────────────
# 路由
# ──────────────────────────────────────────
@app.route("/")
def home():
    return jsonify({
        "status":       "ok",
        "message":      "陰影導航 API v2",
        "current_hour": get_current_hour(),
    })


@app.route("/api/current-time", methods=["GET"])
def get_current_time():
    now  = datetime.now()
    hour = get_current_hour()
    return jsonify({
        "status": "success",
        "data": {
            "current_time": now.strftime("%H:%M"),
            "shade_hour":   hour,
            "date":         TARGET_DATE,
        },
    })


@app.route("/api/stats", methods=["GET"])
def get_stats():
    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT
                (SELECT COUNT(*) FROM region)      AS regions,
                (SELECT COUNT(*) FROM tree)        AS trees,
                (SELECT COUNT(*) FROM node)        AS nodes,
                (SELECT COUNT(*) FROM edge)        AS edges,
                (SELECT COUNT(*) FROM sun)         AS sun_records,
                (SELECT COUNT(*) FROM edge_shadow) AS edge_shadow_records
        """)
        stats = cur.fetchone()
        cur.close()
        return jsonify({"status": "success", "data": stats})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/route", methods=["POST"])
def calculate_route():
    try:
        data = request.json

        start_lat = float(data["start"]["lat"])
        start_lng = float(data["start"]["lng"])
        end_lat   = float(data["end"]["lat"])
        end_lng   = float(data["end"]["lng"])

        hour        = int(data.get("hour", get_current_hour()))
        target_date = data.get("date", TARGET_DATE)
        hour        = max(HOUR_MIN, min(HOUR_MAX, hour))

        print(f"計算路徑：({start_lat}, {start_lng}) → ({end_lat}, {end_lng})"
              f"  日期：{target_date}  時段：{hour}:00")

        # ── 動態 bbox padding ─────────────────────────────────────────
        lat_span = abs(start_lat - end_lat)
        lng_span = abs(start_lng - end_lng)
        padding  = max(0.01, max(lat_span, lng_span) * 0.2)

        bbox_min_lng = min(start_lng, end_lng) - padding
        bbox_max_lng = max(start_lng, end_lng) + padding
        bbox_min_lat = min(start_lat, end_lat) - padding
        bbox_max_lat = max(start_lat, end_lat) + padding

        # ── pgr edge SQL（f-string 直接嵌入，不透過 psycopg2 傳參）──
        pgr_edge_sql = f"""
            SELECT
                e.edge_id                   AS id,
                e.source,
                e.target,
                COALESCE(es.cost, e.length) AS cost
            FROM edge e
            JOIN node n_src ON e.source = n_src.node_id
                AND n_src.geom && ST_MakeEnvelope(
                    {bbox_min_lng}, {bbox_min_lat},
                    {bbox_max_lng}, {bbox_max_lat}, 4326
                )
            JOIN node n_tgt ON e.target = n_tgt.node_id
                AND n_tgt.geom && ST_MakeEnvelope(
                    {bbox_min_lng}, {bbox_min_lat},
                    {bbox_max_lng}, {bbox_max_lat}, 4326
                )
            LEFT JOIN edge_shadow es ON es.edge_id = e.edge_id
                AND es.sun_id = (
                    SELECT sun_id FROM sun
                    WHERE date = '{target_date}' AND hour = {hour}
                    LIMIT 1
                )
        """

        # ── 主查詢 ────────────────────────────────────────────────────
        final_query = f"""
        WITH
        start_node AS (
            SELECT node_id
            FROM node
            ORDER BY geom <-> ST_SetSRID(ST_MakePoint(%(start_lng)s, %(start_lat)s), 4326)
            LIMIT 1
        ),
        end_node AS (
            SELECT node_id
            FROM node
            ORDER BY geom <-> ST_SetSRID(ST_MakePoint(%(end_lng)s, %(end_lat)s), 4326)
            LIMIT 1
        ),
        route AS (
            SELECT *
            FROM pgr_dijkstra(
                $pgr${pgr_edge_sql}$pgr$,
                (SELECT node_id FROM start_node),
                (SELECT node_id FROM end_node),
                directed := false
            )
        )
        SELECT
            json_agg(
                json_build_object(
                    'lat',     ST_Y(n.geom),
                    'lng',     ST_X(n.geom),
                    'node_id', r.node
                )
                ORDER BY r.path_seq
            ) AS path,

            SUM(e.length) FILTER (WHERE r.edge <> -1) AS total_distance,

            AVG(
                CASE
                    WHEN es.cost IS NOT NULL AND e.length > 0
                    THEN GREATEST(0, LEAST(1, 1.0 - es.cost / e.length))
                    ELSE 0
                END
            ) FILTER (WHERE r.edge <> -1) AS avg_shadow_ratio,

            json_agg(
                json_build_object(
                    'edge_id',      r.edge,
                    'shadow_ratio', CASE
                                        WHEN es.cost IS NOT NULL AND e.length > 0
                                        THEN GREATEST(0, LEAST(1, 1.0 - es.cost / e.length))
                                        ELSE 0
                                    END,
                    'cost', COALESCE(es.cost, e.length)
                )
                ORDER BY r.path_seq
            ) FILTER (WHERE r.edge <> -1) AS edge_details

        FROM route r
        JOIN node n ON r.node = n.node_id
        LEFT JOIN edge e ON r.edge = e.edge_id
        LEFT JOIN edge_shadow es ON es.edge_id = e.edge_id
            AND es.sun_id = (
                SELECT sun_id FROM sun
                WHERE date = %(target_date)s AND hour = %(hour)s
                LIMIT 1
            )
        """

        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(final_query, {
            "start_lng":   start_lng,
            "start_lat":   start_lat,
            "end_lng":     end_lng,
            "end_lat":     end_lat,
            "target_date": target_date,
            "hour":        hour,
        })

        result = cur.fetchone()
        cur.close()

        # ── 處理結果 ──────────────────────────────────────────────────
        if not result or not result["path"]:
            return jsonify({"status": "error", "message": "找不到路徑"}), 404

        distance     = float(result["total_distance"]   or 0)
        shadow_ratio = float(result["avg_shadow_ratio"] or 0)
        edge_details = result["edge_details"] or []

        WALK_SPEED_M_PER_MIN = 80
        walk_time    = int(distance / WALK_SPEED_M_PER_MIN)
        path_coords  = [[p["lat"], p["lng"]] for p in result["path"]]
        segment_ratios = [e["shadow_ratio"] for e in edge_details]

        return jsonify({
            "status": "success",
            "route": {
                "path":            path_coords,
                "segment_ratios":  segment_ratios,
                "statistics": {
                    "distance":       round(distance, 1),
                    "shadow_ratio":   round(shadow_ratio, 3),
                    "estimated_time": walk_time,
                    "hour":           hour,
                    "date":           target_date,
                },
            },
        })

    except KeyError as e:
        return jsonify({"status": "error", "message": f"缺少必要欄位：{e}"}), 400
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({"status": "error", "message": str(e)}), 500


# ──────────────────────────────────────────
if __name__ == "__main__":
    print("🚀 啟動陰影導航 API v2（修正版）...")
    print(f"📍 網址：http://localhost:5000")
    print(f"📅 固定日期：{TARGET_DATE}  有效時段：{HOUR_MIN}:00 ~ {HOUR_MAX}:00")
    app.run(host="0.0.0.0", port=5000, debug=True)
