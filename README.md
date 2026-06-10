# Florida Tornado Response AI Analysis

本项目是 hackathon 场景下的 AI 分析模块资料与 demo 数据集合，围绕“佛罗里达州龙卷风应对”场景，帮助资产管理团队在灾前、灾中、灾后做风险识别、行动推荐、通知生成和维修工单草稿生成。

## 1. 项目目标

本项目中的 AI 分析不负责预测龙卷风本身，而是负责把以下结构化数据转化为可执行的资产管理决策：

- 天气 / 龙卷风风险时间线
- 物业资产信息
- 历史维修工单
- CapEx 资产更新计划
- 租约和入住影响
- 承包商资源

最终输出包括：

- 哪些物业受影响
- 每个物业的风险分数和风险等级
- 风险原因
- 推荐行动
- 推荐承包商
- 灾后 draft work order
- 可传给 LLM 的上下文

## 2. 目录说明

```text
.
├── AI分析方案.md
├── README.md
├── mock_data/
├── llm_prompts/
├── risk_engine_docs/
├── risk_engine/
└── 原始业务资料文件
```

### `AI分析方案.md`

AI 分析方案总览文档。

主要说明：

- AI 在本方案中的定位
- AI 需要完成的能力
- 推荐实现方式
- demo 展示闭环
- 后端接口建议
- 个人交付物建议

适合用来给队友或评委解释“AI 分析这部分到底做什么”。

### `mock_data/`

存放 hackathon demo 使用的模拟数据。

数据使用真实佛罗里达城市、县名和大致合理经纬度，但所有业务数据均为 synthetic demo data，不是官方真实数据。

文件说明：

| 文件 | 内容 |
| --- | --- |
| `weather_events.json` | 佛罗里达龙卷风风险时间线 |
| `properties.json` | 物业资产数据，包括位置、房龄、屋顶年龄、HVAC 年龄等 |
| `work_orders.json` | 历史维修工单 |
| `capex_plan.json` | CapEx / 资产更新计划 |
| `lease_exposure.json` | 租约、入住率、居民影响数据 |
| `contractors.json` | 灾后维修承包商资源 |
| `README.md` | mock 数据使用说明 |

### `llm_prompts/`

存放 LLM 生成自然语言内容时使用的 prompt。

LLM 的职责是把风险评分引擎输出的结构化结果转成自然语言内容，而不是计算风险分数。

文件说明：

| 文件 | 用途 |
| --- | --- |
| `property_risk_explanation_prompt.md` | 生成单个物业的风险解释 |
| `executive_summary_prompt.md` | 生成管理层摘要 |
| `tenant_notification_prompt.md` | 生成租户短信、push notification、email |
| `work_order_draft_prompt.md` | 生成灾后维修工单草稿文案 |
| `contractor_recommendation_prompt.md` | 生成承包商推荐说明 |
| `vp_email_prompt.md` | 生成发给 VP 的邮件 |
| `README.md` | prompt 使用说明 |

### `risk_engine_docs/`

存放风险评分引擎的设计文档。

文件说明：

| 文件 | 内容 |
| --- | --- |
| `scoring_algorithm.md` | 风险评分算法、权重、每类分数计算规则和示例 |
| `output_schema.md` | 风险引擎最终输出结构和字段解释 |
| `example_output.jsonc` | 带注释的完整输出示例，适合阅读和讲解 |
| `example_output.json` | 不带注释的合法 JSON 示例，可直接给程序读取 |
| `README.md` | 风险引擎文档说明 |

### `risk_engine/`

存放实际风险评分引擎代码和输出结果。

文件说明：

| 文件 | 内容 |
| --- | --- |
| `calculateRisk.js` | 风险评分函数实现 |
| `README.md` | 风险引擎运行说明 |
| `output/risk_analysis_result.json` | 风险评分引擎生成的最终结果 |

## 3. 我负责的 AI 分析部分

我负责的是 AI 分析链路中的“结构化风险分析 + LLM 内容生成准备”。

可以拆成两层：

### 第一层：风险评分引擎

风险评分引擎读取 mock 数据，计算每个受影响物业的风险。

核心公式：

```text
riskScore =
  weatherRisk * 0.40
+ assetVulnerability * 0.25
+ maintenanceRisk * 0.25
+ businessImpact * 0.10
```

四类分数含义：

| 分数 | 含义 |
| --- | --- |
| `weatherRisk` | 当前时间点该物业所在 county 的龙卷风天气风险 |
| `assetVulnerability` | 物业自身脆弱性，例如屋顶年龄、HVAC 年龄、外墙状态、树木风险 |
| `maintenanceRisk` | 历史维修记录反映出的隐患，例如屋顶漏水、水侵、外墙维修 |
| `businessImpact` | 居民、租约、入住率和业务影响 |

