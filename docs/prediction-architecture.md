# 走势预测架构文档

> 本文档描述 `daily_stock_analysis` 的股价走势预测与选股功能架构：从早期"每次预测实时训练、用完即弃"演进为"**训练/预测解耦、模型持久化+版本化、数据读透缓存**"，并进一步扩展出"**横截面选股打分 + 主动推荐**"能力。
>
> 本项目当前提供三类能力，口径不同、请分开理解：
> - **单票走势预测**（`/prediction/predict`）：预测某只票未来方向（绝对涨跌），能力有限（验证准确率约 52–57%）。
> - **横截面选股打分/推荐**（`/prediction/rank`、`/prediction/recommendations`）：预测"谁比谁强"的相对排序，经 walk-forward / CPCV 验证有较稳定的 alpha，是选股的主力口径。
> - **回测**：验证上述模型与策略的样本外表现，方法论详见 [回测方法论](backtest-methodology.md)。
>
> ⚠️ 免责声明：预测由轻量统计/树模型生成，仅供技术研究，**不构成任何投资建议**。
>
> 维护约束：本文件描述训练/预测/打分口径。凡改动 `model_training_service.py`、`prediction_service.py`、`stock_ranking_service.py`、`train_model.py`、`rank_snapshot.py` 的相关逻辑，须同步本文件（特征清单、标签定义、切分口径、算法超参、评估指标、CLI/接口参数）。

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
| `algorithm` | 算法标识，`logistic_regression_gd`（逻辑回归）或 `lightgbm_gbdt`（LightGBM） |
| `trained_symbols_json` / `symbol_count` | 参与训练的股票列表与数量 |
| `train_start_date` / `train_end_date` | 训练样本日期范围 |
| `horizon_days` | 标签前瞻天数（预测未来第 N 日方向/收益，默认 5 日） |
| `notes` | 备注，含 `label_mode=absolute|relative|cross_section` 标注标签口径 |
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
train(symbols, lookback_days, label_mode, algorithm, horizon, train_end, ...)
  ├─ _collect_samples: 遍历股票 → _load_daily_df(缓存, resolve_name=False)
  │                    → build_features(含大盘环境因子)
  │                    → 按 label_mode 打标签 → 汇聚成一个大样本集 (X, y, dates)
  ├─ train_model(X, y, dates, embargo=horizon, algorithm): 全局时序切分评估 + 全量 refit
  └─ repo.save_model(...): 序列化参数入库 + 版本化 + 设为激活
