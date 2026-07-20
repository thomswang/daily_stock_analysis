# 模型训练与推荐系统 — 操作手册

> 本文档覆盖从「有新数据」到「看到推荐」的完整操作链路。
> 所有命令在 `e:/analysis/daily_stock_analysis` 目录下执行。

---

## 整体流程概览

```
① 更新数据  →  ② 训练模型  →  ③ 生成强弱榜  →  ④ 启动 Web UI
   (可选)        (必须)          (必须)           (按需)
```

| 步骤 | 命令 | 耗时 | 频率 |
|------|------|------|------|
| ① 更新日线数据 | `python backfill.py baidu --all --no-full --end <今天> --browser` | 30-60 分钟 | 每周/每月一次 |
| ① 更新指数数据 | `python backfill.py baidu --symbols "000300" --no-full --end <今天> --browser` | 1-2 分钟 | 同上 |
| ② 训练模型 | `python train_model.py --all --no-refresh --name trend_xsec --lookback 3000` | 50-60 分钟 | 有新数据后 |
| ③ 生成强弱榜 | `python rank_snapshot.py` | 3-5 分钟 | 每次训练后 / 每日盘后 |
| ④ 启动 Web UI | `python main.py --webui` | 即时 | 随时 |

> 数据层已统一到 **`stock_daily_ohlcv`** 表：旧 `stock_daily` / `stock_daily_kline` 表及
> `backfill.py kline`、`backfill_index.py` 已下线。个股与指数（沪深300）均由
> `backfill.py baidu`（或 `westock-ohlcv` 增量）写入同一张 ohlcv 表。

---

## 常用命令速查（直接复制）

> 全部命令在 `e:/analysis/daily_stock_analysis` 目录下执行。
> **训练与快照均为纯本地、零网络**：数据来自 backfill 回填到本地的 `stock_daily_ohlcv` 表，
> 训练/打分不会发起任何网络请求（断网也能跑）。只有步骤①拉数据才需要联网。

```bash
# ① 拉取数据（联网，仅此步需要网络）
python backfill.py baidu --all --no-full --end <今天> --browser \
  --progress data/baidu_progress_tail.json --retry 3 --sleep 1.5 --ktype 1
python backfill.py baidu --symbols "000300" --no-full --end <今天> --browser \
  --progress data/baidu_index_tail.json --retry 3 --sleep 1.5 --ktype 1

# ② 训练模型（纯本地，不联网）—— 带 --no-refresh 显式声明离线
python train_model.py --all --no-refresh --name trend_xsec --lookback 3000

# ③ 生成强弱榜快照（纯本地，不联网）
python rank_snapshot.py                  # 用当前激活的模型
python rank_snapshot.py --name trend_xsec   # 指定模型名
python rank_snapshot.py --model-id 14    # 指定具体版本 id 打分

# ④ 启动 Web UI
python main.py --webui

# 模型管理
python train_model.py --list             # 列出所有版本
python train_model.py --activate 14      # 回滚到指定 id
```

---

## 步骤 ①：更新数据（有新数据时执行）

### 1A. 更新个股日线（stock_daily_ohlcv 表）

这是训练数据的唯一来源，存的是前复权（qfq）OHLCV，单表自带换手率/成交额。

```bash
# 尾窗口增量（推荐日常用）：仅拉最近约 2000 行，覆盖增量绰绰有余
python backfill.py baidu --all --no-full --end 2026-07-11 \
  --progress data/baidu_progress_tail.json --retry 3 --sleep 1.5 --ktype 1 --browser

# westock 每日增量续写（百度段之后追加最新，无 IP 限制）
python backfill.py westock-ohlcv --all --mode incremental \
  --start 2010-01-01 --progress data/westock_ohlcv_progress.json --retry 2

# 试跑：只拉前 50 只验证流程
python backfill.py baidu --all --no-full --end 2026-07-11 --limit 50 --browser
```

**参数说明：**

| 参数 | 说明 |
|------|------|
| `--all` | 全市场 A 股（读 stocks.index.json） |
| `--no-full` | 尾窗口：仅拉最近约 2000 行（老票≈2018 起，新股=上市日起） |
| `--end` | 结束日（写当天即可，upsert 幂等、重复跑安全） |
| `--browser` | 用本机 Chrome 签 token 抓取，规避百度 403 风控 |
| `--sleep 1.5` | 限流秒数（防百度 403） |
| `--limit N` | 只处理前 N 只（试跑用） |
| `--retry 3` | 单只失败重试次数 |

> 详见 `执行ohlcv.md`。所有个股日线统一落 `stock_daily_ohlcv`（旧 `backfill.py kline` 已下线）。

