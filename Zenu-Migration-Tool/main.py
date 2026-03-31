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

# Allow your local HTML file to talk to this server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Directory where client jobs will be isolated
UPLOAD_DIR = "client_migrations"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.post("/api/run-migration")
async def run_migration(
    background_tasks: BackgroundTasks,
    mapping_file: UploadFile = File(...),
    csv_files: list[UploadFile] = File(...)
):
    try:
        # 1. Generate a unique ID for this client's job
        job_id = f"job_{uuid.uuid4().hex[:8]}"
        workspace = os.path.join(UPLOAD_DIR, job_id)
        os.makedirs(workspace, exist_ok=True)
        
        # 2. Save the JSON mapping file
        json_path = os.path.join(workspace, "mapping_rules.json")
        with open(json_path, "wb") as buffer:
            shutil.copyfileobj(mapping_file.file, buffer)
            
        # 3. Save all the uploaded CSV files
        for csv in csv_files:
            # FIX: Strip any folder names sent by the browser (e.g., "3740/agent.csv" -> "agent.csv")
            # We replace backslashes with forward slashes to handle Windows paths safely
            safe_filename = os.path.basename(csv.filename.replace('\\', '/'))
            
            csv_path = os.path.join(workspace, safe_filename)
            with open(csv_path, "wb") as buffer:
                shutil.copyfileobj(csv.file, buffer)
                
        # 4. Trigger the heavy lifting in the background
        background_tasks.add_task(process_job, job_id, workspace, json_path)
        
        return JSONResponse(content={
            "status": "success", 
            "job_id": job_id,
            "message": "Files received successfully. The ETL engine is now building the SQLite database and processing the rules."
        })
        
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


def process_job(job_id: str, workspace: str, json_path: str):
    """This runs in the background so the user's browser doesn't freeze."""
    print(f"[{job_id}] Starting migration process...")
    engine = MigrationEngine(job_id=job_id, workspace=workspace)
    try:
        # Step A: Dump CSVs into our portable SQLite database
        engine.load_csvs_to_sqlite()
        
        # Step B: Run the transformations based on your JSON
        engine.run_mapping(json_path)
        
    except Exception as e:
        print(f"[{job_id}] FAILED: {str(e)}")
    finally:
        engine.close()