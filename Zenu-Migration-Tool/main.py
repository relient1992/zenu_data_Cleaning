from fastapi import FastAPI, File, UploadFile, BackgroundTasks, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel
from datetime import datetime
import shutil
import os
import uuid
import json
import sqlite3
import re
import io
import csv
import zipfile
import pandas as pd

# Import our custom engine
from etl_engine import MigrationEngine

# NEW: Reconciliation engine
from reconciliation import ReconciliationEngine

# NEW: Job tracker router
from job_tracker import tracker_router

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

# NEW: Migration Job Tracker (see job_tracker.py)
app.include_router(tracker_router)

# ==========================================
# ROADMAP DATABASE SETUP
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

@app.get("/api/job-progress/{job_id}")
async def get_job_progress(job_id: str):
    """Live percentage/stage for the run overlay's progress bar."""
    idle = {"percent": 0, "stage": "Queued", "detail": "Waiting for the engine to start",
            "elapsed_seconds": 0, "done": False}
    progress_path = os.path.join(UPLOAD_DIR, job_id, "progress.json")
    if not os.path.exists(progress_path):
        return JSONResponse(content=idle)
    try:
        with open(progress_path, "r", encoding="utf-8") as f:
            return JSONResponse(content=json.load(f))
    except Exception:
        # File is written frequently; a torn read just means "try again shortly".
        return JSONResponse(content=idle)


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

# ==========================================
# NEW: POST-MIGRATION RECONCILIATION ENDPOINTS
# ==========================================

def run_reconciliation_job(job_id: str, workspace: str):
    """Background task wrapper for the reconciliation engine."""
    recon = ReconciliationEngine(job_id=job_id, workspace=workspace)
    recon.run()

@app.get("/api/jobs")
def list_jobs():
    """List all migration job workspaces so the recon page can pick one."""
    jobs = []
    if os.path.exists(UPLOAD_DIR):
        for name in sorted(os.listdir(UPLOAD_DIR), reverse=True):
            ws = os.path.join(UPLOAD_DIR, name)
            if os.path.isdir(ws):
                has_report = os.path.exists(os.path.join(ws, "reconciliation_summary.json"))
                modified = datetime.fromtimestamp(os.path.getmtime(ws)).strftime("%d/%m/%Y %H:%M")
                jobs.append({"job_id": name, "modified": modified, "has_report": has_report})
    return {"status": "success", "data": jobs}

@app.post("/api/reconcile/{job_id}")
async def start_reconciliation(job_id: str, background_tasks: BackgroundTasks):
    """Kick off reconciliation for a completed migration job."""
    workspace = os.path.join(UPLOAD_DIR, job_id)
    if not os.path.isdir(workspace):
        return JSONResponse(status_code=404, content={
            "status": "error", "message": f"Job workspace '{job_id}' not found."})

    # Mark as pending so the UI can poll while it runs
    pending_path = os.path.join(workspace, "reconciliation_summary.json")
    with open(pending_path, "w", encoding="utf-8") as f:
        json.dump({"status": "running", "job_id": job_id}, f)

    background_tasks.add_task(run_reconciliation_job, job_id, workspace)
    return {"status": "success", "job_id": job_id,
            "message": "Reconciliation started. Poll /api/reconcile-report/{job_id}."}

@app.get("/api/reconcile-report/{job_id}")
async def get_reconciliation_report(job_id: str):
    """Return the JSON summary of the reconciliation (or its running state)."""
    summary_path = os.path.join(UPLOAD_DIR, job_id, "reconciliation_summary.json")
    if not os.path.exists(summary_path):
        return JSONResponse(content={"status": "not_found", "job_id": job_id})
    with open(summary_path, "r", encoding="utf-8") as f:
        return JSONResponse(content=json.load(f))

@app.get("/api/reconcile-download/{job_id}")
async def download_reconciliation_report(job_id: str):
    """Download the client-shareable Excel reconciliation report."""
    report_path = os.path.join(UPLOAD_DIR, job_id, "Reconciliation_Report.xlsx")
    if not os.path.exists(report_path):
        return JSONResponse(status_code=404, content={
            "status": "error", "message": "Report not generated yet."})
    return FileResponse(
        report_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"Reconciliation_Report_{job_id}.xlsx")

