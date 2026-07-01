import type React from 'react';
import { useEffect, useMemo, useState } from 'react';
import { FlaskConical, TrendingUp, TrendingDown, Info } from 'lucide-react';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip as ReTooltip,
  XAxis,
  YAxis,
} from 'recharts';
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
import { StockAutocomplete } from '../components/StockAutocomplete';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import type { PredictionBacktestResponse } from '../types/prediction';

const TEXT = {
  zh: {
    documentTitle: '走势预测回测 · DSA',
    eyebrow: '预测回测',
    title: '走势预测回测',
    description:
      '滚动步进(walk-forward)：每隔若干交易日仅用「当时能看到的历史」重训并预测方向，严格防未来函数，检验模型历史命中率与照做的资金曲线。',
    codeLabel: '股票代码',
    horizon: '预测天数',
    lookback: '回溯天数',
    retrain: '重训间隔(天)',
    threshold: '看涨阈值',
    allowShort: '允许做空',
    refresh: '联网刷新',
    run: '开始回测',
    running: '回测中…',
    hintTitle: '怎么读这份回测',
    hint:
      '命中率 > 基线 才说明模型的方向判断有效；策略收益需跑赢「买入持有」才有超额价值。样本越多结论越可信。',
    accuracy: '方向命中率',
    baseline: '基线(猜多数)',
    upPrecision: '看涨精确率',
    predictions: '逐日预测数',
    trades: '交易笔数',
    winRate: '交易胜率',
    strategyReturn: '策略收益',
    benchmarkReturn: '买入持有',
    maxDrawdown: '最大回撤',
    equityTitle: '资金曲线（策略 vs 买入持有）',
    strategy: '策略',
    benchmark: '买入持有',
    verdictWin: '模型方向判断跑赢基线',
    verdictLose: '模型方向判断未跑赢基线（弱于始终猜多数类）',
    range: '评估区间',
    emptyTitle: '输入股票代码后开始回测',
    emptyDesc: '默认使用本地缓存数据，命中即零联网；数据不足时可提高回溯天数或勾选联网刷新。',
    disclaimer: '⚠️ 回测基于历史数据，过往表现不代表未来收益，不构成任何投资建议。',
  },
  en: {
    documentTitle: 'Prediction Backtest · DSA',
    eyebrow: 'Prediction backtest',
    title: 'Trend Prediction Backtest',
    description:
      'Walk-forward: every few trading days the model is retrained using only data visible at that time, then predicts direction—strictly leak-free—to measure historical hit rate and the equity curve of acting on it.',
    codeLabel: 'Stock code',
    horizon: 'Horizon (d)',
    lookback: 'Lookback (d)',
    retrain: 'Retrain every (d)',
    threshold: 'Up threshold',
    allowShort: 'Allow short',
    refresh: 'Refresh online',
    run: 'Run backtest',
    running: 'Running…',
    hintTitle: 'How to read this',
    hint:
      'Accuracy above baseline means the direction call adds value; the strategy must beat buy & hold to show alpha. More samples = more reliable.',
    accuracy: 'Direction hit rate',
    baseline: 'Baseline (majority)',
    upPrecision: 'Up precision',
    predictions: 'Daily predictions',
    trades: 'Trades',
    winRate: 'Trade win rate',
    strategyReturn: 'Strategy return',
    benchmarkReturn: 'Buy & hold',
    maxDrawdown: 'Max drawdown',
    equityTitle: 'Equity curve (strategy vs buy & hold)',
    strategy: 'Strategy',
    benchmark: 'Buy & hold',
    verdictWin: 'Direction call beats the baseline',
    verdictLose: 'Direction call does not beat the baseline (worse than always guessing the majority)',
    range: 'Evaluation range',
    emptyTitle: 'Enter a stock code to backtest',
    emptyDesc: 'Uses local cache by default (zero network on hit); if data is thin, raise the lookback or enable online refresh.',
    disclaimer: '⚠️ Backtest uses historical data. Past performance does not indicate future results and is not investment advice.',
  },
} as const;

const UP_COLOR = '#22c55e';
const DOWN_COLOR = '#ef4444';

function pct(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return '--';
  return `${(value * 100).toFixed(digits)}%`;
}

