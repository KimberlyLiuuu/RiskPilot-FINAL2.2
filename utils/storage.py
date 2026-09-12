import copy
import json
import os
import re
import uuid
from datetime import datetime

import requests


# ============================================================
# PATHS
# ============================================================

BASE_DIR = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        ".."
    )
)

DATA_DIR = os.path.join(
    BASE_DIR,
    "data"
)

DATA_FILE = os.path.join(
    DATA_DIR,
    "riskpilot_data.json"
)


# ============================================================
# TIME
# ============================================================

def _now():

    return datetime.now().isoformat(
        timespec="seconds"
    )


# ============================================================
# DEFAULT PROJECTS
# ============================================================

def _default_projects():

    return {

        "Nairobi Affordable Housing": {

            "country": "Kenya",

            "city": "Nairobi",

            "type":
                "Residential Construction",

            "progress": 68,

            "risk": "Medium",

            "workers": 320,

            "address":
                "Nairobi, Kenya",

            "latitude":
                -1.286389,

            "longitude":
                36.817223,

            "elevation_m":
                1795,

            "terrain_profile":
                "Highland / elevated urban terrain",

            "logs": [],

            "baseline_score":
                58,

            "baseline_risk": {

                "level":
                    "MEDIUM",

                "score":
                    58,

                "risks": {

                    "heavy_rainfall":
                        "MEDIUM",

                    "flooding":
                        "MEDIUM",

                    "landslide":
                        "LOW",

                    "earthquake":
                        "LOW",

                    "extreme_heat":
                        "LOW",

                    "strong_wind":
                        "LOW",

                    "terrain":
                        "MEDIUM",
                },

                "risk_chains": [],

                "impacts": [],

                "actions": [],
            },
        },


        "Nairobi Infrastructure": {

            "country":
                "Kenya",

            "city":
                "Nairobi",

            "type":
                "Infrastructure",

            "progress":
                52,

            "risk":
                "Low",

            "workers":
                180,

            "address":
                "Nairobi, Kenya",

            "latitude":
                -1.286389,

            "longitude":
                36.817223,

            "elevation_m":
                1795,

            "terrain_profile":
                "Highland / elevated urban terrain",

            "logs": [],

            "baseline_score":
                38,

            "baseline_risk": {

                "level":
                    "LOW",

                "score":
                    38,

                "risks": {},

                "risk_chains": [],

                "impacts": [],

                "actions": [],
            },
        },


        "São Paulo Infrastructure": {

            "country":
                "Brazil",

            "city":
                "São Paulo",

            "type":
                "Infrastructure",

            "progress":
                61,

            "risk":
                "Medium",

            "workers":
                210,

            "address":
                "São Paulo, Brazil",

            "latitude":
                -23.55052,

            "longitude":
                -46.633308,

            "elevation_m":
                760,

            "terrain_profile":
                "Elevated plateau / urban terrain",

            "logs": [],

            "baseline_score":
                52,

            "baseline_risk": {

                "level":
                    "MEDIUM",

                "score":
                    52,

                "risks": {

                    "heavy_rainfall":
                        "MEDIUM",

                    "flooding":
                        "MEDIUM",

                    "landslide":
                        "LOW",

                    "earthquake":
                        "LOW",

                    "extreme_heat":
                        "LOW",

                    "strong_wind":
                        "LOW",

                    "terrain":
                        "MEDIUM",
                },

                "risk_chains": [],

                "impacts": [],

                "actions": [],
            },
        },


        "Brazil Housing Project": {

            "country":
                "Brazil",

            "city":
                "São Paulo",

            "type":
                "Housing Construction",

            "progress":
                44,

            "risk":
                "Low",

            "workers":
                150,

            "address":
                "São Paulo, Brazil",

            "latitude":
                -23.55052,

            "longitude":
                -46.633308,

            "elevation_m":
                760,

            "terrain_profile":
                "Elevated plateau / urban terrain",

            "logs": [],

            "baseline_score":
                35,

            "baseline_risk": {

                "level":
                    "LOW",

                "score":
                    35,

                "risks": {},

                "risk_chains": [],

                "impacts": [],

                "actions": [],
            },
        },


        "Johannesburg Project": {

            "country":
                "South Africa",

            "city":
                "Johannesburg",

            "type":
                "Construction",

            "progress":
                73,

            "risk":
                "Medium",

            "workers":
                260,

            "address":
                "Johannesburg, South Africa",

            "latitude":
                -26.204103,

            "longitude":
                28.047305,

            "elevation_m":
                1753,

            "terrain_profile":
                "Highland / elevated urban terrain",

            "logs": [],

            "baseline_score":
                49,

            "baseline_risk": {

                "level":
                    "MEDIUM",

                "score":
                    49,

                "risks": {

                    "heavy_rainfall":
                        "MEDIUM",

                    "flooding":
                        "MEDIUM",

                    "landslide":
                        "MEDIUM",

                    "earthquake":
                        "LOW",

                    "extreme_heat":
                        "LOW",

                    "strong_wind":
                        "LOW",

                    "terrain":
                        "MEDIUM",
                },

                "risk_chains": [],

                "impacts": [],

                "actions": [],
            },
        },


        "Cape Town Housing": {

            "country":
                "South Africa",

            "city":
                "Cape Town",

            "type":
                "Housing",

            "progress":
                38,

            "risk":
                "Low",

            "workers":
                140,

            "address":
                "Cape Town, South Africa",

            "latitude":
                -33.924868,

            "longitude":
                18.424055,

            "elevation_m":
                25,

            "terrain_profile":
                "Coastal / low elevation terrain",

            "logs": [],

            "baseline_score":
                34,

            "baseline_risk": {

                "level":
                    "LOW",

                "score":
                    34,

                "risks": {},

                "risk_chains": [],

                "impacts": [],

                "actions": [],
            },
        },
    }


