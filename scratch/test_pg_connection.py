import psycopg2
import yaml
import os

def check_postgres():
    config_path = "db_configs/postgres_local.yml"
    if not os.path.exists(config_path):
        print(f"Error: {config_path} not found")
        return

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    print("Attempting to connect to PostgreSQL with config:")
    print(f"Host: {config.get('host')}, Port: {config.get('port')}, DBName: {config.get('dbname')}, User: {config.get('username')}")

    try:
        conn = psycopg2.connect(
            host=config.get('host'),
            port=config.get('port'),
            dbname=config.get('dbname'),
            user=config.get('username'),
            password=config.get('password')
        )
        print("Connection successful!")
    except Exception as e:
        print("Failed to connect to PostgreSQL:", e)
        return

    try:
        with conn.cursor() as cur:
            tables = ["lineitem", "orders", "customer", "part", "supplier", "nation", "region", "partsupp"]
            print("\nTable Row Counts:")
            print("-" * 30)
            for t in tables:
                try:
                    cur.execute(f"SELECT count(*) FROM {t}")
                    count = cur.fetchone()[0]
                    print(f"{t:10} : {count:,} rows")
                except Exception as ex:
                    print(f"{t:10} : Table not found or error ({ex})")
                    conn.rollback()
    except Exception as e:
        print("Error querying tables:", e)
    finally:
        conn.close()

if __name__ == "__main__":
    check_postgres()