```

设计要点：
- **一个全局模型**：跨多只股票汇聚样本训练，而非每票一个。样本更多、更稳健，便于统一版本管理。
- **单票失败不中断**：某只股票取数/样本不足时跳过并记录，继续训练其余股票。
- **训练不解析股票名**（`resolve_name=False`），避免无谓联网。
- **训练截止日 `train_end`**：只保留 `date < train_end` 的样本，留出近期做样本外回测。

#### 标签口径 `label_mode`（决定"预测什么"）

| label_mode | 标签定义 | 基线 | 用途 |
|---|---|---|---|
| `absolute`（默认） | 未来 `horizon` 日收益 > `threshold` 记 1（绝对涨跌方向） | 多数类占比（常 55–60%） | 单票走势预测 |
| `relative` | 未来 `horizon` 日是否**跑赢大盘**（沪深300），剔除大盘 β 只看 alpha | ≈50% | 相对超额预测 |
| `cross_section` | 未来 `horizon` 日在**当日横截面**里是否属强势前 50%（先收集连续远期收益，汇聚后按交易日横向排名分强弱） | 恒 ≈50% | **选股主力**：天然市场中性、类别均衡 |

- **横截面行业中性**：若 `stock_industry` 有行业映射，`cross_section` 会在"同日同行业"内排名（`MIN_NAMES_PER_INDUSTRY_DAY=5`），否则退回"同日全市场"排名（`MIN_NAMES_PER_DAY=20`）。
- 特征前瞻对齐：末 `horizon` 行无标签，按行剔除；NaN 行剔除，保持 X/y/date 对齐。

#### 算法 `algorithm`

| algorithm | 标识 | 说明 |
|---|---|---|
| `logistic`（默认） | `logistic_regression_gd` | 手写梯度下降逻辑回归 + 标准化 + L2 + 类别权重 |
| `lightgbm` | `lightgbm_gbdt` | LightGBM GBDT，非线性、能吃交互项；用验证段早停抑制过拟合，再按最优轮数全量 refit |

#### 防泄露的时序切分（`train_model` / `_time_split_indices`）

多股票样本按股票堆叠时"行位置 ≠ 时间先后"，按行位置切会退化成"按股票切分"、时间段重叠而泄露，导致验证准确率虚高。修正为：

1. **全局时序切分**：传入与 X 行对齐的 `dates`，按真实日历日期排序，取 `train_ratio` 分位处日期为切点，切点之后为验证段（诚实的"用过去预测未来"）。
2. **purge / embargo**：训练段再往前挖掉 `embargo`（=`horizon`）个交易日，隔断"未来 N 日标签"跨越切点造成的重叠。
3. **全量 refit 上线**：用切分评估拿到诚实指标后，最终上线模型用**全部有标签样本**重训（LightGBM 复用早停得到的最优轮数），吃到最新一段行情。
4. **类别权重**：纠正正/负样本不平衡的方向偏移。

### 4.3 预测服务 `prediction_service.py`（改造）

核心函数 `predict_stock(stock_code, ...)`：

1. **取数（读透缓存）** `_load_daily_df`：
   - 先查 `stock_daily`；缓存足够（`>= lookback/2` 且新鲜度 `<= 4` 自然日）→ 直接用，零联网。
   - 否则联网增量拉取并**写回缓存**供下次复用。
   - 联网失败但缓存尚可用 → 降级用缓存；否则抛 `PredictionError`（端点转 400）。
2. **特征工程** `build_features`（口径见 `FEATURE_LABELS` / `FEATURE_ORDER`，当前约 30 个因子，随代码为准）：
   - 趋势：MA5/10/20 偏离度、均线多空排列
   - 动量：昨日涨跌、5/10/20 日动量
   - 摆动：RSI(14)、随机%K、布林%B、MACD 柱
   - 波动率：20 日波动率、ATR(14)、当日振幅
   - K 线形态：实体占比、收盘位置、跳空幅度
   - 量价：成交量比率、量能趋势、10 日量价相关
   - 换手率：绝对换手、相对 20 日均值（数据源直供，含流通盘信息）
   - 大盘/环境：指数 MA20 偏离/动量/RSI/波动率、相对大盘强弱(5/20 日)——传入 `market_df`，缺失时中性填 0
   > 扩展特征（换手率、大盘环境）整列缺失时以 0 中性填充，避免 dropna 把整只票样本清空。
3. **模型选择**：
   - `use_saved_model=True` 且存在激活模型 → `_load_active_model` 加载推理（`model.source = "trained"`），**校验特征口径一致**，不一致则退回。
   - 否则退回**实时训练**（`model.source = "on_the_fly"`，保持旧行为，功能不中断）。
4. **输出**：方向、上涨概率、置信度、因子贡献、未来 N 日价格路径（含置信带）、指标、模型元信息。

### 4.4 训练入口 CLI `train_model.py`

用户掌控训练时机的入口：

```bash
python train_model.py --symbols 600519,000001,00700   # 指定股票训练
python train_model.py --from-watchlist                 # 用 .env 的 STOCK_LIST
python train_model.py --all                            # 全市场股票池训练
python train_model.py --from-watchlist --no-refresh    # 纯本地缓存离线训练
python train_model.py --from-watchlist --schedule 18:30 # 每日定时训练(常驻)
python train_model.py --list                           # 列出模型版本
python train_model.py --activate 3                     # 回滚：设激活版本

# 选股主力模型（横截面 + LightGBM），激活名建议为 trend_xsec
python train_model.py --all --label-mode cross_section --algorithm lightgbm --name trend_xsec
```

常用参数：`--lookback`（回溯天数，默认 500）、`--name`（模型名）、`--label-mode`（`absolute`/`relative`/`cross_section`）、`--algorithm`（`logistic`/`lightgbm`）、`--horizon`（标签前瞻天数，默认 5）、`--train-end`（训练截止日，留出样本外）、`--epochs`/`--lr`（逻辑回归超参）、`--no-active`（训练但不激活）、`--notes`（备注）。定时模式复用 `src/scheduler.run_with_schedule`。

### 4.5 横截面选股与主动推荐

选股不看单票绝对涨跌，而看"当日横截面里谁更强"。这套能力经 walk-forward / CPCV 验证（见 [回测方法论](backtest-methodology.md)），扣成本后风险调整优于等权基准。

```
                rank_snapshot.py (CLI / 定时)
                          ▼
        StockRankingService.compute_snapshot   ← 重活：全市场逐票打分(纯缓存)
          ├─ 载入全市场票池 + 剔除 ST(名称含 ST)
          ├─ load_ranking_model("trend_xsec")  ← 激活的横截面模型
          ├─ score_codes(...)  逐票 build_features → 强弱分
          ├─ 附行业(stock_industry) + 名称(stocks.index.json)
          └─ RankSnapshotRepository.save_snapshot → stock_rank_snapshot 表(按 as_of_date 幂等)
                          ▼
        StockRankingService.get_recommendations  ← 轻活：读快照，毫秒级
          ├─ 全市场：默认每行业 ≤ industry_cap(=3) 做分散
          ├─ 行业内：industry=X 只在该行业排名
          └─ 概率加权建议权重(∑=1) + 全体分位 rank_pct + 回测最优口径提示(strategy)
