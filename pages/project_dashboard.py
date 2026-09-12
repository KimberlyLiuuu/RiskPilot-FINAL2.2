import os
import requests
import pandas as pd
import streamlit as st

from datetime import datetime, date, timedelta


st.set_page_config(
    page_title="RiskPilot · Project Dashboard",
    page_icon="🏗️",
    layout="wide",
)


# ============================================================
# PROJECT
# ============================================================

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

    if st.button(
        "← Back to Projects"
    ):

        st.switch_page(
            "pages/projects.py"
        )

    st.stop()


project = projects[
    project_name
]


# ============================================================
# HELPERS
# ============================================================

def risk_icon(risk):

    risk = str(risk).lower()

    if risk == "high":
        return "🔴"

    if risk == "medium":
        return "🟠"

    return "🟢"


def risk_score(risk):

    risk = str(risk).lower()

    if risk == "high":
        return 3

    if risk == "medium":
        return 2

    return 1


def risk_label(score):

    if score >= 70:
        return "High"

    if score >= 45:
        return "Medium"

    return "Low"


def save_projects():

    try:

        import json

        os.makedirs(
            "data",
            exist_ok=True
        )

        with open(
            "data/projects.json",
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                st.session_state.projects,
                f,
                ensure_ascii=False,
                indent=2
            )

    except Exception as e:

        st.error(
            f"Unable to save project data: {e}"
        )


# ============================================================
# WEATHER
# ============================================================

@st.cache_data(ttl=1800)
def get_weather(
    city,
    country
):

    try:

        geo_response = requests.get(

            "https://geocoding-api.open-meteo.com/v1/search",

            params={
                "name": city,
                "count": 1,
                "language": "en",
                "format": "json"
            },

            timeout=10
        )

        geo_response.raise_for_status()

        geo_data = geo_response.json()

        results = geo_data.get(
            "results",
            []
        )

        if not results:

            return None


        latitude = results[0]["latitude"]
        longitude = results[0]["longitude"]


        weather_response = requests.get(

            "https://api.open-meteo.com/v1/forecast",

            params={

                "latitude":
                    latitude,

                "longitude":
                    longitude,

                "current":
                    "temperature_2m,precipitation,rain",

                "hourly":
                    "temperature_2m,precipitation_probability,precipitation",

                "daily":
                    "temperature_2m_max,temperature_2m_min,precipitation_sum",

                "forecast_days":
                    7,

                "timezone":
                    "auto",
            },

            timeout=10
        )

        weather_response.raise_for_status()

        data = weather_response.json()


        return {

            "latitude":
                latitude,

            "longitude":
                longitude,

            "current":
                data.get(
                    "current",
                    {}
                ),

            "hourly":
                data.get(
                    "hourly",
                    {}
                ),

            "daily":
                data.get(
                    "daily",
                    {}
                ),
        }


    except Exception:

        return None


weather = get_weather(
    project.get("city", ""),
    project.get("country", "")
)


# ============================================================
# BASELINE
# ============================================================

baseline = project.get(
    "baseline_risk",
    {}
)


baseline_score = int(
    project.get(
        "baseline_score",
        baseline.get(
            "score",
            0
        )
    )
)


baseline_level = baseline.get(
    "level",
    "LOW"
).title()


# ============================================================
# DAILY LOGS
# ============================================================

daily_logs = project.get(
    "daily_logs",
    []
)


# ============================================================
# CURRENT RISK ENGINE
# ============================================================

def calculate_current_risk():

    score = baseline_score


    # --------------------------------------------------------
    # Weather contribution
    # --------------------------------------------------------

    weather_signal = 0


    if weather:

        current = weather.get(
            "current",
            {}
        )

        temperature = current.get(
            "temperature_2m"
        )

        precipitation = current.get(
            "precipitation",
            0
        )


        if temperature is not None:

            if temperature >= 38:

                weather_signal += 15

            elif temperature >= 34:

                weather_signal += 8


        if precipitation is not None:

            if precipitation >= 20:

                weather_signal += 15

            elif precipitation >= 5:

                weather_signal += 8


    # --------------------------------------------------------
    # Daily log contribution
    # --------------------------------------------------------

    log_signal = 0


    recent_logs = daily_logs[
        -7:
    ]


    for log in recent_logs:

        log_risk = log.get(
            "risk",
            "Low"
        )

        log_signal += (
            risk_score(log_risk)
            - 1
        ) * 5


        incidents = int(
            log.get(
                "incidents",
                0
            )
        )


        log_signal += min(
            incidents * 5,
            15
        )


    # Prevent daily logs from exploding score.

    log_signal = min(
        log_signal,
        20
    )


    # --------------------------------------------------------
    # Final score
    # --------------------------------------------------------

    score = min(
        100,
        score
        + weather_signal
        + log_signal
    )


    level = risk_label(
        score
    )


    return {

        "score":
            score,

        "level":
            level,

        "baseline_signal":
            baseline_score,

        "weather_signal":
            weather_signal,

        "log_signal":
            log_signal,
    }


current_risk = calculate_current_risk()


# Update project risk
project["risk"] = current_risk[
    "level"
]

save_projects()


# ============================================================
# HEADER
# ============================================================

country = project.get(
    "country",
    "Unknown"
)

city = project.get(
    "city",
    "Unknown"
)

project_type = project.get(
    "type",
    "Construction"
)

workers = project.get(
    "workers",
    0
)


st.title(
    f"🏗️ {project_name}"
)