风险等级：

```text
0-30    Low
31-60   Medium
61-80   High
81-100  Critical
```

### 第二层：LLM 自然语言生成

LLM 不负责算分。

LLM 只负责基于风险评分引擎的输出生成：

- 物业风险解释
- 租户短信
- APP push notification
- VP 邮件摘要
- 灾后 draft work order 描述
- 承包商推荐说明

这样设计的好处是：

- 风险等级稳定
- 分数可解释
- 前端展示可控
- LLM 不容易编造事实

## 4. 前端对接说明

前端优先对接这个文件：

```text
risk_engine/output/risk_analysis_result.json
```

该文件是风险评分引擎的最终输出，可以直接用于页面展示。

### 推荐展示字段

页面列表 / 地图 marker 建议使用：

```text
properties[].propertyId
properties[].name
properties[].market
properties[].city
properties[].county
properties[].lat
properties[].lng
properties[].riskScore
properties[].riskLevel
properties[].estimatedRepairExposure
```

物业详情页建议使用：

```text
properties[].scoreBreakdown
properties[].riskDrivers
properties[].recommendedActions
properties[].recommendedDraftWorkOrders
properties[].recommendedContractors
```

管理层 dashboard 建议使用：

```text
portfolioSummary.totalProperties
portfolioSummary.affectedProperties
portfolioSummary.criticalRiskProperties
portfolioSummary.highRiskProperties
portfolioSummary.mediumRiskProperties
portfolioSummary.estimatedRepairExposure
portfolioSummary.residentExposure
portfolioSummary.topAffectedMarkets
```

LLM 生成内容时建议使用：

```text
properties[].llmContext
llmInputs.executiveSummaryContext
```

## 5. 风险结果输出结构

`risk_engine/output/risk_analysis_result.json` 的顶层结构：

```json
{
  "eventId": "TOR-FL-2026-0612",
  "analysisTime": "2026-06-12T14:00:00-04:00",
  "scenarioStage": "Peak tornado risk",
  "portfolioSummary": {},
  "properties": [],
  "llmInputs": {},
  "dataSources": [],
  "metadata": {}
}
```

其中最重要的是：

- `portfolioSummary`：给 dashboard 展示总体影响
- `properties`：给地图、列表、详情页展示每个物业风险
- `llmInputs`：给 LLM 生成管理层摘要或 VP 邮件
- `properties[].llmContext`：给 LLM 生成单个物业相关内容

完整字段解释见：

```text
risk_engine_docs/output_schema.md
```

带注释完整示例见：

```text
risk_engine_docs/example_output.jsonc
```

## 6. 如何重新生成风险结果

当前默认分析时间点是：

```text
2026-06-12T14:00:00-04:00
```

运行：

```bash
node risk_engine/calculateRisk.js
```

会生成：

```text
risk_engine/output/risk_analysis_result.json
```

如果要指定其他时间点：

```bash
node risk_engine/calculateRisk.js 2026-06-12T17:00:00-04:00
```

可选时间点来自：

```text
mock_data/weather_events.json
```

## 7. Demo 推荐流程

前端或演示流程可以按下面顺序使用数据：

```text
1. 读取 weather_events.json 展示龙卷风时间线
2. 用户选择某个时间点
3. 风险评分引擎生成 risk_analysis_result.json
4. 前端读取 portfolioSummary 展示总体影响
5. 前端读取 properties 展示受影响物业列表和地图点位
6. 用户点击某个物业
7. 前端展示 riskScore、riskLevel、scoreBreakdown、riskDrivers
8. 前端展示 recommendedActions
9. 前端展示 recommendedDraftWorkOrders，由用户确认是否创建工单
10. LLM 使用 llmContext 生成短信、push、邮件或工单描述
```

## 8. 当前交付状态

已完成：

- AI 分析方案文档
- 佛罗里达州 demo mock data
- LLM prompt 文件
- 风险评分算法文档
- 风险引擎输出结构文档
- 风险评分函数实现
- `risk_analysis_result.json` 示例输出

未包含：

- 真实天气 API 接入
- 真实物业管理系统接口
- 真实短信 / push notification 发送
- 真实工单系统创建接口
- 机器学习模型训练

## 9. 重要说明

本项目数据用于 hackathon demo。

所有 mock 数据均为 synthetic demo data，不代表官方天气、紧急响应、保险、物业状态或真实维修数据。

风险评分结果用于演示 AI 决策支持流程，不应用于真实灾害响应决策。

