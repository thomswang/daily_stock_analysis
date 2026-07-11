# OHLCV 数据回填 — 日常操作

**主表**: `stock_daily_ohlcv`（qfq 前复权，百度 + westock 双源）
**训练消费**: 暂未接入（`TRAIN_BAR_SOURCE` 无 `ohlcv` 选项）

---

## 日常命令（你只需要这个）

```bash
python backfill.py baidu --all --no-full --end 2026-07-07 \
  --progress data/baidu_progress_tail.json --retry 3 --sleep 1.5 --ktype 1 \
  --browser
```

- `--no-full`：只拉最近约 2000 行（老票 ≈2018 起，新股 = 上市日起），不拉深历史
- `--sleep 1.5`：限流，防止百度 403 风控
- `--browser`：用 Playwright 拉本地 Chrome 签 token + 以浏览器通道发请求，防拦截
- 周末也能跑，覆盖已有数据不影响正确性（upsert 幂等）
- 支持断点续传，中断后重跑同一命令即可
- **重跑自动跳过已到 `--end` 的票**：某票 `last` 已等于 `--end`（且本地已有数据）时，
  重复跑不会再发百度请求（旧版会对 000001 这类老票每轮空拉一次、白费流量+限流）。
  只有 `last < --end`（有增量缺口）的票才会被拉取。要补 2018 年前的深历史，
  须显式改用全量模式或 `--force`（尾窗口 `--no-full` 物理上取不到更早数据）。

---

## 看进度

```bash
python backfill.py baidu --progress-status --progress data/baidu_progress_tail.json
```

输出：`done / skipped / empty / failed` 各自数量

---

## 中断后续传 & 重试失败项

```bash
# 重跑同一命令 = 跳过 done + empty，只处理未完成 + failed
python backfill.py baidu --all --no-full --end 2026-07-07 \
  --progress data/baidu_progress_tail.json --retry 3 --sleep 1.5 --ktype 1 \
  --browser --retry-failed
```

- `--retry-failed`：只拉 `failed` + `unknown`，跳过所有已完成/空项
- 百度 403 熔断（连续 3 只 403 自动中止）后，等几十分钟再跑这条

---

## 单只试跑

```bash
python backfill.py baidu --symbols "600519" --mode range \
  --start 2026-06-01 --end 2026-07-07 \
  --progress data/baidu_test.json --retry 3 --sleep 1.5 --ktype 1
```

> ⚠️ `--symbols` 带前导零的代码必须加引号，否则 shell 会剥掉 `000001` → `1`

---

## westock 每日增量（可选）

百度段覆盖到 `--end` 之后，用 westock 每日追加最新数据（无 IP 限制）：

```bash
python backfill.py westock-ohlcv --all --mode incremental \
  --start 2010-01-01 --progress data/westock_ohlcv_progress.json --retry 2
```

---

## 进度台账状态说明

| 状态 | 含义 | `--retry-failed` 时是否重拉 |
|------|------|:---:|
| `done` | 已成功落库（`last` 已到 `--end` 者重跑直接跳过，不再请求百度） | 否 |
| `skipped` | 本地已覆盖，无需拉 | 否 |
| `empty` | 数据源无数据（未上市/退市） | 否 |
| `failed` | 拉取失败（网络/403） | 是 |
| `unknown` | 还没处理过 | 是 |
