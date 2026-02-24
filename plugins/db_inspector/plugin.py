"""
Database Inspector Tool.
Provides schema inspection and read-only query capabilities.
"""
import subprocess
import json
import logging
import sqlite3
import os
from typing import List, Dict, Optional, Any

logger = logging.getLogger(__name__)

class DatabaseInspector:
    def __init__(self, connection_string: str = None):
        # Allow connection string to be passed, or detect from env?
        # For now, explicit passthrough.
        self.conn_str = connection_string or os.getenv("DATABASE_URL", "")

    def inspect_tables(self) -> List[str]:
        """List tables in the database."""
        if not self.conn_str:
            return []

        if self.conn_str.startswith("sqlite"):
            return self._inspect_sqlite_tables()
        elif "postgres" in self.conn_str or "psql" in self.conn_str:
            return self._inspect_postgres_tables()
        return []

    def _inspect_sqlite_tables(self) -> List[str]:
        path = self.conn_str.replace("sqlite:///", "").replace("sqlite://", "")
        if not os.path.exists(path):
            return []
        try:
            conn = sqlite3.connect(path)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = [row[0] for row in cursor.fetchall()]
            conn.close()
            return tables
        except Exception as e:
            logger.warning(f"SQLite inspection failed: {e}")
            return []

    def _inspect_postgres_tables(self) -> List[str]:
        # Fallback to psql CLI if python env doesn't have psycopg2/sqlalchemy
        # Assumes DATABASE_URL is set or psql works with env vars
        try:
            # Parse conn_str or use env
            # Command: psql $URL -c "\dt"
            cmd = ["psql", self.conn_str, "-c", "\\dt"]
            result = subprocess.run(cmd, capture_output=True, text=True)
            # Parse output... simpler to just return raw output for LLM?
            # Or use json output if supported (psql usually text)
            return [line.strip() for line in result.stdout.split('\n') if line.strip()] 
        except Exception:
             return []

    def run_query(self, sql: str) -> str:
        """Run a read-only query (best effort)."""
        if "drop " in sql.lower() or "delete " in sql.lower() or "update " in sql.lower():
             return "Error: Only read-only queries allowed in Inspector."

        if self.conn_str.startswith("sqlite"):
            path = self.conn_str.replace("sqlite:///", "").replace("sqlite://", "")
            try:
                conn = sqlite3.connect(path)
                cursor = conn.cursor()
                cursor.execute(sql)
                rows = cursor.fetchall()
                conn.close()
                return str(rows)
            except Exception as e:
                return str(e)
        
        # Postgres via CLI
        if "postgres" in self.conn_str:
             try:
                 cmd = ["psql", self.conn_str, "-c", sql]
                 result = subprocess.run(cmd, capture_output=True, text=True)
                 return result.stdout + result.stderr
             except Exception as e:
                 return str(e)

        return "Unsupported database type."
