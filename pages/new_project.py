import streamlit as st

from datetime import date


st.set_page_config(
    page_title="RiskPilot · New Project",
    page_icon="➕",
    layout="wide",
)


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


def calculate_baseline(
    terrain,
    drainage,
    rainfall,
    earthquake,
    heavy_rain,
    flooding,
    landslide,
    earthquake_hazard,
    extreme_heat,
    strong_wind,
    drought,
    wildfire,
    other_hazard
):

    score = 20


    # Rainfall
    if rainfall == "Moderate":
        score += 5

    elif rainfall == "High":
        score += 12

    elif rainfall == "Extreme":
        score += 20


    # Drainage
    if drainage == "Moderate":
        score += 5

    elif drainage == "Poor":
        score += 15


    # Terrain
    if terrain == "Hilly":
        score += 8

    elif terrain == "Mountainous":
        score += 12

    elif terrain == "River Valley":
        score += 10

    elif terrain == "Coastal":
        score += 6


    # Earthquake
    if earthquake == "Moderate":
        score += 8

    elif earthquake == "High":
        score += 15


    # Hazards
    if heavy_rain:
        score += 8

    if flooding:
        score += 12

    if landslide:
        score += 12

    if earthquake_hazard:
        score += 10

    if extreme_heat:
        score += 5

    if strong_wind:
        score += 5

    if drought:
        score += 4

    if wildfire:
        score += 5

    if other_hazard:
        score += 5


    score = min(
        score,
        100
    )


    if score >= 70:
        level = "HIGH"

    elif score >= 45:
        level = "MEDIUM"

    else:
        level = "LOW"


    # --------------------------------------------------------
    # Category risks
    # --------------------------------------------------------

    if rainfall == "Extreme" or heavy_rain:

        rainfall_level = "HIGH"

    elif rainfall == "High":

        rainfall_level = "MEDIUM"

    else:

        rainfall_level = "LOW"


    if flooding or drainage == "Poor":

        flooding_level = "HIGH"

    elif drainage == "Moderate":

        flooding_level = "MEDIUM"

    else:

        flooding_level = "LOW"


    if landslide and terrain in [
        "Hilly",
        "Mountainous",
        "River Valley"
    ]:

        landslide_level = "HIGH"

    elif landslide or terrain in [
        "Hilly",
        "Mountainous"
    ]:

        landslide_level = "MEDIUM"

    else:

        landslide_level = "LOW"


    if earthquake == "High" or earthquake_hazard:

        earthquake_level = "HIGH"

    elif earthquake == "Moderate":

        earthquake_level = "MEDIUM"

    else:

        earthquake_level = "LOW"


    heat_level = (
        "MEDIUM"
        if extreme_heat
        else "LOW"
    )

    wind_level = (
        "MEDIUM"
        if strong_wind
        else "LOW"
    )


    # --------------------------------------------------------
    # Risk chains
    # --------------------------------------------------------

    risk_chains = []


    if heavy_rain or rainfall in [
        "High",
        "Extreme"
    ]:

        risk_chains.append(
            "Heavy Rainfall → Increased Surface Runoff → "
            "Drainage Pressure → Site Water Accumulation → "
            "Safety + Schedule + Quality Risks"
        )


    if flooding:

        risk_chains.append(
            "Flooding → Construction Area Inundation → "
            "Equipment / Material Damage → "
            "Work Interruption → Schedule Delay"
        )


    if landslide:

        risk_chains.append(
            "Heavy Rainfall → Soil Saturation → "
            "Slope Instability → Landslide → "
            "Worker + Equipment Safety Risk"
        )


    if earthquake_hazard:

        risk_chains.append(
            "Earthquake → Ground Movement → "
            "Structural / Equipment Damage → "
            "Work Interruption → Safety + Schedule Risk"
        )


    # --------------------------------------------------------
    # Impacts
    # --------------------------------------------------------

    impacts = []


    if heavy_rain or rainfall in [
        "High",
        "Extreme"
    ]:

        impacts.append(
            "Weather-sensitive outdoor work may be interrupted."
        )


    if flooding or drainage == "Poor":

        impacts.append(
            "Excavation areas may accumulate water."
        )


    if landslide:

        impacts.append(
            "Excavation and slope stability may require additional monitoring."
        )


    if earthquake_hazard:

        impacts.append(
            "Structural and equipment safety may be affected by ground movement."
        )


    if extreme_heat:

        impacts.append(
            "Extreme heat may increase worker fatigue and reduce productivity."
        )


    if strong_wind:

        impacts.append(
            "Strong winds may affect lifting operations and temporary structures."
        )


    if drought:

        impacts.append(
            "Construction water availability may require additional planning."
        )


    if wildfire:

        impacts.append(
            "Emergency evacuation and fire response planning may be required."
        )


    if not impacts:

        impacts.append(
            "No major environmental construction impact has been identified."
        )


    # --------------------------------------------------------
    # Actions
    # --------------------------------------------------------

    actions = []


    if heavy_rain or rainfall in [
        "High",
        "Extreme"
    ]:

        actions.append(
            "Monitor weather conditions and prepare temporary rain protection."
        )


    if flooding or drainage == "Poor":

        actions.append(
            "Inspect drainage systems and prepare emergency pumping equipment."
        )


    if landslide:

        actions.append(
            "Inspect slopes and excavation edges after heavy rainfall."
        )


    if earthquake_hazard:

        actions.append(
            "Review seismic emergency procedures and structural requirements."
        )


    if extreme_heat:

        actions.append(
            "Plan appropriate rest periods and monitor worker heat exposure."
        )


    if strong_wind:

        actions.append(
            "Establish wind-related suspension criteria for lifting operations."
        )


    if drought:

        actions.append(
            "Review construction water availability and emergency supply plans."
        )


    if wildfire:

        actions.append(
            "Review emergency evacuation and fire response procedures."
        )


    actions.append(
        "Add identified environmental risks to the project risk register."
    )


    return {

        "score": score,
        "level": level,

        "terrain": terrain,
        "drainage": drainage,
        "rainfall": rainfall,
        "earthquake": earthquake,

        "risks": {

            "heavy_rainfall":
                rainfall_level,

            "flooding":
                flooding_level,

            "landslide":
                landslide_level,

            "earthquake":
                earthquake_level,

            "extreme_heat":
                heat_level,

            "strong_wind":
                wind_level,
        },

        "selected_hazards": {

            "heavy_rainfall":
                heavy_rain,

            "flooding":
                flooding,

            "landslide":
                landslide,

            "earthquake":
                earthquake_hazard,

            "extreme_heat":
                extreme_heat,

            "strong_wind":
                strong_wind,

            "drought":
                drought,

            "wildfire":
                wildfire,

            "other":
                other_hazard,
        },

        "risk_chains":
            risk_chains,

        "impacts":
            impacts,

        "actions":
            actions,
    }


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.markdown(
        "## 🧭 RiskPilot"
    )

    if st.button(
        "🏠 Global Overview",
        use_container_width=True
    ):

        st.switch_page(
            "app.py"
        )

    if st.button(
        "🌍 Projects",
        use_container_width=True
    ):

        st.switch_page(
            "pages/projects.py"
        )


