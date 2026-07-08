# OHLCV 训练主表回填（stock_daily_ohlcv）

统一训练用日线表：**`stock_daily_ohlcv`**（前复权 qfq，源无关）。

- **百度历史段**（`data_source='BaiduFetcher'`）：历史全量底表，一次性拉取。
- **westock 增量段**（`data_source='Westock'`）：每个交易日追加，与百度段在 `(code, date)` 上同口径（均为 qfq）无缝拼接。

两张源写入同一张表、按 `data_source` 区分覆盖度；`stock_daily_kline`（腾讯表）**按既定约束不可改动**，与此表并存、互不影响。

> ⚠️ **当前未接入训练**：特征加载层 `TRAIN_BAR_SOURCE` 目前仅支持 `kline/quote/auto/merged`，
> 还没有 `ohlcv`。本表是规范化的 qfq OHLCV 数据仓库，训练切换为读本表需另行接线（见文末「待办」）。

> 🔥 **必读：`--symbols` 的前导零代码必须加引号！** Git Bash / PowerShell 会把未加引号的
> `000001`、`001258` 当成数字把前导零剥掉（`000001,001258` → `1,1258`），导致拉取失败或落到错误代码。
> 正确写法：`--symbols "000001,001258"`（双引号包住整个逗号列表）。`--all` 与 `--codes-file` 不受影响
> （代码来自 JSON 列表，不经 shell）。

---

## 0. 进度台账（progress JSON）怎么看

所有回填都靠一个 **进度台账 JSON** 驱动续传（百度用 `data/baidu_progress_tail.json`，westock 用各自的文件）。
它的作用：**实时记录每只股票的回填状态，任意中断后重跑即从断点继续**。

### 结构

```jsonc
{
  "meta": {
    "start_date": "2010-01-01",
    "end_date": "2026-07-07",
    "mode": "full",          // --mode 的分段策略（full/incremental/smart/range）
    "dataset": "baidu",
    "ktype": "1",
    "total": 5207,           // 本次计划处理的总数
    "updated_at": "2026-07-08T16:45:58"
  },
  "codes": {
    "000001": { "status": "done", "error": null, "first": "2018-04-10",
                "last": "2026-07-08", "rows": 2001, "source": "BaiduFetcher",
                "updated_at": "2026-07-08T16:46:09" },
    "600519": { "status": "failed", "error": "403 ...", ... }
  }
}
```

### 状态枚举（status）

| 状态 | 含义 | 续传时默认是否再处理 |
|------|------|----------------------|
| `done` | 成功拉取并落库（或已被完整覆盖） | 否（幂等，可重跑覆盖） |
| `skipped` | 本地已完整覆盖，无需补段 | 否 |
| `empty` | 数据源确实无数据（未上市/已退市） | **否（永久跳过，除非 `--force`）** |
| `failed` | 拉取失败（网络/403/超时等） | 是（用 `--retry-failed`） |
| `unknown` | 从未出现在台账里（还没处理过） | 是 |

### 续传判定规则（代码实证）

主循环 `run_backfill_job` 在每轮开始按以下顺序过滤 `codes`：

1. 不加 `--force`：**剔除 `empty`**（数据源确认无数据，不再浪费请求）。
2. 加 `--retry-failed`：**只保留 `failed` + `unknown`**（`done/skipped/empty` 一律跳过）。
3. 不加 `--retry-failed`：**全部进入处理**；其中已 `done` 的票在尾窗口模式下会重拉最近约 2000 行做幂等覆盖（已存在则新增 0，无害但耗流量）。

> 关键词：台账是「断点续传」的唯一依据。**重跑同一条命令 = 从断点继续**，不需要手动挑代码。

---

## 1. 三种节奏一览

| 节奏 | 命令 | 说明 |
|------|------|------|
| **拉旧数据（一次性 / 尾窗口）** | `backfill.py baidu` | 百度写入本表（默认起点 `2010-01-01`；`--no-full` 走尾窗口 ≈2018 起，见 §3） |
| **每日更新** | `backfill.py westock-ohlcv --mode incremental` | 每个交易日追加最新 qfq K 线 |
| **每周维护** | `backfill.py westock-ohlcv --mode range`（补漏）+ `backfill.py quote`（估值） | 补 daily 漏跑的交易日；估值/基本面较慢，按周拉即可 |

---

## 2. 如何继续执行未完成的（续传）

> 适用：任何一次 `backfill.py baidu / westock-ohlcv / quote` 中途被 Ctrl+C、崩溃、或命中百度 403 熔断。

### 先看账台进度

```bash
python backfill.py baidu --progress-status --progress data/baidu_progress_tail.json
# 输出：计划总数 / done / skipped / empty / failed 各自数量
```

### 场景 A：进程被打断，账台里大部分 `done`，还有一批 `unknown`（从未处理）

**最省流量的做法 —— 用 `--retry-failed`**（跳过所有已完成/空，只拉 `unknown` + `failed`）：

