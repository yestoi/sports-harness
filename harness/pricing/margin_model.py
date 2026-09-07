from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from statistics import NormalDist

FOUR = Decimal("0.0001")
MIN_P = Decimal("0.0001")
MAX_P = Decimal("0.9999")

SIGMA_MARGIN = {"nfl": 13.5, "ncaaf": 17.0}
SIGMA_TOTAL = {"nfl": 10.0, "ncaaf": 13.0}

_STD_NORMAL = NormalDist()


def _clip_p(p: Decimal) -> float:
    if p < MIN_P:
        return float(MIN_P)
    if p > MAX_P:
        return float(MAX_P)
    return float(p)


@dataclass(frozen=True)
class MarginModel:
    sport: str
    mu_home_margin: float
    sigma_margin: float
    mu_total: float | None
    sigma_total: float | None
    source: dict

    @classmethod
    def from_main_lines(
        cls,
        sport: str,
        home_point: Decimal,
        p_home_cover: Decimal,
        total_line: Decimal | None,
        p_over: Decimal | None,
    ) -> "MarginModel":
        if sport not in SIGMA_MARGIN:
            raise ValueError(f"unknown sport: {sport!r}")

        sigma_margin = SIGMA_MARGIN[sport]
        mu_home_margin = -float(home_point) + sigma_margin * _STD_NORMAL.inv_cdf(_clip_p(p_home_cover))

        mu_total: float | None = None
        sigma_total: float | None = None
        if total_line is not None and p_over is not None:
            sigma_total = SIGMA_TOTAL[sport]
            mu_total = float(total_line) + sigma_total * _STD_NORMAL.inv_cdf(_clip_p(p_over))

        source = {
            "home_point": str(home_point),
            "p_home_cover": str(p_home_cover),
            "total_line": str(total_line) if total_line is not None else None,
            "p_over": str(p_over) if p_over is not None else None,
            "sigma_margin": sigma_margin,
            "sigma_total": sigma_total,
            "note": "no key-number adjustment; NFL ties ignored",
        }

        return cls(
            sport=sport,
            mu_home_margin=mu_home_margin,
            sigma_margin=sigma_margin,
            mu_total=mu_total,
            sigma_total=sigma_total,
            source=source,
        )


def _clip(p: Decimal) -> Decimal:
    if p < MIN_P:
        return MIN_P
    if p > MAX_P:
        return MAX_P
    return p


def _prob_over(mu: float, sigma: float, threshold: Decimal) -> Decimal:
    z = (float(threshold) - mu) / sigma
    p = 1 - _STD_NORMAL.cdf(z)
    q = Decimal(str(p)).quantize(FOUR, rounding=ROUND_HALF_UP)
    return _clip(q)


def p_margin_over(model: MarginModel, team_is_home: bool, threshold: Decimal) -> Decimal:
    mu_team = model.mu_home_margin if team_is_home else -model.mu_home_margin
    return _prob_over(mu_team, model.sigma_margin, threshold)


def p_total_over(model: MarginModel, threshold: Decimal) -> Decimal:
    if model.mu_total is None or model.sigma_total is None:
        raise ValueError("model has no total distribution")
    return _prob_over(model.mu_total, model.sigma_total, threshold)


def p_moneyline(model: MarginModel, team_is_home: bool) -> Decimal:
    return p_margin_over(model, team_is_home, Decimal("0"))


def model_json(model: MarginModel) -> dict:
    return {
        "mu": model.mu_home_margin,
        "sigma": model.sigma_margin,
        "mu_total": model.mu_total,
        "sigma_total": model.sigma_total,
        "source": model.source,
    }
