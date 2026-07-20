"""Craftsman dashboard — campaigns, leads, unified inbox, bandit convergence viz.

Run: streamlit run dashboard/app.py
Works in two modes:
  - live: reads Postgres via DATABASE_URL
  - demo: no DB needed; renders the bandit simulator converging (the money-shot)
"""

import os

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="Craftsman", page_icon="🔨", layout="wide")

ARM_COLORS = ["#4C78A8", "#F58518", "#54A24B", "#B279A2", "#E45756"]


def beta_pdf_trace(alpha: float, beta: float, name: str, color: str, x_max: float = 0.25):
    from math import exp, lgamma, log

    xs = np.linspace(1e-6, x_max, 200)
    log_b = lgamma(alpha) + lgamma(beta) - lgamma(alpha + beta)
    ys = np.array([exp((alpha - 1) * log(x) + (beta - 1) * log(1 - x) - log_b) for x in xs])
    return go.Scatter(
        x=xs, y=ys, name=f"{name}  Beta({alpha:.0f},{beta:.0f})",
        mode="lines", line={"color": color, "width": 2.5}, fill="tozeroy",
        opacity=0.85,
    )


def posterior_chart(arms: list[dict], title: str) -> go.Figure:
    fig = go.Figure()
    for i, arm in enumerate(arms):
        fig.add_trace(
            beta_pdf_trace(arm["alpha"], arm["beta"], arm["name"], ARM_COLORS[i % len(ARM_COLORS)])
        )
    fig.update_layout(
        title=title,
        xaxis_title="reply rate",
        yaxis_title="posterior density",
        template="plotly_white",
        legend={"orientation": "h", "y": -0.2},
        margin={"t": 50, "b": 20},
        height=380,
    )
    return fig


# ---------------------------------------------------------------- data access


@st.cache_resource
def get_engine():
    from sqlalchemy import create_engine

    url = os.environ.get("DATABASE_URL", "postgresql+psycopg://craftsman:craftsman@localhost:5432/craftsman")
    return create_engine(url, pool_pre_ping=True)


def try_query(sql: str) -> pd.DataFrame | None:
    try:
        return pd.read_sql(sql, get_engine())
    except Exception:
        return None


# ---------------------------------------------------------------- pages


def page_overview():
    st.title("🔨 Craftsman")
    st.caption("Open-source AI SDR with a Thompson-sampling learning loop.")

    stats = try_query(
        """
        SELECT
          (SELECT count(*) FROM messages WHERE direction='outbound') AS sent,
          (SELECT count(*) FROM messages WHERE direction='inbound'
             AND classification IN ('interested','objection','not_now')) AS replies,
          (SELECT count(*) FROM messages WHERE classification='interested') AS interested,
          (SELECT count(*) FROM review_queue WHERE kind='copywriter') AS copy_rejections
        """
    )
    if stats is None:
        st.warning("Database not reachable — showing demo mode. Set DATABASE_URL for live data.")
        return
    row = stats.iloc[0]
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Sent", int(row.sent))
    c2.metric("Replies", int(row.replies))
    c3.metric("Interested", int(row.interested))
    rate = (row.replies / row.sent) if row.sent else 0
    c4.metric("Reply rate", f"{rate:.1%}")
    c5.metric(
        "Copy rejections", int(row.copy_rejections),
        help="Slot fills blocked by the deterministic validator — the public proof of the anti-hallucination gate.",
    )

    states = try_query("SELECT state, count(*) AS n FROM enrollments GROUP BY state ORDER BY n DESC")
    if states is not None and not states.empty:
        st.subheader("Enrollment states")
        st.bar_chart(states.set_index("state"))


