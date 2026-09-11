"""Small invented prices used only to verify the replay machinery."""
from .engine import Costs, Policy


def fixture():
    observations, quotes = [], []
    for month, prices, scores in [(1, (60_000_000, 65_000_000), (2., 1.)),
                                  (4, (70_000_000, 70_000_000), (1., 3.)),
                                  (7, (72_000_000, 75_000_000), (1., 3.))]:
        at = f"2024-{month:02d}-01T09:00:00+09:00"
        for asset, price, score in zip(("SYNTHETIC_A", "SYNTHETIC_B"), prices, scores):
            common = {"asset_id": asset, "price_krw": price, "observed_at": at,
                      "available_at": at, "source_id": "invented-fixture", "source_sha256": "0" * 64,
                      "evidence_kind": "synthetic"}
            observations.append({**common, "observation_id": f"obs-{asset}-{month}", "score": score,
                                 "model_version": "invented-scores-v1", "max_feature_available_at": at,
                                 "max_training_label_available_at": "2023-12-01T09:00:00+09:00"})
            for side in ("buy", "sell"):
                quotes.append({**common, "quote_id": f"quote-{asset}-{month}-{side}", "side": side,
                               "valid_until": f"2024-{month:02d}-28T23:59:59.999999+09:00"})
    data = {"schema_version": 1, "kind": "synthetic_fixture", "observations": observations,
            "quotes": quotes, "provenance": {"availability_mode": "synthetic", "personal_preference_features": []}}
    policy = Policy(run_id="synthetic-demo-v1", start="2024-01-01", end="2024-07-20",
                    decision_dates=("2024-01-01", "2024-04-01"), strategy="rotate",
                    initial_cash_krw=100_000_000, reserve_krw=5_000_000,
                    purchase_price_cap_krw=90_000_000, execution_delay_days=2,
                    settlement_delay_days=3, order_ttl_days=20, min_holding_days=30,
                    max_observation_age_days=120, max_quote_age_days=30,
                    switch_score_margin=0.2, exit_signal_date="2024-07-01")
    costs = Costs(scenario_id="invented-arithmetic-fixture-not-tax-rules",
                  acquisition_tax_rate="0.01", buy_broker_rate="0.003", sell_broker_rate="0.003",
                  gain_tax_rate="0.10", annual_carry_rate="0.001", buy_fixed_krw=100_000,
                  sell_fixed_krw=100_000, moving_krw=200_000)
    return data, policy, costs
