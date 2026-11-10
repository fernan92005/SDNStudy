# AI-Driven Predictive Network Traffic Management for 6G Using Edge Intelligence

White paper (2026) prototype implementing an AI-native, predictive traffic
management system for next-generation 6G networks. The system uses an LSTM
time-series model to forecast per-link utilization, a rule-based decision
engine to flag impending congestion, and an SDN-style routing layer to
dynamically reroute flows before congestion materialises.

Two execution tracks live in this repo:

- **Track A — Pure-Python simulation (primary).** Runs on WSL2 / Linux / macOS
  without root. NetworkX-based multipath topology, scripted traffic scenarios,
  Keras LSTM, comparison plots.
- **Track B — Mininet + Ryu (stretch).** Same CSV schema and same decision
  engine, but data collected from a real OpenFlow data plane. See
  [`mininet/README.md`](mininet/README.md).

## Repository layout

```
whitepaper/
  docs/                 Abstract, architecture, methodology, references
    figures/            Static / hand-authored figures
  src/
    config.py           Central knobs (topology, ML hparams, thresholds)
    simulation/         Topology + traffic generator + simulator loop
    data/               CSV collector + sliding-window preprocessor
    ml/                 LSTM model, training, prediction
    decision/           Rule-based decision engine
    sdn/                Static and predictive routers
    evaluation/         Metrics aggregation + static-vs-predictive compare
    visualization/      Matplotlib comparison plots
  scripts/              01..05 numbered driver scripts + run_all.py
  data/                 Generated CSVs (gitignored)
  models/               Trained LSTM + scaler (gitignored)
  results/              Summary CSV + figures (gitignored)
  mininet/              Track B stubs (real OF/SDN data plane)
```

## Quick start (Track A)

```bash
cd whitepaper
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python scripts/run_all.py
```

`run_all.py` will, in order:

1. Generate the training dataset by simulating mixed normal + burst traffic.
2. Train the LSTM forecaster and save model + scaler.
3. Run the four experiments — `{normal, congestion} x {static, predictive}`.
4. Aggregate metrics into `results/summary.csv`.
5. Render comparison plots into `results/figures/`.

Individual stages can be invoked directly:

```bash
python scripts/01_generate_dataset.py
python scripts/02_train_model.py
python scripts/03_run_baseline.py
python scripts/04_run_predictive.py
python scripts/05_evaluate.py
```

## White paper deliverables

- [`docs/abstract.md`](docs/abstract.md) — Idea / abstract submission (400-600 words).
- [`docs/architecture.md`](docs/architecture.md) — Architecture, components, edge-intelligence rationale.
- [`docs/methodology.md`](docs/methodology.md) — Methodology section ready to lift into the full paper.
- [`docs/references.md`](docs/references.md) — Seed bibliography.

## License

Research prototype for white paper submission. No license declared.
