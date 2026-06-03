#app.py


# 陰影導航後端 API v2 — 程式碼完整解析

## 目錄

1. [系統概述](#系統概述)
2. [檔案結構](#檔案結構)
3. [初始化與設定](#初始化與設定)
4. [資料庫連線池](#資料庫連線池)
5. [時間工具](#時間工具)
6. [API 路由詳解](#api-路由詳解)
   - [GET /](#get-)
   - [GET /api/current-time](#get-apicurrent-time)
   - [GET /api/stats](#get-apistats)
   - [POST /api/route](#post-apiroute)
7. [核心查詢邏輯：四步驟拆解](#核心查詢邏輯四步驟拆解)
8. [回傳資料格式](#回傳資料格式)
9. [錯誤處理](#錯誤處理)
10. [修正紀錄（v2 vs 原版）](#修正紀錄v2-vs-原版)
11. [啟動方式](#啟動方式)
12. [依賴套件](#依賴套件)

---

## 系統概述

本後端是「台北市遮陰導航」系統的核心，目的是：

> 給定使用者的起點與終點座標，計算一條「最涼快」的步行路徑——
> 優先走有騎樓、行道樹、或大樓陰影遮蔽的街道，而不只是最短距離。

整體流程：

```
前端送出起點/終點座標
        ↓
找最近的路網 Node（KNN）
        ↓
用 Bounding Box 篩選範圍內的 Edge
        ↓
pgr_dijkstra 以遮陰 cost 計算最短路徑
        ↓
回傳路徑座標 + 每段遮陰比例給前端
```

---

## 檔案結構

```
app_v2.py          # 主程式（本檔案）
admin_routes.py    # 選用的管理介面路由（可不存在）
```

---

## 初始化與設定

```python
app = Flask(__name__)
CORS(app)
```

- 使用 **Flask** 作為 Web 框架。
- `CORS(app)` 允許所有來源的跨域請求，方便前端（Leaflet / Mapbox）直接呼叫。

```python
try:
    from admin_routes import admin_bp
    app.register_blueprint(admin_bp)
except ImportError:
    print("⚠️  admin_routes 不存在，略過")
```

- `admin_routes` 是選用模組，用 `try/except` 包住，缺少時不影響主程式啟動。
- 使用 Flask **Blueprint** 機制，將管理路由與主路由分離。

---

## 資料庫連線池

```python
db_pool = pool.SimpleConnectionPool(1, 10, **DB_CONFIG)
```

- **為什麼用 pool？** 每個 HTTP request 若都呼叫 `psycopg2.connect()` 開新連線，
  高併發時會耗盡 PostgreSQL 的連線數（預設上限約 100）。
- `SimpleConnectionPool(1, 10)` 表示最少維持 1 條、最多 10 條連線常駐。

```python
def get_db_connection():
    if 'db_conn' not in g:
        g.db_conn = db_pool.getconn()
    return g.db_conn

@app.teardown_appcontext
def release_db_connection(exception=None):
    conn = g.pop('db_conn', None)
    if conn is not None:
        db_pool.putconn(conn)
```

- `flask.g` 是 Flask 的 **request-scoped 全域物件**，每個 request 獨立一份。
- `get_db_connection()` 確保同一個 request 內只取一次連線（不重複取）。
- `@app.teardown_appcontext` 在 request 結束時自動執行，將連線歸還到 pool，
  無論該 request 是正常完成或拋出例外都會執行。

---

## 時間工具

```python
TARGET_DATE = '2026-06-21'   # 固定夏至
HOUR_MIN    = 9
HOUR_MAX    = 17
```

- 日期固定為夏至，這是台北全年日照最長的一天，陰影資料最完整。
- 有效時段設為 09:00 ~ 17:00，對應太陽高度角有意義的範圍。

```python
def get_current_hour() -> int:
    now  = datetime.now()
    hour = now.hour + (1 if now.minute >= 30 else 0)
    return max(HOUR_MIN, min(HOUR_MAX, hour))
```

- 將當前時間「四捨五入」到整點：
  - 14:20 → 14
  - 14:35 → 15
- 用 `max/min` 夾在有效範圍內，避免查不到 Sun 資料。

---

## API 路由詳解

### GET /

健康檢查端點，回傳 API 狀態與當前對齊整點。

**回傳範例：**
```json
{
  "status": "ok",
  "message": "陰影導航 API v2",
  "current_hour": 14
}
```

---

### GET /api/current-time

回傳當前時間資訊，供前端顯示「目前計算的是幾點的陰影」。

**回傳範例：**
```json
{
  "status": "success",
  "data": {
    "current_time": "14:23",
    "shade_hour": 14,
    "date": "2026-06-21"
  }
}
```

---

### GET /api/stats

回傳資料庫各資料表的筆數，用於後台監控資料是否正常匯入。

**回傳範例：**
```json
{
  "status": "success",
  "data": {
    "regions": 12,
    "trees": 45823,
    "nodes": 18432,
    "edges": 22108,
    "sun_records": 72,
    "edge_shadow_records": 1590576
  }
}
```

---

### POST /api/route

主要功能端點。接收起終點座標，回傳遮陰最佳路徑。

**請求 Body：**
```json
{
  "start": { "lat": 25.0478, "lng": 121.5170 },
  "end":   { "lat": 25.0412, "lng": 121.5320 },
  "hour": 14,
  "date": "2026-06-21"
}
```

- `hour` 與 `date` 為選填，未提供則使用當前時間。

**回傳範例：**
```json
{
  "status": "success",
  "route": {
    "path": [
      [25.0478, 121.5170],
      [25.0465, 121.5198],
      "..."
    ],
    "segment_ratios": [0.82, 0.45, 0.91, "..."],
    "statistics": {
      "distance": 1423.5,
      "shadow_ratio": 0.713,
      "estimated_time": 17,
      "hour": 14,
      "date": "2026-06-21"
    }
  }
}
```

---

## 核心查詢邏輯：四步驟拆解

### Step 1｜動態計算 Bounding Box

```python
lat_span = abs(start_lat - end_lat)
lng_span = abs(start_lng - end_lng)
padding  = max(0.01, max(lat_span, lng_span) * 0.2)
```

- 固定 padding 0.01 度（約 1 km）的問題：若起終點距離 5 km，
  僅往外擴 1 km 可能會截掉繞路的合理路徑。
- 改為依照起終點距離的 20% 動態調整，距離越遠 padding 越大。
- `max(0.01, ...)` 確保近距離時也有最小緩衝。

---

### Step 2｜pgr_dijkstra 的 edge SQL

```python
pgr_edge_sql = f"""
    SELECT
        e.edge_id AS id,
        e.source,
        e.target,
        COALESCE(es.cost, e.length) AS cost
    FROM edge e
    JOIN node n_src ON e.source = n_src.node_id
        AND n_src.geom && ST_MakeEnvelope(...)
    JOIN node n_tgt ON e.target = n_tgt.node_id
        AND n_tgt.geom && ST_MakeEnvelope(...)
    LEFT JOIN edge_shadow es ON es.edge_id = e.edge_id
        AND es.sun_id = (
            SELECT sun_id FROM sun
            WHERE date = '{target_date}' AND hour = {hour}
            LIMIT 1
        )
"""
```

**為什麼用 f-string 而不是 psycopg2 參數傳入？**

`pgr_dijkstra` 接受的第一個參數是一個 SQL 字串（文字型別）。
若用 `%(pgr_sql)s` 透過 psycopg2 傳入，psycopg2 會對字串加上引號並跳脫特殊字元，
導致 PostgreSQL 收到的是已跳脫的文字，而非合法的 SQL 查詢，執行時報錯。

直接用 f-string 嵌入（所有值均為我們自己產生的數字與日期，無 SQL injection 風險）是正確的做法。

**COALESCE(es.cost, e.length) 的意義：**

- `es.cost`：從 `edge_shadow` 取得的遮陰加權 cost。
  cost 的設計邏輯是「越涼快 cost 越低」，例如 `length × (1 - 遮陰比例)`。
- 若該 edge 在這個時段沒有陰影計算資料，fallback 使用 `e.length`（純距離）。
- 這讓 Dijkstra 在資料缺漏時仍能找出路徑，不會因為 cost = NULL 而排除某些路段。

**Bounding Box 篩選的目的：**

pgr_dijkstra 預設會讀取整張 edge 表。台北市路網有數萬條 edge，
用 `ST_MakeEnvelope` 搭配 GiST 空間索引，只傳入起終點矩形範圍內的 edge，
大幅降低 Dijkstra 的搜尋空間，查詢速度可提升數倍至數十倍。

---

### Step 3｜主 CTE 查詢

```sql
WITH
start_node AS (
    SELECT node_id FROM node
    ORDER BY geom <-> ST_SetSRID(ST_MakePoint(...), 4326)
    LIMIT 1
),
end_node AS (
    SELECT node_id FROM node
    ORDER BY geom <-> ST_SetSRID(ST_MakePoint(...), 4326)
    LIMIT 1
),
route AS (
    SELECT * FROM pgr_dijkstra(pgr_edge_sql, start_node, end_node, directed := false)
)
```

- `<->` 是 PostGIS 的 **KNN（K-Nearest Neighbor）運算子**，
  搭配 GiST 索引效率極高，找最近 node 不需要全表掃描。
- `directed := false` 表示路網為無向圖（雙向通行）。

---

### Step 4｜組合結果

```sql
SELECT
    json_agg(...) AS path,
    SUM(e.length) FILTER (WHERE r.edge <> -1) AS total_distance,
    AVG(CASE WHEN es.cost IS NOT NULL AND e.length > 0
             THEN GREATEST(0, LEAST(1, 1.0 - es.cost / e.length))
             ELSE 0 END
    ) FILTER (WHERE r.edge <> -1) AS avg_shadow_ratio,
    json_agg(...) FILTER (WHERE r.edge <> -1) AS edge_details
FROM route r
JOIN node n ON r.node = n.node_id
LEFT JOIN edge e ON r.edge = e.edge_id
LEFT JOIN edge_shadow es ON es.edge_id = e.edge_id
    AND es.sun_id = (SELECT sun_id FROM sun WHERE date = ... AND hour = ... LIMIT 1)
```

**`r.edge <> -1` 的原因：**

pgr_dijkstra 回傳的最後一列是終點 node，其 `edge` 欄位值為 `-1`（代表無對應 edge）。
用 `FILTER (WHERE r.edge <> -1)` 排除這筆，避免 SUM / AVG 計算到 NULL 造成誤差。

**shadow_ratio 的換算公式：**

```
shadow_ratio = 1 - (es.cost / e.length)
```

- `es.cost` 是遮陰加權後的 cost，越涼快越低（最低趨近 0）。
- `e.length` 是路段原始長度。
- 兩者相除得到「cost 佔長度的比例」，1 減去它即為「遮陰比例」。
- 用 `GREATEST(0, LEAST(1, ...))` 夾住在 0~1，防止資料異常時超出範圍。

**為什麼外層 JOIN edge_shadow 要用子查詢鎖定 sun_id？**

```sql
LEFT JOIN edge_shadow es ON es.edge_id = e.edge_id
    AND es.sun_id = (SELECT sun_id FROM sun WHERE date = ... AND hour = ... LIMIT 1)
```

若只寫 `ON es.edge_id = e.edge_id`，一條 edge 可能對應多個時段的 edge_shadow，
導致 JOIN 出多列，使 `json_agg` 重複、`AVG` 計算錯誤。
透過子查詢先找出對應時段的唯一 `sun_id`，確保一條 edge 只對應一筆陰影資料。

---

## 回傳資料格式

| 欄位 | 型別 | 說明 |
|------|------|------|
| `path` | `[[lat, lng], ...]` | 路徑節點座標列表，按順序排列 |
| `segment_ratios` | `[float, ...]` | 每段 edge 的遮陰比例（0=無遮陰，1=全遮陰） |
| `statistics.distance` | `float` | 路徑總長度（公尺） |
| `statistics.shadow_ratio` | `float` | 全路段平均遮陰比例 |
| `statistics.estimated_time` | `int` | 預估步行時間（分鐘，以 80 公尺/分鐘計算） |
| `statistics.hour` | `int` | 實際使用的時段 |
| `statistics.date` | `string` | 實際使用的日期 |

---

## 錯誤處理

| 情況 | HTTP 狀態碼 | 說明 |
|------|-------------|------|
| 找不到路徑 | 404 | pgr_dijkstra 無法連通起終點 |
| 缺少必要欄位 | 400 | `start` / `end` 未提供 |
| 其他例外 | 500 | DB 錯誤或查詢失敗，伺服器印出完整 traceback |

---

## 修正紀錄（v2 vs 原版）

| 問題 | 原版 | v2 修正 |
|------|------|---------|
| 不存在的欄位 | `es.shadow_score` | 改用 `1 - es.cost / e.length` 換算 `shadow_ratio` |
| pgr SQL 傳參錯誤 | `%(pgr_sql)s`（psycopg2 會跳脫） | f-string 直接嵌入 `$pgr$...$pgr$` |
| edge_shadow JOIN 多列 | 無限制 sun_id | 子查詢鎖定唯一 `sun_id` |
| 每次請求開新 DB 連線 | `psycopg2.connect()` | `SimpleConnectionPool(1, 10)` |
| admin_routes 缺失時炸掉 | 直接 import | `try/except ImportError` 保護 |
| bbox padding 固定 | 固定 0.01 度 | `max(0.01, 距離 × 0.2)` 動態調整 |

---

## 啟動方式

```bash
python app_v2.py
```

服務預設跑在 `http://localhost:5000`，`debug=True` 模式下程式碼異動會自動重啟。

**測試路由端點：**

```bash
# 健康檢查
curl http://localhost:5000/

# 查詢路徑
curl -X POST http://localhost:5000/api/route \
  -H "Content-Type: application/json" \
  -d '{
    "start": {"lat": 25.0478, "lng": 121.5170},
    "end":   {"lat": 25.0412, "lng": 121.5320},
    "hour": 14
  }'
```

---

## 依賴套件

```
flask
flask-cors
psycopg2-binary
```

**資料庫端需求：**

```
PostgreSQL >= 13
PostGIS >= 3.0
pgRouting >= 3.0
```

**必要 GiST 索引（若尚未建立）：**

```sql
CREATE INDEX ON node USING GIST(geom);
CREATE INDEX ON edge USING GIST(geom);
```
