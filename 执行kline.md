# K 线回填（近 12 年）

数据源：腾讯 fqkline API → 表 `stock_daily_kline`（前复权 OHLCV，HTTP 直连，800 条/次）

> 相关：统一训练主表 `stock_daily_ohlcv`（百度历史 + westock 每日增量）的操作见 [执行ohlcv.md](执行ohlcv.md)。
> `stock_daily_kline` 按约束**不可改动**，与本表并存、互不影响。

> 🔥 **`--symbols` 前导零代码必须加引号**：如 `--symbols "000001,001258"`。Git Bash / PowerShell 会把
> 未加引号的 `000001` 当数字剥掉前导零（`000001,001258` → `1,1258`），导致拉取失败或落到错误代码。
> `--all` / `--codes-file` 不受影响（代码来自 JSON 列表，不经 shell）。详见 [执行ohlcv.md](执行ohlcv.md)。

统一入口：`python backfill.py kline ...`

---

## 环境

每个终端先执行：

```bash
cd e:/analysis/daily_stock_analysis
export TRAIN_BAR_SOURCE=kline
```

> **数据源**：kline 回填使用 TencentFetcher（腾讯 fqkline HTTP API），不依赖 westock CLI / node。
> **无需 `--sleep`**：每只股票整段 1 次 HTTP 请求，默认 `--sleep 0`。
> **并发建议**：最多 3 个终端并行。TencentFetcher 是 HTTP 直连，并发抗性好，但 3 个已足够。

---

## 并行方案（推荐：3 个终端 × 2 年，共 6 段）

2015–2026 共 **12 年**，分 **6 段 × 2 年**，**开 3 个终端**，每个终端串行跑 2 段：

### 终端 1

```bash
cd e:/analysis/daily_stock_analysis

# 2015-2016
python backfill.py kline --all --mode range --start 2015-01-01 --end 2016-12-31 --progress data/kline_progress_2015_2016.json --retry 3 --adj qfq

# 2017-2018
python backfill.py kline --all --mode range --start 2017-01-01 --end 2018-12-31 --progress data/kline_progress_2017_2018.json --retry 3 --adj qfq
```

### 终端 2

```bash
cd e:/analysis/daily_stock_analysis

# 2019-2020
python backfill.py kline --all --mode range --start 2019-01-01 --end 2020-12-31 --progress data/kline_progress_2019_2020.json --retry 3 --adj qfq

# 2021-2022
python backfill.py kline --all --mode range --start 2021-01-01 --end 2022-12-31 --progress data/kline_progress_2021_2022.json --retry 3 --adj qfq
```

### 终端 3

```bash
cd e:/analysis/daily_stock_analysis

# 2023-2024
python backfill.py kline --all --mode range --start 2023-01-01 --end 2024-12-31 --progress data/kline_progress_2023_2024.json --retry 3 --adj qfq

# 2025-2026
python backfill.py kline --all --mode range --start 2025-01-01 --end 2026-07-03 --progress data/kline_progress_2025_2026.json --retry 3 --adj qfq
```

> 2026 的 `--end` 按实际最新交易日调整（如 `2026-07-04`）。

---

## 试跑（建议先跑）

```bash
cd e:/analysis/daily_stock_analysis
python backfill.py kline --symbols 600519 --mode range --start 2026-07-01 --end 2026-07-03 --progress data/kline_progress_test.json --retry 3 --adj qfq
```

---

## 常用辅助命令

查看进度（任选一个 progress 文件）：

```bash
python backfill.py kline --progress-status --progress data/kline_progress_2015_2016.json
```

中断后续传（与原命令完全相同，已完成股票自动跳过）：

```bash
# 重新粘贴对应终端的那条 backfill.py kline 命令即可
```

仅重试失败项（示例：2015-2016）：

```bash
python backfill.py kline --all --mode range --start 2015-01-01 --end 2016-12-31 --progress data/kline_progress_2015_2016.json --retry 3 --adj qfq --retry-failed
```

---

## 说明

- 共 **5207** 只 A 股；上市日晚于 `--start` 的会从上市日起拉。
- 数据源为腾讯 fqkline API（`web.ifzq.gtimg.cn/appstock/app/fqkline/get`），HTTP 直连，单次上限 800 条，2 年数据一次拉完。
- 台账：`data/kline_progress_*.json`，每完成一只写一次，可随时 Ctrl+C 中断。
- **同一 progress 文件只跑一个进程**；3 个终端对应不同 progress 文件。
- 多进程并行写同一 SQLite 时，偶发 `database is locked` 属正常；失败项用 `--retry-failed` 补跑即可。
- 训练默认读 kline：`export TRAIN_BAR_SOURCE=kline`（与 quote 表分离）。
- **`turnover_rate`**：fqkline 不含换手率，kline 表该字段为 NULL。换手率由 quote 逐日回填获取（`stock_daily_quote` 表），训练时 `build_features` 对缺失列填 0，详见 `docs/prediction-architecture.md`。
- **`amount`**：fqkline 同样不含成交额，kline 表该字段为 NULL。成交额在 quote 表中有。
