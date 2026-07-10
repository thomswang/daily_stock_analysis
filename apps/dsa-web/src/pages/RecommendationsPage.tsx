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
    industryCap: '每行业上限',
    noCap: '不限制',
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
    capLabel: '行业分散',
    backtestLabel: '回测',
    listHint: '强弱分为横截面相对排序（越高越强）；建议权重为等权（清单内∑=100%）。收益为腾讯实时行情，周一开盘买入、当周周五卖出。',
    colRank: '#',
    colName: '股票',
    colIndustry: '行业',
    colScore: '强弱分',
    colWeight: '建议权重',
    colBuySell: '买入 → 卖出',
    colLast: '最新价',
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
    industryCap: 'Per-industry cap',
    noCap: 'No cap',
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
    capLabel: 'Diversify',
    backtestLabel: 'Backtest',
    listHint: 'Strength is a cross-sectional relative rank (higher = stronger); suggested weight is equal-weight (sums to 100%). Returns are live Tencent quotes: buy Monday open, sell Friday close.',
    colRank: '#',
    colName: 'Stock',
    colIndustry: 'Industry',
    colScore: 'Strength',
    colWeight: 'Weight',
    colBuySell: 'Buy → Sell',
    colLast: 'Last',
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

const SCORE_COLOR = '#6366f1';

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
  scoreWidth: (s: number) => number;
}> = ({ items, live, window, text, scoreWidth }) => {
  const liveByCode = useMemo(() => {
    const m = new Map<string, WeeklyLiveItem>();
    for (const l of live) m.set(l.code.toUpperCase(), l);
    return m;
  }, [live]);

  return (
    <div className="overflow-x-auto rounded-xl border border-border/40">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border/60 bg-elevated/40 text-left text-xs uppercase tracking-wider text-secondary-text">
            <th className="w-10 py-2.5 pl-3 pr-2 text-center">{text.colRank}</th>
            <th className="py-2.5 pr-3">{text.colName}</th>
            <th className="py-2.5 pr-3">{text.colIndustry}</th>
            <th className="py-2.5 pr-3">{text.colScore}</th>
            <th className="py-2.5 pr-3 text-right">{text.colWeight}</th>
            <th className="py-2.5 pr-3 text-center">{text.colBuySell}</th>
            <th className="py-2.5 pr-2 text-right">{text.colLast}</th>
            <th className="py-2.5 pr-2 text-right">{text.colR1}</th>
            <th className="py-2.5 pr-2 text-right">{text.colR3}</th>
            <th className="py-2.5 pr-2 text-right">{text.colR5}</th>
            <th className="py-2.5 pr-3 text-center">{text.colStatus}</th>
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
                <td className="py-2.5 pl-3 pr-2 text-center align-middle">
                  <RankMedal rank={it.rank} />
                </td>
                <td className="py-2.5 pr-3 align-middle">
                  <div className="flex flex-col">
                    <span className="font-medium text-foreground">{it.stockName || it.code}</span>
                    <span className="font-mono text-[11px] text-secondary-text">{it.code}</span>
                  </div>
                </td>
                <td className="py-2.5 pr-3 align-middle">
                  {it.industry ? <Badge variant="default">{it.industry}</Badge> : <span className="text-xs text-secondary-text">--</span>}
                </td>
                <td className="py-2.5 pr-3 align-middle">
                  <div className="flex items-center gap-2">
                    <div className="relative h-2 w-24 overflow-hidden rounded-full bg-elevated/60">
                      <div
                        className="absolute left-0 top-0 h-full rounded-full"
                        style={{ width: `${scoreWidth(it.strengthScore)}%`, background: SCORE_COLOR, opacity: 0.8 }}
                      />
                    </div>
                    <span className="font-mono text-xs text-foreground">{it.strengthScore.toFixed(3)}</span>
                  </div>
                </td>
                <td className="py-2.5 pr-3 text-right align-middle">
                  <span className="inline-flex items-center gap-1 font-mono text-xs font-medium text-[hsl(var(--primary))]">
                    <TrendingUp className="h-3 w-3" />
                    {pct(it.suggestedWeight)}
                  </span>
                </td>
                <td className="py-2.5 pr-3 text-center align-middle">
                  <span className="font-mono text-[11px] text-secondary-text">
                    {window.buyDate.slice(5)} → {window.sellDate.slice(5)}
                  </span>
                </td>
                <td className="py-2.5 pr-2 text-right align-middle font-mono text-xs text-foreground tabular-nums">
                  {lastPrice != null ? lastPrice.toFixed(2) : '--'}
                </td>
                <td className="py-2.5 pr-2 text-right align-middle">
                  <ReturnCell value={lv?.return1dPct} pending={pending && !lv?.available} />
                </td>
                <td className="py-2.5 pr-2 text-right align-middle">
                  <ReturnCell value={lv?.return3dPct} pending={pending && !lv?.available} />
                </td>
                <td className="py-2.5 pr-2 text-right align-middle">
                  <ReturnCell value={lv?.returnWkPct} pending={pending && !lv?.available} />
                </td>
                <td className="py-2.5 pr-3 text-center align-middle">
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

  const [industry, setIndustry] = useState<string>('');
  const [topN, setTopN] = useState(20);
  const [industryCap, setIndustryCap] = useState<number | null>(3);
  const [industries, setIndustries] = useState<IndustryOption[]>([]);
  const [data, setData] = useState<WeeklyRecommendationResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);

  useEffect(() => {
    document.title = text.documentTitle;
  }, [text.documentTitle]);

  useEffect(() => {
    let active = true;
    predictionApi
      .industries()
      .then((res) => {
        if (active) setIndustries(res.industries ?? []);
      })
      .catch(() => {
        if (active) setIndustries([]);
      });
    return () => {
      active = false;
    };
  }, []);

  const fetchBoard = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await predictionApi.recommendationsWeekly({
        industry: industry || undefined,
        topN,
        industryCap: industry ? null : industryCap,
      });
      setData(res);
    } catch (err) {
      setError(getParsedApiError(err));
      setData(null);
    } finally {
      setIsLoading(false);
    }
  }, [industry, topN, industryCap]);

  useEffect(() => {
    void fetchBoard();
  }, [fetchBoard]);

  const maxScore = useMemo(() => {
    if (!data?.items?.length) return 1;
    return Math.max(...data.items.map((i) => i.strengthScore), 0.0001);
  }, [data]);
  const minScore = useMemo(() => {
    if (!data?.items?.length) return 0;
    return Math.min(...data.items.map((i) => i.strengthScore), 0);
  }, [data]);

  const scoreWidth = (score: number): number => {
    const span = maxScore - minScore || 1;
    return 25 + ((score - minScore) / span) * 75; // keep a visible minimum bar
  };

  const liveSummary = data?.liveSummary;

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5 px-3 py-4 sm:px-4">
      <PageHeader eyebrow={text.eyebrow} title={text.title} description={text.description} />

      {/* Controls */}
      <Card padding="md">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
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
              {[10, 20, 30, 50].map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1.5 block text-xs uppercase tracking-[0.22em] text-secondary-text">
              {text.industryCap}
            </label>
            <select
              value={industryCap ?? 'none'}
              onChange={(e) => setIndustryCap(e.target.value === 'none' ? null : Number(e.target.value))}
              disabled={isLoading || Boolean(industry)}
              className="input-surface input-focus-glow h-11 w-full rounded-xl border bg-transparent px-3 text-sm lg:w-32 disabled:opacity-50"
            >
              {[2, 3, 5].map((n) => (
                <option key={n} value={n}>≤ {n}</option>
              ))}
              <option value="none">{text.noCap}</option>
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
                    {data.strategy.industryCap != null ? (
                      <span>{text.capLabel}：≤ {data.strategy.industryCap}</span>
                    ) : null}
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
              scoreWidth={scoreWidth}
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
