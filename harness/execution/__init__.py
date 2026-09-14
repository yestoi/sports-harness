"""Paper execution: live book state, fill simulation and the executor loop."""

#: Bumped by any change under `harness/execution/`; part of an order's `config_hash`. 4.5 is
#: 6B's one bump for the whole milestone (spec §7.3, D10): it is the measurement boundary the
#: six corrections C1-C6 (Amendment 6) record, so orders placed afterwards carry new
#: `orders.config_hash` values even though no variant config changed.
EXECUTOR_VERSION = "4.5"
