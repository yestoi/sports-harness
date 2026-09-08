"""The weekly report's inference, stdlib only and pure.

No SciPy: the harness ships no numeric dependency (a gate in the phase-3 plan), so the Student
t tail this whole report rests on is built here from the regularised incomplete beta by Lentz's
continued fraction, and the quantile by bisection on it. Everything downstream -- the
cluster-robust interval, the two multiplicity procedures, the empirical-Bayes shrinkage and the
Kaplan-Meier median -- is arithmetic on top of those two functions.

Every formula here is pinned verbatim by the pre-registration record's analysis plan and the
phase-3 addendum §4 (ruling R1: thresholds, families and formulas are invariants, never a
"tuning"). Where a sample is too small for a formula to mean anything (one cluster, a zero
standard error), the function answers `nan` rather than a number, and the table layer renders
the placeholder -- a report that prints a fabricated interval is worse than one that prints a
dash.

Units: everything here is `float`. The database's `Decimal` probabilities are converted once,
at the table layer, on the way in.
"""

import math
from dataclasses import dataclass
from typing import Sequence

#: Lentz's continued fraction for the incomplete beta, pinned to the brief.
BETACF_MAXIT = 200
BETACF_EPS = 1e-12
#: Bisection tolerance for `t_ppf`, and the bracket the brief fixes.
PPF_TOL = 1e-9
PPF_HI = 1000.0

NAN = float("nan")


# --- the Student t tail ---------------------------------------------------------------------


def _betacf(a: float, b: float, x: float) -> float:
    """The continued fraction of the incomplete beta, evaluated by the modified Lentz method.

    `tiny` guards the classic failure of the algorithm: a zero denominator restarts the
    recurrence at a value small enough not to move the result.
    """
    tiny = 1e-30
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, BETACF_MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < BETACF_EPS:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """The regularised incomplete beta `I_x(a, b)`.

    The continued fraction converges quickly only on one side of `x = (a+1)/(a+b+2)`; the other
    side is evaluated through the symmetry `I_x(a, b) = 1 - I_{1-x}(b, a)`.
    """
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
             + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(front) * _betacf(a, b, x) / a
    return 1.0 - math.exp(front) * _betacf(b, a, 1.0 - x) / b


def t_sf(t: float, df: float) -> float:
    """`P(T > t)` for Student's t with `df` degrees of freedom -- the one-sided upper tail."""
    if df <= 0 or math.isnan(t):
        return NAN
    if t < 0:
        return 1.0 - t_sf(-t, df)
    return 0.5 * betainc(df / 2.0, 0.5, df / (df + t * t))


def two_sided_p(t: float, df: float) -> float:
    """The two-sided p-value of a t statistic -- what the BH and Holm families are built on."""
    if math.isnan(t) or df <= 0:
        return NAN
    return 2.0 * t_sf(abs(t), df)


def t_ppf(q: float, df: float) -> float:
    """The `q` quantile of Student's t, by bisection on `t_sf` over [0, PPF_HI].

    The bracket is one-sided, so a quantile below the median is answered by symmetry rather
    than by widening it.
    """
    if df <= 0 or not 0.0 < q < 1.0:
        return NAN
    if q < 0.5:
        return -t_ppf(1.0 - q, df)
    if q == 0.5:
        return 0.0
    target = 1.0 - q  # the upper-tail mass the quantile leaves
    lo, hi = 0.0, PPF_HI
    while hi - lo > PPF_TOL:
        mid = (lo + hi) / 2.0
        if t_sf(mid, df) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# --- cluster-robust intervals ---------------------------------------------------------------


@dataclass(frozen=True)
class CI:
    """One cell: the mean, its cluster-robust interval, and the two counts every cell prints.

    `n_clusters` is what the grey (< 10) and flag (< 30) rules read, never `n_obs` -- the
    pre-registration record's analysis plan is explicit that "cells with n < 30" means games.
    """

    mean: float
    lo: float
    hi: float
    se: float
    t: float
    n_obs: int
    n_clusters: int

    @property
    def excludes_zero(self) -> bool:
        """True when the interval is entirely on one side of zero (both bounds finite)."""
        if math.isnan(self.lo) or math.isnan(self.hi):
            return False
        return self.lo > 0.0 or self.hi < 0.0


EMPTY_CI = CI(NAN, NAN, NAN, NAN, NAN, 0, 0)


def cluster_ci(values: Sequence[float], clusters: Sequence, level: float = 0.90) -> CI:
    """Mean of `values` with a cluster-robust interval, clustered by `clusters` (games).

    `SE^2 = (G / (G - 1)) x sum_g (sum_{i in g} (x_i - xbar))^2 / N^2`, `t = xbar / SE`, and
    `xbar +/- t_{(1+level)/2, G-1} x SE` (pre-registration analysis plan, addendum §4). One
    cluster has no between-cluster variation to estimate, so its interval is `nan`, not zero.
    """
    if len(values) != len(clusters):
        raise ValueError("values and clusters must be the same length")
    n = len(values)
    if n == 0:
        return EMPTY_CI
    xbar = math.fsum(values) / n
    sums: dict = {}
    for value, cluster in zip(values, clusters):
        sums[cluster] = sums.get(cluster, 0.0) + (value - xbar)
    g = len(sums)
    if g < 2:
        return CI(xbar, NAN, NAN, NAN, NAN, n, g)
    var = (g / (g - 1)) * math.fsum(s * s for s in sums.values()) / (n * n)
    se = math.sqrt(var) if var > 0 else 0.0
    if se == 0.0:
        return CI(xbar, xbar, xbar, 0.0, NAN, n, g)
    crit = t_ppf(0.5 + level / 2.0, g - 1)
    return CI(xbar, xbar - crit * se, xbar + crit * se, se, xbar / se, n, g)


