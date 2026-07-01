import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { TrendingUp, TrendingDown, RefreshCw, CheckCircle2, XCircle, Clock, Target } from 'lucide-react';
import { predictionApi } from '../api/prediction';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import {
  ApiErrorAlert,
  Badge,
  Button,
  Card,
  EmptyState,
  PageHeader,
  Pagination,
  StatCard,
} from '../components/common';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import type {
  PredictionAccuracyResponse,
  PredictionRecordItem,
} from '../types/prediction';

const TEXT = {
  zh: {
    documentTitle: '历史预测与准确率 · DSA',
    eyebrow: '预测复盘',
    title: '历史预测 / 准确率',
    description: '记录每次预测并在窗口到期后用真实行情回填打分，量化模型方向命中率与收益偏差。',
    codePlaceholder: '按股票代码过滤，如 600519',
    filterAll: '全部状态',
    statusPending: '待评估',
    statusEvaluated: '已评估',
    statusInsufficient: '数据不足',
    evaluate: '回填评估',
    evaluating: '评估中…',
    search: '查询',
    reset: '重置',
    total: '预测总数',
    evaluated: '已评估',
    accuracy: '方向命中率',
    pending: '待评估',
    avgExpected: '平均期望收益',
    avgActual: '平均实际收益',
    emptyTitle: '还没有预测记录',
    emptyDesc: '在「走势预测」页做几次预测后，这里会出现记录；等窗口到期后点「回填评估」即可看到命中率。',
    colDate: '预测日',
    colStock: '股票',
    colDir: '方向',
    colProb: '上涨概率',
    colExpected: '期望收益',
    colStatus: '状态',
    colActual: '实际收益',
    colResult: '结果',
    colModel: '模型',
    up: '看涨',
    down: '看跌',
    correct: '命中',
    wrong: '未中',
    evalDone: (n: number) => `本次回填：${n} 条已评估`,
  },
  en: {
    documentTitle: 'Prediction History & Accuracy · DSA',
    eyebrow: 'Prediction review',
    title: 'History / Accuracy',
    description: 'Every prediction is logged and scored against real prices once the window matures, quantifying hit rate and return bias.',
    codePlaceholder: 'Filter by code, e.g. 600519',
    filterAll: 'All status',
    statusPending: 'Pending',
    statusEvaluated: 'Evaluated',
    statusInsufficient: 'Insufficient',
    evaluate: 'Backfill',
    evaluating: 'Evaluating…',
    search: 'Search',
    reset: 'Reset',
    total: 'Total predictions',
    evaluated: 'Evaluated',
    accuracy: 'Direction hit rate',
    pending: 'Pending',
    avgExpected: 'Avg expected return',
    avgActual: 'Avg actual return',
    emptyTitle: 'No predictions yet',
    emptyDesc: 'Make a few predictions on the Prediction page; records show here. Click “Backfill” once the window matures to see the hit rate.',
    colDate: 'As of',
    colStock: 'Stock',
    colDir: 'Direction',
    colProb: 'Up prob',
    colExpected: 'Expected',
    colStatus: 'Status',
    colActual: 'Actual',
    colResult: 'Result',
    colModel: 'Model',
    up: 'Bull',
    down: 'Bear',
    correct: 'Hit',
    wrong: 'Miss',
    evalDone: (n: number) => `Backfilled: ${n} evaluated`,
  },
} as const;

const UP_COLOR = '#22c55e';
const DOWN_COLOR = '#ef4444';
const PAGE_SIZE = 20;

function pct(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return '--';
  return `${(value * 100).toFixed(digits)}%`;
}

