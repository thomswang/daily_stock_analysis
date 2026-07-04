---
applyTo: "main.py,server.py,train_model.py,rank_snapshot.py,src/**/*.py,data_provider/**/*.py,api/**/*.py,bot/**/*.py,scripts/**/*.py,tests/**/*.py"
---

# Backend Instructions

- Preserve current pipeline boundaries and reuse existing services, repositories, schemas, and fallback logic instead of creating parallel paths.
- Changes touching config, CLI flags, schedule semantics, API behavior, auth, or report payloads must sync `.env.example` and assess Web/Desktop compatibility.
- Changes in `data_provider/` must preserve provider priority, normalization behavior, timeout/retry expectations, and graceful degradation.
- Changes to model training, feature engineering, labels, prediction/scoring, backtest methodology, or stock-selection strategy logic (e.g. `src/services/model_training_service.py`, `src/services/prediction_service.py`, `src/services/stock_ranking_service.py`, `src/services/prediction_backtest_service.py`, `scripts/*backtest*.py`, `train_model.py`, `rank_snapshot.py`) must sync `docs/prediction-architecture.md` (training/prediction/scoring) and `docs/backtest-methodology.md` (backtest methodology), and append to `docs/CHANGELOG.md`. Keep feature lists, label definitions, split/anti-leakage rules, algorithms/hyperparameters, evaluation metrics, backtest rules, and CLI/API params consistent with the code so agents never reason from stale docs.
- Prefer `./scripts/ci_gate.sh` when feasible; otherwise run `python -m py_compile` on changed files plus the closest deterministic tests.
- Do not let a single provider, notification channel, or optional integration failure break the main analysis flow unless the requirement explicitly demands fail-fast behavior.
