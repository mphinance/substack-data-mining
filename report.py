"""
report.py — Turn computed metrics into a narrative report.

Two layers:
  1. build_report_data(exp)   — pure: collapse the whole export into a compact,
                                aggregated facts dict (no subscriber PII).
  2. render_markdown(data)    — deterministic narrative. Default. No network,
                                nothing leaves the machine. Always works.
  3. polish_with_ai(data)     — OPTIONAL: rewrite the report into editorial prose
                                with Claude. Sends only the aggregated facts dict
                                (totals + your own post titles), never emails.

The AI layer is opt-in precisely because the app's promise is "processed in
memory, nothing uploaded." Calling the API sends the facts dict off the machine,
so the UI gates it behind explicit consent.
"""

from __future__ import annotations

import os

import playbook
from ingest import Export
from metrics import (
    audience_performance,
    best_send_windows,
    device_breakdown,
    feature_lift,
    geo_breakdown,
    headline_stats,
    leaderboard,
)

AI_MODEL = "claude-opus-4-8"


# --------------------------------------------------------------------------- #
# 1. Facts (pure, aggregated, no PII)
# --------------------------------------------------------------------------- #

def build_report_data(exp: Export, tz: str = "America/New_York",
                      min_delivers: int = 30) -> dict:
    """Everything the report needs, as plain JSON-able values. No emails."""
    stats = headline_stats(exp)
    lb = leaderboard(exp, min_delivers=min_delivers)

    top, bottom = [], []
    if not lb.empty:
        for _, r in lb.head(5).iterrows():
            top.append({"title": str(r.get("title", "")),
                        "open_rate": round(float(r["open_rate"]), 4),
                        "delivers": int(r["delivers"])})
        for _, r in lb.tail(3).iterrows():
            bottom.append({"title": str(r.get("title", "")),
                           "open_rate": round(float(r["open_rate"]), 4),
                           "delivers": int(r["delivers"])})

    windows = []
    bw = best_send_windows(exp, tz=tz)
    if not bw.empty:
        for _, r in bw.iterrows():
            windows.append({"weekday": r["weekday"], "hour": int(r["hour"]),
                            "opens": int(r["opens"])})

    lift = []
    fl = feature_lift(exp, min_delivers=min_delivers)
    if not fl.empty:
        for _, r in fl.iterrows():
            lift.append({"feature": r["feature"],
                         "with": None if r["with"] != r["with"] else round(float(r["with"]), 4),
                         "without": None if r["without"] != r["without"] else round(float(r["without"]), 4),
                         "lift_pts": None if r["lift"] is None else round(float(r["lift"]) * 100, 1),
                         "n_with": int(r["n_with"])})

    audience = []
    ap = audience_performance(exp, min_delivers=min_delivers)
    if not ap.empty:
        for _, r in ap.iterrows():
            audience.append({"audience": r["audience"], "posts": int(r["posts"]),
                             "avg_open_rate": round(float(r["avg_open_rate"]), 4)})

    countries = []
    geo = geo_breakdown(exp, "country", top=5)
    if not geo.empty:
        for _, r in geo.iterrows():
            countries.append({"country": r["country"], "opens": int(r["opens"])})

    # prefetch reality check
    prefetch_pct = None
    dev = device_breakdown(exp).get("device_type")
    if dev is not None and not dev.empty:
        total = int(dev["opens"].sum())
        proxy = int(dev.loc[dev["device_type"].isin(["Privacy proxy", "Gmail (proxy)"]), "opens"].sum())
        prefetch_pct = round(proxy / total, 4) if total else None

    # Fast recommendation set (title/timing/cadence/measurement — no clustering).
    # The Playbook tab computes the full set including topics + conversion.
    suggestions = playbook.generate(exp, tz=tz, min_delivers=min_delivers)

    return {
        "tz": tz,
        "min_delivers": min_delivers,
        "headline": stats,
        "suggestions": suggestions,
        "top_posts": top,
        "bottom_posts": bottom,
        "best_windows": windows,
        "title_feature_lift": lift,
        "audience": audience,
        "top_countries": countries,
        "prefetch_pct": prefetch_pct,
    }


# --------------------------------------------------------------------------- #
# 2. Deterministic narrative (default)
# --------------------------------------------------------------------------- #

