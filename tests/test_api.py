"""Offline API tests using a small in-memory Mongo-compatible test double.

These tests exercise HTTP validation and business logic without requiring a MongoDB
server. They do not replace an integration run against MongoDB 6+.
"""
from datetime import datetime, timezone, timedelta
from copy import deepcopy
from fastapi.testclient import TestClient
from app.main import Store, create_app

UTC = timezone.utc

class Result:
    def __init__(self, modified_count=0): self.modified_count = modified_count

class Cursor:
    def __init__(self, docs): self.docs = list(docs)
    def sort(self, spec, direction=None):
        if isinstance(spec, str): spec = [(spec, direction if direction is not None else 1)]
        for key, direction in reversed(spec): self.docs.sort(key=lambda d: (d.get(key) is None, d.get(key)), reverse=direction < 0)
        return self
    def skip(self, n): self.docs = self.docs[n:]; return self
    def limit(self, n): self.docs = self.docs[:n]; return self
    def __iter__(self): return iter(deepcopy(self.docs))

class MemoryCollection:
    def __init__(self): self.docs = []; self.indexes = []
    def create_index(self, keys, unique=False): self.indexes.append((keys, unique))
    def _matches(self, doc, query):
        for key, expected in query.items():
            actual = doc.get(key)
            if isinstance(expected, dict):
                for op, value in expected.items():
                    if op == "$gte" and not (actual is not None and actual >= value): return False
                    if op == "$lte" and not (actual is not None and actual <= value): return False
                    if op == "$ne" and actual == value: return False
            elif actual != expected: return False
        return True
    def find_one(self, query, projection=None):
        for d in self.docs:
            if self._matches(d, query): return deepcopy(d)
        return None
    def insert_one(self, doc):
        for keys, unique in self.indexes:
            if unique and any(all(old.get(k) == doc.get(k) for k, _ in keys) for old in self.docs):
                from app.main import DuplicateKeyError
                raise DuplicateKeyError("duplicate key")
        self.docs.append(deepcopy(doc))
    def update_one(self, query, update):
        for d in self.docs:
            if self._matches(d, query):
                before = deepcopy(d)
                d.update(deepcopy(update.get("$set", {})))
                return Result(int(d != before))
        return Result(0)
    def find(self, query=None, projection=None):
        query = query or {}
        return Cursor([d for d in self.docs if self._matches(d, query)])
    def aggregate(self, pipeline):
        # Execute the endpoint's aggregation intent against deterministic test data.
        match = pipeline[0]["$match"]
        query = {}
        for key, value in match.items(): query[key] = value
        docs = [d for d in self.docs if self._matches(d, query)]
        if any("employees" in stage.get("$group", {}) for stage in pipeline):
            groups = {}
            for d in docs:
                g = groups.setdefault(d["attendance_date"], {"date": d["attendance_date"], "employees": set(), "records": 0, "completed": 0, "total_minutes": 0})
                g["employees"].add(d["employee_id"]); g["records"] += 1
                if d.get("punch_out") is not None:
                    g["completed"] += 1
                    g["total_minutes"] += (d["punch_out"] - d["punch_in"]).total_seconds() / 60
            return [{"date": g["date"], "employee_count": len(g["employees"]), "records": g["records"], "completed": g["completed"], "total_minutes": g["total_minutes"]} for _, g in sorted(groups.items())]
        groups = {}
        for d in docs:
            g = groups.setdefault(d["employee_id"], {"employee_id": d["employee_id"], "days_present": 0, "completed_days": 0, "total_minutes": 0})
            g["days_present"] += 1
            if d.get("punch_out") is not None:
                g["completed_days"] += 1
                g["total_minutes"] += (d["punch_out"] - d["punch_in"]).total_seconds() / 60
        return list(groups.values())

def make_client():
    attendance, audit = MemoryCollection(), MemoryCollection()
    return TestClient(create_app(Store(attendance, audit))), attendance, audit

def seed(attendance, employee="E1", day="2026-10-14", minutes=480, closed=True):
    start = datetime.fromisoformat(day + "T09:00:00+00:00")
    attendance.insert_one({"_id": f"{employee}-{day}", "employee_id": employee, "attendance_date": day,
        "punch_in": start, "punch_out": start + timedelta(minutes=minutes) if closed else None,
        "created_at": start, "updated_at": start})

def test_health_and_openapi():
    c, _, _ = make_client()
    assert c.get("/health").json() == {"status": "ok"}
    assert c.get("/openapi.json").status_code == 200

def test_punch_in_creates_record():
    c, attendance, _ = make_client()
    r = c.post("/attendance/punch-in", json={"employee_id": "E1"})
    assert r.status_code == 201
    assert r.json()["employee_id"] == "E1"
    assert r.json()["status"] == "open"
    assert len(attendance.docs) == 1

def test_punch_in_rejects_duplicate_open_session():
    c, attendance, _ = make_client(); seed(attendance, day=datetime.now(UTC).date().isoformat(), closed=False)
    assert c.post("/attendance/punch-in", json={"employee_id":"E1"}).status_code == 409

def test_punch_in_rejects_duplicate_completed_day():
    c, attendance, _ = make_client(); seed(attendance, day=datetime.now(UTC).date().isoformat(), closed=True)
    assert c.post("/attendance/punch-in", json={"employee_id":"E1"}).status_code == 409

def test_punch_in_validates_payload():
    c, _, _ = make_client()
    assert c.post("/attendance/punch-in", json={}).status_code == 422
    assert c.post("/attendance/punch-in", json={"employee_id":""}).status_code == 422

