# Quote 回填（近 6 年）

数据源：`westock quote --date` → 表 `stock_daily_quote`（不复权，40+ 字段，较慢，适合放采集机跑）

> 相关：统一训练主表 `stock_daily_ohlcv`（前复权 K 线，含换手率）的操作见 [执行ohlcv.md](执行ohlcv.md)。
> `stock_daily_quote` 是估值/基本面截面，**不写** ohlcv 时间序列表。

> 🔥 **`--symbols` 前导零代码必须加引号**：如 `--symbols "000001,001258"`。Git Bash / PowerShell 会把
> 未加引号的 `000001` 当数字剥掉前导零（`000001,001258` → `1,1258`），导致拉取失败或落到错误代码。
> `--all` / `--codes-file` 不受影响（代码来自 JSON 列表，不经 shell）。详见 [执行ohlcv.md](执行ohlcv.md)。

统一入口：`python backfill.py quote ...`

---

## 环境

每个终端先执行：

```bash
cd e:/analysis/daily_stock_analysis
```

westock CLI 已内置在 `.claude/skills/westock-data/`，**一般不必**再设 `WESTOCK_DATA_DIR`。
若要用仓库外另一份 westock-data，再 `export WESTOCK_DATA_DIR=...` 覆盖。

Quote 逐日拉取，默认需要限流（与 kline 不同）：

| 参数 | 默认 | 作用 |
|------|------|------|
| `--sleep 0.1` | CLI 默认 | 每只股票/每段处理完后的间隔 |
| `WESTOCK_QUOTE_SLEEP=0.1` | 内置默认 | 单股区间内，每批并发 quote 请求之间的间隔 |
| `WESTOCK_QUOTE_BATCH=3` | 内置默认 | 每批并发的交易日数 |

全量 6 终端并行时保持 `--sleep 0.1`；试跑单股可 `--sleep 0`。

---

## 并行方案（6 个终端 × 1 年）

2021–2026 各开一个终端，每个终端粘贴一条命令（**同一年份不要开两个进程**）：

### 终端 1：2021

```bash
cd e:/analysis/daily_stock_analysis
python backfill.py quote --all --mode range --start 2021-01-01 --end 2021-12-31 --progress data/progress_2021.json --sleep 0.1 --retry 2
```

### 终端 2：2022

```bash
cd e:/analysis/daily_stock_analysis
python backfill.py quote --all --mode range --start 2022-01-01 --end 2022-12-31 --progress data/progress_2022.json --sleep 0.1 --retry 2
```

### 终端 3：2023

```bash
cd e:/analysis/daily_stock_analysis
python backfill.py quote --all --mode range --start 2023-01-01 --end 2023-12-31 --progress data/progress_2023.json --sleep 0.1 --retry 2
```

### 终端 4：2024

```bash
cd e:/analysis/daily_stock_analysis
python backfill.py quote --all --mode range --start 2024-01-01 --end 2024-12-31 --progress data/progress_2024.json --sleep 0.1 --retry 2
```

### 终端 5：2025

```bash
cd e:/analysis/daily_stock_analysis
python backfill.py quote --all --mode range --start 2025-01-01 --end 2025-12-31 --progress data/progress_2025.json --sleep 0.1 --retry 2
```

### 终端 6：2026

```bash
cd e:/analysis/daily_stock_analysis
python backfill.py quote --all --mode range --start 2026-01-01 --end 2026-07-03 --progress data/progress_2026.json --sleep 0.1 --retry 2
```

> 2026 的 `--end` 按实际最新交易日调整（如 `2026-07-04`）。

---

## 试跑（建议先跑）

```bash
cd e:/analysis/daily_stock_analysis
python backfill.py quote --symbols 600519 --mode range --start 2026-07-01 --end 2026-07-03 --progress data/progress_test.json --sleep 0 --retry 2
```

---

## 常用辅助命令

查看进度：

```bash
python backfill.py quote --progress-status --progress data/progress_2021.json
```

中断后续传（与原命令完全相同，已完成股票自动跳过）：

```bash
# 重新粘贴对应终端的那条 backfill.py quote 命令即可
```

仅重试失败项：

```bash
python backfill.py quote --all --mode range --start 2021-01-01 --end 2021-12-31 --progress data/progress_2021.json --sleep 0.1 --retry 2 --retry-failed
```

---

## 说明

- 共 **5207** 只 A 股；上市日晚于 `--start` 的会自动从上市日起拉。
- 台账：`data/progress_YYYY.json`，每完成一只写一次，可随时 Ctrl+C 中断。
- 多进程并行写同一 SQLite 时，若出现 `database is locked`，可把 `--sleep` 调到 `0.4`，或稍后再 `--retry-failed`。
- **同一年份只跑一个进程**。
- 可在另一台机器用独立 `DATABASE_PATH` 跑完后再拷库合并；kline 与 quote 分表，互不影响。
- kline 与 quote 都拉齐后，训练可用：`export TRAIN_BAR_SOURCE=merged`（kline OHLCV + quote 估值列）。
