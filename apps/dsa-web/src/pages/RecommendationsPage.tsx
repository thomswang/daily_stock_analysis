import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { CalendarClock, Info, ListChecks, RefreshCw, Sparkles, TrendingUp, Trophy } from 'lucide-react';
import { predictionApi } from '../api/prediction';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import {
  ApiErrorAlert,
  Badge,
  Button,
  Card,
  EmptyState,
  PageHeader,
  StatCard,
} from '../components/common';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import type {
  IndustryOption,
  RecommendationItem,
  SnapshotRun,
  WeeklyLiveItem,
  WeeklyRecommendationResponse,
  WeeklyTradeWindow,
} from '../types/prediction';

// ============ Localized text ============

const TEXT = {
  zh: {
    documentTitle: '选股推荐 · DSA',
    eyebrow: 'AI 横截面选股',
    title: '选股推荐',
    description: '系统每日扫描全市场给每只票打「强弱分」，主动挑出最强的一篮子。经长周期回测(2020–2026)：双周调仓·等权·行业分散的口径风险调整后最优。',
    scope: '范围',
    allMarket: '全市场',
    topN: '选取数量',
    snapshot: '快照',
    model: '模型',
    generatedAt: '生成时间',
    latestTag: '最新',
    refresh: '刷新榜单',
    loading: '读取中…',
    emptyTitle: '暂无榜单',
    emptyDesc: '后台尚未生成当日强弱榜。请先运行 python rank_snapshot.py 生成快照。',
    asOf: '数据截至',
    universe: '打分股票',
    picked: '入选',
    industriesCovered: '覆盖行业',
    strategyTitle: '推荐交易口径（回测最优）',
    rebalanceLabel: '调仓',
    weightingLabel: '权重',
    backtestLabel: '回测',
    listHint: '强弱分为横截面相对排序（越高越强）；建议权重为等权（清单内∑=100%）。收益为腾讯实时行情，周一开盘买入、当周周五卖出。',
    colRank: '#',
    colName: '股票',
    colIndustry: '行业',
    colScore: '强弱分',
    colWeight: '建议权重',
    colBuySell: '买入 → 卖出',
    colBuy: '买入价（周一开）',
    colLast: '最新价',
    colLastPending: '最新价（待买入）',
    colLastHolding: '最新价（截至今天）',
    colLastSettled: '卖出价（周五收）',
    colR1: '1日',
    colR3: '3日',
    colR5: '当周',
    colStatus: '状态',
    disclaimerTitle: '风险提示',
    // Trade window banner
    windowTitle: '本周交易窗口',
    windowBuy: '买入日（周一开盘）',
    windowSell: '卖出日（周五收盘）',
    windowNext: '下次买入',
    windowPending: '待买入',
    windowHolding: '持有中',
    windowBuyToday: '本周一买入',
    windowHintLive: '实时行情',
    windowHintPending: '预测买入日未到，暂无收益（买入后自动更新）',
    windowSettled: '已结算',
    windowHintSettled: '预测周已收盘，以下为当周实际收益（非实时回测）',
    windowPredictedBuy: '预测买入时间',
    windowAsOf: '数据快照',
    liveTitleSettled: '当周收益概览（已结算）',
    // Live summary
    liveTitle: '实时收益概览（腾讯行情）',
    liveAvg1: '平均1日',
    liveAvg3: '平均3日',
    liveAvg5: '平均当周',
    liveWin1: '1日胜率',
    liveWin3: '3日胜率',
    liveWin5: '当周胜率',
    unit: '只',
    statusPending: '待买入',
    statusLive: '实时',
    statusMissing: '无行情',
  },
  en: {
    documentTitle: 'Stock Picks · DSA',
    eyebrow: 'AI cross-sectional picks',
    title: 'Stock Recommendations',
    description: 'The system scans the whole market daily and scores every stock, actively surfacing the strongest basket. Long-horizon tested (2020–2026): biweekly rebalance + equal weighting + industry diversification is the best risk-adjusted setup.',
    scope: 'Scope',
    allMarket: 'Whole market',
    topN: 'Top N',
    snapshot: 'Snapshot',
    model: 'Model',
    generatedAt: 'Generated',
    latestTag: 'latest',
    refresh: 'Refresh',
    loading: 'Loading…',
    emptyTitle: 'No board yet',
    emptyDesc: 'Today\'s strength board has not been generated. Run python rank_snapshot.py first.',
    asOf: 'As of',
    universe: 'Scored',
    picked: 'Picked',
    industriesCovered: 'Industries',
    strategyTitle: 'Recommended playbook (backtest-optimal)',
    rebalanceLabel: 'Rebalance',
    weightingLabel: 'Weighting',
    backtestLabel: 'Backtest',
    listHint: 'Strength is a cross-sectional relative rank (higher = stronger); suggested weight is equal-weight (sums to 100%). Returns are live Tencent quotes: buy Monday open, sell Friday close.',
    colRank: '#',
    colName: 'Stock',
    colIndustry: 'Industry',
    colScore: 'Strength',
    colWeight: 'Weight',
    colBuySell: 'Buy → Sell',
    colBuy: 'Buy (Mon open)',
    colLast: 'Last',
    colLastPending: 'Last (pending)',
    colLastHolding: 'Last (as of today)',
    colLastSettled: 'Sell (Fri close)',
    colR1: '1D',
    colR3: '3D',
    colR5: 'Wk',
    colStatus: 'Status',
    disclaimerTitle: 'Disclaimer',
    windowTitle: 'This week\'s trade window',
    windowBuy: 'Buy (Mon open)',
    windowSell: 'Sell (Fri close)',
    windowNext: 'Next buy',
    windowPending: 'Pending',
    windowHolding: 'Holding',
    windowBuyToday: 'Bought Mon',
    windowHintLive: 'Live quotes',
    windowHintPending: 'Buy date not reached yet — no returns (auto-updates after buy)',
    windowSettled: 'Settled',
    windowHintSettled: 'Prediction week closed — actual returns for that week (not live)',
    windowPredictedBuy: 'Predicted buy time',
    windowAsOf: 'Snapshot',
    liveTitleSettled: 'Week returns overview (settled)',
    liveTitle: 'Live returns overview (Tencent)',
    liveAvg1: 'Avg 1D',
    liveAvg3: 'Avg 3D',
    liveAvg5: 'Avg Wk',
    liveWin1: '1D win',
    liveWin3: '3D win',
    liveWin5: 'Wk win',
    unit: '',
    statusPending: 'Pending',
    statusLive: 'Live',
    statusMissing: 'No quote',
  },
} as const;

