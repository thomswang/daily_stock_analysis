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
| ① 更新日线数据 | `python backfill.py kline --all --mode incremental` | 30-60 分钟 | 每周/每月一次 |
| ① 更新指数数据 | `python backfill_index.py` | 1-2 分钟 | 同上 |
| ② 训练模型 | `python train_model.py --all --no-refresh --name trend_xsec --lookback 3000` | 50-60 分钟 | 有新数据后 |
| ③ 生成强弱榜 | `python rank_snapshot.py` | 3-5 分钟 | 每次训练后 / 每日盘后 |
| ④ 启动 Web UI | `python main.py --webui` | 即时 | 随时 |

---

## 步骤 ①：更新数据（有新数据时执行）

### 1A. 更新个股日线（stock_daily_kline 表）

这是训练数据的主要来源，存的是前复权 K 线。

```bash
# 增量更新：只补每只票缺失的最新几天（推荐日常用）
python backfill.py kline --all --mode incremental

# 全量重拉：从 2010 年开始重新拉所有数据（数据损坏/首次部署时用）
python backfill.py kline --all --mode full

# 指定区间补数据
python backfill.py kline --all --mode range --start 2025-01-01 --end 2025-06-30

# 试跑：只拉前 50 只验证流程
python backfill.py kline --all --mode incremental --limit 50
```

**参数说明：**

| 参数 | 说明 |
|------|------|
| `--all` | 全市场 A 股（约 5200 只） |
| `--mode incremental` | 增量：只补每只票缺失的最近几天 |
| `--mode full` | 全量：从 2010 年重新拉 |
| `--mode range` | 精确区间：配合 `--start` / `--end` |
| `--mode smart` | 智能补缺口 |
| `--limit N` | 只处理前 N 只（试跑用） |
| `--retry 2` | 单只失败重试次数 |
| `--sleep 0.0` | 请求间隔秒数（kline 很快，默认 0） |
| `--adj qfq` | 复权类型（默认前复权，不要改） |

**输出示例：**
```
===== kline 回填完成 =====
计划总数: 5207
实际拉取: 5200
跳过(已最新): 7
返回为空: 0
失败:     0
新增 kline 行: 5200
====================
```

### 1B. 更新大盘指数（stock_daily 表）

指数数据用于训练时的「大盘环境特征」。之前的报错 `no such column: stock_daily.close` 就是因为这个表结构不对，但**不影响训练核心逻辑**，只是大盘特征填 0。

```bash
# 回填全部默认宽基指数（上证/沪深300/中证500/中证1000/深成/创业板）
python backfill_index.py

# 只回填沪深300
python backfill_index.py --symbols 000300.SH

# 查看默认指数清单
python backfill_index.py --list
```

> **注意**：如果 `stock_daily` 表结构有列缺失问题，这一步可能仍会报错。这不影响训练（模型会中性化处理），但修复后能让大盘环境特征生效，理论上能提升一点准确率。

---

## 步骤 ②：训练模型

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
# 1. 更新个股日线（增量）
python backfill.py kline --all --mode incremental

# 2. 更新大盘指数
python backfill_index.py

# 3. 训练新模型（用本地缓存，不联网）
python train_model.py --all --no-refresh --name trend_xsec --lookback 3000

# 4. 生成强弱榜（用新模型打分）
python rank_snapshot.py

# 5. 启动 Web UI
python main.py --webui
```

### 场景 C：首次部署 / 数据全量重建

```bash
# 1. 全量拉取个股日线（从2010年开始，耗时较长）
python backfill.py kline --all --mode full

# 2. 全量拉取大盘指数
python backfill_index.py

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

# 每日 16:30 自动增量回填 kline
python backfill.py kline --all --mode incremental --schedule 16:30
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
6. **大盘指数报错可忽略** — `stock_daily.close` 列缺失不影响训练核心，只是大盘特征填 0
