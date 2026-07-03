import type React from 'react';
import { useEffect, useMemo, useState } from 'react';
import { TrendingUp, TrendingDown, Sparkles, Info } from 'lucide-react';
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
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
import type { PredictionResponse } from '../types/prediction';

// ============ Localized text ============

const TEXT = {
  zh: {
    documentTitle: '走势预测 · DSA',
    eyebrow: 'AI 走势预测',
    title: '股价走势预测',
    description: '拉取历史 K 线，构造技术因子并训练轻量逻辑回归模型，预测次日涨跌方向与未来价格路径。',
    codePlaceholder: '输入股票代码或名称，如 600519 / 00700 / AAPL',
    horizon: '预测天数',
    lookback: '训练回溯',
    days: '天',
    run: '开始预测',
    running: '模型训练中…',
    emptyTitle: '还没有预测结果',
    emptyDesc: '在上方输入一只股票并点击「开始预测」，模型会实时训练并给出走势判断。',
    direction: '方向判断',
    up: '看涨',
    down: '看跌',
    upProb: '上涨概率',
    confidence: '置信度',
    expectedReturn: '区间期望收益',
    lastClose: '最新收盘',
    asOf: '数据截至',
    horizonReturn: (d: number) => `未来 ${d} 交易日`,
    chartTitle: '价格走势与预测路径',
    histLegend: '历史收盘',
    projLegend: '预测中枢',
    bandLegend: '波动区间',
    factorsTitle: '因子贡献（模型为什么这么判断）',
    factorHint: '正值推动上涨，负值推动下跌，按影响力排序',
    metricsTitle: '模型评估',
    trainAcc: '训练准确率',
    validAcc: '验证准确率',
    baseline: '基线（瞎猜）',
    samples: '样本数',
    algorithm: '算法',
    contribution: '贡献',
    weight: '权重',
    value: '因子值',
    disclaimerTitle: '风险提示',
    sourceTrained: '离线训练模型',
    sourceOnTheFly: '实时训练',
    modelVersion: '版本',
    trainedAt: '训练于',
  },
  en: {
    documentTitle: 'Trend Prediction · DSA',
    eyebrow: 'AI trend prediction',
    title: 'Stock Trend Prediction',
    description: 'Fetch historical candles, build technical factors and train a lightweight logistic model to predict next-day direction and a future price path.',
    codePlaceholder: 'Enter a stock code or name, e.g. 600519 / 00700 / AAPL',
    horizon: 'Horizon',
    lookback: 'Lookback',
    days: 'd',
    run: 'Predict',
    running: 'Training model…',
    emptyTitle: 'No prediction yet',
    emptyDesc: 'Enter a stock above and click “Predict”. The model trains on the fly and returns a trend outlook.',
    direction: 'Direction',
    up: 'Bullish',
    down: 'Bearish',
    upProb: 'Up probability',
    confidence: 'Confidence',
    expectedReturn: 'Expected return',
    lastClose: 'Last close',
    asOf: 'As of',
    horizonReturn: (d: number) => `Next ${d} trading days`,
    chartTitle: 'Price history & projected path',
    histLegend: 'History close',
    projLegend: 'Projected',
    bandLegend: 'Volatility band',
    factorsTitle: 'Factor contributions (why the model decided)',
    factorHint: 'Positive pushes up, negative pushes down, sorted by impact',
    metricsTitle: 'Model evaluation',
    trainAcc: 'Train accuracy',
    validAcc: 'Valid accuracy',
    baseline: 'Baseline (guess)',
    samples: 'Samples',
    algorithm: 'Algorithm',
    contribution: 'Contribution',
    weight: 'Weight',
    value: 'Value',
    disclaimerTitle: 'Disclaimer',
    sourceTrained: 'Offline-trained model',
    sourceOnTheFly: 'Trained live',
    modelVersion: 'Version',
    trainedAt: 'Trained at',
  },
} as const;

