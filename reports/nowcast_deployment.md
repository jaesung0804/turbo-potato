# Monthly asking-price comparison

The existing annual model uses prior calendar-year prices. Its neutral price can lag a moving market, so the asking-price panel and list now show a separate monthly valuation as their default comparison price. Annual ranking scores and annual uncertainty ranges retain their original meaning and are explicitly labelled.

The monthly model uses recent price medians, exponentially weighted medians, transaction frequency, effective sample size, distinct transaction days, and floor characteristics. Inference uses the same 31-day assumed reporting lag as the experiment, and the observable prior 365-day median floor. The UI displays the cutoff, recent count, and representative floor. Asking prices are not training labels. Household turnover is excluded because verified historical household denominators are unavailable.

The selected model was chosen using 2024 data and trained through 2025. Subsequent transaction evaluation covers 325,084 transactions: MAE 0.8714 → 0.4335억 in 2025 and 0.7521 → 0.5142억 in March–July 2026. These results condition on the actual transaction floor; they are not claimed as measured performance of a representative-floor price or of the annual ranking. Data uses retrospective corrections and an assumed publication lag, not archived point-in-time records.

The small trained artifact is checked against its SHA-256 manifest before inference. Its supported period is March–December 2026. Outside this period, or when history is insufficient, the website clearly falls back to the annual reference. A new year requires retraining and validation before enabling the monthly comparison. The monthly price is not an annual interval or an investment-return forecast.

Validation: full Python suite, JavaScript checks, asking-price neutral point and fallback tests, artifact integrity tests, and complete-bundle publication checks. The existing daily Pages build regenerates all current estimates from verified raw transactions.
