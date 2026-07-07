"""
metrics.py — Engagement intelligence computed from an Export.

Every function is pure (Export/DataFrames in, DataFrame out) so it can be
unit-checked from the CLI without launching Streamlit.

Core definitions
----------------
open_rate   = unique opening emails / unique delivered emails   (per post)
A "unique open" collapses multiple opens by the same reader into one, which is
the honest denominator-matched rate. Raw open events are kept as `total_opens`.
"""

from __future__ import annotations

import re

import pandas as pd

from ingest import Export

TICKER_RE = re.compile(r"\$[A-Z]{1,5}\b")
QUESTION_RE = re.compile(r"\?")
NUMBER_RE = re.compile(r"\d")


# --------------------------------------------------------------------------- #
# Per-post engagement leaderboard
# --------------------------------------------------------------------------- #

def post_engagement(exp: Export) -> pd.DataFrame:
    """One row per post: delivers, unique opens, total opens, open_rate + metadata."""
    if exp.delivers.empty and exp.opens.empty:
        return pd.DataFrame()

    delivered = (
        exp.delivers.groupby("numeric_id")["email"].nunique()
        if not exp.delivers.empty else pd.Series(dtype=int)
    ).rename("delivers")

    uniq_opens = (
        exp.opens.groupby("numeric_id")["email"].nunique()
        if not exp.opens.empty else pd.Series(dtype=int)
    ).rename("unique_opens")

    total_opens = (
        exp.opens.groupby("numeric_id").size()
        if not exp.opens.empty else pd.Series(dtype=int)
    ).rename("total_opens")

    eng = pd.concat([delivered, uniq_opens, total_opens], axis=1).fillna(0)
    for c in ("delivers", "unique_opens", "total_opens"):
        eng[c] = eng[c].astype(int)
    eng["open_rate"] = (eng["unique_opens"] / eng["delivers"]).where(eng["delivers"] > 0)

    # attach post metadata
    if not exp.posts.empty:
        meta_cols = [c for c in ("numeric_id", "title", "subtitle", "post_date",
                                 "email_sent_at", "type", "audience", "is_published")
                     if c in exp.posts.columns]
        eng = eng.reset_index().merge(exp.posts[meta_cols], on="numeric_id", how="left")
    else:
        eng = eng.reset_index()

    return eng.sort_values("open_rate", ascending=False, na_position="last")


def leaderboard(exp: Export, min_delivers: int = 30, top: int | None = None) -> pd.DataFrame:
    """Posts ranked by open rate, filtered to those with enough reach to be meaningful."""
    eng = post_engagement(exp)
    if eng.empty:
        return eng
    eng = eng[eng["delivers"] >= min_delivers]
    return eng.head(top) if top else eng


# --------------------------------------------------------------------------- #
# Timing — when does the audience actually open?
# --------------------------------------------------------------------------- #

def open_timing(exp: Export, tz: str = "America/New_York") -> pd.DataFrame:
    """Long DataFrame of open events with local weekday/hour for heatmapping."""
    if exp.opens.empty:
        return pd.DataFrame()
    o = exp.opens.dropna(subset=["timestamp"]).copy()
    local = o["timestamp"].dt.tz_convert(tz)
    o["hour"] = local.dt.hour
    o["weekday"] = local.dt.day_name()
    o["weekday_num"] = local.dt.weekday
    return o


def timing_heatmap(exp: Export, tz: str = "America/New_York") -> pd.DataFrame:
    """weekday x hour matrix of open counts (rows Mon..Sun, cols 0..23)."""
    o = open_timing(exp, tz)
    if o.empty:
        return pd.DataFrame()
    order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    pivot = (o.pivot_table(index="weekday", columns="hour", values="email",
                           aggfunc="count", fill_value=0)
             .reindex(order).fillna(0))
    for h in range(24):
        if h not in pivot.columns:
            pivot[h] = 0
    return pivot[sorted(pivot.columns)]


def best_send_windows(exp: Export, tz: str = "America/New_York", top: int = 5) -> pd.DataFrame:
    """Top weekday+hour windows by open volume — a plain-English 'send at' answer."""
    o = open_timing(exp, tz)
    if o.empty:
        return pd.DataFrame()
    g = (o.groupby(["weekday", "weekday_num", "hour"])
         .size().reset_index(name="opens")
         .sort_values("opens", ascending=False))
    return g.head(top)[["weekday", "hour", "opens"]]


