# Design decisions

1. **FastAPI + Pydantic**: type validation and generated OpenAPI docs make the contract discoverable.
2. **MongoDB collections**: `attendance` stores current state; `attendance_audit` stores correction events separately to preserve history.
3. **Indexes**: unique `(employee_id, attendance_date)` prevents duplicate daily records; date index supports range analytics.
4. **Concurrency**: unique index is the final guard against concurrent duplicate punch-ins. Punch-out uses a conditional update to reduce race risk.
5. **Analytics**: daily and employee summaries use MongoDB aggregation pipelines rather than loading all records into Python.
6. **Audit payload**: correction events capture actor, timestamp, reason, and before/after punch values.
7. **Validation**: timezone-aware timestamps are required for corrections; punch-out must be later than punch-in.
8. **Scope limitation**: exact requirements were not available because the Drive kit was not accessible in this session. The UTC-day assumption and endpoint shapes should be reconciled with the official contract before final submission.
