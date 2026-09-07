from decimal import Decimal, ROUND_HALF_UP

FOUR = Decimal("0.0001")


def implied(price_decimal: Decimal) -> Decimal:
    return (Decimal(1) / Decimal(price_decimal)).quantize(FOUR, rounding=ROUND_HALF_UP)


def proportional_devig(implieds: list[Decimal]) -> list[Decimal]:
    total = sum(implieds)
    if total <= 0:
        return [Decimal("0.5000")] * len(implieds) if len(implieds) == 2 else implieds
    return [(p / total).quantize(FOUR, rounding=ROUND_HALF_UP) for p in implieds]


def power_devig(implieds: list[Decimal], tol: float = 1e-9, max_iter: int = 100) -> list[Decimal]:
    ps = [float(p) for p in implieds]
    if any(p <= 0 or p >= 1 for p in ps):
        raise ValueError("power devig needs implieds strictly inside (0,1)")
    lo, hi = 0.5, 5.0
    f = lambda k: sum(p ** k for p in ps) - 1.0  # noqa: E731
    if f(lo) < 0 or f(hi) > 0:
        raise ValueError("no root in bracket")
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    k = (lo + hi) / 2
    return [Decimal(str(p ** k)).quantize(FOUR, rounding=ROUND_HALF_UP) for p in ps]


def devig(prices: list[Decimal], method: str = "power") -> list[Decimal]:
    imps = [implied(p) for p in prices]
    if method == "power":
        try:
            return power_devig(imps)
        except (ValueError, ZeroDivisionError, OverflowError):
            pass
    return proportional_devig(imps)