# ============================================================
# PAGE
# ============================================================

st.title(
    "＋ New Project"
)

st.caption(
    "Create a project and establish its environmental baseline."
)

st.markdown("---")


# ============================================================
# PROJECT INFORMATION
# ============================================================

st.header(
    "1. Project Information"
)

col1, col2 = st.columns(2)


with col1:

    new_country = st.text_input(
        "🌍 Country",
        placeholder="e.g. Japan"
    )

    new_city = st.text_input(
        "📍 City",
        placeholder="e.g. Tokyo"
    )

    new_project_type = st.selectbox(
        "🏗️ Project Type",
        [
            "Residential Construction",
            "Infrastructure",
            "Commercial",
            "Hospital",
            "School",
            "Industrial",
            "Transportation",
            "Energy",
            "Other",
        ]
    )


with col2:

    new_project_name = st.text_input(
        "📋 Project Name",
        placeholder="e.g. Tokyo Urban Housing Project"
    )

    new_workers = st.number_input(
        "👷 Number of Workers",
        min_value=0,
        max_value=100000,
        value=100,
        step=10
    )

    new_project_scale = st.selectbox(
        "📐 Project Scale",
        [
            "Small",
            "Medium",
            "Large",
            "Mega Project"
        ]
    )


col1, col2 = st.columns(2)


with col1:

    new_start_date = st.date_input(
        "📅 Planned Start Date",
        value=date.today()
    )


with col2:

    new_end_date = st.date_input(
        "📅 Planned End Date",
        value=date.today()
    )


