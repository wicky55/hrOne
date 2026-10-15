# REVIEW.md

| # | Where | What is wrong | How to notice it | How to fix it |
|---|---|---|---|---|
| 1 | `health()` | Returns success without checking MongoDB. | Stop MongoDB and request `/health`. | Ping MongoDB and return 503 if unavailable. |
| 2 | `list_employees()` | Pagination skips the first page and total ignores filters. | Request page 1 with page_size 2; filter by department. | Use `(page - 1) * page_size`, filtered count, and sorting. |
| 3 | `create_employee()` | Duplicate checks alone are not race-safe; shift times are not validated. | Send simultaneous duplicate creates or identical shift times. | Add a unique index, catch duplicate-key errors, and validate shift times. |
| 4 | `punch_in()` | Unknown employees are not handled; timestamp, status, IST date, and concurrency rules are incomplete. | Test an unknown employee, invalid timestamp, and simultaneous punches. | Validate inputs, apply R1/R2, and enforce a unique attendance key. |
| 5 | `compute_late_minutes()` | Does not correctly enforce all grace-period boundary cases. | Test 09:40:00 and 09:40:01 for a 09:30 shift. | Implement R1/R2 using timezone-aware calculations and whole seconds. |
| 6 | `compute_overtime()` | Does not fully handle overnight shifts or the 30-minute minimum. | Test an overnight shift and 29 versus 30 minutes of overtime. | Calculate the correct shift-end instant and apply R3. |
| 7 | `compute_work_hours()` | Uses ordinary float rounding instead of guaranteed half-up rounding. | Test values around a half-cent rounding boundary. | Use decimal arithmetic and R4/R8 rounding. |
| 8 | `list_attendance()` | Fetches all matching records into Python, sorts in Python, and returns a record ID. | Test pagination and inspect the response. | Sort, count, and paginate in MongoDB; omit internal IDs. |
| 9 | Application initialization | Required indexes are not created automatically. | Inspect indexes on a fresh database. | Create indexes idempotently at startup. |
| 10 | Remaining API contract | Punch-out, correction, analytics, and explain endpoints are missing. | Inspect `/docs` against `openapi.yaml`. | Implement and test each required endpoint. |

## Reviewed and retained

- `compute_work_hours()` correctly uses the elapsed time between two instants in principle; its rounding behavior still needs correction.
- The existing Pydantic request models provide a starting point, but stricter validation is required.
- The sample data is illustrative, not sufficient to prove analytics correctness.
