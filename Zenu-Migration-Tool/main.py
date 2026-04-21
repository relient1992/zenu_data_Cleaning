from fastapi import FastAPI, File, UploadFile, BackgroundTasks, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from datetime import datetime
import shutil
import os
import uuid
import json
import sqlite3

# Import our custom engine
from etl_engine import MigrationEngine

app = FastAPI(title="Zenu Migration API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = "client_migrations"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ==========================================
# NEW: ROADMAP DATABASE SETUP
# ==========================================
ROADMAP_DB_DIR = r"C:\Users\Daryl\OneDrive - Zenu Realestate Pty Ltd\Documents\sql"
os.makedirs(ROADMAP_DB_DIR, exist_ok=True)
ROADMAP_DB_PATH = os.path.join(ROADMAP_DB_DIR, "db_roadmap.db")

def init_roadmap_db():
    with sqlite3.connect(ROADMAP_DB_PATH) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS crm_roadmap (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                crm_name TEXT UNIQUE NOT NULL,
                completion_percentage INTEGER DEFAULT 0,
                status TEXT,
                last_updated DATETIME
            )
        ''')
        conn.commit()

init_roadmap_db()

class RoadmapItem(BaseModel):
    crm_name: str
    completion_percentage: int

@app.get("/api/roadmap")
def get_roadmap():
    with sqlite3.connect(ROADMAP_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM crm_roadmap ORDER BY completion_percentage DESC, crm_name ASC")
        rows = cursor.fetchall()
        return {"status": "success", "data": [dict(row) for row in rows]}

@app.post("/api/roadmap")
def upsert_roadmap(item: RoadmapItem):
    status = "Completed" if item.completion_percentage >= 100 else "In Progress"
    with sqlite3.connect(ROADMAP_DB_PATH) as conn:
        conn.execute('''
            INSERT INTO crm_roadmap (crm_name, completion_percentage, status, last_updated)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(crm_name) DO UPDATE SET
                completion_percentage=excluded.completion_percentage,
                status=excluded.status,
                last_updated=excluded.last_updated
        ''', (item.crm_name, item.completion_percentage, status, datetime.now()))
        conn.commit()
    return {"status": "success", "message": f"Updated {item.crm_name} to {item.completion_percentage}%"}

@app.delete("/api/roadmap/{crm_name}")
def delete_roadmap(crm_name: str):
    with sqlite3.connect(ROADMAP_DB_PATH) as conn:
        conn.execute("DELETE FROM crm_roadmap WHERE crm_name = ?", (crm_name,))
        conn.commit()
    return {"status": "success", "message": f"Removed {crm_name} from roadmap."}

# ==========================================
# EXISTING MIGRATION ENDPOINTS
# ==========================================
@app.post("/api/run-migration")
async def run_migration(
    background_tasks: BackgroundTasks,
    mapping_file: UploadFile = File(...),
    csv_files: list[UploadFile] = File(...),
    source_crm: str = Form("Agentbox"),
    chunk_size: int = Form(500000)
):
    try:
        job_id = f"job_{uuid.uuid4().hex[:8]}"
        workspace = os.path.join(UPLOAD_DIR, job_id)
        os.makedirs(workspace, exist_ok=True)
        
        json_path = os.path.join(workspace, "mapping_rules.json")
        with open(json_path, "wb") as buffer:
            shutil.copyfileobj(mapping_file.file, buffer)
            
        for csv in csv_files:
            safe_filename = os.path.basename(csv.filename.replace('\\', '/'))
            csv_path = os.path.join(workspace, safe_filename)
            with open(csv_path, "wb") as buffer:
                shutil.copyfileobj(csv.file, buffer)
                
        # Pass chunk_size to the background task
        background_tasks.add_task(process_job, job_id, workspace, json_path, source_crm, chunk_size)
        
        return JSONResponse(content={
            "status": "success", 
            "job_id": job_id,
            "message": "Files received successfully. Engine starting."
        })
        
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

@app.get("/api/job-logs/{job_id}")
async def get_job_logs(job_id: str):
    log_path = os.path.join(UPLOAD_DIR, job_id, "process.log")
    if not os.path.exists(log_path):
        return JSONResponse(content={"logs": []})
    
    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    return JSONResponse(content={"logs": [line.strip() for line in lines]})

def process_job(job_id: str, workspace: str, json_path: str, source_crm: str, chunk_size: int):
    # Pass chunk_size to the Engine
    engine = MigrationEngine(job_id=job_id, workspace=workspace, source_crm=source_crm, chunk_size=chunk_size)
    try:
        engine.log(f"[{job_id}] Starting migration process for {source_crm}...")
        engine.load_csvs_to_sqlite()
        engine.run_mapping(json_path)
    except Exception as e:
        engine.log(f"[{job_id}] CRITICAL SYSTEM FAILURE: {str(e)}")
    finally:
        engine.close()