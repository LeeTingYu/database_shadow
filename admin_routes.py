"""
admin_routes.py

管理者後端 API
掛載方式：在 app_v2.py 用 register_blueprint(admin_bp)
"""

from flask import Blueprint, request, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor
import json

admin_bp = Blueprint("admin", __name__)

# ──────────────────────────────────────────
# DB 設定
# ──────────────────────────────────────────
DB_CONFIG = {
    "host":     "localhost",
    "port":     5432,
    "database": "shadow",
    "user":     "postgres",
    "password": "123456",
}

TARGET_DATE = "2026-06-21"

# 遮蔭來源權重（和 datong_weighted_erd.py 一致）
ARCADE_WEIGHT   = 1.0
BUILDING_WEIGHT = 0.7
TREE_WEIGHT     = 0.5
ALPHA           = 0.8

ARCADE_NEAR_DISTANCE_M = 5.0
METRIC_SRID            = 3826


def get_conn():
    return psycopg2.connect(**DB_CONFIG)


# ══════════════════════════════════════════
# 🌳 樹
# ══════════════════════════════════════════

@admin_bp.route("/api/admin/trees", methods=["GET"])
def list_trees():
    """查看所有樹"""
    try:
        conn = get_conn()
        cur  = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT
                t_id,
                t_dist,
                ST_Y(t_geom) AS lat,
                ST_X(t_geom) AS lng,
                t_height,
                canopy_radius,
                traffic_island,
                created_at,
                delete_at
            FROM tree
            WHERE delete_at IS NULL
            ORDER BY t_id
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify({"status": "success", "data": rows})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@admin_bp.route("/api/admin/trees", methods=["POST"])
def create_tree():
    """
    新增一棵樹
    Body: { lat, lng, t_dist, t_height, canopy_radius, traffic_island }
    """
    try:
        data           = request.json
        lat            = float(data["lat"])
        lng            = float(data["lng"])
        t_dist         = data.get("t_dist", "")
        t_height       = float(data.get("t_height", 6.0))
        canopy_radius  = float(data.get("canopy_radius", 3.0))
        traffic_island = bool(data.get("traffic_island", False))

        conn = get_conn()
        cur  = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            INSERT INTO tree (t_dist, t_geom, t_height, canopy_radius, traffic_island, created_at)
            VALUES (
                %s,
                ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                %s, %s, %s,
                NOW()
            )
            RETURNING t_id
        """, (t_dist, lng, lat, t_height, canopy_radius, traffic_island))

        new_id = cur.fetchone()["t_id"]
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({
            "status":  "success",
            "message": f"樹 {new_id} 新增成功，請記得執行重算",
            "t_id":    new_id,
        }), 201

    except KeyError as e:
        return jsonify({"status": "error", "message": f"缺少欄位：{e}"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@admin_bp.route("/api/admin/trees/<int:t_id>", methods=["DELETE"])
def delete_tree(t_id):
    """軟刪除一棵樹"""
    try:
        conn = get_conn()
        cur  = conn.cursor()
        cur.execute("""
            UPDATE tree SET delete_at = NOW()
            WHERE t_id = %s AND delete_at IS NULL
        """, (t_id,))

        if cur.rowcount == 0:
            cur.close()
            conn.close()
            return jsonify({"status": "error", "message": "找不到該樹或已刪除"}), 404

        conn.commit()
        cur.close()
        conn.close()
        return jsonify({
            "status":  "success",
            "message": f"樹 {t_id} 已刪除，請記得執行重算",
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ══════════════════════════════════════════
# 🏘️ 騎樓
# ══════════════════════════════════════════

@admin_bp.route("/api/admin/arcades", methods=["GET"])
def list_arcades():
    """查看所有騎樓"""
    try:
        conn = get_conn()
        cur  = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT
                a_id,
                b_id,
                a_dist,
                ST_AsGeoJSON(geom)::json AS geometry,
                a_area,
                a_length,
                created_at,
                delete_at
            FROM arcade
            WHERE delete_at IS NULL
            ORDER BY a_id
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify({"status": "success", "data": rows})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@admin_bp.route("/api/admin/arcades", methods=["POST"])
def create_arcade():
    """
    新增騎樓
    Body: { geojson: <GeoJSON geometry>, a_dist, b_id }
    """
    try:
        data        = request.json
        geojson_str = json.dumps(data["geojson"])
        a_dist      = data.get("a_dist", "")
        b_id        = data.get("b_id")

        conn = get_conn()
        cur  = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            INSERT INTO arcade (b_id, a_dist, geom, created_at)
            VALUES (
                %s, %s,
                ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326),
                NOW()
            )
            RETURNING a_id
        """, (b_id, a_dist, geojson_str))

        new_id = cur.fetchone()["a_id"]
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({
            "status":  "success",
            "message": f"騎樓 {new_id} 新增成功，請記得執行重算",
            "a_id":    new_id,
        }), 201

    except KeyError as e:
        return jsonify({"status": "error", "message": f"缺少欄位：{e}"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@admin_bp.route("/api/admin/arcades/<int:a_id>", methods=["DELETE"])
def delete_arcade(a_id):
    """軟刪除騎樓"""
    try:
        conn = get_conn()
        cur  = conn.cursor()
        cur.execute("""
            UPDATE arcade SET delete_at = NOW()
            WHERE a_id = %s AND delete_at IS NULL
        """, (a_id,))

        if cur.rowcount == 0:
            cur.close()
            conn.close()
            return jsonify({"status": "error", "message": "找不到該騎樓或已刪除"}), 404

        conn.commit()
        cur.close()
        conn.close()
        return jsonify({
            "status":  "success",
            "message": f"騎樓 {a_id} 已刪除，請記得執行重算",
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ══════════════════════════════════════════
# 🏗️ 建築
# ══════════════════════════════════════════

@admin_bp.route("/api/admin/buildings", methods=["GET"])
def list_buildings():
    """查看所有建築"""
    try:
        conn = get_conn()
        cur  = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT
                b_id,
                b_dist,
                ST_AsGeoJSON(geom)::json AS geometry,
                b_height,
                created_at,
                delete_at
            FROM building
            WHERE delete_at IS NULL
            ORDER BY b_id
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify({"status": "success", "data": rows})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@admin_bp.route("/api/admin/buildings", methods=["POST"])
def create_building():
    """
    新增建築
    Body: { geojson: <GeoJSON geometry>, b_dist, b_height }
    """
    try:
        data        = request.json
        geojson_str = json.dumps(data["geojson"])
        b_dist      = data.get("b_dist", "")
        b_height    = float(data.get("b_height", 15.0))

        conn = get_conn()
        cur  = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            INSERT INTO building (b_dist, geom, b_height, created_at)
            VALUES (
                %s,
                ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326),
                %s,
                NOW()
            )
            RETURNING b_id
        """, (b_dist, geojson_str, b_height))

        new_id = cur.fetchone()["b_id"]
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({
            "status":  "success",
            "message": f"建築 {new_id} 新增成功，請記得執行重算",
            "b_id":    new_id,
        }), 201

    except KeyError as e:
        return jsonify({"status": "error", "message": f"缺少欄位：{e}"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@admin_bp.route("/api/admin/buildings/<int:b_id>", methods=["DELETE"])
def delete_building(b_id):
    """軟刪除建築"""
    try:
        conn = get_conn()
        cur  = conn.cursor()
        cur.execute("""
            UPDATE building SET delete_at = NOW()
            WHERE b_id = %s AND delete_at IS NULL
        """, (b_id,))

        if cur.rowcount == 0:
            cur.close()
            conn.close()
            return jsonify({"status": "error", "message": "找不到該建築或已刪除"}), 404

        conn.commit()
        cur.close()
        conn.close()
        return jsonify({
            "status":  "success",
            "message": f"建築 {b_id} 已刪除，請記得執行重算",
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ══════════════════════════════════════════
# 🛣️ Edge
# ══════════════════════════════════════════

@admin_bp.route("/api/admin/edges", methods=["GET"])
def list_edges():
    """
    查看所有 edge 的 cost
    Query param: hour（可選，不傳就回傳所有時段的平均）
    """
    try:
        hour = request.args.get("hour", type=int)
        conn = get_conn()
        cur  = conn.cursor(cursor_factory=RealDictCursor)

        if hour:
            cur.execute("""
                SELECT
                    e.edge_id,
                    e.source,
                    e.target,
                    e.length,
                    s.hour,
                    es.shade_score,
                    es.cost
                FROM edge e
                JOIN edge_shadow es ON es.edge_id = e.edge_id
                JOIN sun s ON s.sun_id = es.sun_id
                    AND s.date = %s
                    AND s.hour = %s
                ORDER BY e.edge_id
            """, (TARGET_DATE, hour))
        else:
            cur.execute("""
                SELECT
                    e.edge_id,
                    e.source,
                    e.target,
                    e.length,
                    AVG(es.shade_score) AS avg_shade_score,
                    AVG(es.cost)        AS avg_cost,
                    MIN(es.cost)        AS min_cost,
                    MAX(es.cost)        AS max_cost
                FROM edge e
                JOIN edge_shadow es ON es.edge_id = e.edge_id
                JOIN sun s ON s.sun_id = es.sun_id
                    AND s.date = %s
                GROUP BY e.edge_id, e.source, e.target, e.length
                ORDER BY e.edge_id
            """, (TARGET_DATE,))

        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify({"status": "success", "data": rows, "count": len(rows)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@admin_bp.route("/api/admin/edges/<int:edge_id>", methods=["GET"])
def get_edge_detail(edge_id):
    """查看單一 edge 在各時段的 shade_score + cost"""
    try:
        conn = get_conn()
        cur  = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT
                e.edge_id,
                e.source,
                e.target,
                e.length,
                ST_AsGeoJSON(ST_MakeLine(n1.geom, n2.geom))::json AS geometry
            FROM edge e
            JOIN node n1 ON e.source = n1.node_id
            JOIN node n2 ON e.target = n2.node_id
            WHERE e.edge_id = %s
        """, (edge_id,))

        edge_info = cur.fetchone()
        if not edge_info:
            cur.close()
            conn.close()
            return jsonify({"status": "error", "message": "找不到該 edge"}), 404

        cur.execute("""
            SELECT
                s.hour,
                s.azimuth,
                s.elevation,
                es.shade_score,
                es.cost,
                es.computed_at
            FROM edge_shadow es
            JOIN sun s ON s.sun_id = es.sun_id
                AND s.date = %s
            WHERE es.edge_id = %s
            ORDER BY s.hour
        """, (TARGET_DATE, edge_id))

        hourly = cur.fetchall()
        cur.close()
        conn.close()

        return jsonify({
            "status": "success",
            "data": {
                "edge":   edge_info,
                "hourly": hourly,
            }
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ══════════════════════════════════════════
# 🔄 手動重算
# ══════════════════════════════════════════

def _find_affected_edges(cur, target_type: str, target_id: int) -> list:
    """找出受影響的 edge_id 清單"""
    if target_type == "tree":
        cur.execute("""
            SELECT DISTINCT e.edge_id
            FROM edge e
            JOIN node n1 ON e.source = n1.node_id
            JOIN node n2 ON e.target = n2.node_id
            CROSS JOIN (
                SELECT ST_Buffer(
                    ST_Transform(t_geom, %(srid)s),
                    canopy_radius + (
                        t_height / NULLIF(tan(radians(
                            (SELECT MIN(elevation) FROM sun
                             WHERE date = %(date)s AND elevation > 0)
                        )), 0)
                    )
                ) AS impact_zone
                FROM tree WHERE t_id = %(id)s
            ) z
            WHERE ST_Intersects(
                ST_Transform(ST_MakeLine(n1.geom, n2.geom), %(srid)s),
                z.impact_zone
            )
        """, {"id": target_id, "date": TARGET_DATE, "srid": METRIC_SRID})

    elif target_type == "arcade":
        cur.execute("""
            SELECT DISTINCT e.edge_id
            FROM edge e
            JOIN node n1 ON e.source = n1.node_id
            JOIN node n2 ON e.target = n2.node_id
            CROSS JOIN (
                SELECT ST_Buffer(
                    ST_Transform(geom, %(srid)s),
                    %(dist)s
                ) AS impact_zone
                FROM arcade WHERE a_id = %(id)s
            ) z
            WHERE ST_Intersects(
                ST_Transform(ST_MakeLine(n1.geom, n2.geom), %(srid)s),
                z.impact_zone
            )
        """, {"id": target_id, "dist": ARCADE_NEAR_DISTANCE_M, "srid": METRIC_SRID})

    elif target_type == "building":
        cur.execute("""
            SELECT DISTINCT e.edge_id
            FROM edge e
            JOIN node n1 ON e.source = n1.node_id
            JOIN node n2 ON e.target = n2.node_id
            CROSS JOIN (
                SELECT ST_Buffer(
                    ST_Transform(geom, %(srid)s),
                    COALESCE(b_height, 15.0) / NULLIF(tan(radians(
                        (SELECT MIN(elevation) FROM sun
                         WHERE date = %(date)s AND elevation > 0)
                    )), 0)
                ) AS impact_zone
                FROM building WHERE b_id = %(id)s
            ) z
            WHERE ST_Intersects(
                ST_Transform(ST_MakeLine(n1.geom, n2.geom), %(srid)s),
                z.impact_zone
            )
        """, {"id": target_id, "date": TARGET_DATE, "srid": METRIC_SRID})

    else:
        return []

    return [r["edge_id"] for r in cur.fetchall()]


def _recalculate_edges(conn, cur, edge_ids: list) -> int:
    """對指定 edge_ids × 所有 sun_id 重算，寫回 edge_shadow"""
    if not edge_ids:
        return 0

    cur.execute("""
        SELECT sun_id, hour, azimuth, elevation
        FROM sun
        WHERE date = %s AND hour BETWEEN 9 AND 17 AND elevation > 0
        ORDER BY hour
    """, (TARGET_DATE,))
    sun_list = cur.fetchall()

    total_updated = 0

    for sun in sun_list:
        cur.execute(f"""
            WITH
            sun_param AS (
                SELECT
                    radians(%(azimuth)s + 180.0) AS shadow_rad,
                    %(elevation)s::double precision AS elevation_deg
            ),
            arcade_union AS (
                SELECT COALESCE(
                    ST_Buffer(
                        ST_UnaryUnion(ST_Collect(ST_MakeValid(
                            ST_Transform(geom, {METRIC_SRID})
                        ))),
                        {ARCADE_NEAR_DISTANCE_M}
                    ),
                    ST_GeomFromText('POLYGON EMPTY', {METRIC_SRID})
                ) AS geom_m
                FROM arcade WHERE delete_at IS NULL
            ),
            building_shadow AS (
                SELECT COALESCE(
                    ST_UnaryUnion(ST_Collect(ST_MakeValid(shadow_geom))),
                    ST_GeomFromText('POLYGON EMPTY', {METRIC_SRID})
                ) AS geom_m
                FROM (
                    SELECT ST_Difference(
                        ST_ConvexHull(ST_Collect(
                            bgeom,
                            ST_Translate(bgeom,
                                sin(sp.shadow_rad) * CASE WHEN sp.elevation_deg <= 0 THEN 0
                                    ELSE height_m / tan(radians(sp.elevation_deg)) END,
                                cos(sp.shadow_rad) * CASE WHEN sp.elevation_deg <= 0 THEN 0
                                    ELSE height_m / tan(radians(sp.elevation_deg)) END
                            )
                        )), bgeom
                    ) AS shadow_geom
                    FROM (
                        SELECT
                            ST_MakeValid(ST_Transform(geom, {METRIC_SRID})) AS bgeom,
                            COALESCE(NULLIF(b_height, 0), 15.0) AS height_m
                        FROM building WHERE delete_at IS NULL
                    ) b CROSS JOIN sun_param sp
                ) sub
            ),
            tree_shadow AS (
                SELECT COALESCE(
                    ST_UnaryUnion(ST_Collect(ST_MakeValid(shadow_geom))),
                    ST_GeomFromText('POLYGON EMPTY', {METRIC_SRID})
                ) AS geom_m
                FROM (
                    SELECT ST_Difference(
                        ST_ConvexHull(ST_Collect(
                            canopy,
                            ST_Translate(canopy,
                                sin(sp.shadow_rad) * CASE WHEN sp.elevation_deg <= 0 THEN 0
                                    ELSE height_m / tan(radians(sp.elevation_deg)) END,
                                cos(sp.shadow_rad) * CASE WHEN sp.elevation_deg <= 0 THEN 0
                                    ELSE height_m / tan(radians(sp.elevation_deg)) END
                            )
                        )), canopy
                    ) AS shadow_geom
                    FROM (
                        SELECT
                            ST_Buffer(
                                ST_MakeValid(ST_Transform(t_geom, {METRIC_SRID})),
                                COALESCE(NULLIF(canopy_radius, 0), 3.0)
                            ) AS canopy,
                            COALESCE(NULLIF(t_height, 0), 6.0) AS height_m
                        FROM tree WHERE delete_at IS NULL
                    ) t CROSS JOIN sun_param sp
                ) sub
            ),
            shade_layers AS (
                SELECT
                    au.geom_m AS arcade_geom_m,
                    ST_Difference(bs.geom_m, au.geom_m) AS building_geom_m,
                    ST_Difference(ts.geom_m, ST_Union(au.geom_m, bs.geom_m)) AS tree_geom_m
                FROM arcade_union au, building_shadow bs, tree_shadow ts
            ),
            target_edges AS (
                SELECT
                    e.edge_id,
                    ST_Length(ST_Transform(ST_MakeLine(n1.geom, n2.geom), {METRIC_SRID})) AS len_m,
                    ST_Transform(ST_MakeLine(n1.geom, n2.geom), {METRIC_SRID}) AS geom_m
                FROM edge e
                JOIN node n1 ON e.source = n1.node_id
                JOIN node n2 ON e.target = n2.node_id
                WHERE e.edge_id = ANY(%(edge_ids)s)
            ),
            scores AS (
                SELECT
                    te.edge_id,
                    te.len_m,
                    CASE WHEN te.len_m = 0 THEN 0 ELSE LEAST(1.0, GREATEST(0.0,
                        CASE WHEN ST_Intersects(te.geom_m, sl.arcade_geom_m)
                             THEN ST_Length(ST_Intersection(te.geom_m, sl.arcade_geom_m))
                             ELSE 0 END / te.len_m)) END AS arcade_score,
                    CASE WHEN te.len_m = 0 THEN 0 ELSE LEAST(1.0, GREATEST(0.0,
                        CASE WHEN ST_Intersects(te.geom_m, sl.building_geom_m)
                             THEN ST_Length(ST_Intersection(te.geom_m, sl.building_geom_m))
                             ELSE 0 END / te.len_m)) END AS building_score,
                    CASE WHEN te.len_m = 0 THEN 0 ELSE LEAST(1.0, GREATEST(0.0,
                        CASE WHEN ST_Intersects(te.geom_m, sl.tree_geom_m)
                             THEN ST_Length(ST_Intersection(te.geom_m, sl.tree_geom_m))
                             ELSE 0 END / te.len_m)) END AS tree_score
                FROM target_edges te CROSS JOIN shade_layers sl
            )
            INSERT INTO edge_shadow (edge_id, sun_id, shade_score, cost, computed_at)
            SELECT
                edge_id,
                %(sun_id)s,
                LEAST(1.0,
                    {ARCADE_WEIGHT}   * arcade_score +
                    {BUILDING_WEIGHT} * building_score +
                    {TREE_WEIGHT}     * tree_score
                ) AS shade_score,
                len_m * (1.0 - {ALPHA} * LEAST(1.0,
                    {ARCADE_WEIGHT}   * arcade_score +
                    {BUILDING_WEIGHT} * building_score +
                    {TREE_WEIGHT}     * tree_score
                )) AS cost,
                NOW()
            FROM scores
            ON CONFLICT (edge_id, sun_id)
            DO UPDATE SET
                shade_score = EXCLUDED.shade_score,
                cost        = EXCLUDED.cost,
                computed_at = NOW()
        """, {
            "edge_ids":  edge_ids,
            "sun_id":    sun["sun_id"],
            "azimuth":   sun["azimuth"],
            "elevation": sun["elevation"],
        })

        total_updated += cur.rowcount

    return total_updated


@admin_bp.route("/api/admin/recalculate", methods=["POST"])
def recalculate():
    """
    手動觸發重算
    Body: { type: 'tree'|'arcade'|'building', id: <int> }
    不傳 type/id 就重算所有 edge（很慢，謹慎使用）
    """
    try:
        data        = request.json or {}
        target_type = data.get("type")
        target_id   = data.get("id")

        conn = get_conn()
        cur  = conn.cursor(cursor_factory=RealDictCursor)

        if target_type and target_id:
            edge_ids = _find_affected_edges(cur, target_type, int(target_id))
            if not edge_ids:
                cur.close()
                conn.close()
                return jsonify({
                    "status":  "success",
                    "message": "沒有受影響的 edge，不需要重算",
                    "updated": 0,
                })
            scope = f"{target_type} id={target_id}，受影響 edge：{edge_ids}"
        else:
            cur.execute("SELECT edge_id FROM edge")
            edge_ids = [r["edge_id"] for r in cur.fetchall()]
            scope    = "全部 edge"

        print(f"[重算] {scope}")
        updated = _recalculate_edges(conn, cur, edge_ids)
        conn.commit()
        cur.close()
        conn.close()

        return jsonify({
            "status":       "success",
            "message":      f"重算完成：{scope}",
            "edges_count":  len(edge_ids),
            "rows_updated": updated,
        })

    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({"status": "error", "message": str(e)}), 500