def _pct(x) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def render_markdown(d: dict) -> str:
    h = d["headline"]
    lines: list[str] = []
    lines.append("# Newsletter Engagement Report\n")

    # Headline
    subs = h.get("n_subscribers", 0)
    paid = h.get("n_paid")
    paid_txt = f" ({paid} paid)" if paid else ""
    lines.append(
        f"You published **{h.get('n_posts', 0)} posts** to **{subs:,} subscribers**{paid_txt}. "
        f"Across posts with enough reach to measure, your average open rate is "
        f"**{_pct(h.get('avg_open_rate'))}**, on {h.get('total_opens', 0):,} opens "
        f"from {h.get('total_delivers', 0):,} deliveries.\n"
    )

    if h.get("best_post"):
        lines.append(f"🏆 **Top performer:** *{h['best_post']}* at "
                     f"**{_pct(h.get('best_post_rate'))}** open rate.\n")

    # Reality check
    if d.get("prefetch_pct") is not None and d["prefetch_pct"] > 0.5:
        lines.append(
            f"> ⚠️ **Read this before trusting the rates:** {_pct(d['prefetch_pct'])} of your "
            f"opens come from Apple Mail Privacy Protection and Gmail prefetch bots, not humans. "
            f"Use open rate to compare posts against each other, not as an absolute truth.\n"
        )

    # Your next moves — the top recommendations up front
    if d.get("suggestions"):
        lines.append("## Your next moves\n")
        lines.append(playbook.render_markdown(d["suggestions"], top=5))
        lines.append("")

    # What's working
    if d["top_posts"]:
        lines.append("## What's working\n")
        lines.append("Your highest open rates:\n")
        for p in d["top_posts"]:
            lines.append(f"- **{_pct(p['open_rate'])}** — {p['title']}  _(sent to {p['delivers']})_")
        lines.append("")

    # Editorial rules
    strong = [f for f in d["title_feature_lift"] if f["lift_pts"] is not None and f["lift_pts"] >= 2]
    weak = [f for f in d["title_feature_lift"] if f["lift_pts"] is not None and f["lift_pts"] <= -2]
    if strong or weak:
        lines.append("## Title patterns that move open rate\n")
        for f in strong:
            lines.append(f"- ✅ **{f['feature']}** lifts open rate by **{f['lift_pts']:+.1f} pts** "
                         f"({_pct(f['with'])} vs {_pct(f['without'])}, n={f['n_with']} posts). Do more of this.")
        for f in weak:
            lines.append(f"- ⛔ **{f['feature']}** costs you **{f['lift_pts']:+.1f} pts** "
                         f"({_pct(f['with'])} vs {_pct(f['without'])}). Reconsider.")
        lines.append("")

    # Timing
    if d["best_windows"]:
        w = d["best_windows"][0]
        others = ", ".join(f"{x['weekday']} {x['hour']}:00" for x in d["best_windows"][1:3])
        lines.append("## Best time to send\n")
        lines.append(f"Your audience opens most around **{w['weekday']} {w['hour']}:00 {d['tz'].split('/')[-1]}** "
                     f"(also strong: {others}). Schedule sends so they land shortly before these windows.\n")

    # Free vs paid
    if len(d["audience"]) >= 2:
        a = {x["audience"]: x for x in d["audience"]}
        ev = a.get("everyone")
        pd = a.get("only_paid")
        if ev and pd:
            gap = (ev["avg_open_rate"] - pd["avg_open_rate"]) * 100
            verdict = ("Free and paid content perform about the same"
                       if abs(gap) < 3 else
                       f"Free content out-opens paid by {gap:+.1f} pts")
            lines.append("## Free vs paid\n")
            lines.append(f"{verdict} — free {_pct(ev['avg_open_rate'])} across {ev['posts']} posts, "
                         f"paid {_pct(pd['avg_open_rate'])} across {pd['posts']}.\n")

    # Geography
    if d["top_countries"]:
        top_c = ", ".join(f"{c['country']} ({c['opens']:,})" for c in d["top_countries"][:3])
        lines.append("## Where your readers are\n")
        lines.append(f"Top by opens: {top_c}.\n")

    # Weak posts as action items
    if d["bottom_posts"]:
        lines.append("## Worth a second look\n")
        lines.append("Lowest open rates among posts with real reach — study the titles:\n")
        for p in d["bottom_posts"]:
            lines.append(f"- **{_pct(p['open_rate'])}** — {p['title']}")
        lines.append("")

    lines.append("---\n_Generated locally from your Substack export. No data left this machine._")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 3. Optional AI polish
# --------------------------------------------------------------------------- #

def ai_available() -> bool:
    """True if the anthropic SDK is importable and some credential is resolvable."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    # A key in the env is the common case; the SDK can also use an `ant` profile,
    # so absence of the env var isn't proof — but it's the honest default signal.
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


SYSTEM = (
    "You are an analyst writing a punchy, honest engagement report for a Substack "
    "author. You are given aggregated statistics (no personal data). Write a concise, "
    "confident report in markdown: lead with the single most important takeaway, then "
    "cover what's working, the title patterns that move open rate (with the actual "
    "numbers), the best send time, free-vs-paid, and 3 concrete action items. Be "
    "specific and quote the real numbers you're given. Do not invent data. If open "
    "rates are dominated by Apple/Gmail prefetch bots, say so plainly."
)


def polish_with_ai(data: dict, model: str = AI_MODEL) -> str:
    """Rewrite the report as editorial prose via Claude. Sends `data` only.

    Raises on any API/SDK failure so the caller can fall back to the deterministic
    report. Requires the `anthropic` package and a resolvable credential.
    """
    import json

    import anthropic

    client = anthropic.Anthropic()  # resolves ANTHROPIC_API_KEY / ant profile
    draft = render_markdown(data)
    resp = client.messages.create(
        model=model,
        max_tokens=4000,
        thinking={"type": "adaptive"},
        system=SYSTEM,
        messages=[{
            "role": "user",
            "content": (
                "Here are the aggregated stats as JSON:\n\n"
                f"```json\n{json.dumps(data, indent=2)}\n```\n\n"
                "And a deterministic draft you can improve on:\n\n"
                f"{draft}\n\n"
                "Write the final report."
            ),
        }],
    )
    return "".join(b.text for b in resp.content if b.type == "text")
