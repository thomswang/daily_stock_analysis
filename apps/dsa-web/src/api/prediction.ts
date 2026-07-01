import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  PredictionAccuracyResponse,
  PredictionBacktestRequest,
  PredictionBacktestResponse,
  PredictionEvaluateResponse,
  PredictionHistoryParams,
  PredictionHistoryResponse,
  PredictionRequest,
  PredictionResponse,
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
};
