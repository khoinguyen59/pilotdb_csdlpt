# Distributed PostgreSQL (Citus) Evaluation Guide

This document describes the architectural implications, setup instructions, and validation strategy for evaluating PilotDB on a distributed PostgreSQL cluster (specifically Citus).

---

## 1. Architectural Implications of Data Sharding on Block Sampling

PilotDB relies on **block sampling** (via `TABLESAMPLE SYSTEM (p PERCENT)`) to achieve physical-level I/O savings. In a distributed DBMS like Citus:
1. **Data Sharding**: Tables are horizontally partitioned into shards across multiple worker nodes.
2. **Distributed Query Planner**: A coordinator node receives the query, distributes execution to workers, and aggregates results.
3. **Block Structure**: Since block sampling occurs at the storage layer of individual worker nodes, running `TABLESAMPLE SYSTEM` on a distributed table is executed concurrently across worker shards.

### Citus Shard Sampling Equivalence
Citus supports running `TABLESAMPLE` on distributed tables by pushing the sampling clause down to the local worker shard tables.
- **Theorem (Distributed Block Sampling Equivalence)**: Under uniform shard distribution, a block sample of rate $p$ across all worker shards is statistically equivalent to a block sample of rate $p$ on a single node containing the merged dataset.
- **Safety check**: If shard keys are highly skewed, the variance bounds computed by PilotDB (Lemma 4.8) may require adjustment due to inter-shard variance.

---

## 2. Distributed Cluster Setup (Citus Docker Compose)

To evaluate PilotDB in a distributed environment, we configure a Citus cluster with 1 Coordinator node and 2 Worker nodes using Docker Compose.

### `docker-compose.yml` Configuration

```yaml
version: '3.8'

services:
  coordinator:
    image: citusdata/citus:12.1
    ports:
      - "5432:5432"
    environment:
      POSTGRES_USER: pilotdb
      POSTGRES_PASSWORD: PilotDB123
      POSTGRES_DB: tpch_distributed
    volumes:
      - coordinator_data:/var/lib/postgresql/data

  worker1:
    image: citusdata/citus:12.1
    environment:
      POSTGRES_USER: pilotdb
      POSTGRES_PASSWORD: PilotDB123
      POSTGRES_DB: tpch_distributed
    depends_on:
      - coordinator

  worker2:
    image: citusdata/citus:12.1
    environment:
      POSTGRES_USER: pilotdb
      POSTGRES_PASSWORD: PilotDB123
      POSTGRES_DB: tpch_distributed
    depends_on:
      - coordinator

volumes:
  coordinator_data:
```

### Initializing the Citus Cluster
Once the containers are running, execute the following SQL on the coordinator node to register worker nodes and distribute TPC-H tables:

```sql
-- Register worker nodes
SELECT * FROM citus_add_node('worker1', 5432);
SELECT * FROM citus_add_node('worker2', 5432);

-- Distribute large tables (e.g., lineitem sharded by l_orderkey)
SELECT create_distributed_table('lineitem', 'l_orderkey');
SELECT create_distributed_table('orders', 'o_orderkey');
SELECT create_distributed_table('customer', 'c_custkey');
```

---

## 3. Validation and Benchmarking Strategy

When executing PilotDB AQP queries against Citus:
1. **Pilot Run**: The pilot query is pushed down to all workers, returning a 1% block sample from each shard.
2. **Variance Calculation**: The coordinator aggregates pilot statistics. The variance bounds (Lemma 4.8) are computed exactly as in the single-node case.
3. **Execution**: The final optimized sampling rates are applied, and workers execute the sampling query concurrently.
4. **Performance Evaluation**:
   - Compare coordinator network traffic during exact vs AQP executions.
   - Verify that network transfer times are reduced proportionally to the sample rate.