def page_bandit():
    st.title("Bandit — live posteriors")
    st.caption(
        "Each copy variant is a Beta posterior over reply rate. "
        "Thompson sampling routes traffic to the winner while staying honest about uncertainty."
    )

    arms_df = try_query(
        """
        SELECT v.id, v.name, v.alpha, v.beta, v.active, s.step_order,
               c.name AS campaign
        FROM variants v
        JOIN sequence_steps s ON v.step_id = s.id
        JOIN campaigns c ON s.campaign_id = c.id
        ORDER BY c.name, s.step_order
        """
    )
    if arms_df is None or arms_df.empty:
        st.info("No live variants yet — demo below shows the simulator converging.")
        demo_convergence()
        return

    for (campaign, step), group in arms_df.groupby(["campaign", "step_order"]):
        arms = group.to_dict("records")
        st.plotly_chart(
            posterior_chart(arms, f"{campaign} — step {step}"),
            use_container_width=True,
        )
        table = group[["name", "alpha", "beta", "active"]].copy()
        table["trials"] = (table.alpha + table.beta - 2).astype(int)
        table["posterior_mean"] = (table.alpha / (table.alpha + table.beta)).round(4)
        st.dataframe(table, hide_index=True, use_container_width=True)

    st.divider()
    st.subheader("Simulator")
    demo_convergence()


def demo_convergence():
    """Replay the simulator so you can watch posteriors converge — zero real emails."""
    from craftsman.bandit.simulator import SimArm, simulate

    col1, col2, col3 = st.columns(3)
    rate_a = col1.slider("true rate: pain_led", 0.0, 0.15, 0.06, 0.005)
    rate_b = col2.slider("true rate: trigger_led", 0.0, 0.15, 0.02, 0.005)
    n_sends = col3.slider("simulated sends", 100, 2000, 500, 100)

    result = simulate(
        [SimArm("pain_led", rate_a), SimArm("trigger_led", rate_b)],
        n_sends=n_sends, seed=42, snapshot_every=max(10, n_sends // 40),
    )

    snap_idx = st.slider("watch it converge →", 0, len(result.history) - 1, len(result.history) - 1)
    snap = result.history[snap_idx]
    arms = [
        {"name": name, "alpha": snap[name]["alpha"], "beta": snap[name]["beta"]}
        for name in ("pain_led", "trigger_led")
    ]
    st.plotly_chart(
        posterior_chart(arms, f"after {snap['send']} sends"), use_container_width=True
    )
    traffic = {name: snap[name]["traffic"] for name in ("pain_led", "trigger_led")}
    total = sum(traffic.values()) or 1
    st.caption(
        f"traffic split: pain_led {traffic['pain_led']} ({traffic['pain_led']/total:.0%}) · "
        f"trigger_led {traffic['trigger_led']} ({traffic['trigger_led']/total:.0%})"
    )


def page_inbox():
    st.title("Unified inbox")
    label = st.selectbox(
        "Filter", ["all", "interested", "objection", "not_now", "ooo", "unsubscribe", "bounce_or_auto"]
    )
    where = "" if label == "all" else f"AND m.classification = '{label}'"
    df = try_query(
        f"""
        SELECT l.email, m.subject, m.body, m.classification,
               m.classification_confidence AS confidence
        FROM messages m
        JOIN enrollments e ON m.enrollment_id = e.id
        JOIN leads l ON e.lead_id = l.id
        WHERE m.direction = 'inbound' {where}
        ORDER BY m.id DESC LIMIT 200
        """
    )
    if df is None or df.empty:
        st.info("No inbound messages yet.")
        return
    st.dataframe(df, hide_index=True, use_container_width=True)

    review = try_query(
        "SELECT kind, payload, created_at FROM review_queue WHERE NOT resolved ORDER BY created_at DESC LIMIT 50"
    )
    if review is not None and not review.empty:
        st.subheader("Human review queue")
        st.dataframe(review, hide_index=True, use_container_width=True)


def page_leads():
    st.title("Leads")
    df = try_query(
        """
        SELECT l.email, l.first_name, l.last_name, l.title, l.status,
               l.icp_score, l.email_verified, c.domain
        FROM leads l LEFT JOIN companies c ON l.company_id = c.id
        ORDER BY l.icp_score DESC NULLS LAST LIMIT 500
        """
    )
    if df is None or df.empty:
        st.info("No leads yet — POST a CSV to /leads/import.")
        return
    st.dataframe(df, hide_index=True, use_container_width=True)


PAGES = {
    "Overview": page_overview,
    "Bandit": page_bandit,
    "Inbox": page_inbox,
    "Leads": page_leads,
}

choice = st.sidebar.radio("Craftsman", list(PAGES.keys()))
PAGES[choice]()
