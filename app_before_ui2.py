import streamlit as st


st.set_page_config(
    page_title="RiskPilot",
    page_icon="🏗️",
    layout="wide"
)


# ============================================================
# PROJECT DATA
# ============================================================

projects = {
    "Kenya": {
        "flag": "🇰🇪",
        "city": "Nairobi",
        "name": "Nairobi Affordable Housing",
        "type": "Residential Construction",
        "progress": 68,
        "risk": "Medium",
        "workers": 320,
        "language": "English / Kiswahili"
    },

    "Brazil": {
        "flag": "🇧🇷",
        "city": "Sao Paulo",
        "name": "Sao Paulo Community Housing",
        "type": "Residential Construction",
        "progress": 42,
        "risk": "Low",
        "workers": 180,
        "language": "Portuguese"
    },

    "Ghana": {
        "flag": "🇬🇭",
        "city": "Accra",
        "name": "Accra Infrastructure Project",
        "type": "Infrastructure",
        "progress": 31,
        "risk": "Medium",
        "workers": 240,
        "language": "English"
    },

    "South Africa": {
        "flag": "🇿🇦",
        "city": "Johannesburg",
        "name": "Johannesburg Public Facility",
        "type": "Public Infrastructure",
        "progress": 55,
        "risk": "High",
        "workers": 290,
        "language": "English"
    }
}


# ============================================================
# HEADER
# ============================================================

st.title("RiskPilot")

st.subheader(
    "AI Construction Risk Management Platform"
)

st.markdown("---")


# ============================================================
# GLOBAL PROJECTS
# ============================================================

st.header("Global Projects")

st.write(
    "Manage construction projects across different "
    "countries and monitor safety, schedule, quality "
    "and environmental risks."
)


# ============================================================
# SUMMARY
# ============================================================

col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric(
        "Countries",
        len(projects)
    )

with col2:
    st.metric(
        "Active Projects",
        len(projects)
    )

with col3:
    st.metric(
        "Total Workers",
        sum(
            p["workers"]
            for p in projects.values()
        )
    )

with col4:
    st.metric(
        "High Risk Projects",
        sum(
            1
            for p in projects.values()
            if p["risk"] == "High"
        )
    )


st.markdown("---")


# ============================================================
# PROJECT LIST
# ============================================================

st.header("Projects by Country")


for country, project in projects.items():

    with st.container(border=True):

        col1, col2 = st.columns([1, 3])

        with col1:

            st.subheader(
                project["flag"]
            )

            st.caption(
                country
            )

        with col2:

            st.subheader(
                project["name"]
            )

            st.write(
                "Location:",
                project["city"]
            )

            st.write(
                "Project type:",
                project["type"]
            )

            st.write(
                "Progress:",
                f'{project["progress"]}%'
            )

            st.write(
                "Workers:",
                project["workers"]
            )

            st.write(
                "Languages:",
                project["language"]
            )

            if project["risk"] == "High":

                st.error(
                    "Risk Level: HIGH"
                )

            elif project["risk"] == "Medium":

                st.warning(
                    "Risk Level: MEDIUM"
                )

            else:

                st.success(
                    "Risk Level: LOW"
                )

            if st.button(
                f'Open {project["city"]} Project',
                key=f"open_{country}",
                use_container_width=True
            ):

                st.session_state[
                    "selected_project"
                ] = project["name"]

                st.session_state[
                    "selected_country"
                ] = country

                st.switch_page(
                    "pages/project_dashboard.py"
                )


# ============================================================
# ADD PROJECT
# ============================================================

st.markdown("---")

st.header("Add New Project")

col1, col2 = st.columns(2)

with col1:

    new_country = st.selectbox(
        "Country",
        [
            "Kenya",
            "Brazil",
            "Ghana",
            "South Africa",
            "Other"
        ]
    )

with col2:

    new_project = st.text_input(
        "Project Name"
    )


if st.button(
    "Create Project",
    use_container_width=True
):

    if new_project.strip():

        st.success(
            f"Project '{new_project}' is ready."
        )

    else:

        st.warning(
            "Please enter a project name."
        )


# ============================================================
# FOOTER
# ============================================================

st.markdown("---")

st.caption(
    "RiskPilot | AI Construction Risk Manager"
)