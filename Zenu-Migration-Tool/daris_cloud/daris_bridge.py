"""DARVIS bridge - lets DARVIS (claude.ai) run the Zenu Migration Tool's engine on this PC.

A local MCP server (stdio, JSON-RPC 2.0, standard library only). The Claude desktop app starts it;
DARVIS calls it as the host server "daris_local". The client's export never leaves this PC:
the page sends a folder path, the rules (matrices) and the options; the job runs here with the
same MigrationEngine / ReconciliationEngine as the classic tool and writes to client_migrations/.

Register in the Claude desktop app's claude_desktop_config.json:
  "mcpServers": {"daris_local": {"command": "<python.exe>", "args": ["<this file>"]}}
"""
import csv
import heapq
import json
import os
import re
import shutil
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_DIR = os.path.join(PROJECT, "client_migrations")
HOME = os.path.expanduser("~")
VERSION = "1.10.0"
# The classic Job Tracker's database (job_tracker.py): read only, for client names of older jobs
TRACKER_DB = os.path.join(os.environ.get("TRACKER_DB_DIR", r"C:\Users\Daryl\OneDrive - Zenu Realestate Pty Ltd\Documents\sql"),
                          "db_job_tracker.db")
CRMS = {
    "Agentbox": "Agentbox", "Eagle": "Eagle Database", "VaultRE": "VaultRE", "Rex": "Rex",
    "ZenuTransfer": "Zenu Office Transfer", "General": "Generic Data Clean / Import",
    "MailingProspect": "Mailing Address to Prospect / Ownership",
}
# File names that point at a CRM's export (from the crm_*.py processors)
SIGNATURES = {
    "Agentbox": {"contact.csv", "contact_x_agent.csv", "note_x_contact.csv", "listing_x_contact.csv", "contact_category.csv"},
    "Eagle": {"agents.csv", "addresses.csv", "notes.csv", "appraisals.csv", "properties.csv"},
    "VaultRE": {"propertylifehistory.csv", "contactnotetypes.csv", "owners.csv", "landlords.csv", "feedbacknotes.csv", "staff.csv"},
    "Rex": {"feedback_contacts.csv", "note_properties.csv", "note_contacts.csv", "match_profiles.csv", "reminders.csv", "feedback.csv"},
}

# The protocol owns the real stdout; the engine's print() calls go to stderr (the app's log).
PROTO_OUT = sys.stdout.buffer
sys.stdout = sys.stderr
os.chdir(PROJECT)
sys.path.insert(0, PROJECT)
JOBS = {}          # job_id -> thread
LOCK = threading.Lock()


def log(msg):
    sys.stderr.write(f"[daris_bridge] {msg}\n")
    sys.stderr.flush()


# ---------------------------------------------------------------- paths
def allowed_roots():
    roots = [HOME, PROJECT]
    return [os.path.realpath(r) for r in roots if os.path.isdir(r)]


def safe_path(path, must_exist=True):
    if not path or not str(path).strip():
        raise ValueError("No folder given.")
    p = os.path.realpath(os.path.expandvars(os.path.expanduser(str(path).strip().strip('"'))))
    if not any(os.path.normcase(p) == os.path.normcase(r) or os.path.normcase(p).startswith(os.path.normcase(r) + os.sep)
               for r in allowed_roots()):
        raise ValueError("DARVIS can only read folders inside your user folder.")
    if must_exist and not os.path.exists(p):
        raise ValueError(f"Folder not found: {p}")
    return p


def job_dir(job_id):
    job_id = os.path.basename(str(job_id or "").strip())
    if not job_id.startswith("job_"):
        raise ValueError("Unknown job id.")
    d = os.path.join(UPLOAD_DIR, job_id)
    if not os.path.isdir(d):
        raise ValueError(f"Job {job_id} not found.")
    return job_id, d


def mb(n):
    return round(n / 1048576, 2)


def clean_client(name):
    """A client name as typed: printable, single spaces, at most 120 characters."""
    s = " ".join("".join(ch if ch.isprintable() else " " for ch in str(name or "")).split())
    return s[:120]


def tracker_clients():
    """job_id -> client name from the classic Job Tracker (read only; empty if it isn't there)."""
    if not os.path.exists(TRACKER_DB):
        return {}
    import sqlite3
    try:
        uri = "file:" + TRACKER_DB.replace("\\", "/") + "?mode=ro"
        from contextlib import closing
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as conn:
            rows = conn.execute("SELECT job_id, client_name FROM migrations WHERE job_id IS NOT NULL AND job_id <> '' "
                                "ORDER BY updated_at").fetchall()
        return {str(j).strip(): c for j, c in rows if c}
    except Exception as e:
        log(f"job tracker not readable: {e}")
        return {}


def client_of(meta, job_id, tracker):
    if meta.get("client"):
        return meta["client"], "job"
    if job_id in tracker:
        return tracker[job_id], "job tracker"
    return "", ""


def csv_files(folder):
    """Every .csv under the folder, flattened by name (as the classic folder upload does)."""
    out = {}
    for root, _dirs, files in os.walk(folder):
        if os.path.basename(root).startswith("_baseline"):
            continue
        for f in files:
            if f.lower().endswith(".csv"):
                out[f] = os.path.join(root, f)
    return out


def guess_crm(names):
    low = {n.lower() for n in names}
    scores = {crm: len(sig & low) for crm, sig in SIGNATURES.items()}
    best = max(scores, key=scores.get)
    return (best if scores[best] >= 2 else ""), scores


# ---------------------------------------------------------------- tools
def t_status(_a):
    try:
        import etl_engine  # noqa: F401
        engine = "ready"
    except Exception as e:                       # pragma: no cover - reported to the page
        engine = f"can't load the engine: {e}"
    return {"bridge": "DARVIS bridge", "version": VERSION, "project": PROJECT, "jobs_folder": UPLOAD_DIR,
            "engine": engine, "python": sys.version.split()[0], "crms": CRMS, "readable_roots": allowed_roots()}


def t_list_folder(a):
    path = a.get("path")
    if not path:
        places = []
        for label, p in (("Desktop", os.path.join(HOME, "Desktop")), ("Downloads", os.path.join(HOME, "Downloads")),
                         ("Documents", os.path.join(HOME, "Documents")), ("Migration jobs", UPLOAD_DIR)):
            if os.path.isdir(p):
                places.append({"label": label, "path": p})
        for name in sorted(os.listdir(HOME)):
            p = os.path.join(HOME, name)
            if name.lower().startswith("onedrive") and os.path.isdir(p):
                places.append({"label": name, "path": p})
        return {"path": "", "places": places}
    p = safe_path(path)
    if not os.path.isdir(p):
        raise ValueError("That's a file, not a folder.")
    folders, files, other = [], [], 0
    try:
        entries = sorted(os.scandir(p), key=lambda e: e.name.lower())
    except PermissionError:
        raise ValueError("Windows won't let DARVIS open that folder.")
    for e in entries:
        if e.name.startswith((".", "$")):
            continue
        try:
            if e.is_dir():
                folders.append({"name": e.name, "path": e.path})
            elif e.name.lower().endswith((".csv", ".json")):
                files.append({"name": e.name, "size_mb": mb(e.stat().st_size)})
            else:
                other += 1
        except OSError:
            continue
    parent = os.path.dirname(p)
    return {"path": p, "parent": parent if parent != p and safe_ok(parent) else "", "folders": folders[:400],
            "files": files[:400], "other_files": other}


def safe_ok(p):
    try:
        safe_path(p)
        return True
    except ValueError:
        return False


