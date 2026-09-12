import math
import numpy as np
import pandas as pd
import streamlit as st
import requests
import matplotlib.pyplot as plt


st.set_page_config(
    page_title="RiskPilot · Weather & Terrain",
    page_icon="🌦️",
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

    st.stop()


project = projects[
    project_name
]


city = project.get(
    "city",
    ""
)

country = project.get(
    "country",
    ""
)


baseline = project.get(
    "baseline_risk",
    {}
)


# ============================================================
# WEATHER
# ============================================================

@st.cache_data(ttl=1800)
def load_weather(
    city,
    country
):

    try:

        geo = requests.get(

            "https://geocoding-api.open-meteo.com/v1/search",

            params={
                "name": city,
                "count": 1,
                "language": "en",
                "format": "json"
            },

            timeout=10
        )

        geo.raise_for_status()

        results = geo.json().get(
            "results",
            []
        )

        if not results:

            return None


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
                    "temperature_2m,precipitation,rain",

                "daily":
                    "temperature_2m_max,temperature_2m_min,precipitation_sum",

                "forecast_days":
                    7,

                "timezone":
                    "auto",
            },

            timeout=10
        )

        weather.raise_for_status()

        return weather.json()


    except Exception:

        return None


weather = load_weather(
    city,
    country
)


# ============================================================
# HEADER
# ============================================================

st.title(
    "🌦️ Weather & Terrain"
)

st.caption(
    f"{project_name} · {city}, {country}"
)

st.markdown("---")


# ============================================================
# TWO COLUMNS
# ============================================================

weather_col, terrain_col = st.columns(
    2
)


# ============================================================
# WEATHER
# ============================================================

with weather_col:

    st.header(
        "🌦️ Weather"
    )


    if not weather:

        st.warning(
            "Weather data is currently unavailable."
        )

    else:

        current = weather.get(
            "current",
            {}
        )


        temperature = current.get(
            "temperature_2m"
        )

        precipitation = current.get(
            "precipitation"
        )


        m1, m2 = st.columns(2)


        with m1:

            st.metric(
                "Temperature",
                (
                    f"{temperature} °C"
                    if temperature is not None
                    else "—"
                )
            )


        with m2:

            st.metric(
                "Precipitation",
                (
                    f"{precipitation} mm"
                    if precipitation is not None
                    else "—"
                )
            )


        daily = weather.get(
            "daily",
            {}
        )


        dates = daily.get(
            "time",
            []
        )

        max_temp = daily.get(
            "temperature_2m_max",
            []
        )

        min_temp = daily.get(
            "temperature_2m_min",
            []
        )

        rain = daily.get(
            "precipitation_sum",
            []
        )


        if dates:

            df = pd.DataFrame({

                "Date":
                    pd.to_datetime(
                        dates
                    ),

                "Max":
                    max_temp,

                "Min":
                    min_temp,

                "Rain":
                    rain,
            })


            st.markdown(
                "### 🌡️ Temperature Trend"
            )


            temp_chart = df.set_index(
                "Date"
            )[[
                "Max",
                "Min"
            ]]


            st.line_chart(
                temp_chart,
                height=240
            )


            st.caption(
                "7-day maximum and minimum temperature."
            )


            st.markdown(
                "### 🌧️ Precipitation Trend"
            )


            rain_chart = df.set_index(
                "Date"
            )[[
                "Rain"
            ]]


            st.line_chart(
                rain_chart,
                height=220
            )


            st.caption(
                "7-day accumulated precipitation forecast in mm."
            )


# ============================================================
# TERRAIN
# ============================================================

with terrain_col:

    st.header(
        "⛰️ Terrain"
    )


    terrain = baseline.get(
        "terrain",
        "Unknown"
    )

    drainage = baseline.get(
        "drainage",
        "Unknown"
    )


    st.metric(
        "Terrain Type",
        terrain
    )


    st.metric(
        "Drainage Condition",
        drainage
    )


    st.markdown(
        "### 🗺️ Terrain Risk Contour"
    )


    # --------------------------------------------------------
    # Synthetic terrain surface
    #
    # This is a visualization of terrain risk morphology,
    # not a replacement for a real DEM.
    # --------------------------------------------------------

    x = np.linspace(
        -5,
        5,
        80
    )

    y = np.linspace(
        -5,
        5,
        80
    )

    X, Y = np.meshgrid(
        x,
        y
    )


    if terrain == "Mountainous":

        Z = (
            4 * np.exp(
                -((X + 1) ** 2 + Y ** 2)
                / 4
            )
            +
            3 * np.exp(
                -((X - 2) ** 2 + (Y - 2) ** 2)
                / 2
            )
        )


    elif terrain == "Hilly":

        Z = (
            2.5 * np.sin(
                X / 1.5
            )
            +
            2 * np.cos(
                Y / 2
            )
            +
            5
        )


    elif terrain == "River Valley":

        Z = (
            6
            - 2.5 * np.exp(
                -(X ** 2) / 2
            )
            +
            0.4 * Y
        )


    elif terrain == "Coastal":

        Z = (
            2
            +
            0.25 * X
            +
            0.15 * Y
        )


    else:

        Z = (
            1
            + 0.15 * np.sin(X)
            + 0.15 * np.cos(Y)
        )


    fig, ax = plt.subplots(
        figsize=(6, 5)
    )


    contour = ax.contour(
        X,
        Y,
        Z,
        levels=12
    )


    ax.clabel(
        contour,
        inline=True,
        fontsize=8
    )


    ax.set_title(
        f"Terrain Profile · {terrain}"
    )

    ax.set_xlabel(
        "Relative Site X"
    )

    ax.set_ylabel(
        "Relative Site Y"
    )


    st.pyplot(
        fig,
        use_container_width=True
    )


    st.caption(
        "Contour visualization is a terrain-profile "
        "representation for project analysis."
    )


    if terrain in [
        "Hilly",
        "Mountainous"
    ]:

        st.warning(
            "Slope-related conditions should be monitored, "
            "especially after heavy rainfall."
        )

    elif terrain == "River Valley":

        st.warning(
            "Valley terrain may increase drainage and "
            "water accumulation sensitivity."
        )

    elif terrain == "Coastal":

        st.info(
            "Coastal conditions may require additional "
            "consideration of drainage and wind exposure."
        )

    else:

        st.success(
            "No major terrain-related elevation signal "
            "is currently identified."
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