def test_punch_out_without_record_is_404():
    c, _, _ = make_client()
    assert c.post("/attendance/punch-out", json={"employee_id":"E404"}).status_code == 404

def test_punch_out_closes_session():
    c, attendance, _ = make_client(); seed(attendance, day=datetime.now(UTC).date().isoformat(), closed=False)
    r = c.post("/attendance/punch-out", json={"employee_id":"E1"})
    assert r.status_code == 200 and r.json()["status"] == "complete"
    assert r.json()["duration_minutes"] >= 0

def test_punch_out_twice_conflicts():
    c, attendance, _ = make_client(); seed(attendance, day=datetime.now(UTC).date().isoformat(), closed=True)
    assert c.post("/attendance/punch-out", json={"employee_id":"E1"}).status_code == 409

def test_correction_missing_record_is_404():
    c, _, _ = make_client()
    r = c.post("/attendance/corrections", json={"employee_id":"E1", "attendance_date":"2026-10-14", "punch_in":"2026-10-14T09:00:00+05:30", "reason":"Forgot punch", "corrected_by":"manager"})
    assert r.status_code == 404

def test_correction_requires_a_timestamp():
    c, attendance, _ = make_client(); seed(attendance)
    r = c.post("/attendance/corrections", json={"employee_id":"E1", "attendance_date":"2026-10-14", "reason":"Fix missing punch", "corrected_by":"manager"})
    assert r.status_code == 422

def test_correction_rejects_naive_datetime():
    c, attendance, _ = make_client(); seed(attendance)
    r = c.post("/attendance/corrections", json={"employee_id":"E1", "attendance_date":"2026-10-14", "punch_in":"2026-10-14T08:00:00", "reason":"Fix punch time", "corrected_by":"manager"})
    assert r.status_code == 422

def test_correction_rejects_out_before_in():
    c, attendance, _ = make_client(); seed(attendance)
    r = c.post("/attendance/corrections", json={"employee_id":"E1", "attendance_date":"2026-10-14", "punch_in":"2026-10-14T18:00:00+00:00", "reason":"Fix punch time", "corrected_by":"manager"})
    assert r.status_code == 422

def test_correction_updates_and_writes_audit():
    c, attendance, audit = make_client(); seed(attendance)
    r = c.post("/attendance/corrections", json={"employee_id":"E1", "attendance_date":"2026-10-14", "punch_in":"2026-10-14T08:30:00+00:00", "reason":"Forgot to punch in", "corrected_by":"manager"})
    assert r.status_code == 200
    assert r.json()["punch_in"].startswith("2026-10-14T08:30:00")
    assert len(audit.docs) == 1
    assert audit.docs[0]["reason"] == "Forgot to punch in"
    assert audit.docs[0]["before"]["punch_in"] == datetime.fromisoformat("2026-10-14T09:00:00+00:00")

def test_list_filters_and_paginates():
    c, attendance, _ = make_client(); seed(attendance, "E1", "2026-10-13"); seed(attendance, "E2", "2026-10-14")
    r = c.get("/attendance", params={"employee_id":"E1", "start_date":"2026-10-13", "end_date":"2026-10-13", "limit":1})
    assert r.status_code == 200 and len(r.json()) == 1 and r.json()[0]["employee_id"] == "E1"

def test_list_rejects_inverted_dates():
    c, _, _ = make_client()
    assert c.get("/attendance", params={"start_date":"2026-10-15", "end_date":"2026-10-01"}).status_code == 422

def test_list_limit_validation():
    c, _, _ = make_client()
    assert c.get("/attendance", params={"limit":0}).status_code == 422
    assert c.get("/attendance", params={"limit":501}).status_code == 422

def test_daily_analytics_aggregation_intent():
    c, attendance, _ = make_client(); seed(attendance, "E1", "2026-10-14", 480); seed(attendance, "E2", "2026-10-14", 240, closed=False)
    r = c.get("/analytics/daily", params={"start_date":"2026-10-14", "end_date":"2026-10-14"})
    assert r.status_code == 200
    data = r.json()[0]
    assert data["employee_count"] == 2 and data["records"] == 2 and data["completed"] == 1
    assert data["total_minutes"] == 480

def test_employee_analytics_aggregation_intent():
    c, attendance, _ = make_client(); seed(attendance, "E1", "2026-10-13", 480); seed(attendance, "E1", "2026-10-14", 240, closed=False)
    r = c.get("/analytics/employee/E1", params={"start_date":"2026-10-01", "end_date":"2026-10-31"})
    assert r.status_code == 200
    assert r.json() == {"employee_id":"E1", "days_present":2, "completed_days":1, "total_minutes":480}

def test_employee_analytics_empty():
    c, _, _ = make_client()
    r = c.get("/analytics/employee/E404", params={"start_date":"2026-10-01", "end_date":"2026-10-31"})
    assert r.status_code == 200 and r.json()["days_present"] == 0

def test_analytics_rejects_inverted_range():
    c, _, _ = make_client()
    assert c.get("/analytics/daily", params={"start_date":"2026-10-15", "end_date":"2026-10-01"}).status_code == 422
    assert c.get("/analytics/employee/E1", params={"start_date":"2026-10-15", "end_date":"2026-10-01"}).status_code == 422

def test_audit_history_and_unknown_id():
    c, attendance, audit = make_client(); seed(attendance)
    audit.insert_one({"_id":"a1", "attendance_id":"E1-2026-10-14", "changed_at":datetime.now(UTC), "reason":"test"})
    assert c.get("/attendance/E1-2026-10-14/audit").status_code == 200
    assert c.get("/attendance/no-such-id/audit").status_code == 404
