import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  IndustriesResponse,
  PredictionBacktestRequest,
  PredictionBacktestResponse,
  RecommendationRunsResponse,
  RecommendationsParams,
  RecommendationsResponse,
  RecommendationBacktestParams,
  RecommendationBacktestResponse,
  WeeklyRecommendationResponse,
  WeeklyRecommendationsParams,
} from '../types/prediction';

export const predictionApi = {
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
        run_id: params.runId ?? undefined,
        industry: params.industry || undefined,
        top_n: params.topN ?? 20,
      },
    });
    return toCamelCase<RecommendationsResponse>(response.data);
  },

  /** List historical snapshot runs (for the "snapshot selector" dropdown). */
  recommendationRuns: async (limit = 50): Promise<RecommendationRunsResponse> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/prediction/recommendations/runs', {
      params: { limit },
    });
    return toCamelCase<RecommendationRunsResponse>(response.data);
  },

  /** Available industries in the selected ranking snapshot run (for the industry dropdown). */
  industries: async (runId?: number | null): Promise<IndustriesResponse> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/prediction/industries', {
      params: { run_id: runId ?? undefined },
    });
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
          run_id: params.runId ?? undefined,
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
          run_id: params.runId ?? undefined,
          industry: params.industry || undefined,
          top_n: params.topN ?? 20,
        },
        timeout: 60000,
      },
    );
    return toCamelCase<WeeklyRecommendationResponse>(response.data);
  },
};