# ==========================================
# NEW: EXCEL CONSOLIDATION ENDPOINT
# ==========================================
CONSOLIDATE_DIR = os.path.join(UPLOAD_DIR, "_consolidated")
os.makedirs(CONSOLIDATE_DIR, exist_ok=True)

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _sanitize_sheet_name(name: str, used: set) -> str:
    """Excel sheet names: max 31 chars, no : \\ / ? * [ ], must be unique."""
    base = os.path.splitext(os.path.basename(name))[0]
    base = re.sub(r"[:\\/?*\[\]]", "_", base).strip() or "Sheet"
    base = base[:31]
    candidate = base
    i = 1
    while candidate.lower() in used:
        suffix = f"_{i}"
        candidate = base[:31 - len(suffix)] + suffix
        i += 1
    used.add(candidate.lower())
    return candidate


@app.post("/api/consolidate-excel")
async def consolidate_excel(
    files: list[UploadFile] = File(...),
    mode: str = Form("append"),
    output_name: str = Form("consolidated"),
):
    """
    Consolidate several Excel files into a single .xlsx file.

    mode = "append"  -> stack every sheet's rows into one combined sheet
    mode = "sheets"  -> put each source file (its first sheet) on its own tab
    """
    if not files:
        return JSONResponse(status_code=400, content={
            "status": "error", "message": "No files were uploaded."})

    job_id = f"consolidate_{uuid.uuid4().hex[:8]}"
    workspace = os.path.join(CONSOLIDATE_DIR, job_id)
    os.makedirs(workspace, exist_ok=True)

    safe_out = re.sub(r"[^A-Za-z0-9._ -]", "_", output_name).strip() or "consolidated"
    if not safe_out.lower().endswith(".xlsx"):
        safe_out += ".xlsx"
    out_path = os.path.join(workspace, safe_out)

    try:
        # Save uploads to disk first
        saved = []
        for f in files:
            fname = os.path.basename(f.filename.replace("\\", "/"))
            if not fname.lower().endswith((".xlsx", ".xls")):
                continue
            dest = os.path.join(workspace, fname)
            with open(dest, "wb") as buffer:
                shutil.copyfileobj(f.file, buffer)
            saved.append((fname, dest))

        if not saved:
            return JSONResponse(status_code=400, content={
                "status": "error",
                "message": "No valid Excel files (.xlsx/.xls) were found in the upload."})

        files_processed = 0
        total_rows = 0

        if mode == "sheets":
            # One tab per source file (first sheet of each).
            used_names = set()
            with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
                for fname, path in saved:
                    df = pd.read_excel(path, dtype=str)
                    df = df.fillna("")
                    sheet = _sanitize_sheet_name(fname, used_names)
                    df.to_excel(writer, sheet_name=sheet, index=False)
                    files_processed += 1
                    total_rows += len(df)
        else:
            # Default: append every sheet of every file into a single sheet.
            frames = []
            for fname, path in saved:
                sheets = pd.read_excel(path, sheet_name=None, dtype=str)
                for sheet_name, df in sheets.items():
                    if df.empty:
                        continue
                    df = df.fillna("")
                    df.insert(0, "__source_file__", fname)
                    df.insert(1, "__source_sheet__", sheet_name)
                    frames.append(df)
                files_processed += 1

            if not frames:
                return JSONResponse(status_code=400, content={
                    "status": "error", "message": "All uploaded files were empty."})

            combined = pd.concat(frames, ignore_index=True, sort=False)
            total_rows = len(combined)
            with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
                combined.to_excel(writer, sheet_name="Consolidated", index=False)

        return FileResponse(
            out_path,
            media_type=XLSX_MEDIA_TYPE,
            filename=safe_out,
            headers={
                "X-Files-Processed": str(files_processed),
                "X-Total-Rows": str(total_rows),
            },
        )

    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


# ==========================================
# NEW: IMPORT PREP ENDPOINT
# Replicates the "Internal Output" flow of the Import Prep .xlsm macro:
#   Input file  ->  reorder/rename columns to match the Zenu template ("Compare To")
#               ->  remove special characters  ->  CSV output (split at 15,000 rows)
# ==========================================
IMPORT_PREP_DIR = os.path.join(UPLOAD_DIR, "_import_prep")
os.makedirs(IMPORT_PREP_DIR, exist_ok=True)

# Sheets the macro skips.
IMPORT_PREP_SKIP_SHEETS = {"document history", "instructions", "summary"}

# Tokens the macro searches for to locate the header row within a data sheet.
IMPORT_PREP_HEADER_TOKENS = [
    "contact_identifier", "property_identifier", "contact_id",
    "property_id", "zenu_contact_id", "zenu_property_id",
]

