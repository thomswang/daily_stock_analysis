// Stock price trend prediction types

export interface PredictionRequest {
  code: string;
  horizonDays?: number;
  lookbackDays?: number;
  language?: 'zh' | 'en';
}

export interface FactorContribution {
  key: string;
  label: string;
  value: number;
  weight: number;
  contribution: number;
}

export interface HistoryPoint {
  date: string;
  close: number;
}

export interface ProjectedPoint {
  date: string;
  day: number;
  price: number;
  lower: number;
  upper: number;
}

export interface ModelMetrics {
  trainAccuracy: number | null;
  validAccuracy: number | null;
  trainSamples: number;
  validSamples: number;
  baselineAccuracy: number | null;
  epochs: number;
  learningRate: number;
}

export interface ModelInfo {
  algorithm: string;
  featureCount: number;
  lookbackDays: number;
  trainedSamples: number;
  /** trained = loaded a persisted offline-trained model; on_the_fly = trained live for this request. */
  source?: 'trained' | 'on_the_fly' | null;
  /** Persisted model version (only when source = trained). */
  version?: string | null;
  /** ISO timestamp of when the persisted model was trained (only when source = trained). */
  trainedAt?: string | null;
}

export interface PredictionRecordItem {
  id: number;
  code: string;
  stockName: string | null;
  asOfDate: string | null;
  horizonDays: number;
  direction: 'up' | 'down';
  upProbability: number | null;
  confidence: number | null;
  expectedReturnPct: number | null;
  lastClose: number | null;
  modelSource: string | null;
  modelName: string | null;
  modelVersion: string | null;
  evalStatus: 'pending' | 'evaluated' | 'insufficient';
  actualClose: number | null;
  actualReturnPct: number | null;
  actualDirection: 'up' | 'down' | null;
  isCorrect: boolean | null;
  evaluatedAt: string | null;
  createdAt: string | null;
}

export interface PredictionHistoryResponse {
  items: PredictionRecordItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface PredictionAccuracyResponse {
  total: number;
  pending: number;
  evaluated: number;
  correct: number;
  accuracy: number | null;
  avgExpectedReturnPct: number | null;
  avgActualReturnPct: number | null;
}

export interface PredictionEvaluateResponse {
  processed: number;
  evaluated: number;
  insufficient: number;
  errors: number;
}

export interface PredictionHistoryParams {
  code?: string;
  status?: 'pending' | 'evaluated' | 'insufficient';
  limit?: number;
  offset?: number;
}

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

export interface RecommendationsResponse {
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

export interface IndustryOption {
  industry: string;
  count: number;
}

export interface IndustriesResponse {
  asOfDate: string | null;
  count: number;
  industries: IndustryOption[];
}

export interface RecommendationsParams {
  industry?: string;
  topN?: number;
  industryCap?: number | null;
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
  status: 'buy_today' | 'holding' | 'pending';
  statusLabel: string;
  nextBuyDate: string;
  daysSinceBuy: number;
  daysToSell: number;
  isBuyReached: boolean;
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
  industryCap?: number | null;
}

export interface PredictionResponse {
  stockCode: string;
  stockName: string | null;
  asOfDate: string;
  lastClose: number;
  horizonDays: number;
  direction: 'up' | 'down';
  upProbability: number;
  confidence: number;
  expectedReturnPct: number;
  dailyVolatility: number;
  history: HistoryPoint[];
  projected: ProjectedPoint[];
  factors: FactorContribution[];
  metrics: ModelMetrics;
  model: ModelInfo;
  disclaimer: string;
}