def t_inspect_export(a):
    p = safe_path(a.get("path"))
    files = csv_files(p)
    if not files:
        raise ValueError("No CSV files in that folder.")
    info = []
    for name in sorted(files, key=str.lower):
        fp = files[name]
        cols = []
        try:
            with open(fp, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
                cols = next(csv.reader(f), [])[:80]
        except Exception:
            pass
        info.append({"file": name, "size_mb": mb(os.path.getsize(fp)), "columns": len(cols), "header": cols})
    crm, scores = guess_crm(files)
    has_clean = "contact_cleaned.csv" in {n.lower() for n in files}
    return {"path": p, "csv_files": len(info), "total_mb": round(sum(x["size_mb"] for x in info), 1),
            "likely_crm": crm, "signature_matches": scores, "has_contact_cleaned": has_clean, "files": info}


def run_job(job_id, workspace, export_folder, baseline_folder, crm, chunk_size, top_up):
    def progress(pct, stage, detail=""):
        with open(os.path.join(workspace, "progress.json"), "w", encoding="utf-8") as f:
            json.dump({"percent": pct, "stage": stage, "detail": detail, "elapsed_seconds": 0, "done": False}, f)
    try:
        progress(0.5, "Copying export files", export_folder)
        files = csv_files(export_folder)
        for i, (name, src) in enumerate(sorted(files.items())):
            progress(0.5 + 1.0 * i / max(1, len(files)), "Copying export files", f"{name} ({i + 1}/{len(files)})")
            shutil.copy2(src, os.path.join(workspace, name))
        if top_up and baseline_folder:
            bdir = os.path.join(workspace, "_baseline")
            os.makedirs(bdir, exist_ok=True)
            for name, src in csv_files(baseline_folder).items():
                shutil.copy2(src, os.path.join(bdir, name))
        from etl_engine import MigrationEngine
        engine = MigrationEngine(job_id=job_id, workspace=workspace, source_crm=crm, chunk_size=chunk_size, top_up=top_up)
        try:
            engine.log(f"[{job_id}] Starting {'TOP-UP' if top_up else 'full'} migration process for {crm}... (DARVIS)")
            engine.load_csvs_to_sqlite()
            engine.run_mapping(os.path.join(workspace, "mapping_rules.json"))
        except Exception as e:
            engine.log(f"[{job_id}] CRITICAL SYSTEM FAILURE: {e}")
        finally:
            engine.close()
    except Exception as e:
        with open(os.path.join(workspace, "process.log"), "a", encoding="utf-8") as f:
            f.write(f"[{job_id}] CRITICAL SYSTEM FAILURE (bridge): {e}\n")
        log(traceback.format_exc())
    finally:
        meta_path = os.path.join(workspace, "job_meta.json")
        try:
            meta = json.load(open(meta_path, encoding="utf-8"))
            meta["finished"] = datetime.now().isoformat(timespec="seconds")
            json.dump(meta, open(meta_path, "w", encoding="utf-8"), indent=1)
        except Exception:
            pass


def t_start_migration(a):
    crm = str(a.get("crm", "")).strip()
    client = clean_client(a.get("client"))
    if not client:
        raise ValueError("Give the client's name, so the job can be found later.")
    if crm not in CRMS:
        raise ValueError(f"Unknown CRM '{crm}'. Use one of: {', '.join(CRMS)}.")
    export_folder = safe_path(a.get("export_folder"))
    if not csv_files(export_folder):
        raise ValueError("No CSV files in the export folder.")
    matrices = a.get("matrices")
    if isinstance(matrices, str):
        matrices = json.loads(matrices)
    if not isinstance(matrices, dict) or not matrices or not all(isinstance(v, list) for v in matrices.values()):
        raise ValueError("The rules must be matrices: {\"Group name\": [rules...]} with at least one group.")
    top_up = bool(a.get("top_up"))
    baseline = safe_path(a.get("baseline_folder")) if top_up else ""
    if top_up and not csv_files(baseline):
        raise ValueError("A top-up needs the previous export's folder (with its CSV files).")
    try:
        chunk = int(a.get("chunk_size", 500000) or 0)
    except (TypeError, ValueError):
        raise ValueError("Split rows must be a whole number (0 = no split).")
    job_id = f"job_{uuid.uuid4().hex[:8]}"
    workspace = os.path.join(UPLOAD_DIR, job_id)
    os.makedirs(workspace, exist_ok=True)
    with open(os.path.join(workspace, "mapping_rules.json"), "w", encoding="utf-8") as f:
        json.dump(matrices, f)
    inputs = sorted(csv_files(export_folder))
    meta = {"job_id": job_id, "client": client, "crm": crm, "export_folder": export_folder, "baseline_folder": baseline, "top_up": top_up,
            "chunk_size": chunk, "groups": list(matrices), "inputs": inputs, "started": datetime.now().isoformat(timespec="seconds"),
            "source": "DARIS Cloud"}
    with open(os.path.join(workspace, "job_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    th = threading.Thread(target=run_job, args=(job_id, workspace, export_folder, baseline, crm, chunk, top_up), daemon=True)
    with LOCK:
        JOBS[job_id] = th
    th.start()
    return {"job_id": job_id, "client": client, "workspace": workspace, "crm": crm, "groups": list(matrices),
            "csv_files": len(inputs), "top_up": top_up, "message": f"{crm} migration for {client} started on this PC."}


def read_progress(d):
    try:
        with open(os.path.join(d, "progress.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"percent": 0, "stage": "Queued", "detail": "", "done": False}


def read_meta(d):
    try:
        with open(os.path.join(d, "job_meta.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def log_lines(d):
    try:
        with open(os.path.join(d, "process.log"), encoding="utf-8", errors="replace") as f:
            return [l.rstrip("\n") for l in f]
    except FileNotFoundError:
        return []


def outputs(d, meta):
    inputs = set(meta.get("inputs") or [])
    skip = {"mapping_rules.json", "job_meta.json", "progress.json", "process.log", "reconciliation_summary.json", CHANGES_FILE, CONSOL_FILE}
    out = []
    for e in sorted(os.scandir(d), key=lambda e: e.name.lower()):
        if e.is_file() and e.name not in inputs and e.name not in skip and not e.name.endswith("_raw_data.db"):
            out.append({"file": e.name, "size_mb": mb(e.stat().st_size)})
    return out


def job_state(job_id, d):
    meta, prog, lines = read_meta(d), read_progress(d), log_lines(d)
    running = job_id in JOBS and JOBS[job_id].is_alive()
    failed = any("CRITICAL SYSTEM FAILURE" in l for l in lines)
    done = bool(prog.get("done")) or (not running and bool(meta.get("finished")))
    state = "running" if running else "failed" if failed else "done" if done else "stopped"
    return meta, prog, lines, state


def t_job_status(a):
    job_id, d = job_dir(a.get("job_id"))
    meta, prog, lines, state = job_state(job_id, d)
    client, client_from = client_of(meta, job_id, tracker_clients() if not meta.get("client") else {})
    return {"job_id": job_id, "client": client, "client_from": client_from, "state": state, "crm": meta.get("crm", ""),
            "progress": prog, "started": meta.get("started", ""), "export_folder": meta.get("export_folder", ""),
            "log_tail": lines[-int(min(max(a.get("tail", 25), 1), 200)):],
            "outputs": outputs(d, meta) if state != "running" else [],
            "reconciliation": recon_state(d), "output_changes": changes_summary(d)}


def recon_state(d):
    p = os.path.join(d, "reconciliation_summary.json")
    if not os.path.exists(p):
        return "not run"
    try:
        s = json.load(open(p, encoding="utf-8"))
        return s.get("status") or s.get("overall") or "ready"
    except Exception:
        return "running"


def t_list_jobs(a):
    jobs = []
    want = " ".join(str(a.get("client") or "").lower().split())
    tracker = tracker_clients()
    if os.path.isdir(UPLOAD_DIR):
        for name in os.listdir(UPLOAD_DIR):
            d = os.path.join(UPLOAD_DIR, name)
            if not (name.startswith("job_") and os.path.isdir(d)):
                continue
            meta, prog, lines, state = job_state(name, d)
            client, client_from = client_of(meta, name, tracker)
            crm = meta.get("crm") or next((l.split(" for ")[-1].split("...")[0] for l in lines[:3] if " migration process for " in l), "")
            if want and want not in f"{client} {name} {crm}".lower():
                continue
            jobs.append({"job_id": name, "client": client, "client_from": client_from, "crm": crm, "state": state,
                         "percent": prog.get("percent", 0),
                         "modified": datetime.fromtimestamp(os.path.getmtime(d)).strftime("%d/%m/%Y %H:%M"),
                         "mtime": os.path.getmtime(d), "from": meta.get("source", "classic tool"),
                         "reconciliation": recon_state(d)})
    jobs.sort(key=lambda j: j["mtime"], reverse=True)
    for j in jobs:
        j.pop("mtime")
    out = {"jobs": jobs[: int(a.get("limit", 30) or 30)], "matching": len(jobs)}
    if want:
        out["search"] = want
    return out


def t_set_client_name(a):
    job_id, d = job_dir(a.get("job_id"))
    client = clean_client(a.get("client"))
    if not client:
        raise ValueError("Give the client's name.")
    meta = read_meta(d)
    old = meta.get("client", "")
    if not meta:                                   # a classic-tool job: start its job_meta.json
        meta = {"job_id": job_id, "source": "classic tool"}
    meta["client"] = client
    meta["client_set"] = datetime.now().isoformat(timespec="seconds")
    with open(os.path.join(d, "job_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    return {"job_id": job_id, "client": client, "previous": old, "message": f"{job_id} is now under {client}."}


def t_job_log(a):
    job_id, d = job_dir(a.get("job_id"))
    lines = log_lines(d)
    want = str(a.get("contains") or "").lower()
    if want:
        lines = [l for l in lines if want in l.lower()]
    tail = int(min(max(a.get("tail", 200), 1), 1000))
    return {"job_id": job_id, "total_lines": len(lines), "lines": lines[-tail:]}


def t_start_reconciliation(a):
    job_id, d = job_dir(a.get("job_id"))
    _meta, _prog, _lines, state = job_state(job_id, d)
    if state == "running":
        raise ValueError("The migration is still running. Reconcile when it's finished.")
    with open(os.path.join(d, "reconciliation_summary.json"), "w", encoding="utf-8") as f:
        json.dump({"status": "running", "job_id": job_id}, f)

    def go():
        try:
            from reconciliation import ReconciliationEngine
            ReconciliationEngine(job_id=job_id, workspace=d).run()
        except Exception as e:
            with open(os.path.join(d, "reconciliation_summary.json"), "w", encoding="utf-8") as f:
                json.dump({"status": "error", "job_id": job_id, "message": str(e)}, f)
            log(traceback.format_exc())
    threading.Thread(target=go, daemon=True).start()
    return {"job_id": job_id, "message": "Reconciliation started."}


def t_reconciliation_report(a):
    job_id, d = job_dir(a.get("job_id"))
    p = os.path.join(d, "reconciliation_summary.json")
    if not os.path.exists(p):
        return {"job_id": job_id, "status": "not run"}
    with open(p, encoding="utf-8") as f:
        s = json.load(f)
    text = json.dumps(s)
    if len(text) > 60000:                          # keep the answer small for the page and the chat
        s = {"job_id": job_id, "status": s.get("status"), "note": "Report trimmed - open the Excel report for detail.",
             "summary": {k: v for k, v in s.items() if not isinstance(v, (list, dict))}}
    s["excel_report"] = os.path.exists(os.path.join(d, "Reconciliation_Report.xlsx"))
    return s


# ---------------------------------------------------------------- change values in the output files
CHANGES_FILE = "output_changes.json"
CHANGED_DIR = "With changes"
CHANGES_LOG = "DARIS_Changes.xlsx"
CHANGE_ACTIONS = ("set", "fill", "replace", "clear", "combine")
WHERE_OPS = ("is", "contains", "blank", "filled")
ID_HINTS = ("identifier", "_id")
CHANGE_THREADS = {}


def output_xlsx(d):
    """The job's Zenu output workbooks (not the reconciliation report)."""
    return sorted(e.name for e in os.scandir(d) if e.is_file() and e.name.lower().endswith(".xlsx")
                  and not e.name.lower().startswith(("reconciliation", "daris_", "~$")))


def cell_text(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def read_header(path):
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True)
    try:
        ws = wb.active
        first = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
        rows = (ws.max_row - 1) if ws.max_row else None
        return ws.title, [cell_text(v) for v in first], rows
    finally:
        wb.close()


def t_output_columns(a):
    job_id, d = job_dir(a.get("job_id"))
    if job_state(job_id, d)[3] == "running":
        raise ValueError("The migration is still running.")
    files = []
    for name in output_xlsx(d):
        try:
            sheet, cols, rows = read_header(os.path.join(d, name))
            files.append({"file": name, "sheet": sheet, "rows": rows, "columns": [c for c in cols if c][:400]})
        except Exception as e:
            files.append({"file": name, "error": f"can't read: {e}"})
    if not files:
        raise ValueError("This job has no output workbooks yet.")
    return {"job_id": job_id, "files": files}


def read_json_safely(path, default):
    """Read a status file; on Windows it can be mid-swap for a moment, so retry before giving up."""
    for _ in range(40):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return dict(default)
        except (PermissionError, ValueError, OSError):
            time.sleep(0.05)
    return dict(default)


def write_json_safely(path, data):
    """Write a status file atomically; Windows refuses the swap while a reader has it open, so retry."""
    tmp = f"{path}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    for _ in range(100):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05)
    try:
        os.remove(tmp)
    finally:
        raise PermissionError(f"Couldn't update {os.path.basename(path)} - is it open in another program?")


def read_changes(d):
    return read_json_safely(os.path.join(d, CHANGES_FILE), {"rules": [], "state": "none"})


def write_changes(d, data):
    write_json_safely(os.path.join(d, CHANGES_FILE), data)


def changes_summary(d):
    c = read_changes(d)
    if not c.get("rules") and c.get("state") in (None, "none"):
        return None
    return {"state": c.get("state"), "rules": len(c.get("rules") or []), "applied": c.get("finished", ""), "folder": c.get("folder", "")}


def clean_rule(r, columns_by_file):
    if not isinstance(r, dict):
        raise ValueError("Each change must be an object.")
    action = str(r.get("action") or "set").strip()
    if action not in CHANGE_ACTIONS:
        raise ValueError("A change must be set, fill, replace, clear or combine.")
    file = str(r.get("file") or "all").strip()
    if file != "all" and file not in columns_by_file:
        raise ValueError(f"There's no output file called {file} in this job.")
    targets = list(columns_by_file) if file == "all" else [file]
    col_in = str(r.get("column") or "").strip()
    found = {c.lower(): c for f in targets for c in columns_by_file[f]}
    column = found.get(col_in.lower())
    if not column:
        raise ValueError(f"No output file{'' if file == 'all' else ' ' + file} has a column called '{col_in}'.")
    value = " ".join(str(r.get("value") or "").split())[:500]
    find = str(r.get("find") or "").strip()[:500]
    if action in ("set", "fill") and not value:
        raise ValueError("Type the new value (to empty a column, use clear).")
    if action == "replace" and not find:
        raise ValueError("Type the value to replace.")
    low = column.lower()
    if action != "replace" and (low.endswith(ID_HINTS) or "identifier" in low):
        raise ValueError(f"{column} links records together, so it can only have single values replaced, not set or cleared.")
    out = {"file": file, "column": column, "action": action, "value": "" if action in ("clear", "combine") else value,
           "find": find if action == "replace" else ""}
    if action == "combine":
        raw = r.get("sources") or []
        if isinstance(raw, str):
            raw = [x for x in re.split(r"\s*[,+]\s*", raw) if x.strip()]
        sources = []
        for name in raw[:9]:
            c = found.get(str(name).strip().lower())
            if not c:
                raise ValueError(f"No output file{'' if file == 'all' else ' ' + file} has a column called '{str(name).strip()}' to combine.")
            sources.append(c)
        if len(sources) < 2 and not (len(sources) == 1 and sources[0] != column):
            raise ValueError("Pick at least two columns to combine (or one other column).")
        if len(sources) > 8:
            raise ValueError("Combine at most 8 columns.")
        sep = str(r.get("separator") if r.get("separator") is not None else " ").replace("\\n", "\n")[:20]
        out.update({"sources": sources, "separator": sep, "labels": bool(r.get("labels"))})
    wc = str(r.get("where_column") or "").strip()
    if wc:
        wcol = {c.lower(): c for f in targets for c in columns_by_file[f]}.get(wc.lower())
        if not wcol:
            raise ValueError(f"No output file has a column called '{wc}' for the condition.")
        op = str(r.get("where_op") or "is").strip()
        if op not in WHERE_OPS:
            raise ValueError("The condition must be is, contains, blank or filled.")
        wv = str(r.get("where_value") or "").strip()[:500]
        if op in ("is", "contains") and not wv:
            raise ValueError("Type the value for the condition.")
        out.update({"where_column": wcol, "where_op": op, "where_value": wv})
    return out


def rule_text(r):
    what = {"set": f"set every cell to '{r['value']}'", "fill": f"fill blank cells with '{r['value']}'",
            "replace": f"replace '{r['find']}' with " + (f"'{r['value']}'" if r["value"] else "blank"), "clear": "clear it",
            "combine": "combine " + " + ".join(r.get("sources") or []) + f" (separated by {json.dumps(r.get('separator', ' '))}"
                       + (", with column names" if r.get("labels") else "") + ")"}[r["action"]]
    cond = ""
    if r.get("where_column"):
        op = {"is": "is", "contains": "contains", "blank": "is blank", "filled": "is not blank"}[r["where_op"]]
        cond = f", where {r['where_column']} {op}"
        if r["where_op"] in ("is", "contains"):
            cond += f" '{r['where_value']}'"
    return f"{r['column']} ({'all files' if r['file'] == 'all' else r['file']}): {what}{cond}"


def combine_value(rule, values, sidx):
    """The combined text of a row's source cells (blanks skipped)."""
    parts = []
    for k, name in sidx:
        v = cell_text(values[k]) if k < len(values) else ""
        if v:
            parts.append(f"{name}: {v}" if rule.get("labels") else v)
    return rule.get("separator", " ").join(parts) or None


def change_one(rule, value, where_value, values=None, sidx=None):
    """New value for one cell, or the same object when unchanged."""
    if rule.get("where_column"):
        w, want = cell_text(where_value).lower(), rule.get("where_value", "").lower()
        op = rule["where_op"]
        ok = w == want if op == "is" else want in w if op == "contains" else not w if op == "blank" else bool(w)
        if not ok:
            return value
    cur = cell_text(value)
    if rule["action"] == "combine":
        new = combine_value(rule, values or [], sidx or [])
        return value if cell_text(new) == cur else new
    if rule["action"] == "set":
        return value if cur == rule["value"] else rule["value"]
    if rule["action"] == "fill":
        return rule["value"] if not cur else value
    if rule["action"] == "replace":
        return (rule["value"] or None) if cur.lower() == rule["find"].lower() else value
    return None if value not in (None, "") else value


def rewrite_workbook(src, dst, plan, counts):
    """Stream src -> dst, changing only the planned columns; other cells keep their values and number formats."""
    from copy import copy
    from openpyxl import Workbook, load_workbook
    from openpyxl.cell import WriteOnlyCell
    wb = load_workbook(src, read_only=True)
    try:
        ws = wb.active
        out = Workbook(write_only=True)
        ows = out.create_sheet(ws.title)
        rows = ws.iter_rows()
        header = next(rows, ())
        hcells = []
        for c in header:
            wc = WriteOnlyCell(ows, c.value)
            for attr in ("font", "border", "alignment", "fill"):
                try:
                    setattr(wc, attr, copy(getattr(c, attr)))
                except Exception:
                    pass
            hcells.append(wc)
        ows.append(hcells)
        width = len(header)
        for row in rows:
            cells = list(row) + [None] * (width - len(row))
            values = [c.value if c is not None else None for c in cells]
            for i, ci, wi, sidx in plan:
                old = values[ci]
                new = change_one(counts["rules"][i], old, values[wi] if wi is not None else None, values, sidx)
                if new is not old and cell_text(new) != cell_text(old):
                    values[ci] = new
                    counts["cells"][i] += 1
                    ex = counts["examples"][i]
                    if len(ex) < 5:
                        ex.append({"file": os.path.basename(dst), "row": cell_text(values[0])[:60] if values else "",
                                   "before": cell_text(old)[:200], "after": cell_text(new)[:200]})
            line = []
            for k, v in enumerate(values):
                if isinstance(v, str) and not v.strip():
                    v = None                          # blank = no cell (Excel treats "" as filled)
                c = cells[k]
                fmt = getattr(c, "number_format", "General") if c is not None else "General"
                if fmt and fmt != "General" and v is not None:
                    wc = WriteOnlyCell(ows, v)
                    wc.number_format = fmt
                    line.append(wc)
                else:
                    line.append(v)
            ows.append(line)
        tmp = dst + ".tmp.xlsx"
        out.save(tmp)
        os.replace(tmp, dst)
    finally:
        wb.close()


def run_changes(job_id, d, rules):
    data = read_changes(d)
    folder = os.path.join(d, CHANGED_DIR)
    try:
        os.makedirs(folder, exist_ok=True)
        files = output_xlsx(d)
        counts = {"rules": rules, "cells": [0] * len(rules), "examples": [[] for _ in rules]}
        per_file = {}
        for n, name in enumerate(files):
            data.update({"state": "running", "progress": f"{name} ({n + 1}/{len(files)})"})
            write_changes(d, data)
            src, dst = os.path.join(d, name), os.path.join(folder, name)
            _sheet, header, _rows = read_header(src)
            idx = {}
            for k, h in enumerate(header):
                idx.setdefault(h.lower(), k)
            plan = []
            for i, r in enumerate(rules):
                if r["file"] not in ("all", name):
                    continue
                ci = idx.get(r["column"].lower())
                wi = idx.get(r["where_column"].lower()) if r.get("where_column") else None
                if ci is None or (r.get("where_column") and wi is None):
                    continue
                sidx = None
                if r["action"] == "combine":
                    sidx = [(idx.get(c.lower()), c) for c in r["sources"]]
                    if any(k is None for k, _c in sidx):
                        continue                         # this file doesn't have every column to combine
                plan.append((i, ci, wi, sidx))
            before = sum(counts["cells"])
            if plan:
                rewrite_workbook(src, dst, plan, counts)
            else:
                shutil.copy2(src, dst)
            per_file[name] = sum(counts["cells"]) - before
        # the log of what changed, next to the files
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Changes"
        ws.append(["#", "Change", "Cells changed"])
        for i, r in enumerate(rules):
            ws.append([i + 1, rule_text(r), counts["cells"][i]])
        ws2 = wb.create_sheet("Files")
        ws2.append(["File", "Cells changed"])
        for name, c in per_file.items():
            ws2.append([name, c])
        wb.save(os.path.join(folder, CHANGES_LOG))
        data.update({"state": "done", "progress": "", "folder": folder, "files": list(per_file) + [CHANGES_LOG],
                     "results": [{"change": rule_text(r), "cells_changed": counts["cells"][i], "examples": counts["examples"][i]}
                                 for i, r in enumerate(rules)],
                     "per_file": per_file, "finished": datetime.now().isoformat(timespec="seconds"), "error": ""})
    except Exception as e:
        log(traceback.format_exc())
        data.update({"state": "failed", "progress": "", "error": str(e)})
    write_changes(d, data)


def remove_changed_copies(d, data):
    """Undo all: delete only the files DARVIS wrote into 'With changes' (then the folder if it's empty)."""
    folder = os.path.join(d, CHANGED_DIR)
    if not os.path.isdir(folder):
        return 0
    n = 0
    for name in data.get("files") or []:
        p = os.path.join(folder, os.path.basename(name))
        if os.path.isfile(p):
            os.remove(p)
            n += 1
    if not os.listdir(folder):
        os.rmdir(folder)
    return n


def t_apply_output_changes(a):
    job_id, d = job_dir(a.get("job_id"))
    if job_state(job_id, d)[3] == "running":
        raise ValueError("The migration is still running. Change values when it's finished.")
    th = CHANGE_THREADS.get(job_id)
    if th and th.is_alive():
        raise ValueError("DARVIS is still writing the changed files for this job. Wait a moment.")
    rules_in = a.get("rules")
    if isinstance(rules_in, str):
        rules_in = json.loads(rules_in or "[]")
    if not isinstance(rules_in, list) or len(rules_in) > 50:
        raise ValueError("rules must be a list of at most 50 changes.")
    data = read_changes(d)
    if not rules_in:
        removed = remove_changed_copies(d, data)
        write_changes(d, {"rules": [], "state": "none"})
        return {"job_id": job_id, "rules": [], "removed_files": removed,
                "message": "All changes undone. The original output files were never changed."}
    columns_by_file = {}
    for name in output_xlsx(d):
        columns_by_file[name] = [c for c in read_header(os.path.join(d, name))[1] if c]
    if not columns_by_file:
        raise ValueError("This job has no output workbooks yet.")
    rules = [clean_rule(r, columns_by_file) for r in rules_in]
    write_changes(d, {**data, "rules": rules, "state": "running", "progress": "Starting", "error": ""})
    th = threading.Thread(target=run_changes, args=(job_id, d, rules), daemon=True)
    CHANGE_THREADS[job_id] = th
    th.start()
    return {"job_id": job_id, "rules": [rule_text(r) for r in rules], "folder": os.path.join(d, CHANGED_DIR),
            "message": "Writing changed copies of the output files into the job's 'With changes' folder. The originals stay as they are."}


def t_output_changes_status(a):
    job_id, d = job_dir(a.get("job_id"))
    th = CHANGE_THREADS.get(job_id)
    alive = bool(th and th.is_alive())              # check the worker BEFORE reading: it may finish in between
    data = read_changes(d)
    if data.get("state") == "running" and not alive:
        data = read_changes(d)
        if data.get("state") == "running":
            data["state"] = "stopped"               # the bridge restarted mid-way: apply again
    data["rule_texts"] = [rule_text(r) for r in data.get("rules") or []]
    data["job_id"] = job_id
    return data


# ---------------------------------------------------------------- combine output files into one workbook
CONSOL_DIR = "Consolidated"
CONSOL_FILE = "consolidation.json"
CONSOL_THREADS = {}


def tab_name(file, used):
    """'Zenu_Property_Notes(notelisting)_Final.xlsx' -> 'Property_Notes(notelisting)' (Excel: 31 chars, no []:*?/\\)."""
    base = os.path.splitext(os.path.basename(file))[0]
    base = re.sub(r"^Zenu_", "", base)
    base = re.sub(r"_Final(?=(_pt\d+|_part\d+)?$)", "", base)
    base = re.sub(r"[\[\]:*?/\\]", "_", base).strip("' ") or "Sheet"
    name, k = base[:31], 2
    while name.lower() in used or name.lower() == "contents":
        suffix = f" ({k})"
        name, k = base[:31 - len(suffix)] + suffix, k + 1
    used.add(name.lower())
    return name


def copy_sheet(src, out, title):
    """Stream the first sheet of src into a new sheet of the write-only workbook out (values, number formats,
    header style). -> data rows copied."""
    from copy import copy
    from openpyxl import load_workbook
    from openpyxl.cell import WriteOnlyCell
    wb = load_workbook(src, read_only=True)
    n = 0
    try:
        ws = wb.active
        ows = out.create_sheet(title)
        rows = ws.iter_rows()
        header = next(rows, ())
        hcells = []
        for c in header:
            wc = WriteOnlyCell(ows, c.value)
            for attr in ("font", "border", "alignment", "fill"):
                try:
                    setattr(wc, attr, copy(getattr(c, attr)))
                except Exception:
                    pass
            hcells.append(wc)
        ows.append(hcells)
        ows.freeze_panes = "A2"
        for row in rows:
            line = []
            for c in row:
                v = c.value if c is not None else None
                if isinstance(v, str) and not v.strip():
                    v = None                          # blank = no cell (Excel treats "" as filled)
                fmt = getattr(c, "number_format", "General") if c is not None else "General"
                if v is not None and fmt and fmt != "General":
                    wc = WriteOnlyCell(ows, v)
                    wc.number_format = fmt
                    line.append(wc)
                else:
                    line.append(v)
            ows.append(line)
            n += 1
    finally:
        wb.close()
    return n


def consol_sources(d, source):
    folder = os.path.join(d, CHANGED_DIR) if source == "changed" else d
    if source == "changed" and not os.path.isdir(folder):
        raise ValueError("This job has no 'With changes' copies. Write changed files first, or use the original files.")
    return folder, output_xlsx(folder)


def read_consol(d):
    return read_json_safely(os.path.join(d, CONSOL_FILE), {"state": "none"})


def write_consol(d, data):
    write_json_safely(os.path.join(d, CONSOL_FILE), data)


def run_consolidate(job_id, d, folder, files, out_path, source):
    from openpyxl import Workbook
    data = {"state": "running", "progress": "Starting", "files": files, "source": source, "path": out_path, "error": ""}
    write_consol(d, data)
    try:
        out = Workbook(write_only=True)
        used, plan = set(), []
        for f in files:
            _sheet, header, rows = read_header(os.path.join(folder, f))
            plan.append({"tab": tab_name(f, used), "file": f, "rows": rows, "columns": len([h for h in header if h])})
        contents = out.create_sheet("Contents")
        contents.append(["Tab", "Source file", "Rows", "Columns"])
        for p in plan:
            contents.append([p["tab"], p["file"], p["rows"], p["columns"]])
        contents.append([])
        contents.append(["Job", job_id])
        contents.append(["Source", "With changes copies" if source == "changed" else "Original output files"])
        contents.append(["Created", datetime.now().strftime("%d/%m/%Y %H:%M")])
        for k, p in enumerate(plan):
            data["progress"] = f"{p['file']} ({k + 1}/{len(plan)})"
            write_consol(d, data)
            p["rows"] = copy_sheet(os.path.join(folder, p["file"]), out, p["tab"])
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        tmp = out_path + ".tmp.xlsx"
        out.save(tmp)
        os.replace(tmp, out_path)
        data.update({"state": "done", "progress": "", "tabs": plan, "size_mb": mb(os.path.getsize(out_path)),
                     "finished": datetime.now().isoformat(timespec="seconds")})
    except Exception as e:
        log(traceback.format_exc())
        data.update({"state": "failed", "progress": "", "error": str(e)})
    write_consol(d, data)


def t_consolidate_outputs(a):
    job_id, d = job_dir(a.get("job_id"))
    meta, _prog, _lines, state = job_state(job_id, d)
    if state == "running":
        raise ValueError("The migration is still running. Combine the files when it's finished.")
    th = CONSOL_THREADS.get(job_id)
    if th and th.is_alive():
        raise ValueError("DARVIS is still combining files for this job. Wait a moment.")
    source = "changed" if a.get("source") == "changed" else "original"
    folder, available = consol_sources(d, source)
    want = a.get("files") or []
    if isinstance(want, str):
        want = [x.strip() for x in want.split(",") if x.strip()]
    files = available if not want else [f for f in available if f in set(want)]
    missing = [f for f in want if f not in available]
    if missing:
        raise ValueError(f"Not output files of this job{' (With changes)' if source == 'changed' else ''}: {', '.join(missing[:5])}")
    if not files:
        raise ValueError("Pick at least one output file.")
    who = re.sub(r"[^0-9A-Za-z]+", "_", meta.get("client") or job_id).strip("_")[:40] or job_id
    name = os.path.basename(str(a.get("name") or "").strip())
    name = re.sub(r'[<>:"/\\|?*]+', "_", name).strip(" .")
    if not name:
        name = f"{who}_Consolidated{'_with_changes' if source == 'changed' else ''}_{datetime.now():%Y%m%d_%H%M}"
    if not name.lower().endswith(".xlsx"):
        name += ".xlsx"
    out_path = os.path.join(d, CONSOL_DIR, name)
    write_consol(d, {"state": "running", "progress": "Starting", "files": files, "source": source, "path": out_path, "error": ""})
    th = threading.Thread(target=run_consolidate, args=(job_id, d, folder, files, out_path, source), daemon=True)
    CONSOL_THREADS[job_id] = th
    th.start()
    return {"job_id": job_id, "files": files, "source": source, "path": out_path,
            "message": f"Combining {len(files)} file(s) into {name} (one tab per file) in the job's {CONSOL_DIR} folder."}


def t_consolidation_status(a):
    job_id, d = job_dir(a.get("job_id"))
    th = CONSOL_THREADS.get(job_id)
    alive = bool(th and th.is_alive())              # check the worker BEFORE reading: it may finish in between
    data = read_consol(d)
    if data.get("state") == "running" and not alive:
        data = read_consol(d)
        if data.get("state") == "running":
            data["state"] = "stopped"               # the bridge restarted mid-way: combine again
    folder = os.path.join(d, CONSOL_DIR)
    data["existing"] = sorted(({"file": e.name, "size_mb": mb(e.stat().st_size),
                                "modified": datetime.fromtimestamp(e.stat().st_mtime).strftime("%d/%m/%Y %H:%M")}
                               for e in os.scandir(folder) if e.is_file() and e.name.lower().endswith(".xlsx")),
                              key=lambda x: x["file"]) if os.path.isdir(folder) else []
    changed = os.path.join(d, CHANGED_DIR)
    data["changed_available"] = os.path.isdir(changed) and bool(output_xlsx(changed))
    data["job_id"] = job_id
    return data


# ---------------------------------------------------------------- Contact cleaning history (runs saved on this PC)
HIST_DIR = os.path.join(UPLOAD_DIR, "_contact_cleaning")
HIST_CHUNK = 700_000                                  # raw bytes per chunk (about 0.9 MB as base64)
HIST_META = "run.json"


def hist_dir(run_id, must_exist=True):
    run_id = os.path.basename(str(run_id or "").strip())
    if not re.fullmatch(r"cc_\d{8}_\d{6}_[0-9a-f]{4}", run_id):
        raise ValueError("Unknown saved run.")
    d = os.path.join(HIST_DIR, run_id)
    if must_exist and not os.path.isdir(d):
        raise ValueError(f"Saved run {run_id} not found.")
    return run_id, d


def hist_file_name(name):
    name = os.path.basename(str(name or "")).strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" .")
    if not name or name.lower() == HIST_META:
        raise ValueError("Bad file name.")
    return name[:150]


def t_history_save(a):
    """Create (no run_id) or update a saved Contact cleaning run's details (mapping, settings, summary)."""
    meta = a.get("meta") or {}
    if not isinstance(meta, dict):
        raise ValueError("meta must be an object.")
    if len(json.dumps(meta)) > 400_000:
        raise ValueError("The run's settings are too big to save.")
    if a.get("run_id"):
        run_id, d = hist_dir(a.get("run_id"))
        old = read_json_safely(os.path.join(d, HIST_META), {})
    else:
        run_id = f"cc_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:4]}"
        d = os.path.join(HIST_DIR, run_id)
        os.makedirs(d, exist_ok=True)
        old = {"created": datetime.now().isoformat(timespec="seconds"), "files": []}
    keep = {k: old[k] for k in ("created", "files") if k in old}
    data = {**meta, **keep, "run_id": run_id, "updated": datetime.now().isoformat(timespec="seconds")}
    write_json_safely(os.path.join(d, HIST_META), data)
    return {"run_id": run_id, "folder": d}


def t_history_put_chunk(a):
    """Write one chunk (base64) of an uploaded file into a saved run; chunks come in order 0..total-1."""
    import base64
    run_id, d = hist_dir(a.get("run_id"))
    name = hist_file_name(a.get("file"))
    try:
        index, total = int(a.get("index", 0)), int(a.get("total", 1))
    except (TypeError, ValueError):
        raise ValueError("index and total must be numbers.")
    if not (0 <= index < total <= 2000):
        raise ValueError("Bad chunk index.")
    raw = base64.b64decode(str(a.get("data") or ""), validate=False)
    with open(os.path.join(d, name), "wb" if index == 0 else "ab") as f:
        f.write(raw)
    if index == total - 1:                             # last chunk: record the file in run.json
        meta = read_json_safely(os.path.join(d, HIST_META), {})
        files = [x for x in meta.get("files", []) if x.get("name") != name]
        files.append({"name": name, "size": os.path.getsize(os.path.join(d, name))})
        meta["files"] = files
        write_json_safely(os.path.join(d, HIST_META), meta)
    return {"run_id": run_id, "file": name, "index": index, "done": index == total - 1}


def t_history_list(a):
    want = " ".join(str(a.get("search") or "").lower().split())
    runs = []
    if os.path.isdir(HIST_DIR):
        for e in os.scandir(HIST_DIR):
            if not (e.is_dir() and e.name.startswith("cc_")):
                continue
            m = read_json_safely(os.path.join(e.path, HIST_META), {})
            if not m.get("files"):
                continue                               # a save that never finished
            item = {"run_id": e.name, "label": m.get("label", ""), "files": [f["name"] for f in m.get("files", [])],
                    "profile": m.get("profile", ""), "rows": m.get("rows"), "summary_rows": m.get("summary_rows"),
                    "overall": m.get("overall", ""), "id_tag": m.get("id_tag", ""), "changes": len(m.get("rules") or []),
                    "created": m.get("created", ""), "updated": m.get("updated", ""),
                    "size_mb": mb(sum(f.get("size", 0) for f in m.get("files", [])))}
            if want and want not in f"{item['label']} {' '.join(item['files'])} {e.name} {item['profile']}".lower():
                continue
            runs.append(item)
    runs.sort(key=lambda x: x["updated"] or x["created"], reverse=True)
    return {"runs": runs[: int(a.get("limit", 30) or 30)], "matching": len(runs), "folder": HIST_DIR}


def t_history_get(a):
    run_id, d = hist_dir(a.get("run_id"))
    m = read_json_safely(os.path.join(d, HIST_META), {})
    for f in m.get("files", []):
        p = os.path.join(d, f["name"])
        f["size"] = os.path.getsize(p) if os.path.exists(p) else 0
        f["chunks"] = max(1, -(-f["size"] // HIST_CHUNK))
    return m


def t_history_get_chunk(a):
    import base64
    run_id, d = hist_dir(a.get("run_id"))
    name = hist_file_name(a.get("file"))
    p = os.path.join(d, name)
    if not os.path.exists(p):
        raise ValueError(f"{name} isn't in saved run {run_id}.")
    index = int(a.get("index", 0) or 0)
    with open(p, "rb") as f:
        f.seek(index * HIST_CHUNK)
        raw = f.read(HIST_CHUNK)
    return {"run_id": run_id, "file": name, "index": index, "data": base64.b64encode(raw).decode("ascii")}


def t_history_delete(a):
    run_id, d = hist_dir(a.get("run_id"))
    shutil.rmtree(d)
    return {"run_id": run_id, "deleted": True}


def _rows_of(path):
    """(header, row iterator) for an output workbook (first sheet) or an export CSV."""
    if path.lower().endswith(".csv"):
        try:
            csv.field_size_limit(min(sys.maxsize, 2 ** 31 - 1))
        except OverflowError:
            pass
        f = open(path, encoding="utf-8-sig", errors="replace", newline="")
        reader = csv.reader(f)
        header = [str(h).strip() for h in next(reader, [])]

        def it():
            try:
                for r in reader:
                    yield r
            finally:
                f.close()
        return header, it(), f.close
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True)
    rows = wb.active.iter_rows(values_only=True)
    return [cell_text(h) for h in next(rows, ())], rows, wb.close


def t_find_in_outputs(a):
    """Rows in a job's files where a cell equals (or contains) a value - e.g. a contact ID. Searches the Zenu output
    workbooks and the export CSVs copied into the job (contact_cleaned.csv, staff.csv ...)."""
    job_id, d = job_dir(a.get("job_id"))
    q = str(a.get("value") or "").strip().lower()
    if not q:
        raise ValueError("Give the value to look for (e.g. a contact ID).")
    col = str(a.get("column") or "").strip().lower()
    contains = bool(a.get("contains"))
    which = str(a.get("files") or "all").lower()
    try:
        limit = min(max(int(a.get("limit") or 20), 1), 50)
    except (TypeError, ValueError):
        limit = 20
    sources = []
    if which in ("all", "outputs"):
        sources += [(n, "output") for n in output_xlsx(d)]
    if which in ("all", "exports", "inputs"):
        sources += [(e.name, "export") for e in sorted(os.scandir(d), key=lambda e: e.name.lower())
                    if e.is_file() and e.name.lower().endswith(".csv")]
    # the contact file first, then the outputs, then the other exports; at most per_file matches from one file
    sources.sort(key=lambda x: (0 if x[0].lower() == "contact_cleaned.csv" else 1 if x[1] == "output" else 2))
    per_file = 5
    hits, scanned, searched = [], 0, []
    for name, kind in sources:
        if len(hits) >= limit:
            break
        try:
            header, rows, close = _rows_of(os.path.join(d, name))
        except Exception as e:                          # an unreadable file shouldn't stop the search
            log(f"find: can't read {name}: {e}")
            continue
        try:
            idx = [i for i, h in enumerate(header) if h and (not col or h.lower() == col)]
            if not idx:
                continue
            searched.append(name)
            for r in rows:
                scanned += 1
                for i in idx:
                    v = cell_text(r[i]).strip().lower() if i < len(r) else ""
                    if (q in v) if contains else (v == q):
                        hits.append({"file": name, "kind": kind,
                                     "row": {header[k]: cell_text(r[k])[:200] for k in range(min(len(header), len(r)))
                                             if header[k] and cell_text(r[k]).strip()}})
                        break
                if len(hits) >= limit or sum(1 for h in hits if h["file"] == name) >= per_file:
                    break
        finally:
            close()
    return {"job_id": job_id, "value": q, "matches": hits, "files_searched": len(searched), "searched": searched,
            "rows_scanned": scanned, "more_may_exist": len(hits) >= limit}


QUERY_OPS = ("is", "not", "contains", "starts", "blank", "filled", "gt", "gte", "lt", "lte")
QUERY_AGGS = ("count", "sum", "avg", "min", "max")


def _num(s):
    """A number from a cell ('$1,200,000' -> 1200000.0), else None."""
    t = str(s or "").strip().replace("$", "").replace(",", "").replace(" ", "")
    if not t or not re.fullmatch(r"-?\d+(\.\d+)?", t):
        return None
    return float(t)


def _date(s):
    """(y, m, d, H, M, S) from dd/mm/yyyy [hh:mm[:ss]] or yyyy-mm-dd[ hh:mm[:ss]], else None."""
    t = str(s or "").strip()
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?", t)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?", t)
        if not m:
            return None
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return (y, mo, d, int(m.group(4) or 0), int(m.group(5) or 0), int(m.group(6) or 0))


def _sort_key(v):
    """Numbers by value, dates by date, else text; blanks are left out before sorting."""
    n = _num(v)
    if n is not None:
        return (2, n, "")
    d = _date(v)
    if d is not None:
        return (1, 0, "%04d%02d%02d%02d%02d%02d" % d)
    return (0, 0, str(v).lower())


def _cmp_ok(v, op, x):
    a, b = _num(v), _num(x)
    if a is None or b is None:
        a, b = _date(v), _date(x)
    if a is None or b is None:
        return False
    return a > b if op == "gt" else a >= b if op == "gte" else a < b if op == "lt" else a <= b


def _query_file(d, want, cols_needed, source):
    """The job file to query: an output workbook ('zenu_buyer', 'buyer' ...) or an export CSV; with no name, the only
    output workbook that has all the columns asked about."""
    base = os.path.join(d, CHANGED_DIR) if source == "changed" else d
    if not os.path.isdir(base):
        raise ValueError("This job has no 'With changes' copies yet.")
    if source == "folder":
        names = sorted(e.name for e in os.scandir(d) if e.is_file() and e.name.lower().endswith((".csv", ".xlsx"))
                       and not e.name.startswith("~$"))
    else:
        names = output_xlsx(base) + ([] if source == "changed" else
                                     sorted(e.name for e in os.scandir(d) if e.is_file() and e.name.lower().endswith(".csv")))
    w = str(want or "").strip().lower()
    if w:
        stem = lambda n: os.path.splitext(n)[0].lower()
        for test in (lambda n: n.lower() == w, lambda n: stem(n) == w, lambda n: stem(n) == "zenu_" + w,
                     lambda n: stem(n).endswith("_" + w) or stem(n).endswith("_" + w.rstrip("s")), lambda n: w in n.lower()):
            hit = [n for n in names if test(n)]
            if len(hit) == 1:
                return hit[0], os.path.join(base, hit[0])
            if len(hit) > 1:
                raise ValueError(f"'{want}' matches several files: {', '.join(hit)}. Name one.")
        raise ValueError(f"No file '{want}' in this job. Files: {', '.join(names)}")
    have = []
    for n in (names if source == "folder" else output_xlsx(base)):
        try:
            hp = os.path.join(base, n)
            if n.lower().endswith(".csv"):
                hh, _it, cl = _rows_of(hp)
                cl()
            else:
                hh = read_header(hp)
            h = [str(x).lower() for x in hh]
        except Exception:
            continue
        if all(c.lower() in h for c in cols_needed):
            have.append(n)
    if len(have) == 1:
        return have[0], os.path.join(base, have[0])
    raise ValueError(("Several output files have those columns: " + ", ".join(have) if have else
                      "No single output file has all of: " + ", ".join(cols_needed)) + ". Give file.")


def _norm_key(v):
    """Join keys: trimmed, lower case, 1379961.0 / 1,379,961 -> 1379961."""
    t = str(v or "").strip().lower()
    if re.fullmatch(r"-?\d{1,3}(,\d{3})+(\.0+)?|-?\d+\.0+", t):
        t = t.replace(",", "")
        t = re.sub(r"\.0+$", "", t)
    return t


def _load_join(d, j, source, main_header):
    """Read a joined file into {key: [values of the brought-in columns]} (first row per key)."""
    if not isinstance(j, dict) or not j.get("file") or not j.get("on"):
        raise ValueError("Each join needs file (the other file) and on (the column of the main file that holds the key).")
    jname, jpath = _query_file(d, j["file"], [], "folder" if source == "folder" else "original")
    h, rows, close = _rows_of(jpath)
    try:
        low = {x.lower(): i for i, x in enumerate(h) if x}
        to = str(j.get("to") or j["on"]).strip()
        ti = low.get(to.lower())
        if ti is None:
            raise ValueError(f"{jname} has no column '{to}' to match {j['on']} on. Columns: {', '.join(x for x in h if x)[:1500]}")
        want = [str(c) for c in (j.get("columns") or []) if str(c).strip()]
        ci = []
        for c in want:
            i = low.get(c.lower())
            if i is None:
                raise ValueError(f"{jname} has no column '{c}'. Columns: {', '.join(x for x in h if x)[:1500]}")
            ci.append(i)
        if not ci:
            ci = [i for i, x in enumerate(h) if x and i != ti]
        table, dup = {}, 0
        for r in rows:
            k = _norm_key(cell_text(r[ti]) if ti < len(r) else "")
            if not k:
                continue
            if k in table:
                dup += 1
                continue
            table[k] = [cell_text(r[i]) if i < len(r) else "" for i in ci]
    finally:
        close()
    stem = os.path.splitext(jname)[0]
    names = [f"{stem}.{h[i]}" for i in ci]
    plain = [h[i] for i in ci]
    return {"file": jname, "on": j["on"], "to": h[ti], "table": table, "names": names, "plain": plain, "dup": dup}


def t_query_outputs(a):
    """Questions about one file of a job, beyond look-ups: filter (where), sort (sort_by, highest first by default),
    top N (limit), count / sum / avg / min / max of a column, and counts or totals per value (group_by)."""
    if a.get("folder"):
        job_id, d = None, safe_path(a.get("folder"))
        if not os.path.isdir(d):
            raise ValueError("folder must be a folder.")
    else:
        job_id, d = job_dir(a.get("job_id"))
    src_kind = "folder" if a.get("folder") else str(a.get("source") or "original")
    if a.get("list_files"):                                 # which files can be asked about, with their columns
        names = (sorted(e.name for e in os.scandir(d) if e.is_file() and e.name.lower().endswith((".csv", ".xlsx")) and not e.name.startswith("~$"))
                 if src_kind == "folder" else output_xlsx(d) + sorted(e.name for e in os.scandir(d) if e.is_file() and e.name.lower().endswith(".csv")))
        files = []
        for n in names:
            try:
                hh, _it, cl = _rows_of(os.path.join(d, n))
                cl()
                files.append({"file": n, "columns": [h for h in hh if h][:150]})
            except Exception as e:
                files.append({"file": n, "error": str(e)[:120]})
        return {"job_id": job_id, "folder": d if src_kind == "folder" else None, "files": files}
    joins = [j for j in (a.get("join") or []) if j][:3]
    if joins and not a.get("file"):
        raise ValueError("With join, give file (the main file), e.g. zenu_buyer.")
    where = [w for w in (a.get("where") or []) if isinstance(w, dict) and w.get("column")]
    sort_by = str(a.get("sort_by") or "").strip()
    group_by = str(a.get("group_by") or "").strip()
    agg = str(a.get("aggregate") or "").strip().lower()
    agg_col = str(a.get("agg_column") or "").strip()
    if agg and agg not in QUERY_AGGS:
        raise ValueError(f"aggregate is one of {', '.join(QUERY_AGGS)}.")
    if agg and agg != "count" and not agg_col:
        raise ValueError("Give agg_column (the column to add up / average / take the min or max of).")
    for w in where:
        if (w.get("op") or "is") not in QUERY_OPS:
            raise ValueError(f"where op is one of {', '.join(QUERY_OPS)}.")
    want_cols = [str(c) for c in (a.get("columns") or []) if str(c).strip()]
    needed = [w["column"] for w in where] + [c for c in (sort_by, group_by, agg_col) if c] + want_cols
    name, path = _query_file(d, a.get("file"), needed, src_kind)
    try:
        limit = min(max(int(a.get("limit") or 10), 1), 200)
    except (TypeError, ValueError):
        limit = 10
    desc = a.get("descending", True) is not False
    header, rows, close = _rows_of(path)
    try:
        base_len = len(header)
        jl = []
        for j in joins:
            J = _load_join(d, j, src_kind, header)
            oi = {h.lower(): i for i, h in enumerate(header[:base_len]) if h}.get(str(J["on"]).strip().lower())
            if oi is None:
                raise ValueError(f"{name} has no column '{J['on']}' to join {J['file']} on.")
            J["oi"], J["start"], J["found"] = oi, len(header), 0
            header = header + J["names"]
            jl.append(J)
        low = {h.lower(): i for i, h in enumerate(header) if h}
        for J in jl:                                            # a joined column can also be named without its file
            for k, pl in enumerate(J["plain"]):
                low.setdefault(pl.lower(), J["start"] + k)
                low.setdefault(f"{os.path.splitext(J['file'])[0].lower().replace('zenu_', '')}.{pl.lower()}", J["start"] + k)

        def col(c):
            i = low.get(str(c).strip().lower())
            if i is None:
                raise ValueError(f"{name} has no column '{c}'. Columns: {', '.join(h for h in header if h)[:1500]}")
            return i
        conds = [(col(w["column"]), w.get("op") or "is", str(w.get("value") if w.get("value") is not None else "").strip().lower())
                 for w in where]
        si = col(sort_by) if sort_by else None
        gi = col(group_by) if group_by else None
        ai = col(agg_col) if agg_col else None
        show = [col(c) for c in want_cols] if want_cols else None
        total, matched = 0, 0
        top, seq = [], 0                                        # heap of (key, seq, row) - only the best `limit` rows kept
        groups = {}
        acc = {"n": 0, "sum": 0.0, "min": None, "max": None, "min_row": None, "max_row": None}
        first = []
        for r in rows:
            total += 1
            cells = [cell_text(r[i]) if i < len(r) else "" for i in range(base_len)]
            for J in jl:
                got = J["table"].get(_norm_key(cells[J["oi"]]))
                if got is not None:
                    J["found"] += 1
                cells.extend(got if got is not None else [""] * len(J["names"]))
            ok = True
            for i, op, x in conds:
                v = cells[i].lower()
                if op == "is":
                    ok = v == x
                elif op == "not":
                    ok = v != x
                elif op == "contains":
                    ok = x in v
                elif op == "starts":
                    ok = v.startswith(x)
                elif op == "blank":
                    ok = not v
                elif op == "filled":
                    ok = bool(v)
                else:
                    ok = _cmp_ok(cells[i], op, x)
                if not ok:
                    break
            if not ok:
                continue
            matched += 1
            if ai is not None:
                n = _num(cells[ai])
                if n is not None:
                    acc["n"] += 1
                    acc["sum"] += n
                    if acc["min"] is None or n < acc["min"]:
                        acc["min"], acc["min_row"] = n, cells
                    if acc["max"] is None or n > acc["max"]:
                        acc["max"], acc["max_row"] = n, cells
            if gi is not None:
                g = groups.setdefault(cells[gi] or "(blank)", {"count": 0, "n": 0, "sum": 0.0, "min": None, "max": None})
                g["count"] += 1
                if ai is not None:
                    n = _num(cells[ai])
                    if n is not None:
                        g["n"] += 1
                        g["sum"] += n
                        g["min"] = n if g["min"] is None else min(g["min"], n)
                        g["max"] = n if g["max"] is None else max(g["max"], n)
            if si is not None:
                if not cells[si]:
                    continue                                   # blanks never make the top
                k = _sort_key(cells[si])
                seq += 1
                item = (k if desc else _neg(k), -seq, cells)
                if len(top) < limit:
                    heapq.heappush(top, item)
                elif item > top[0]:
                    heapq.heapreplace(top, item)
            elif len(first) < limit:
                first.append(cells)
    finally:
        close()

    def pick(cells):
        idx = show if show else [i for i in range(len(header)) if header[i] and cells[i]]
        keep = set(idx) | ({si} if si is not None else set()) | ({ai} if ai is not None else set())
        return {header[i]: cells[i][:200] for i in sorted(keep, key=lambda i: (idx.index(i) if i in idx else 999, i)) if header[i]}
    out = {"job_id": job_id, "file": name, "rows_in_file": total, "matched": matched}
    if a.get("folder"):
        out["folder"] = d
    if jl:
        out["joined"] = [{"file": J["file"], "on": f"{J['on']} = {J['to']}", "rows_with_a_match": J["found"], "of_rows": total,
                          "columns": J["names"], **({"keys_with_several_rows_first_used": J["dup"]} if J["dup"] else {})} for J in jl]
    if si is not None:
        out["sorted_by"] = f"{header[si]} ({'highest' if desc else 'lowest'} first; blanks left out)"
        out["rows"] = [pick(c) for _k, _s, c in sorted(top, reverse=True)]
    elif gi is None and not agg:
        out["rows"] = [pick(c) for c in first]
    if agg:
        name_a = header[ai] if ai is not None else ""
        if agg == "count":
            out["result"] = {"count": matched}
        else:
            val = (acc["sum"] if agg == "sum" else (acc["sum"] / acc["n"] if acc["n"] else None) if agg == "avg"
                   else acc[agg])
            out["result"] = {agg: round(val, 2) if isinstance(val, float) else val, "column": name_a, "numbers_used": acc["n"]}
            if agg in ("min", "max") and acc[agg + "_row"]:
                out["result"]["row"] = pick(acc[agg + "_row"])
    if gi is not None:
        def gval(g):
            if not agg or agg == "count":
                return g["count"]
            return round(g["sum"], 2) if agg == "sum" else round(g["sum"] / g["n"], 2) if agg == "avg" and g["n"] else g.get(agg) or 0
        ordered = sorted(groups.items(), key=lambda kv: gval(kv[1]), reverse=True)
        out["group_by"] = header[gi]
        out["groups"] = [{"value": k[:120], "count": g["count"], **({agg: gval(g)} if agg and agg != "count" else {})} for k, g in ordered[:50]]
        out["group_count"] = len(groups)
    return out


def _neg(k):
    """Sort key for lowest first inside a max-heap of 'best' rows."""
    return (-k[0], -k[1], "".join(chr(0x10FFFF - ord(ch)) for ch in k[2]) + chr(0x10FFFF))


def t_open_folder(a):
    if a.get("history"):
        d = hist_dir(a.get("run_id"))[1] if a.get("run_id") else HIST_DIR
        os.makedirs(d, exist_ok=True)
    elif a.get("job_id") and a.get("consolidated"):
        _job_id, d = job_dir(a.get("job_id"))
        d = os.path.join(d, CONSOL_DIR)
        if not os.path.isdir(d):
            raise ValueError("There are no combined workbooks for this job yet.")
    elif a.get("job_id") and a.get("changed"):
        _job_id, d = job_dir(a.get("job_id"))
        d = os.path.join(d, CHANGED_DIR)
        if not os.path.isdir(d):
            raise ValueError("There are no changed files for this job yet.")
    elif a.get("job_id"):
        _job_id, d = job_dir(a.get("job_id"))
    else:
        d = safe_path(a.get("path"))
    if hasattr(os, "startfile"):
        os.startfile(d)                            # opens File Explorer on this PC
        return {"opened": d}
    return {"opened": "", "path": d, "note": "Open this folder yourself."}


# ---------------------------------------------------------------- JSON import (the page converts; files are read and saved here)
JSON_DIR = "JSON Import"


def _job_base(d, source):
    if source == "changed":
        base = os.path.join(d, CHANGED_DIR)
        if not os.path.isdir(base):
            raise ValueError("This job has no 'With changes' copies.")
        return base
    return d


def _csv_header(path):
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        return [str(h).strip() for h in next(csv.reader(f), [])]


def t_json_sources(a):
    """The files a JSON import can be made from: the job's output workbooks (original or 'With changes') and the
    cleaned contacts (contact_cleaned.csv), with their columns, sizes and chunk counts."""
    job_id, d = job_dir(a.get("job_id"))
    if job_state(job_id, d)[3] == "running":
        raise ValueError("The migration is still running.")
    source = "changed" if a.get("source") == "changed" else "original"
    base = _job_base(d, source)
    files = []
    for name in output_xlsx(base):
        p = os.path.join(base, name)
        size = os.path.getsize(p)
        item = {"file": name, "kind": "output", "size": size, "size_mb": mb(size), "chunks": max(1, -(-size // HIST_CHUNK))}
        try:
            _sheet, cols, rows = read_header(p)
            item.update(columns=[c for c in cols if c][:400], rows=rows)
        except Exception as e:
            item["error"] = f"can't read: {e}"
        files.append(item)
    for name in sorted(os.listdir(d)):
        if name.lower() == "contact_cleaned.csv" and os.path.isfile(os.path.join(d, name)):
            p = os.path.join(d, name)
            size = os.path.getsize(p)
            files.append({"file": name, "kind": "contacts", "size": size, "size_mb": mb(size),
                          "chunks": max(1, -(-size // HIST_CHUNK)), "columns": _csv_header(p)[:400]})
    saved = []
    jd = os.path.join(d, JSON_DIR)
    if os.path.isdir(jd):
        saved = [{"file": e.name, "size_mb": mb(e.stat().st_size), "modified": datetime.fromtimestamp(e.stat().st_mtime).strftime("%Y-%m-%d %H:%M")}
                 for e in sorted(os.scandir(jd), key=lambda e: e.name.lower()) if e.is_file() and not e.name.endswith(".part")]
    meta = read_meta(d)
    return {"job_id": job_id, "client": meta.get("client", ""), "source": source, "has_changed": os.path.isdir(os.path.join(d, CHANGED_DIR)),
            "files": files, "saved": saved, "json_folder": jd}


def t_job_file_chunk(a):
    """One base64 chunk of a job's output workbook (or 'With changes' copy) or contact_cleaned.csv."""
    import base64
    job_id, d = job_dir(a.get("job_id"))
    name = os.path.basename(str(a.get("file") or "")).strip()
    if name.lower() == "contact_cleaned.csv":
        p = os.path.join(d, name)
    elif name in output_xlsx(_job_base(d, "changed" if a.get("source") == "changed" else "original")):
        p = os.path.join(_job_base(d, "changed" if a.get("source") == "changed" else "original"), name)
    else:
        raise ValueError(f"{name} isn't an output file of job {job_id}.")
    size = os.path.getsize(p)
    index = int(a.get("index", 0) or 0)
    with open(p, "rb") as f:
        f.seek(index * HIST_CHUNK)
        raw = f.read(HIST_CHUNK)
    return {"job_id": job_id, "file": name, "index": index, "chunks": max(1, -(-size // HIST_CHUNK)), "size": size,
            "data": base64.b64encode(raw).decode("ascii")}


def t_save_json_import(a):
    """Write one base64 chunk of the import.json (or its check report) into the job's 'JSON Import' folder.
    Chunks come in order 0..total-1; the file only replaces an earlier one once the last chunk is in."""
    import base64
    job_id, d = job_dir(a.get("job_id"))
    name = hist_file_name(a.get("file"))
    if not name.lower().endswith((".json", ".csv")):
        raise ValueError("Only .json and .csv files can be saved here.")
    try:
        index, total = int(a.get("index", 0)), int(a.get("total", 1))
    except (TypeError, ValueError):
        raise ValueError("index and total must be numbers.")
    if not (0 <= index < total <= 5000):
        raise ValueError("Bad chunk index.")
    folder = os.path.join(d, JSON_DIR)
    os.makedirs(folder, exist_ok=True)
    part = os.path.join(folder, name + ".part")
    with open(part, "wb" if index == 0 else "ab") as f:
        f.write(base64.b64decode(str(a.get("data") or ""), validate=False))
    out = {"job_id": job_id, "file": name, "index": index, "done": index == total - 1, "folder": folder}
    if index == total - 1:
        final = os.path.join(folder, name)
        os.replace(part, final)
        out.update(path=final, size_mb=mb(os.path.getsize(final)))
    return out


RO = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
TOOLS = [
    ("bridge_status", "Is the DARVIS bridge running on this PC, and can it load the migration engine? Lists the CRMs.", {}, t_status, RO),
    ("list_folder", "Browse folders on this PC (inside the user folder). No path: common places. Shows sub-folders and CSV/JSON files.",
     {"path": {"type": "string"}}, t_list_folder, RO),
    ("inspect_export", "Describe a CRM export folder: its CSV files, sizes, header columns and which CRM it looks like.",
     {"path": {"type": "string"}}, t_inspect_export, RO),
    ("start_migration", "Start a migration on this PC: copies the export folder's CSVs into a new job in client_migrations and runs "
     "the Zenu Migration Tool engine with the given matrices (rules).",
     {"client": {"type": "string", "description": "the client's name (agency), saved with the job"},
      "export_folder": {"type": "string"}, "crm": {"type": "string", "enum": list(CRMS)},
      "matrices": {"type": "object", "description": "{group name: [rules]} from the classic tool's matrix JSON"},
      "chunk_size": {"type": "integer", "description": "rows per output file, 0 = no split"},
      "top_up": {"type": "boolean"}, "baseline_folder": {"type": "string"}}, t_start_migration,
     {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}),
    ("job_status", "Progress, recent log lines and output files of a migration job.",
     {"job_id": {"type": "string"}, "tail": {"type": "integer"}}, t_job_status, RO),
    ("list_jobs", "Recent migration jobs on this PC (DARVIS and classic tool), newest first, with each job's client. "
     "'client' searches client names (and job ids / CRMs).",
     {"limit": {"type": "integer"}, "client": {"type": "string"}}, t_list_jobs, RO),
    ("set_client_name", "Name (or rename) the client of a migration job. Saved in the job's job_meta.json.",
     {"job_id": {"type": "string"}, "client": {"type": "string"}}, t_set_client_name,
     {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}),
    ("job_log", "A job's process.log (optionally only lines containing a word).",
     {"job_id": {"type": "string"}, "tail": {"type": "integer"}, "contains": {"type": "string"}}, t_job_log, RO),
    ("start_reconciliation", "Run the reconciliation for a finished job (writes Reconciliation_Report.xlsx in the job folder).",
     {"job_id": {"type": "string"}}, t_start_reconciliation, {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}),
    ("reconciliation_report", "The reconciliation summary of a job.", {"job_id": {"type": "string"}}, t_reconciliation_report, RO),
    ("open_folder", "Open a job's folder (changed: its 'With changes' folder; consolidated: its 'Consolidated' folder) "
     "or a path in File Explorer on this PC.",
     {"job_id": {"type": "string"}, "changed": {"type": "boolean"}, "consolidated": {"type": "boolean"}, "history": {"type": "boolean"},
      "run_id": {"type": "string"}, "path": {"type": "string"}},
     t_open_folder,
     {"readOnlyHint": True, "openWorldHint": False}),
    ("output_columns", "The output workbooks of a finished job with their columns and row counts.",
     {"job_id": {"type": "string"}}, t_output_columns, RO),
    ("apply_output_changes", "Change values in a job's output files: writes changed copies into the job's 'With changes' folder "
     "(the originals are never changed) using ALL the given rules, replacing earlier changes. rules: [{file ('all' or a file name), "
     "column, action (set | fill | replace | clear | combine), value, find (for replace), sources + separator + labels (for combine: "
     "columns joined into column, blanks skipped), optional where_column + where_op (is | contains | blank | filled) + where_value}]. "
     "An empty list undoes all changes (removes the changed copies).",
     {"job_id": {"type": "string"}, "rules": {"type": "array", "items": {"type": "object"}}}, t_apply_output_changes,
     {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}),
    ("output_changes_status", "The value changes saved for a job's output files, and whether the changed copies are written "
     "(cells changed per rule and per file).", {"job_id": {"type": "string"}}, t_output_changes_status, RO),
    ("consolidate_outputs", "Combine a finished job's output files into ONE workbook, one tab per file plus a Contents tab, "
     "saved in the job's 'Consolidated' folder (the output files are not changed). files: output file names (empty = all); "
     "source: 'original' or 'changed' (the 'With changes' copies); name: optional workbook name.",
     {"job_id": {"type": "string"}, "files": {"type": "array", "items": {"type": "string"}},
      "source": {"type": "string", "enum": ["original", "changed"]}, "name": {"type": "string"}}, t_consolidate_outputs,
     {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}),
    ("consolidation_status", "Whether the combined workbook for a job is being written / done (tabs, rows, path), the combined "
     "workbooks already in its Consolidated folder, and whether 'With changes' copies exist.",
     {"job_id": {"type": "string"}}, t_consolidation_status, RO),
    ("find_in_outputs", "Find rows in a job's files where a cell equals a value (e.g. a contact ID): the Zenu output workbooks and "
     "the export CSVs in the job (contact_cleaned.csv, staff.csv ...). contains=true matches part of a cell; column limits the "
     "search to one column; files: all | outputs | exports. Returns the matching rows (up to 50).",
     {"job_id": {"type": "string"}, "value": {"type": "string"}, "column": {"type": "string"}, "contains": {"type": "boolean"},
      "files": {"type": "string", "enum": ["all", "outputs", "exports"]}, "limit": {"type": "integer"}}, t_find_in_outputs, RO),
    ("query_outputs", "Answer questions about ONE file of a job beyond look-ups: where filters (op is | not | contains | starts | "
     "blank | filled | gt | gte | lt | lte; gt/lt compare numbers or dates), sort_by + descending (default highest first) + limit "
     "(top N rows), aggregate (count | sum | avg | min | max of agg_column; min/max also return that row), group_by (count, or "
     "the aggregate, per value). file: output file ('zenu_buyer' / 'buyer') or export CSV; omitted = the only output file with "
     "those columns. join: [{file, on (main column), to (its column, default = on), columns}] brings other files' columns in by a "
     "key (e.g. buyer property_identifier = listings.csv id -> state), usable as file.column or column. columns: what to return. "
     "source: original | changed. folder: query a folder on the PC (e.g. an export not migrated yet) instead of a job. "
     "list_files=true: just list the files and their columns.",
     {"job_id": {"type": "string"}, "file": {"type": "string"}, "where": {"type": "array", "items": {"type": "object"}},
      "sort_by": {"type": "string"}, "descending": {"type": "boolean"}, "limit": {"type": "integer"},
      "columns": {"type": "array", "items": {"type": "string"}}, "group_by": {"type": "string"},
      "aggregate": {"type": "string", "enum": list(QUERY_AGGS)}, "agg_column": {"type": "string"},
      "source": {"type": "string", "enum": ["original", "changed"]}, "folder": {"type": "string"}, "list_files": {"type": "boolean"},
      "join": {"type": "array", "items": {"type": "object"}}}, t_query_outputs, RO),
    ("history_save", "Save (no run_id: create) a Contact cleaning run's details on this PC: label, handler, mapping, settings, value "
     "changes and a summary. The uploaded files are sent with history_put_chunk.",
     {"run_id": {"type": "string"}, "meta": {"type": "object"}}, t_history_save, {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}),
    ("history_put_chunk", "Write one base64 chunk of an uploaded file into a saved Contact cleaning run (chunks in order).",
     {"run_id": {"type": "string"}, "file": {"type": "string"}, "index": {"type": "integer"}, "total": {"type": "integer"},
      "data": {"type": "string"}}, t_history_put_chunk, {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}),
    ("history_list", "Saved Contact cleaning runs on this PC, newest first (search: label, file name or run id).",
     {"search": {"type": "string"}, "limit": {"type": "integer"}}, t_history_list, RO),
    ("history_get", "A saved Contact cleaning run's details and its files (with chunk counts).", {"run_id": {"type": "string"}},
     t_history_get, RO),
    ("history_get_chunk", "One base64 chunk of a saved run's uploaded file.",
     {"run_id": {"type": "string"}, "file": {"type": "string"}, "index": {"type": "integer"}}, t_history_get_chunk, RO),
    ("history_delete", "Delete a saved Contact cleaning run (its folder) from this PC.", {"run_id": {"type": "string"}},
     t_history_delete, {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False}),
    ("json_sources", "For the JSON import: a finished job's output workbooks (source original | changed) and contact_cleaned.csv, "
     "with columns, sizes and chunk counts, plus the files already saved in its 'JSON Import' folder.",
     {"job_id": {"type": "string"}, "source": {"type": "string", "enum": ["original", "changed"]}}, t_json_sources, RO),
    ("job_file_chunk", "One base64 chunk of a job's output workbook or contact_cleaned.csv (for the JSON import).",
     {"job_id": {"type": "string"}, "file": {"type": "string"}, "index": {"type": "integer"},
      "source": {"type": "string", "enum": ["original", "changed"]}}, t_job_file_chunk, RO),
    ("save_json_import", "Write one base64 chunk of a JSON import file (.json or its _check.csv) into the job's 'JSON Import' "
     "folder (chunks in order; replaces the file of the same name when the last chunk is in).",
     {"job_id": {"type": "string"}, "file": {"type": "string"}, "index": {"type": "integer"}, "total": {"type": "integer"},
      "data": {"type": "string"}}, t_save_json_import, {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}),
]
REQUIRED = {"inspect_export": ["path"], "start_migration": ["client", "export_folder", "crm", "matrices"], "job_status": ["job_id"],
            "set_client_name": ["job_id", "client"], "output_columns": ["job_id"], "apply_output_changes": ["job_id", "rules"],
            "output_changes_status": ["job_id"], "consolidate_outputs": ["job_id"], "consolidation_status": ["job_id"],
            "find_in_outputs": ["job_id", "value"],
            "history_put_chunk": ["run_id", "file", "index", "total", "data"], "history_get": ["run_id"],
            "history_get_chunk": ["run_id", "file", "index"], "history_delete": ["run_id"],
            "json_sources": ["job_id"], "job_file_chunk": ["job_id", "file", "index"],
            "save_json_import": ["job_id", "file", "index", "total", "data"],
            "job_log": ["job_id"], "start_reconciliation": ["job_id"], "reconciliation_report": ["job_id"]}
HANDLERS = {name: fn for name, _d, _p, fn, _ann in TOOLS}


# ---------------------------------------------------------------- MCP over stdio
def send(obj):
    PROTO_OUT.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
    PROTO_OUT.flush()


def tool_list():
    return [{"name": n, "description": d, "annotations": ann,
             "inputSchema": {"type": "object", "properties": p, "required": REQUIRED.get(n, [])}}
            for n, d, p, _fn, ann in TOOLS]


def handle(msg):
    method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if mid is None:                                 # notification
        return
    if method == "initialize":
        want = params.get("protocolVersion") or "2025-06-18"
        version = want if want in ("2024-11-05", "2025-03-26", "2025-06-18") else "2025-06-18"
        send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": version, "capabilities": {"tools": {}},
                                                     "serverInfo": {"name": "daris_local", "version": VERSION},
                                                     "instructions": "DARVIS bridge: runs Zenu Migration Tool jobs on this PC."}})
    elif method == "ping":
        send({"jsonrpc": "2.0", "id": mid, "result": {}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": tool_list()}})
    elif method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        fn = HANDLERS.get(name)
        if not fn:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"Unknown tool: {name}"}})
            return
        try:
            out = fn(args)
            send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}],
                                                         "structuredContent": out, "isError": False}})
        except Exception as e:
            log(f"{name} failed: {e}")
            send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": str(e)}], "isError": True}})
    elif method in ("resources/list", "prompts/list"):
        send({"jsonrpc": "2.0", "id": mid, "result": {"resources": []} if method == "resources/list" else {"prompts": []}})
    else:
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}})


def main():
    log(f"started (v{VERSION}) for {PROJECT}")
    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        for m in (msg if isinstance(msg, list) else [msg]):
            try:
                handle(m)
            except Exception as e:                   # never let one bad message stop the bridge
                log(traceback.format_exc())
                if m.get("id") is not None:
                    send({"jsonrpc": "2.0", "id": m.get("id"), "error": {"code": -32603, "message": str(e)}})


if __name__ == "__main__":
    main()
