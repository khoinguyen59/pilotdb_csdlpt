import os
import json
import sqlite3
import hashlib
import time
import re
import logging
from contextlib import contextmanager
import pandas as pd
import sqlglot
from sqlglot import exp

logger = logging.getLogger(__name__)

class PilotCacheManager:
    """Adaptive Pilot Query Cache Manager.
    
    Provides two layers of caching for AQP:
    - Layer 1 (Exact Cache): Key is the exact pilot SQL query. Stores pilot results DataFrame.
    - Layer 2 (Template Cache): Key is the normalized query template. Stores solved table sampling rates.
    """
    
    def __init__(self, cache_db_path: str = ".pilotdb_cache.db", ttl_seconds: int = 86400):
        self.cache_db_path = cache_db_path
        self.ttl_seconds = ttl_seconds
        self._init_db()
        
    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.cache_db_path)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        
    def _init_db(self):
        with self._connection() as conn:
            cursor = conn.cursor()
            # Layer 1: Exact Cache
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS exact_cache (
                    query_hash TEXT PRIMARY KEY,
                    pilot_sql TEXT NOT NULL,
                    pilot_results TEXT NOT NULL, -- serialized pandas DataFrame
                    created_at REAL NOT NULL
                )
            """)
            # Layer 2: Template Cache
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS template_cache (
                    template_hash TEXT PRIMARY KEY,
                    query_template TEXT NOT NULL,
                    solved_rates TEXT NOT NULL, -- JSON serialized dict
                    created_at REAL NOT NULL
                )
            """)
            
    def _get_hash(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
        
    def normalize_query(self, sql: str, dialect: str) -> str:
        """Parse query with sqlglot and replace literals with placeholders."""
        try:
            parsed = sqlglot.parse_one(sql, read=dialect)
            # Replace string, number, date literals
            for literal in parsed.find_all(exp.Literal):
                literal.replace(exp.Literal.string("?"))
            # Replace Boolean expressions
            for boolean in parsed.find_all(exp.Boolean):
                boolean.replace(exp.Literal.string("?"))
            return parsed.sql(dialect)
        except Exception as e:
            logger.debug("sqlglot failed to parse query, falling back to regex: %e", e)
            # Graceful fallback: collapse whitespace, lowercase, replace numeric patterns and quotes
            cleaned = re.sub(r"\s+", " ", sql).strip().lower()
            cleaned = re.sub(r"'\d{4}-\d{2}-\d{2}'", "'?'", cleaned)
            cleaned = re.sub(r"'\d{2}/\d{2}/\d{4}'", "'?'", cleaned)
            cleaned = re.sub(r"'.*?'", "'?'", cleaned)
            cleaned = re.sub(r"\b\d+\b", "?", cleaned)
            return cleaned

    def get_exact_cache(self, pilot_sql: str) -> pd.DataFrame | None:
        """Layer 1: Retrieve pilot results DataFrame from exact SQL cache if not expired."""
        query_hash = self._get_hash(pilot_sql)
        try:
            with self._connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT pilot_results, created_at FROM exact_cache WHERE query_hash = ?",
                    (query_hash,)
                )
                row = cursor.fetchone()
                if row:
                    results_json, created_at = row
                    if time.time() - created_at <= self.ttl_seconds:
                        logger.info("[Cache Layer 1] Hit for exact pilot query hash: %s", query_hash[:8])
                        data = json.loads(results_json)
                        return pd.DataFrame(data)
                    else:
                        logger.info("[Cache Layer 1] Expired entry for exact pilot query hash: %s", query_hash[:8])
                        cursor.execute("DELETE FROM exact_cache WHERE query_hash = ?", (query_hash,))
        except Exception as e:
            logger.warning("[Cache Layer 1] Error reading cache: %s", e)
        return None

    def set_exact_cache(self, pilot_sql: str, pilot_results: pd.DataFrame):
        """Layer 1: Save pilot results DataFrame to exact SQL cache."""
        query_hash = self._get_hash(pilot_sql)
        try:
            results_json = pilot_results.to_json(orient="records")
            with self._connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT OR REPLACE INTO exact_cache (query_hash, pilot_sql, pilot_results, created_at) VALUES (?, ?, ?, ?)",
                    (query_hash, pilot_sql, results_json, time.time())
                )
                logger.info("[Cache Layer 1] Stored results for exact query hash: %s", query_hash[:8])
        except Exception as e:
            logger.warning("[Cache Layer 1] Error writing cache: %s", e)

    def get_template_cache(self, sql: str, dialect: str) -> dict | None:
        """Layer 2: Retrieve solved sampling rates from template cache if not expired."""
        template = self.normalize_query(sql, dialect)
        template_hash = self._get_hash(template)
        try:
            with self._connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT solved_rates, created_at FROM template_cache WHERE template_hash = ?",
                    (template_hash,)
                )
                row = cursor.fetchone()
                if row:
                    rates_json, created_at = row
                    if time.time() - created_at <= self.ttl_seconds:
                        logger.info("[Cache Layer 2] Hit for query template hash: %s", template_hash[:8])
                        return json.loads(rates_json)
                    else:
                        logger.info("[Cache Layer 2] Expired entry for template hash: %s", template_hash[:8])
                        cursor.execute("DELETE FROM template_cache WHERE template_hash = ?", (template_hash,))
        except Exception as e:
            logger.warning("[Cache Layer 2] Error reading cache: %s", e)
        return None

    def set_template_cache(self, sql: str, dialect: str, solved_rates: dict):
        """Layer 2: Save solved sampling rates to template cache."""
        template = self.normalize_query(sql, dialect)
        template_hash = self._get_hash(template)
        try:
            rates_json = json.dumps(solved_rates)
            with self._connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT OR REPLACE INTO template_cache (template_hash, query_template, solved_rates, created_at) VALUES (?, ?, ?, ?)",
                    (template_hash, template, rates_json, time.time())
                )
                logger.info("[Cache Layer 2] Stored solved rates for template hash: %s", template_hash[:8])
        except Exception as e:
            logger.warning("[Cache Layer 2] Error writing cache: %s", e)

    def clear(self):
        """Delete all cached entries."""
        try:
            with self._connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM exact_cache")
                cursor.execute("DELETE FROM template_cache")
                logger.info("[Cache] Cleared all cache entries.")
        except Exception as e:
            logger.warning("[Cache] Error clearing cache: %s", e)
