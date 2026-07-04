# 回测方法论

> 本文档描述 `daily_stock_analysis` 里预测/选股模型与策略的回测方法：目标是给出**诚实的样本外表现**，防未来函数、防数据泄露、扣真实成本。
>
> ⚠️ 免责声明：回测结果仅供技术研究，历史表现不代表未来收益，**不构成任何投资建议**。
>
> 维护约束：凡改动回测脚本或回测服务（`scripts/*backtest*.py`、`src/services/prediction_backtest_service.py`）的规则/口径，须同步本文件（回测规则、切分/防泄露口径、评估指标、CLI 参数）与 `docs/CHANGELOG.md`。

---

## 1. 为什么需要多种回测

单一"训练集/测试集一刀切"在金融时间序列上极易骗自己：模型在牛市训练、在牛市验证，看起来很准，换个市场状态就失灵。因此本项目用**三层证据**交叉验证：

| 方法 | 回答的问题 | 脚本/入口 |
|---|---|---|
| 单票 walk-forward | 单只票的方向预测在样本外命中率如何 | `POST /prediction/backtest`（`prediction_backtest_service.py`） |
| 策略 walk-forward | 选股策略扣成本后能否跑赢基准 | `scripts/walk_forward_backtest.py` |
| CPCV（组合purged交叉验证） | 策略是不是"运气好/过拟合" | `scripts/cpcv_backtest.py` |
| 周度 Top-N | 具体"周一买/周五卖、选前N"规则的真实表现 | `scripts/weekly_topn_backtest.py` |

---

## 2. 通用防泄露原则（所有回测共用）

1. **只用可见信息**：某个决策日的信号只能用该日**之前**的数据算。周一开盘买入 → 排名只能用上周五收盘及更早的数据。
2. **purge / embargo**：训练段与验证/测试段之间挖掉 `embargo`（≈标签前瞻天数）个交易日，隔断"未来 N 日标签"跨越切点造成的重叠。
3. **滚动重训**：模型定期只用当时可见的历史重训，模拟真实上线的"随时间前进不断更新"。重训频率可配（`--retrain-months`，默认月度=1；长周期回测建议季度=3 以缩短耗时）。
4. **扣成本**：按持仓变动（换手）计交易成本，双边计费。A 股口径建议 `--cost-bps 15`（≈往返 0.3%，含佣金+印花税+滑点）。
5. **剔除 ST**：退市风险股不纳入，贴近实盘。
6. **纯离线读缓存**：回测脚本一律只读本地 `stock_daily`（`_load_cached_df`），**绝不联网**，保证可复现、不受数据源限流干扰；跑长周期前需先用 `python backfill.py quote` / `python backfill.py kline` 把历史回填到位。
7. **回溯窗口**：`--lookback`（每票回溯自然日）决定能回看多久——默认 1600≈到 2019 初，跑 2018 起的长周期需 `--lookback 3000`。
8. **股票范围**：`--stocks N` 抽样 N 只（默认）；`--stocks 0`（或 ≤0）= 全市场（更准但耗时数倍）。

> ⚠️ **幸存者偏差提示**：当前票池来自 `data/cache/stocks.index.json`（**当前在市**股票），未纳入区间内已退市/长期停牌标的，故长周期回测收益存在**系统性高估**（无法买到"已消失的输家/赢家"）。结论解读时需打折扣；彻底消除需引入时点票池（point-in-time universe）。

---

## 3. 单票 walk-forward 回测

- 入口：`POST /api/v1/prediction/backtest`（服务 `src/services/prediction_backtest_service.py`）。
- 逻辑：对单只票，每隔 `retrain_every` 个交易日，仅用当时可见历史重训并预测未来 `horizon_days` 方向；逐日给出方向命中率、`up_precision`、策略资金曲线 vs 基准、最大回撤。
- 用途：直观理解"模型预测明天涨，明天实际涨没涨"。定位是**教学/单票体检**，绝对方向能力有限（约 52–57%）。

关键参数：`code`、`horizon_days`、`lookback_days`、`retrain_every`、`min_train`、`threshold`、`allow_short`、`start_date`/`end_date`。

---

## 4. 策略 walk-forward 回测（`scripts/walk_forward_backtest.py`）

模拟真实选股策略的完整闭环：

```
逐个调仓日：
  ├─ 月度重训横截面 LightGBM（train_end = 信号日 - embargo，purge）
  ├─ 用信号日截面打分 → 排名
  ├─ 组合构建：固定分位 or 概率加权
  ├─ 持有 rebal 个交易日，用真实收盘价算持有期收益
  └─ 按换手扣成本
```

对比 4 种组合构建：`Long top20% 等权`、`Long-Short top/bottom20% 等权`、`Long 概率加权`、`Long-Short 概率加权`。

关键参数：`--stocks`（≤0=全市场）、`--start`/`--end`、`--lookback`（回溯自然日，2018 起需 3000）、`--train-days`、`--horizon`、`--rebal`（调仓间隔）、`--retrain-months`（重训频率）、`--cost-bps`、`--quantile`。

**关键结论**：固定分位 + 高频调仓常被成本吃光；**概率加权 + 拉长调仓周期**显著改善净收益（扣成本后概率加权多头年化 ≈21.8% vs 市场 21.2%，夏普 0.92 vs 0.80，回撤更小）。

---

## 5. CPCV 组合 purged 交叉验证（`scripts/cpcv_backtest.py`）

把历史切成若干 block，穷举多种"训练/测试"组合，每次都做 purge + embargo，输出**性能分布**（IC、夏普）而非单一数字，用来判断策略是否稳健、是否过拟合。

