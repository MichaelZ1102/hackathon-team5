# weather_events.json 使用文档

## 1. 概述

`weather_events.json` 是佛罗里达州极端天气事件数据集，**仅包含原始天气事件数据**，供下游 AI 或应用自行消费与分析。

- **下游 AI**：读取事件、时间线与气象指标，自行实现影响判定与业务分析
- **Google Maps 前端**（可选）：在地图上绘制影响范围、路径与时间轴动画

| 属性 | 值 |
|------|-----|
| 文件位置 | `weather-events/weather_events.json`（与本目录下 `hurricane_map.html` 同级） |
| Schema 版本 | `1.0.0` |
| 坐标系 | WGS84 |
| 坐标顺序 | GeoJSON 标准 **`[longitude, latitude]`**（经度在前） |
| 事件总数 | 12 |
| 事件类型 | `hurricane`（飓风）、`blizzard`（暴风雪） |
| 数据性质 | 历史复盘 + 模拟预警（混合） |

**地理范围**：佛罗里达州大致边界 — 经度 -87.63 ~ -79.97，纬度 24.40 ~ 31.00。

**重要说明**（见 `metadata.notes`）：

- 暴风雪事件仅限 **Panhandle（北部狭长地带）** 罕见场景，不代表全州冬季风险
- `status: "forecast"` 的事件为 **模拟预警**，非 NWS 官方预报
- 历史事件的坐标与时间为近似值，用于演示与分析，非精气象再分析数据

---

## 2. 顶层结构

```json
{
  "metadata": { ... },
  "events": [ ... ]
}
```

```
weather_events.json
├── metadata          # 数据集元信息
└── events[]          # 12 条天气事件
    ├── 基本信息       # id, name, type, status, severity, timeRange
    ├── 空间索引       # boundingBox, centerPoint, affectedCounties
    ├── track           # GeoJSON LineString（可选，飓风/低压漂移路径）
    ├── timeline[]      # 核心：按时间推进的影响阶段
    └── mapRendering    # 前端地图样式提示（可选）
```

---

## 3. metadata 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `schemaVersion` | string | 当前为 `"1.0.0"`，用于版本兼容 |
| `region` | string | 区域标识，固定为 `"Florida, USA"` |
| `coordinateSystem` | string | `"WGS84"` |
| `generatedAt` | string | ISO 8601 UTC 生成时间 |
| `totalEvents` | number | 事件总数（当前 12） |
| `eventTypes` | string[] | 包含的事件类型列表 |
| `notes` | string | 数据用途与限制说明 |

---

## 4. Event（单条事件）字段

### 4.1 基本信息

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | string | ✓ | 唯一标识，格式如 `FL-HUR-2022-IAN`、`FL-BLZ-2018-01` |
| `name` | string | ✓ | 可读名称 |
| `type` | `"hurricane"` \| `"blizzard"` | ✓ | 天气类型 |
| `status` | `"historical"` \| `"forecast"` | ✓ | 历史复盘 / 未来预警 |
| `severity` | object | ✓ | 强度等级（见下表） |
| `timeRange` | object | ✓ | `{ start, end }`，ISO 8601 UTC |

**severity 对象**：

| 类型 | scale | level 含义 | 示例 label |
|------|-------|-----------|-----------|
| hurricane | Saffir-Simpson | 1–5 | `"Category 4"` |
| blizzard | NWS | 1–4 | `"Major"`, `"Extreme"` |

### 4.2 空间索引

| 字段 | 类型 | 说明 |
|------|------|------|
| `boundingBox` | object | `{ west, south, east, north }`，快速空间过滤与 `fitBounds` |
| `centerPoint` | object | `{ lat, lng }`，地图默认中心（注意：此处为 **lat/lng 分开**，与 GeoJSON 顺序不同） |
| `affectedCounties` | string[] | 受影响县名，可与资产的 `county` 字段关联 |

### 4.3 track（移动路径）