function signedPct(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '--';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}%`;
}

const PredictionHistoryPage: React.FC = () => {
  const { language } = useUiLanguage();
  const text = TEXT[language];

  const [codeFilter, setCodeFilter] = useState('');
  const [appliedCode, setAppliedCode] = useState('');
  const [status, setStatus] = useState<'' | 'pending' | 'evaluated' | 'insufficient'>('');
  const [page, setPage] = useState(1);

  const [items, setItems] = useState<PredictionRecordItem[]>([]);
  const [total, setTotal] = useState(0);
  const [accuracy, setAccuracy] = useState<PredictionAccuracyResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [evaluating, setEvaluating] = useState(false);
  const [evalNote, setEvalNote] = useState<string | null>(null);
  const [error, setError] = useState<ParsedApiError | null>(null);

  useEffect(() => {
    document.title = text.documentTitle;
  }, [text.documentTitle]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [history, acc] = await Promise.all([
        predictionApi.history({
          code: appliedCode || undefined,
          status: status || undefined,
          limit: PAGE_SIZE,
          offset: (page - 1) * PAGE_SIZE,
        }),
        predictionApi.accuracy(appliedCode || undefined),
      ]);
      setItems(history.items);
      setTotal(history.total);
      setAccuracy(acc);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setLoading(false);
    }
  }, [appliedCode, status, page]);

  useEffect(() => {
    void load();
  }, [load]);

  const totalPages = useMemo(() => Math.max(1, Math.ceil(total / PAGE_SIZE)), [total]);

  const applySearch = () => {
    setAppliedCode(codeFilter.trim().toUpperCase());
    setPage(1);
  };

  const resetFilters = () => {
    setCodeFilter('');
    setAppliedCode('');
    setStatus('');
    setPage(1);
  };

  const runEvaluate = async () => {
    if (evaluating) return;
    setEvaluating(true);
    setEvalNote(null);
    setError(null);
    try {
      const res = await predictionApi.evaluate(true, 500);
      setEvalNote(text.evalDone(res.evaluated));
      await load();
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setEvaluating(false);
    }
  };

  const statusBadge = (s: string) => {
    if (s === 'evaluated') return <Badge variant="success">{text.statusEvaluated}</Badge>;
    if (s === 'insufficient') return <Badge variant="warning">{text.statusInsufficient}</Badge>;
    return <Badge variant="default">{text.statusPending}</Badge>;
  };

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5 px-3 py-4 sm:px-4">
      <PageHeader eyebrow={text.eyebrow} title={text.title} description={text.description} />

      {/* Accuracy summary */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard tone="primary" label={text.total} value={accuracy?.total ?? '--'} hint={`${text.evaluated} ${accuracy?.evaluated ?? 0}`} />
        <StatCard
          tone="success"
          label={text.accuracy}
          value={accuracy?.accuracy != null ? pct(accuracy.accuracy) : '--'}
          hint={`${accuracy?.correct ?? 0}/${accuracy?.evaluated ?? 0}`}
        />
        <StatCard label={text.pending} value={accuracy?.pending ?? '--'} hint={<Clock className="inline h-3 w-3" />} />
        <StatCard
          label={text.avgActual}
          value={<span style={{ color: (accuracy?.avgActualReturnPct ?? 0) >= 0 ? UP_COLOR : DOWN_COLOR }}>{signedPct(accuracy?.avgActualReturnPct)}</span>}
          hint={`${text.avgExpected} ${signedPct(accuracy?.avgExpectedReturnPct)}`}
        />
      </div>

      {/* Filters + evaluate */}
      <Card padding="md">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <input
            value={codeFilter}
            onChange={(e) => setCodeFilter(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') applySearch(); }}
            placeholder={text.codePlaceholder}
            className="input-surface input-focus-glow h-11 flex-1 rounded-xl border bg-transparent px-3 text-sm"
          />
          <select
            value={status}
            onChange={(e) => { setStatus(e.target.value as typeof status); setPage(1); }}
            className="input-surface input-focus-glow h-11 rounded-xl border bg-transparent px-3 text-sm lg:w-40"
          >
            <option value="">{text.filterAll}</option>
            <option value="pending">{text.statusPending}</option>
            <option value="evaluated">{text.statusEvaluated}</option>
            <option value="insufficient">{text.statusInsufficient}</option>
          </select>
          <div className="flex gap-2">
            <Button variant="secondary" onClick={applySearch}>{text.search}</Button>
            <Button variant="ghost" onClick={resetFilters}>{text.reset}</Button>
            <Button variant="primary" isLoading={evaluating} loadingText={text.evaluating} onClick={() => void runEvaluate()}>
              <RefreshCw className="h-4 w-4" />
              {text.evaluate}
            </Button>
          </div>
        </div>
        {evalNote ? <p className="mt-2 text-xs text-success">{evalNote}</p> : null}
      </Card>

      {error ? <ApiErrorAlert error={error} /> : null}

      {!loading && items.length === 0 && !error ? (
        <EmptyState title={text.emptyTitle} description={text.emptyDesc} className="border-dashed" />
      ) : null}

      {items.length > 0 ? (
        <Card padding="sm">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[860px] text-sm">
              <thead>
                <tr className="border-b border-border/60 text-left text-xs uppercase tracking-wider text-secondary-text">
                  <th className="px-3 py-2.5">{text.colDate}</th>
                  <th className="px-3 py-2.5">{text.colStock}</th>
                  <th className="px-3 py-2.5">{text.colDir}</th>
                  <th className="px-3 py-2.5 text-right">{text.colProb}</th>
                  <th className="px-3 py-2.5 text-right">{text.colExpected}</th>
                  <th className="px-3 py-2.5">{text.colStatus}</th>
                  <th className="px-3 py-2.5 text-right">{text.colActual}</th>
                  <th className="px-3 py-2.5 text-center">{text.colResult}</th>
                  <th className="px-3 py-2.5">{text.colModel}</th>
                </tr>
              </thead>
              <tbody>
                {items.map((r) => {
                  const isUp = r.direction === 'up';
                  return (
                    <tr key={r.id} className="border-b border-border/30 transition-colors hover:bg-hover/40">
                      <td className="whitespace-nowrap px-3 py-2.5 font-mono text-xs text-secondary-text">{r.asOfDate}</td>
                      <td className="whitespace-nowrap px-3 py-2.5">
                        <span className="font-medium text-foreground">{r.code}</span>
                        {r.stockName ? <span className="ml-1 text-xs text-muted-text">{r.stockName}</span> : null}
                      </td>
                      <td className="px-3 py-2.5">
                        <span className="flex items-center gap-1" style={{ color: isUp ? UP_COLOR : DOWN_COLOR }}>
                          {isUp ? <TrendingUp className="h-4 w-4" /> : <TrendingDown className="h-4 w-4" />}
                          {isUp ? text.up : text.down}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono text-xs">{pct(r.upProbability)}</td>
                      <td className="px-3 py-2.5 text-right font-mono text-xs" style={{ color: (r.expectedReturnPct ?? 0) >= 0 ? UP_COLOR : DOWN_COLOR }}>
                        {signedPct(r.expectedReturnPct)}
                      </td>
                      <td className="px-3 py-2.5">{statusBadge(r.evalStatus)}</td>
                      <td className="px-3 py-2.5 text-right font-mono text-xs" style={{ color: (r.actualReturnPct ?? 0) >= 0 ? UP_COLOR : DOWN_COLOR }}>
                        {r.actualReturnPct != null ? signedPct(r.actualReturnPct) : '--'}
                      </td>
                      <td className="px-3 py-2.5 text-center">
                        {r.isCorrect == null ? (
                          <span className="text-muted-text">--</span>
                        ) : r.isCorrect ? (
                          <span className="inline-flex items-center gap-1 text-success" title={text.correct}><CheckCircle2 className="h-4 w-4" /></span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-danger" title={text.wrong}><XCircle className="h-4 w-4" /></span>
                        )}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2.5">
                        {r.modelSource === 'trained' ? (
                          <Badge variant="info"><Target className="mr-1 inline h-3 w-3" />{r.modelVersion ?? 'trained'}</Badge>
                        ) : (
                          <Badge variant="default">on-the-fly</Badge>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      ) : null}

      {totalPages > 1 ? (
        <Pagination currentPage={page} totalPages={totalPages} onPageChange={setPage} />
      ) : null}
    </div>
  );
};

export default PredictionHistoryPage;