```bash
# 百度（保持与首跑一致的 --no-full 口径；--sleep 1.5 限流，避免 IP 风控）
python backfill.py baidu --all --no-full --end 2026-07-07 \
  --progress data/baidu_progress_tail.json --retry 3 --sleep 1.5 --ktype 1 --retry-failed

# westock-ohlcv
python backfill.py westock-ohlcv --all --mode incremental \
  --progress data/westock_ohlcv_progress.json --retry 2 --retry-failed
```

> 例：你的 `baidu_progress_tail.json` 里 `total=5207` 但只到 `000070`（约 70 只 `done`），
> 上面这条命令会自动跳过这 70 只，只拉剩余约 5000+ 只未处理的。

**备选 —— 原命令原样重跑**（不加 `--retry-failed`）：会重新处理全部，已 `done` 的票做幂等尾窗口覆盖。
若想重跑原命令但**不重复下载已完成的票**，把 `--mode` 改成 `incremental` 即可（已覆盖的票直接 `skipped`，未覆盖的票仍按尾窗口补）。

### 场景 B：命中百度 403 熔断（连续 3 只 403 自动中止，账台留一批 `failed`）

百度对 `403` 是 **IP 风控**（`fail_fast_on_error_substr="403"`、`consecutive=3`：连续 3 只命中即提前退出，
避免继续轰炸被拉黑）。账台里留下 `failed` 状态的票。

```bash
# 稍等几十分钟（让风控窗口过去）再只重跑 failed：
python backfill.py baidu --all --no-full --end 2026-07-07 \
  --progress data/baidu_progress_tail.json --retry 3 --sleep 1.5 --ktype 1 --retry-failed
```

> 注意：刚解封就立刻重跑很可能再次 403。建议间隔一段时间，或换网络/代理后再 `--retry-failed`。

---

## 3. 拉旧数据（百度历史全量 / 尾窗口）

按年份分片串行/少量并行。end 建议填**「昨日」**，这样首日 westock 增量能借库内 qfq 收盘价正确推导（见「注意事项」）。

```bash
# 终端 1：2010-2017
python backfill.py baidu --all --mode range --start 2010-01-01 --end 2017-12-31 \
  --progress data/baidu_progress_2010_2017.json --retry 3 --ktype 1

# 终端 2：2018-昨日
python backfill.py baidu --all --mode range --start 2018-01-01 --end 2026-07-07 \
  --progress data/baidu_progress_2018_2026.json --retry 3 --ktype 1
```

### 全量 vs 尾窗口（两种「少拉」方式）

两个**互相独立**的开关，可任意组合：

- **`--mode`**（full / incremental / smart / range）= **分段策略**：控制把请求切成哪几段、按本地覆盖补哪段。
- **`--no-full`**（有 / 无）= **全量策略（full_mode）**：控制百度单次请求**是否带 `all=1`**（即是否回传深历史）。

`backfill.py baidu` 的 `--start` **默认就是 `2010-01-01`**（写死在 `baidu_service.py` 的 `DEFAULT_START_DATE`）。
百度请求是否带全量由 **full_mode** 决定，三档：

| full_mode | 触发方式 | 百度返回 | 传输量 | 适用 |
|---|---|---|---|---|
| `auto`（默认，不加 `--no-full`） | 本地无数据 / 有效起点早于本地 first → 全量；否则尾窗口 | 自适应 | 自适应 | 日常 / 首次种子 |
| `tail`（加 `--no-full`） | 永远尾窗口 | 仅最近约 2000 行 | 最小（约半） | 只要近期数据 ★日常主用 |
| `full`（预留） | 强制全量（all=1） | 整段 | 最大 | 补齐/修复深历史 |

实际起点 = **max(`--start`, 该股票上市日)**（`resolve_effective_start`，上市日来自 `data/cache/cn_list_dates.json`）。

> 🔥 **`--no-full` 与 `--mode` 无关**：`--mode` 控制「分段策略」，`--no-full` 控制「单段是否带 all=1」。

#### 方式 A：仅限制落库窗口（首跑仍全量传输）

把 `--start` 改成更晚日期，首跑因本地无数据仍走全量（百度回传整段后再本地裁到窗口）：

```bash
python backfill.py baidu --all --mode range --start 2021-01-01 --end 2026-07-07 \
  --progress data/baidu_progress_2021_now.json --retry 3 --ktype 1
```

#### 方式 B：尾窗口模式 `--no-full`（首跑即最小传输）★推荐作为日常主用

首次即从百度只拉最近约 2000 行，不回传深历史：

```bash
python backfill.py baidu --all --no-full --end 2026-07-07 \
  --progress data/baidu_progress_tail.json --retry 3 --sleep 1.5 --ktype 1
```

> ⚠️ 尾窗口是**固定约 2000 行**：老票对应 **2018-04-10** 起、新股对应**上市日**起（其整段不足 2000 行，全量=尾窗口）。
> 若 `--start` 早于尾窗口起点（如老票用 `--start 2010`），会**静默只落 2018+**，并打出警告：
> `XXX 尾窗口仅覆盖到 2018-04-10（早于该日的请求起点 ... 百度不返回，需 full 模式才能补齐）`。