IMPORT_PREP_MAX_ROWS = 15000

# Everything NOT in this set (after non-ASCII is stripped) is disallowed.
# Mirrors the VBA regex in DealSpecialChars — leaves letters/digits/space and a
# specific punctuation set; drops ~ ` |, and " is converted to ' separately.
_IMPORT_PREP_DISALLOWED = re.compile(r"[^A-Za-z0-9 ,\-.:;{}\[\]_&'\\/<>%+=@#!$^*()?\r\n]")


def _import_prep_clean(text: str):
    """Return (cleaned_text, changed?). Faithful to RemoveNonASCII + DealSpecialChars."""
    # RemoveNonASCII: keep only printable ASCII (32..126)
    token = "".join(ch for ch in text if 32 <= ord(ch) <= 126)
    # Remaining disallowed ASCII: " -> '  ,  everything else removed
    cleaned = _IMPORT_PREP_DISALLOWED.sub(
        lambda m: "'" if m.group(0) == '"' else "", token)
    return cleaned, (cleaned != text)


def _import_prep_stringify(val, header_lower: str) -> str:
    """Format a raw cell value the way the macro would before cleaning."""
    if pd.isna(val):
        return ""
    # Dates -> dd/mm/yyyy (with time for inspection start/end)
    if hasattr(val, "strftime"):
        if header_lower in ("inspection_start_date", "inspection_end_date"):
            return val.strftime("%d/%m/%Y %H:%M")
        return val.strftime("%d/%m/%Y")
    # Whole-number floats -> integer text (so 12.0 becomes "12", not "12.0")
    if isinstance(val, float) and val.is_integer():
        return str(int(val))
    return str(val)


def _import_prep_read_sheets(path: str, filename: str):
    """Return an ordered dict {sheet_name: DataFrame(header=None)}."""
    low = filename.lower()
    if low.endswith(".csv") or low.endswith(".txt"):
        stem = os.path.splitext(os.path.basename(filename))[0]
        df = pd.read_csv(path, header=None, dtype=object, keep_default_na=False)
        return {stem: df}
    return pd.read_excel(path, sheet_name=None, header=None, dtype=object)


def _import_prep_template_headers(path: str, filename: str):
    sheets = _import_prep_read_sheets(path, filename)
    first = next(iter(sheets.values()))
    if first.empty:
        return []
    row0 = first.iloc[0].tolist()
    headers = []
    for cell in row0:
        headers.append("" if pd.isna(cell) else str(cell).strip())
    # Trim trailing blanks (mirrors reading up to the last populated header column)
    while headers and headers[-1] == "":
        headers.pop()
    return headers


def _import_prep_find_header_row(df) -> int:
    for r in range(len(df)):
        cells = {str(x).strip().lower() for x in df.iloc[r].tolist() if not pd.isna(x)}
        if cells & set(IMPORT_PREP_HEADER_TOKENS):
            return r
    return 0  # fallback: treat the first row as the header


def _import_prep_build_sheet(df, template_headers):
    """Map one input sheet onto the template columns. Returns (rows, changed_count)."""
    header_row = _import_prep_find_header_row(df)
    input_headers = df.iloc[header_row].tolist()

    # header(lower) -> column index (first occurrence wins)
    col_index = {}
    for idx, h in enumerate(input_headers):
        if pd.isna(h):
            continue
        key = str(h).strip().lower()
        if key not in col_index:
            col_index[key] = idx

    matched = []  # (template_col_position, input_col_idx, header_lower)
    for pos, th in enumerate(template_headers):
        idx = col_index.get(th.strip().lower())
        if idx is not None:
            matched.append((pos, idx, th.strip().lower()))

    data = df.iloc[header_row + 1:]
    rows = [list(template_headers)]
    changed = 0

    for _, drow in data.iterrows():
        out = ["" for _ in template_headers]
        for pos, idx, header_lower in matched:
            raw = drow.iloc[idx] if idx < len(drow) else ""
            formatted = _import_prep_stringify(raw, header_lower)
            cleaned, was_changed = _import_prep_clean(formatted)
            if was_changed:
                changed += 1
            out[pos] = cleaned
        rows.append(out)

    # Trim trailing fully-empty data rows
    while len(rows) > 1 and all(c == "" for c in rows[-1]):
        rows.pop()

    return rows, changed


