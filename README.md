# HROne Employee Attendance & Analytics API

A FastAPI and MongoDB backend for employee management, attendance tracking, and attendance analytics.

## Tech Stack
- Python
- FastAPI
- MongoDB
- PyMongo

## Features
- Employee management
- Attendance punch-in and punch-out
- Attendance history and filtering
- Monthly employee attendance reports
- Department attendance summaries
- Late-comer leaderboard
- Daily attendance trends
- MongoDB indexes and query explain plans

## Setup

1. Clone the repository:
   ```bash
   git clone YOUR_REPOSITORY_URL
   cd hrone-attendance-api
   ```

2. Create a virtual environment:
   ```bash
   python -m venv .venv
   ```

3. Activate it on Windows:
   ```bash
   .venv\Scripts\activate
   ```

4. Install dependencies:
   ```bash
   python -m pip install -r requirements.txt
   ```

5. Create a local `.env` file with your MongoDB configuration:
   ```text
   MONGO_URI=mongodb://localhost:27017
   MONGO_DB=attendance_db
   ```

6. Start the API:
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```

7. Open the interactive API documentation:
   http://127.0.0.1:8000/docs

## Configuration
Keep `.env` and all credentials out of version control. Configure your own MongoDB instance before running the application.

## Status
Developed as a software engineering assignment project. Test all endpoints against the assignment's API contract before submission.
