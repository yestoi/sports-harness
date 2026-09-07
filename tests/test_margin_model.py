from decimal import Decimal
from statistics import NormalDist

import pytest

from harness.pricing.margin_model import (
    SIGMA_MARGIN,
    SIGMA_TOTAL,
    MarginModel,
    model_json,
    p_margin_over,
    p_moneyline,
    p_total_over,
)

FOUR = Decimal("0.0001")


def _q(x: float) -> Decimal:
    return Decimal(str(x)).quantize(FOUR)


def test_home_favorite_at_half_point_gives_mu_equal_to_line():
    model = MarginModel.from_main_lines(
        "nfl",
        home_point=Decimal("-3.5"),
        p_home_cover=Decimal("0.5"),
        total_line=None,
        p_over=None,
    )
    assert model.mu_home_margin == pytest.approx(3.5)
    assert p_margin_over(model, True, Decimal("3.5")) == Decimal("0.5000")


def test_p_margin_over_matches_normaldist_computation():
    model = MarginModel.from_main_lines(
        "nfl",
        home_point=Decimal("-3.5"),
        p_home_cover=Decimal("0.5"),
        total_line=None,
        p_over=None,
    )
    sigma = SIGMA_MARGIN["nfl"]
    expected = _q(1 - NormalDist(3.5, sigma).cdf(0.5))
    assert p_margin_over(model, True, Decimal("0.5")) == expected


def test_away_symmetry_with_home():
    model = MarginModel.from_main_lines(
        "nfl",
        home_point=Decimal("-3.5"),
        p_home_cover=Decimal("0.5"),
        total_line=None,
        p_over=None,
    )
    home_p = p_margin_over(model, True, Decimal("3.5"))
    away_p = p_margin_over(model, False, Decimal("-3.5"))
    assert abs((Decimal(1) - home_p) - away_p) <= Decimal("0.0001")


def test_totals_monotone_decreasing_across_thresholds():
    model = MarginModel.from_main_lines(
        "nfl",
        home_point=Decimal("-3.5"),
        p_home_cover=Decimal("0.5"),
        total_line=Decimal("44.5"),
        p_over=Decimal("0.5"),
    )
    p1 = p_total_over(model, Decimal("41.5"))
    p2 = p_total_over(model, Decimal("44.5"))
    p3 = p_total_over(model, Decimal("47.5"))
    assert p1 > p2 > p3


def test_from_main_lines_shifts_mu_by_sigma_times_inverse_cdf_of_p():
    p = Decimal("0.52")
    model = MarginModel.from_main_lines(
        "nfl",
        home_point=Decimal("-3.5"),
        p_home_cover=p,
        total_line=None,
        p_over=None,
    )
    sigma = SIGMA_MARGIN["nfl"]
    expected_mu = 3.5 + sigma * NormalDist().inv_cdf(float(p))
    assert model.mu_home_margin == pytest.approx(expected_mu)


def test_from_main_lines_total_shift_by_sigma_times_inverse_cdf_of_p_over():
    p_over = Decimal("0.55")
    model = MarginModel.from_main_lines(
        "nfl",
        home_point=Decimal("-3.5"),
        p_home_cover=Decimal("0.5"),
        total_line=Decimal("44.5"),
        p_over=p_over,
    )
    sigma_t = SIGMA_TOTAL["nfl"]
    expected_mu_total = 44.5 + sigma_t * NormalDist().inv_cdf(float(p_over))
    assert model.mu_total == pytest.approx(expected_mu_total)


def test_p_total_over_raises_without_total():
    model = MarginModel.from_main_lines(
        "nfl",
        home_point=Decimal("-3.5"),
        p_home_cover=Decimal("0.5"),
        total_line=None,
        p_over=None,
    )
    with pytest.raises(ValueError):
        p_total_over(model, Decimal("44.5"))


def test_p_margin_over_clips_at_extreme_threshold():
    model = MarginModel.from_main_lines(
        "nfl",
        home_point=Decimal("-3.5"),
        p_home_cover=Decimal("0.5"),
        total_line=None,
        p_over=None,
    )
    p = p_margin_over(model, True, Decimal("60.5"))
    assert p >= Decimal("0.0001")
    assert p <= Decimal("0.9999")


def test_p_moneyline_home_favorite_greater_than_half():
    model = MarginModel.from_main_lines(
        "nfl",
        home_point=Decimal("-3.5"),
        p_home_cover=Decimal("0.5"),
        total_line=None,
        p_over=None,
    )
    home_ml = p_moneyline(model, True)
    away_ml = p_moneyline(model, False)
    assert home_ml > Decimal("0.5")
    assert away_ml < Decimal("0.5")
    assert abs((home_ml + away_ml) - Decimal("1")) <= Decimal("0.0001")


def test_model_json_contains_expected_keys():
    model = MarginModel.from_main_lines(
        "nfl",
        home_point=Decimal("-3.5"),
        p_home_cover=Decimal("0.5"),
        total_line=Decimal("44.5"),
        p_over=Decimal("0.5"),
    )
    j = model_json(model)
    assert set(j.keys()) == {"mu", "sigma", "mu_total", "sigma_total", "source"}
    assert j["sigma"] == SIGMA_MARGIN["nfl"]
    assert j["sigma_total"] == SIGMA_TOTAL["nfl"]
    assert isinstance(j["source"], dict)
    assert "note" in j["source"]


def test_source_records_inputs_as_strings():
    model = MarginModel.from_main_lines(
        "nfl",
        home_point=Decimal("-3.5"),
        p_home_cover=Decimal("0.52"),
        total_line=Decimal("44.5"),
        p_over=Decimal("0.55"),
    )
    assert model.source["home_point"] == "-3.5"
    assert model.source["p_home_cover"] == "0.52"
    assert model.source["total_line"] == "44.5"
    assert model.source["p_over"] == "0.55"


def test_source_total_none_when_no_total_given():
    model = MarginModel.from_main_lines(
        "nfl",
        home_point=Decimal("-3.5"),
        p_home_cover=Decimal("0.5"),
        total_line=None,
        p_over=None,
    )
    assert model.source["total_line"] is None
    assert model.source["p_over"] is None
    assert model.mu_total is None
    assert model.sigma_total is None


def test_ncaaf_uses_wider_sigma():
    model = MarginModel.from_main_lines(
        "ncaaf",
        home_point=Decimal("-3.5"),
        p_home_cover=Decimal("0.5"),
        total_line=None,
        p_over=None,
    )
    assert model.sigma_margin == SIGMA_MARGIN["ncaaf"]
    assert SIGMA_MARGIN["ncaaf"] > SIGMA_MARGIN["nfl"]
