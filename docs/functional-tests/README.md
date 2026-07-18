# Functional test plan

LaTeX source for the Grid Trading Web functional, integration, reconciliation, recovery, load, and acceptance test plan.

Build with:

```bash
cd docs/functional-tests
make
```

The stable delivery PDF is written to `../../output/pdf/grid-trading-functional-tests.pdf`.
LaTeX build intermediates remain under `build/`.

Execution labels:

- `C-AUTO`: Codex can complete without external state changes.
- `C-LIVE`: Codex can run controlled Binance checks; real orders require explicit user approval.
- `HUMAN`: requires a person in Binance, browser, or server UI.
- `JOINT`: a person creates the external condition and Codex verifies convergence.

The plan deliberately keeps legacy CSV migration out of scope until the end-to-end gates pass.
