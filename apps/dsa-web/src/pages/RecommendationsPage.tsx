import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Info, RefreshCw, Sparkles, TrendingUp, Trophy } from 'lucide-react';
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
} from '../types/prediction';

// ============ Localized text ============

const TEXT = {
  zh: {
    documentTitle: '选股推荐 · DSA',
    eyebrow: 'AI 横截面选股',
    title: '选股推荐',
    description: '系统每日扫描全市场给每只票打「强弱分」，主动挑出最强的一篮子。经 walk-forward 回测：双周调仓·概率加权·行业分散的口径风险调整后最优。',
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
    listHint: '强弱分为横截面相对排序（越高越强），建议权重为概率加权（清单内∑=100%）',
    colRank: '#',
    colName: '股票',
    colIndustry: '行业',
    colScore: '强弱分',
    colWeight: '建议权重',
    colClose: '最新价',
    unit: '只',
    disclaimerTitle: '风险提示',
  },
  en: {
    documentTitle: 'Stock Picks · DSA',
    eyebrow: 'AI cross-sectional picks',
    title: 'Stock Recommendations',
    description: 'The system scans the whole market daily and scores every stock, actively surfacing the strongest basket. Walk-forward tested: biweekly rebalance + probability weighting + industry diversification is the best risk-adjusted setup.',
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
    listHint: 'Strength is a cross-sectional relative rank (higher = stronger); suggested weight is probability-weighted (sums to 100%)',
    colRank: '#',
    colName: 'Stock',
    colIndustry: 'Industry',
    colScore: 'Strength',
    colWeight: 'Weight',
    colClose: 'Close',
    unit: '',
    disclaimerTitle: 'Disclaimer',
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

          {/* Ranking table */}
          <Card title={text.listTitle} subtitle={text.listHint} padding="md">
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
                  {data.items.map((it: RecommendationItem) => (
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