function signedPct(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '--';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}%`;
}

const PredictionBacktestPage: React.FC = () => {
  const { language } = useUiLanguage();
  const text = TEXT[language];

  const [code, setCode] = useState('');
  const [horizon, setHorizon] = useState(5);
  const [lookback, setLookback] = useState(500);
  const [retrainEvery, setRetrainEvery] = useState(5);
  const [threshold, setThreshold] = useState(0.5);
  const [allowShort, setAllowShort] = useState(false);
  const [refresh, setRefresh] = useState(true);

  const [result, setResult] = useState<PredictionBacktestResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);

  useEffect(() => {
    document.title = text.documentTitle;
  }, [text.documentTitle]);

  const run = async () => {
    if (!code.trim() || loading) return;
    setLoading(true);
    setError(null);
    try {
      const res = await predictionApi.backtest({
        code: code.trim(),
        horizonDays: horizon,
        lookbackDays: lookback,
        retrainEvery,
        threshold,
        allowShort,
        refresh,
      });
      setResult(res);
    } catch (err) {
      setError(getParsedApiError(err));
      setResult(null);
    } finally {
      setLoading(false);
    }
  };

  const beatBaseline = result != null && result.accuracy > result.baselineAccuracy;
  const equityData = useMemo(() => result?.equityCurve ?? [], [result]);

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5 px-3 py-4 sm:px-4">
      <PageHeader eyebrow={text.eyebrow} title={text.title} description={text.description} />

      {/* Controls */}
      <Card padding="md">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-4">
          <div className="lg:col-span-2">
            <label className="mb-1 block text-xs text-secondary-text">{text.codeLabel}</label>
            <StockAutocomplete value={code} onChange={setCode} onSubmit={() => void run()} />
          </div>
          <div>
            <label className="mb-1 block text-xs text-secondary-text">{text.horizon}</label>
            <input
              type="number" min={1} max={20} value={horizon}
              onChange={(e) => setHorizon(Number(e.target.value))}
              className="input-surface input-focus-glow h-11 w-full rounded-xl border bg-transparent px-3 text-sm"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-secondary-text">{text.lookback}</label>
            <input
              type="number" min={120} max={1500} step={50} value={lookback}
              onChange={(e) => setLookback(Number(e.target.value))}
              className="input-surface input-focus-glow h-11 w-full rounded-xl border bg-transparent px-3 text-sm"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-secondary-text">{text.retrain}</label>
            <input
              type="number" min={1} max={60} value={retrainEvery}
              onChange={(e) => setRetrainEvery(Number(e.target.value))}
              className="input-surface input-focus-glow h-11 w-full rounded-xl border bg-transparent px-3 text-sm"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-secondary-text">{text.threshold}</label>
            <input
              type="number" min={0.05} max={0.95} step={0.05} value={threshold}
              onChange={(e) => setThreshold(Number(e.target.value))}
              className="input-surface input-focus-glow h-11 w-full rounded-xl border bg-transparent px-3 text-sm"
            />
          </div>
          <div className="flex items-end gap-4">
            <label className="flex items-center gap-2 text-sm text-secondary-text">
              <input type="checkbox" checked={allowShort} onChange={(e) => setAllowShort(e.target.checked)} />
              {text.allowShort}
            </label>
            <label className="flex items-center gap-2 text-sm text-secondary-text">
              <input type="checkbox" checked={refresh} onChange={(e) => setRefresh(e.target.checked)} />
              {text.refresh}
            </label>
          </div>
          <div className="flex items-end">
            <Button variant="primary" isLoading={loading} loadingText={text.running} onClick={() => void run()} className="w-full">
              <FlaskConical className="h-4 w-4" />
              {text.run}
            </Button>
          </div>
        </div>
      </Card>

      {error ? <ApiErrorAlert error={error} /> : null}

      {!result && !error ? (
        <EmptyState title={text.emptyTitle} description={text.emptyDesc} className="border-dashed" />
      ) : null}

      {result ? (
        <>
          {/* Verdict */}
          <Card padding="md">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="font-medium text-foreground">
                  {result.stockCode}{result.stockName ? ` · ${result.stockName}` : ''}
                </span>
                <Badge variant="default">{text.range}: {result.startDate} ~ {result.endDate}</Badge>
              </div>
              <Badge variant={beatBaseline ? 'success' : 'warning'}>
                {beatBaseline ? text.verdictWin : text.verdictLose}
              </Badge>
            </div>
          </Card>

          {/* Stat cards */}
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatCard
              tone={beatBaseline ? 'success' : 'primary'}
              label={text.accuracy}
              value={pct(result.accuracy)}
              hint={`${text.baseline} ${pct(result.baselineAccuracy)}`}
            />
            <StatCard label={text.upPrecision} value={pct(result.upPrecision)} hint={`${text.predictions} ${result.nPredictions}`} />
            <StatCard
              label={text.strategyReturn}
              value={<span style={{ color: result.strategyReturnPct >= 0 ? UP_COLOR : DOWN_COLOR }}>{signedPct(result.strategyReturnPct)}</span>}
              hint={`${text.benchmarkReturn} ${signedPct(result.benchmarkReturnPct)}`}
            />
            <StatCard
              label={text.winRate}
              value={pct(result.winRate)}
              hint={`${text.trades} ${result.nTrades} · ${text.maxDrawdown} ${result.maxDrawdownPct}%`}
            />
          </div>

          {/* Equity curve */}
          {equityData.length > 1 ? (
            <Card padding="md">
              <p className="mb-3 text-sm font-medium text-foreground">{text.equityTitle}</p>
              <div className="h-72 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={equityData} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" opacity={0.4} />
                    <XAxis dataKey="date" tick={{ fontSize: 11 }} minTickGap={32} />
                    <YAxis tick={{ fontSize: 11 }} width={48} domain={['auto', 'auto']} tickFormatter={(v: number) => v.toFixed(2)} />
                    <ReTooltip
                      contentStyle={{ background: 'hsl(var(--card))', border: '1px solid hsl(var(--border))', borderRadius: 12, fontSize: 12 }}
                      formatter={(value) => (typeof value === 'number' ? value.toFixed(3) : String(value))}
                    />
                    <Legend wrapperStyle={{ fontSize: 12 }} />
                    <Line type="monotone" dataKey="strategy" name={text.strategy} stroke={UP_COLOR} dot={false} strokeWidth={2} />
                    <Line type="monotone" dataKey="benchmark" name={text.benchmark} stroke="#94a3b8" dot={false} strokeWidth={1.5} strokeDasharray="4 3" />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </Card>
          ) : null}

          {/* Hint */}
          <Card padding="md">
            <div className="flex items-start gap-2 text-sm text-secondary-text">
              <Info className="mt-0.5 h-4 w-4 shrink-0 text-info" />
              <div>
                <p className="mb-1 font-medium text-foreground">{text.hintTitle}</p>
                <p>{text.hint}</p>
                <p className="mt-2 flex items-center gap-1 text-xs text-muted-text">
                  {result.strategyReturnPct >= 0 ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
                  {text.disclaimer}
                </p>
              </div>
            </div>
          </Card>
        </>
      ) : null}
    </div>
  );
};

export default PredictionBacktestPage;
