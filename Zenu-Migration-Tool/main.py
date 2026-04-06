from fastapi import FastAPI, File, UploadFile, BackgroundTasks, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import shutil
import os
import uuid
import json

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

@app.post("/api/run-migration")
async def run_migration(
    background_tasks: BackgroundTasks,
    mapping_file: UploadFile = File(...),
    csv_files: list[UploadFile] = File(...),
    source_crm: str = Form("Agentbox")
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
                
        # Trigger the engine in the background
        background_tasks.add_task(process_job, job_id, workspace, json_path, source_crm)
        
        return JSONResponse(content={
            "status": "success", 
            "job_id": job_id,
            "message": "Files received successfully. Engine starting."
        })
        
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

# NEW: Live Polling Endpoint for the Web UI
@app.get("/api/job-logs/{job_id}")
async def get_job_logs(job_id: str):
    log_path = os.path.join(UPLOAD_DIR, job_id, "process.log")
    if not os.path.exists(log_path):
        return JSONResponse(content={"logs": []})
    
    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    return JSONResponse(content={"logs": [line.strip() for line in lines]})

def process_job(job_id: str, workspace: str, json_path: str, source_crm: str):
    engine = MigrationEngine(job_id=job_id, workspace=workspace, source_crm=source_crm)
    try:
        engine.log(f"[{job_id}] Starting migration process for {source_crm}...")
        engine.load_csvs_to_sqlite()
        engine.run_mapping(json_path)
    except Exception as e:
        engine.log(f"[{job_id}] CRITICAL SYSTEM FAILURE: {str(e)}")
    finally:
        engine.close()