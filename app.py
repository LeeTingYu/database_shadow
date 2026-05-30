"""
陰影導航後端 API v2
支援時間參數的路徑計算
執行方式：python app_v2.py
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor
import json
from datetime import datetime

app = Flask(__name__)
CORS(app)

DB_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'database': 'shadow',
    'user': 'postgres',
    'password': '123456'
}

def get_db_connection():
    return psycopg2.connect(**DB_CONFIG)

def get_current_hour():
    """取得當前整點化時間（30分前靠前，30分後靠後）"""
    now = datetime.now()
    hour = now.hour
    minute = now.minute
    if minute >= 30:
        hour = hour + 1
    # 限制在 9-17 之間
    hour = max(9, min(17, hour))
    return hour

@app.route('/')
def home():
    return jsonify({
        'status': 'ok',
        'message': '陰影導航 API v2',
        'current_hour': get_current_hour()
    })

@app.route('/api/current-time', methods=['GET'])
def get_current_time():
    """取得當前整點化時間"""
    now = datetime.now()
    hour = get_current_hour()
    return jsonify({
        'status': 'success',
        'data': {
            'current_time': now.strftime('%H:%M'),
            'shade_hour': hour,
            'date': '2026-06-21'  # 固定夏至
        }
    })

@app.route('/api/stats', methods=['GET'])
def get_stats():
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT 
                (SELECT COUNT(*) FROM region) AS regions,
                (SELECT COUNT(*) FROM building) AS buildings,
                (SELECT COUNT(*) FROM tree) AS trees,
                (SELECT COUNT(*) FROM node) AS nodes,
                (SELECT COUNT(*) FROM edge) AS edges,
                (SELECT COUNT(*) FROM sun) AS sun_records
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

        start_lat = data['start']['lat']
        start_lng = data['start']['lng']
        end_lat = data['end']['lat']
        end_lng = data['end']['lng']

        # 取得時間參數（沒傳就用當前時間）
        hour = data.get('hour', get_current_hour())
        target_date = '2026-06-21'

        print(f"計算路徑：({start_lat}, {start_lng}) → ({end_lat}, {end_lng}) 時段：{hour}:00")

        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # bbox
        min_lng = min(start_lng, end_lng) - 0.01
        max_lng = max(start_lng, end_lng) + 0.01
        min_lat = min(start_lat, end_lat) - 0.01
        max_lat = max(start_lat, end_lat) + 0.01

        # 查詢這個時段的陰影 cost
        # 如果有 edge_hourly_shadow 用精確的，否則用平均的
        query = """
        WITH
        start_node AS (
            SELECT node_id
            FROM node
            ORDER BY geom <-> ST_SetSRID(ST_MakePoint(%s, %s), 4326)
            LIMIT 1
        ),
        end_node AS (
            SELECT node_id
            FROM node
            ORDER BY geom <-> ST_SetSRID(ST_MakePoint(%s, %s), 4326)
            LIMIT 1
        ),
        route AS (
            SELECT * FROM pgr_dijkstra(
                'SELECT 
                    e.edge_id AS id,
                    e.source,
                    e.target,
                    e.cost
                FROM edge e
                JOIN node n_src ON e.source = n_src.node_id
                JOIN node n_tgt ON e.target = n_tgt.node_id
                WHERE 
                    n_src.geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)
                    AND n_tgt.geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)',
                (SELECT node_id FROM start_node),
                (SELECT node_id FROM end_node),
                directed := false
            )
        )
        SELECT 
            json_agg(
                json_build_object(
                    'lat', ST_Y(n.geom),
                    'lng', ST_X(n.geom),
                    'node_id', r.node
                )
                ORDER BY r.path_seq
            ) AS path,
            SUM(e.length) AS total_distance,
            AVG(e.shade_score) AS avg_shade_score,
            json_agg(
                json_build_object(
                    'edge_id', r.edge,
                    'shade_score', COALESCE(e.shade_score, 0)
                )
                ORDER BY r.path_seq
            ) AS edge_details
        FROM route r
        JOIN node n ON r.node = n.node_id
        LEFT JOIN edge e ON r.edge = e.edge_id
        WHERE r.edge != -1;
        """

        cur.execute(query, (
            start_lng, start_lat,
            end_lng, end_lat,
            min_lng, min_lat, max_lng, max_lat,
            min_lng, min_lat, max_lng, max_lat
        ))

        result = cur.fetchone()
        cur.close()
        conn.close()

        if not result or not result['path']:
            return jsonify({'status': 'error', 'message': '找不到路徑'}), 404

        distance = result['total_distance'] or 0
        shade_score = result['avg_shade_score'] or 0
        walk_time = int(distance / 80)

        # 把路徑整理成座標陣列
        path_coords = [[p['lat'], p['lng']] for p in result['path']]

        # 每段路的遮蔭分數（用來顯示顏色）
        edge_details = result['edge_details'] or []
        segment_scores = [e['shade_score'] for e in edge_details if e['edge_id'] != -1]

        return jsonify({
            'status': 'success',
            'route': {
                'path': path_coords,
                'segment_scores': segment_scores,
                'statistics': {
                    'distance': round(distance, 1),
                    'shade_score': round(shade_score, 2),
                    'estimated_time': walk_time,
                    'hour': hour
                }
            }
        })

    except Exception as e:
        print(f"錯誤：{e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

exec(open('admin_routes.py', encoding='utf-8').read())

if __name__ == '__main__':
    print("🚀 啟動陰影導航 API v2...")
    print("📍 網址：http://localhost:5000")
    app.run(host='0.0.0.0', port=5000, debug=True)
