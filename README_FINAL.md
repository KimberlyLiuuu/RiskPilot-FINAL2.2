# RiskPilot Final

This version keeps the current project data but excludes `.env`, `.venv`, `.git`, and backup copies.

## Main changes
- Sidebar order: + New Project → App → Project Dashboard → History Projects
- Active / History project lifecycle with End Project and Reopen
- Star and Pin projects
- Active and historical projects grouped by continent
- Global map uses one risk marker per project; same-city projects are offset
- Dashboard no longer shows Progress
- Dashboard keeps Overall Risk, Workers, Daily Logs and Baseline
- Weather & Terrain is a paired module
- Temperature and precipitation trend charts
- Elevation contour visualization without matplotlib in the main app
- Daily Logs: user records facts; AI determines risk level and score
- Saved logs can be edited and re-analyzed
- Form clears after successful save
- Daily Risk includes every log date, even when the date is before project creation
- Historical weather is fetched from Open-Meteo Archive when available
- Daily risk combines baseline + weather + AI-analyzed site logs
- Historical projects preserve logs, baseline and risk history

## Run
1. Copy your existing `.env` into this folder. Do not commit it.
2. Activate your virtual environment.
3. Install/update dependencies:
   `pip install -r requirements.txt`
4. Run:
   `streamlit run app.py`

The DeepSeek API key should be stored as `DEEPSEEK_API_KEY` in `.env`.
