"""Phase 6D.1's execution-viability experiment (addendum §1.1).

Nothing in `harness/` outside this package imports it; `tests/test_exp_isolation.py` asserts that,
and the database role of §1.1(b) is the boundary that does not depend on the assertion.
"""
from harness.execution.policy import COUNTERFACTUAL_LABEL

#: §1.1(b)/§4.7. The least-privileged role both capabilities connect as. The user creates it once;
#: until then every run fails closed at session open.
EXP_DB_ROLE = "harness_exp"

#: §1.9(e). Every exploratory cell carries it, so no number of this milestone can be read as a
#: registered variant's performance.
EXP_LABEL = COUNTERFACTUAL_LABEL


def exp_label(run_id: str, arm_id: str, manifest_hash: str) -> str:
    return f"{EXP_LABEL} run={run_id} arm={arm_id} manifest={manifest_hash[:12]}"


class IsolationError(RuntimeError):
    """Raised **before any work** when isolation cannot be established (§1.1b/e).

    Three causes, all fail-closed: the connected role can INSERT into a production table, the
    role's secret is absent, or the destination names a database other than the configured one.
    """
