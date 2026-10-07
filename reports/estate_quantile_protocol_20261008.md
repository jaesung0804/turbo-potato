# Direct price quantile candidate — 2026-10-08

Status: implementation and synthetic contracts only. No real-data fit, market
accuracy result, or production quantile artifact has been produced. The current
published price model and its separate symmetric error band remain active.

## Choice and interpretation

Use pooled LightGBM pinball regressors at 0.1, 0.5 and 0.9, with the existing
lagged transaction, neighborhood, exact-area, age, activity and floor features.
Targets are log unit-price residuals relative to the historical anchor; historical
price levels enter as relative differences, and every eligible past row retains
positive recency weight (48-month half-life). Three independent fits permit
asymmetric tails. Increasing rearrangement resolves quantile crossing and is
reported separately. The rearranged P50 is the canonical current price.

This is a conditional transaction-price distribution at the stated month/floor,
not a confidence interval for its median or an inference about related parties.
No low-price exclusion, winsorization, duplicate suppression or price-dependent
weight is added. Existing source rules excluding cancellations and direct deals
remain explicit. Original repeated rows survive. If low sales dominate the local
history, even its median can move: quantiles do not identify a gift or guarantee
robustness to an arbitrary mixture. A cheap sale outside the band remains visible.

TFT supplies the useful multi-quantile objective and covariate distinction.
Chronos-2 is a contemporary covariate-aware forecasting alternative. Sparse,
irregular exact-area transactions with transaction-specific floors make a pooled
tabular quantile baseline a practical first experiment; superiority to TFT or
Chronos is not claimed without an actual matched benchmark. The implementation
is a monthly nowcast, not a multi-horizon future appreciation model.

- [TFT paper, Google Research](https://research.google/pubs/temporal-fusion-transformers-for-interpretable-multi-horizon-time-series-forecasting/)
- [Chronos-2, Amazon Science](https://www.amazon.science/blog/introducing-chronos-2-from-univariate-to-universal-forecasting)
- [LightGBM objective and alpha parameters](https://lightgbm.readthedocs.io/en/latest/Parameters.html)

## Frozen experiment scope

Reuse already saved, verified capital-region history; collect no new originals.
Training targets: March–December 2007–2025. Evaluation: March–August 2026.
January/February remain excluded consistently with the existing feature engine.
The feature cutoff respects the historical 61/31-day availability policy; actual
publication vintages are unavailable, so retrospective revisions/cancellations
remain a limitation. New inference uses the same stable tie ordering as training.

Keep parameters at 220 trees, learning rate 0.04, 23 leaves, minimum child 100,
lambda 10, seed 20261008. No search guided by the named low-price example. Compare
to the exact frozen 2026 production median and error-band artifacts after checking
their SHA-256s. The baseline endpoints are its old symmetric 80% error band; they
are not relabeled as directly trained quantiles.

Report pinball loss for each level, observed CDF, median MAE/MAPE, interval width,
80% transaction coverage, complex-balanced coverage and crossings. Break down by
region, month, fewer than three recent trades, and low/missing/other floors.
Diagnostic gates require improved mean pinball, median MAE no more than 2% worse,
75–85% overall and complex-balanced coverage, and 70–90% coverage in groups with
at least 100 trades. Require at least 1,000 evaluation trades. Small groups remain
unverified. These diagnostics do not automatically approve production.

The 2026 period was used in prior research and the motivating case is known.
Results must therefore be labeled retrospective, not untouched holdout results.
Record later observed months separately for prospective monitoring after an
explicitly validated release.

## Storage and release boundary

`analyze_estate_quantiles.py` requires the existing read-only storage readiness
gate before reading a source or fitting. The current gate has no live provider
capacity/free-resource/retention/conflict-probe adapter and returns `ready=false`.
Its 64 MiB compressed / 256 MiB expanded verification limits also cannot be
silently raised to fit a large corpus. Resolve those prerequisites with actual
provider evidence and a verified supported input scope; do not fabricate a
certificate or bypass the gate with an unverified local raw file.

Restore the exact source and `estate-model-state` before processing. Candidate
output is a new directory inside the restored model state. Retain the original
source snapshot, destination receipt, input and code hashes. Verify destination
head again before saving, and publish only via the existing CAS push using the
original receipt. A conflict requires review/recomputation, not a refreshed
receipt for stale output. No model binaries or private profiles belong in Git.

There is no push-triggered training workflow. The runner is an explicit candidate
experiment, never an active model switch. Promotion additionally requires a new
immutable validated manifest with `quantiles`, matching stable feature-engine
identity and P50, and no legacy `confidence` configuration. Until then the UI and
Excel keep old reference bands distinct from empty direct-quantile fields.

## Excel and travel scope

The separately deployed Excel feature exports the complete filtered result and
blank visit cells. Four destinations use weekday 08:00 public transit. Locally
stored, source-dated facility facts and verified Naver observations are private
browser data. Only the example complex has all four routes verified so far;
this is not an automatic all-complex transit scraper. Unqueried travel times,
unverified FAR/land shares and asking prices remain blank. 2024-10 facility data
are labeled with that date, not presented as a current facility audit.
