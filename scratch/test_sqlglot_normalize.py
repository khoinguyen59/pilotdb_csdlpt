import sqlglot
from sqlglot import exp

def normalize_query(sql, dialect):
    parsed = sqlglot.parse_one(sql, read=dialect)
    # Find all literals and replace them
    for literal in parsed.find_all(exp.Literal):
        literal.replace(exp.Literal.string("?"))
    return parsed.sql(dialect)

sql1 = "SELECT l_shipdate, sum(l_extendedprice) FROM lineitem WHERE l_shipdate >= '1995-09-01' GROUP BY l_shipdate"
sql2 = "SELECT l_shipdate, sum(l_extendedprice) FROM lineitem WHERE l_shipdate >= '1996-01-01' GROUP BY l_shipdate"

print("SQL 1 Normalized:", normalize_query(sql1, "duckdb"))
print("SQL 2 Normalized:", normalize_query(sql2, "duckdb"))