# ============================================================
# DATABASE
# ============================================================

def _default_db():

    return {

        "version": 2,

        "projects":
            _default_projects(),

        "updated_at":
            _now(),
    }


def save_db(db):

    os.makedirs(
        DATA_DIR,
        exist_ok=True
    )

    db["updated_at"] = _now()

    tmp_file = (
        DATA_FILE +
        ".tmp"
    )

    with open(
        tmp_file,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(

            db,

            f,

            ensure_ascii=False,

            indent=2
        )

    os.replace(
        tmp_file,
        DATA_FILE
    )


def load_db():

    os.makedirs(
        DATA_DIR,
        exist_ok=True
    )

    if not os.path.exists(
        DATA_FILE
    ):

        db = _default_db()

        save_db(
            db
        )

        return db


    try:

        with open(
            DATA_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            db = json.load(
                f
            )


        if not isinstance(
            db,
            dict
        ):

            raise ValueError(
                "Invalid database"
            )


        db.setdefault(
            "version",
            2
        )


        db.setdefault(
            "projects",
            {}
        )


        return db


    except Exception:

        backup = (
            DATA_FILE +
            ".broken"
        )

        try:

            os.replace(
                DATA_FILE,
                backup
            )

        except Exception:

            pass


        db = _default_db()

        save_db(
            db
        )

        return db


# ============================================================
# PROJECT STORAGE
# ============================================================

def get_projects():

    return load_db().get(
        "projects",
        {}
    )


def save_projects(projects):

    db = load_db()

    db["projects"] = projects

    save_db(
        db
    )


def get_project(name):

    return get_projects().get(
        name
    )


def upsert_project(
    name,
    project
):

    db = load_db()

    db.setdefault(
        "projects",
        {}
    )[name] = project

    save_db(
        db
    )


# ============================================================
# RISK HELPERS
# ============================================================

def risk_score(risk):

    value = str(
        risk
    ).lower()

    if value == "high":

        return 3

    if value == "medium":

        return 2

    return 1


def risk_icon(risk):

    value = str(
        risk
    ).lower()

    if value == "high":

        return "🔴"

    if value == "medium":

        return "🟠"

    return "🟢"


# ============================================================
# ELEVATION / TERRAIN
# ============================================================

def classify_elevation(
    elevation
):

    if elevation is None:

        return "Unknown"

    if elevation < 100:

        return (
            "Coastal / "
            "low elevation terrain"
        )

    if elevation < 500:

        return (
            "Low-relief / "
            "rolling terrain"
        )

    if elevation < 1000:

        return (
            "Elevated plateau / "
            "hilly terrain"
        )

    return (
        "Highland / "
        "elevated terrain"
    )


def terrain_risk_from_elevation(
    elevation
):

    if elevation is None:

        return "LOW"

    if elevation >= 1500:

        return "MEDIUM"

    return "LOW"


# ============================================================
# GEOCODING
# ============================================================

def geocode_address(
    address,
    city="",
    country=""
):

    query = (
        address.strip()
        if address
        and address.strip()
        else
        f"{city}, {country}"
    )


    if not query.strip():

        return None


    try:

        response = requests.get(

            "https://nominatim.openstreetmap.org/search",

            params={

                "q":
                    query,

                "format":
                    "jsonv2",

                "addressdetails":
                    1,

                "limit":
                    1,
            },

            headers={

                "User-Agent":
                    "RiskPilot Construction Risk Manager/2.0"
            },

            timeout=15,
        )


        response.raise_for_status()


        results = response.json()


        if not results:

            return None


        item = results[0]


        address_data = (
            item.get(
                "address",
                {}
            )
        )


        return {

            "latitude":
                float(
                    item["lat"]
                ),

            "longitude":
                float(
                    item["lon"]
                ),

            "display_name":
                item.get(
                    "display_name",
                    query
                ),

            "address":
                address_data,

            "osm_type":
                item.get(
                    "type"
                ),
        }


    except Exception:

        return None


# ============================================================
# ELEVATION
# ============================================================

def get_elevation(
    latitude,
    longitude
):

    try:

        response = requests.get(

            "https://api.open-meteo.com/v1/forecast",

            params={

                "latitude":
                    latitude,

                "longitude":
                    longitude,

                "current":
                    "temperature_2m",

                "elevation":
                    "true",
            },

            timeout=15,
        )


        response.raise_for_status()


        value = response.json().get(
            "elevation"
        )


        if value is not None:

            return float(
                value
            )


    except Exception:

        pass


    # Fallback

    try:

        response = requests.get(

            "https://api.open-elevation.com/api/v1/lookup",

            params={

                "locations":
                    f"{latitude},{longitude}"
            },

            timeout=20,
        )


        response.raise_for_status()


        results = response.json().get(
            "results",
            []
        )


        if results:

            return float(
                results[0][
                    "elevation"
                ]
            )


    except Exception:

        pass


    return None


# ============================================================
# LOCATION ENRICHMENT
# ============================================================

def location_enrichment(
    address,
    city,
    country
):

    geo = geocode_address(

        address,

        city,

        country
    )


    if not geo:

        return {

            "location_found":
                False,

            "latitude":
                None,

            "longitude":
                None,

            "elevation_m":
                None,

            "terrain_profile":
                "Unknown",

            "geocoded_address":
                "",
        }


    elevation = get_elevation(

        geo["latitude"],

        geo["longitude"]
    )


    return {

        "location_found":
            True,

        "latitude":
            geo["latitude"],

        "longitude":
            geo["longitude"],

        "elevation_m":
            elevation,

        "terrain_profile":
            classify_elevation(
                elevation
            ),

        "terrain_risk":
            terrain_risk_from_elevation(
                elevation
            ),

        "geocoded_address":
            geo["display_name"],

        "address_details":
            geo.get(
                "address",
                {}
            ),
    }


# ============================================================
# BASELINE CALCULATION
# ============================================================

def calculate_baseline(

    *,

    rainfall,

    drainage,

    terrain,

    earthquake,

    heavy_rain,

    flooding,

    landslide,

    earthquake_hazard,

    extreme_heat,

    strong_wind,

    drought,

    wildfire,

    other_hazard,

    elevation=None
):

    score = 20


    score += {

        "Moderate":
            5,

        "High":
            12,

        "Extreme":
            20

    }.get(
        rainfall,
        0
    )


    score += {

        "Moderate":
            5,

        "Poor":
            15

    }.get(
        drainage,
        0
    )


    score += {

        "Hilly":
            8,

        "Mountainous":
            12,

        "River Valley":
            10,

        "Coastal":
            6

    }.get(
        terrain,
        0
    )


    score += {

        "Moderate":
            8,

        "High":
            15

    }.get(
        earthquake,
        0
    )


    score += (
        8
        if heavy_rain
        else 0
    )


    score += (
        12
        if flooding
        else 0
    )


    score += (
        12
        if landslide
        else 0
    )


    score += (
        10
        if earthquake_hazard
        else 0
    )


    score += (
        5
        if extreme_heat
        else 0
    )


    score += (
        5
        if strong_wind
        else 0
    )


    score += (
        4
        if drought
        else 0
    )


    score += (
        5
        if wildfire
        else 0
    )


    score += (
        5
        if other_hazard
        else 0
    )


    elevation_risk = (
        terrain_risk_from_elevation(
            elevation
        )
    )


    if elevation_risk == "MEDIUM":

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


    rainfall_level = (

        "HIGH"

        if (
            rainfall == "Extreme"
            or heavy_rain
        )

        else
        "MEDIUM"

        if rainfall == "High"

        else
        "LOW"
    )


    flooding_level = (

        "HIGH"

        if (
            flooding
            or drainage == "Poor"
        )

        else
        "MEDIUM"

        if drainage == "Moderate"

        else
        "LOW"
    )


    landslide_level = (

        "HIGH"

        if (
            landslide
            and
            terrain in [
                "Hilly",
                "Mountainous",
                "River Valley"
            ]
        )

        else
        "MEDIUM"

        if (
            landslide
            or terrain in [
                "Hilly",
                "Mountainous"
            ]
        )

        else
        "LOW"
    )


    earthquake_level = (

        "HIGH"

        if (
            earthquake == "High"
            or earthquake_hazard
        )

        else
        "MEDIUM"

        if earthquake == "Moderate"

        else
        "LOW"
    )


    chains = []

    impacts = []

    actions = []


    if (
        heavy_rain
        or
        rainfall in [
            "High",
            "Extreme"
        ]
    ):

        chains.append(

            "Heavy Rainfall → "
            "Increased Surface Runoff → "
            "Drainage Pressure → "
            "Site Water Accumulation → "
            "Safety + Schedule + "
            "Quality Risks"
        )


        impacts.append(

            "Weather-sensitive "
            "outdoor work may be "
            "interrupted."
        )


        actions.append(

            "Monitor weather conditions "
            "and prepare temporary "
            "rain protection."
        )


    if (
        flooding
        or
        drainage == "Poor"
    ):

        chains.append(

            "Flooding → Construction "
            "Area Inundation → Equipment "
            "/ Material Damage → Work "
            "Interruption → Schedule Delay"
        )


        impacts.append(

            "Excavation areas may "
            "accumulate water."
        )


        actions.append(

            "Inspect drainage systems "
            "and prepare emergency "
            "pumping equipment."
        )


    if landslide:

        chains.append(

            "Heavy Rainfall → Soil "
            "Saturation → Slope Instability "
            "→ Landslide → Worker + "
            "Equipment Safety Risk"
        )


        impacts.append(

            "Excavation and slope stability "
            "may require additional monitoring."
        )


        actions.append(

            "Inspect slopes and excavation "
            "edges after heavy rainfall."
        )


    if earthquake_hazard:

        chains.append(

            "Earthquake → Ground Movement "
            "→ Structural / Equipment Damage "
            "→ Work Interruption → Safety + "
            "Schedule Risk"
        )


        impacts.append(

            "Structural and equipment safety "
            "may be affected by ground movement."
        )


        actions.append(

            "Review seismic emergency "
            "procedures and structural requirements."
        )


    if extreme_heat:

        impacts.append(

            "Extreme heat may increase "
            "worker fatigue and reduce productivity."
        )


        actions.append(

            "Plan appropriate rest periods "
            "and monitor worker heat exposure."
        )


    if strong_wind:

        impacts.append(

            "Strong winds may affect lifting "
            "operations and temporary structures."
        )


        actions.append(

            "Establish wind-related suspension "
            "criteria for lifting operations."
        )


    if drought:

        impacts.append(

            "Construction water availability "
            "may require additional planning."
        )


        actions.append(

            "Review construction water availability "
            "and emergency supply plans."
        )


    if wildfire:

        impacts.append(

            "Emergency evacuation and fire "
            "response planning may be required."
        )


        actions.append(

            "Review emergency evacuation "
            "and fire response procedures."
        )


    if elevation_risk == "MEDIUM":

        impacts.append(

            "High elevation may affect terrain, "
            "access, drainage or slope-management "
            "conditions; verify site-specific "
            "topography."
        )


        actions.append(

            "Review detailed topographic and "
            "slope data before major excavation "
            "or temporary works."
        )


    if not impacts:

        impacts.append(

            "No major environmental construction "
            "impact has been identified from "
            "the selected conditions."
        )


    actions.append(

        "Add identified environmental risks "
        "to the project risk register."
    )


    return {

        "score":
            score,

        "level":
            level,

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
                (
                    "MEDIUM"
                    if extreme_heat
                    else "LOW"
                ),

            "strong_wind":
                (
                    "MEDIUM"
                    if strong_wind
                    else "LOW"
                ),

            "terrain":
                elevation_risk,
        },

        "risk_chains":
            chains,

        "impacts":
            impacts,

        "actions":
            actions,

        "inputs": {

            "rainfall":
                rainfall,

            "drainage":
                drainage,

            "terrain":
                terrain,

            "earthquake":
                earthquake,

            "elevation_m":
                elevation,
        },
    }


# ============================================================
# DAILY LOG
# ============================================================

def new_log_entry(
    filename,
    content,
    analysis=None
):

    return {

        "id":
            str(
                uuid.uuid4()
            ),

        "filename":
            filename,

        "uploaded_at":
            _now(),

        "content":
            content,

        "analysis":
            analysis,
    }


def add_log(
    project_name,
    log_entry
):

    db = load_db()


    project = (
        db[
            "projects"
        ].get(
            project_name
        )
    )


    if project is None:

        raise KeyError(
            project_name
        )


    project.setdefault(
        "logs",
        []
    )


    project[
        "logs"
    ].insert(

        0,

        log_entry
    )


    save_db(
        db
    )


def delete_log(
    project_name,
    log_id
):

    db = load_db()


    project = (
        db[
            "projects"
        ].get(
            project_name
        )
    )


    if not project:

        return False


    before = len(
        project.get(
            "logs",
            []
        )
    )


    project["logs"] = [

        item

        for item
        in project.get(
            "logs",
            []
        )

        if item.get(
            "id"
        ) != log_id
    ]


    save_db(
        db
    )


    return (
        len(
            project["logs"]
        )
        != before
    )


# ============================================================
# UPDATE PROJECT FROM AI LOG ANALYSIS
# ============================================================

def update_project_from_log(
    project_name,
    analysis
):

    db = load_db()


    project = (
        db[
            "projects"
        ].get(
            project_name
        )
    )


    if not project:

        return


    risk = str(
        analysis.get(
            "risk_level",
            ""
        )
    ).upper()


    if risk in [
        "LOW",
        "MEDIUM",
        "HIGH"
    ]:

        old_score = risk_score(
            project.get(
                "risk",
                "Low"
            )
        )


        new_score = risk_score(
            risk
        )


        if new_score > old_score:

            project["risk"] = (
                risk.title()
            )


    progress = analysis.get(
        "progress_percent"
    )


    if isinstance(
        progress,
        (int, float)
    ):

        if (
            0 <= progress <= 100
        ):

            project["progress"] = (
                int(progress)
            )


    save_db(
        db
    )


# ============================================================
# JSON PARSER
# ============================================================

def safe_json_from_text(
    text
):

    if not text:

        return None


    try:

        return json.loads(
            text
        )

    except Exception:

        match = re.search(

            r"\{.*\}",

            text,

            flags=re.S
        )


        if match:

            try:

                return json.loads(
                    match.group(0)
                )

            except Exception:

                return None


    return None


# ============================================================
# DEEPSEEK DAILY LOG ANALYSIS
# ============================================================

def analyze_daily_log(

    project_name,

    project,

    content
):

    api_key = os.getenv(
        "DEEPSEEK_API_KEY",
        ""
    ).strip()


    if not api_key:

        return {

            "status":
                "not_analyzed",

            "risk_level":
                "UNKNOWN",

            "summary":
                "DeepSeek API key is not configured. "
                "The Daily Log has been saved and can "
                "be analyzed after DEEPSEEK_API_KEY "
                "is added.",

            "hazards":
                [],

            "impacts":
                [],

            "actions":
                [],
        }


    prompt = f"""

You are RiskPilot, an AI construction risk manager.

Analyze the following construction Daily Log.

Return ONLY valid JSON.

Required keys:

summary: string

risk_level: LOW|MEDIUM|HIGH

risk_reason: string

hazards: array of strings

impacts: array of strings

actions: array of strings

progress_percent: number or null

workers_mentioned: number or null

urgent: boolean


PROJECT

Name:
{project_name}

Country:
{project.get('country', '')}

City:
{project.get('city', '')}

Address:
{project.get('address', '')}

Project Type:
{project.get('type', '')}

Current Project Risk:
{project.get('risk', '')}

Current Progress:
{project.get('progress', 0)}%


DAILY LOG

{content[:20000]}

"""


    try:

        response = requests.post(

            "https://api.deepseek.com/chat/completions",

            headers={

                "Authorization":
                    f"Bearer {api_key}",

                "Content-Type":
                    "application/json",
            },

            json={

                "model":
                    "deepseek-chat",

                "messages": [

                    {

                        "role":
                            "system",

                        "content":
                            "You are a precise "
                            "construction risk analyst. "
                            "Output JSON only."
                    },

                    {

                        "role":
                            "user",

                        "content":
                            prompt
                    },
                ],

                "temperature":
                    0.2,

                "response_format": {

                    "type":
                        "json_object"
                },
            },

            timeout=60
        )


        response.raise_for_status()


        payload = response.json()


        text = (
            payload[
                "choices"
            ][
                0
            ][
                "message"
            ][
                "content"
            ]
        )


        parsed = safe_json_from_text(
            text
        )


        if parsed is None:

            raise ValueError(
                "DeepSeek returned invalid JSON"
            )


        parsed[
            "status"
        ] = "analyzed"


        return parsed


    except Exception as exc:

        return {

            "status":
                "error",

            "risk_level":
                "UNKNOWN",

            "summary":
                "AI analysis failed, but "
                "the Daily Log was saved "
                "successfully. Error: "
                f"{exc}",

            "hazards":
                [],

            "impacts":
                [],

            "actions":
                [],
        }