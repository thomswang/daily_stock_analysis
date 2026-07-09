import type React from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { BarChart3, Calendar, Info, ListChecks, RefreshCw, Sparkles, TrendingDown, TrendingUp, Trophy } from 'lucide-react';
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
  RecommendationsResponse,
  RecommendationBacktestResponse,
  BacktestStockItem,
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
    listTitle: '强弱榜',
    listHint: '强弱分为横截面相对排序（越高越强），建议权重为等权（清单内∑=100%）',
    colRank: '#',
    colName: '股票',
    colIndustry: '行业',
    colScore: '强弱分',
    colWeight: '建议权重',
    colClose: '最新价',
    unit: '只',
    disclaimerTitle: '风险提示',
    // Tabs & backtest table
    tabList: '推荐列表',
    tabBacktest: '收益回测',
    backtestHint: '基于历史K线，模拟周一开盘价(集合竞价)买入，统计 1/3/5 日实际涨跌幅。仅供研究参考。',
    backtestStrategy: '本次回测口径',
    backtestBuyDate: '实际买入日',
    backtestEmpty: '暂无回测数据',
    btColCode: '股票',
    btColScore: '强弱分',
    btColBuyDate: '买入日',
    btColBuyPrice: '买入价',
    btColAuction: '竞价价',
    btColOpen: '开盘价',
    btColR1: '1日收益',
    btColR3: '3日收益',
    btColR5: '当周收益',
    btColKline: 'K线判断',
    btColVol: '成交量',
    btSummaryTotal: '回测样本',
    btSummaryAvg1: '平均1日',
    btSummaryAvg3: '平均3日',
    btSummaryAvg5: '平均当周',
    btSummaryBest: '最佳1日',
    btSummaryWorst: '最差1日',
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
    listTitle: 'Strength board',
    listHint: 'Strength is a cross-sectional relative rank (higher = stronger); suggested weight is equal-weight (sums to 100%)',
    colRank: '#',
    colName: 'Stock',
    colIndustry: 'Industry',
    colScore: 'Strength',
    colWeight: 'Weight',
    colClose: 'Close',
    unit: '',
    disclaimerTitle: 'Disclaimer',
    // Tabs & backtest table
    tabList: 'Picks',
    tabBacktest: 'Returns backtest',
    backtestHint: 'Simulates buying at Monday\'s open (call auction) and reports the actual 1/3/5-day returns. Research use only.',
    backtestStrategy: 'Backtest setup',
    backtestBuyDate: 'Actual buy date',
    backtestEmpty: 'No backtest data',
    btColCode: 'Stock',
    btColScore: 'Score',
    btColBuyDate: 'Buy date',
    btColBuyPrice: 'Buy price',
    btColAuction: 'Auction',
    btColOpen: 'Open',
    btColR1: '1D ret',
    btColR3: '3D ret',
    btColR5: 'Wk ret',
    btColKline: 'K-line',
    btColVol: 'Volume',
    btSummaryTotal: 'Samples',
    btSummaryAvg1: 'Avg 1D',
    btSummaryAvg3: 'Avg 3D',
    btSummaryAvg5: 'Avg Wk',
    btSummaryBest: 'Best 1D',
    btSummaryWorst: 'Worst 1D',
  },
} as const;

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

// ============== Backtest table helpers ==============

const KLINE_PRIMARY_STYLES: Record<string, { bg: string; color: string }> = {
  '均线修复转强': { bg: 'bg-emerald-500/10', color: 'text-emerald-400' },
  '强势反弹': { bg: 'bg-emerald-500/10', color: 'text-emerald-400' },
  '趋势修复观察': { bg: 'bg-amber-500/10', color: 'text-amber-400' },
  '震荡修复': { bg: 'bg-amber-500/10', color: 'text-amber-400' },
  '蓄势整理': { bg: 'bg-sky-500/10', color: 'text-sky-400' },
  '方向选择中': { bg: 'bg-violet-500/10', color: 'text-violet-400' },
  '底部震荡': { bg: 'bg-rose-500/10', color: 'text-rose-400' },
  '回调确认': { bg: 'bg-rose-500/10', color: 'text-rose-400' },
  '弱势延续': { bg: 'bg-rose-500/20', color: 'text-rose-300' },
  '不确定': { bg: 'bg-slate-500/10', color: 'text-slate-400' },
};
const KLINE_DEFAULT = { bg: 'bg-slate-500/10', color: 'text-slate-400' };

const VOLUME_STYLES: Record<string, string> = {
  '放量': 'bg-rose-500/10 text-rose-400',
  '温和放量': 'bg-amber-500/10 text-amber-400',
  '正常': 'bg-emerald-500/10 text-emerald-400',
  '缩量': 'bg-sky-500/10 text-sky-400',
  '异常': 'bg-slate-500/10 text-slate-400',
};