| 字段 | 类型 | 说明 |
|------|------|------|
| `track` | GeoJSON LineString | 飓风中心路径或暴风雪低压漂移路径 |
| `track.type` | `"LineString"` | 固定值 |
| `track.coordinates` | `number[][]` | `[[lng, lat], ...]` |

### 4.4 mapRendering（前端样式）

| 字段 | 类型 | 说明 |
|------|------|------|
| `layerType` | string | 图层类型，当前均为 `"polygon"` |
| `strokeColor` | string | 边框颜色（HEX） |
| `strokeOpacity` | number | 边框透明度 0–1 |
| `strokeWeight` | number | 边框宽度（像素） |
| `fillColor` | string | 填充颜色（HEX） |
| `fillOpacity` | number | 填充透明度 0–1 |
| `zIndex` | number | 图层叠放顺序 |
| `showTrack` | boolean | 是否绘制 `track` 折线 |
| `timelineAnimation` | boolean | 是否支持时间轴动画切换 stage |

---

## 5. Timeline Stage（时间线阶段）

每个 `timeline` 元素代表事件在某一时刻的影响状态。

| 字段 | 类型 | 说明 |
|------|------|------|
| `stageId` | string | 阶段唯一 ID，如 `landfall`、`peak`、`watch` |
| `timestamp` | string | ISO 8601 UTC 时间戳 |
| `phase` | string | 阶段语义：`formation`、`approach`、`landfall`、`peak`、`weakening`、`dissipating`、`watch`、`warning`、`ending` |
| `geometry` | GeoJSON Polygon | 当前影响范围多边形（飓风为 34kt 风圈外沿） |
| `center` | object | 飓风阶段中心 `{ lat, lng }`（仅 hurricane） |
| `windRadiiKm` | object | 风圈半径（km）：`34kt` / `50kt` / `64kt`（仅 hurricane） |
| `windRadiiGeometry` | object | 各风圈 GeoJSON Polygon，键同 `windRadiiKm`（仅 hurricane） |
| `windSpeedKph` | number | 风速（km/h） |
| `windSpeedMph` | number | 风速（mph） |
| `pressureHpa` | number | 气压（hPa），飓风为主 |
| `precipitationMm` | number \| null | 降水量（mm） |
| `snowfallCm` | number \| null | 降雪量（cm），暴风雪为主 |
| `visibilityKm` | number \| null | 能见度（km），暴风雪为主 |
| `impactRadiusKm` | number | 影响半径（km），用于距离判定 |
| `confidence` | number | 0–1，该阶段数据可信度 |

### 飓风 vs 暴风雪 stage 差异

| 特征 | hurricane | blizzard |
|------|-----------|----------|
| geometry 含义 | 34kt 热带风暴风圈（最大影响范围） | 降雪覆盖区 |
| 中心位置 | `center` + `track` 路径 | `track` 为低压漂移路径 |
| 分级风圈 | `windRadiiKm` / `windRadiiGeometry`（34/50/64 kt） | — |
| 关键指标 | `windSpeedKph`、`pressureHpa` | `snowfallCm`、`visibilityKm` |
| 典型 phase 序列 | formation → approach → landfall → peak → weakening | watch → warning → peak → ending |
| `snowfallCm` | 通常为 `null` | 有具体数值 |
| forecast confidence | 0.55–0.75 | 0.48–0.65 |

---

## 6. 事件清单（12 条）

### 历史飓风（6 条，`status: "historical"`）

| ID | 名称 | 时间 | 区域 | 等级 |
|----|------|------|------|------|
| FL-HUR-2022-IAN | Hurricane Ian | 2022-09-26 ~ 09-29 | SW Florida | Cat 4 |
| FL-HUR-2018-MICHAEL | Hurricane Michael | 2018-10-08 ~ 10-11 | Panhandle | Cat 5 |
| FL-HUR-2017-IRMA | Hurricane Irma | 2017-09-09 ~ 09-11 | Keys + Peninsula | Cat 4 |
| FL-HUR-2005-WILMA | Hurricane Wilma | 2005-10-23 ~ 10-25 | South Florida | Cat 3 |
| FL-HUR-2004-CHARLEY | Hurricane Charley | 2004-08-12 ~ 08-14 | SW Florida | Cat 4 |
| FL-HUR-1992-ANDREW | Hurricane Andrew | 1992-08-23 ~ 08-25 | Miami-Dade | Cat 5 |

