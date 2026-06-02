"""
陰影導航後端 API v2
支援時間參數的遮陰路徑計算
執行方式：python app_v2.py
"""
 
from flask import Flask, request, jsonify
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from admin_routes import admin_bp   # ← 用 Blueprint 正確 import
 
app = Flask(__name__)
CORS(app)
app.register_blueprint(admin_bp)
 
# ──────────────────────────────────────────
# DB 設定
# ──────────────────────────────────────────
DB_CONFIG = {
    'host':     'localhost',
    'port':     5432,
    'database': 'shadow',
    'user':     'postgres',
    'password': '123456',
}
 
def get_db_connection():
    return psycopg2.connect(**DB_CONFIG)
 
 
# ──────────────────────────────────────────
# 時間工具
# ──────────────────────────────────────────
TARGET_DATE = '2026-06-21'   # 固定夏至
HOUR_MIN    = 9
HOUR_MAX    = 17
 
def get_current_hour() -> int:
    """
    把當前時刻對齊到整點：
      - 30 分前 → 靠前整點
      - 30 分後 → 靠後整點
    結果夾在 HOUR_MIN ~ HOUR_MAX 之間
    """
    now    = datetime.now()
    hour   = now.hour + (1 if now.minute >= 30 else 0)
    return max(HOUR_MIN, min(HOUR_MAX, hour))
 
 
# ──────────────────────────────────────────
# 路由
# ──────────────────────────────────────────
@app.route('/')
def home():
    return jsonify({
        'status':       'ok',
        'message':      '陰影導航 API v2',
        'current_hour': get_current_hour(),
    })
 
 
@app.route('/api/current-time', methods=['GET'])
def get_current_time():
    now  = datetime.now()
    hour = get_current_hour()
    return jsonify({
        'status': 'success',
        'data': {
            'current_time': now.strftime('%H:%M'),
            'shade_hour':   hour,
            'date':         TARGET_DATE,
        },
    })
 
 
@app.route('/api/stats', methods=['GET'])
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
        conn.close()
        return jsonify({'status': 'success', 'data': stats})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
 
 