def _import_prep_csv_bytes(rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")


def _import_prep_chunk(rows, max_rows):
    """Split into [header + up to max_rows data] chunks. Returns list of row-lists."""
    header = rows[0]
    data = rows[1:]
    if len(data) <= max_rows:
        return [rows]
    chunks = []
    for start in range(0, len(data), max_rows):
        chunks.append([header] + data[start:start + max_rows])
    return chunks


def _import_prep_csv_name(sheet_name: str) -> str:
    name = str(sheet_name).strip()
    name = name.replace(" ", "_")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return (name or "output").lower() + ".csv"


@app.post("/api/import-prep")
async def import_prep(
    input_file: UploadFile = File(...),
    template_file: UploadFile = File(...),
    mode: str = Form("remove"),
):
    if mode != "remove":
        return JSONResponse(status_code=400, content={
            "status": "error",
            "message": "Only 'remove' mode is supported in this tool."})

    job_id = f"import_prep_{uuid.uuid4().hex[:8]}"
    workspace = os.path.join(IMPORT_PREP_DIR, job_id)
    os.makedirs(workspace, exist_ok=True)

    try:
        in_name = os.path.basename(input_file.filename.replace("\\", "/"))
        tpl_name = os.path.basename(template_file.filename.replace("\\", "/"))
        in_path = os.path.join(workspace, in_name)
        tpl_path = os.path.join(workspace, tpl_name)
        with open(in_path, "wb") as f:
            shutil.copyfileobj(input_file.file, f)
        with open(tpl_path, "wb") as f:
            shutil.copyfileobj(template_file.file, f)

        template_headers = _import_prep_template_headers(tpl_path, tpl_name)
        if not template_headers:
            return JSONResponse(status_code=400, content={
                "status": "error",
                "message": "The Compare To template has no header row (row 1 is empty)."})

        sheets = _import_prep_read_sheets(in_path, in_name)

        # Build outputs: {csv_filename: rows}
        outputs = []          # list of (filename, rows)
        report_lines = ["Import Prep report", f"Input file: {in_name}",
                        f"Template: {tpl_name}", f"Mode: remove special characters", ""]
        total_changed = 0
        total_data_rows = 0

        for sheet_name, df in sheets.items():
            if str(sheet_name).strip().lower() in IMPORT_PREP_SKIP_SHEETS:
                continue
            if df is None or df.empty:
                continue
            rows, changed = _import_prep_build_sheet(df, template_headers)
            data_rows = len(rows) - 1
            if data_rows <= 0:
                continue
            total_changed += changed
            total_data_rows += data_rows

            base = _import_prep_csv_name(sheet_name)[:-4]  # strip .csv
            chunks = _import_prep_chunk(rows, IMPORT_PREP_MAX_ROWS)
            if len(chunks) == 1:
                outputs.append((base + ".csv", chunks[0]))
            else:
                for i, ch in enumerate(chunks, start=1):
                    outputs.append((f"{base}_{i}.csv", ch))
            report_lines.append(
                f"{sheet_name}: {data_rows} data rows, {changed} cells cleaned"
                + (f", split into {len(chunks)} files" if len(chunks) > 1 else ""))

        if not outputs:
            return JSONResponse(status_code=400, content={
                "status": "error",
                "message": "No data rows were produced. Check that the input's headers "
                           "match the template and that a header row was detected."})

        report_lines += ["", f"TOTAL: {total_data_rows} data rows, "
                             f"{total_changed} cells cleaned across {len(outputs)} file(s)."]
        report_text = "\r\n".join(report_lines)

        common_headers = {
            "X-Files-Generated": str(len(outputs)),
            "X-Cells-Changed": str(total_changed),
            "X-Total-Rows": str(total_data_rows),
        }

        # Single CSV when possible; otherwise a ZIP with all files + report.
        if len(outputs) == 1:
            fname, rows = outputs[0]
            out_path = os.path.join(workspace, fname)
            with open(out_path, "wb") as f:
                f.write(_import_prep_csv_bytes(rows))
            with open(os.path.join(workspace, "import_prep_report.txt"), "w",
                      encoding="utf-8") as f:
                f.write(report_text)
            return FileResponse(out_path, media_type="text/csv",
                                filename=fname, headers=common_headers)

        zip_path = os.path.join(workspace, "import_prep_output.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for fname, rows in outputs:
                zf.writestr(fname, _import_prep_csv_bytes(rows))
            zf.writestr("import_prep_report.txt", report_text)
        return FileResponse(zip_path, media_type="application/zip",
                            filename="import_prep_output.zip", headers=common_headers)

    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})