
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sqlite3
from datetime import datetime

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Standup(BaseModel):
    name: str
    completed: str
    working_on: str
    blocker: str


def create_database():
    connection = sqlite3.connect("pulsedesk.db")
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS standups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            completed TEXT,
            working_on TEXT,
            blocker TEXT,
            submitted_at TEXT
        )
    """)

    connection.commit()

    try:
        cursor.execute("ALTER TABLE standups ADD COLUMN name TEXT")
        connection.commit()
    except sqlite3.OperationalError:
        pass

    connection.close()


create_database()


@app.get("/")
def home():
    return {"message": "PulseDesk API is running!"}


@app.post("/standup")
def submit_standup(standup: Standup):

    connection = sqlite3.connect("pulsedesk.db")
    cursor = connection.cursor()

    submitted_at = datetime.now().isoformat()

    cursor.execute("""
        INSERT INTO standups (
            name,
            completed,
            working_on,
            blocker,
            submitted_at
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
        standup.name,
        standup.completed,
        standup.working_on,
        standup.blocker,
        submitted_at
    ))

    connection.commit()

    update_id = cursor.lastrowid

    connection.close()

    return {
        "message": "Standup saved successfully!",
        "update_id": update_id,
        "submitted_at": submitted_at,
        "data": standup
    }


@app.get("/standups")
def get_standups():

    connection = sqlite3.connect("pulsedesk.db")
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            id,
            name,
            completed,
            working_on,
            blocker,
            submitted_at
        FROM standups
        ORDER BY id DESC
    """)

    rows = cursor.fetchall()

    connection.close()

    standups = []

    for row in rows:
        standups.append({
            "id": row[0],
            "name": row[1],
            "completed": row[2],
            "working_on": row[3],
            "blocker": row[4],
            "submitted_at": row[5]
        })
     
    return {
        "count": len(standups),
        "standups": standups
    }
@app.get("/standups/{update_id}")
def get_single_standup(update_id: int):

    connection = sqlite3.connect("pulsedesk.db")
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            id,
            name,
            completed,
            working_on,
            blocker,
            submitted_at
        FROM standups
        WHERE id = ?
    """, (update_id,))

    row = cursor.fetchone()

    connection.close()

    if row is None:
        return {
            "message": "Standup not found",
            "update_id": update_id
        }

    return {
        "id": row[0],
        "name": row[1],
        "completed": row[2],
        "working_on": row[3],
        "blocker": row[4],
        "submitted_at": row[5]
    }         
@app.get("/digest")
def get_digest():

    connection = sqlite3.connect("pulsedesk.db")
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            id,
            name,
            completed,
            working_on,
            blocker,
            submitted_at
        FROM standups
        ORDER BY id DESC
    """)

    rows = cursor.fetchall()

    connection.close()

    updates = []

    for row in rows:
        updates.append({
            "id": row[0],
            "name": row[1],
            "completed": row[2],
            "working_on": row[3],
            "blocker": row[4],
            "submitted_at": row[5]
        })

    blockers = []

    for update in updates:
        if update["blocker"] and update["blocker"].lower() not in [
            "no",
            "none",
            "no blockers",
            "nothing"
        ]:
            blockers.append({
                "update_id": update["id"],
                "name": update["name"],
                "blocker": update["blocker"]
            })

    return {
        "total_updates": len(updates),
        "total_blockers": len(blockers),
        "updates": updates,
        "blockers": blockers
    }