st.markdown("---")


# ============================================================
# ENVIRONMENT
# ============================================================

st.header(
    "2. Environmental Baseline"
)

col1, col2 = st.columns(2)


with col1:

    new_terrain = st.selectbox(
        "⛰️ Terrain",
        [
            "Flat",
            "Hilly",
            "Mountainous",
            "Coastal",
            "River Valley",
            "Urban",
            "Unknown"
        ]
    )

    new_drainage = st.selectbox(
        "💧 Drainage Condition",
        [
            "Good",
            "Moderate",
            "Poor",
            "Unknown"
        ]
    )


with col2:

    new_rainfall = st.selectbox(
        "🌧️ Seasonal Rainfall",
        [
            "Low",
            "Moderate",
            "High",
            "Extreme"
        ]
    )

    new_earthquake = st.selectbox(
        "🌎 Earthquake Exposure",
        [
            "Low",
            "Moderate",
            "High",
            "Unknown"
        ]
    )


st.markdown("---")


# ============================================================
# HAZARDS
# ============================================================

st.header(
    "3. Potential Natural Hazards"
)

col1, col2, col3 = st.columns(3)


with col1:

    heavy_rain = st.checkbox(
        "🌧️ Heavy Rainfall"
    )

    flooding = st.checkbox(
        "🌊 Flooding"
    )

    landslide = st.checkbox(
        "⛰️ Landslide"
    )


with col2:

    earthquake_hazard = st.checkbox(
        "🌎 Earthquake"
    )

    extreme_heat = st.checkbox(
        "🌡️ Extreme Heat"
    )

    strong_wind = st.checkbox(
        "💨 Strong Wind"
    )


with col3:

    drought = st.checkbox(
        "☀️ Drought"
    )

    wildfire = st.checkbox(
        "🔥 Wildfire"
    )

    other_hazard = st.checkbox(
        "⚠️ Other Hazard"
    )


st.markdown("---")


# ============================================================
# CREATE
# ============================================================

if st.button(
    "🚀 Create Project",
    type="primary",
    use_container_width=True
):

    if not new_country.strip():

        st.error(
            "Please enter a country."
        )

    elif not new_city.strip():

        st.error(
            "Please enter a city."
        )

    elif not new_project_name.strip():

        st.error(
            "Please enter a project name."
        )

    elif (
        new_project_name
        in st.session_state.projects
    ):

        st.error(
            "A project with this name already exists."
        )

    elif new_end_date < new_start_date:

        st.error(
            "Planned End Date cannot be earlier than Start Date."
        )

    else:

        baseline = calculate_baseline(

            terrain=new_terrain,

            drainage=new_drainage,

            rainfall=new_rainfall,

            earthquake=new_earthquake,

            heavy_rain=heavy_rain,

            flooding=flooding,

            landslide=landslide,

            earthquake_hazard=
                earthquake_hazard,

            extreme_heat=extreme_heat,

            strong_wind=strong_wind,

            drought=drought,

            wildfire=wildfire,

            other_hazard=other_hazard
        )


        project = {

            "country":
                new_country.strip(),

            "city":
                new_city.strip(),

            "type":
                new_project_type,

            "workers":
                int(new_workers),

            "start_date":
                str(new_start_date),

            "end_date":
                str(new_end_date),

            "project_scale":
                new_project_scale,

            "baseline_score":
                baseline["score"],

            "baseline_risk":
                baseline,

            "daily_logs":
                [],

            "risk":
                baseline["level"].title(),
        }


        st.session_state.projects[
            new_project_name.strip()
        ] = project


        st.session_state.selected_project = (
            new_project_name.strip()
        )


        # Save permanently
        try:

            from pathlib import Path

            Path("data").mkdir(
                exist_ok=True
            )

            with open(
                "data/projects.json",
                "w",
                encoding="utf-8"
            ) as f:

                import json

                json.dump(
                    st.session_state.projects,
                    f,
                    ensure_ascii=False,
                    indent=2
                )

        except Exception as e:

            st.error(
                f"Project created but could not be saved: {e}"
            )


        st.success(
            f"✅ {new_project_name} created successfully!"
        )


        st.info(
            f"Baseline Risk: "
            f"{risk_icon(baseline['level'])} "
            f"{baseline['level']} · "
            f"{baseline['score']}/100"
        )


        st.switch_page(
            "pages/project_dashboard.py"
        )