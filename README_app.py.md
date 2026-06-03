# 陰影導航系統 — 完整專案說明

台北市遮陰路徑導航系統。將城市中大樓、行道樹與騎樓的陰影面積量化，計算最涼爽的步行路徑。

---

## 目錄

1. [專案架構](#專案架構)
2. [資料庫設計（ERD）](#資料庫設計erd)
3. [檔案清單](#檔案清單)
4. [db.py — 共用連線池](#dbpy--共用連線池)
5. [app_v2.py — 主後端 API](#app_v2py--主後端-api)
6. [admin_routes.py — 管理者 API](#admin_routespy--管理者-api)
7. [index.html — 使用者前端](#indexhtml--使用者前端)
8. [admin.html — 管理者前端](#adminhtml--管理者前端)
9. [資料庫 Migration](#資料庫-migration)
10. [啟動方式](#啟動方式)
11. [已知修正紀錄](#已知修正紀錄)

---

## 專案架構

```
使用者 index.html
    ↓ POST /api/route
app_v2.py（Flask）
    ↓ pgr_dijkstra
PostgreSQL + PostGIS + pgRouting
    ↑ 陰影計算寫回
admin_routes.py（Blueprint）
    ↑ 管理者 admin.html
```

整體流程：

```
前端送出起點/終點座標
        ↓
找最近的路網 Node（KNN <->）
        ↓
ST_MakeEnvelope 篩選 Bounding Box 內的 Edge
        ↓
pgr_dijkstra 以 edge_shadow.cost 為權重計算最涼路徑
        ↓
回傳路徑座標 + 每段 shadow_ratio 給前端染色
```

---

## 資料庫設計（ERD）

### 主要資料表

| 資料表 | 說明 |
|--------|------|
| `region` | 行政區，`dist` 代碼、`geom` 邊界 |
| `node` | 路口節點，`geom` 點位，供 pgRouting 使用 |
| `edge` | 路段，`source/target` 對應 node，`length` 由 ST_Length 計算 |
| `tree` | 行道樹，`t_geom`（TWD97→WGS84）、`t_height`、`canopy_radius` |
| `arcade` | 騎樓，`geom` 多邊形 |
| `building` | 建築物，`footprint` 多邊形、`b_height` |
| `sun` | 太陽角度，`date`、`hour`、`azimuth`（方位角）、`elevation`（仰角） |
| `edge_shadow` | 每條 edge × 每個 sun_id 的遮陰計算結果 |

### edge_shadow 欄位

| 欄位 | 說明 |
|------|------|
| `edge_id` | FK → edge |
| `sun_id` | FK → sun |
| `shade_score` | 綜合遮陰比例 0~1（越高越涼） |
| `cost` | `length × (1 - α × shade_score)`，Dijkstra 權重 |
| `computed_at` | 計算時間戳 |

### 遮陰權重設計

```
shade_score = min(1,
    arcade_score   × 1.0 +   ← 騎樓最涼，全遮
    building_score × 0.7 +   ← 大樓次之
    tree_score     × 0.5     ← 樹陰再次
)

cost = length × (1 - 0.8 × shade_score)
```

---

## 檔案清單

```
db.py              共用資料庫連線池（兩個後端都從這裡 import）
app_v2.py          主後端 API（路徑計算）
admin_routes.py    管理者 Blueprint（CRUD + 陰影重算）
index.html         使用者前端（地圖導航）
admin.html         管理者前端（資料管理）
```

---

## db.py — 共用連線池

**目的：** 讓 `app_v2.py` 和 `admin_routes.py` 共用同一個連線池，避免重複定義 `DB_CONFIG`。

```python
from db import get_conn, release_conn
```

| 函式 | 說明 |
|------|------|
| `get_conn()` | 從 pool 取得連線 |
| `release_conn(conn)` | 歸還連線到 pool |

連線池設定：`SimpleConnectionPool(1, 10)`，最少 1 條、最多 10 條常駐。

---

## app_v2.py — 主後端 API

### 路由總覽

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | `/` | 健康檢查，回傳當前對齊整點 |
| GET | `/api/current-time` | 當前時間與遮蔭時段 |
| GET | `/api/stats` | 各資料表筆數 |
| POST | `/api/route` | **主要路徑計算** |

### POST /api/route 請求格式

```json
{
  "start": { "lat": 25.0478, "lng": 121.5170 },
  "end":   { "lat": 25.0412, "lng": 121.5320 },
  "hour": 14,
  "date": "2026-06-21"
}
```

`hour` 與 `date` 為選填，未提供則使用當前時間。

### POST /api/route 回傳格式

```json
{
  "status": "success",
  "route": {
    "path": [[25.0478, 121.5170], [25.0465, 121.5198], "..."],
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

| 欄位 | 說明 |
|------|------|
| `path` | 路徑節點座標 `[[lat,lng],...]`，按順序 |
| `segment_ratios` | 每段遮陰比例，0=無遮陰，1=全遮陰 |
| `shadow_ratio` | 全路段平均遮陰比例 |
| `distance` | 總長度（公尺） |
| `estimated_time` | 預估步行時間（分鐘，以 80m/min 計算） |

### 核心查詢邏輯

**Step 1：動態 Bounding Box**

```python
padding = max(0.01, max(lat_span, lng_span) * 0.2)
```

起終點距離越遠，padding 越大，避免截掉合理繞路路徑。

**Step 2：pgr_dijkstra edge SQL（f-string 直接嵌入）**

pgr_dijkstra 的第一個參數是 SQL 字串。若用 psycopg2 的 `%(key)s` 傳入，psycopg2 會對字串加引號並跳脫，導致 PostgreSQL 收到的是已跳脫的文字而非合法 SQL。因此改用 f-string 直接嵌入，bbox 座標與日期均為我們自己產生的數字，無 SQL injection 風險。

**Step 3：外層 JOIN 鎖定唯一 sun_id**

```sql
LEFT JOIN edge_shadow es ON es.edge_id = e.edge_id
    AND es.sun_id = (
        SELECT sun_id FROM sun
        WHERE date = %(target_date)s AND hour = %(hour)s
        LIMIT 1
    )
```

若只寫 `ON es.edge_id = e.edge_id`，一條 edge 有多個時段的 edge_shadow 時，JOIN 出多列會讓 `AVG` 和 `json_agg` 計算錯誤。

**shadow_ratio 換算公式**

```
shadow_ratio = 1 - (es.cost / e.length)
```

`es.cost` 越低代表越涼快，換算成 0~1 的遮陰比例。用 `GREATEST(0, LEAST(1, ...))` 防止資料異常時超出範圍。

---

## admin_routes.py — 管理者 API

以 Flask Blueprint 掛載：`app.register_blueprint(admin_bp)`

### 路由總覽

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | `/api/admin/trees` | 列出所有行道樹（支援 limit/offset/dist/search） |
| POST | `/api/admin/trees` | 新增一棵樹，自動觸發受影響 edge 重算 |
| DELETE | `/api/admin/trees/<id>` | 軟刪除樹木，自動觸發重算 |
| GET | `/api/admin/arcades` | 列出所有騎樓 |
| POST | `/api/admin/arcades` | 新增騎樓（傳 GeoJSON），自動重算 |
| DELETE | `/api/admin/arcades/<id>` | 軟刪除騎樓，自動重算 |
| GET | `/api/admin/buildings` | 列出所有建築物（支援 limit/offset/dist/search） |
| POST | `/api/admin/buildings` | 新增建築物（傳 WKT），自動重算 |
| DELETE | `/api/admin/buildings/<id>` | 軟刪除建築物，自動重算 |
| GET | `/api/admin/edges/<id>` | 查看單一 edge 各時段 shade_score + cost |
| POST | `/api/admin/recalculate` | 手動觸發指定物件的 edge 重算 |

### insert/delete 後自動重算流程

```
1. insert/delete 操作
        ↓
2. _find_affected_edges()
   用 ST_Intersects 找出受影響的 edge_id 清單
        ↓
3. _recalculate_edges()
   對這些 edge × 9筆 sun_id（9~17時）
   重算 shade_score 和 cost
        ↓
4. ON CONFLICT (edge_id, sun_id) DO UPDATE
   寫回 edge_shadow
```

### 手動重算請求格式

```json
{ "type": "tree", "id": 1234 }
```

`type` 可為 `tree`、`arcade`、`building`。

### 分頁查詢參數

| 參數 | 說明 |
|------|------|
| `limit` | 每頁筆數（預設 50） |
| `offset` | 起始位移 |
| `dist` | 行政區篩選 |
| `search` | 模糊搜尋 ID 或行政區 |

---

## index.html — 使用者前端

### 功能

- 點擊地圖設定起點/終點，或使用 GPS 定位
- 行政區快選 Chip，快速跳轉地圖
- 計算最涼爽路徑，路線以 **深藍（涼爽）→ 深紅（炎熱）** 色階染色
- 統計卡顯示距離、遮蔭率、預估時間
- 開始導航模式：GPS 即時追蹤、方向指示、剩餘距離

### 色階對照

| 顏色 | shadow_ratio | 意義 |
|------|-------------|------|
| 深藍 `#1a3a6b` | 1.0 | 最涼爽 |
| 藍 `#2d7dd2` | 0.8 | 涼爽 |
| 青 `#4ab8c0` | 0.6 | 普通 |
| 黃 `#f0c040` | 0.4 | 偏熱 |
| 橘紅 `#e05c2a` | 0.2 | 熱 |
| 深紅 `#8b1a1a` | 0.0 | 最熱 |

### 重要設計說明

- `reverseGeocode()`：Nominatim 反向地理編碼，加 **5 秒 timeout**，超時靜默回退顯示座標
- `calcRoute()`：路徑計算加 **60 秒 timeout**，逾時顯示提示
- `drawRoute()`：使用後端 `segment_ratios` 真實遮陰比例染色，不使用亂數
- `toRad()`：角度轉弧度，改為獨立函式，不污染 `Math` 內建物件

---

## admin.html — 管理者前端

### 頁面結構

| 頁面 | 功能 |
|------|------|
| 🌳 行道樹 | 列表（分頁/搜尋/行政區篩選）+ 新增 + 刪除 |
| 🏘️ 騎樓 | 列表（分頁）+ 新增（輸入 GeoJSON）+ 刪除 |
| 🏗️ 建築物 | 列表（分頁/搜尋/行政區篩選）+ 新增（輸入 WKT）+ 刪除 |
| 🛤️ Edge 查詢 | 輸入 edge_id，查看 9~17 時各時段 shade_score + cost |
| 🔄 手動重算 | 選擇類型與 ID，觸發重算並顯示執行結果 |

### 前端設計重點

- 所有 API 呼叫使用 `apiFetch()`，統一加上 **60 秒 timeout**
- 新增/刪除按鈕操作中顯示 `loading` 狀態並 disabled，防止重複點擊
- `closeModal()` 關閉時自動清空表單 input 和 textarea
- `debounce(fn, key)` 搜尋欄 400ms 防抖，用字串 key 區分不同輸入框
- Toast 通知取代 `alert()`，右下角滑入顯示操作結果

---

## 資料庫 Migration

若 `edge_shadow` 資料表尚未有 `shade_score` 欄位，請執行：

```sql
ALTER TABLE edge_shadow
  ADD COLUMN IF NOT EXISTS shade_score DOUBLE PRECISION;

-- ON CONFLICT 需要唯一約束
ALTER TABLE edge_shadow
  ADD CONSTRAINT edge_shadow_edge_sun_unique
  UNIQUE (edge_id, sun_id);
```

必要的 GiST 空間索引：

```sql
CREATE INDEX ON node USING GIST(geom);
CREATE INDEX ON edge USING GIST(geom);
```

---

## 啟動方式

```bash
# 安裝依賴
pip install flask flask-cors psycopg2-binary

# 啟動後端
python app_v2.py
# 服務跑在 http://localhost:5000

# 前端直接開啟
open index.html   # 使用者介面
open admin.html   # 管理者介面
```

**資料庫端需求：**

```
PostgreSQL >= 13
PostGIS >= 3.0
pgRouting >= 3.0
```

---

## 已知修正紀錄

### app_v2.py

| 問題 | 修正 |
|------|------|
| `es.shadow_score` 欄位不存在 | 改用 `1 - es.cost / e.length` 換算 `shadow_ratio` |
| pgr_dijkstra SQL 用 `%(pgr_sql)s` 傳參會被 psycopg2 跳脫 | 改用 f-string 直接嵌入 |
| 外層 JOIN edge_shadow 無限制 sun_id 造成多列 | 加子查詢鎖定唯一 sun_id |
| 每次 request 都開新連線 | 改用 `SimpleConnectionPool` |
| `admin_routes` 不存在時整個 app 炸掉 | 加 `try/except ImportError` 保護 |
| bbox padding 固定 0.01 度 | 改為 `max(0.01, 距離 × 0.2)` 動態計算 |

### admin_routes.py

| 問題 | 修正 |
|------|------|
| 重複定義 `DB_CONFIG` 和 `get_conn()` | 抽出 `db.py`，統一使用共用 pool |
| 連線在例外時不歸還 | 所有路由改用 `try/finally` + `release_conn()` |
| GET trees/buildings 無分頁 | 加上 limit/offset/dist/search |
| POST buildings 接 geojson，但前端傳 WKT | 改接 `footprint_wkt`，用 `ST_GeomFromText` |
| 全量重算無防護 | 加上 `confirm_full_recalc: true` 確認機制 |

### index.html

| 問題 | 修正 |
|------|------|
| 路徑顏色用亂數 | 改用後端 `segment_ratios` 真實遮陰比例 |
| `stats.shade_score` 對不上後端 | 改成 `stats.shadow_ratio` |
| `calcRoute` 無 timeout | 加 AbortController 60 秒保護 |
| `reverseGeocode` 無 timeout | 加 5 秒 timeout，靜默回退 |
| `navShade` 永遠顯示 `—` | `startNav()` 時填入統計卡的遮蔭率 |
| `Math.radians` 污染內建物件 | 改為獨立函式 `toRad()` |
| 頁面載入就打 `/api/stats` 但只印 console | 直接刪除 |

### admin.html

| 問題 | 修正 |
|------|------|
| `showPage()` 用 `event.currentTarget` 抓不到 sidebar | 改成參數傳入 el |
| `alert()` UX 差 | 改成右下角 Toast |
| 下一頁無終止條件 | 用 `total` 控制按鈕 disabled |
| 搜尋每個字都打 API | 加 `debounce(400ms)` |
| 新增樹木表單缺少 `canopy_radius` | 補上輸入框 |
| 所有 fetch 無 timeout | 統一換成 `apiFetch()`（60 秒） |
| 關 Modal 後表單不清空 | `closeModal()` 自動清空 input/textarea |
| 刪除/新增按鈕無 loading 狀態 | 操作中 disabled + 顯示「中...」 |
| `deb()` 用函式物件當 key 導致互相取消 | 改用字串 key |
| Edge 查詢無 loading 狀態 | 查詢中顯示「查詢中...」並 disabled 按鈕 |
