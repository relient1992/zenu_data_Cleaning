from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import sqlite3
import json
import os

# =====================================================================
# MIGRATION JOB TRACKER
# ---------------------------------------------------------------------
# One record per client migration, with:
#   - Entity-level progress (Contacts, Prospects, Appraisals, ...)
#   - An issues log (severity / status / resolution)
#   - Optional link to an engine job_id, enabling one-click sync:
#       * entity rows auto-created from the job's reconciliation
#         row counts
#       * FAIL/WARN findings auto-imported into the issues log
#
# Mounted into main.py with:
#   from job_tracker import tracker_router
#   app.include_router(tracker_router)
# =====================================================================

# Same folder as the roadmap DB. Override with env var TRACKER_DB_DIR
# if you ever need to relocate it.
TRACKER_DB_DIR = os.environ.get(
    "TRACKER_DB_DIR",
    r"C:\Users\Daryl\OneDrive - Zenu Realestate Pty Ltd\Documents\sql"
)
os.makedirs(TRACKER_DB_DIR, exist_ok=True)
TRACKER_DB_PATH = os.path.join(TRACKER_DB_DIR, "db_job_tracker.db")

# Where the migration engine stores job workspaces (matches main.py)
UPLOAD_DIR = "client_migrations"

MIGRATION_STATUSES = ["Not Started", "In Progress", "QA",
                      "Client Review", "Signed Off", "On Hold"]
ENTITY_STATUSES = ["Pending", "Mapped", "Migrated", "Validated", "Signed Off"]

tracker_router = APIRouter(prefix="/api/tracker", tags=["Job Tracker"])