**断点续传 & 重试失败项（关键）**：重跑同一命令 = 自动跳过已完成项，只处理未完成 + 失败项；百度 403 熔断（连续 3 只 403 会中止）后，等几十分钟再跑即可。

```bash
# 重跑 = 跳过 done+empty，只处理未完成 + failed
python backfill.py baidu --all --no-full --end <今天> --browser \
  --progress data/baidu_progress_tail.json --retry 3 --sleep 1.5 --ktype 1 --retry-failed

# 看进度台账：done / skipped / empty / failed / unknown 各自数量
python backfill.py baidu --progress-status --progress data/baidu_progress_tail.json
```

**进度台账状态**：`done`=已落库（重跑跳过）｜`skipped`=本地已覆盖｜`empty`=无数据（不重拉）｜`failed`/`unknown`=`--retry-failed` 时重拉。

**校验落库结果**（拉完确认数据进表）：
```bash
python -c "import sqlite3; c=sqlite3.connect('data/stock_analysis.db'); r=c.execute(\"select count(*),min(date),max(date) from stock_daily_ohlcv where code='000001'\").fetchone(); print('rows',r[0],'|',r[1],'~',r[2])"
```

### 1B. 更新大盘指数（沪深300，也落 stock_daily_ohlcv）

指数数据用于训练时的「大盘环境特征」。沪深300 与个股同表（落库码恒为裸码 `000300`）。

```bash
# 日常拉取沪深300 最新（尾窗口，覆盖增量绰绰有余）
python backfill.py baidu --symbols "000300" --no-full --end 2026-07-11 \
  --progress data/baidu_index_tail.json --retry 3 --sleep 1.5 --ktype 1 --browser
```

> ⚠️ 必须加引号（前导零）；始终只传 `000300`，脚本内部会自动映射到百度指数请求码。
> 旧 `backfill_index.py`（写旧 stock_daily 表）已下线，指数统一由 baidu 拉到 ohlcv。

---

## 步骤 ②：训练模型

> **零网络保证**：`train_model.py` 的训练取数统一走本地 `stock_daily_ohlcv` 表
> （`preload_training_cache`，纯本地、绝不联网），本地无数据的票直接跳过，不会回退联网。
> 因此**生成模型一定不联网**——即使断网，加不加 `--no-refresh` 都不影响离线训练。
> `--no-refresh` 现仅为显式声明/历史兼容，建议始终带上以明确意图。

### 标准训练命令（推荐）

```bash
python train_model.py --all --no-refresh --name trend_xsec --lookback 3000
```

**各参数含义：**

| 参数 | 值 | 说明 |
|------|-----|------|
| `--all` | — | 全市场 A 股（约 5200 只） |
| `--no-refresh` | — | 不联网，纯用本地缓存（步骤①已更新数据后用这个） |
| `--name` | `trend_xsec` | 模型名，同名下按版本管理 |
| `--lookback` | `3000` | 每只票回溯 3000 天（约 12 年，覆盖 2015~2026） |

### 完整参数速查

```bash
python train_model.py \
  --all \                              # 全市场
  --no-refresh \                       # 不联网，用本地缓存
  --name trend_xsec \                  # 模型名
  --lookback 3000 \                    # 回溯天数
  --label-mode cross_section \         # 标签口径（默认横截面排名）
  --algorithm lightgbm \              # 算法（默认 LightGBM）
  --top-pct 0.5 \                      # 横截面正样本阈值（前50%）
  --horizon 5 \                        # 前瞻天数（默认5=一周）
  --epochs 400 \                       # 训练轮数
  --lr 0.3 \                           # 学习率
  --notes "10年数据全量训练"            # 备注
```

### 什么时候用 `--no-refresh`，什么时候不用？

| 场景 | 用 `--no-refresh`？ | 原因 |
|------|---------------------|------|
| 步骤①已执行 backfill 更新数据 | ✅ 用 | 数据已在库里，无需重复联网 |
| 没跑 backfill，想顺便刷新数据 | ❌ 不用 | 训练时会联网拉最新数据（慢很多） |
| 离线环境 / 网络不好 | ✅ 用 | 纯本地 |

### 训练过程日志解读

```
17:17  开始训练：5207 只股票                          ← 正常
17:17  剔除 ST 股 251 只，剩余 4956 只                 ← 正常
17:18~18:04  逐票读取日线 + 构造特征                    ← 最耗时的阶段（~47分钟）
18:04  部分新股"有效样本过少，跳过"                     ← 正常，新股数据太少
18:05  样本汇聚完成：977 万条                          ← 数据准备完毕
18:05~18:15  LightGBM 训练中                           ← 等待
18:15  ===== 训练完成 =====                            ← 看到这个才算结束
```

### 训练结果解读