@app.route('/api/route', methods=['POST'])
def calculate_route():
    try:
        data = request.json
 
        start_lat = float(data['start']['lat'])
        start_lng = float(data['start']['lng'])
        end_lat   = float(data['end']['lat'])
        end_lng   = float(data['end']['lng'])
 
        # hour：前端可指定，否則用當前整點
        hour        = int(data.get('hour', get_current_hour()))
        target_date = data.get('date', TARGET_DATE)
 
        # 合法範圍檢查
        hour = max(HOUR_MIN, min(HOUR_MAX, hour))
 
        print(f"計算路徑：({start_lat}, {start_lng}) → ({end_lat}, {end_lng})"
              f"  日期：{target_date}  時段：{hour}:00")
 
        # Bounding box（起終點各往外 0.01 度，約 1 km）
        bbox_min_lng = min(start_lng, end_lng) - 0.01
        bbox_max_lng = max(start_lng, end_lng) + 0.01
        bbox_min_lat = min(start_lat, end_lat) - 0.01
        bbox_max_lat = max(start_lat, end_lat) + 0.01
 
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)
 
        # ── 主查詢 ──────────────────────────────────────────────────────────
        #
        # 設計邏輯：
        #   1. start_node / end_node：用 <-> 運算子找最近的 node（KNN）
        #   2. pgr_dijkstra 的 edge SQL：
        #      - JOIN edge_shadow + sun 取得當下時段的 cost
        #      - 若該 edge 這個時段沒有 edge_shadow 資料，fallback 用 edge.length
        #      - 只考慮 bbox 範圍內的 edge（source & target 都在 bbox 內）
        #   3. 最後 JOIN 回 node 取座標、JOIN edge 取 length、
        #      JOIN edge_shadow 取 shadow_score（給前端畫顏色用）
        #
        query = """
        WITH
        -- 1. 找最近的起點 node
        start_node AS (
            SELECT node_id
            FROM node
            ORDER BY geom <-> ST_SetSRID(ST_MakePoint(%(start_lng)s, %(start_lat)s), 4326)
            LIMIT 1
        ),
 
        -- 2. 找最近的終點 node
        end_node AS (
            SELECT node_id
            FROM node
            ORDER BY geom <-> ST_SetSRID(ST_MakePoint(%(end_lng)s, %(end_lat)s), 4326)
            LIMIT 1
        ),
 
        -- 3. pgr_dijkstra 計算最短（最涼快）路徑
        route AS (
            SELECT *
            FROM pgr_dijkstra(
                -- edge SQL：在 bbox 內的 edge，以 edge_shadow.cost 為權重
                $pgr$
                SELECT
                    e.edge_id                   AS id,
                    e.source,
                    e.target,
                    COALESCE(es.cost, e.length) AS cost   -- 有遮陰 cost 優先，否則用距離
                FROM edge e
                -- 只加入 bbox 範圍內的 source node
                JOIN node n_src ON e.source = n_src.node_id
                    AND n_src.geom && ST_MakeEnvelope(
                        $1, $2, $3, $4, 4326   -- bbox_min_lng, bbox_min_lat, bbox_max_lng, bbox_max_lat
                    )
                -- 只加入 bbox 範圍內的 target node
                JOIN node n_tgt ON e.target = n_tgt.node_id
                    AND n_tgt.geom && ST_MakeEnvelope(
                        $1, $2, $3, $4, 4326
                    )
                -- LEFT JOIN 取當下時段的遮陰 cost（無資料時 COALESCE fallback）
                LEFT JOIN edge_shadow es ON es.edge_id = e.edge_id
                LEFT JOIN sun         s  ON s.sun_id   = es.sun_id
                    AND s.date = $5           -- target_date
                    AND s.hour = $6           -- hour
                $pgr$,
                (SELECT node_id FROM start_node),
                (SELECT node_id FROM end_node),
                directed := false
            )
        )
 
        -- 4. 組合結果：路徑座標 + 統計 + 每段遮陰分數
        SELECT
            -- 路徑座標（按順序排列）
            json_agg(
                json_build_object(
                    'lat',     ST_Y(n.geom),
                    'lng',     ST_X(n.geom),
                    'node_id', r.node
                )
                ORDER BY r.path_seq
            ) AS path,
 
            -- 總距離（公尺）
            SUM(e.length) FILTER (WHERE r.edge <> -1) AS total_distance,
 
            -- 平均遮陰分數（0=無遮陰，1=全遮陰）
            AVG(es.shadow_score) FILTER (WHERE r.edge <> -1) AS avg_shadow_score,
 
            -- 每段 edge 的遮陰資訊（給前端畫顏色）
            json_agg(
                json_build_object(
                    'edge_id',      r.edge,
                    'shadow_score', COALESCE(es.shadow_score, 0),
                    'cost',         COALESCE(es.cost, e.length)
                )
                ORDER BY r.path_seq
            ) FILTER (WHERE r.edge <> -1) AS edge_details
 
        FROM route r
        JOIN  node n  ON r.node    = n.node_id
        LEFT JOIN edge e  ON r.edge    = e.edge_id
        -- 再次 JOIN edge_shadow 取 shadow_score（顯示用，不影響路徑計算）
        LEFT JOIN edge_shadow es ON es.edge_id = e.edge_id
        LEFT JOIN sun         s  ON s.sun_id   = es.sun_id
            AND s.date = %(target_date)s
            AND s.hour = %(hour)s
        """
 
        # psycopg2 的 %(name)s 語法 與 $1..$6（pgr SQL 內）並存時，
        # pgr 內的 $1~$6 要用 %s 傳入，外層 CTE 用 %(name)s
        # → 整合成一個 params dict，pgr 內的參數用 positional %s 並接在最前面
        params = {
            'start_lng':   start_lng,
            'start_lat':   start_lat,
            'end_lng':     end_lng,
            'end_lat':     end_lat,
            'target_date': target_date,
            'hour':        hour,
        }
 
        # 注意：pgr 內的 $1~$6 是 libpq 的 server-side binding，
        # 必須另外傳，這裡改用字串格式化塞入 bbox 與時間（安全，因為都是數字/日期）
        pgr_edge_sql = f"""
            SELECT
                e.edge_id                   AS id,
                e.source,
                e.target,
                COALESCE(es.cost, e.length) AS cost
            FROM edge e
            JOIN node n_src ON e.source = n_src.node_id
                AND n_src.geom && ST_MakeEnvelope(
                    {bbox_min_lng}, {bbox_min_lat}, {bbox_max_lng}, {bbox_max_lat}, 4326
                )
            JOIN node n_tgt ON e.target = n_tgt.node_id
                AND n_tgt.geom && ST_MakeEnvelope(
                    {bbox_min_lng}, {bbox_min_lat}, {bbox_max_lng}, {bbox_max_lat}, 4326
                )
            LEFT JOIN edge_shadow es ON es.edge_id = e.edge_id
            LEFT JOIN sun         s  ON s.sun_id   = es.sun_id
                AND s.date = '{target_date}'
                AND s.hour = {hour}
        """
 
        # 重新整理 query，直接把 pgr_edge_sql 嵌入
        final_query = """
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
                %(pgr_sql)s,
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
            SUM(e.length)        FILTER (WHERE r.edge <> -1) AS total_distance,
            AVG(es.shadow_score) FILTER (WHERE r.edge <> -1) AS avg_shadow_score,
            json_agg(
                json_build_object(
                    'edge_id',      r.edge,
                    'shadow_score', COALESCE(es.shadow_score, 0),
                    'cost',         COALESCE(es.cost, e.length)
                )
                ORDER BY r.path_seq
            ) FILTER (WHERE r.edge <> -1) AS edge_details
        FROM route r
        JOIN  node        n  ON r.node    = n.node_id
        LEFT JOIN edge    e  ON r.edge    = e.edge_id
        LEFT JOIN edge_shadow es ON es.edge_id = e.edge_id
        LEFT JOIN sun     s  ON s.sun_id   = es.sun_id
            AND s.date = %(target_date)s
            AND s.hour = %(hour)s
        """
 
        cur.execute(final_query, {
            'start_lng':   start_lng,
            'start_lat':   start_lat,
            'end_lng':     end_lng,
            'end_lat':     end_lat,
            'pgr_sql':     pgr_edge_sql,   # pgr_dijkstra 接受字串參數
            'target_date': target_date,
            'hour':        hour,
        })
 
        result = cur.fetchone()
        cur.close()
        conn.close()
 
        # ── 處理結果 ──────────────────────────────────────────────────────
        if not result or not result['path']:
            return jsonify({'status': 'error', 'message': '找不到路徑'}), 404
 
        distance     = float(result['total_distance']  or 0)
        shadow_score = float(result['avg_shadow_score'] or 0)
        edge_details = result['edge_details'] or []
 
        WALK_SPEED_M_PER_MIN = 80
        walk_time = int(distance / WALK_SPEED_M_PER_MIN)
 
        # 路徑座標 [[lat, lng], ...]
        path_coords = [[p['lat'], p['lng']] for p in result['path']]
 
        # 每段遮陰分數（給前端畫綠/黃/紅）
        segment_scores = [e['shadow_score'] for e in edge_details]
 
        return jsonify({
            'status': 'success',
            'route': {
                'path':           path_coords,
                'segment_scores': segment_scores,
                'statistics': {
                    'distance':       round(distance, 1),      # 公尺
                    'shadow_score':   round(shadow_score, 3),  # 0~1，越大越涼
                    'estimated_time': walk_time,               # 分鐘
                    'hour':           hour,
                    'date':           target_date,
                },
            },
        })
 
    except KeyError as e:
        return jsonify({'status': 'error', 'message': f'缺少必要欄位：{e}'}), 400
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'status': 'error', 'message': str(e)}), 500
 
 
# ──────────────────────────────────────────
if __name__ == '__main__':
    print("🚀 啟動陰影導航 API v2...")
    print(f"📍 網址：http://localhost:5000")
    print(f"📅 固定日期：{TARGET_DATE}  有效時段：{HOUR_MIN}:00 ~ {HOUR_MAX}:00")
    app.run(host='0.0.0.0', port=5000, debug=True)
 
