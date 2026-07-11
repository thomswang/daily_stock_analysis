# OHLCV 数据回填 — 日常操作

**主表**: `stock_daily_ohlcv`（qfq 前复权，百度 + westock 双源）
**训练消费**: ✅ 已接入（`TRAIN_BAR_SOURCE=ohlcv` 默认即此表；沪深300 作为大盘 β 基准，供
`prediction_service.load_market_df` 派生 `mkt_*` / `rel_strength_*` 环境特征）

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

## 沪深300 指数（特殊码处理 ⚠️）

沪深300 在百度侧是**指数**而非个股，接口/查询码都不同：

| 码 | 用途 | 在哪用 |
|----|------|--------|
| `000300` | **落库码 / 本地 key / 训练读取码**（裸码） | 你传给 `--symbols`、落库 `stock_daily_ohlcv.code`、训练 `load_market_df("000300.SH")` 回退到的裸码 |
| `399300` | **仅对百度发出的请求码**（深证镜像） | `baidu_fetcher.INDEX_BAIDU_MAP` 内部自动映射，**你永远不需要手动传** |

> 关键：**你始终只传 `000300`**。脚本会自动把抓取请求改成 `399300` + `group=quotation_index_kline`，
> 但落库和训练读取一律用裸码 `000300`。切勿把 `--symbols` 写成 `399300`，否则落库码错位、训练读不到。

### 日常拉取最新（推荐，每日/周末都跑）

> ⚠️ 必须加引号（前导零）；`--no-full` 取最近约 2000 行（≈8 年），覆盖增量绰绰有余；
> `--end` 写当天或"今天"即可，upsert 幂等、重复跑安全。

```bash
python backfill.py baidu --symbols "000300" --no-full --end 2026-07-11 \
  --progress data/baidu_index_tail.json --retry 3 --sleep 1.5 --ktype 1 \
  --browser
```

### 全量刷新（首次 / 发现历史缺口时）

从 2010 年起全量重拉（已有数据 upsert 覆盖，不影响正确性）：

```bash
python backfill.py baidu --symbols "000300" --mode full \
  --start 2010-01-01 --end 2026-07-11 \
  --progress data/baidu_index_full.json --retry 3 --sleep 1.5 --ktype 1 \
  --browser
```

### 校验落库结果

```bash
python -c "import sqlite3; c=sqlite3.connect('data/stock_analysis.db'); r=c.execute(\"select count(*),min(date),max(date),round(min(close),2),round(max(close),2) from stock_daily_ohlcv where code='000300'\").fetchone(); print('rows',r[0],'|',r[1],'~',r[2],'| close',r[3],'~',r[4])"
```

> 预期：rows≈4000+，close 落在沪深300 正常区间（约 2000~6000）；`turnoverratio` 为 `None` 属正常（指数无换手率）。

### 训练侧是否能命中

```bash
python -c "from src.services.prediction_service import load_market_df; df=load_market_df(); print('market rows', len(df), df[['date','close']].head(1).to_dict('records'), df[['date','close']].tail(1).to_dict('records'))"
```

> 返回非空 DataFrame 即表示大盘环境特征已生效（不再中性填 0）。

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