```
===== 训练完成 =====
模型:     trend_xsec @ 20260706_171722  (id=15)     ← 模型名+版本号+数据库ID
激活:     是                                         ← 自动设为生产模型
标签口径: 周度交易收益横截面强势前50%                   ← 模型学的是"下周谁更强"
算法:     LightGBM(梯度提升树)                        ← 算法类型
股票数:   4940                                       ← 实际参与训练的票数
总样本:   9778998  (训练 7104006 / 验证 2651345)      ← 样本量
训练准确率: 53.22%                                   ← 训练集表现
验证准确率: 52.57%                                   ← 验证集表现（核心指标）
基线(猜多数): 50.37%                                 ← 瞎猜的准确率
样本日期:  2015-02-26 ~ 2026-06-26                   ← 数据覆盖范围
耗时:     3341.92s                                   ← 总耗时
====================
```

**重点看：**
- `验证准确率` > `基线` → 模型有效
- `训练准确率` 和 `验证准确率` 差距小 → 没有过拟合
- `样本日期` 起始 → 确认数据读全了

### 模型管理

```bash
# 查看所有模型版本
python train_model.py --list

# 回滚到旧版本
python train_model.py --activate 14

# 训练但不自动激活（保留当前生产模型不变）
python train_model.py --all --no-refresh --name trend_xsec --lookback 3000 --no-active
```

### 试跑 / 测试命令（不污染生产模型）

> 以下命令都带 `--no-active`，训练完**不会覆盖**线上激活模型，只用来验证流程/效果。
> 试跑建议用独立的 `--name`（如 `trend_xsec_smoke` / `trend_xsec_oos`），便于事后 `--list` 区分、清理。

```bash
# ① 极速冒烟测试：全市场取前 60 只、回溯 400 天、绝对涨跌标签，验证流程能跑通
python train_model.py --all --limit 60 --no-active --lookback 400 \
  --label-mode absolute --notes "A股本地验证-勿激活"

# ② 稍大一点的试跑：前 100 只、回溯 800 天、横截面标签（与生产口径一致）
python train_model.py --all --limit 100 --no-active --lookback 800 \
  --name trend_xsec_smoke --notes "试跑-勿激活"

# ③ 样本外验证（walk-forward）：训练截止上周五，把"这周"留作考卷
#    train-end 之后模型一律没看过，本周结束即可对照预测与实际横截面排名
python train_model.py --all --no-refresh --name trend_xsec_oos --lookback 3000 \
  --train-end 2026-07-03 --no-active --notes "样本外验证-截止上周五"
```

**常用测试命令对照：**

| 命令 | 用途 | 关键参数 |
|------|------|---------|
| ① | 快速验证流程通不通 | `--limit 60` 只取前 60 只；`--label-mode absolute` 绝对涨跌标签；`--lookback 400`≈1.6 年 |
| ② | 接近生产口径的小样本试训 | `--limit 100` + 横截面标签（与 `trend_xsec` 同口径） |
| ③ | 留出最近一周做"考题" | `--train-end <上周五>` 截止日；`--no-active` 不覆盖生产模型 |

> ⚠️ **`--limit` 必须配合 `--all` 才有意义**：它只是"在已载入的全市场名单上剪一刀"（取前 N 只），
> 单独写 `--limit` 会报错退出。配 `--from-watchlist` / `--symbols` 时 `--limit` 会被忽略（仅 `--all` 生效）。
> 另外即使是 60/100 只试跑，里面没有本地数据或 ST 的票会被自动跳过，实际参与训练的会少于设定值。
> 样本外验证（③）判对错要看「本周横截面排名」而非「周五收盘是否高于周一开盘」，详见文档顶部标签口径说明。

---

## 步骤 ③：生成强弱榜快照

### 标准命令

```bash
python rank_snapshot.py
```

**做什么：** 用当前激活的模型，给全市场每只票打一个"下周大概率走强"的分数，存入 `stock_rank_snapshot` 表。

**输出示例：**
```
===== 强弱榜预计算完成 =====
打分日:   2026-07-06
模型:     trend_xsec @ 20260706_171722
打分股票: 4940 只
落库记录: 4940 条
行业覆盖: 28 个
============================
```

**参数：**

| 参数 | 默认 | 说明 |
|------|------|------|
| `--name` | `trend_xsec` | 使用的模型名 |
| `--lookback` | `250` | 特征回溯天数（需要足够算指标） |
| `--limit N` | — | 只打分前 N 只（试跑用） |

### 指定模型 / 版本打分

快照默认用「当前激活」模型。可按模型名或具体版本 id 打到指定模型：

```bash
python rank_snapshot.py --name trend_xsec     # 按模型名（与训练 --name 对应）
python rank_snapshot.py --model-id 14         # 按具体版本 id（精确到某一版）
python rank_snapshot.py --limit 50            # 试跑：只打分前 50 只
```

