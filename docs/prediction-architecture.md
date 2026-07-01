# 走势预测架构文档

> 本文档描述 `daily_stock_analysis` 的股价走势预测功能架构：从早期"每次预测实时训练、用完即弃"演进为"**训练/预测解耦、模型持久化+版本化、数据读透缓存**"的可复用架构。
>
> ⚠️ 免责声明：预测由轻量统计模型生成，仅供技术研究，**不构成任何投资建议**。

---

## 1. 设计目标与演进

### 1.1 旧模式的问题（改造前）

预测请求链路为：`联网拉 K 线 → 内存算特征 → 内存训练 → 预测 → 结果返回后数据全部销毁`。

| 问题 | 说明 |
|---|---|
| 重复联网 | 同一只票反复预测，每次都重新抓行情，慢且依赖网络、易被限流 |
| 重复训练 | 每次从零训模型，浪费计算 |
| 结果不留痕 | 预测完即丢，无法追溯、无法评估准确率 |
| 模型不可复用 | 训练产物无版本、无法回滚、无法离线批量预测 |

### 1.2 改造后的核心思想（参考 invest_dojo）

借鉴 `invest_dojo` 量化模块的三个工程精华，但**适配本项目 SQLite 单机规模**、避免过度设计：

| invest_dojo 的能力 | 本项目的落地方式 |
|---|---|
| 特征库 / 数据分层 | 复用**已存在**的 `stock_daily` 表做 K 线读透缓存，不新建大表 |
| 模型版本化（models / model_versions + MinIO） | 新增 `prediction_models` 表，模型参数**以 JSON 直接入库**，省去对象存储 |
| 训练作为独立异步任务（Celery train-svc） | 训练作为**用户可控的离线 CLI 任务**（手动 / 定时），无需引入 Celery/Redis |

> 关键取舍：`invest_dojo` 的训练目前仍是占位符（`dummy_train`），其价值在架构；本项目的模型是**真实可训练**的轻量逻辑回归，改造后两者优点结合——**真模型 + 可复用架构**。

---

## 2. 系统总览

```
                      训练入口（用户掌控）
                ┌──────────────────────────────┐
                │ train_model.py (CLI)          │
                │  --symbols / --from-watchlist  │
                │  --schedule HH:MM (定时常驻)    │
                └───────────────┬──────────────┘
                                ▼
                ┌──────────────────────────────┐
                │ ModelTrainingService           │
                │ 汇聚多股票样本 → 训练全局模型    │
                └───┬───────────────────────┬───┘
          读K线(缓存优先)│                    │写模型(版本化+激活)
                    ▼                        ▼
        ┌──────────────────┐      ┌──────────────────────┐
        │ StockRepository    │      │ PredictionModelRepo    │
        │ stock_daily (已存在) │      │ prediction_models (新增) │
        └────────┬─────────┘      └───────────┬──────────┘
                 │ 读透缓存                     │ 加载激活模型
                 ▼                             ▼
        ┌──────────────────────────────────────────────┐
        │ prediction_service.predict_stock               │
        │  取数(缓存) → 特征 → [加载激活模型 | 实时训练]    │
        │            → 预测方向/概率 → 推演价格路径         │
        └───────────────────────┬──────────────────────┘
                                ▼
                ┌──────────────────────────────┐
                │ POST /api/v1/prediction/predict│
                └───────────────┬──────────────┘
                                ▼
                        前端预测页面 / Bot
```

---

## 3. 数据模型

### 3.1 原始行情：`stock_daily`（复用，未改）

存储每日 OHLC + 成交量 + 技术指标 + 数据来源。由主分析流程 `pipeline.py`、回测 `backtest_service` 及本次的预测/训练共同读写，形成**共享缓存**。

关键列：`code, date, open, high, low, close, volume, amount, pct_chg, ma5/10/20, data_source`，唯一约束 `(code, date)`。

### 3.2 模型仓库：`prediction_models`（新增）

定义见 `src/storage.py` 的 `PredictionModel`。模型本体极小（`n_features` 个权重 + 偏置 + 标准化 mean/std），因此参数直接以 JSON 存 DB。

