"""Flask blueprint every service mounts, so the hub can ask any of them for
the price book, the model list and the spend summary."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from .ledger import Ledger
from .pricing import PriceBook


def make_blueprint(ledger: Ledger, book: PriceBook, monthly_cap_usd: float, available_models: list[str]) -> Blueprint:
    bp = Blueprint("agentkit", __name__)

    @bp.get("/api/pricing")
    def pricing():
        return jsonify({"checked": book.checked, "models": book.list(), "monthly_cap_usd": monthly_cap_usd})

    @bp.get("/api/models")
    def models():
        kind = request.args.get("kind", "text")
        rows = []
        for m in book.list(kind=kind):
            rows.append({**m, "available": m["key"] in available_models or m.get("model") in available_models})
        return jsonify({"rows": rows})

    @bp.get("/api/ledger/summary")
    def summary():
        s = ledger.summary(request.args.get("month") or None)
        s["monthly_cap_usd"] = monthly_cap_usd
        s["cap_reached"] = s["total"] >= monthly_cap_usd
        return jsonify(s)

    @bp.get("/api/ledger/rows")
    def rows():
        return jsonify({"rows": ledger.rows(request.args.get("month") or None, request.args.get("post_id") or None,
                                            int(request.args.get("limit", 200)))})

    return bp
