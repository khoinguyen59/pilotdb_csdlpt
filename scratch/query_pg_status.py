import psycopg2

def main():
    try:
        conn = psycopg2.connect("postgresql://pilotdb:pilotdb@localhost:5432/tpch")
        cur = conn.cursor()
        cur.execute("""
            SELECT pid, state, query, wait_event_type, wait_event, 
                   query_start, now() - query_start AS duration 
            FROM pg_stat_activity 
            WHERE state IS NOT NULL AND query NOT LIKE '%pg_stat_activity%'
            ORDER BY duration DESC;
        """)
        rows = cur.fetchall()
        print(f"{'PID':<6} | {'STATE':<10} | {'DURATION':<12} | {'WAIT EVENT':<20} | QUERY")
        print("-" * 100)
        for row in rows:
            pid, state, query, wait_event_type, wait_event, _, duration = row
            wait_str = f"{wait_event_type or ''}:{wait_event or ''}"
            # Truncate query for display
            query_trunc = query.strip().replace("\n", " ")[:60]
            print(f"{pid:<6} | {state:<10} | {str(duration):<12} | {wait_str:<20} | {query_trunc}")
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    main()
