// Stock prediction / recommendation types (选股推荐 + 预测回测)

export interface PredictionBacktestRequest {
  code: string;
  horizonDays?: number;
  lookbackDays?: number;
  retrainEvery?: number;
  minTrain?: number;
  threshold?: number;
  allowShort?: boolean;
  refresh?: boolean;
  startDate?: string;
  endDate?: string;
}

export interface BacktestPoint {
  date: string;
  upProbability: number;
  direction: 'up' | 'down';
  actualReturnPct: number;
  correct: boolean;
}

export interface EquityPoint {
  date: string;
  strategy: number;
  benchmark: number;
}

export interface PredictionBacktestResponse {
  stockCode: string;
  stockName: string | null;
  horizonDays: number;
  lookbackDays: number;
  retrainEvery: number;
  threshold: number;
  allowShort: boolean;
  startDate: string;
  endDate: string;
  nPredictions: number;
  correct: number;
  accuracy: number;
  baselineAccuracy: number;
  upPrecision: number | null;
  predUpCount: number;
  actualUpRatio: number;
  nTrades: number;
  winRate: number | null;
  strategyReturnPct: number;
  benchmarkReturnPct: number;
  maxDrawdownPct: number;
  equityCurve: EquityPoint[];
  points: BacktestPoint[];
  disclaimer: string;
}

// ============ Stock recommendations (cross-sectional strength board) ============

export interface RecommendationItem {
  code: string;
  stockName: string | null;
  industry: string | null;
  strengthScore: number;
  rank: number;
  rankInIndustry?: number | null;
  rankPct: number;
  suggestedWeight: number;
  lastClose: number | null;
}

export interface StrategyHint {
  name: string;
  rebalance: string;
  weighting: string;
  industryCap: number | null;
  backtest: string | null;
}

/** 一次强弱打分快照执行（不可变，永不覆盖），供前端「快照选择」下拉。 */
export interface SnapshotRun {
  runId: number;
  modelId?: number | null;
  modelName: string;
  modelVersion: string;
  asOfDate: string | null;
  generatedAt: string | null;
  lookbackDays?: number | null;
  universeSize?: number | null;
  industryCount?: number | null;
  note?: string | null;
}

export interface RecommendationsResponse {
  runId: number;
  modelId?: number | null;
  modelName: string;
  modelVersion: string;
  generatedAt: string | null;
  scope: string;
  industry: string | null;
  asOfDate: string | null;
  universeSize: number;
  count: number;
  industryCap: number | null;
  strategy: StrategyHint | null;
  items: RecommendationItem[];
  disclaimer: string;
}

export interface RecommendationRunsResponse {
  count: number;
  runs: SnapshotRun[];
}

export interface IndustryOption {
  industry: string;
  count: number;
}

export interface IndustriesResponse {
  runId: number | null;
  asOfDate: string | null;
  count: number;
  industries: IndustryOption[];
}

export interface RecommendationsParams {
  industry?: string;
  topN?: number;
  runId?: number | null;
}

// ============ Recommendation backtest (Mon open buy) ============

export interface BacktestStockItem {
  code: string;
  stockName: string | null;
  industry: string | null;
  strengthScore: number;
  rank: number;
  buyDate: string;
  priceSource: string;
  buyPrice: number | null;
  auctionPrice: number | null;
  openPrice: number | null;
  return1dPct: number | null;
  return3dPct: number | null;
  returnWkPct: number | null;
  klineJudgment: string;
  klineSecondary: string;
  volumeStatus: string;
  note?: string | null;
}

export interface BacktestSummary {
  total: number;
  withData: number;
  avg1dPct: number;
  avg3dPct: number;
  avgWkPct: number;
  winRate1d: number;
  winRate3d: number;
  winRateWk: number;
  best1dPct: number;
  worst1dPct: number;
}

export interface RecommendationBacktestResponse {
  runId: number;
  modelId?: number | null;
  modelName: string;
  modelVersion: string;
  generatedAt: string | null;
  asOfDate: string | null;
  buyDate: string;
  actualBuyDate: string;
  strategyNote: string;
  summary: BacktestSummary;
  items: BacktestStockItem[];
  disclaimer: string;
}

export interface RecommendationBacktestParams {
  industry?: string;
  topN?: number;
  runId?: number | null;
}

// ============ 周度推荐（单页：榜单 + 实时收益 + 买卖窗口） ============

export interface WeeklyLiveItem {
  code: string;
  available: boolean;
  buyDate: string | null;
  buyPrice: number | null;
  lastPrice: number | null;
  return1dPct: number | null;
  return3dPct: number | null;
  returnWkPct: number | null;
  note?: string | null;
}

export interface WeeklyTradeWindow {
  buyDate: string;
  sellDate: string;
  status: 'buy_today' | 'holding' | 'settled' | 'pending';
  statusLabel: string;
  nextBuyDate: string;
  daysSinceBuy: number;
  daysToSell: number;
  isBuyReached: boolean;
  isSettled: boolean;
  /** 特征参考日（模型看到的最后一天行情），非预测目标周。 */
  asOfDate: string | null;
  /** 预测目标周区间（买入周一~卖出周五），显式标明"预测的是哪一周"，与 asOfDate 区分。 */
  predictWeek: string;
}

export interface WeeklyLiveSummary {
  total: number;
  withData: number;
  avg1dPct: number;
  avg3dPct: number;
  avgWkPct: number;
  winRate1d: number;
  winRate3d: number;
  winRateWk: number;
  best1dPct: number;
  worst1dPct: number;
}

export interface WeeklyRecommendationResponse {
  runId: number;
  modelId?: number | null;
  modelName: string;
  modelVersion: string;
  generatedAt: string | null;
  scope: string;
  industry: string | null;
  asOfDate: string | null;
  universeSize: number;
  count: number;
  industryCap: number | null;
  strategy: StrategyHint | null;
  items: RecommendationItem[];
  tradeWindow: WeeklyTradeWindow;
  live: WeeklyLiveItem[];
  liveSummary: WeeklyLiveSummary;
  dataSource: string | null;
  fetchedAt: string | null;
  disclaimer: string;
}

export interface WeeklyRecommendationsParams {
  industry?: string;
  topN?: number;
  runId?: number | null;
}