def paired_contrast(values_a: Sequence[float], values_b: Sequence[float],
                    clusters: Sequence, level: float = 0.90) -> CI:
    """`cluster_ci` on the elementwise difference `a - b`.

    Table 2's contrasts are paired on the shared snapshot, so the caller aligns the two
    sequences before calling: element `i` of each is the same snapshot.
    """
    if len(values_a) != len(values_b):
        raise ValueError("paired sequences must be the same length")
    return cluster_ci([a - b for a, b in zip(values_a, values_b)], clusters, level=level)


# --- multiplicity ---------------------------------------------------------------------------


def bh_reject(pvalues: Sequence[float], q: float = 0.10) -> list[bool]:
    """Benjamini-Hochberg at false-discovery rate `q`, answered in the caller's order.

    Reject every hypothesis whose p-value is at or below `p_(k)`, where `k` is the largest
    rank with `p_(k) <= k q / m`.
    """
    m = len(pvalues)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvalues[i])
    cutoff = -1
    for rank, i in enumerate(order, start=1):
        if pvalues[i] <= rank * q / m:
            cutoff = rank
    out = [False] * m
    for rank, i in enumerate(order, start=1):
        if rank <= cutoff:
            out[i] = True
    return out


def holm_reject(pvalues: Sequence[float], alpha: float = 0.10) -> list[bool]:
    """Holm's step-down at family-wise `alpha`, answered in the caller's order.

    Walk the sorted p-values and reject while `p_(i) <= alpha / (m - i + 1)`; the first
    failure stops the procedure for every remaining hypothesis.
    """
    m = len(pvalues)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvalues[i])
    out = [False] * m
    for rank, i in enumerate(order, start=1):
        if pvalues[i] > alpha / (m - rank + 1):
            break
        out[i] = True
    return out


# --- empirical-Bayes shrinkage --------------------------------------------------------------


@dataclass(frozen=True)
class Shrunk:
    """Cell means pulled toward the stratum's grand mean, with the posterior interval.

    `note` carries "no heterogeneity detected" exactly when `tau2` is zero: the data give no
    evidence that the cells differ at all, so every cell collapses onto the grand mean and no
    cell counts as significant on the posterior interval (analysis plan, ruling R13).
    """

    m_tilde: list[float]
    B: list[float]
    tau2: float
    lo: list[float]
    hi: list[float]
    note: str | None = None


NO_HETEROGENEITY = "no heterogeneity detected"


def eb_shrink(means: Sequence[float], ses: Sequence[float], dfs: Sequence[int]) -> Shrunk:
    """`tau^2 = max(0, var(m) - mean(se^2))`, `B_i = tau^2 / (tau^2 + se_i^2)`,
    `m~_i = B_i m_i + (1 - B_i) mbar`, `lo/hi = m~_i +/- t_{0.95, df_i} sqrt(B_i se_i^2)`.

    `var` is the sample variance of the cell means (the usual `G - 1` denominator); a single
    cell has none, so `tau^2` is zero and the cell is its own grand mean.
    """
    if not (len(means) == len(ses) == len(dfs)):
        raise ValueError("means, ses and dfs must be the same length")
    k = len(means)
    if k == 0:
        return Shrunk([], [], 0.0, [], [], NO_HETEROGENEITY)
    mbar = math.fsum(means) / k
    var_m = math.fsum((m - mbar) ** 2 for m in means) / (k - 1) if k > 1 else 0.0
    mean_se2 = math.fsum(se * se for se in ses) / k
    tau2 = max(0.0, var_m - mean_se2)
    if tau2 == 0.0:
        return Shrunk([mbar] * k, [0.0] * k, 0.0, [mbar] * k, [mbar] * k, NO_HETEROGENEITY)
    shrinkage = [tau2 / (tau2 + se * se) for se in ses]
    tilde = [b * m + (1.0 - b) * mbar for b, m in zip(shrinkage, means)]
    lo, hi = [], []
    for b, m, se, df in zip(shrinkage, tilde, ses, dfs):
        half = t_ppf(0.95, df) * math.sqrt(b * se * se) if df > 0 else NAN
        lo.append(m - half)
        hi.append(m + half)
    return Shrunk(tilde, shrinkage, tau2, lo, hi, None)


# --- Kaplan-Meier ---------------------------------------------------------------------------


def km_median(durations: Sequence[float], censored: Sequence[bool]) -> tuple[float | None, float]:
    """The Kaplan-Meier median duration and the censored share.

    The median is the smallest observed event time at which the survival function has fallen to
    0.5 or below; when it never does (heavy censoring, or a follow-up that ends first) there is
    no median and the caller prints the censored share instead of a number.
    """
    if len(durations) != len(censored):
        raise ValueError("durations and censored must be the same length")
    n = len(durations)
    if n == 0:
        return None, 0.0
    share = sum(1 for c in censored if c) / n
    rows = sorted(zip(durations, censored), key=lambda r: (r[0], r[1]))
    survival = 1.0
    at_risk = n
    i = 0
    while i < len(rows):
        time = rows[i][0]
        events = 0
        tied = 0
        while i + tied < len(rows) and rows[i + tied][0] == time:
            if not rows[i + tied][1]:
                events += 1
            tied += 1
        if events:
            survival *= 1.0 - events / at_risk
            if survival <= 0.5:
                return time, share
        at_risk -= tied
        i += tied
    return None, share
