"""
attribution.py — Which post converted each paid subscriber?

Last-touch model: for every paid subscriber with a `first_payment_at`, find the
most recent post they *opened* at or before they paid, and credit that post with
the conversion. It's the honest "what were they reading right before they pulled
the trigger" signal — the closest thing an export gives you to a conversion funnel.

All outputs are aggregated to the post level. No email ever leaves these functions.
"""

from __future__ import annotations

import pandas as pd

from ingest import Export


def _paid_with_payment(exp: Export) -> pd.DataFrame:
    subs = exp.subscribers
    if subs.empty or "is_paid" not in subs or "first_payment_at" not in subs:
        return pd.DataFrame()
    paid = subs[subs["is_paid"] & subs["first_payment_at"].notna()]
    return paid[["email", "first_payment_at"]].dropna()


def last_touch(exp: Export) -> dict:
    """Attribute each paid conversion to the last post opened before payment.

    Returns a dict with:
      - by_post:    DataFrame(numeric_id, title, conversions) ranked
      - attributed: int  (paid subs credited to a post)
      - no_pre_open: int (paid subs who never opened a post before paying)
      - total_paid_with_date: int
      - days_to_convert: list[float]  (open→payment gap, for a histogram)
    """
    paid = _paid_with_payment(exp)
    empty = {"by_post": pd.DataFrame(), "attributed": 0, "no_pre_open": 0,
             "total_paid_with_date": 0, "days_to_convert": []}
    if paid.empty or exp.opens.empty:
        return empty

    o = exp.opens.dropna(subset=["email", "timestamp"])[["email", "timestamp", "numeric_id"]]
    merged = o.merge(paid, on="email", how="inner")
    # opens that happened at or before the payment
    pre = merged[merged["timestamp"] <= merged["first_payment_at"]]

    total = paid["email"].nunique()
    if pre.empty:
        return {**empty, "total_paid_with_date": total, "no_pre_open": total}

    # last open per subscriber before paying
    pre = pre.sort_values("timestamp")
    last = pre.groupby("email").tail(1).copy()
    last["days_to_convert"] = (
        (last["first_payment_at"] - last["timestamp"]).dt.total_seconds() / 86400
    )

    attributed = last["email"].nunique()
    by_post = (last.groupby("numeric_id").size()
               .rename("conversions").reset_index()
               .sort_values("conversions", ascending=False))

    if not exp.posts.empty and "numeric_id" in exp.posts:
        titles = exp.posts[["numeric_id", "title"]].drop_duplicates("numeric_id")
        by_post = by_post.merge(titles, on="numeric_id", how="left")
    else:
        by_post["title"] = by_post["numeric_id"]

    return {
        "by_post": by_post,
        "attributed": int(attributed),
        "no_pre_open": int(total - attributed),
        "total_paid_with_date": int(total),
        "days_to_convert": [round(x, 1) for x in last["days_to_convert"].tolist()],
    }