| 列 | 含义 |
|---|---|
| `id` | 主键 |
| `name` | 模型名（默认 `trend_lr`），同名下按版本管理 |
| `version` | 版本号，训练时间戳，如 `20260701_114548` |
| `algorithm` | 算法标识，`logistic_regression_gd` |
| `trained_symbols_json` / `symbol_count` | 参与训练的股票列表与数量 |
| `train_start_date` / `train_end_date` | 训练样本日期范围 |
| `horizon_days` | 标签口径（预测未来第几日方向，当前为 1=次日） |
| `feature_names_json` | 特征顺序（加载时校验口径一致性） |
| `params_json` | 模型参数 `{n_features, weights, bias, mean, std}` |
| `train_samples` / `valid_samples` | 训练/验证样本数 |
| `train_accuracy` / `valid_accuracy` / `baseline_accuracy` | 评估指标 |
| `metrics_json` | 完整指标快照 |
| `is_active` | 是否为当前线上预测使用的激活版本 |
| `created_at` / `notes` | 创建时间 / 备注 |

唯一约束 `(name, version)`；索引 `(name, is_active)`。**同名模型任意时刻只有一个激活版本**，支持一键回滚到历史版本。

> 建表方式：ORM 模型注册后由 `Base.metadata.create_all` 自动创建，无需手写迁移。

---

## 4. 分层与职责

### 4.1 数据访问层

- **`StockRepository`（`src/repositories/stock_repo.py`，已存在）**：`get_range` 读缓存、`save_dataframe` 写缓存。
- **`PredictionModelRepository`（`src/repositories/prediction_model_repo.py`，新增）**：
  - `save_model(...)`：插入模型记录，`set_active=True` 时自动取消同名其它版本的激活。
  - `get_active(name)`：返回当前激活模型（供预测加载）。
  - `list_models(...)` / `get_by_id(...)`：版本列表与详情。
  - `set_active(model_id)`：切换激活版本（回滚）。
  - ⚠️ 写操作需显式 `session.commit()`（`get_session()` 不自动提交）。

### 4.2 训练服务 `ModelTrainingService`（`src/services/model_training_service.py`）

职责：把训练从预测链路剥离，作为**离线任务**。

```
train(symbols, lookback_days, ...)
  ├─ _collect_samples: 遍历股票 → _load_daily_df(缓存, resolve_name=False)
  │                    → build_features → 打标签(次日涨=1)
  │                    → 汇聚成一个大样本集 (X, y)   ← 全局模型
  ├─ train_model(X, y): 手写梯度下降训逻辑回归 + 时间序切分评估
  └─ repo.save_model(...): 序列化参数入库 + 版本化 + 设为激活
```

设计要点：
- **一个全局模型**：跨多只股票汇聚样本训练，而非每票一个。样本更多、更稳健，便于统一版本管理。
- **单票失败不中断**：某只股票取数/样本不足时跳过并记录，继续训练其余股票。
- **训练不解析股票名**（`resolve_name=False`），避免无谓联网。

### 4.3 预测服务 `prediction_service.py`（改造）

核心函数 `predict_stock(stock_code, ...)`：

1. **取数（读透缓存）** `_load_daily_df`：
   - 先查 `stock_daily`；缓存足够（`>= lookback/2` 且新鲜度 `<= 4` 自然日）→ 直接用，零联网。
   - 否则联网增量拉取并**写回缓存**供下次复用。
   - 联网失败但缓存尚可用 → 降级用缓存；否则抛 `PredictionError`（端点转 400）。
2. **特征工程** `build_features`：7 个技术因子（MA 偏离、动量、量比、RSI、MACD 柱等）。
3. **模型选择**：
   - `use_saved_model=True` 且存在激活模型 → `_load_active_model` 加载推理（`model.source = "trained"`），**校验特征口径一致**，不一致则退回。
   - 否则退回**实时训练**（`model.source = "on_the_fly"`，保持旧行为，功能不中断）。
4. **输出**：方向、上涨概率、置信度、因子贡献、未来 N 日价格路径（含置信带）、指标、模型元信息。

### 4.4 训练入口 CLI `train_model.py`

用户掌控训练时机的入口：

```bash
python train_model.py --symbols 600519,000001,00700   # 指定股票训练
python train_model.py --from-watchlist                 # 用 .env 的 STOCK_LIST
python train_model.py --from-watchlist --no-refresh    # 纯本地缓存离线训练
python train_model.py --from-watchlist --schedule 18:30 # 每日定时训练(常驻)
python train_model.py --list                           # 列出模型版本
python train_model.py --activate 3                     # 回滚：设激活版本
```