const KLineChip: React.FC<{ primary: string; secondary: string }> = ({ primary, secondary }) => {
  const s = KLINE_PRIMARY_STYLES[primary] ?? KLINE_DEFAULT;
  return (
    <span
      className={`inline-flex flex-col items-center gap-0.5 rounded-md px-2 py-0.5 text-center ${s.bg} ${s.color}`}
      style={{ minWidth: 76, lineHeight: 1.15 }}
      title={secondary}
    >
      <span className="text-[11px] font-semibold">{primary}</span>
      <span className="text-[10px] font-medium opacity-75">{secondary}</span>
    </span>
  );
};

const VolumeChip: React.FC<{ status: string }> = ({ status }) => {
  const cls = VOLUME_STYLES[status] ?? VOLUME_STYLES['异常'];
  return (
    <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-semibold ${cls}`}>
      {status}
    </span>
  );
};

const ReturnCell: React.FC<{ value: number | null | undefined }> = ({ value }) => {
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

const ScorePct: React.FC<{ value: number }> = ({ value }) => (
  <span className="font-mono text-xs font-semibold tabular-nums text-foreground">
    {(value * 100).toFixed(0)}
  </span>
);

// ============== Sub-components: RankingTable & BacktestPanel ==============

const RankingTable: React.FC<{
  items: RecommendationItem[];
  text: typeof TEXT.zh;
  scoreWidth: (s: number) => number;
}> = ({ items, text, scoreWidth }) => (
  <div className="overflow-x-auto">
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b border-border/60 text-left text-xs uppercase tracking-wider text-secondary-text">
          <th className="w-10 py-2 pr-2 text-center">{text.colRank}</th>
          <th className="py-2 pr-3">{text.colName}</th>
          <th className="py-2 pr-3">{text.colIndustry}</th>
          <th className="py-2 pr-3">{text.colScore}</th>
          <th className="py-2 pr-3 text-right">{text.colWeight}</th>
          <th className="py-2 pr-1 text-right">{text.colClose}</th>
        </tr>
      </thead>
      <tbody>
        {items.map((it) => (
          <tr key={it.code} className="border-b border-border/30 transition-colors hover:bg-elevated/40">
            <td className="py-2.5 pr-2 text-center align-middle">
              <RankMedal rank={it.rank} />
            </td>
            <td className="py-2.5 pr-3 align-middle">
              <div className="flex flex-col">
                <span className="font-medium text-foreground">{it.stockName || it.code}</span>
                <span className="font-mono text-xs text-secondary-text">{it.code}</span>
              </div>
            </td>
            <td className="py-2.5 pr-3 align-middle">
              {it.industry ? (
                <Badge variant="default">{it.industry}</Badge>
              ) : (
                <span className="text-xs text-secondary-text">--</span>
              )}
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
            <td className="py-2.5 pr-1 text-right align-middle font-mono text-xs text-foreground">
              {it.lastClose != null ? it.lastClose.toFixed(2) : '--'}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

const BacktestPanel: React.FC<{
  data: RecommendationBacktestResponse | null;
  loading: boolean;
  error: ParsedApiError | null;
  onRefresh: () => void;
  text: typeof TEXT.zh;
}> = ({ data, loading, error, onRefresh, text }) => {
  if (error) {
    return <ApiErrorAlert error={error} />;
  }
  if (loading && !data) {
    return (
      <div className="flex items-center justify-center gap-2 py-10 text-sm text-secondary-text">
        <RefreshCw className="h-4 w-4 animate-spin" />
        {text.loading}
      </div>
    );
  }
  if (!data || data.items.length === 0) {
    return <EmptyState title={text.backtestEmpty} className="border-dashed py-8" />;
  }
  const s = data.summary;
  return (
    <div className="flex flex-col gap-4 animate-fade-in">
      {/* Strategy info row */}
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-border/40 bg-elevated/30 px-3 py-2 text-xs text-secondary-text">
        <Calendar className="h-3.5 w-3.5 text-cyan" />
        <span>
          <span className="text-foreground">{text.backtestBuyDate}：</span>
          {data.actualBuyDate}
        </span>
        <span className="mx-1 hidden h-3 w-px bg-border/60 sm:inline-block" />
        <span className="hidden sm:inline">
          <span className="text-foreground">{text.backtestStrategy}：</span>
          {data.strategyNote}
        </span>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-6">
        <StatCard tone="primary" label={text.btSummaryTotal} value={s.total} hint={`有效 ${s.withData}`} />
        <StatCard
          label={text.btSummaryAvg1}
          value={<span className={s.avg1dPct >= 0 ? 'text-rose-400' : 'text-emerald-400'}>{(s.avg1dPct > 0 ? '+' : '') + s.avg1dPct.toFixed(2) + '%'}</span>}
          hint={`胜率 ${(s.winRate1d * 100).toFixed(0)}%`}
        />
        <StatCard
          label={text.btSummaryAvg3}
          value={<span className={s.avg3dPct >= 0 ? 'text-rose-400' : 'text-emerald-400'}>{(s.avg3dPct > 0 ? '+' : '') + s.avg3dPct.toFixed(2) + '%'}</span>}
          hint={`胜率 ${(s.winRate3d * 100).toFixed(0)}%`}
        />
        <StatCard
          label={text.btSummaryAvg5}
          value={<span className={s.avgWkPct >= 0 ? 'text-rose-400' : 'text-emerald-400'}>{(s.avgWkPct > 0 ? '+' : '') + s.avgWkPct.toFixed(2) + '%'}</span>}
          hint={`胜率 ${(s.winRateWk * 100).toFixed(0)}%`}
        />
        <StatCard
          label={text.btSummaryBest}
          value={<span className="text-rose-400">+{s.best1dPct.toFixed(2)}%</span>}
          icon={<TrendingUp className="h-4 w-4" />}
        />
        <StatCard
          label={text.btSummaryWorst}
          value={<span className="text-emerald-400">{s.worst1dPct.toFixed(2)}%</span>}
          icon={<TrendingDown className="h-4 w-4" />}
        />
      </div>

      {/* Backtest table */}
      <div className="overflow-x-auto rounded-xl border border-border/40">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border/60 bg-elevated/40 text-left text-xs uppercase tracking-wider text-secondary-text">
              <th className="py-2.5 pl-3 pr-2">{text.btColCode}</th>
              <th className="py-2.5 pr-2 text-center">{text.btColScore}</th>
              <th className="py-2.5 pr-2 text-center">{text.btColBuyDate}</th>
              <th className="py-2.5 pr-2 text-center">买入价来源</th>
              <th className="py-2.5 pr-2 text-right">{text.btColBuyPrice}</th>
              <th className="py-2.5 pr-2 text-right">{text.btColAuction}</th>
              <th className="py-2.5 pr-2 text-right">{text.btColOpen}</th>
              <th className="py-2.5 pr-2 text-right">{text.btColR1}</th>
              <th className="py-2.5 pr-2 text-right">{text.btColR3}</th>
              <th className="py-2.5 pr-2 text-right">{text.btColR5}</th>
              <th className="py-2.5 pr-2 text-center">{text.btColKline}</th>
              <th className="py-2.5 pr-3 text-center">{text.btColVol}</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((it: BacktestStockItem) => (
              <tr key={it.code} className="border-b border-border/20 transition-colors hover:bg-elevated/30">
                <td className="py-2.5 pl-3 pr-2 align-middle">
                  <div className="flex flex-col">
                    <span className="font-medium text-foreground">{it.stockName || it.code}</span>
                    <span className="font-mono text-[11px] text-secondary-text">{it.code}</span>
                  </div>
                </td>
                <td className="py-2.5 pr-2 text-center align-middle">
                  <ScorePct value={it.strengthScore} />
                </td>
                <td className="py-2.5 pr-2 text-center align-middle font-mono text-xs text-secondary-text">
                  {it.buyDate}
                </td>
                <td className="py-2.5 pr-2 text-center align-middle">
                  {it.priceSource === '集合竞价' ? (
                    <Badge variant="success">{it.priceSource}</Badge>
                  ) : (
                    <span className="text-xs text-secondary-text">{it.priceSource || '--'}</span>
                  )}
                </td>
                <td className="py-2.5 pr-2 text-right align-middle font-mono text-xs text-foreground tabular-nums">
                  {it.buyPrice != null ? it.buyPrice.toFixed(2) : '--'}
                </td>
                <td className="py-2.5 pr-2 text-right align-middle font-mono text-xs text-secondary-text tabular-nums">
                  {it.auctionPrice != null ? it.auctionPrice.toFixed(2) : '--'}
                </td>
                <td className="py-2.5 pr-2 text-right align-middle font-mono text-xs text-secondary-text tabular-nums">
                  {it.openPrice != null ? it.openPrice.toFixed(2) : '--'}
                </td>
                <td className="py-2.5 pr-2 text-right align-middle"><ReturnCell value={it.return1dPct} /></td>
                <td className="py-2.5 pr-2 text-right align-middle"><ReturnCell value={it.return3dPct} /></td>
                <td className="py-2.5 pr-2 text-right align-middle"><ReturnCell value={it.returnWkPct} /></td>
                <td className="py-2.5 pr-2 text-center align-middle">
                  <KLineChip primary={it.klineJudgment} secondary={it.klineSecondary} />
                </td>
                <td className="py-2.5 pr-3 text-center align-middle">
                  <VolumeChip status={it.volumeStatus} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between gap-2 text-xs text-secondary-text">
        <span>{data.disclaimer}</span>
        <Button variant="ghost" size="sm" onClick={onRefresh} isLoading={loading}>
          <RefreshCw className="h-3.5 w-3.5" />
          重新计算
        </Button>
      </div>
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
  const [data, setData] = useState<RecommendationsResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);

  // Backtest state
  const [activeTab, setActiveTab] = useState<'list' | 'backtest'>('list');
  const [backtestData, setBacktestData] = useState<RecommendationBacktestResponse | null>(null);
  const [backtestLoading, setBacktestLoading] = useState(false);
  const [backtestError, setBacktestError] = useState<ParsedApiError | null>(null);
  /** 用 ref 标记"本组参数下是否已经请求过"，避免请求失败时 setState 触发的死循环 */
  const backtestLoadedKeyRef = useRef<string>('');

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
      const res = await predictionApi.recommendations({
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

  const fetchBacktest = useCallback(async (force = false) => {
    const cacheKey = `${industry || ''}|${topN}`;
    if (!force && backtestLoadedKeyRef.current === cacheKey) {
      return; // 当前参数组合已请求过（无论成功失败），不再自动重试
    }
    backtestLoadedKeyRef.current = cacheKey;
    setBacktestLoading(true);
    setBacktestError(null);
    try {
      const res = await predictionApi.recommendationsBacktest({
        industry: industry || undefined,
        topN,
      });
      setBacktestData(res);
    } catch (err) {
      setBacktestError(getParsedApiError(err));
      setBacktestData(null);
    } finally {
      setBacktestLoading(false);
    }
  }, [industry, topN]);

  // 切换到「收益回测」Tab 时，若当前参数未请求过则触发一次
  useEffect(() => {
    if (activeTab === 'backtest') {
      void fetchBacktest(false);
    }
  }, [activeTab, fetchBacktest]);

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
          {/* Summary */}
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatCard tone="primary" label={text.scope} value={data.scope} hint={`${text.asOf} ${data.asOfDate ?? '--'}`} />
            <StatCard label={text.universe} value={data.universeSize} hint={data.industryCap ? `${text.capLabel} ≤ ${data.industryCap}` : ''} />
            <StatCard tone="success" label={text.picked} value={data.count} hint={text.unit} />
            <StatCard
              label={text.industriesCovered}
              value={new Set(data.items.map((i) => i.industry).filter(Boolean)).size}
            />
          </div>

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

          {/* Tabbed Card: 推荐列表 / 收益回测 */}
          <Card padding="md">
            {/* Tab Bar */}
            <div
              className="-mx-4 mb-4 flex items-center gap-1 border-b border-border/40 px-4"
              role="tablist"
            >
              <button
                type="button"
                role="tab"
                aria-selected={activeTab === 'list'}
                onClick={() => setActiveTab('list')}
                className={`-mb-px inline-flex items-center gap-1.5 border-b-2 px-3 py-2.5 text-sm font-semibold transition-colors ${
                  activeTab === 'list'
                    ? 'border-cyan text-cyan'
                    : 'border-transparent text-secondary-text hover:text-foreground'
                }`}
              >
                <ListChecks className="h-4 w-4" />
                {text.tabList}
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={activeTab === 'backtest'}
                onClick={() => setActiveTab('backtest')}
                className={`-mb-px inline-flex items-center gap-1.5 border-b-2 px-3 py-2.5 text-sm font-semibold transition-colors ${
                  activeTab === 'backtest'
                    ? 'border-cyan text-cyan'
                    : 'border-transparent text-secondary-text hover:text-foreground'
                }`}
              >
                <BarChart3 className="h-4 w-4" />
                {text.tabBacktest}
              </button>
              <div className="ml-auto pb-2 text-xs text-secondary-text">
                {text.listHint}
              </div>
            </div>

            {activeTab === 'list' ? (
              <RankingTable
                items={data.items}
                text={text}
                scoreWidth={scoreWidth}
              />
            ) : (
              <BacktestPanel
                data={backtestData}
                loading={backtestLoading}
                error={backtestError}
                onRefresh={() => void fetchBacktest(true)}
                text={text}
              />
            )}
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
