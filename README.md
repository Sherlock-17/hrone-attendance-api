# Employee Attendance & Analytics API

A compact FastAPI + MongoDB implementation for employee punch-in/out, auditable manual corrections, and aggregation-based analytics.

## Requirements
- Python 3.11+
- MongoDB 6.0+ (local or Atlas)

## Run
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
export MONGODB_URI='mongodb://localhost:27017'
export MONGODB_DATABASE='hrone_attendance'
uvicorn app.main:app --reload
```
Open `http://127.0.0.1:8000/docs` for interactive API docs. Health check: `GET /health`.

## Endpoints
- `POST /attendance/punch-in` — `{"employee_id":"E001"}`
- `POST /attendance/punch-out` — `{"employee_id":"E001"}`
- `POST /attendance/corrections` — employee_id, attendance_date, at least one corrected timestamp, reason, corrected_by
- `GET /attendance` — optional employee_id, start_date, end_date, skip, limit
- `GET /analytics/daily?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`
- `GET /analytics/employee/{employee_id}?start_date=...&end_date=...`
- `GET /attendance/{attendance_id}/audit`
- `GET /health`

## Business rules and assumptions
The assignment email did not include the downloadable kit contents, so the exact OpenAPI contract, sample data, and starter app could not be verified. This implementation makes the following explicit assumptions: one record per employee per UTC calendar day; one punch-in and punch-out per day; no duplicate session; punch-out after punch-in; corrections require a reason and actor; every correction records before/after values in a separate audit collection. Adjust date/timezone semantics and response shapes to the supplied kit before submission if they differ.

## Tests
```bash
pytest -q
```
The included unit tests use a small in-memory test double and do not require a running MongoDB server. They verify API/business logic and expected analytics outputs, but do not emulate MongoDB internals. Run an integration smoke test against MongoDB 6.0+ before submitting to verify actual aggregation operator behaviour and index creation.

## Notes
- Do not commit secrets or `.env` files.
- This is a starter submission and must be reviewed against the official kit before submission.
