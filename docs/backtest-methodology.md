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
3. **滚动重训**：模型定期（月度）只用当时可见的历史重训，模拟真实上线的"随时间前进不断更新"。
4. **扣成本**：按持仓变动（换手）计交易成本，双边计费。
5. **剔除 ST**：退市风险股不纳入，贴近实盘。

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

对比 4 种组合构建：`Long top20% 等权`、`Long-Short top/bottom20% 等权`、`Long 概率加权`、`Long-Short 概率加权`。可配 `--rebal`（调仓间隔）、`--cost-bps`。

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
- **月度重训** + purge/embargo；**剔除 ST**；按换手（L1 权重变动）扣双边成本。

### 6.2 两阶段结构（省算力）
1. 阶段1（贵）：逐周打分一次，缓存每周排名与概率。
2. 阶段2（廉价）：从缓存排名模拟多种调仓/分散口径，无需重复训练。

### 6.3 对比口径与结论
配置：持有周数（1/2/4 周）、组合构建（等权前 N / 概率加权）、`--keep-rank`（排名缓冲降换手）、行业分散上限 `cap`。

**关键结论（2024–2026，1412 只，扣成本）**：
- "周调·前20等权"（原始规则）夏普仅 ≈0.32，跑输等权大盘；周度满仓换手成本吃掉大半利润。
- **"双周·概率加权·行业≤3" 胜出**：累计 35.1%、年化 13.0%、**夏普 0.84**、**最大回撤 -14.7%**，优于等权基准（夏普 0.57 / 回撤 -21.7%）。
- 破除误区：**周一开盘入场优于收盘入场**（收盘追高，一周内均值回归被套）。
- 该口径已固化到线上推荐接口的 `strategy` 提示（见 `stock_ranking_service.STRATEGY_HINT`）。

关键参数：`--stocks`、`--top-n`、`--start`/`--end`、`--rebal`/持有周数、`--cost-bps`、`--keep-rank`、`--probw-k`、`--keep-st`。

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
