
from datetime import datetime, timezone
from app.main import (
    compute_late_minutes,
    compute_work_hours,
    compute_overtime,
)

IST = timezone.utc  # Test timestamps below are explicitly UTC.

def dt(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

# 1. Work-hours calculation: 9 hours 7 minutes = 9.12 hours.
assert compute_work_hours(
    dt("2026-07-06T03:58:00Z"),
    dt("2026-07-06T13:05:00Z"),
) == 9.12

print("Work-hours test passed")

# 2. Overtime below 30 minutes must be zero.
assert compute_overtime(
    dt("2026-07-06T13:05:00Z"),
    "18:30",
    "2026-07-06",
    "09:30",
) == 0

print("Overtime threshold test passed")
print("Basic helper tests passed")