// Map raw backend algorithm ids to friendly display names.
const ALGORITHM_LABELS: Record<string, string> = {
  lightgbm_gbdt: 'LightGBM',
  logistic_regression_gd: 'Logistic Regression',
};

function algorithmLabel(algorithm: string): string {
  return ALGORITHM_LABELS[algorithm] ?? algorithm;
}

function formatTrainedAt(value: string | null | undefined): string | null {
  if (!value) return null;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value).slice(0, 19).replace('T', ' ');
  return parsed.toLocaleString();
}

// ============ Helpers ============

const UP_COLOR = '#22c55e';
const DOWN_COLOR = '#ef4444';
const HIST_COLOR = '#38bdf8';

function pct(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return '--';
  return `${(value * 100).toFixed(digits)}%`;
}

function signedPct(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '--';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}%`;
}

type ChartRow = {
  date: string;
  close?: number;
  price?: number;
  /** [lower, upper] range tuple for the native recharts band area. */
  range?: [number, number];
};

function buildChartData(result: PredictionResponse): ChartRow[] {
  const rows: ChartRow[] = result.history.map((h) => ({ date: h.date, close: h.close }));
  // Bridge point so the projection line/band connects to the last history close.
  if (rows.length > 0) {
    const last = rows[rows.length - 1];
    last.price = last.close;
    last.range = [last.close as number, last.close as number];
  }
  result.projected.forEach((p) => {
    rows.push({ date: p.date, price: p.price, range: [p.lower, p.upper] });
  });
  return rows;
}

// ============ Main Page ============

const PredictionPage: React.FC = () => {
  const { language } = useUiLanguage();
  const text = TEXT[language];

  const [code, setCode] = useState('');
  const [horizon, setHorizon] = useState(5);
  const [lookback, setLookback] = useState(250);
  const [isRunning, setIsRunning] = useState(false);
  const [result, setResult] = useState<PredictionResponse | null>(null);
  const [error, setError] = useState<ParsedApiError | null>(null);

  useEffect(() => {
    document.title = text.documentTitle;
  }, [text.documentTitle]);

  const runPrediction = async (submitCode?: string) => {
    const target = (submitCode ?? code).trim();
    if (!target || isRunning) return;
    setIsRunning(true);
    setError(null);
    try {
      const response = await predictionApi.predict({
        code: target,
        horizonDays: horizon,
        lookbackDays: lookback,
        language,
      });
      setResult(response);
    } catch (err) {
      setError(getParsedApiError(err));
      setResult(null);
    } finally {
      setIsRunning(false);
    }
  };

  const chartData = useMemo(() => (result ? buildChartData(result) : []), [result]);
  const dirColor = result?.direction === 'up' ? UP_COLOR : DOWN_COLOR;
  const maxAbsContribution = useMemo(() => {
    if (!result) return 1;
    return Math.max(...result.factors.map((f) => Math.abs(f.contribution)), 0.0001);
  }, [result]);

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5 px-3 py-4 sm:px-4">
      <PageHeader eyebrow={text.eyebrow} title={text.title} description={text.description} />

      {/* Controls */}
      <Card padding="md">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
          <div className="min-w-0 flex-1">
            <label className="mb-1.5 block text-xs uppercase tracking-[0.22em] text-secondary-text">
              {text.eyebrow}
            </label>
            <StockAutocomplete
              value={code}
              onChange={setCode}
              onSubmit={(submitted) => {
                setCode(submitted);
                void runPrediction(submitted);
              }}
              disabled={isRunning}
              placeholder={text.codePlaceholder}
            />
          </div>
          <div>
            <label className="mb-1.5 block text-xs uppercase tracking-[0.22em] text-secondary-text">
              {text.horizon}
            </label>
            <select
              value={horizon}
              onChange={(e) => setHorizon(Number(e.target.value))}
              disabled={isRunning}
              className="input-surface input-focus-glow h-11 w-full rounded-xl border bg-transparent px-3 text-sm lg:w-28"
            >
              {[3, 5, 10, 15, 20].map((d) => (
                <option key={d} value={d}>{d} {text.days}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1.5 block text-xs uppercase tracking-[0.22em] text-secondary-text">
              {text.lookback}
            </label>
            <select
              value={lookback}
              onChange={(e) => setLookback(Number(e.target.value))}
              disabled={isRunning}
              className="input-surface input-focus-glow h-11 w-full rounded-xl border bg-transparent px-3 text-sm lg:w-32"
            >
              {[120, 250, 500, 800].map((d) => (
                <option key={d} value={d}>{d} {text.days}</option>
              ))}
            </select>
          </div>
          <Button
            variant="primary"
            size="lg"
            isLoading={isRunning}
            loadingText={text.running}
            onClick={() => void runPrediction()}
            disabled={!code.trim()}
            className="lg:w-36"
          >
            <Sparkles className="h-4 w-4" />
            {text.run}
          </Button>
        </div>
      </Card>

      {error ? <ApiErrorAlert error={error} /> : null}

      {!result && !error ? (
        <EmptyState title={text.emptyTitle} description={text.emptyDesc} className="border-dashed" />
      ) : null}

      {result ? (
        <div className="flex flex-col gap-5 animate-fade-in">
          {/* Summary stats */}
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatCard
              tone={result.direction === 'up' ? 'success' : 'danger'}
              label={text.direction}
              value={(
                <span className="flex items-center gap-2" style={{ color: dirColor }}>
                  {result.direction === 'up' ? <TrendingUp className="h-6 w-6" /> : <TrendingDown className="h-6 w-6" />}
                  {result.direction === 'up' ? text.up : text.down}
                </span>
              )}
              hint={`${result.stockCode}${result.stockName ? ` · ${result.stockName}` : ''}`}
            />
            <StatCard
              tone="primary"
              label={text.upProb}
              value={pct(result.upProbability)}
              hint={`${text.confidence} ${pct(result.confidence)}`}
            />
            <StatCard
              tone={result.expectedReturnPct >= 0 ? 'success' : 'danger'}
              label={text.expectedReturn}
              value={<span style={{ color: result.expectedReturnPct >= 0 ? UP_COLOR : DOWN_COLOR }}>{signedPct(result.expectedReturnPct)}</span>}
              hint={text.horizonReturn(result.horizonDays)}
            />
            <StatCard
              label={text.lastClose}
              value={result.lastClose.toFixed(2)}
              hint={`${text.asOf} ${result.asOfDate}`}
            />
          </div>

          {/* Chart */}
          <Card title={text.chartTitle} padding="md">
            <div className="h-80 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={chartData} margin={{ top: 10, right: 12, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="predBand" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={dirColor} stopOpacity={0.28} />
                      <stop offset="100%" stopColor={dirColor} stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.15)" />
                  <XAxis dataKey="date" tick={{ fontSize: 11, fill: 'rgb(148,163,184)' }} minTickGap={28} />
                  <YAxis
                    domain={['auto', 'auto']}
                    tick={{ fontSize: 11, fill: 'rgb(148,163,184)' }}
                    width={52}
                    tickFormatter={(v: number) => v.toFixed(1)}
                  />
                  <ReTooltip
                    contentStyle={{
                      background: 'rgba(17,24,39,0.95)',
                      border: '1px solid rgba(148,163,184,0.25)',
                      borderRadius: 12,
                      fontSize: 12,
                      color: '#e2e8f0',
                    }}
                    formatter={(value, name) => {
                      if (Array.isArray(value)) {
                        const [lo, hi] = value as number[];
                        return [`${lo?.toFixed?.(2)} ~ ${hi?.toFixed?.(2)}`, name];
                      }
                      const num = typeof value === 'number' ? value : Number(value);
                      return [Number.isFinite(num) ? num.toFixed(2) : String(value), name];
                    }}
                  />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Area
                    type="monotone"
                    dataKey="range"
                    stroke="none"
                    fill="url(#predBand)"
                    name={text.bandLegend}
                    connectNulls
                    isAnimationActive={false}
                  />
                  <Line
                    type="monotone"
                    dataKey="close"
                    stroke={HIST_COLOR}
                    strokeWidth={2}
                    dot={false}
                    name={text.histLegend}
                    connectNulls
                  />
                  <Line
                    type="monotone"
                    dataKey="price"
                    stroke={dirColor}
                    strokeWidth={2.4}
                    strokeDasharray="5 4"
                    dot={{ r: 2.5, fill: dirColor }}
                    name={text.projLegend}
                    connectNulls
                  />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </Card>

          <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
            {/* Factor contributions */}
            <Card title={text.factorsTitle} subtitle={text.factorHint} padding="md">
              <div className="mt-2 flex flex-col gap-3">
                {result.factors.map((f) => {
                  const positive = f.contribution >= 0;
                  const widthPct = Math.min(100, (Math.abs(f.contribution) / maxAbsContribution) * 100);
                  return (
                    <div key={f.key} className="flex flex-col gap-1">
                      <div className="flex items-center justify-between text-sm">
                        <span className="text-foreground">{f.label}</span>
                        <span className="font-mono text-xs" style={{ color: positive ? UP_COLOR : DOWN_COLOR }}>
                          {positive ? '+' : ''}{f.contribution.toFixed(3)}
                        </span>
                      </div>
                      <div className="relative h-2 w-full overflow-hidden rounded-full bg-elevated/60">
                        <div
                          className="absolute top-0 h-full rounded-full"
                          style={{
                            width: `${widthPct}%`,
                            left: positive ? '50%' : undefined,
                            right: positive ? undefined : '50%',
                            background: positive ? UP_COLOR : DOWN_COLOR,
                            opacity: 0.7,
                          }}
                        />
                        <div className="absolute left-1/2 top-0 h-full w-px bg-border/70" />
                      </div>
                    </div>
                  );
                })}
              </div>
            </Card>

            {/* Model evaluation */}
            <Card title={text.metricsTitle} padding="md">
              <div className="mt-2 grid grid-cols-2 gap-3">
                <StatCard tone="primary" label={text.trainAcc} value={pct(result.metrics.trainAccuracy)} />
                <StatCard tone="success" label={text.validAcc} value={pct(result.metrics.validAccuracy)} />
                <StatCard label={text.baseline} value={pct(result.metrics.baselineAccuracy)} />
                <StatCard
                  label={text.samples}
                  value={result.model.trainedSamples}
                  hint={`${result.metrics.trainSamples} / ${result.metrics.validSamples}`}
                />
              </div>
              <div className="mt-4 flex flex-wrap items-center gap-2">
                <Badge variant="info">{text.algorithm}: {algorithmLabel(result.model.algorithm)}</Badge>
                {result.model.source ? (
                  <Badge variant={result.model.source === 'trained' ? 'success' : 'default'}>
                    {result.model.source === 'trained' ? text.sourceTrained : text.sourceOnTheFly}
                    {result.model.source === 'trained' && result.model.version
                      ? ` · ${text.modelVersion} ${result.model.version}`
                      : ''}
                  </Badge>
                ) : null}
                <Badge variant="default">features: {result.model.featureCount}</Badge>
                {result.metrics.epochs > 0 ? (
                  <Badge variant="default">epochs: {result.metrics.epochs}</Badge>
                ) : null}
                {result.model.source === 'trained' && formatTrainedAt(result.model.trainedAt) ? (
                  <Badge variant="default">{text.trainedAt} {formatTrainedAt(result.model.trainedAt)}</Badge>
                ) : null}
              </div>
            </Card>
          </div>

          {/* Disclaimer */}
          <div className="flex items-start gap-2 rounded-xl border border-warning/25 bg-warning/5 px-4 py-3 text-sm text-secondary-text">
            <Info className="mt-0.5 h-4 w-4 flex-shrink-0 text-warning" />
            <span>
              <span className="font-medium text-warning">{text.disclaimerTitle}：</span>
              {result.disclaimer}
            </span>
          </div>
        </div>
      ) : null}
    </div>
  );
};

export default PredictionPage;