**默认起点到底从哪天？**（实测 000001/600519/001258/600036 多票确认）
- `auto` 下：`--start` 不传即 `2010-01-01`；上市早于 2010 的老票从 **2010-01-04** 拉起（约 4000 行），
  **不会**回上市日、**不会**从 2018。新股（如 001258 2022-07-27）从**上市日**拉。
- `tail` 下：老票从 **2018-04-10** 起（固定约 2000 行），新股从**上市日**起（整段不足 2000 行，全量=尾窗口）。

> 百度接口实测**忽略** start/end：能否「少传」取决于 `full_mode`（是否带 all=1）与本地是否已存深历史，
> 而非接口参数。重跑安全（upsert 覆盖，新增 0 属正常）。

---

## 4. 每日更新（westock kline qfq 增量）

每个交易日收盘后跑一次，`--mode incremental` 只补最近尾窗口（默认 `fresh_days=4`）：

```bash
python backfill.py westock-ohlcv --all --mode incremental \
  --start 2010-01-01 --progress data/westock_ohlcv_progress.json --retry 2
```

- `start` 对 incremental 模式无实际影响（由本地覆盖度决定补哪段），写默认即可。
- 用 Windows 任务计划程序 / cron 在交易日 16:00 后定时触发本命令即实现「每日更新」。
- westock kline 单次可拉整段，限额宽松，直接整段拉取 + upsert 覆盖，简单可靠。

---

## 5. 每周维护

### 5.1 补漏本表（daily 漏跑时）

若某天 daily 任务失败/未跑，周末用 `range` 补回这一周：

```bash
python backfill.py westock-ohlcv --all --mode range \
  --start 2026-07-01 --end 2026-07-07 \
  --progress data/westock_ohlcv_progress.json --retry 2
```

### 5.2 拉估值/基本面（quote，较慢，按周）

`stock_daily_quote`（不复权截面，PE/PB/市值/换手等）较慢，按周拉最近一周即可；它**不写**本时间序列表：

```bash
python backfill.py quote --all --mode range \
  --start 2026-06-29 --end 2026-07-05 \
  --progress data/progress_weekly.json --sleep 0.1 --retry 2
```

---

## 6. 常用辅助命令

查看进度（任选一个 progress 文件）：

```bash
python backfill.py westock-ohlcv --progress-status --progress data/westock_ohlcv_progress.json
python backfill.py baidu --progress-status --progress data/baidu_progress_2018_2026.json
```

中断后续传（与原命令完全相同，已完成股票自动跳过，详见 §2）：

```bash
# 重新粘贴对应那条 backfill.py baidu / westock-ohlcv 命令即可
# 想只补失败项：末尾加 --retry-failed
```

仅重试失败项：

```bash
python backfill.py baidu --all --mode range --start 2018-01-01 --end 2026-07-07 \
  --progress data/baidu_progress_2018_2026.json --retry 3 --sleep 1.5 --ktype 1 --retry-failed

python backfill.py westock-ohlcv --all --mode incremental \
  --progress data/westock_ohlcv_progress.json --retry 2 --retry-failed
```

试跑（单只验证）：

```bash
python backfill.py baidu --symbols "600519" --mode range --start 2026-06-01 --end 2026-07-07 \
  --progress data/baidu_test.json --retry 3 --ktype 1

python backfill.py westock-ohlcv --symbols "600519" --mode incremental \
  --progress data/westock_ohlcv_test.json --retry 2
```

---

## 7. 注意事项

- **seed 依赖**：westock-ohlcv 首行涨跌幅/昨收由「库内该 code 在 qfq 下早于本批的最近收盘价」推导。
  故百度历史段须先拉到「昨日」，首日 westock 增量才能正确对齐，避免出现 7~8% 断崖。
- **westock quote --date 不写本表**：其价格是不复权口径，直接写入会与百度 qfq 出现断崖；估值类走 `stock_daily_quote`。
- **volume 单位**：westock kline 单位为「手」，落库统一转股（`×100`），与百度/腾讯口径一致，join 无需换算。
- **preClose 不落库**：`preClose = 前一日 close`，可由 `close` 偏移在特征工程阶段推导，本表不冗余存储。
- **并发**：baidu 需浏览器 token，建议 ≤2 终端并行；westock-ohlcv 限额宽松可多终端。
  并行写同一 SQLite 偶发 `database is locked` 属正常，用 `--retry-failed` 补跑即可。
- **百度 403 熔断**：连续 3 只命中 `403`（疑似 IP 风控）会自动中止整轮，账台留 `failed`。
  不要立即重跑，间隔一段时间或换网络后再用 `--retry-failed` 续传。
- **stock_daily_kline（腾讯表）不可改动**：本表与之并存，互不干扰。

---

## 8. 待办（架构）

- 特征加载层 `src/repositories/training_bars.py` 的 `TRAIN_BAR_SOURCE` 目前无 `ohlcv` 选项，
  训练尚未消费 `stock_daily_ohlcv`。若要训练改用本表（推荐，因其 qfq 口径统一且含换手率），
  需新增 `ohlcv` 分支并对接 `get_ohlcv_kline`。
