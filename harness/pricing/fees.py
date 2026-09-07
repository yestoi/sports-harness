from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
#: Kalshi's actual fee rounding unit: it ceils an order's fee to the hundredth of a cent,
#: not the whole cent (F11) -- rounding the whole order up to a full cent overstated fees
#: on the small maker orders this harness places.
CENTICENT = Decimal("0.0001")


def ceil_to_cent(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_CEILING)


def ceil_to_centicent(x: Decimal) -> Decimal:
    return x.quantize(CENTICENT, rounding=ROUND_CEILING)


@dataclass(frozen=True)
class FeeModel:
    maker_rate: Decimal
    taker_rate: Decimal
    #: Kalshi's per-series fee multiplier. Decimal, not int (F45/R21): a series priced at half
    #: fees carries 0.5, and an int() cast turned that into 0 and made the series look free.
    multiplier: Decimal = Decimal(1)


KALSHI_FOOTBALL = FeeModel(Decimal("0.0175"), Decimal("0.07"), Decimal(1))


def fee_model_for(fee_type: str | None, multiplier: Decimal | str | int | None) -> FeeModel:
    """The fee model for a market's recorded (fee_type, fee_multiplier).

    An unrecognised fee_type raises rather than falling through to the maker-fee model: an
    unpriced shape would otherwise be scored at football rates and the error would only show up
    in the realised P&L (D14).
    """
    m = Decimal(str(multiplier or 1))
    if fee_type is None:
        return KALSHI_FOOTBALL
    if fee_type == "quadratic":
        return FeeModel(Decimal("0"), Decimal("0.07"), m)
    if fee_type == "quadratic_with_maker_fees":
        return FeeModel(Decimal("0.0175"), Decimal("0.07"), m)
    raise ValueError(f"unsupported fee_type {fee_type!r}")


def fee_for_order(model: FeeModel, role: str, p: Decimal, contracts: Decimal | int) -> Decimal:
    rate = model.maker_rate if role == "maker" else model.taker_rate
    raw = rate * model.multiplier * Decimal(contracts) * p * (Decimal(1) - p)
    return max(ceil_to_centicent(raw), Decimal("0.0000")) if raw > 0 else Decimal("0.0000")


def fee_per_contract(model: FeeModel, role: str, p: Decimal, contracts: Decimal | int) -> Decimal:
    if contracts <= 0:
        return Decimal("0.0000")
    return (fee_for_order(model, role, p, contracts) / Decimal(contracts)).quantize(CENTICENT, rounding=ROUND_HALF_UP)


def cost(model: FeeModel, role: str, p: Decimal, contracts: Decimal | int) -> Decimal:
    return (p + fee_per_contract(model, role, p, contracts)).quantize(CENTICENT, rounding=ROUND_HALF_UP)