type Lang = (typeof TEXT)[keyof typeof TEXT];

// ============== Strength score visualizer ==============

/**
 * 极简强弱分：条形长度归一化到当前榜单（主视觉信号）+ 冷暖双色（强=暖红，弱=冷绿）。
 *
 * 设计原则：
 *   - 条形长度 = 相对强弱，一眼可比
 *   - 颜色：高分离度冷暖映射，0.55~0.70 区间也能明显区分
 *   - 去掉徽章底色，数字干净利落
 */
const ScoreCell: React.FC<{ score: number; min: number; max: number }> = ({ score, min, max }) => {
  const span = max - min || 1;
  // 归一化到 [8%, 100%]，保证最低分也有一小段可见
  const pct = Math.max(8, ((score - min) / span) * 100);

  // 冷暖映射：score ∈ [min,max] → t ∈ [0,1]
  // t=0(最弱) → 青冷色 hsl(175, 72%, 48%)  类似 Teal
  // t=1(最强) → 暖玫色 hsl(355, 82%, 60%)  类似 Rose
  const t = Math.min(1, Math.max(0, (score - min) / span));
  const h = 175 + (355 - 175) * t;       // 175 → 355 (跨过 360° 无缝)
  const sat = 72 + (82 - 72) * t;
  const light = 48 + (60 - 48) * t;
  const color = `hsl(${h}, ${sat}%, ${light}%)`;
  const colorLight = `hsl(${h}, ${sat}%, ${light + 14}%)`;

  return (
    <div className="flex flex-col items-center gap-1.5">
      {/* 干净数值 */}
      <span className="font-mono text-[13px] font-semibold tabular-nums leading-none" style={{ color }}>
        {score.toFixed(3)}
      </span>
      {/* 细长条 — 长度即强弱 */}
      <div className="relative h-1 w-full overflow-hidden rounded-full bg-white/[0.06]">
        <div
          className="absolute inset-y-0 left-0 rounded-full transition-all"
          style={{
            width: `${pct}%`,
            background: `linear-gradient(90deg, ${color}, ${colorLight})`,
            boxShadow: t > 0.7 ? `0 0 6px ${color}40` : 'none',
          }}
        />
      </div>
    </div>
  );
};

