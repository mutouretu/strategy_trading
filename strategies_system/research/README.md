# Research workspace

Parameter search, scenario studies, optimization, model training and generated
reports belong here from the research phase onward. Nothing in this directory is
imported by the strategy or live runtime packages.

The research layer contains three versioned inputs:

- `protocols/`: objective profiles and time-ordered dataset splits;
- `data_manifests/`: verified upstream archives, reconstruction provenance and
  locked Parquet identities;
- `scenario_studies/`: Study definitions that reference an existing
  `ExperimentSpec` and the protocols above.

Run facts, Trace, metrics and Study state do not belong in loose JSON result
files. They share one Experiment SQLite database; the optimization layer owns
only `optimization_*` tables in that database.

The 6B COIN-M baseline uses
`protocols/btc_coinm_historical_split_v1.json` and
`scenario_studies/coinm_btc_formal_baseline_v1.json`. TRAIN and VALIDATION may
be executed by the optimization Study; HOLDOUT is content-locked but is
rejected if included before final out-of-sample validation. The immutable
baseline comparison is stored in `optimization_baseline_reports` alongside the
Run, Trace and metric facts it references.
