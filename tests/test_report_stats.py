"""The report's statistics, all stdlib and all pure: no database, no dependency.

Every function is pinned to a value that can be checked by hand or against a table, because
these are the numbers the gate and the §9.6 success criterion are judged on. The brief fixes
the formulas verbatim (ruling R1), so the tests fix the values they produce.
"""

import math

from harness.report.stats import (
    CI,
    bh_reject,
    betainc,
    cluster_ci,
    eb_shrink,
    holm_reject,
    km_median,
    paired_contrast,
    t_ppf,
    t_sf,
    two_sided_p,
)

#: Benjamini-Hochberg (1995) worked example, 15 p-values. BH at q = 0.05 rejects the first
#: four (the published answer); at q = 0.10 it rejects nine, while Holm at alpha = 0.10 stops
#: at three -- which is exactly the difference the two procedures are here to make.
BH1995 = [0.0001, 0.0004, 0.0019, 0.0095, 0.0201, 0.0278, 0.0298, 0.0344,
          0.0459, 0.3240, 0.4262, 0.5719, 0.6528, 0.7590, 1.0000]


def test_t_ppf_known_values():
    assert abs(t_ppf(0.95, 10) - 1.812) < 0.001
    assert abs(t_ppf(0.95, 1) - 6.314) < 0.001
    assert abs(t_ppf(0.975, 30) - 2.042) < 0.001


def test_t_sf_known_value():
    assert abs(t_sf(2.0, 10) - 0.0367) < 0.0005
    # The tail is a survival function: symmetric, and a half at zero.
    assert abs(t_sf(0.0, 10) - 0.5) < 1e-9
    assert abs(t_sf(-2.0, 10) - (1 - t_sf(2.0, 10))) < 1e-12
    assert abs(two_sided_p(-2.0, 10) - 2 * t_sf(2.0, 10)) < 1e-12
    # betainc is the regularised form: 0 at 0, 1 at 1, and symmetric under (a, b) -> (b, a).
    assert betainc(3.0, 5.0, 0.0) == 0.0
    assert betainc(3.0, 5.0, 1.0) == 1.0
    assert abs(betainc(3.0, 5.0, 0.4) + betainc(5.0, 3.0, 0.6) - 1.0) < 1e-12


def test_cluster_ci_matches_hand_computation():
    # Three clusters of two. xbar = 3.5; cluster deviation sums -4, 0, +4, so
    # SE^2 = (3/2) x (16 + 0 + 16) / 36 = 4/3 and SE = 1.154700...; t = 3.5 / SE.
    ci = cluster_ci([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], ["a", "a", "b", "b", "c", "c"])
    assert isinstance(ci, CI)
    assert ci.n_obs == 6 and ci.n_clusters == 3
    assert abs(ci.mean - 3.5) < 1e-12
    assert abs(ci.se - math.sqrt(4 / 3)) < 1e-12
    assert abs(ci.t - 3.5 / math.sqrt(4 / 3)) < 1e-12
    # t_{0.95, 2} = 2.919986; bound = 3.5 -/+ 2.919986 x 1.154700
    assert abs(ci.lo - 0.127887) < 1e-3
    assert abs(ci.hi - 6.872113) < 1e-3


def test_paired_contrast():
    # a - b is [1, 2, 3, 4] over two clusters: SE^2 = (2/1) x (4 + 4) / 16 = 1.
    ci = paired_contrast([2.0, 4.0, 6.0, 8.0], [1.0, 2.0, 3.0, 4.0], ["a", "a", "b", "b"])
    assert ci.n_obs == 4 and ci.n_clusters == 2
    assert abs(ci.mean - 2.5) < 1e-12
    assert abs(ci.se - 1.0) < 1e-12
    assert abs(ci.t - 2.5) < 1e-12
    # t_{0.95, 1} = 6.313752
    assert abs(ci.lo - (2.5 - 6.313752)) < 1e-3
    assert abs(ci.hi - (2.5 + 6.313752)) < 1e-3


def test_bh_and_holm_on_textbook_vectors():
    bh = bh_reject(BH1995, q=0.05)
    assert bh == [True] * 4 + [False] * 11
    assert bh_reject(BH1995, q=0.10) == [True] * 9 + [False] * 6
    assert holm_reject(BH1995, alpha=0.10) == [True] * 3 + [False] * 12

    # Both procedures answer in the caller's order, not sorted order.
    shuffled = [BH1995[i] for i in (14, 0, 7, 3, 1, 9, 2, 5, 11, 4, 13, 6, 10, 8, 12)]
    expected = [bh_reject(BH1995, q=0.10)[i] for i in (14, 0, 7, 3, 1, 9, 2, 5, 11, 4, 13, 6, 10, 8, 12)]
    assert bh_reject(shuffled, q=0.10) == expected
    assert bh_reject([], q=0.10) == []
    assert holm_reject([], alpha=0.10) == []


def test_eb_shrink_uses_cluster_se_and_reports_no_heterogeneity():
    # var(m) = 0.04, mean(se^2) = 0.01, so tau^2 = 0.03 and B = 0.03 / 0.04 = 0.75.
    shrunk = eb_shrink([0.0, 0.2, 0.4], [0.1, 0.1, 0.1], [9, 9, 9])
    assert abs(shrunk.tau2 - 0.03) < 1e-12
    assert all(abs(b - 0.75) < 1e-12 for b in shrunk.B)
    assert [round(m, 6) for m in shrunk.m_tilde] == [0.05, 0.2, 0.35]
    # half-width = t_{0.95, 9} x sqrt(B se^2) = 1.833113 x 0.0866025
    half = 1.833113 * math.sqrt(0.75 * 0.01)
    assert abs(shrunk.lo[0] - (0.05 - half)) < 1e-4
    assert abs(shrunk.hi[2] - (0.35 + half)) < 1e-4
    assert shrunk.note is None

    flat = eb_shrink([0.1, 0.1, 0.1], [0.1, 0.1, 0.1], [9, 9, 9])
    assert flat.tau2 == 0.0
    assert flat.B == [0.0, 0.0, 0.0]
    assert all(abs(m - 0.1) < 1e-12 for m in flat.m_tilde)
    assert flat.lo == flat.m_tilde == flat.hi
    assert flat.note == "no heterogeneity detected"


def test_km_median_with_censoring():
    # S drops 1 -> 0.8 -> 0.6 at t = 1, 2; t = 3 is censored; t = 4 takes S to 0.3 <= 0.5.
    median, censored_share = km_median([1.0, 2.0, 3.0, 4.0, 5.0], [False, False, True, False, False])
    assert median == 4.0
    assert abs(censored_share - 0.2) < 1e-12

    # Survival that never falls to 0.5 has no median; the censored share is still reported.
    median, censored_share = km_median([1.0, 2.0, 3.0], [False, True, True])
    assert median is None
    assert abs(censored_share - 2 / 3) < 1e-12
    assert km_median([], []) == (None, 0.0)
