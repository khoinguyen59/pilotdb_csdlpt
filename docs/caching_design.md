# Adaptive Pilot Query Caching Design

This document details the architectural design and implementation plan for the **Adaptive Pilot Query Caching** module in PilotDB.

---

## 1. Problem Statement & Motivation

As highlighted in the PilotDB paper (Figure 13) and empirical evaluations, the **pilot query execution (sample planning)** is the primary source of overhead in Approximate Query Processing (AQP). For fast DBMS like DuckDB or highly cached systems, the pilot run (at 1% sampling rate) can take up to 90% of the total AQP execution time, especially for complex queries.

For repeated analytical workloads (e.g., dashboard refreshes, templated BI queries), running the pilot query and the optimization solver on every execution is redundant. By caching and reusing either the pilot query statistics or the final sampling plan (rates), we can reduce AQP overhead to virtually **0 seconds**.

---

## 2. Dual-Layer Cache Architecture

To achieve high hit rates and maximize overhead reduction, PilotDB implements a **Dual-Layer Cache**:

```
                  [User Input SQL Query]
                            │
                            ▼
               ┌────────────────────────┐
               │    Layer 1: Exact      │ ──[Hit]──► Retrieve Pilot Statistics
               │      SQL Cache         │            (Run Optimizer Solver ──► Final Query)
               └────────────────────────┘
                            │
                         [Miss]
                            │
                            ▼
               ┌────────────────────────┐
               │   Layer 2: Template    │ ──[Hit]──► Retrieve Solved Sampling Rates
               │   (Similarity) Cache   │            (Skip Pilot & Solver ──► Final Query)
               └────────────────────────┘
                            │
                         [Miss]
                            │
                            ▼
                 Execute Pilot Query
                 Run Rate Optimizer
```

### Layer 1: Exact SQL Cache (Metadata & Statistics Caching)
- **Key**: Exact rewritten pilot SQL string (or original user query string + parameters).
- **Value**: The computed pilot statistics (`y1`, `y2_pairs`, `N1`, `N2`, `mean`, `variance`, etc.) and the query block size.
- **Action**: When a query hits this cache, the pilot query execution is bypassed. The engine feeds the cached statistics directly into the Rate Optimizer solver.
- **Use Case**: Safe for queries with identical predicates where we still want the optimizer to solve for rates dynamically based on the target error budget.

### Layer 2: Template Caching (Sampling Plan Caching)
- **Key**: Normalized query AST template (structural signature).
- **Value**: The solved sampling rates (e.g., `{'lineitem': 0.057, 'part': 0.01}`).
- **Normalization Strategy**: Using `sqlglot`, all literal constants (numbers, dates, strings) are replaced with placeholders (e.g., `?` or generic literals).
  - Example: `WHERE l_shipdate >= '1995-09-01'` and `WHERE l_shipdate >= '1996-01-01'` map to the same template.
- **Action**: Bypasses both the pilot query and the solver. It executes the final sampling query immediately using the cached rates.
- **Use Case**: Maximizes speedup for templated BI workloads where parameters change slightly but data distribution and optimal sample rates remain stable.

---

## 3. Persistent Cache Schema & Storage

The cache is managed by a centralized manager class `PilotCacheManager`. By default, it is backed by an in-memory dictionary for short-lived workloads, with optional persistence to a local SQLite database (`.pilotdb_cache.db`) or JSON file for production workloads.

### Persisted Schema (SQLite backend)

```sql
CREATE TABLE IF NOT EXISTS exact_cache (
    query_hash TEXT PRIMARY KEY,
    pilot_sql TEXT NOT NULL,
    pilot_stats TEXT NOT NULL,  -- JSON string containing pilot statistics
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS template_cache (
    template_hash TEXT PRIMARY KEY,
    query_template TEXT NOT NULL,
    solved_rates TEXT NOT NULL,  -- JSON string containing table rates (e.g., {"lineitem": 0.057})
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 4. Cache Policy & Validation

To prevent stale cache plans from violating the A priori error guarantees, the following cache policies are enforced:

1. **TTL (Time To Live)**: Cache entries are invalidated after a configurable duration (default: 24 hours) or when database schema changes are detected.
2. **DBMS Verification**: Since sample rates depend on table size and distribution, cache entries are segmented by the target DBMS connection string or database name.
3. **Forced Refresh Flag**: Users can bypass the cache by passing a `force_refresh=True` parameter to `execute_aqp()`.
