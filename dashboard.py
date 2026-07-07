"""
Newsletter Engagement Dashboard — v2.0

Upload a Substack export .zip and it goes to work: post leaderboard, best send
times, subject-line teardown, audience split, and geo/device breakdown — all
computed from the per-post opens/delivers files that v0.1 never touched.
"""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import playbook
from attribution import last_touch
from content import cluster_topics
from ingest import load_export
from report import ai_available, build_report_data, polish_with_ai, render_markdown
from metrics import (
    audience_performance,
    best_send_windows,
    device_breakdown,
    feature_lift,
    geo_breakdown,
    headline_stats,
    leaderboard,
    subject_line_analysis,
    timing_heatmap,
)

st.set_page_config(page_title="Newsletter Engagement Dashboard",
                   page_icon="📈", layout="wide", initial_sidebar_state="expanded")

ACCENT = "#FF4B4B"
PLOTLY_TMPL = "plotly_dark"


@st.cache_data(show_spinner="Parsing export…")
def _load(file_bytes: bytes):
    return load_export(file_bytes)


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.header("📥 Load your data")
    st.markdown(
        "1. Substack → **Settings → Exports → New export**\n"
        "2. Download the `.zip`\n"
        "3. Drop it below."
    )
    uploaded = st.file_uploader("Substack export (.zip)", type="zip")
    st.divider()
    tz = st.selectbox("Timezone for timing analysis",
                      ["America/New_York", "America/Chicago", "America/Denver",
                       "America/Los_Angeles", "UTC", "Europe/London", "Europe/Amsterdam"],
                      index=0)
    min_delivers = st.slider("Min. deliveries to rank a post", 0, 200, 30, step=10,
                             help="Filters out tiny sends whose open rates are noise.")
    st.divider()
    st.caption("🔒 Processed in memory. Nothing is uploaded or stored.")


st.title("📈 Newsletter Engagement Dashboard")

if not uploaded:
    st.info("👈 Upload your Substack export zip to begin.")
    st.markdown(
        "**What you'll get:** which posts actually got opened, the best day/hour to "
        "send, which title patterns lift open rate, free-vs-paid performance, and where "
        "your readers are — plus an honest read on how much of your 'open rate' is real."
    )
    st.stop()

exp = _load(uploaded.getvalue())

if not exp.ok:
    for w in exp.warnings:
        st.error(w)
    st.stop()
for w in exp.warnings:
    st.warning(w)

stats = headline_stats(exp)

# --------------------------------------------------------------------------- #
# Headline strip
# --------------------------------------------------------------------------- #
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Published posts", f"{stats['n_posts']:,}")
c2.metric("Subscribers", f"{stats['n_subscribers']:,}")
c3.metric("Paid", f"{stats.get('n_paid') or 0:,}")
c4.metric("Avg open rate", f"{stats.get('avg_open_rate', 0):.1%}")
c5.metric("Total opens", f"{stats['total_opens']:,}")

if stats.get("best_post"):
    st.success(f"🏆 **Top performer:** *{stats['best_post']}* — "
               f"{stats['best_post_rate']:.1%} open rate")

# Apple/Gmail prefetch reality check
dev = device_breakdown(exp).get("device_type", pd.DataFrame())
if not dev.empty:
    total = dev["opens"].sum()
    proxy = dev.loc[dev["device_type"].isin(["Privacy proxy", "Gmail (proxy)"]), "opens"].sum()
    if total and proxy / total > 0.5:
        st.warning(
            f"⚠️ **Open-rate reality check:** {proxy/total:.0%} of your opens come from "
            f"Apple Mail Privacy Protection / Gmail prefetch bots, not humans. Treat open "
            f"rate as a *relative* signal (post vs post), not an absolute truth."
        )

(tab_play, tab_report, tab_board, tab_timing, tab_titles, tab_conv, tab_topics,
 tab_aud, tab_geo, tab_growth) = st.tabs(
    ["🎯 Playbook", "📋 Report", "🏆 Leaderboard", "🕐 Best Send Time", "✍️ Subject Lines",
     "💰 Conversions", "🧬 Topics", "👥 Free vs Paid", "🌍 Geo & Devices", "📈 Growth"]
)

# --------------------------------------------------------------------------- #
# Playbook — the ranked, evidence-backed recommendations (no AI key needed)
# --------------------------------------------------------------------------- #
_AREA_ICON = {"Titles": "✍️", "Timing": "🕐", "Topics": "🧬", "Conversion": "💰",
              "Cadence": "🗓️", "Measurement": "📏"}
_CONF_COLOR = {"High": "#00D26A", "Medium": "#FFB020", "Low": "#8A8F98"}