- 参考 López de Prado《Advances in Financial Machine Learning》的 Combinatorial Purged Cross-Validation。
- **判据**：若绝大多数路径 IC 为正、夏普分布不依赖某个特定切分，则策略非"运气好"。本项目横截面策略 IC 路径 100% 为正。

---

## 6. 周度 Top-N 回测（`scripts/weekly_topn_backtest.py`）

针对"每周选前 N、周一集合竞价(开盘)买入、周五收盘卖出"这一具体规则的专门回测，支持一次跑多口径对比。

### 6.1 规则与防泄露
- **信号日**：调仓期首个交易日（周一）**之前**的最后一个交易日（上周五）。排名只用它及更早的数据。
- **成交价**：买入=周一开盘、卖出=期末周五收盘；单期收益 = 期末收盘 / 期初开盘 − 1。
- **定期重训**（`--retrain-months`，默认月度；长周期建议季度）+ purge/embargo；**剔除 ST**；按换手（L1 权重变动）扣双边成本。

### 6.2 两阶段结构（省算力）
1. 阶段1（贵）：逐周打分一次，缓存每周排名与概率。
2. 阶段2（廉价）：从缓存排名模拟多种调仓/分散口径，无需重复训练。

### 6.3 对比口径与结论
配置：持有周数（1/2/4 周）、组合构建（等权前 N / 概率加权）、`--keep-rank`（排名缓冲降换手）、行业分散上限 `cap`。

**关键结论**：
- 短窗（2024–2026）曾得出"双周·概率加权·行业≤3"胜出；但**长周期（2020–2026，样本外）复核后被推翻**——见下。
- 长周期（2020–2026，1881 只，扣成本 15bp/边，季度重训）：
  - "周调·前20等权"（原始规则）夏普仅 0.45、超额 **-5.3%/年跑输基准**；周度满仓换手把利润吃光。
  - **"双周·等权·缓冲·行业≤3" 综合最优/最稳**：累计 210.8%、年化 19.4%、**夏普 0.80**、**回撤 -16.6%(全场最低)**，优于等权基准（夏普 0.64 / 回撤 -29.1%）；月度·等权收益/夏普更高（20.7%/0.94）但样本偏少、回撤略大。
  - ⚠️ **等权稳定优于概率加权**（双周等权夏普 0.80 vs 概率加权 0.66）：短窗的"概率加权更好"是特定行情产物（过拟合嫌疑），跨多状态不成立。
- 破除误区：**周一开盘入场优于收盘入场**（收盘追高，一周内均值回归被套）。
- 最优口径已固化到线上推荐接口的 `strategy` 提示（`stock_ranking_service.STRATEGY_HINT`=双周·等权·缓冲·行业≤3）。完整长周期报告见 [backtest-report-2018.md](backtest-report-2018.md)。

关键参数：`--stocks`（≤0=全市场）、`--top-n`、`--start`/`--end`、`--lookback`（2018 起需 3000）、`--retrain-months`（长周期建议 3）、`--horizon`、`--cost-bps`、`--keep-rank`、`--probw-k`、`--keep-st`。

### 6.4 长周期回测（2018→今）

已具备用 2018-07 起全历史回测的能力。推荐命令（2000 只样本、季度重训、A股成本）：

```bash
python scripts/weekly_topn_backtest.py --stocks 2000 --top-n 20 \
    --start 2020-01-01 --end 2026-07-01 \
    --lookback 3000 --retrain-months 3 --cost-bps 15
```

- 回测样本外区间取 2020→今：把 2018-07~2019 留作**初始训练热身**（特征需 ~150 根 K 线、模型需 ≥1.5 年可见历史）。
- 覆盖 2020 疫情急跌、2021 结构牛、2022 熊、2023 震荡、2024–25 修复等多种市场状态，比只测近两年更能反映真实稳健性。
- 结果与解读见 [backtest-report-2018.md](backtest-report-2018.md)。

---

## 7. 评估指标词典

| 指标 | 含义 |
|---|---|
| 方向命中率 | 预测涨跌方向答对的比例（单票口径） |
| 基线 | 永远猜多数类能达到的准确率；模型须显著高于它才有增量 |
| Rank IC | 预测排名与实际未来收益排名的相关性（横截面选股核心指标，>0 即有选股能力） |
| ICIR | IC 的均值 / 标准差，衡量选股能力的稳定性 |
| 夏普比率 | 收益 / 波动的风险调整收益，年化 |
| 最大回撤 | 资金曲线从高点到低点的最大跌幅 |
| 换手率 | 每次调仓的持仓变动比例，直接决定交易成本 |
| 超额 | 相对同期基准（等权全市场/指数）的收益差 |

---

## 8. 相关文件索引

| 文件 | 角色 |
|---|---|
| `src/services/prediction_backtest_service.py` | 单票 walk-forward 回测服务 |
| `scripts/walk_forward_backtest.py` | 策略 walk-forward 回测（多组合构建对比） |
| `scripts/cpcv_backtest.py` | CPCV 稳健性回测（IC/夏普分布） |
| `scripts/weekly_topn_backtest.py` | 周度 Top-N 回测（多调仓/分散口径对比） |
| `api/v1/endpoints/prediction.py` → `/prediction/backtest` | 单票回测 API |
| `docs/prediction-architecture.md` | 训练/预测/打分架构（本文档的上游） |

> 训练与打分口径（特征、标签、切分、算法）见 [prediction-architecture.md](prediction-architecture.md)。
