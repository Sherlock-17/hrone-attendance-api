"""Employee Attendance & Analytics API.

Assumptions where the email brief does not specify exact contract details:
- One attendance record per employee per UTC calendar day.
- A punch-in cannot occur while an open attendance session exists for that employee.
- Punch-out requires an open session and must be after punch-in.
- Manual corrections update the attendance record and append an immutable audit event.
- API dates are ISO-8601; analytics accept inclusive start_date/end_date.
"""
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from typing import Any, Optional
from uuid import uuid4
import os

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
try:
    from pymongo import MongoClient
    from pymongo.errors import DuplicateKeyError
except ImportError:  # Allows unit tests to import the app before dependencies are installed.
    MongoClient = None
    class DuplicateKeyError(Exception):
        pass

UTC = timezone.utc
app = FastAPI(title="Employee Attendance & Analytics API", version="1.0.0")

class PunchRequest(BaseModel):
    employee_id: str = Field(min_length=1, max_length=128)

class CorrectionRequest(BaseModel):
    employee_id: str = Field(min_length=1, max_length=128)
    attendance_date: date
    punch_in: Optional[datetime] = None
    punch_out: Optional[datetime] = None
    reason: str = Field(min_length=3, max_length=1000)
    corrected_by: str = Field(min_length=1, max_length=128)

class AttendanceOut(BaseModel):
    id: str
    employee_id: str
    attendance_date: date
    punch_in: datetime
    punch_out: Optional[datetime] = None
    duration_minutes: Optional[int] = None
    status: str

class Store:
    def __init__(self, attendance=None, audit=None):
        self.attendance = attendance
        self.audit = audit

    def setup(self):
        if self.attendance is not None:
            self.attendance.create_index([("employee_id", 1), ("attendance_date", 1)], unique=True)
            self.attendance.create_index([("attendance_date", 1)])
            self.audit.create_index([("attendance_id", 1), ("changed_at", 1)])

def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise HTTPException(422, "Datetime must include a timezone")
    return dt.astimezone(UTC)

