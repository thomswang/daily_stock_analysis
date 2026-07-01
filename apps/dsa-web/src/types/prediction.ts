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
