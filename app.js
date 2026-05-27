// ============================================
// 陰影導航 - 主程式
// 色階：深藍（涼爽）→ 深紅（炎熱）
// ============================================

const API_URL = 'http://localhost:5000';

// ============ 地圖初始化 ============
const map = L.map('map', {
    zoomControl: false,
    attributionControl: true
}).setView([25.0330, 121.5654], 14);

// 深色地圖底圖
L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
    attribution: '© OpenStreetMap © CartoDB',
    subdomains: 'abcd',
    maxZoom: 19
}).addTo(map);

// ============ 狀態管理 ============
let state = {
    mode: 'start',   // 'start' | 'end' | 'done'
    start: null,     // {lat, lng, address}
    end: null,       // {lat, lng, address}
    startMarker: null,
    endMarker: null,
    routeLayers: []  // 路徑圖層（分段顏色）
};

// ============ UI 元素 ============
const elements = {
    hintBar: document.getElementById('hintBar'),
    startInput: document.getElementById('startInput'),
    endInput: document.getElementById('endInput'),
    clearStart: document.getElementById('clearStart'),
    clearEnd: document.getElementById('clearEnd'),
    routeBtn: document.getElementById('routeBtn'),
    routeBtnText: document.getElementById('routeBtnText'),
    resultSection: document.getElementById('resultSection'),
    statDistance: document.getElementById('statDistance'),
    statShade: document.getElementById('statShade'),
    statTime: document.getElementById('statTime'),
    shadeMeterFill: document.getElementById('shadeMeterFill'),
    shadeMeterThumb: document.getElementById('shadeMeterThumb'),
    loadingOverlay: document.getElementById('loadingOverlay'),
    gpsBtn: document.getElementById('gpsBtn'),
    drawer: document.getElementById('drawer')
};

// ============ 色階計算 ============
// shade_score: 0 = 炎熱（深紅），1 = 涼爽（深藍）
function shadeToColor(score) {
    // 色階停頓點
    const stops = [
        { t: 0.0, r: 139, g: 26,  b: 26  },  // 深紅 #8b1a1a
        { t: 0.2, r: 224, g: 92,  b: 42  },  // 橘紅 #e05c2a
        { t: 0.4, r: 240, g: 192, b: 64  },  // 黃   #f0c040
        { t: 0.6, r: 74,  g: 184, b: 192 },  // 青   #4ab8c0
        { t: 0.8, r: 45,  g: 125, b: 210 },  // 藍   #2d7dd2
        { t: 1.0, r: 26,  g: 58,  b: 107 },  // 深藍 #1a3a6b
    ];

    const s = Math.max(0, Math.min(1, score));

    // 找到對應的區間
    for (let i = 0; i < stops.length - 1; i++) {
        if (s >= stops[i].t && s <= stops[i + 1].t) {
            const ratio = (s - stops[i].t) / (stops[i + 1].t - stops[i].t);
            const r = Math.round(stops[i].r + ratio * (stops[i + 1].r - stops[i].r));
            const g = Math.round(stops[i].g + ratio * (stops[i + 1].g - stops[i].g));
            const b = Math.round(stops[i].b + ratio * (stops[i + 1].b - stops[i].b));
            return `rgb(${r},${g},${b})`;
        }
    }
    return 'rgb(26,58,107)';
}

// ============ 自訂 Marker ============
function createMarker(lat, lng, type) {
    const color = type === 'start' ? '#22c55e' : '#ef4444';
    const icon = L.divIcon({
        className: '',
        html: `
            <div style="
                width: 32px;
                height: 32px;
                background: ${color};
                border-radius: 50% 50% 50% 0;
                transform: rotate(-45deg);
                box-shadow: 0 4px 12px rgba(0,0,0,0.5);
                border: 3px solid white;
            ">
                <div style="
                    width: 10px;
                    height: 10px;
                    background: white;
                    border-radius: 50%;
                    position: absolute;
                    top: 50%;
                    left: 50%;
                    transform: translate(-50%, -50%);
                "></div>
            </div>
        `,
        iconSize: [32, 32],
        iconAnchor: [16, 32]
    });

    return L.marker([lat, lng], { icon });
}