# --------------------------------------------------------------------------- #
# Subject-line teardown — what title patterns win?
# --------------------------------------------------------------------------- #

def subject_line_analysis(exp: Export, min_delivers: int = 30) -> pd.DataFrame:
    """Per-post title features joined to open_rate for correlation."""
    eng = leaderboard(exp, min_delivers=min_delivers)
    if eng.empty or "title" not in eng:
        return pd.DataFrame()
    eng = eng.copy()
    t = eng["title"].fillna("")
    eng["title_len"] = t.str.len()
    eng["word_count"] = t.str.split().map(len)
    eng["has_ticker"] = t.str.contains(TICKER_RE)
    eng["has_question"] = t.str.contains(QUESTION_RE)
    eng["has_number"] = t.str.contains(NUMBER_RE)
    return eng


def feature_lift(exp: Export, min_delivers: int = 30) -> pd.DataFrame:
    """Mean open_rate with vs without each boolean title feature (the 'so what' table)."""
    s = subject_line_analysis(exp, min_delivers=min_delivers)
    if s.empty:
        return pd.DataFrame()
    rows = []
    for feat, label in [("has_ticker", "$TICKER in title"),
                        ("has_question", "Question mark"),
                        ("has_number", "Contains a number")]:
        with_r = s.loc[s[feat], "open_rate"].mean()
        without_r = s.loc[~s[feat], "open_rate"].mean()
        rows.append({
            "feature": label,
            "with": with_r,
            "without": without_r,
            "lift": (with_r - without_r) if pd.notna(with_r) and pd.notna(without_r) else None,
            "n_with": int(s[feat].sum()),
        })
    return pd.DataFrame(rows).sort_values("lift", ascending=False, na_position="last")


# --------------------------------------------------------------------------- #
# Free vs paid, and audience segmentation
# --------------------------------------------------------------------------- #

def audience_performance(exp: Export, min_delivers: int = 30) -> pd.DataFrame:
    """Open rate rolled up by post audience (everyone vs only_paid)."""
    eng = leaderboard(exp, min_delivers=min_delivers)
    if eng.empty or "audience" not in eng:
        return pd.DataFrame()
    g = (eng.groupby("audience")
         .agg(posts=("numeric_id", "count"),
              avg_open_rate=("open_rate", "mean"),
              total_delivers=("delivers", "sum"),
              total_unique_opens=("unique_opens", "sum"))
         .reset_index()
         .sort_values("avg_open_rate", ascending=False))
    return g


# --------------------------------------------------------------------------- #
# Geography & device (only present on opens events)
# --------------------------------------------------------------------------- #

def geo_breakdown(exp: Export, col: str = "country", top: int = 15) -> pd.DataFrame:
    if exp.opens.empty or col not in exp.opens:
        return pd.DataFrame()
    s = exp.opens[col].replace("", pd.NA).dropna()
    return (s.value_counts().head(top).rename_axis(col).reset_index(name="opens"))


def device_breakdown(exp: Export) -> dict[str, pd.DataFrame]:
    out = {}
    for col in ("device_type", "client_os", "client_type"):
        out[col] = geo_breakdown(exp, col=col, top=10)
    return out


# --------------------------------------------------------------------------- #
# One-line headline stats (for the summary strip / auto-report seed)
# --------------------------------------------------------------------------- #

def headline_stats(exp: Export) -> dict:
    eng = post_engagement(exp)
    stats = {
        "n_posts": int(exp.posts["is_published"].sum()) if "is_published" in exp.posts else len(exp.posts),
        "n_subscribers": len(exp.subscribers),
        "n_paid": int(exp.subscribers["is_paid"].sum()) if "is_paid" in exp.subscribers else None,
        "total_opens": int(exp.opens.shape[0]) if not exp.opens.empty else 0,
        "total_delivers": int(exp.delivers.shape[0]) if not exp.delivers.empty else 0,
    }
    scored = eng.dropna(subset=["open_rate"]) if not eng.empty else eng
    scored = scored[scored["delivers"] >= 30] if not scored.empty else scored
    if not scored.empty:
        stats["avg_open_rate"] = float(scored["open_rate"].mean())
        best = scored.iloc[scored["open_rate"].argmax()]
        stats["best_post"] = str(best.get("title", "")) or best["numeric_id"]
        stats["best_post_rate"] = float(best["open_rate"])
    return stats
