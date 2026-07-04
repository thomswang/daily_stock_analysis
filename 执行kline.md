# K 线回填（近 6 年）

数据源：`westock kline --fq qfq` → 表 `stock_daily_kline`（前复权 OHLCV，整段拉取，速度快）

脚本入口：`backfill_kline.py`

---

## 环境

每个终端先执行：

```bash
cd e:/analysis/daily_stock_analysis
export WESTOCK_DATA_DIR=e:/analysis/westock-data
export WESTOCK_KLINE_SLEEP=0.05
export TRAIN_BAR_SOURCE=kline
```

---

## 并行方案（推荐：3 个终端 × 2 年）

2021–2026 共 6 年，**开 3 个终端**，每个终端粘贴一条命令（不要重复跑同一 `progress` 文件）：

### 终端 1：2021–2022

```bash
cd e:/analysis/daily_stock_analysis
export WESTOCK_DATA_DIR=e:/analysis/westock-data
python backfill_kline.py --all --mode range --start 2021-01-01 --end 2022-12-31 --progress data/kline_progress_2021_2022.json --sleep 0.05 --retry 2 --adj qfq
```

### 终端 2：2023–2024

```bash
cd e:/analysis/daily_stock_analysis
export WESTOCK_DATA_DIR=e:/analysis/westock-data
python backfill_kline.py --all --mode range --start 2023-01-01 --end 2024-12-31 --progress data/kline_progress_2023_2024.json --sleep 0.05 --retry 2 --adj qfq
```

### 终端 3：2025–2026

```bash
cd e:/analysis/daily_stock_analysis
export WESTOCK_DATA_DIR=e:/analysis/westock-data
python backfill_kline.py --all --mode range --start 2025-01-01 --end 2026-07-03 --progress data/kline_progress_2025_2026.json --sleep 0.05 --retry 2 --adj qfq
```

> 2026 的 `--end` 按实际最新交易日调整（如 `2026-07-04`）。

---

## 试跑（建议先跑）

```bash
cd e:/analysis/daily_stock_analysis
export WESTOCK_DATA_DIR=e:/analysis/westock-data
python backfill_kline.py --all --limit 20 --start 2024-01-01 --end 2024-12-31 --progress data/kline_progress_test.json --sleep 0.05 --retry 2 --adj qfq
```

---

## 常用辅助命令

查看进度：

```bash
python backfill_kline.py --progress-status --progress data/kline_progress_2021_2022.json
```

中断后续传（与原命令完全相同，已完成股票自动跳过）：

```bash
# 重新粘贴对应终端的那条 backfill_kline.py 命令即可
```

仅重试失败项：

```bash
python backfill_kline.py --all --mode range --start 2021-01-01 --end 2022-12-31 --progress data/kline_progress_2021_2022.json --sleep 0.05 --retry 2 --adj qfq --retry-failed
```

---

## 说明

- 共 **5207** 只 A 股；上市日晚于 `--start` 的会从上市日起拉。
- 台账：`data/kline_progress_*.json`，每完成一只写一次，可随时 Ctrl+C 中断。
- **同一 progress 文件只跑一个进程**；3 个终端对应 3 个不同 progress 文件。
- 多进程写同一 SQLite 若出现 `database is locked`，可把 `--sleep` 调到 `0.2`，或稍后 `--retry-failed`。
- 训练默认读 kline：`export TRAIN_BAR_SOURCE=kline`（与 quote 表分离）。