with tab_play:
    st.subheader("What to do next")
    st.caption("Ranked, evidence-backed moves computed entirely from your own numbers — "
               "no AI, nothing leaves your machine. Higher confidence = bigger effect on more posts.")

    @st.cache_data(show_spinner="Reading your data…")
    def _playbook(_sig, tz_, md):
        topics = cluster_topics(exp, k=6, min_delivers=md)
        att = last_touch(exp)
        return playbook.generate(exp, tz=tz_, min_delivers=md, topics=topics, attribution=att)

    sugs = _playbook((len(exp.opens), len(exp.subscribers)), tz, min_delivers)
    if not sugs:
        st.info("Not enough data yet to make confident recommendations.")
    else:
        areas = ["All"] + sorted({s["area"] for s in sugs})
        pick = st.radio("Filter", areas, horizontal=True, label_visibility="collapsed")
        shown = [s for s in sugs if pick == "All" or s["area"] == pick]

        for s in shown:
            icon = _AREA_ICON.get(s["area"], "•")
            color = _CONF_COLOR.get(s["confidence"], "#8A8F98")
            st.markdown(
                f"""<div style="border:1px solid #3a3f4b;border-left:4px solid {color};
                border-radius:8px;padding:12px 16px;margin-bottom:10px;background:#1c1e26;">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                <span style="font-size:1.02rem;font-weight:600;">{icon}&nbsp; {s['action']}</span>
                <span style="font-size:.72rem;color:{color};border:1px solid {color};
                border-radius:10px;padding:1px 9px;white-space:nowrap;">{s['confidence']} · {s['area']}</span>
                </div>
                <div style="color:#b6bcc7;font-size:.9rem;margin-top:5px;">{s['why']}</div>
                </div>""",
                unsafe_allow_html=True)

        st.download_button("⬇ Download playbook (.md)",
                           playbook.render_markdown(sugs),
                           file_name="playbook.md", mime="text/markdown")

# --------------------------------------------------------------------------- #
# Auto-report — "upload a zip and it goes to work"
# --------------------------------------------------------------------------- #
with tab_report:
    st.subheader("Your engagement report")
    st.caption("Generated locally from the numbers in the other tabs. Nothing leaves "
               "your machine unless you choose the AI rewrite below.")
    data = build_report_data(exp, tz=tz, min_delivers=min_delivers)

    # Session cache so an AI rewrite survives reruns until inputs change.
    sig = (len(exp.opens), len(exp.subscribers), tz, min_delivers)
    if st.session_state.get("report_sig") != sig:
        st.session_state["report_sig"] = sig
        st.session_state["report_md"] = render_markdown(data)
        st.session_state["report_is_ai"] = False

    b1, b2, b3 = st.columns([1, 1, 2])
    with b1:
        if st.button("↻ Rebuild", use_container_width=True):
            st.session_state["report_md"] = render_markdown(data)
            st.session_state["report_is_ai"] = False
    with b2:
        st.download_button("⬇ Download .md", st.session_state["report_md"],
                           file_name="engagement_report.md", mime="text/markdown",
                           use_container_width=True)
    with b3:
        if ai_available():
            if st.button("✨ Rewrite with Claude", use_container_width=True):
                with st.spinner("Claude is writing…"):
                    try:
                        st.session_state["report_md"] = polish_with_ai(data)
                        st.session_state["report_is_ai"] = True
                    except Exception as e:  # fall back to the local report
                        st.error(f"AI rewrite failed ({e}). Showing the local report.")
        else:
            st.caption("💡 Set `ANTHROPIC_API_KEY` to enable an AI-written rewrite "
                       "(sends aggregated stats only — no emails).")

    if st.session_state.get("report_is_ai"):
        st.caption("✨ Rewritten by Claude from your aggregated stats.")
    st.markdown(st.session_state["report_md"])

# --------------------------------------------------------------------------- #
# Leaderboard
# --------------------------------------------------------------------------- #
with tab_board:
    st.subheader("Posts ranked by open rate")
    lb = leaderboard(exp, min_delivers=min_delivers)
    if lb.empty:
        st.info("No posts meet the delivery threshold — lower it in the sidebar.")
    else:
        show = lb[["title", "post_date", "audience", "delivers",
                   "unique_opens", "open_rate"]].copy()
        show["post_date"] = pd.to_datetime(show["post_date"]).dt.strftime("%Y-%m-%d")
        st.dataframe(
            show, use_container_width=True, hide_index=True,
            column_config={
                "title": "Post",
                "post_date": "Date",
                "audience": "Audience",
                "delivers": st.column_config.NumberColumn("Delivered"),
                "unique_opens": st.column_config.NumberColumn("Opens"),
                "open_rate": st.column_config.ProgressColumn(
                    "Open rate", format="%.1f%%", min_value=0, max_value=1),
            },
        )
        top10 = lb.head(10).sort_values("open_rate")
        fig = px.bar(top10, x="open_rate", y="title", orientation="h",
                     template=PLOTLY_TMPL, labels={"open_rate": "Open rate", "title": ""})
        fig.update_traces(marker_color=ACCENT)
        fig.update_layout(xaxis_tickformat=".0%", height=420, margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, use_container_width=True)

