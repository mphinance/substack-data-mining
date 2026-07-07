"""
playbook.py — Deterministic recommendation engine. No AI key required.

Reads the computed metrics and emits ranked, concrete suggestions: what to write,
how to title it, when to send, where to put the paywall. Every suggestion carries
its evidence (the actual number) and a confidence grade derived from sample size
and effect magnitude — so a rule fired on 4 posts reads weaker than one on 40.

The rules are relative to each author's own data, so this helps anyone's export,
not just one newsletter. Everything degrades gracefully when data is thin.
"""

from __future__ import annotations

import pandas as pd

from ingest import Export
from metrics import (
    audience_performance,
    best_send_windows,
    feature_lift,
    headline_stats,
    subject_line_analysis,
)

CONF_WEIGHT = {"High": 3.0, "Medium": 2.0, "Low": 1.0}


def _sug(area: str, action: str, why: str, confidence: str, effect: float) -> dict:
    """One recommendation. `effect` is a rough magnitude used only for ranking."""
    return {
        "area": area, "action": action, "why": why,
        "confidence": confidence,
        "score": effect * CONF_WEIGHT.get(confidence, 1.0),
    }


def _conf(n: int, effect_pts: float) -> str:
    """Grade confidence from sample size and effect size (in percentage points)."""
    if n >= 8 and effect_pts >= 3:
        return "High"
    if n >= 4 and effect_pts >= 2:
        return "Medium"
    return "Low"


# --------------------------------------------------------------------------- #
# Individual rule families
# --------------------------------------------------------------------------- #

def _title_rules(exp: Export, min_delivers: int) -> list[dict]:
    out = []
    fl = feature_lift(exp, min_delivers=min_delivers)
    if not fl.empty:
        for _, r in fl.iterrows():
            if r["lift"] is None:
                continue
            pts = r["lift"] * 100
            n = int(r["n_with"])
            if abs(pts) < 2 or n < 3:
                continue
            feat = r["feature"]
            conf = _conf(n, abs(pts))
            if pts > 0:
                out.append(_sug(
                    "Titles",
                    f"Use “{feat}” in more titles.",
                    f"Your {n} posts with it open {r['with']:.0%} vs {r['without']:.0%} "
                    f"without — a {pts:+.1f} pt lift.",
                    conf, abs(pts)))
            else:
                out.append(_sug(
                    "Titles",
                    f"Rethink “{feat}” in titles.",
                    f"Posts with it open {r['with']:.0%} vs {r['without']:.0%} "
                    f"without — {pts:+.1f} pts.",
                    conf, abs(pts)))

    # Optimal title length: compare shorter vs longer half by open rate.
    s = subject_line_analysis(exp, min_delivers=min_delivers)
    if not s.empty and len(s) >= 8 and s["open_rate"].notna().sum() >= 8:
        s = s.dropna(subset=["open_rate"])
        med = s["title_len"].median()
        short = s[s["title_len"] <= med]["open_rate"].mean()
        longr = s[s["title_len"] > med]["open_rate"].mean()
        if pd.notna(short) and pd.notna(longr):
            pts = abs(short - longr) * 100
            if pts >= 2:
                better_short = short > longr
                rng = (f"under ~{int(med)} characters" if better_short
                       else f"over ~{int(med)} characters")
                rate = max(short, longr)
                out.append(_sug(
                    "Titles",
                    f"Keep titles {rng}.",
                    f"Those open {rate:.0%} vs {min(short, longr):.0%} for the rest "
                    f"— a {pts:.1f} pt gap.",
                    _conf(len(s), pts), pts))
    return out


def _timing_rules(exp: Export, tz: str) -> list[dict]:
    bw = best_send_windows(exp, tz=tz, top=5)
    if bw.empty:
        return []
    top = bw.iloc[0]
    zone = tz.split("/")[-1].replace("_", " ")
    # Is the top window clearly ahead of the pack?
    lead = top["opens"] / bw["opens"].mean() if bw["opens"].mean() else 1
    conf = "High" if lead > 1.3 else "Medium"
    others = ", ".join(f"{r['weekday']} {int(r['hour'])}:00" for _, r in bw.iloc[1:3].iterrows())
    return [_sug(
        "Timing",
        f"Aim your sends for {top['weekday']} around {int(top['hour'])}:00 {zone}.",
        f"That's your single busiest open window (also strong: {others}). "
        f"Schedule so the email lands shortly before it.",
        conf, 4.0)]