### 预警飓风（3 条，`status: "forecast"`）

| ID | 名称 | 预计时间 | 区域 | 等级 |
|----|------|----------|------|------|
| FL-HUR-2026-FCST-01 | Tropical Storm Marco | 2026-08-15 ~ 08-18 | Tampa Bay | Cat 2 |
| FL-HUR-2026-FCST-02 | Hurricane Elena | 2026-09-22 ~ 09-26 | Keys → East Coast | Cat 3 |
| FL-HUR-2027-FCST-03 | Hurricane Delta | 2027-06-01 ~ 06-04 | Panhandle | Cat 1 |

### 暴风雪（3 条）

| ID | 名称 | 时间 | 区域 | status |
|----|------|------|------|--------|
| FL-BLZ-2018-01 | Panhandle Winter Storm | 2018-01-02 ~ 01-04 | Escambia/Santa Rosa | historical |
| FL-BLZ-2027-FCST-01 | North FL Blizzard Warning | 2027-01-12 ~ 01-14 | Panhandle | forecast |
| FL-BLZ-2027-FCST-02 | Big Bend Ice Storm | 2027-02-02 ~ 02-04 | Jefferson/Madison | forecast |

---

## 7. Google Maps 集成指南

### 7.1 加载数据

```javascript
const response = await fetch("/weather_events.json");
const { metadata, events } = await response.json();
```

### 7.2 初始化地图视野

```javascript
function fitEventBounds(map, event) {
  const bb = event.boundingBox;
  const bounds = new google.maps.LatLngBounds(
    { lat: bb.south, lng: bb.west },
    { lat: bb.north, lng: bb.east }
  );
  map.fitBounds(bounds);
}
```

### 7.3 绘制 timeline 多边形

GeoJSON 坐标为 `[lng, lat]`，Google Maps 需要 `{ lat, lng }`，需转换：

```javascript
function geoJsonToPaths(polygon) {
  return polygon.coordinates[0].map(([lng, lat]) => ({ lat, lng }));
}

function drawStage(map, stage, style) {
  return new google.maps.Polygon({
    paths: geoJsonToPaths(stage.geometry),
    strokeColor: style.strokeColor,
    strokeOpacity: style.strokeOpacity,
    strokeWeight: style.strokeWeight,
    fillColor: style.fillColor,
    fillOpacity: style.fillOpacity,
    map,
    zIndex: style.zIndex,
  });
}
```

### 7.4 绘制飓风路径

```javascript
function drawTrack(map, event) {
  if (!event.mapRendering.showTrack || !event.track) return;

  const path = event.track.coordinates.map(([lng, lat]) => ({ lat, lng }));
  return new google.maps.Polyline({
    path,
    strokeColor: event.mapRendering.strokeColor,
    strokeOpacity: 0.9,
    strokeWeight: 3,
    map,
  });
}
```

### 7.5 时间轴动画

```javascript
let currentStageIndex = 0;
let activePolygon = null;

function showStage(map, event, index) {
  if (activePolygon) activePolygon.setMap(null);

  const stage = event.timeline[index];
  activePolygon = drawStage(map, stage, event.mapRendering);

  const metrics = event.type === "hurricane"
    ? `Wind: ${stage.windSpeedMph} mph, Pressure: ${stage.pressureHpa} hPa`
    : `Snow: ${stage.snowfallCm} cm, Visibility: ${stage.visibilityKm} km`;

  console.log(`${stage.phase} @ ${stage.timestamp} — ${metrics}`);
}

// 绑定 slider：slider.value → showStage(map, selectedEvent, slider.value)
```

### 7.6 推荐渲染流程

