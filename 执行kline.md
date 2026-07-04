# K 线回填（近 12 年）

数据源：`westock kline --fq qfq` → 表 `stock_daily_kline`（前复权 OHLCV，整段拉取，速度快）

统一入口：`python backfill.py kline ...`

---

## 环境

每个终端先执行：

```bash
cd e:/analysis/daily_stock_analysis
export TRAIN_BAR_SOURCE=kline
```

westock CLI 已内置在 `.claude/skills/westock-data/`，**一般不必**再设 `WESTOCK_DATA_DIR`。
若要用仓库外另一份 westock-data，再 `export WESTOCK_DATA_DIR=...` 覆盖。

> **无需 `--sleep`**：kline 每只股票整段只调 1 次 westock（不像 quote 逐日循环）。默认 `--sleep 0`。
> **6 进程并行**写同一 SQLite 时，若出现 `database is locked`，可加 `--sleep 0.2`，或稍后 `--retry-failed`。

---

## 并行方案（推荐：6 个终端 × 2 年）

2015–2026 共 **12 年**，**开 6 个终端**，每个终端粘贴一条命令（不要重复跑同一 `progress` 文件）：

### 终端 1：2015–2016

```bash
cd e:/analysis/daily_stock_analysis
python backfill.py kline --all --mode range --start 2015-01-01 --end 2016-12-31 --progress data/kline_progress_2015_2016.json --retry 2 --adj qfq
```

### 终端 2：2017–2018

```bash
cd e:/analysis/daily_stock_analysis
python backfill.py kline --all --mode range --start 2017-01-01 --end 2018-12-31 --progress data/kline_progress_2017_2018.json --retry 2 --adj qfq
```

### 终端 3：2019–2020

```bash
cd e:/analysis/daily_stock_analysis
python backfill.py kline --all --mode range --start 2019-01-01 --end 2020-12-31 --progress data/kline_progress_2019_2020.json --retry 2 --adj qfq
```

### 终端 4：2021–2022

```bash
cd e:/analysis/daily_stock_analysis
python backfill.py kline --all --mode range --start 2021-01-01 --end 2022-12-31 --progress data/kline_progress_2021_2022.json --retry 2 --adj qfq
```

### 终端 5：2023–2024

```bash
cd e:/analysis/daily_stock_analysis
python backfill.py kline --all --mode range --start 2023-01-01 --end 2024-12-31 --progress data/kline_progress_2023_2024.json --retry 2 --adj qfq
```

### 终端 6：2025–2026

```bash
cd e:/analysis/daily_stock_analysis
python backfill.py kline --all --mode range --start 2025-01-01 --end 2026-07-03 --progress data/kline_progress_2025_2026.json --retry 2 --adj qfq
```

> 2026 的 `--end` 按实际最新交易日调整（如 `2026-07-04`）。

---

## 试跑（建议先跑）

```bash
cd e:/analysis/daily_stock_analysis
python backfill.py kline --symbols 600519 --mode range --start 2026-07-01 --end 2026-07-03 --progress data/kline_progress_test.json --retry 2 --adj qfq
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

仅重试失败项（示例：终端 1）：

```bash
python backfill.py kline --all --mode range --start 2015-01-01 --end 2016-12-31 --progress data/kline_progress_2015_2016.json --retry 2 --adj qfq --retry-failed
```

---

## 说明

- 共 **5207** 只 A 股；上市日晚于 `--start` 的会从上市日起拉。
- 台账：`data/kline_progress_*.json`，每完成一只写一次，可随时 Ctrl+C 中断。
- **同一 progress 文件只跑一个进程**；6 个终端对应 6 个不同 progress 文件。
- 6 进程同时写 `stock_analysis.db`，偶发 `database is locked` 属正常；失败项用 `--retry-failed` 补跑即可。
- kline 与 quote 不同：**整段 1 次 node 请求/股/段**，默认不限流；quote 逐日才需要 `--sleep 0.1`。
- 训练默认读 kline：`export TRAIN_BAR_SOURCE=kline`（与 quote 表分离）。
- **`turnover_rate` 历史覆盖**：westock kline 的 `exchange`（换手%）在 **2016–2017 常为 0**，2018 起逐步有值，2019+ 较完整；落库如实存 0，不是回填 bug。长周期训练时换手因子在老区间等效缺失（`build_features` 填 0），详见 `docs/prediction-architecture.md`。