def _conn():
    conn = sqlite3.connect(TRACKER_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_tracker_db():
    with _conn() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS migrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_name TEXT NOT NULL,
                source_crm TEXT,
                target_crm TEXT DEFAULT 'Zenu',
                job_id TEXT,
                status TEXT DEFAULT 'Not Started',
                due_date TEXT,
                assigned_to TEXT,
                notes TEXT,
                created_at DATETIME,
                updated_at DATETIME
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                migration_id INTEGER NOT NULL,
                entity_name TEXT NOT NULL,
                status TEXT DEFAULT 'Pending',
                row_count INTEGER,
                notes TEXT,
                updated_at DATETIME,
                UNIQUE(migration_id, entity_name),
                FOREIGN KEY (migration_id) REFERENCES migrations(id)
                    ON DELETE CASCADE
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                migration_id INTEGER NOT NULL,
                severity TEXT DEFAULT 'WARN',
                title TEXT NOT NULL,
                detail TEXT,
                status TEXT DEFAULT 'Open',
                created_at DATETIME,
                resolved_at DATETIME,
                FOREIGN KEY (migration_id) REFERENCES migrations(id)
                    ON DELETE CASCADE
            )
        ''')
        conn.commit()


init_tracker_db()


# ---------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------
class MigrationIn(BaseModel):
    id: Optional[int] = None
    client_name: str
    source_crm: Optional[str] = None
    target_crm: Optional[str] = "Zenu"
    job_id: Optional[str] = None
    status: Optional[str] = "Not Started"
    due_date: Optional[str] = None       # dd/mm/yyyy free text
    assigned_to: Optional[str] = None
    notes: Optional[str] = None


class EntityIn(BaseModel):
    migration_id: int
    entity_name: str
    status: Optional[str] = "Pending"
    row_count: Optional[int] = None
    notes: Optional[str] = None


class IssueIn(BaseModel):
    migration_id: int
    severity: Optional[str] = "WARN"
    title: str
    detail: Optional[str] = None


# ---------------------------------------------------------------------
# MIGRATIONS
# ---------------------------------------------------------------------
@tracker_router.get("/migrations")
def list_migrations():
    with _conn() as conn:
        rows = conn.execute('''
            SELECT m.*,
                (SELECT COUNT(*) FROM entities e
                    WHERE e.migration_id = m.id) AS entity_count,
                (SELECT COUNT(*) FROM entities e
                    WHERE e.migration_id = m.id
                      AND e.status IN ('Validated','Signed Off')) AS entities_done,
                (SELECT COUNT(*) FROM issues i
                    WHERE i.migration_id = m.id
                      AND i.status = 'Open') AS open_issues
            FROM migrations m
            ORDER BY m.updated_at DESC
        ''').fetchall()
        return {"status": "success", "data": [dict(r) for r in rows]}


@tracker_router.get("/migrations/{migration_id}")
def get_migration(migration_id: int):
    with _conn() as conn:
        mig = conn.execute("SELECT * FROM migrations WHERE id = ?",
                           (migration_id,)).fetchone()
        if not mig:
            return JSONResponse(status_code=404, content={
                "status": "error", "message": "Migration not found."})
        entities = conn.execute(
            "SELECT * FROM entities WHERE migration_id = ? ORDER BY entity_name",
            (migration_id,)).fetchall()
        issues = conn.execute(
            "SELECT * FROM issues WHERE migration_id = ? "
            "ORDER BY status DESC, created_at DESC",
            (migration_id,)).fetchall()
        return {"status": "success",
                "data": {"migration": dict(mig),
                         "entities": [dict(e) for e in entities],
                         "issues": [dict(i) for i in issues]}}


@tracker_router.post("/migrations")
def upsert_migration(item: MigrationIn):
    now = datetime.now()
    with _conn() as conn:
        if item.id:
            conn.execute('''
                UPDATE migrations SET client_name=?, source_crm=?, target_crm=?,
                    job_id=?, status=?, due_date=?, assigned_to=?, notes=?,
                    updated_at=?
                WHERE id=?
            ''', (item.client_name, item.source_crm, item.target_crm,
                  item.job_id, item.status, item.due_date, item.assigned_to,
                  item.notes, now, item.id))
            conn.commit()
            return {"status": "success", "id": item.id,
                    "message": f"Updated migration '{item.client_name}'."}
        cur = conn.execute('''
            INSERT INTO migrations (client_name, source_crm, target_crm,
                job_id, status, due_date, assigned_to, notes,
                created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        ''', (item.client_name, item.source_crm, item.target_crm,
              item.job_id, item.status, item.due_date, item.assigned_to,
              item.notes, now, now))
        conn.commit()
        return {"status": "success", "id": cur.lastrowid,
                "message": f"Created migration '{item.client_name}'."}


@tracker_router.delete("/migrations/{migration_id}")
def delete_migration(migration_id: int):
    with _conn() as conn:
        conn.execute("DELETE FROM migrations WHERE id = ?", (migration_id,))
        conn.commit()
    return {"status": "success", "message": "Migration deleted."}


# ---------------------------------------------------------------------
# ENTITIES
# ---------------------------------------------------------------------
@tracker_router.post("/entities")
def upsert_entity(item: EntityIn):
    now = datetime.now()
    with _conn() as conn:
        conn.execute('''
            INSERT INTO entities (migration_id, entity_name, status,
                row_count, notes, updated_at)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(migration_id, entity_name) DO UPDATE SET
                status=excluded.status,
                row_count=COALESCE(excluded.row_count, entities.row_count),
                notes=COALESCE(excluded.notes, entities.notes),
                updated_at=excluded.updated_at
        ''', (item.migration_id, item.entity_name, item.status,
              item.row_count, item.notes, now))
        conn.commit()
        _touch_migration(conn, item.migration_id)
    return {"status": "success",
            "message": f"Entity '{item.entity_name}' saved."}


@tracker_router.delete("/entities/{entity_id}")
def delete_entity(entity_id: int):
    with _conn() as conn:
        conn.execute("DELETE FROM entities WHERE id = ?", (entity_id,))
        conn.commit()
    return {"status": "success", "message": "Entity removed."}


# ---------------------------------------------------------------------
# ISSUES
# ---------------------------------------------------------------------
@tracker_router.post("/issues")
def add_issue(item: IssueIn):
    with _conn() as conn:
        conn.execute('''
            INSERT INTO issues (migration_id, severity, title, detail,
                status, created_at)
            VALUES (?,?,?,?, 'Open', ?)
        ''', (item.migration_id, item.severity, item.title, item.detail,
              datetime.now()))
        conn.commit()
        _touch_migration(conn, item.migration_id)
    return {"status": "success", "message": "Issue logged."}


@tracker_router.patch("/issues/{issue_id}/toggle")
def toggle_issue(issue_id: int):
    with _conn() as conn:
        row = conn.execute("SELECT status FROM issues WHERE id = ?",
                           (issue_id,)).fetchone()
        if not row:
            return JSONResponse(status_code=404, content={
                "status": "error", "message": "Issue not found."})
        if row["status"] == "Open":
            conn.execute("UPDATE issues SET status='Resolved', resolved_at=? "
                         "WHERE id=?", (datetime.now(), issue_id))
        else:
            conn.execute("UPDATE issues SET status='Open', resolved_at=NULL "
                         "WHERE id=?", (issue_id,))
        conn.commit()
    return {"status": "success", "message": "Issue status toggled."}


@tracker_router.delete("/issues/{issue_id}")
def delete_issue(issue_id: int):
    with _conn() as conn:
        conn.execute("DELETE FROM issues WHERE id = ?", (issue_id,))
        conn.commit()
    return {"status": "success", "message": "Issue deleted."}


# ---------------------------------------------------------------------
# SYNC FROM RECONCILIATION
# Pulls the linked job's reconciliation_summary.json:
#   - row_counts   -> entity rows (status 'Migrated', with counts)
#   - FAIL / WARN  -> open issues (skips duplicates already imported)
# ---------------------------------------------------------------------
@tracker_router.post("/migrations/{migration_id}/sync")
def sync_from_reconciliation(migration_id: int):
    with _conn() as conn:
        mig = conn.execute("SELECT * FROM migrations WHERE id = ?",
                           (migration_id,)).fetchone()
        if not mig:
            return JSONResponse(status_code=404, content={
                "status": "error", "message": "Migration not found."})
        job_id = mig["job_id"]
        if not job_id:
            return JSONResponse(status_code=400, content={
                "status": "error",
                "message": "No job_id linked to this migration. "
                           "Edit the migration and set its Job ID first."})

        summary_path = os.path.join(UPLOAD_DIR, job_id,
                                    "reconciliation_summary.json")
        if not os.path.exists(summary_path):
            return JSONResponse(status_code=404, content={
                "status": "error",
                "message": f"No reconciliation report found for job "
                           f"'{job_id}'. Run reconciliation first."})

        with open(summary_path, "r", encoding="utf-8") as f:
            summary = json.load(f)
        if summary.get("status") != "complete":
            return JSONResponse(status_code=400, content={
                "status": "error",
                "message": "Reconciliation has not completed for this job."})

        now = datetime.now()
        entities_synced = 0
        for rc in summary.get("row_counts", []):
            conn.execute('''
                INSERT INTO entities (migration_id, entity_name, status,
                    row_count, updated_at)
                VALUES (?,?, 'Migrated', ?, ?)
                ON CONFLICT(migration_id, entity_name) DO UPDATE SET
                    row_count=excluded.row_count,
                    updated_at=excluded.updated_at
            ''', (migration_id, rc["output_group"], rc["rows"], now))
            entities_synced += 1

        issues_added = 0
        existing = {r["title"] for r in conn.execute(
            "SELECT title FROM issues WHERE migration_id = ?",
            (migration_id,)).fetchall()}
        for iss in summary.get("issues", []):
            title = f"[Recon] {iss['area']}: {iss['detail'][:140]}"
            if title in existing:
                continue
            conn.execute('''
                INSERT INTO issues (migration_id, severity, title, detail,
                    status, created_at)
                VALUES (?,?,?,?, 'Open', ?)
            ''', (migration_id, iss["severity"], title, iss["detail"], now))
            issues_added += 1

        _touch_migration(conn, migration_id)
        conn.commit()

    return {"status": "success",
            "message": f"Synced {entities_synced} entity group(s) and "
                       f"imported {issues_added} new issue(s) from "
                       f"reconciliation of job '{job_id}'."}


def _touch_migration(conn, migration_id):
    conn.execute("UPDATE migrations SET updated_at=? WHERE id=?",
                 (datetime.now(), migration_id))


# ---------------------------------------------------------------------
# Reference lists for the UI dropdowns
# ---------------------------------------------------------------------
@tracker_router.get("/meta")
def get_meta():
    return {"status": "success",
            "migration_statuses": MIGRATION_STATUSES,
            "entity_statuses": ENTITY_STATUSES}