function pct(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return '--';
  return `${(value * 100).toFixed(digits)}%`;
}

const RankMedal: React.FC<{ rank: number }> = ({ rank }) => {
  const tone = rank === 1 ? '#f59e0b' : rank === 2 ? '#94a3b8' : rank === 3 ? '#b45309' : undefined;
  if (tone) {
    return <Trophy className="h-4 w-4" style={{ color: tone }} aria-hidden />;
  }
  return <span className="font-mono text-xs text-secondary-text">{rank}</span>;
};

const ReturnCell: React.FC<{ value: number | null | undefined; pending?: boolean }> = ({ value, pending }) => {
  if (pending) {
    return <span className="text-[11px] font-medium text-amber-400/90">待买入</span>;
  }
  if (value == null) return <span className="text-secondary-text/60">--</span>;
  const positive = value > 0;
  const negative = value < 0;
  const color = positive ? 'text-rose-400' : negative ? 'text-emerald-400' : 'text-secondary-text';
  return (
    <span className={`font-mono text-xs font-semibold tabular-nums ${color}`}>
      {value > 0 ? '+' : ''}
      {value.toFixed(2)}%
    </span>
  );
};

const StatusBadge: React.FC<{ live: WeeklyLiveItem; window: WeeklyTradeWindow; text: Lang }> = ({
  live,
  window,
  text,
}) => {
  if (!window.isBuyReached) {
    return <Badge variant="warning">{text.statusPending}</Badge>;
  }
  if (live.available) {
    return window.isSettled ? (
      <Badge variant="info">{text.windowSettled}</Badge>
    ) : (
      <Badge variant="success">{text.statusLive}</Badge>
    );
  }
  return <Badge variant="default">{text.statusMissing}</Badge>;
};

// ============== Trade window banner ==============

const TradeWindowBanner: React.FC<{ window: WeeklyTradeWindow; text: Lang }> = ({ window, text }) => {
  const pending = window.status === 'pending';
  const settled = window.status === 'settled';
  const accent = pending
    ? 'border-amber-500/30 bg-amber-500/5'
    : settled
      ? 'border-cyan/30 bg-cyan/5'
      : 'border-emerald-500/30 bg-emerald-500/5';
  const statusColor = pending ? 'text-amber-400' : settled ? 'text-cyan' : 'text-emerald-400';
  const statusText = pending
    ? text.windowPending
    : settled
      ? text.windowSettled
      : window.status === 'buy_today'
        ? text.windowBuyToday
        : text.windowHolding;
  const badgeVariant = pending ? 'warning' : settled ? 'info' : 'success';
  const hint = pending ? text.windowHintPending : settled ? text.windowHintSettled : text.windowHintLive;
  return (
    <div className={`flex flex-col gap-3 rounded-2xl border px-4 py-3 ${accent}`}>
      <div className="flex flex-wrap items-center gap-2">
        <CalendarClock className={`h-4 w-4 ${statusColor}`} />
        <span className="text-sm font-semibold text-foreground">{text.windowTitle}</span>
        <Badge variant={badgeVariant}>{statusText}</Badge>
        <span className="ml-auto text-[11px] text-secondary-text">{hint}</span>
      </div>

      {/* 醒目：预测买入时间（锚定到预测周，而非请求当天） */}
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-border/40 bg-elevated/40 px-3 py-2">
        <span className="rounded-md bg-cyan/15 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-cyan">
          {text.windowPredictedBuy}
        </span>
        <span className="font-mono text-sm font-semibold" style={{ color: statusColor }}>
          {window.buyDate} 开盘 → {window.sellDate} 收盘
        </span>
        {window.asOfDate ? (
          <span className="ml-auto text-[11px] text-secondary-text">
            {text.windowAsOf}：{window.asOfDate}
          </span>
        ) : null}
      </div>

      <div className="grid grid-cols-3 gap-2 sm:gap-4">
        <div className="flex flex-col">
          <span className="text-[11px] uppercase tracking-wider text-secondary-text">{text.windowBuy}</span>
          <span className="font-mono text-sm font-semibold text-foreground">{window.buyDate}</span>
        </div>
        <div className="flex flex-col">
          <span className="text-[11px] uppercase tracking-wider text-secondary-text">{text.windowSell}</span>
          <span className="font-mono text-sm font-semibold text-foreground">{window.sellDate}</span>
        </div>
        <div className="flex flex-col">
          <span className="text-[11px] uppercase tracking-wider text-secondary-text">{text.windowNext}</span>
          <span className="font-mono text-sm font-semibold text-foreground">{window.nextBuyDate}</span>
        </div>
      </div>
    </div>
  );
};