def _topic_rules(topics: pd.DataFrame | None) -> list[dict]:
    if topics is None or topics.empty:
        return []
    t = topics.dropna(subset=["avg_open_rate"])
    if len(t) < 2:
        return []
    t = t.sort_values("avg_open_rate", ascending=False)
    best, worst = t.iloc[0], t.iloc[-1]
    spread = (best["avg_open_rate"] - worst["avg_open_rate"]) * 100
    if spread < 2:
        return []
    conf = _conf(int(min(best["n_posts"], worst["n_posts"])), spread)
    out = [_sug(
        "Topics",
        f"Write more about “{best['label']}”.",
        f"Your strongest theme at {best['avg_open_rate']:.0%} open rate across "
        f"{int(best['n_posts'])} posts (terms: {best['top_terms']}).",
        conf, spread)]
    if spread >= 3:
        out.append(_sug(
            "Topics",
            f"Go lighter on “{worst['label']}”, or retitle it.",
            f"Your weakest theme at {worst['avg_open_rate']:.0%} vs "
            f"{best['avg_open_rate']:.0%} for your best — {spread:.1f} pts behind.",
            conf, spread))
    return out


def _conversion_rules(att: dict | None) -> list[dict]:
    if not att or att.get("attributed", 0) == 0:
        return []
    out = []
    bp = att["by_post"]
    days = att.get("days_to_convert", [])
    if not bp.empty:
        top = bp.iloc[0]
        out.append(_sug(
            "Conversion",
            f"Model paid CTAs on “{top.get('title', top['numeric_id'])}”.",
            f"It's credited with the most conversions ({int(top['conversions'])}) "
            f"among {att['attributed']} traced signups.",
            "Medium", 3.0))
    if days:
        med = sorted(days)[len(days) // 2]
        if med <= 3:
            out.append(_sug(
                "Conversion",
                "Put your paywall nudge in the post itself, not a later drip.",
                f"Readers convert a median of {med:.1f} days after their last open "
                f"— they decide fast, while the post is fresh.",
                "Medium", 2.5))
    return out


def _cadence_rules(exp: Export) -> list[dict]:
    if exp.posts.empty or "post_date" not in exp.posts:
        return []
    d = exp.posts.dropna(subset=["post_date"]).sort_values("post_date")
    if "is_published" in d:
        d = d[d["is_published"]]
    if len(d) < 6:
        return []
    span_days = (d["post_date"].max() - d["post_date"].min()).days or 1
    per_week = len(d) / (span_days / 7)
    gaps = d["post_date"].diff().dt.days.dropna()
    # Consistency: high variability relative to the typical gap.
    if not gaps.empty and gaps.median() > 0 and (gaps.std() / gaps.median()) > 1.2:
        return [_sug(
            "Cadence",
            "Even out your publishing rhythm.",
            f"You average ~{per_week:.1f} posts/week but the gaps are uneven "
            f"(median {gaps.median():.0f} days, some much longer). A steadier "
            f"cadence keeps the list warm.",
            "Low", 1.5)]
    return [_sug(
        "Cadence",
        f"Hold your ~{per_week:.1f} posts/week rhythm.",
        f"You publish consistently (median {gaps.median():.0f} days apart) — that "
        f"regularity is worth protecting.",
        "Low", 1.0)]


def _measurement_note(exp: Export) -> list[dict]:
    stats = headline_stats(exp)
    from metrics import device_breakdown
    dev = device_breakdown(exp).get("device_type")
    if dev is None or dev.empty:
        return []
    total = int(dev["opens"].sum())
    proxy = int(dev.loc[dev["device_type"].isin(["Privacy proxy", "Gmail (proxy)"]), "opens"].sum())
    if total and proxy / total > 0.5:
        return [_sug(
            "Measurement",
            "Compare open rates post-to-post, never treat them as absolute.",
            f"{proxy / total:.0%} of your opens are Apple/Gmail prefetch bots, not "
            f"humans — so relative movement is trustworthy, the headline number isn't.",
            "High", 2.0)]
    return []


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def generate(exp: Export, tz: str = "America/New_York", min_delivers: int = 30,
             topics: pd.DataFrame | None = None, attribution: dict | None = None) -> list[dict]:
    """All recommendations, ranked best-first.

    Pass precomputed `topics` (from content.cluster_topics) and `attribution`
    (from attribution.last_touch) to include those rule families; omit them to
    keep the call fast and dependency-light (e.g. when seeding the report).
    """
    sugs: list[dict] = []
    sugs += _title_rules(exp, min_delivers)
    sugs += _timing_rules(exp, tz)
    sugs += _topic_rules(topics)
    sugs += _conversion_rules(attribution)
    sugs += _cadence_rules(exp)
    sugs += _measurement_note(exp)
    return sorted(sugs, key=lambda s: s["score"], reverse=True)


def render_markdown(sugs: list[dict], top: int | None = None) -> str:
    """Render suggestions as a markdown checklist (used in the report + download)."""
    if not sugs:
        return "_Not enough data yet to make confident recommendations._"
    items = sugs[:top] if top else sugs
    lines = []
    for s in items:
        lines.append(f"- **[{s['area']} · {s['confidence']}]** {s['action']}  \n  _{s['why']}_")
    return "\n".join(lines)