常用参数：`--lookback`（回溯天数，默认 500）、`--name`（模型名）、`--epochs`/`--lr`（超参）、`--no-active`（训练但不激活）、`--notes`（备注）。定时模式复用 `src/scheduler.run_with_schedule`。

---

## 5. 端到端流程

### 5.1 训练流程

```
用户执行 train_model.py
  → 解析股票列表(--symbols / --from-watchlist)
  → ModelTrainingService.train
      → 逐股票读缓存/联网 → 特征+标签 → 汇聚样本
      → train_model 训练全局逻辑回归
      → save_model 入库 prediction_models(新版本, is_active=True)
  → 打印训练摘要(版本/样本/验证准确率/基线)
```

### 5.2 预测流程（请求角度）

```
前端/API 请求 POST /api/v1/prediction/predict {code, horizon_days, lookback_days}
  → predict_stock
      → _load_daily_df 读透缓存(命中免联网)
      → build_features
      → get_active("trend_lr") 命中 → 加载模型推理(source=trained)
                              未命中 → 实时训练(source=on_the_fly)
      → 预测方向/概率 + 推演价格路径
  → PredictionResponse(含 model.source / model.version)
```

---

## 6. API 契约

- **端点**：`POST /api/v1/prediction/predict`
- **请求**（`PredictionRequest`）：`code`、`horizon_days`(1–20)、`lookback_days`(60–800)、`language`(zh/en)
- **响应**（`PredictionResponse`）：`direction`、`up_probability`、`confidence`、`expected_return_pct`、`history`、`projected`、`factors`、`metrics`，以及 `model`：
  - `algorithm` / `feature_count` / `lookback_days` / `trained_samples`
  - `source`：`trained`（加载持久化模型）| `on_the_fly`（实时训练）
  - `version` / `trained_at`：仅 `trained` 时有值

> 契约向后兼容：新增字段均为可选，旧客户端不受影响。

---

## 7. 关键取舍小结

| 决策 | 选择 | 理由 |
|---|---|---|
| 模型粒度 | 单一全局模型（跨股票汇聚） | 样本更多、更稳；便于统一版本管理，符合"训练一个走势模型" |
| 模型存储 | 参数 JSON 直接入库 | 模型极小，省去 MinIO/文件系统依赖，适配单机 |
| K 线缓存 | 复用 `stock_daily` | 与主分析/回测共享，不重复建表、不重复联网 |
| 训练调度 | CLI 手动 + `--schedule` 常驻 | 入口由用户掌控，无需引入 Celery/Redis |
| 模型缺失时 | 退回实时训练 | 平滑降级，功能永不中断 |

---

## 8. 未来演进（尚未实现）

当前实现了"**可持久化、可复用、用户可控的训练**"。若要进一步做到"**越用越准的数据飞轮**"，可在此基础上叠加：

1. **预测结果落库**：新增 `prediction_records` 表，记录每次预测。
2. **准确率回填 Job（打分）**：预测到期后，从 `stock_daily` 读实际走势，回填"实际涨跌/是否命中"，量化模型准确率。
3. **真实样本再训练（进化）**：用被市场验证过的真实战绩作为新样本重训模型——这才是让模型真正"越用越准"的一环。

> 注意：回填 Job 只负责"记账/打分"，不改模型；让模型变准的是"用真实战绩再训练"这一独立步骤。

---

## 9. 相关文件索引

| 文件 | 角色 |
|---|---|
| `train_model.py` | 训练入口 CLI |
| `src/services/model_training_service.py` | 训练服务（汇聚样本 + 训练 + 持久化） |
| `src/services/prediction_service.py` | 预测服务（读透缓存 + 加载/实时训练 + 推演） |
| `src/repositories/prediction_model_repo.py` | 模型仓库（存取/版本/激活） |
| `src/repositories/stock_repo.py` | K 线缓存读写（复用） |
| `src/storage.py` → `PredictionModel` | 模型元数据表定义 |
| `api/v1/endpoints/prediction.py` | 预测 API 端点 |
| `api/v1/schemas/prediction.py` | 请求/响应 schema |