```

设计要点：
- **打分预计算、查询派生**：全市场打分很重，由后台/定时每日算一次落库；行业榜靠"过滤 + 组内重排"从同一份快照秒级派生。
- **剔除 ST**：快照计算默认剔除名称含 `ST` 的退市风险股（与回测口径一致）。
- **行业分散上限**：全市场推荐时每行业最多 `industry_cap` 只，避免单一板块霸榜；选具体行业时该上限不生效。
- **建议权重**：概率加权（`clip(强度 - 全体中位, 0, None)` 归一），可直接当一篮子买入比例。
- **回测最优口径提示**：响应 `strategy` 字段固化 walk-forward 选出的最优交易口径（双周·概率加权·行业≤3），指导"清单怎么落到实盘"。

数据表 `stock_rank_snapshot`（`src/storage.py` → `StockRankSnapshot`）关键列：`as_of_date, code, stock_name, industry, strength_score, last_close, model_name, model_version`，唯一约束 `(as_of_date, code)`，索引 `(as_of_date, industry)`。

CLI：
```bash
python rank_snapshot.py                 # 全市场预计算一次(纯缓存，不联网)
python rank_snapshot.py --limit 300     # 试跑前 300 只
python rank_snapshot.py --schedule 17:30 # 每日收盘后定时预计算(常驻)
```

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

### 选股相关端点

| 端点 | 用途 | 关键参数 |
|---|---|---|
| `POST /api/v1/prediction/rank` | 对**一批指定股票**用横截面模型打强弱分并排序 | `codes`、`top_n`、`model_name` |
| `GET /api/v1/prediction/recommendations` | 系统**主动推荐**：读当日快照出强弱榜 | `industry`（留空=全市场）、`top_n`、`industry_cap`（默认 3，全市场生效） |
| `GET /api/v1/prediction/industries` | 当日快照的行业清单（供行业下拉） | — |
| `POST /api/v1/prediction/backtest` | 单票 walk-forward 回测（见回测文档） | 见 `PredictionBacktestRequest` |

前端页面：`apps/dsa-web/src/pages/RecommendationsPage.tsx`（选股推荐），路由 `/recommendations`。

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

## 8. 数据飞轮与演进

### 8.1 已实现

- **可持久化、可复用、用户可控的训练**（`prediction_models` + `train_model.py`）。
- **横截面选股 + 主动推荐**（`stock_rank_snapshot` + `stock_ranking_service` + 推荐接口 + 前端页面）。
- **预测结果落库与打分**：`prediction_records` 表记录每次预测；`/prediction/evaluate` 到期后从 `stock_daily` 回填实际涨跌/是否命中；`/prediction/history`、`/prediction/accuracy` 查询与聚合。

### 8.2 待演进

- **真实样本再训练（进化）**：用被市场验证过的真实战绩作为新样本重训模型，形成"越用越准"的闭环。
- **风控开关**：回测显示策略仅在"急反转行情"失灵，可加基于大盘状态的开关规避。

> 注意：回填 Job 只负责"记账/打分"，不改模型；让模型变准的是"用真实战绩再训练"这一独立步骤。

---

## 9. 相关文件索引

| 文件 | 角色 |
|---|---|
| `train_model.py` | 训练入口 CLI（支持 `--label-mode` / `--algorithm` / `--train-end`） |
| `rank_snapshot.py` | 全市场强弱榜预计算 CLI（支持 `--schedule` 定时） |
| `src/services/model_training_service.py` | 训练服务（汇聚样本 + 标签口径 + 训练 + 持久化） |
| `src/services/prediction_service.py` | 预测/打分服务（读透缓存 + 训练/切分 + 特征 + `score_codes`/`rank_stocks`） |
| `src/services/stock_ranking_service.py` | 选股推荐服务（全市场预计算 + 行业/全市推荐 + 剔除 ST + 行业分散） |
| `src/services/prediction_backtest_service.py` | 单票 walk-forward 回测服务 |
| `scripts/walk_forward_backtest.py` / `scripts/cpcv_backtest.py` / `scripts/weekly_topn_backtest.py` | 策略回测脚本（见回测文档） |
| `src/repositories/prediction_model_repo.py` | 模型仓库（存取/版本/激活） |
| `src/repositories/rank_snapshot_repo.py` | 强弱榜快照仓库（存取/行业清单/删除） |
| `src/repositories/stock_industry_repo.py` | 行业归属仓库（横截面行业中性/推荐行业过滤） |
| `src/repositories/stock_repo.py` | K 线缓存读写（复用） |
| `src/storage.py` → `PredictionModel` / `StockRankSnapshot` / `StockIndustry` | 模型元数据 / 强弱榜快照 / 行业归属表定义 |
| `api/v1/endpoints/prediction.py` | 预测/打分/推荐 API 端点 |
| `api/v1/schemas/prediction.py` | 请求/响应 schema |
| `apps/dsa-web/src/pages/RecommendationsPage.tsx` | 选股推荐前端页面 |

> 回测方法论（walk-forward / CPCV / 周度 Top-N / 单票回测）见 [backtest-methodology.md](backtest-methodology.md)。