```mermaid
flowchart LR
    A[加载 JSON] --> B[按 boundingBox fitBounds]
    B --> C[绘制 track Polyline]
    C --> D[默认显示首个 timeline stage]
    D --> E[时间轴 slider 切换 stage]
    E --> F[更新 Polygon + InfoWindow]
```

---

## 8. 下游 AI 数据消费指引

本文件不包含预计算的影响评分、损失估算或 fatalities 等分析结果；下游 AI 应基于以下原始字段自行分析。

### 8.1 可用原始数据

| 维度 | 字段 | 用途 |
|------|------|------|
| 事件筛选 | `type`、`status`、`severity`、`timeRange` | 按类型/时段/强度过滤 |
| 空间范围 | `boundingBox`、`centerPoint`、`affectedCounties` | 粗筛与县级关联 |
| 时序演变 | `timeline[].timestamp`、`phase`、`geometry` | 按时间点获取影响范围 |
| 气象指标 | `windSpeedKph`、`pressureHpa`、`snowfallCm`、`visibilityKm` 等 | 强度判定 |
| 路径与范围 | `track`、`center`、`windRadiiKm`、`geometry` | 中心轨迹 + 分级风圈 |
| 预报可信度 | `timeline[].confidence` | 仅 `forecast` 事件需关注 |

### 8.2 加载示例

```python
import json

with open("weather_events.json", encoding="utf-8") as f:
    data = json.load(f)

for event in data["events"]:
    print(event["id"], event["type"], event["severity"]["label"])
    for stage in event["timeline"]:
        print(f"  {stage['timestamp']} {stage['phase']}")
```

### 8.3 常用过滤条件

| 场景 | 过滤条件 |
|------|----------|
| 仅历史事件 | `event["status"] == "historical"` |
| 仅预警 | `event["status"] == "forecast"` |
| 按类型 | `event["type"] in ("hurricane", "blizzard")` |
| 按县 | `county in event["affectedCounties"]` |
| 按时间 | `event["timeRange"]["start"]` / `end` 与查询窗口重叠 |
| 按强度 | `event["severity"]["level"] >= 3` |
| 高可信度预警阶段 | `event["status"] == "forecast" and stage["confidence"] >= 0.6` |

---

## 9. 坐标系注意事项

本数据集存在 **两种坐标表示方式**，集成时请勿混淆：

| 位置 | 格式 | 示例 |
|------|------|------|
| GeoJSON（`geometry`、`track`） | `[lng, lat]` | `[-81.8744, 26.7153]` |
| `centerPoint`、`boundingBox` | 分开字段 | `{ "lat": 26.7153, "lng": -81.8744 }` |

Google Maps API 统一使用 `{ lat, lng }`；Shapely / GeoJSON 使用 `(lng, lat)` 或 `[lng, lat]`。

---

## 10. 数据质量与使用限制

1. **纯数据文件**：不含损失估算、伤亡统计或影响评分等衍生分析字段。
2. **非官方气象数据**：不可用于生命财产决策，仅用于演示、原型开发与 AI 测试。
3. **几何简化**：Polygon 为 4–5 顶点的矩形近似，适合地图展示与 point-in-polygon，不适合精细气象分析。
4. **forecast 事件**：`confidence` 普遍低于 historical（约 0.48–0.75），下游分析时应自行处理不确定性。
5. **暴风雪范围**：仅覆盖 Panhandle / Big Bend，不应外推到全州。
6. **ID 命名规则**：
   - 飓风历史：`FL-HUR-{YEAR}-{NAME}`
   - 飓风预警：`FL-HUR-{YEAR}-FCST-{NN}`
   - 暴风雪：`FL-BLZ-{YEAR}-{NN}` 或 `FL-BLZ-{YEAR}-FCST-{NN}`

---

## 11. 快速验证

```bash
python -c "
import json
d = json.load(open('weather_events.json'))
print('events:', len(d['events']))
print('types:', {e['type'] for e in d['events']})
print('historical:', sum(1 for e in d['events'] if e['status']=='historical'))
print('forecast:', sum(1 for e in d['events'] if e['status']=='forecast'))
"
```
