import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  IndustriesResponse,
  PredictionAccuracyResponse,
  PredictionBacktestRequest,
  PredictionBacktestResponse,
  PredictionEvaluateResponse,
  PredictionHistoryParams,
  PredictionHistoryResponse,
  PredictionRequest,
  PredictionResponse,
  RecommendationsParams,
  RecommendationsResponse,
  RecommendationBacktestParams,
  RecommendationBacktestResponse,
  WeeklyRecommendationResponse,
  WeeklyRecommendationsParams,
} from '../types/prediction';

export const predictionApi = {
  /**
   * Run a lightweight ML trend prediction for a single stock.
   */
  predict: async (params: PredictionRequest): Promise<PredictionResponse> => {
    const requestData: Record<string, unknown> = {
      code: params.code.trim(),
    };
    if (params.horizonDays != null) requestData.horizon_days = params.horizonDays;
    if (params.lookbackDays != null) requestData.lookback_days = params.lookbackDays;
    if (params.language) requestData.language = params.language;

    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/prediction/predict',
      requestData,
      { timeout: 60000 },
    );
    return toCamelCase<PredictionResponse>(response.data);
  },

  /** List persisted historical predictions (paginated). */
  history: async (params: PredictionHistoryParams = {}): Promise<PredictionHistoryResponse> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/prediction/history', {
      params: {
        code: params.code || undefined,
        status: params.status || undefined,
        limit: params.limit ?? 20,
        offset: params.offset ?? 0,
      },
    });
    return toCamelCase<PredictionHistoryResponse>(response.data);
  },

  /** Aggregate accuracy statistics. */
  accuracy: async (code?: string): Promise<PredictionAccuracyResponse> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/prediction/accuracy', {
      params: { code: code || undefined },
    });
    return toCamelCase<PredictionAccuracyResponse>(response.data);
  },

  /** Trigger backfill evaluation of due pending predictions. */
  evaluate: async (refresh = true, limit = 500): Promise<PredictionEvaluateResponse> => {
    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/prediction/evaluate',
      { refresh, limit },
      { timeout: 120000 },
    );
    return toCamelCase<PredictionEvaluateResponse>(response.data);
  },

  /** Run a walk-forward backtest of the trend prediction model. */
  backtest: async (params: PredictionBacktestRequest): Promise<PredictionBacktestResponse> => {
    const requestData: Record<string, unknown> = { code: params.code.trim() };
    if (params.horizonDays != null) requestData.horizon_days = params.horizonDays;
    if (params.lookbackDays != null) requestData.lookback_days = params.lookbackDays;
    if (params.retrainEvery != null) requestData.retrain_every = params.retrainEvery;
    if (params.minTrain != null) requestData.min_train = params.minTrain;
    if (params.threshold != null) requestData.threshold = params.threshold;
    if (params.allowShort != null) requestData.allow_short = params.allowShort;
    if (params.refresh != null) requestData.refresh = params.refresh;
    if (params.startDate) requestData.start_date = params.startDate;
    if (params.endDate) requestData.end_date = params.endDate;

    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/prediction/backtest',
      requestData,
      { timeout: 120000 },
    );
    return toCamelCase<PredictionBacktestResponse>(response.data);
  },

  /** System-generated stock picks (cross-sectional strength board, whole market or by industry). */
  recommendations: async (params: RecommendationsParams = {}): Promise<RecommendationsResponse> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/prediction/recommendations', {
      params: {
        industry: params.industry || undefined,
        top_n: params.topN ?? 20,
        industry_cap: params.industryCap === null ? undefined : (params.industryCap ?? 3),
      },
    });
    return toCamelCase<RecommendationsResponse>(response.data);
  },

  /** Available industries in the latest ranking snapshot (for the industry dropdown). */
  industries: async (): Promise<IndustriesResponse> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/prediction/industries');
    return toCamelCase<IndustriesResponse>(response.data);
  },

  /**
   * Recommendation backtest: simulate buying at Monday's open price, compute
   * 1/3/5-day actual returns using the same recommended basket.
   */
  recommendationsBacktest: async (
    params: RecommendationBacktestParams = {},
  ): Promise<RecommendationBacktestResponse> => {
    const response = await apiClient.get<Record<string, unknown>>(
      '/api/v1/prediction/recommendations/backtest',
      {
        params: {
          industry: params.industry || undefined,
          top_n: params.topN ?? 20,
        },
      },
    );
    return toCamelCase<RecommendationBacktestResponse>(response.data);
  },

  /**
   * Weekly recommendations (single page): ranking board + buy/sell window
   * (Mon buy / Fri sell) + live returns fetched via TencentFetcher.
   */
  recommendationsWeekly: async (
    params: WeeklyRecommendationsParams = {},
  ): Promise<WeeklyRecommendationResponse> => {
    const response = await apiClient.get<Record<string, unknown>>(
      '/api/v1/prediction/recommendations/weekly',
      {
        params: {
          industry: params.industry || undefined,
          top_n: params.topN ?? 20,
          industry_cap: params.industryCap === null ? undefined : (params.industryCap ?? 3),
        },
        timeout: 60000,
      },
    );
    return toCamelCase<WeeklyRecommendationResponse>(response.data);
  },
};