> `--model-id` 优先于 `--name`：指定 id 后会精确使用该版本，适合回滚后用旧模型重算快照。

### 什么时候需要重新跑？

- **每次训练新模型后** → 必须重跑，让快照用上新模型
- **每日盘后** → 建议重跑，让特征反映最新行情
- **不需要每次开 Web UI 都跑** → 快照存在数据库里，Web UI 直接读

---

## 步骤 ④：启动 Web UI

```bash
python main.py --webui
```

打开浏览器访问 `http://127.0.0.1:8000`。

> Web UI 是轻量查询，直接读数据库里的快照表，不计算。随时启动/关闭都行。

---

## 常见场景操作指南

### 场景 A：日常盘后更新推荐

数据已经在 backfill 阶段更新过了，只需要重算快照：

```bash
# 1. 生成新的强弱榜（用已有模型 + 最新行情特征）
python rank_snapshot.py

# 2. 启动 Web UI 查看
python main.py --webui
```

### 场景 B：有新数据，重新训练模型

```bash
# 1. 更新个股日线（尾窗口增量）
python backfill.py baidu --all --no-full --end 2026-07-11 --browser \
  --progress data/baidu_progress_tail.json --retry 3 --sleep 1.5 --ktype 1

# 2. 更新大盘指数（沪深300）
python backfill.py baidu --symbols "000300" --no-full --end 2026-07-11 --browser \
  --progress data/baidu_index_tail.json --retry 3 --sleep 1.5 --ktype 1

# 3. 训练新模型（用本地缓存，不联网）
python train_model.py --all --no-refresh --name trend_xsec --lookback 3000

# 4. 生成强弱榜（用新模型打分）
python rank_snapshot.py

# 5. 启动 Web UI
python main.py --webui
```

### 场景 C：首次部署 / 数据全量重建

```bash
# 1. 全量拉取个股日线（从 2015 年起，耗时较长）
python backfill.py baidu --all --mode range --start 2015-01-01 --end 2026-07-11 --browser \
  --progress data/baidu_progress.json --retry 3 --sleep 1.5 --ktype 1

# 2. 全量拉取大盘指数（沪深300）
python backfill.py baidu --symbols "000300" --mode full --start 2010-01-01 --end 2026-07-11 --browser \
  --progress data/baidu_index_full.json --retry 3 --sleep 1.5 --ktype 1

# 3. 训练模型
python train_model.py --all --no-refresh --name trend_xsec --lookback 3000

# 4. 生成强弱榜
python rank_snapshot.py

# 5. 启动 Web UI
python main.py --webui
```

### 场景 D：定时自动化

```bash
# 每日 17:30 自动生成强弱榜（后台常驻，Ctrl+C 退出）
python rank_snapshot.py --schedule 17:30

# 每日 18:30 自动训练（后台常驻）
python train_model.py --all --no-refresh --name trend_xsec --lookback 3000 --schedule 18:30

# 个股日线回填（baidu/westock-ohlcv）建议用系统计划任务/cron 每日触发，例如：
#   python backfill.py baidu --all --no-full --end <今天> --browser ...
```

### 场景 E：模型效果不好，想回滚

```bash
# 查看历史版本
python train_model.py --list

# 回滚到 id=14
python train_model.py --activate 14

# 用回滚后的模型重新生成快照
python rank_snapshot.py
```

---

## 注意事项

1. **训练时不要中断** — 训练耗时约 50-60 分钟，中途 Ctrl+C 不会保存任何结果
2. **`--no-refresh` 是关键** — backfill 已更新数据后，训练加 `--no-refresh` 避免重复联网
3. **训练后必须跑 `rank_snapshot.py`** — 否则 Web UI 推荐页显示旧数据
4. **模型自动激活** — 训练完成后新模型自动设为生产模型，旧模型保留可回滚
5. **ST 股自动剔除** — 训练时自动排除 ST/退市风险股，无需手动处理
6. **数据表已统一** — 全链路（回填/训练/预测/回测/快照）只用 `stock_daily_ohlcv`；旧 `stock_daily`、`stock_daily_kline` 表及 `backfill.py kline`、`backfill_index.py` 已下线


# 更新日志
python backfill.py baidu --all --no-full --end 2026-07-17 \
  --progress data/baidu_progress_tail.json_2026-07-17.json --retry 3 --sleep 1.5 --ktype 1 --browser 

python backfill.py baidu --all --no-full --end 2026-07-17 \
  --progress data/baidu_progress_tail.json_2026-07-17.json --retry 3 --sleep 1.5 --ktype 1 --browser --retry-failed

python backfill.py baidu --symbols "000300" --no-full --end 2026-07-17 \
  --progress data/baidu_index_tail.json --retry 3 --sleep 1.5 --ktype 1 --browser