"""Paper execution: live book state, fill simulation and the executor loop."""

#: Bumped by any change under `harness/execution/`; part of an order's `config_hash`. 4.5 is
#: 6B's one bump for the whole milestone (spec §7.3, D10): it is the measurement boundary the
#: six corrections C1-C6 (Amendment 6) record, so orders placed afterwards carry new
#: `orders.config_hash` values even though no variant config changed.
#: **Keep it a plain decimal numeral** while `orders.nw_executor_version` exists: spec amendment
#: 0.17 stamps every counterfactual write with this value in a `numeric` column, so `4.5.1` or
#: `4.6-rc1` would be unstorable (`harness.execution.store.executor_version_numeric` raises with
#: that message rather than letting the driver fail inside a write). Moving to such a version
#: means moving the column to text in its own additive revision and amendment first.
EXECUTOR_VERSION = "4.5"