st.caption(
    f"📍 {city}, {country}"
    f"  ·  {project_type}"
)


# ============================================================
# PROJECT OVERVIEW
# ============================================================

st.markdown("---")

st.header(
    "📊 Project Overview"
)


overview_col1, overview_col2, overview_col3 = (
    st.columns(3)
)


with overview_col1:

    st.metric(
        "Overall Risk",
        (
            f"{risk_icon(current_risk['level'])} "
            f"{current_risk['level'].upper()}"
        )
    )

    st.caption(
        f"Risk score: "
        f"{current_risk['score']}/100"
    )


with overview_col2:

    st.metric(
        "Workers",
        f"{workers:,}"
    )

    st.caption(
        "Current project workforce"
    )


with overview_col3:

    st.metric(
        "Daily Logs",
        len(daily_logs)
    )

    st.caption(
        "Recorded project workdays"
    )


# ============================================================
# RISK EXPLANATION
# ============================================================

st.markdown("")

signal_col1, signal_col2, signal_col3 = st.columns(3)


with signal_col1:

    st.markdown(
        "### 🧱 Baseline"
    )

    st.metric(
        "Baseline Score",
        f"{baseline_score}/100"
    )


with signal_col2:

    st.markdown(
        "### 🌦️ Weather"
    )

    st.metric(
        "Weather Signal",
        f"+{current_risk['weather_signal']}"
    )


with signal_col3:

    st.markdown(
        "### 📝 Daily Logs"
    )

    st.metric(
        "Log Signal",
        f"+{current_risk['log_signal']}"
    )


st.caption(
    "Current Risk Picture combines the project's "
    "original baseline with recent weather conditions "
    "and recorded daily logs."
)


# ============================================================
# MODULE GRID
# ============================================================

st.markdown("---")

st.header(
    "🧭 Project Intelligence"
)

st.caption(
    "Choose what you want to investigate."
)


module1, module2 = st.columns(2)


with module1:

    with st.container(
        border=True
    ):

        st.markdown(
            "## 🌦️ Weather & Terrain"
        )

        st.caption(
            "Current weather, temperature, rainfall, "
            "precipitation trends and terrain context."
        )

        if weather:

            temp = weather.get(
                "current",
                {}
            ).get(
                "temperature_2m"
            )

            precipitation = weather.get(
                "current",
                {}
            ).get(
                "precipitation"
            )

            if temp is not None:

                st.metric(
                    "Temperature",
                    f"{temp} °C"
                )

            if precipitation is not None:

                st.caption(
                    f"Precipitation: "
                    f"{precipitation} mm"
                )


        if st.button(
            "Open Weather & Terrain →",
            key="open_weather",
            use_container_width=True
        ):

            st.switch_page(
                "pages/weather_terrain.py"
            )


with module2:

    with st.container(
        border=True
    ):

        st.markdown(
            "## 📊 Baseline Risk"
        )

        st.caption(
            "The environmental risk picture established "
            "when the project was created."
        )

        st.metric(
            "Baseline",
            f"{baseline_score}/100"
        )

        st.caption(
            baseline_level
        )


        if st.button(
            "Open Baseline →",
            key="open_baseline",
            use_container_width=True
        ):

            st.switch_page(
                "pages/baseline_risk.py"
            )


module3, module4 = st.columns(2)


with module3:

    with st.container(
        border=True
    ):

        st.markdown(
            "## 🚨 Current Risk Picture"
        )

        st.caption(
            "Risk after combining baseline, "
            "weather and recent daily logs."
        )

        st.metric(
            "Current Risk",
            (
                f"{risk_icon(current_risk['level'])} "
                f"{current_risk['level']}"
            )
        )

        st.caption(
            f"{current_risk['score']}/100"
        )


        if st.button(
            "Open Current Risk →",
            key="open_current_risk",
            use_container_width=True
        ):

            st.switch_page(
                "pages/current_risk.py"
            )


with module4:

    with st.container(
        border=True
    ):

        st.markdown(
            "## 📝 Daily Logs"
        )

        st.caption(
            "Record what actually happened on site "
            "and see how risk changes over time."
        )

        st.metric(
            "Recorded Logs",
            len(daily_logs)
        )


        if daily_logs:

            latest = daily_logs[-1]

            st.caption(
                f"Latest: "
                f"{latest.get('date', '-')}"
                f" · "
                f"{risk_icon(latest.get('risk'))} "
                f"{latest.get('risk', 'Low')}"
            )


        if st.button(
            "Open Daily Logs →",
            key="open_logs",
            use_container_width=True
        ):

            st.switch_page(
                "pages/daily_logs.py"
            )


# ============================================================
# QUICK INSIGHT
# ============================================================

st.markdown("---")

st.header(
    "💡 What Needs Attention?"
)


attention = []


if current_risk["weather_signal"] > 0:

    attention.append(
        "Current weather is increasing the project's risk signal."
    )


if current_risk["log_signal"] > 0:

    attention.append(
        "Recent daily logs contain risk signals."
    )


if baseline_score >= 70:

    attention.append(
        "The project's original environmental baseline is already high."
    )


if not attention:

    attention.append(
        "No additional risk signal has been detected beyond the baseline."
    )


for item in attention:

    st.info(
        item
    )


# ============================================================
# BACK
# ============================================================

st.markdown("---")

if st.button(
    "← Back to Projects"
):

    st.switch_page(
        "pages/projects.py"
    )