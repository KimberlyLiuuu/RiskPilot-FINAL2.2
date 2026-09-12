import streamlit as st


st.set_page_config(
    page_title="RiskPilot · Current Risk",
    page_icon="🚨",
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


def risk_icon(risk):

    risk = str(
        risk
    ).lower()

    if risk == "high":
        return "🔴"

    if risk == "medium":
        return "🟠"

    return "🟢"


def risk_score(risk):

    risk = str(
        risk
    ).lower()

    if risk == "high":
        return 3

    if risk == "medium":
        return 2

    return 1


baseline_score = int(
    project.get(
        "baseline_score",
        project.get(
            "baseline_risk",
            {}
        ).get(
            "score",
            0
        )
    )
)


daily_logs = project.get(
    "daily_logs",
    []
)


# ============================================================
# SIGNALS
# ============================================================

weather_signal = 0


try:

    import requests

    city = project.get(
        "city",
        ""
    )


    geo = requests.get(

        "https://geocoding-api.open-meteo.com/v1/search",

        params={
            "name": city,
            "count": 1,
            "language": "en",
            "format": "json"
        },

        timeout=5
    ).json()


    results = geo.get(
        "results",
        []
    )


    if results:

        lat = results[0]["latitude"]
        lon = results[0]["longitude"]


        weather = requests.get(

            "https://api.open-meteo.com/v1/forecast",

            params={

                "latitude":
                    lat,

                "longitude":
                    lon,

                "current":
                    "temperature_2m,precipitation",

                "timezone":
                    "auto",
            },

            timeout=5
        ).json()


        current = weather.get(
            "current",
            {}
        )


        temp = current.get(
            "temperature_2m"
        )

        precipitation = current.get(
            "precipitation",
            0
        )


        if temp is not None:

            if temp >= 38:

                weather_signal += 15

            elif temp >= 34:

                weather_signal += 8


        if precipitation >= 20:

            weather_signal += 15

        elif precipitation >= 5:

            weather_signal += 8


except Exception:

    pass


log_signal = 0


for log in daily_logs[-7:]:

    log_signal += (
        risk_score(
            log.get(
                "risk",
                "Low"
            )
        ) - 1
    ) * 5


    log_signal += min(
        int(
            log.get(
                "incidents",
                0
            )
        ) * 5,
        15
    )


log_signal = min(
    log_signal,
    20
)


current_score = min(
    100,
    baseline_score
    + weather_signal
    + log_signal
)


if current_score >= 70:

    current_level = "High"

elif current_score >= 45:

    current_level = "Medium"

else:

    current_level = "Low"


# ============================================================
# HEADER
# ============================================================

st.title(
    "🚨 Current Risk Picture"
)

st.caption(
    f"{project_name}"
)

st.markdown("---")


# ============================================================
# MAIN SCORE
# ============================================================

score_col1, score_col2 = st.columns(
    [1, 2]
)


with score_col1:

    st.metric(
        "Current Risk",
        (
            f"{risk_icon(current_level)} "
            f"{current_level.upper()}"
        )
    )

    st.metric(
        "Score",
        f"{current_score}/100"
    )


with score_col2:

    st.markdown(
        "### How the risk is being formed"
    )

    st.write(
        "Current Risk is not an independent risk assessment."
    )

    st.write(
        "It is generated from the project's baseline "
        "environmental risk, current weather conditions, "
        "and recent site logs."
    )


# ============================================================
# THREE SIGNALS
# ============================================================

st.markdown("---")

st.header(
    "🔍 Risk Signals"
)


c1, c2, c3 = st.columns(3)


with c1:

    st.markdown(
        "### 🧱 Baseline"
    )

    st.metric(
        "Environmental Starting Point",
        f"{baseline_score}/100"
    )


with c2:

    st.markdown(
        "### 🌦️ Weather"
    )

    st.metric(
        "Current Weather Signal",
        f"+{weather_signal}"
    )


with c3:

    st.markdown(
        "### 📝 Daily Logs"
    )

    st.metric(
        "Recent Site Signal",
        f"+{log_signal}"
    )


# ============================================================
# DAILY TREND
# ============================================================

st.markdown("---")

st.header(
    "📈 Daily Risk Trend"
)


if daily_logs:

    trend = []


    for log in daily_logs:

        value = (
            risk_score(
                log.get(
                    "risk",
                    "Low"
                )
            )
            * 33
        )


        trend.append({

            "Date":
                log.get(
                    "date",
                    ""
                ),

            "Risk":
                value
        })


    import pandas as pd


    df = pd.DataFrame(
        trend
    )


    st.line_chart(
        df.set_index(
            "Date"
        )
    )

else:

    st.info(
        "Daily risk trend will appear after daily logs are recorded."
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