# --------------------------------------------------------------------------- #
# Timing
# --------------------------------------------------------------------------- #
with tab_timing:
    st.subheader(f"When your audience opens ({tz})")
    heat = timing_heatmap(exp, tz=tz)
    if heat.empty:
        st.info("No open events with timestamps found.")
    else:
        fig = px.imshow(heat, aspect="auto", template=PLOTLY_TMPL,
                        color_continuous_scale="Reds",
                        labels=dict(x="Hour of day", y="", color="Opens"))
        fig.update_layout(height=360, margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("**Top send windows** (by open volume)")
        win = best_send_windows(exp, tz=tz)
        win = win.assign(window=win["weekday"] + " @ " + win["hour"].astype(str) + ":00")
        st.dataframe(win[["window", "opens"]], hide_index=True, use_container_width=True)

# --------------------------------------------------------------------------- #
# Subject lines
# --------------------------------------------------------------------------- #
with tab_titles:
    st.subheader("What title patterns lift open rate?")
    lift = feature_lift(exp, min_delivers=min_delivers)
    if lift.empty:
        st.info("Not enough scored posts.")
    else:
        lc = st.columns(len(lift))
        for col, (_, row) in zip(lc, lift.iterrows()):
            delta = None if row["lift"] is None else f"{row['lift']*100:+.1f} pts"
            col.metric(row["feature"],
                       "—" if pd.isna(row["with"]) else f"{row['with']:.1%}",
                       delta, help=f"vs {row['without']:.1%} without · n={row['n_with']} posts")

        s = subject_line_analysis(exp, min_delivers=min_delivers)
        try:
            import statsmodels  # noqa: F401  (enables the OLS trendline)
            trend = "ols"
        except ImportError:
            trend = None
        fig = px.scatter(s, x="title_len", y="open_rate", hover_name="title",
                         color="has_ticker", template=PLOTLY_TMPL, trendline=trend,
                         labels={"title_len": "Title length (chars)", "open_rate": "Open rate",
                                 "has_ticker": "$TICKER"})
        fig.update_layout(yaxis_tickformat=".0%", height=420, margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, use_container_width=True)

# --------------------------------------------------------------------------- #
# Conversions — last-touch attribution
# --------------------------------------------------------------------------- #
with tab_conv:
    st.subheader("What converts free readers to paid?")
    st.caption("Last-touch model: the post each paid subscriber opened most recently "
               "*before* their first payment gets the credit. Aggregated — no emails shown.")
    att = last_touch(exp)
    if att["total_paid_with_date"] == 0:
        st.info("No paid subscribers with a payment date in this export.")
    else:
        k1, k2, k3 = st.columns(3)
        k1.metric("Paid subs (dated)", att["total_paid_with_date"])
        k2.metric("Traced to a post", att["attributed"])
        k3.metric("No pre-payment open", att["no_pre_open"],
                  help="Paid without opening a tracked post first (e.g. comped, or "
                       "signed up and paid immediately).")

        days = att["days_to_convert"]
        if days:
            med = sorted(days)[len(days) // 2]
            st.markdown(f"**Speed to convert:** median **{med:.1f} days** from last "
                        f"open to payment — readers decide fast.")
            fig = px.histogram(x=days, nbins=20, template=PLOTLY_TMPL,
                               labels={"x": "Days between last open and payment"})
            fig.update_traces(marker_color=ACCENT)
            fig.update_layout(height=260, margin=dict(l=0, r=0, t=10, b=0),
                              yaxis_title="Conversions")
            st.plotly_chart(fig, use_container_width=True)

        bp = att["by_post"]
        if not bp.empty:
            st.markdown("**Posts credited with the most conversions**")
            top = bp.head(12).sort_values("conversions")
            fig = px.bar(top, x="conversions", y="title", orientation="h",
                         template=PLOTLY_TMPL, labels={"conversions": "Conversions", "title": ""})
            fig.update_traces(marker_color="#00D26A")
            fig.update_layout(height=420, margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig, use_container_width=True)

# --------------------------------------------------------------------------- #
# Topics — content clustering
# --------------------------------------------------------------------------- #
with tab_topics:
    st.subheader("Which themes get opened?")
    st.caption("Your post bodies, TF-IDF-clustered into topics and joined to open rate "
               "(weighted by reach). Labels are the most distinctive terms per cluster.")
    k = st.slider("Number of topics", 3, 10, 6)

    @st.cache_data(show_spinner="Clustering post bodies…")
    def _topics(_sig, kk, md):
        return cluster_topics(exp, k=kk, min_delivers=md)

    topics = _topics((len(exp.html_by_id),), k, min_delivers)
    if topics.empty:
        st.info("Not enough post bodies to cluster.")
    else:
        plot = topics.dropna(subset=["avg_open_rate"]).sort_values("avg_open_rate")
        fig = px.bar(plot, x="avg_open_rate", y="label", orientation="h",
                     template=PLOTLY_TMPL, text="n_posts",
                     labels={"avg_open_rate": "Avg open rate", "label": ""})
        fig.update_traces(marker_color=ACCENT, texttemplate="%{text} posts", textposition="outside")
        fig.update_layout(xaxis_tickformat=".0%", height=380, margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, use_container_width=True)

        for _, row in topics.iterrows():
            rate = "n/a" if pd.isna(row["avg_open_rate"]) else f"{row['avg_open_rate']:.1%}"
            with st.expander(f"🧬 {row['label']}  —  {row['n_posts']} posts · {rate} open rate"):
                st.caption(f"Distinctive terms: {row['top_terms']}")
                for ex in row["examples"]:
                    st.markdown(f"- {ex}")

# --------------------------------------------------------------------------- #
# Free vs paid
# --------------------------------------------------------------------------- #
with tab_aud:
    st.subheader("Free vs paid content performance")
    ap = audience_performance(exp, min_delivers=min_delivers)
    if ap.empty:
        st.info("No audience breakdown available.")
    else:
        fig = px.bar(ap, x="audience", y="avg_open_rate", template=PLOTLY_TMPL,
                     text_auto=".1%", labels={"audience": "", "avg_open_rate": "Avg open rate"})
        fig.update_traces(marker_color=ACCENT)
        fig.update_layout(yaxis_tickformat=".0%", height=360, margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(ap, hide_index=True, use_container_width=True)

# --------------------------------------------------------------------------- #
# Geo & devices
# --------------------------------------------------------------------------- #
with tab_geo:
    st.subheader("Where & how they read")
    g1, g2 = st.columns(2)
    with g1:
        st.markdown("**Top countries**")
        geo = geo_breakdown(exp, "country", top=12)
        if not geo.empty:
            fig = px.bar(geo.sort_values("opens"), x="opens", y="country",
                         orientation="h", template=PLOTLY_TMPL)
            fig.update_traces(marker_color=ACCENT)
            fig.update_layout(height=380, margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig, use_container_width=True)
    with g2:
        st.markdown("**Mail client / device**")
        dt = device_breakdown(exp)["client_os"]
        if not dt.empty:
            fig = px.pie(dt, values="opens", names="client_os", template=PLOTLY_TMPL, hole=0.5)
            fig.update_layout(height=380, margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig, use_container_width=True)

# --------------------------------------------------------------------------- #
# Growth (carried over from v0.1, with an honesty caveat)
# --------------------------------------------------------------------------- #
with tab_growth:
    st.subheader("Subscriber growth")
    st.caption("⚠️ Built from *current* subscribers only — people who unsubscribed are "
               "absent from the export, so this curve understates churn.")
    subs = exp.subscribers.dropna(subset=["created_at"]).sort_values("created_at")
    if subs.empty:
        st.info("No subscriber dates available.")
    else:
        daily = (subs.set_index("created_at").resample("D").size()
                 .cumsum().reset_index(name="subscribers"))
        fig = px.line(daily, x="created_at", y="subscribers", template=PLOTLY_TMPL,
                      labels={"created_at": "", "subscribers": ""})
        fig.update_traces(line_color=ACCENT, line_width=3)
        posts = exp.posts.dropna(subset=["post_date"])
        if not posts.empty:
            merged = pd.merge_asof(posts.sort_values("post_date"), daily,
                                   left_on="post_date", right_on="created_at",
                                   direction="nearest")
            fig.add_trace(go.Scatter(
                x=merged["post_date"], y=merged["subscribers"], mode="markers",
                name="Post", text=merged["title"],
                hovertemplate="<b>%{text}</b><br>%{x|%Y-%m-%d}<extra></extra>",
                marker=dict(color="#00D26A", size=8, symbol="x")))
        fig.update_layout(hovermode="x unified", height=440, margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, use_container_width=True)