// ============ 地圖點擊事件 ============
map.on('click', async (e) => {
    const { lat, lng } = e.latlng;

    if (state.mode === 'start') {
        setStart(lat, lng);
    } else if (state.mode === 'end') {
        setEnd(lat, lng);
    }
});

function setStart(lat, lng) {
    state.start = { lat, lng };
    state.mode = 'end';

    // 更新 Marker
    if (state.startMarker) map.removeLayer(state.startMarker);
    state.startMarker = createMarker(lat, lng, 'start').addTo(map);

    // 更新 UI
    elements.startInput.value = `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
    setHint('點擊地圖設定終點 🏁');
    updateRouteBtn();

    // 反向地理編碼（非同步更新地址）
    reverseGeocode(lat, lng).then(addr => {
        if (addr) elements.startInput.value = addr;
    });
}

function setEnd(lat, lng) {
    state.end = { lat, lng };
    state.mode = 'done';

    // 更新 Marker
    if (state.endMarker) map.removeLayer(state.endMarker);
    state.endMarker = createMarker(lat, lng, 'end').addTo(map);

    // 更新 UI
    elements.endInput.value = `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
    setHint('點擊「計算路徑」開始導航 🗺️');
    updateRouteBtn();

    // 反向地理編碼
    reverseGeocode(lat, lng).then(addr => {
        if (addr) elements.endInput.value = addr;
    });
}

// ============ 反向地理編碼 ============
async function reverseGeocode(lat, lng) {
    try {
        const resp = await fetch(
            `https://nominatim.openstreetmap.org/reverse?lat=${lat}&lon=${lng}&format=json`,
            { headers: { 'User-Agent': 'ShadowNav/1.0' } }
        );
        const data = await resp.json();
        return data.display_name?.split(',').slice(0, 2).join(',') || null;
    } catch {
        return null;
    }
}

// ============ 計算路徑 ============
async function calculateRoute() {
    if (!state.start || !state.end) return;

    // 顯示 Loading
    elements.loadingOverlay.style.display = 'flex';
    clearRoute();

    try {
        const resp = await fetch(`${API_URL}/api/route`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                start: { lat: state.start.lat, lng: state.start.lng },
                end: { lat: state.end.lat, lng: state.end.lng }
            })
        });

        const data = await resp.json();

        if (data.status !== 'success') {
            alert('找不到路徑！請確認起點和終點在台北市路網內。');
            return;
        }

        drawRoute(data.route);
        showStats(data.route.statistics);

    } catch (err) {
        console.error('API 錯誤：', err);
        alert('無法連線到後端，請確認 python app.py 正在執行中。');
    } finally {
        elements.loadingOverlay.style.display = 'none';
    }
}

// ============ 繪製路徑（分段染色）============
function drawRoute(route) {
    clearRoute();

    const path = route.path;
    const shadeScore = route.statistics.shade_score;

    if (!path || path.length < 2) return;

    // 分段繪製（每兩點之間一條線）
    for (let i = 0; i < path.length - 1; i++) {
        // 模擬每段的遮蔭分數（實際上應從後端取得每段的 shade_score）
        // 這裡用整體分數 + 隨機微小變化來展示效果
        const segmentShade = Math.max(0, Math.min(1,
            shadeScore + (Math.random() - 0.5) * 0.3
        ));

        const color = shadeToColor(segmentShade);

        const segment = L.polyline(
            [[path[i][0], path[i][1]], [path[i+1][0], path[i+1][1]]],
            {
                color: color,
                weight: 6,
                opacity: 0.9,
                lineCap: 'round',
                lineJoin: 'round'
            }
        ).addTo(map);

        // 黑色外框（讓路徑更清楚）
        const outline = L.polyline(
            [[path[i][0], path[i][1]], [path[i+1][0], path[i+1][1]]],
            {
                color: 'rgba(0,0,0,0.3)',
                weight: 9,
                opacity: 0.5,
                lineCap: 'round',
                lineJoin: 'round'
            }
        ).addTo(map);

        state.routeLayers.push(outline, segment);
    }

    // 地圖縮放到路徑範圍
    const bounds = L.latLngBounds(path.map(p => [p[0], p[1]]));
    map.fitBounds(bounds, { padding: [80, 80] });
}