// ============== Combined ranking + live table ==============

const CombinedTable: React.FC<{
  items: RecommendationItem[];
  live: WeeklyLiveItem[];
  window: WeeklyTradeWindow;
  text: Lang;
}> = ({ items, live, window, text }) => {
  const liveByCode = useMemo(() => {
    const m = new Map<string, WeeklyLiveItem>();
    for (const l of live) m.set(l.code.toUpperCase(), l);
    return m;
  }, [live]);

  // 归一化条形长度到当前榜单，让强弱差异一目了然。
  const [sMin, sMax] = useMemo(() => {
    if (!items.length) return [0, 1];
    const scores = items.map((i) => i.strengthScore);
    return [Math.min(...scores), Math.max(...scores)];
  }, [items]);

  // 最新价列头按交易窗口状态区分含义，避免「最新价」被误解为实时报价。
  const lastColLabel =
    window.status === 'pending'
      ? text.colLastPending
      : window.isSettled
        ? text.colLastSettled
        : text.colLastHolding;

  return (
    <div className="overflow-x-auto rounded-xl border border-border/40">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border/60 bg-elevated/40 text-center text-xs uppercase tracking-wider text-secondary-text">
            <th className="w-10 px-2 py-2.5">{text.colRank}</th>
            <th className="px-2 py-2.5">{text.colName}</th>
            <th className="px-2 py-2.5">{text.colIndustry}</th>
            <th className="px-2 py-2.5">{text.colScore}</th>
            <th className="px-2 py-2.5">{text.colWeight}</th>
            <th className="px-2 py-2.5">{text.colBuySell}</th>
            <th className="px-2 py-2.5">{text.colBuy}</th>
            <th className="px-2 py-2.5">{lastColLabel}</th>
            <th className="px-2 py-2.5">{text.colR1}</th>
            <th className="px-2 py-2.5">{text.colR3}</th>
            <th className="px-2 py-2.5">{text.colR5}</th>
            <th className="px-2 py-2.5">{text.colStatus}</th>
          </tr>
        </thead>
        <tbody>
          {items.map((it) => {
            const key = it.code.toUpperCase();
            const lv = liveByCode.get(key);
            const pending = !window.isBuyReached;
            const lastPrice = lv?.lastPrice != null ? lv.lastPrice : it.lastClose;
            return (
              <tr key={it.code} className="border-b border-border/20 transition-colors hover:bg-elevated/30">
                <td className="px-2 py-2.5 text-center align-middle">
                  <RankMedal rank={it.rank} />
                </td>
                <td className="px-2 py-2.5 text-center align-middle">
                  <div className="flex flex-col items-center">
                    <span className="font-medium text-foreground">{it.stockName || it.code}</span>
                    <span className="font-mono text-[11px] text-secondary-text">{it.code}</span>
                  </div>
                </td>
                <td className="px-2 py-2.5 text-center align-middle">
                  {it.industry ? <Badge variant="default">{it.industry}</Badge> : <span className="text-xs text-secondary-text">--</span>}
                </td>
                <td className="px-2 py-2.5 text-center align-middle">
                  <ScoreCell score={it.strengthScore} min={sMin} max={sMax} />
                </td>
                <td className="px-2 py-2.5 text-center align-middle">
                  <span className="inline-flex items-center gap-1 font-mono text-xs font-medium text-[hsl(var(--primary))]">
                    <TrendingUp className="h-3 w-3" />
                    {pct(it.suggestedWeight)}
                  </span>
                </td>
                <td className="px-2 py-2.5 text-center align-middle">
                  <span className="font-mono text-[11px] text-secondary-text">
                    {window.buyDate.slice(5)} → {window.sellDate.slice(5)}
                  </span>
                </td>
                <td className="px-2 py-2.5 text-center align-middle font-mono text-xs text-secondary-text tabular-nums">
                  {pending && !lv?.available
                    ? '待买入'
                    : lv?.buyPrice != null
                      ? lv.buyPrice.toFixed(2)
                      : '--'}
                </td>
                <td className="px-2 py-2.5 text-center align-middle font-mono text-xs text-foreground tabular-nums">
                  {pending && !lv?.available ? '待买入' : lastPrice != null ? lastPrice.toFixed(2) : '--'}
                </td>
                <td className="px-2 py-2.5 text-center align-middle">
                  <ReturnCell value={lv?.return1dPct} pending={pending && !lv?.available} />
                </td>
                <td className="px-2 py-2.5 text-center align-middle">
                  <ReturnCell value={lv?.return3dPct} pending={pending && !lv?.available} />
                </td>
                <td className="px-2 py-2.5 text-center align-middle">
                  <ReturnCell value={lv?.returnWkPct} pending={pending && !lv?.available} />
                </td>
                <td className="px-2 py-2.5 text-center align-middle">
                  {lv ? <StatusBadge live={lv} window={window} text={text} /> : <span className="text-xs text-secondary-text">--</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};

const RecommendationsPage: React.FC = () => {
  const { language } = useUiLanguage();
  const text = TEXT[language];

  const [runId, setRunId] = useState<number | null>(null);
  const [runs, setRuns] = useState<SnapshotRun[]>([]);
  const [industry, setIndustry] = useState<string>('');
  const [topN, setTopN] = useState(20);
  const [industries, setIndustries] = useState<IndustryOption[]>([]);
  const [data, setData] = useState<WeeklyRecommendationResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);

  useEffect(() => {
    document.title = text.documentTitle;
  }, [text.documentTitle]);

  // 载入历史快照 run 列表（供「快照选择」下拉，可回溯不同模型/时间）
  useEffect(() => {
    let active = true;
    predictionApi
      .recommendationRuns(50)
      .then((res) => {
        if (active) setRuns(res.runs ?? []);
      })
      .catch(() => {
        if (active) setRuns([]);
      });
    return () => {
      active = false;
    };
  }, []);

  // run 变化时刷新行业下拉（行业清单是按 run 的）
  useEffect(() => {
    let active = true;
    predictionApi
      .industries(runId)
      .then((res) => {
        if (active) setIndustries(res.industries ?? []);
      })
      .catch(() => {
        if (active) setIndustries([]);
      });
    return () => {
      active = false;
    };
  }, [runId]);

  const fetchBoard = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await predictionApi.recommendationsWeekly({
        runId,
        industry: industry || undefined,
        topN,
      });
      setData(res);
    } catch (err) {
      setError(getParsedApiError(err));
      setData(null);
    } finally {
      setIsLoading(false);
    }
  }, [runId, industry, topN]);

  useEffect(() => {
    void fetchBoard();
  }, [fetchBoard]);

  const liveSummary = data?.liveSummary;

  return (
    <div className="mx-auto flex w-full max-w-7xl flex-col gap-5 px-3 py-4 sm:px-4">
      <PageHeader eyebrow={text.eyebrow} title={text.title} description={text.description} />

      {/* Controls */}
      <Card padding="md">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
          <div className="min-w-0 flex-1">
            <label className="mb-1.5 block text-xs uppercase tracking-[0.22em] text-secondary-text">
              {text.snapshot}
            </label>
            <select
              value={runId ?? ''}
              onChange={(e) => setRunId(e.target.value === '' ? null : Number(e.target.value))}
              disabled={isLoading || runs.length === 0}
              className="input-surface input-focus-glow h-11 w-full rounded-xl border bg-transparent px-3 text-sm disabled:opacity-50"
            >
              <option value="">{text.latestTag}</option>
              {runs.map((r, idx) => (
                <option key={r.runId} value={r.runId}>
                  #{r.runId} · M{r.modelId} · {r.modelName}@{r.modelVersion} · {r.generatedAt}
                  {idx === 0 ? `（${text.latestTag}）` : ''}
                </option>
              ))}
            </select>
          </div>
          <div className="min-w-0 flex-1">
            <label className="mb-1.5 block text-xs uppercase tracking-[0.22em] text-secondary-text">
              {text.scope}
            </label>
            <select
              value={industry}
              onChange={(e) => setIndustry(e.target.value)}
              disabled={isLoading}
              className="input-surface input-focus-glow h-11 w-full rounded-xl border bg-transparent px-3 text-sm"
            >
              <option value="">{text.allMarket}</option>
              {industries.map((it) => (
                <option key={it.industry} value={it.industry}>
                  {it.industry}（{it.count}）
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1.5 block text-xs uppercase tracking-[0.22em] text-secondary-text">
              {text.topN}
            </label>
            <select
              value={topN}
              onChange={(e) => setTopN(Number(e.target.value))}
              disabled={isLoading}
              className="input-surface input-focus-glow h-11 w-full rounded-xl border bg-transparent px-3 text-sm lg:w-28"
            >
              {[10, 20].map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </div>
          <Button
            variant="primary"
            size="lg"
            isLoading={isLoading}
            loadingText={text.loading}
            onClick={() => void fetchBoard()}
            className="lg:w-32"
          >
            <RefreshCw className="h-4 w-4" />
            {text.refresh}
          </Button>
        </div>
      </Card>

      {error ? <ApiErrorAlert error={error} /> : null}

      {!data && !error && !isLoading ? (
        <EmptyState title={text.emptyTitle} description={text.emptyDesc} className="border-dashed" />
      ) : null}

      {data ? (
        <div className="flex flex-col gap-5 animate-fade-in">
          {/* Summary cards */}
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatCard tone="primary" label={text.scope} value={data.scope} hint={`${text.asOf} ${data.asOfDate ?? '--'}`} />
            <StatCard label={text.picked} value={data.count} hint={text.unit} />
            <StatCard
              tone="success"
              label={text.industriesCovered}
              value={new Set(data.items.map((i) => i.industry).filter(Boolean)).size}
            />
            <StatCard
              label={text.liveAvg5}
              value={
                <span className={liveSummary && liveSummary.avgWkPct >= 0 ? 'text-rose-400' : 'text-emerald-400'}>
                  {(liveSummary && liveSummary.avgWkPct > 0 ? '+' : '') + (liveSummary ? liveSummary.avgWkPct.toFixed(2) : '0.00') + '%'}
                </span>
              }
              hint={data.tradeWindow.isBuyReached ? `${text.liveWin5} ${liveSummary ? (liveSummary.winRateWk * 100).toFixed(0) : 0}%` : text.windowPending}
            />
          </div>

          {/* 当前快照来源（模型 + 生成时间），用于回溯 */}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-xl border border-border/60 bg-card/40 px-4 py-2 text-xs text-secondary-text">
            <span className="font-medium text-foreground">{text.snapshot} #{data.runId}</span>
            <span>{text.model}：M{data.modelId} · {data.modelName}@{data.modelVersion}</span>
            <span>{text.generatedAt}：{data.generatedAt ?? '--'}</span>
            <span>{text.asOf}：{data.asOfDate ?? '--'}</span>
          </div>

          {/* Trade window banner */}
          <TradeWindowBanner window={data.tradeWindow} text={text} />

          {/* Live returns overview strip */}
          {data.tradeWindow.isBuyReached && liveSummary ? (
            <div className="grid grid-cols-3 gap-2.5 lg:grid-cols-6">
              <StatCard label={text.liveAvg1} value={<span className={liveSummary.avg1dPct >= 0 ? 'text-rose-400' : 'text-emerald-400'}>{(liveSummary.avg1dPct > 0 ? '+' : '') + liveSummary.avg1dPct.toFixed(2) + '%'}</span>} hint={`${text.liveWin1} ${(liveSummary.winRate1d * 100).toFixed(0)}%`} />
              <StatCard label={text.liveAvg3} value={<span className={liveSummary.avg3dPct >= 0 ? 'text-rose-400' : 'text-emerald-400'}>{(liveSummary.avg3dPct > 0 ? '+' : '') + liveSummary.avg3dPct.toFixed(2) + '%'}</span>} hint={`${text.liveWin3} ${(liveSummary.winRate3d * 100).toFixed(0)}%`} />
              <StatCard label={text.liveAvg5} value={<span className={liveSummary.avgWkPct >= 0 ? 'text-rose-400' : 'text-emerald-400'}>{(liveSummary.avgWkPct > 0 ? '+' : '') + liveSummary.avgWkPct.toFixed(2) + '%'}</span>} hint={`${text.liveWin5} ${(liveSummary.winRateWk * 100).toFixed(0)}%`} />
              <StatCard tone="primary" label={data.tradeWindow.isSettled ? text.liveTitleSettled : text.liveTitle} value={liveSummary.withData} hint={`${text.picked} ${liveSummary.total}`} />
            </div>
          ) : null}

          {/* Strategy hint */}
          {data.strategy ? (
            <Card padding="md">
              <div className="flex items-start gap-3">
                <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-2xl bg-primary-gradient text-[hsl(var(--primary-foreground))]">
                  <Sparkles className="h-5 w-5" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-semibold text-foreground">{text.strategyTitle}</span>
                    <Badge variant="success">{data.strategy.name}</Badge>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-secondary-text">
                    <span>{text.rebalanceLabel}：{data.strategy.rebalance}</span>
                    <span>{text.weightingLabel}：{data.strategy.weighting}</span>
                  </div>
                  {data.strategy.backtest ? (
                    <p className="mt-1.5 text-xs text-secondary-text">
                      <span className="text-emerald-400">{text.backtestLabel}：</span>
                      {data.strategy.backtest}
                    </p>
                  ) : null}
                </div>
              </div>
            </Card>
          ) : null}

          {/* Combined table: 推荐列表 + 实时收益（单页，不再分 Tab） */}
          <Card padding="md">
            <div className="-mx-4 mb-3 flex items-center gap-2 border-b border-border/40 px-4 pb-3">
              <ListChecks className="h-4 w-4 text-cyan" />
              <span className="text-sm font-semibold text-foreground">{text.title}</span>
              <span className="ml-auto text-xs text-secondary-text">{text.listHint}</span>
            </div>
            <CombinedTable
              items={data.items}
              live={data.live}
              window={data.tradeWindow}
              text={text}
            />
          </Card>

          {/* Disclaimer */}
          <div className="flex items-start gap-2 rounded-xl border border-warning/25 bg-warning/5 px-4 py-3 text-sm text-secondary-text">
            <Info className="mt-0.5 h-4 w-4 flex-shrink-0 text-warning" />
            <span>
              <span className="font-medium text-warning">{text.disclaimerTitle}：</span>
              {data.disclaimer}
            </span>
          </div>
        </div>
      ) : null}
    </div>
  );
};

export default RecommendationsPage;
