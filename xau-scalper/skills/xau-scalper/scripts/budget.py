"""Module 6 - budget. lot = risk$ / (stop_distance * contract_oz_per_lot), floored to lot_step.
Refuses when even min_lot would risk more than risk_per_trade. Never rounds up."""

from decimal import Decimal, ROUND_DOWN


def risk_dollars(cfg):
    return Decimal(str(cfg["account_size"])) * Decimal(str(cfg["risk_per_trade_pct"])) / Decimal(100)


def size(entry, stop, cfg):
    dist = abs(Decimal(str(entry)) - Decimal(str(stop)))
    allowed = risk_dollars(cfg)
    oz = Decimal(str(cfg["contract_oz_per_lot"]))
    step = Decimal(str(cfg["lot_step"]))
    min_lot = Decimal(str(cfg["min_lot"]))
    if dist <= 0:
        return {"ok": False, "lot": 0.0, "reason": "stop distance must be above zero"}
    raw = allowed / (dist * oz)
    lot = (raw / step).to_integral_value(rounding=ROUND_DOWN) * step
    min_risk = min_lot * dist * oz
    doc = {"stop_distance": float(dist), "risk_allowed": float(allowed), "raw_lot": float(round(raw, 6)),
           "default_lot": cfg["default_lot"],
           "default_lot_risk": float(Decimal(str(cfg["default_lot"])) * dist * oz)}
    if lot < min_lot:
        doc.update(ok=False, lot=0.0, risk_usd=0.0,
                   reason="stop too wide: %.2f away needs %s lot for $%.2f, below the %s minimum (min lot would risk $%.2f)"
                          % (dist, round(raw, 4), allowed, min_lot, min_risk))
        return doc
    doc.update(ok=True, lot=float(lot), risk_usd=float(lot * dist * oz))
    if doc["default_lot_risk"] > float(allowed):
        doc["warning"] = "your default %.2f lot would risk $%.2f here, over the $%.2f limit" % (
            cfg["default_lot"], doc["default_lot_risk"], allowed)
    return doc