// ============ 清除路徑 ============
function clearRoute() {
    state.routeLayers.forEach(layer => map.removeLayer(layer));
    state.routeLayers = [];
}

// ============ 顯示統計 ============
function showStats(stats) {
    elements.resultSection.style.display = 'block';

    // 距離
    const dist = stats.distance;
    elements.statDistance.textContent = dist >= 1000
        ? `${(dist/1000).toFixed(1)}km`
        : `${Math.round(dist)}m`;

    // 遮蔭率
    const shade = Math.round(stats.shade_score * 100);
    elements.statShade.textContent = `${shade}%`;

    // 時間
    elements.statTime.textContent = `${stats.estimated_time}分`;

    // 溫度計（shade_score 越高越涼，指針越往右）
    const pct = (1 - stats.shade_score) * 100;
    elements.shadeMeterFill.style.width = `${pct}%`;
    elements.shadeMeterThumb.style.left = `${stats.shade_score * 100}%`;

    setHint('');
}

// ============ GPS 定位 ============
elements.gpsBtn.addEventListener('click', () => {
    if (!navigator.geolocation) {
        alert('你的瀏覽器不支援定位功能');
        return;
    }

    elements.gpsBtn.style.opacity = '0.5';

    navigator.geolocation.getCurrentPosition(
        (pos) => {
            const lat = pos.coords.latitude;
            const lng = pos.coords.longitude;

            map.setView([lat, lng], 16);
            setStart(lat, lng);
            elements.gpsBtn.style.opacity = '1';
        },
        (err) => {
            alert('定位失敗，請允許瀏覽器存取位置。');
            elements.gpsBtn.style.opacity = '1';
        }
    );
});

// ============ 清除按鈕 ============
elements.clearStart.addEventListener('click', () => {
    state.start = null;
    elements.startInput.value = '';
    if (state.startMarker) {
        map.removeLayer(state.startMarker);
        state.startMarker = null;
    }
    state.mode = 'start';
    clearRoute();
    elements.resultSection.style.display = 'none';
    setHint('點擊地圖設定起點 📍');
    updateRouteBtn();
});

elements.clearEnd.addEventListener('click', () => {
    state.end = null;
    elements.endInput.value = '';
    if (state.endMarker) {
        map.removeLayer(state.endMarker);
        state.endMarker = null;
    }
    if (state.start) state.mode = 'end';
    clearRoute();
    elements.resultSection.style.display = 'none';
    setHint('點擊地圖設定終點 🏁');
    updateRouteBtn();
});

// ============ 行政區快選 ============
document.querySelectorAll('.district-chip').forEach(chip => {
    chip.addEventListener('click', () => {
        const lat = parseFloat(chip.dataset.lat);
        const lng = parseFloat(chip.dataset.lng);
        map.setView([lat, lng], 15);
    });
});

// ============ 計算按鈕 ============
elements.routeBtn.addEventListener('click', calculateRoute);

// ============ 工具函數 ============
function setHint(text) {
    if (!text) {
        elements.hintBar.classList.add('hidden');
        return;
    }
    elements.hintBar.textContent = text;
    elements.hintBar.classList.remove('hidden');
}

function updateRouteBtn() {
    if (state.start && state.end) {
        elements.routeBtn.disabled = false;
        elements.routeBtnText.textContent = '🌿 計算最涼爽路線';
    } else if (state.start) {
        elements.routeBtn.disabled = true;
        elements.routeBtnText.textContent = '請設定終點';
    } else {
        elements.routeBtn.disabled = true;
        elements.routeBtnText.textContent = '請設定起點和終點';
    }
}

// ============ 初始化 ============
setHint('點擊地圖設定起點 📍');
console.log('🚀 陰影導航已啟動');
