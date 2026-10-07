# SmartPoli

Medicine reminders, safe-dose checks and care sharing for patients, caregivers and doctors.
FastAPI backend (`backend/`), vanilla-JS web app / PWA (`backend/static/`), Android wrapper (`android/`).

## Run locally
```
cd backend
pip install -r requirements.txt
SMARTPOLI_DATABASE_URL=sqlite:///./dev.db SMARTPOLI_DISABLE_SCHEDULER=1 uvicorn main:app --reload
python -m pytest -q
```
Copy `.env.example` to `.env` and fill in your own values. Never commit `.env`.

## License
Copyright (C) 2026 Garv Chopra. Licensed under the **GNU Affero General Public License v3.0** (see `LICENSE`).
If you run a modified version of SmartPoli as a network service, you must make your modified source available to its users.

## Security
See `SECURITY.md`. SmartPoli is a reminder and information tool, not medical advice.