def _serialize(doc: dict) -> dict:
    pin, pout = doc.get("punch_in"), doc.get("punch_out")
    duration = None
    if pin and pout:
        duration = int((pout - pin).total_seconds() // 60)
    return {
        "id": str(doc["_id"]), "employee_id": doc["employee_id"],
        "attendance_date": doc["attendance_date"], "punch_in": pin,
        "punch_out": pout, "duration_minutes": duration,
        "status": "complete" if pout else "open",
    }

def create_app(store: Store) -> FastAPI:
    @asynccontextmanager
    async def lifespan(api: FastAPI):
        store.setup()
        yield

    api = FastAPI(title="Employee Attendance & Analytics API", version="1.0.0", lifespan=lifespan)

    @api.get("/health")
    def health():
        return {"status": "ok"}

    @api.post("/attendance/punch-in", status_code=201)
    def punch_in(payload: PunchRequest):
        now = datetime.now(UTC)
        day = now.date().isoformat()
        # Unique employee/day index also prevents duplicate records under concurrent requests.
        existing = store.attendance.find_one({"employee_id": payload.employee_id, "attendance_date": day})
        if existing:
            if existing.get("punch_out") is None:
                raise HTTPException(409, "Employee already has an open attendance session")
            raise HTTPException(409, "Attendance already exists for this employee today")
        doc = {"_id": str(uuid4()), "employee_id": payload.employee_id,
               "attendance_date": day, "punch_in": now, "punch_out": None,
               "created_at": now, "updated_at": now}
        try:
            store.attendance.insert_one(doc)
        except DuplicateKeyError:
            raise HTTPException(409, "Attendance already exists for this employee today")
        return _serialize(doc)

    @api.post("/attendance/punch-out")
    def punch_out(payload: PunchRequest):
        now = datetime.now(UTC)
        day = now.date().isoformat()
        doc = store.attendance.find_one({"employee_id": payload.employee_id, "attendance_date": day})
        if not doc:
            raise HTTPException(404, "No attendance session found for today")
        if doc.get("punch_out") is not None:
            raise HTTPException(409, "Attendance session is already closed")
        if now <= doc["punch_in"]:
            raise HTTPException(422, "Punch-out must be after punch-in")
        result = store.attendance.update_one({"_id": doc["_id"], "punch_out": None},
                                             {"$set": {"punch_out": now, "updated_at": now}})
        if getattr(result, "modified_count", 1) == 0:
            raise HTTPException(409, "Attendance session was closed by another request")
        updated = store.attendance.find_one({"_id": doc["_id"]})
        return _serialize(updated)

    @api.post("/attendance/corrections")
    def correct_attendance(payload: CorrectionRequest):
        if payload.punch_in is None and payload.punch_out is None:
            raise HTTPException(422, "At least one of punch_in or punch_out must be provided")
        query = {"employee_id": payload.employee_id, "attendance_date": payload.attendance_date.isoformat()}
        doc = store.attendance.find_one(query)
        if not doc:
            raise HTTPException(404, "Attendance record not found")
        new_in = _utc(payload.punch_in) if payload.punch_in else doc["punch_in"]
        new_out = _utc(payload.punch_out) if payload.punch_out else doc.get("punch_out")
        if new_out and new_out <= new_in:
            raise HTTPException(422, "Punch-out must be after punch-in")
        changed_at = datetime.now(UTC)
        changes = {}
        if payload.punch_in is not None: changes["punch_in"] = new_in
        if payload.punch_out is not None: changes["punch_out"] = new_out
        before = {"punch_in": doc["punch_in"], "punch_out": doc.get("punch_out")}
        after = {"punch_in": new_in, "punch_out": new_out}
        store.attendance.update_one({"_id": doc["_id"]}, {"$set": {**changes, "updated_at": changed_at}})
        store.audit.insert_one({
            "_id": str(uuid4()), "attendance_id": str(doc["_id"]),
            "employee_id": payload.employee_id, "attendance_date": payload.attendance_date.isoformat(),
            "changed_at": changed_at, "changed_by": payload.corrected_by,
            "reason": payload.reason, "before": before, "after": after,
        })
        return _serialize(store.attendance.find_one({"_id": doc["_id"]}))

    @api.get("/attendance")
    def list_attendance(employee_id: Optional[str] = None,
                        start_date: Optional[date] = None, end_date: Optional[date] = None,
                        limit: int = Query(100, ge=1, le=500), skip: int = Query(0, ge=0)):
        query: dict[str, Any] = {}
        if employee_id: query["employee_id"] = employee_id
        if start_date or end_date:
            query["attendance_date"] = {}
            if start_date: query["attendance_date"]["$gte"] = start_date.isoformat()
            if end_date: query["attendance_date"]["$lte"] = end_date.isoformat()
        if start_date and end_date and start_date > end_date:
            raise HTTPException(422, "start_date must be on or before end_date")
        docs = store.attendance.find(query).sort([("attendance_date", -1), ("employee_id", 1)]).skip(skip).limit(limit)
        return [_serialize(d) for d in docs]

    @api.get("/analytics/daily")
    def daily_analytics(start_date: date, end_date: date):
        if start_date > end_date: raise HTTPException(422, "start_date must be on or before end_date")
        pipeline = [
            {"$match": {"attendance_date": {"$gte": start_date.isoformat(), "$lte": end_date.isoformat()}}},
            {"$group": {"_id": "$attendance_date", "employees": {"$addToSet": "$employee_id"},
                        "records": {"$sum": 1},
                        "completed": {"$sum": {"$cond": [{"$ne": [{"$ifNull": ["$punch_out", None]}, None]}, 1, 0]}},
                        "total_duration_ms": {"$sum": {"$cond": [
                            {"$and": [{"$ne": [{"$ifNull": ["$punch_out", None]}, None]},
                                      {"$ne": [{"$ifNull": ["$punch_in", None]}, None]}]},
                            {"$subtract": ["$punch_out", "$punch_in"]}, 0]}}}},
            {"$project": {"_id": 0, "date": "$_id", "employee_count": {"$size": "$employees"},
                          "records": 1, "completed": 1,
                          "total_minutes": {"$divide": ["$total_duration_ms", 60000]}}},
            {"$sort": {"date": 1}},
        ]
        return list(store.attendance.aggregate(pipeline))

    @api.get("/analytics/employee/{employee_id}")
    def employee_analytics(employee_id: str, start_date: date, end_date: date):
        if start_date > end_date: raise HTTPException(422, "start_date must be on or before end_date")
        pipeline = [
            {"$match": {"employee_id": employee_id,
                        "attendance_date": {"$gte": start_date.isoformat(), "$lte": end_date.isoformat()}}},
            {"$group": {"_id": "$employee_id", "days_present": {"$sum": 1},
                        "completed_days": {"$sum": {"$cond": [{"$ne": [{"$ifNull": ["$punch_out", None]}, None]}, 1, 0]}},
                        "total_duration_ms": {"$sum": {"$cond": [
                            {"$and": [{"$ne": [{"$ifNull": ["$punch_out", None]}, None]},
                                      {"$ne": [{"$ifNull": ["$punch_in", None]}, None]}]},
                            {"$subtract": ["$punch_out", "$punch_in"]}, 0]}}}},
            {"$project": {"_id": 0, "employee_id": "$_id", "days_present": 1, "completed_days": 1,
                          "total_minutes": {"$divide": ["$total_duration_ms", 60000]}}},
        ]
        result = list(store.attendance.aggregate(pipeline))
        return result[0] if result else {"employee_id": employee_id, "days_present": 0, "completed_days": 0, "total_minutes": 0}

    @api.get("/attendance/{attendance_id}/audit")
    def audit_history(attendance_id: str):
        if not store.attendance.find_one({"_id": attendance_id}):
            raise HTTPException(404, "Attendance record not found")
        return list(store.audit.find({"attendance_id": attendance_id}, {"_id": 0}).sort("changed_at", 1))

    return api

def _build_default_app():
    if MongoClient is None:
        raise RuntimeError("pymongo is not installed; run pip install -r requirements.txt")
    uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
    db_name = os.getenv("MONGODB_DATABASE", "hrone_attendance")
    client = MongoClient(uri, serverSelectionTimeoutMS=3000)
    db = client[db_name]
    return create_app(Store(db["attendance"], db["attendance_audit"]))

try:
    app = _build_default_app()
except Exception:
    app = FastAPI(title="Employee Attendance & Analytics API", version="1.0.0")

    @app.get("/health")
    def health():
        return {"status": "ok", "database": "unavailable"}
