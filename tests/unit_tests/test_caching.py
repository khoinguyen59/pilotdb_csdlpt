import os
import time
import pandas as pd
import pytest
import duckdb
from pilotdb.pilot_engine.caching import PilotCacheManager
from pilotdb.execute import execute_aqp
from pilotdb.query import Query

@pytest.fixture
def temp_cache_db():
    db_path = "test_temp_cache.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    manager = PilotCacheManager(cache_db_path=db_path, ttl_seconds=10)
    yield manager
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except Exception:
            pass

def test_normalization(temp_cache_db):
    manager = temp_cache_db
    sql1 = "SELECT * FROM lineitem WHERE l_shipdate >= '1995-09-01' AND l_quantity > 10"
    sql2 = "SELECT * FROM lineitem WHERE l_shipdate >= '1996-02-28' AND l_quantity > 25"
    
    norm1 = manager.normalize_query(sql1, "duckdb")
    norm2 = manager.normalize_query(sql2, "duckdb")
    
    assert norm1 == norm2
    assert "1995-09-01" not in norm1
    assert "10" not in norm1
    assert "?" in norm1

def test_layer1_exact_cache(temp_cache_db):
    manager = temp_cache_db
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    sql = "SELECT * FROM t WHERE id = 1"
    
    # Cache miss
    assert manager.get_exact_cache(sql) is None
    
    # Store
    manager.set_exact_cache(sql, df)
    
    # Cache hit
    cached_df = manager.get_exact_cache(sql)
    assert cached_df is not None
    pd.testing.assert_frame_equal(cached_df, df)

def test_layer1_exact_cache_ttl(temp_cache_db):
    # Set a tiny TTL for testing
    manager = PilotCacheManager(cache_db_path="test_temp_ttl.db", ttl_seconds=1)
    try:
        df = pd.DataFrame({"a": [1]})
        sql = "SELECT * FROM t"
        
        manager.set_exact_cache(sql, df)
        assert manager.get_exact_cache(sql) is not None
        
        time.sleep(1.5)
        # Should expire
        assert manager.get_exact_cache(sql) is None
    finally:
        if os.path.exists("test_temp_ttl.db"):
            os.remove("test_temp_ttl.db")

def test_layer2_template_cache(temp_cache_db):
    manager = temp_cache_db
    sql = "SELECT * FROM lineitem WHERE l_quantity > 10"
    rates = {"lineitem": 0.05}
    
    # Cache miss
    assert manager.get_template_cache(sql, "duckdb") is None
    
    # Store
    manager.set_template_cache(sql, "duckdb", rates)
    
    # Hit for same query
    assert manager.get_template_cache(sql, "duckdb") == rates
    
    # Hit for query with different parameter value
    sql_diff = "SELECT * FROM lineitem WHERE l_quantity > 25"
    assert manager.get_template_cache(sql_diff, "duckdb") == rates

def test_clear_cache(temp_cache_db):
    manager = temp_cache_db
    df = pd.DataFrame({"a": [1]})
    sql = "SELECT * FROM t"
    
    manager.set_exact_cache(sql, df)
    manager.set_template_cache(sql, "duckdb", {"t": 0.05})
    
    manager.clear()
    
    assert manager.get_exact_cache(sql) is None
    assert manager.get_template_cache(sql, "duckdb") is None

def test_execute_aqp_integration():
    # Set up a small local duckdb table
    db_file = "test_aqp_integration.db"
    if os.path.exists(db_file):
        os.remove(db_file)
        
    conn = duckdb.connect(db_file)
    conn.execute("CREATE TABLE lineitem (l_quantity DOUBLE, l_extendedprice DOUBLE, l_shipdate VARCHAR)")
    # Insert some dummy rows
    for i in range(100):
        conn.execute(f"INSERT INTO lineitem VALUES ({i}, {10.0 * i}, '1995-09-01')")
    conn.close()

    db_config = {
        "dbms": "duckdb",
        "database": db_file,
        "path": db_file,
    }
    
    # Clear general caching database to start fresh
    from pilotdb.execute import cache_manager
    cache_manager.clear()
    
    query = Query(
        name="test_integration_q",
        query="SELECT SUM(l_extendedprice) FROM lineitem WHERE l_quantity > 10",
        error=0.5,
        failure_probability=0.05,
        table_cols={"lineitem": ["l_quantity", "l_extendedprice", "l_shipdate"]},
        table_size={"lineitem": 100},
    )
    
    # First execution: miss
    res1, timing1 = execute_aqp(query, db_config, pilot_sample_rate=10.0, use_cache=True, force_refresh=True)
    assert timing1["fallback_reason"] != "cache_hit_template"
    
    # Second execution: hit
    res2, timing2 = execute_aqp(query, db_config, pilot_sample_rate=10.0, use_cache=True, force_refresh=False)
    assert timing2["fallback_reason"] == "cache_hit_template"
    
    # Third execution with force_refresh: miss
    res3, timing3 = execute_aqp(query, db_config, pilot_sample_rate=10.0, use_cache=True, force_refresh=True)
    assert timing3["fallback_reason"] != "cache_hit_template"

    # Cleanup
    if os.path.exists(db_file):
        os.remove(db_file)
