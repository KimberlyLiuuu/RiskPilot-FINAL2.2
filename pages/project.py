import streamlit as st


st.set_page_config(
    page_title="RiskPilot · Projects",
    page_icon="🌍",
    layout="wide",
)


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.markdown(
        "## 🧭 RiskPilot"
    )

    if st.button(
        "＋ New Project",
        type="primary",
        use_container_width=True
    ):

        st.switch_page(
            "pages/new_project.py"
        )


    if st.button(
        "🏠 Global Overview",
        use_container_width=True
    ):

        st.switch_page(
            "app.py"
        )


# ============================================================
# DATA
# ============================================================

projects = st.session_state.get(
    "projects",
    {}
)


def risk_icon(risk):

    risk = str(risk).lower()

    if risk == "high":
        return "🔴"

    if risk == "medium":
        return "🟠"

    return "🟢"


# ============================================================
# HEADER
# ============================================================

st.title(
    "🌍 Projects"
)

st.caption(
    "All construction projects in the RiskPilot portfolio."
)

st.markdown("---")


# ============================================================
# PROJECTS
# ============================================================

if not projects:

    st.info(
        "No projects yet."
    )

else:

    items = list(
        projects.items()
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
            project_name,
            project
        ) in zip(
            cols,
            row
        ):

            with col:

                risk = project.get(
                    "risk",
                    "Low"
                )

                with st.container(
                    border=True
                ):

                    st.markdown(
                        f"## "
                        f"{risk_icon(risk)} "
                        f"{project_name}"
                    )

                    st.write(
                        f"📍 "
                        f"{project.get('city', '')}, "
                        f"{project.get('country', '')}"
                    )

                    st.write(
                        f"🏗️ "
                        f"{project.get('type', '')}"
                    )

                    st.write(
                        f"👷 "
                        f"{project.get('workers', 0):,} workers"
                    )

                    baseline = project.get(
                        "baseline_score",
                        0
                    )

                    st.metric(
                        "Baseline",
                        f"{baseline}/100"
                    )

                    logs = project.get(
                        "daily_logs",
                        []
                    )

                    st.caption(
                        f"📝 "
                        f"{len(logs)} daily log(s)"
                    )


                    if st.button(
                        "Open Dashboard →",
                        key=
                            f"projects_open_{project_name}",
                        use_container_width=True
                    ):

                        st.session_state.selected_project = (
                            project_name
                        )

                        st.switch_page(
                            "pages/project_dashboard.py"
                        )