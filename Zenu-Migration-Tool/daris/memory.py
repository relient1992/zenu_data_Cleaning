import os
import sqlite3
from datetime import datetime

from .knowledge import is_placeholder, normalize_header

# =====================================================================
# MAPPING MEMORY
# ---------------------------------------------------------------------
# Every header -> Zenu field decision that was confirmed by you (source
# 'user') or suggested by the local LLM (source 'llm') is kept here, so
# the same header is never re-solved. A user row always beats an llm row.
# target = '' means "never map this header" (keep it as-is).
# =====================================================================

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daris_memory.db")


class MappingMemory:
    def __init__(self, db_path: str = DEFAULT_DB):
        self.db_path = db_path
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS header_mappings (
                    norm_header TEXT PRIMARY KEY,
                    raw_header  TEXT,
                    target      TEXT,
                    source      TEXT,
                    hits        INTEGER DEFAULT 0,
                    updated     DATETIME
                )
            """)
            conn.commit()

    def lookup(self, header):
        """Return (target, source) or None."""
        key = normalize_header(header)
        if not key or is_placeholder(header):
            return None
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT target, source FROM header_mappings WHERE norm_header = ?",
                (key,)).fetchone()
            if row:
                conn.execute("UPDATE header_mappings SET hits = hits + 1 WHERE norm_header = ?",
                             (key,))
                conn.commit()
        return (row[0], row[1]) if row else None

    def remember(self, header, target, source="user"):
        """Returns False (and stores nothing) for placeholder headers like 'Column_5'."""
        key = normalize_header(header)
        if not key or is_placeholder(header):
            return False
        with sqlite3.connect(self.db_path) as conn:
            existing = conn.execute(
                "SELECT source FROM header_mappings WHERE norm_header = ?", (key,)).fetchone()
            # Never let a model guess overwrite a human decision
            if existing and existing[0] == "user" and source != "user":
                return False
            conn.execute("""
                INSERT INTO header_mappings (norm_header, raw_header, target, source, hits, updated)
                VALUES (?, ?, ?, ?, 0, ?)
                ON CONFLICT(norm_header) DO UPDATE SET
                    raw_header=excluded.raw_header, target=excluded.target,
                    source=excluded.source, updated=excluded.updated
            """, (key, str(header), target or "", source, datetime.now()))
            conn.commit()
        return True

    def forget(self, header):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM header_mappings WHERE norm_header = ?",
                         (normalize_header(header),))
            conn.commit()

    def stats(self):
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT source, COUNT(*) FROM header_mappings GROUP BY source").fetchall()
        return {src: cnt for src, cnt in rows}


# =====================================================================
# ROLE MEMORY (General cleaning)
# ---------------------------------------------------------------------
# Which column you confirmed for each cleaning role (first name, partner
# mobile, do-not-SMS ...), learned from every General run with "Remember my
# changes" ticked - so the next export from the same CRM maps itself.
# Roles without a Zenu field (second person, permissions) can only be
# learned here.
# =====================================================================
class RoleMemory:
    def __init__(self, db_path: str = DEFAULT_DB):
        self.db_path = db_path
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS role_mappings (
                    norm_header TEXT PRIMARY KEY,
                    raw_header  TEXT,
                    role        TEXT,
                    hits        INTEGER DEFAULT 0,
                    updated     DATETIME
                )
            """)
            conn.commit()

    def lookup_many(self, headers):
        """{header: role} for the headers that were confirmed before."""
        keys = {normalize_header(h): h for h in headers if normalize_header(h) and not is_placeholder(h)}
        if not keys:
            return {}
        with sqlite3.connect(self.db_path) as conn:
            q = ",".join("?" * len(keys))
            rows = conn.execute(f"SELECT norm_header, role FROM role_mappings WHERE norm_header IN ({q})",
                                list(keys)).fetchall()
        return {keys[k]: role for k, role in rows if role}

    def remember(self, header, role):
        key = normalize_header(header)
        if not key or is_placeholder(header) or not role:
            return False
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO role_mappings (norm_header, raw_header, role, hits, updated)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(norm_header) DO UPDATE SET
                    raw_header=excluded.raw_header, role=excluded.role,
                    hits=role_mappings.hits + 1, updated=excluded.updated
            """, (key, str(header), role, datetime.now().isoformat(sep=" ")))
            conn.commit()
        return True

    def forget(self, header):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM role_mappings WHERE norm_header = ?", (normalize_header(header),))
            conn.commit()

    def count(self):
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM role_mappings").fetchone()[0]
