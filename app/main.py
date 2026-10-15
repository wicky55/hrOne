"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from decimal import Decimal, ROUND_HALF_UP
from pymongo import MongoClient, ASCENDING
from pymongo.errors import DuplicateKeyError
from typing import Optional
from fastapi import HTTPException

from pydantic import BaseModel, StrictInt
from dotenv import load_dotenv
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pymongo import MongoClient
import re
from datetime import date
from fastapi import FastAPI, HTTPException, Query

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

def create_indexes():
    db.employees.create_index(
        [("emp_code", ASCENDING)],
        unique=True,
        name="uq_employee_code",
    )
    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", ASCENDING)],
        unique=True,
        name="uq_attendance_employee_date",
    )
    db.attendance_logs.create_index(
        [("date", ASCENDING), ("status", ASCENDING)],
        name="ix_attendance_date_status",
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    create_indexes()
    yield


app = FastAPI(
    title="Employee Attendance & Analytics API",
    version="2.0.0",
    lifespan=lifespan,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #




def truncate_seconds(dt: datetime) -> datetime:
    """Remove fractional seconds and normalize to UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0)


def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    punch_in = truncate_seconds(punch_in)
    h, m = map(int, shift_start.split(":"))

    local_in = punch_in.astimezone(IST)
    start = local_in.replace(
        hour=h, minute=m, second=0, microsecond=0
    )

    # If this is an overnight shift and the punch-in is
    # after midnight but before shift_end, start was yesterday.
    return_minutes = 0
    elapsed = (local_in - start).total_seconds()

    if elapsed > 10 * 60:
        return_minutes = int(elapsed // 60)

    return max(0, return_minutes)


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    punch_in = truncate_seconds(punch_in)
    punch_out = truncate_seconds(punch_out)

    seconds = Decimal(str((punch_out - punch_in).total_seconds()))
    hours = (seconds / Decimal("3600")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return float(hours)


def compute_overtime(
    punch_out: datetime,
    shift_end: str,
    date_str: str,
    shift_start: str = "09:30",
) -> int:
    punch_out = truncate_seconds(punch_out)
    end_h, end_m = map(int, shift_end.split(":"))
    start_h, start_m = map(int, shift_start.split(":"))

    start_total = start_h * 60 + start_m
    end_total = end_h * 60 + end_m

    end_date = datetime.strptime(date_str, "%Y-%m-%d").date()

    # Overnight shifts end on the following calendar day.
    if end_total <= start_total:
        end_date += timedelta(days=1)

    shift_end_dt = datetime(
        end_date.year, end_date.month, end_date.day,
        end_h, end_m, tzinfo=IST
    ).astimezone(timezone.utc)

    minutes = int(
        (punch_out - shift_end_dt).total_seconds() // 60
    )

    return minutes if minutes >= 30 else 0



# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"

class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = None


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #

@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="MongoDB unavailable",
        )




@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if body.shift_start == body.shift_end:
        raise HTTPException(
            status_code=422,
            detail="shift_start and shift_end must differ",
        )

    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="emp_code already exists",
        )

    doc.pop("_id", None)
    doc["created_at"] = int(
        doc["created_at"].timestamp() * 1000
    )
    return doc


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    ts = datetime.fromtimestamp(body.punched_at / 1000) if body.punched_at else datetime.now()
    d = ts.date().isoformat()
    if db.attendance_logs.find_one({"emp_code": body.emp_code, "date": d}):
        raise HTTPException(409, "already punched in for this date")
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"]),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    res = db.attendance_logs.insert_one(doc)
    doc["id"] = str(res.inserted_id)
    doc.pop("_id", None)
    return doc


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status
    docs = list(db.attendance_logs.find(q))
    docs.sort(key=lambda d: d["date"], reverse=True)
    total = len(docs)
    page_docs = docs[(page - 1) * page_size : page * page_size]
    for d in page_docs:
        d["id"] = str(d.pop("_id"))
    return {"items": page_docs, "total": total, "page": page, "page_size": page_size}


@app.on_event("startup")
def create_indexes():
    db.employees.create_index(
        [("emp_code", ASCENDING)],
        unique=True,
        name="uq_employee_code",
    )

    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", ASCENDING)],
        unique=True,
        name="uq_attendance_employee_date",
    )

    db.attendance_logs.create_index(
        [("date", ASCENDING), ("status", ASCENDING)],
        name="ix_attendance_date_status",
    )

    
@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    # Validate pagination
    if page < 1:
        raise HTTPException(
            status_code=422,
            detail="page must be at least 1",
        )

    if page_size < 1 or page_size > 100:
        raise HTTPException(
            status_code=422,
            detail="page_size must be between 1 and 100",
        )

    # Build the MongoDB filter
    query = {}
    if department:
        query["department"] = department

    # Count only matching employees
    total = db.employees.count_documents(query)

    # Fetch the requested page
    items = list(
        db.employees.find(query, {"_id": 0})
        .sort("emp_code", 1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


from decimal import Decimal, ROUND_HALF_UP


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    # 1. Confirm that the employee exists.
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="Unknown employee")

    # 2. Resolve and validate epoch milliseconds.
    if body.punched_at is None:
        punch_out_utc = datetime.now(timezone.utc).replace(microsecond=0)
    else:
        if body.punched_at < 100_000_000_000:
            raise HTTPException(
                status_code=422,
                detail="punched_at must be epoch milliseconds",
            )
        try:
            punch_out_utc = datetime.fromtimestamp(
                body.punched_at / 1000, tz=timezone.utc
            ).replace(microsecond=0)
        except (ValueError, OverflowError, OSError):
            raise HTTPException(
                status_code=422,
                detail="Invalid punched_at timestamp",
            )

    # PyMongo stores naive datetimes as UTC by default.
    punch_out_db = punch_out_utc.replace(tzinfo=None)

    # 3. Find the most recent punch-in at or before this time.
    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$lte": punch_out_db},
        },
        sort=[("punch_in", -1)],
    )

    if not record:
        raise HTTPException(
            status_code=404,
            detail="No punch-in found",
        )

    # 4. Do not allow a second punch-out.
    if record.get("punch_out") is not None:
        raise HTTPException(
            status_code=409,
            detail="Attendance record already punched out",
        )

    punch_in_db = record["punch_in"]
    if punch_in_db.tzinfo is None:
        punch_in_utc = punch_in_db.replace(tzinfo=timezone.utc)
    else:
        punch_in_utc = punch_in_db.astimezone(timezone.utc)

    elapsed_seconds = (
        punch_out_utc - punch_in_utc
    ).total_seconds()

    # 5. Validate the time interval.
    if elapsed_seconds <= 0 or elapsed_seconds > 24 * 60 * 60:
        raise HTTPException(
            status_code=422,
            detail="Punch-out must be after punch-in and within 24 hours",
        )

    # 6. Calculate work hours using decimal half-up rounding.
    hours = (
        Decimal(str(elapsed_seconds)) / Decimal("3600")
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    work_hours = float(hours)

    # 7. Calculate the scheduled shift end in IST.
    punch_in_ist = punch_in_utc.astimezone(IST)
    punch_out_ist = punch_out_utc.astimezone(IST)

    start_h, start_m = map(
        int, emp["shift_start"].split(":")
    )
    end_h, end_m = map(
        int, emp["shift_end"].split(":")
    )

    shift_date = punch_in_ist.date()

    # For overnight shifts, an early-morning punch-in belongs
    # to the shift that began on the previous calendar day.
    if (end_h, end_m) <= (start_h, start_m):
        if (punch_in_ist.hour, punch_in_ist.minute) < (end_h, end_m):
            from datetime import timedelta
            shift_date -= timedelta(days=1)

    shift_end_ist = datetime(
        shift_date.year,
        shift_date.month,
        shift_date.day,
        end_h,
        end_m,
        tzinfo=IST,
    )

    if (end_h, end_m) <= (start_h, start_m):
        from datetime import timedelta
        shift_end_ist += timedelta(days=1)

    overtime_elapsed = (
        punch_out_ist - shift_end_ist
    ).total_seconds()
    overtime_minutes = (
        int(overtime_elapsed // 60)
        if overtime_elapsed >= 30 * 60
        else 0
    )

    # 8. Update the attendance record.
    half_day = work_hours < 4.50

    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": punch_out_db,
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day,
            }
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record was already updated",
        )

    # 9. Return the updated record without MongoDB's internal ID.
    updated = db.attendance_logs.find_one(
        {"_id": record["_id"]}
    )
    updated.pop("_id", None)

    # Convert datetimes to epoch milliseconds for the API response.
    for field in ("punch_in", "punch_out"):
        value = updated.get(field)
        if value is not None:
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            updated[field] = int(value.timestamp() * 1000)

    return updated

import re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP


PRESENT_STATUSES = ["PRESENT", "WFH", "ON_DUTY"]


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    # 1. Validate YYYY-MM and reject invalid months.
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(
            status_code=422,
            detail="month must be in YYYY-MM format",
        )

    year, month_num = map(int, month.split("-"))
    month_start = date(year, month_num, 1)

    if month_num == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month_num + 1, 1)

    month_end = next_month - timedelta(days=1)
    start_str = month_start.isoformat()
    end_str = month_end.isoformat()

    # 2. Start from the employee so employees with no logs are included.
    pipeline = [
        {"$match": {"emp_code": emp_code}},
        {
            "$set": {
                "joined_date_obj": {
                    "$dateFromString": {
                        "dateString": "$joined_on",
                        "format": "%Y-%m-%d",
                    }
                }
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "pipeline": [
                    {
                        "$match": {
                            "date": {
                                "$gte": start_str,
                                "$lt": next_month.isoformat(),
                            }
                        }
                    },
                    {
                        "$set": {
                            "log_date_obj": {
                                "$dateFromString": {
                                    "dateString": "$date",
                                    "format": "%Y-%m-%d",
                                }
                            }
                        }
                    },
                ],
                "as": "logs",
            }
        },
        {
            "$set": {
                "period_start": {
                    "$cond": [
                        {"$gt": ["$joined_date_obj", {
                            "$dateFromString": {
                                "dateString": start_str,
                                "format": "%Y-%m-%d",
                            }
                        }]},
                        "$joined_date_obj",
                        {
                            "$dateFromString": {
                                "dateString": start_str,
                                "format": "%Y-%m-%d",
                            }
                        },
                    ]
                },
                "period_end": {
                    "$dateFromString": {
                        "dateString": end_str,
                        "format": "%Y-%m-%d",
                    }
                },
            }
        },
        {
            "$set": {
                "working_days": {
                    "$let": {
                        "vars": {
                            "span": {
                                "$max": [
                                    0,
                                    {
                                        "$dateDiff": {
                                            "startDate": "$period_start",
                                            "endDate": {
                                                "$dateAdd": {
                                                    "startDate": "$period_end",
                                                    "unit": "day",
                                                    "amount": 1,
                                                }
                                            },
                                            "unit": "day",
                                        }
                                    },
                                ]
                            }
                        },
                        "in": {
                            "$size": {
                                "$filter": {
                                    "input": {
                                        "$map": {
                                            "input": {
                                                "$range": [0, "$$span"]
                                            },
                                            "as": "offset",
                                            "in": {
                                                "$dateAdd": {
                                                    "startDate": "$period_start",
                                                    "unit": "day",
                                                    "amount": "$$offset",
                                                }
                                            },
                                        }
                                    },
                                    "as": "work_date",
                                    "cond": {
                                        "$and": [
                                            {
                                                "$gte": [
                                                    {"$dayOfWeek": "$$work_date"},
                                                    2,
                                                ]
                                            },
                                            {
                                                "$lte": [
                                                    {"$dayOfWeek": "$$work_date"},
                                                    6,
                                                ]
                                            },
                                        ]
                                    },
                                }
                            }
                        },
                    }
                }
            }
        },
        {
            "$set": {
                "present_days": {
                    "$sum": {
                        "$map": {
                            "input": {
                                "$filter": {
                                    "input": "$logs",
                                    "as": "log",
                                    "cond": {
                                        "$and": [
                                            {
                                                "$in": [
                                                    "$$log.status",
                                                    PRESENT_STATUSES,
                                                ]
                                            },
                                            {
                                                "$gte": [
                                                    {
                                                        "$dayOfWeek":
                                                            "$$log.log_date_obj"
                                                    },
                                                    2,
                                                ]
                                            },
                                            {
                                                "$lte": [
                                                    {
                                                        "$dayOfWeek":
                                                            "$$log.log_date_obj"
                                                    },
                                                    6,
                                                ]
                                            },
                                        ]
                                    },
                                }
                            },
                            "as": "log",
                            "in": {
                                "$cond": [
                                    {"$eq": ["$$log.half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                        }
                    }
                },
                "leave_days": {
                    "$size": {
                        "$filter": {
                            "input": "$logs",
                            "as": "log",
                            "cond": {"$eq": ["$$log.status", "LEAVE"]},
                        }
                    }
                },
                "late_count": {
                    "$size": {
                        "$filter": {
                            "input": "$logs",
                            "as": "log",
                            "cond": {"$gt": ["$$log.late_minutes", 0]},
                        }
                    }
                },
                "total_late_minutes": {
                    "$sum": "$logs.late_minutes"
                },
                "total_overtime_minutes": {
                    "$sum": "$logs.overtime_minutes"
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "emp_code": 1,
                "working_days": 1,
                "present_days": 1,
                "leave_days": 1,
                "late_count": 1,
                "total_late_minutes": 1,
                "total_overtime_minutes": 1,
            }
        },
    ]

    # 3. Execute the aggregation. No result means unknown employee.
    results = list(db.employees.aggregate(pipeline))

    if not results:
        raise HTTPException(status_code=404, detail="Unknown employee")

    result = results[0]
    working_days = result["working_days"]
    present_days = result["present_days"]

    # 4. Round the percentage using decimal half-up rounding.
    if working_days == 0:
        attendance_pct = None
    else:
        attendance_pct = float(
            (
                Decimal(str(present_days))
                / Decimal(working_days)
                * Decimal("100")
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        )

    return {
        "emp_code": result["emp_code"],
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": result["leave_days"],
        "late_count": result["late_count"],
        "total_late_minutes": result["total_late_minutes"] or 0,
        "total_overtime_minutes": (
            result["total_overtime_minutes"] or 0
        ),
        "attendance_pct": attendance_pct,
    }

from datetime import date
from decimal import Decimal, ROUND_HALF_UP


@app.get("/analytics/departments/summary")
def department_summary(month: str, department: Optional[str] = None):
    # Validate month format and range.
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(
            status_code=422,
            detail="month must be in YYYY-MM format",
        )

    year, month_num = map(int, month.split("-"))

    month_start = date(year, month_num, 1)
    if month_num == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month_num + 1, 1)

    month_start_str = month_start.isoformat()
    next_month_str = next_month.isoformat()

    # Find employees who had joined by the end of this month.
    employee_filter = {
        "joined_on": {"$lt": next_month_str}
    }
    if department:
        employee_filter["department"] = department

    pipeline = [
        {"$match": employee_filter},
        {
            "$lookup": {
                "from": "attendance_logs",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "pipeline": [
                    {
                        "$match": {
                            "date": {
                                "$gte": month_start_str,
                                "$lt": next_month_str,
                            }
                        }
                    }
                ],
                "as": "logs",
            }
        },
        {
            "$unwind": {
                "path": "$logs",
                "preserveNullAndEmptyArrays": True,
            }
        },
        {
            "$group": {
                "_id": "$department",
                "employees": {"$addToSet": "$emp_code"},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": [
                                        "$logs.status",
                                        ["PRESENT", "WFH", "ON_DUTY"],
                                    ]},
                                    {"$ne": ["$logs.date", None]},
                                    {
                                        "$gte": [
                                            {
                                                "$dayOfWeek": {
                                                    "$dateFromString": {
                                                        "dateString": "$logs.date"
                                                    }
                                                }
                                            },
                                            2,
                                        ]
                                    },
                                    {
                                        "$lte": [
                                            {
                                                "$dayOfWeek": {
                                                    "$dateFromString": {
                                                        "dateString": "$logs.date"
                                                    }
                                                }
                                            },
                                            6,
                                        ]
                                    },
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$logs.half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "work_hours_sum": {
                    "$sum": {
                        "$cond": [
                            {"$ne": ["$logs.work_hours", None]},
                            "$logs.work_hours",
                            0,
                        ]
                    }
                },
                "work_hours_count": {
                    "$sum": {
                        "$cond": [
                            {"$ne": ["$logs.work_hours", None]},
                            1,
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$logs.late_minutes", 0]},
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$logs.late_minutes", 0]}
                },
                "leave_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$logs.status", "LEAVE"]},
                            1,
                            0,
                        ]
                    }
                },
                "on_duty_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$logs.status", "ON_DUTY"]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": {"$size": "$employees"},
                "present_days": 1,
                "avg_work_hours": {
                    "$cond": [
                        {"$gt": ["$work_hours_count", 0]},
                        {
                            "$round": [
                                {
                                    "$divide": [
                                        "$work_hours_sum",
                                        "$work_hours_count",
                                    ]
                                },
                                2,
                            ]
                        },
                        None,
                    ]
                },
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1,
            }
        },
        {"$sort": {"department": 1}},
    ]

    items = list(db.employees.aggregate(pipeline))

    # Return the contract's envelope.
    return {"month": month, "items": items}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str,
    limit: int = 10,
    department: Optional[str] = None,
):
    # Validate the month and limit.
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(
            status_code=422,
            detail="month must be in YYYY-MM format",
        )

    if limit < 1 or limit > 50:
        raise HTTPException(
            status_code=422,
            detail="limit must be between 1 and 50",
        )

    year, month_num = map(int, month.split("-"))

    month_start = date(year, month_num, 1)
    if month_num == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month_num + 1, 1)

    start_str = month_start.isoformat()
    next_str = next_month.isoformat()

    pipeline = [
        # Use the attendance date index to filter the requested month.
        {
            "$match": {
                "date": {
                    "$gte": start_str,
                    "$lt": next_str,
                }
            }
        },

        # Aggregate the stored late_minutes by employee.
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$gt": [
                                    {"$ifNull": ["$late_minutes", 0]},
                                    0,
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
            }
        },

        # Only employees with a positive total belong on the leaderboard.
        {"$match": {"total_late_minutes": {"$gt": 0}}},

        # Fetch employee details; ignore logs for deleted employees.
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},

        # The contract requires ranking within the selected department.
        *(
            [{"$match": {"employee.department": department}}]
            if department is not None
            else []
        ),

        # Rank by total minutes only, so equal totals receive equal ranks.
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {"rank": {"$rank": {}}},
            }
        },

        # Apply the limit to rank, preserving ties at the cutoff.
        {"$match": {"rank": {"$lte": limit}}},

        # Deterministic order for rows sharing a rank.
        {
            "$sort": {
                "total_late_minutes": -1,
                "employee.emp_code": 1,
            }
        },

        {
            "$project": {
                "_id": 0,
                "rank": 1,
                "emp_code": "$employee.emp_code",
                "name": "$employee.name",
                "department": "$employee.department",
                "total_late_minutes": 1,
                "late_count": 1,
            }
        },
    ]

    items = list(db.attendance_logs.aggregate(pipeline))

    return {"month": month, "items": items}

@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to"),
):
    # Validate strict YYYY-MM-DD dates.
    try:
        if (
            not re.fullmatch(r"\d{4}-\d{2}-\d{2}", from_date)
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", to_date)
        ):
            raise ValueError

        start = date.fromisoformat(from_date)
        end = date.fromisoformat(to_date)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="'from' and 'to' must be valid YYYY-MM-DD dates",
        )

    if end < start:
        raise HTTPException(
            status_code=422,
            detail="'to' must be on or after 'from'",
        )

    # The inclusive date range cannot exceed 92 days.
    number_of_days = (end - start).days + 1
    if number_of_days > 92:
        raise HTTPException(
            status_code=422,
            detail="Date range cannot exceed 92 days",
        )

    start_dt = datetime(
        start.year, start.month, start.day, tzinfo=timezone.utc
    )
    end_exclusive = end + timedelta(days=1)
    end_dt = datetime(
        end_exclusive.year,
        end_exclusive.month,
        end_exclusive.day,
        tzinfo=timezone.utc,
    )

    # Start with employees so a department with no attendance logs
    # can still produce a complete daily trend.
    pipeline = [
        {"$match": {"department": department}},
        {
            "$set": {
                "joined_date_obj": {
                    "$dateFromString": {
                        "dateString": "$joined_on",
                        "format": "%Y-%m-%d",
                    }
                }
            }
        },
        {
            "$group": {
                "_id": None,
                "employees": {
                    "$push": {
                        "emp_code": "$emp_code",
                        "joined_on": "$joined_date_obj",
                    }
                },
            }
        },

        # Generate every calendar day inside MongoDB.
        {
            "$set": {
                "days": {
                    "$map": {
                        "input": {
                            "$range": [
                                0,
                                {
                                    "$dateDiff": {
                                        "startDate": start_dt,
                                        "endDate": end_dt,
                                        "unit": "day",
                                    }
                                },
                            ]
                        },
                        "as": "offset",
                        "in": {
                            "$dateAdd": {
                                "startDate": start_dt,
                                "unit": "day",
                                "amount": "$$offset",
                            }
                        },
                    }
                }
            }
        },
        {"$unwind": "$days"},
        {
            "$set": {
                "day_string": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$days",
                        "timezone": "UTC",
                    }
                },
                "eligible_employees": {
                    "$filter": {
                        "input": "$employees",
                        "as": "emp",
                        "cond": {
                            "$lte": ["$$emp.joined_on", "$days"]
                        },
                    }
                },
            }
        },

        # Fetch logs only for this department's employees on this day.
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {
                    "day": "$day_string",
                    "codes": {
                        "$map": {
                            "input": "$employees",
                            "as": "emp",
                            "in": "$$emp.emp_code",
                        }
                    },
                },
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$date", "$$day"]},
                                    {"$in": ["$emp_code", "$$codes"]},
                                ]
                            }
                        }
                    }
                ],
                "as": "logs",
            }
        },

        # Compute daily metrics from the stored attendance fields.
        {
            "$set": {
                "headcount": {"$size": "$eligible_employees"},
                "is_working_day": {
                    "$and": [
                        {"$gte": [{"$dayOfWeek": "$days"}, 2]},
                        {"$lte": [{"$dayOfWeek": "$days"}, 6]},
                    ]
                },
                "present_count": {
                    "$sum": {
                        "$map": {
                            "input": {
                                "$filter": {
                                    "input": "$logs",
                                    "as": "log",
                                    "cond": {
                                        "$in": [
                                            "$$log.status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                }
                            },
                            "as": "log",
                            "in": {
                                "$cond": [
                                    {"$eq": ["$$log.half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                        }
                    }
                },
                "late_count": {
                    "$size": {
                        "$filter": {
                            "input": "$logs",
                            "as": "log",
                            "cond": {
                                "$gt": [
                                    {"$ifNull": ["$$log.late_minutes", 0]},
                                    0,
                                ]
                            },
                        }
                    }
                },
            }
        },

        # Attendance rate is null on weekends or when headcount is zero.
        {
            "$set": {
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]},
                            ]
                        },
                        {
                            "$divide": [
                                {
                                    "$floor": {
                                        "$add": [
                                            {
                                                "$multiply": [
                                                    {
                                                        "$divide": [
                                                            "$present_count",
                                                            "$headcount",
                                                        ]
                                                    },
                                                    10000,
                                                ]
                                            },
                                            0.5,
                                        ]
                                    }
                                },
                                10000,
                            ]
                        },
                        None,
                    ]
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": "$day_string",
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
            }
        },
        {"$sort": {"date": 1}},

        # Collect the sorted daily rows to calculate a rolling 7-row average.
        {
            "$group": {
                "_id": None,
                "items": {"$push": "$$ROOT"},
            }
        },
        {
            "$project": {
                "_id": 0,
                "items": {
                    "$map": {
                        "input": {"$range": [0, {"$size": "$items"}]},
                        "as": "i",
                        "in": {
                            "$let": {
                                "vars": {
                                    "current": {
                                        "$arrayElemAt": ["$items", "$$i"]
                                    },
                                    "window": {
                                        "$slice": [
                                            "$items",
                                            {
                                                "$max": [
                                                    0,
                                                    {"$subtract": ["$$i", 6]},
                                                ]
                                            },
                                            {
                                                "$min": [
                                                    7,
                                                    {"$add": ["$$i", 1]},
                                                ]
                                            },
                                        ]
                                    },
                                },
                                "in": {
                                    "$mergeObjects": [
                                        "$$current",
                                        {
                                            "moving_avg_7d": {
                                                "$let": {
                                                    "vars": {
                                                        "valid_rates": {
                                                            "$map": {
                                                                "input": {
                                                                    "$filter": {
                                                                        "input": "$$window",
                                                                        "as": "row",
                                                                        "cond": {
                                                                            "$ne": [
                                                                                "$$row.attendance_rate",
                                                                                None,
                                                                            ]
                                                                        },
                                                                    }
                                                                },
                                                                "as": "row",
                                                                "in": "$$row.attendance_rate",
                                                            }
                                                        }
                                                    },
                                                    "in": {
                                                        "$cond": [
                                                            {
                                                                "$gt": [
                                                                    {
                                                                        "$size": "$$valid_rates"
                                                                    },
                                                                    0,
                                                                ]
                                                            },
                                                            {
                                                                "$divide": [
                                                                    {
                                                                        "$floor": {
                                                                            "$add": [
                                                                                {
                                                                                    "$multiply": [
                                                                                        {
                                                                                            "$avg": "$$valid_rates"
                                                                                        },
                                                                                        10000,
                                                                                    ]
                                                                                },
                                                                                0.5,
                                                                            ]
                                                                        }
                                                                    },
                                                                    10000,
                                                                ]
                                                            },
                                                            None,
                                                        ]
                                                    },
                                                }
                                            }
                                        },
                                    ]
                                },
                            }
                        },
                    }
                },
            }
        },
    ]

    result = list(db.employees.aggregate(pipeline))

    # No employee documents means the department is unknown.
    if not result:
        raise HTTPException(
            status_code=404,
            detail="Unknown department",
        )

    return {
        "department": department,
        "items": result[0]["items"],
    }

# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
