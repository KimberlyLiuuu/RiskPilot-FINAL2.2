import streamlit as st


st.set_page_config(
    page_title="RiskPilot · Baseline Risk",
    page_icon="📊",
    layout="wide",
)


project_name = st.session_state.get(
    "selected_project"
)

projects = st.session_state.get(
    "projects",
    {}
)


if not project_name or project_name not in projects:

    st.warning(
        "No project selected."
    )

    st.stop()


project = projects[
    project_name
]


baseline = project.get(
    "baseline_risk",
    {}
)


score = baseline.get(
    "score",
    project.get(
        "baseline_score",
        0
    )
)


level = baseline.get(
    "level",
    "LOW"
)


def icon(risk):

    risk = str(
        risk
    ).lower()

    if risk == "high":
        return "🔴"

    if risk == "medium":
        return "🟠"

    return "🟢"


# ============================================================
# HEADER
# ============================================================

st.title(
    "📊 Baseline Risk"
)

st.caption(
    f"{project_name} · "
    f"Environmental risk established at project creation."
)

st.markdown("---")


# ============================================================
# SCORE
# ============================================================

col1, col2 = st.columns(2)


with col1:

    st.metric(
        "Baseline Score",
        f"{score}/100"
    )


with col2:

    st.metric(
        "Baseline Level",
        (
            f"{icon(level)} "
            f"{level}"
        )
    )


# ============================================================
# ENVIRONMENT
# ============================================================

st.markdown("---")

st.header(
    "🌍 Environmental Conditions"
)


c1, c2 = st.columns(2)


with c1:

    st.write(
        f"**Terrain:** "
        f"{baseline.get('terrain', 'Unknown')}"
    )

    st.write(
        f"**Drainage:** "
        f"{baseline.get('drainage', 'Unknown')}"
    )


with c2:

    st.write(
        f"**Rainfall:** "
        f"{baseline.get('rainfall', 'Unknown')}"
    )

    st.write(
        f"**Earthquake:** "
        f"{baseline.get('earthquake', 'Unknown')}"
    )


# ============================================================
# RISK CATEGORIES
# ============================================================

st.markdown("---")

st.header(
    "⚠️ Baseline Risk Categories"
)


risks = baseline.get(
    "risks",
    {}
)


risk_names = {

    "heavy_rainfall":
        "🌧️ Heavy Rainfall",

    "flooding":
        "🌊 Flooding",

    "landslide":
        "⛰️ Landslide",

    "earthquake":
        "🌎 Earthquake",

    "extreme_heat":
        "🌡️ Extreme Heat",

    "strong_wind":
        "💨 Strong Wind",
}


items = list(
    risk_names.items()
)


for start in range(
    0,
    len(items),
    3
):

    row = items[
        start:start + 3
    ]

    cols = st.columns(
        len(row)
    )


    for col, (
        key,
        name
    ) in zip(
        cols,
        row
    ):

        with col:

            with st.container(
                border=True
            ):

                risk = risks.get(
                    key,
                    "LOW"
                )

                st.markdown(
                    f"### {name}"
                )

                st.markdown(
                    f"## "
                    f"{icon(risk)} "
                    f"{risk}"
                )


# ============================================================
# CHAINS
# ============================================================

st.markdown("---")

st.header(
    "🔗 Potential Risk Chains"
)


chains = baseline.get(
    "risk_chains",
    []
)


if chains:

    for chain in chains:

        st.info(
            chain
        )

else:

    st.success(
        "No major environmental risk chain identified."
    )


# ============================================================
# ACTIONS
# ============================================================

st.markdown("---")

st.header(
    "🛡️ Preventive Actions"
)


actions = baseline.get(
    "actions",
    []
)


for action in actions:

    st.write(
        f"• {action}"
    )


# ============================================================
# BACK
# ============================================================

st.markdown("---")

if st.button(
    "← Back to Dashboard"
):

    st.switch_page(
        "pages/project_dashboard.py"
    )