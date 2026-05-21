import re

import sqlglot
from sqlglot import exp

from pilotdb.pilot_engine.commons import *
from pilotdb.pilot_engine.multi_table_sampling import sampling_placeholder


def _get_from(expression):
    """Compatibility helper: sqlglot >=26 renamed 'from' → 'from_'."""
    return expression.args.get("from_") or expression.args.get("from")


def _get_with(expression):
    return expression.args.get("with_") or expression.args.get("with")


def _set_with(expression, value):
    if "with_" in expression.arg_types:
        expression.set("with_", value)
    else:
        expression.set("with", value)


class Sampling_Rewriter:
    def __init__(self, table_cols, table_size, database):
        self.table_cols = table_cols
        self.table_size = table_size
        self.database = database

        self.subquery_count = 0
        self.alias = {}
        self.table_alias = {}
        self.cte = {}
        self.largest_table = None
        self.sampled_tables = []
        self.single_sample = False
        self.sampled_cte = set()
        self.aggregator_mapping = {}
        self.subquery_dict = {}

    def find_alias(self, expression):
        alias_list = expression.find_all(exp.Alias, bfs=False)
        for alias in alias_list:
            self.alias[alias.alias] = alias.this
        for table in expression.find_all(exp.Table):
            if table.alias:
                self.table_alias[table.alias] = table
        for alia, value in self.alias.items():
            if isinstance(value, exp.Column) and value.this.this in self.alias:
                self.alias[alia] = self.alias[value.this.this]

        return None

    def extract_cte(self, expression):
        cte_list = expression.find_all(exp.CTE)
        for cte in cte_list:
            self.cte[cte.alias] = cte.this

    def find_all_aggregator(self, expression):
        for agg in expression.find_all(exp.AggFunc):
            self.aggregator_mapping[agg.parent.alias] = agg
            table_set = set()
            for column in agg.find_all(exp.Column):
                table_set.add(column.table)
            if len(table_set) > 1:
                self.single_sample = True

    def find_all_tables(self, expression):
        table_list = []
        for table in _get_from(expression).find_all(exp.Table):
            table_list.append(table)
        if "joins" in expression.args:
            for join in expression.args["joins"]:
                table_list.append(join.find(exp.Table))
        return table_list

    # Paper §3.2: tables below this size are treated as small enough to
    # full-scan; we only sample tables with cardinality >= this threshold.
    LARGE_TABLE_SIZE_THRESHOLD = 100_000

    def add_table_sample(self, expression):
        """Wrap large tables in this scope with TABLESAMPLE markers.

        Each wrapped table gets a unique marker rate (1, 2, 3, …) so
        `replace_sample_method` can map each rendered TABLESAMPLE back to
        its per-table placeholder. This enables the final query to
        realise the full vector plan §3.2 produces rather than collapsing
        every candidate plan to a single TABLESAMPLE on `largest_table`.

        Backward-compatible: when exactly one large table is in scope,
        the resulting SQL is identical to the legacy single-table path.
        """
        # Collect tables in THIS scope's FROM + JOIN only — subqueries
        # are handled recursively by `primary_query_rewriter`.
        candidate_tables: list[exp.Table] = []
        from_node = _get_from(expression)
        if from_node is not None:
            for table in from_node.find_all(exp.Table):
                if isinstance(table.parent, exp.TableSample):
                    continue
                candidate_tables.append(table)
        if "joins" in expression.args:
            for join in expression.args["joins"]:
                for table in join.find_all(exp.Table):
                    if isinstance(table.parent, exp.TableSample):
                        continue
                    candidate_tables.append(table)

        if not candidate_tables:
            return False

        # Filter to large tables — fall back to the legacy "always pick
        # the largest table mentioned" behaviour when nothing qualifies,
        # so single-table queries on small synthetic schemas still get
        # rewritten (matching existing tests).
        sampleable: list[tuple[exp.Table, str]] = []
        for table in candidate_tables:
            name = table.this.this
            if (
                name in self.table_size
                and self.table_size[name] >= self.LARGE_TABLE_SIZE_THRESHOLD
            ):
                sampleable.append((table, name))

        if not sampleable:
            # Legacy single-table fallback: wrap whichever table the
            # original heuristic picked (matches the previous behaviour).
            table_names = [t.this.this for t in candidate_tables]
            self.largest_table = table_names[0]
            for name in self.table_size:
                if name in table_names:
                    self.largest_table = name
                    break
            for table in candidate_tables:
                if table.this.this == self.largest_table:
                    if self.database == DUCKDB:
                        alias_name = table.alias_or_name
                        sub = sqlglot.parse_one("SELECT *, rowid FROM x TABLESAMPLE SYSTEM (1)")
                        from_clause = sub.args.get("from_") or sub.args.get("from")
                        from_clause.this.this.replace(table.this.copy())
                        sub_node = sub.subquery(alias_name)
                        table.replace(sub_node)
                    else:
                        ts = _get_from(
                            sqlglot.parse_one("from x TABLESAMPLE SYSTEM (1)")
                        ).this
                        ts.set("this", table.copy())
                        table.replace(ts)
                    return True
            return False

        # Multi-table path: largest_table is the biggest of the sampleable
        # set, but we wrap *every* large table with a distinct marker.
        sampleable.sort(key=lambda pair: -self.table_size[pair[1]])
        # Limit to top 2 tables to comply with Lemma 4.8 (2-way join variance decomposition)
        sampleable = sampleable[:2]

        self.largest_table = sampleable[0][1]
        self.sampled_tables = []
        for idx, (table_node, table_name) in enumerate(sampleable):
            marker_value = idx + 1
            self.sampled_tables.append((table_name, marker_value))
            if self.database == DUCKDB:
                alias_name = table_node.alias_or_name
                sub = sqlglot.parse_one(f"SELECT *, rowid FROM x TABLESAMPLE SYSTEM ({marker_value})")
                from_clause = sub.args.get("from_") or sub.args.get("from")
                from_clause.this.this.replace(table_node.this.copy())
                sub_node = sub.subquery(alias_name)
                table_node.replace(sub_node)
            else:
                ts = _get_from(
                    sqlglot.parse_one(
                        f"from x TABLESAMPLE SYSTEM ({marker_value})"
                    )
                ).this
                ts.set("this", table_node.copy())
                table_node.replace(ts)
        return True

    def extract_items(self, expression, type):
        extracted_items = []
        for item in expression.find_all(type):
            extracted_items.append(item)
        return extracted_items

    def subquery_in_where(self, expression, column_information):
        if "where" in expression.args:
            subquery_list = expression.args["where"].find_all(exp.Select)
            for subquery in subquery_list:
                tables_in_from = self.find_all_tables(subquery)

                column_list = []
                subquery_2_name = {y: x for x, y in self.subquery_dict.items()}
                for table in tables_in_from:
                    if table.this.this in column_information:
                        column_list += column_information[table.this.this]
                    else:
                        if table.this.this in self.cte:
                            new_cte_expression = []
                            new_cte_expression.append(self.cte[table.this.this])
                            for table_in_cte in self.cte[table.this.this].find_all(
                                exp.Table
                            ):
                                if table_in_cte.this.this in self.cte:
                                    new_cte_expression.insert(
                                        0, self.cte[table_in_cte.this.this]
                                    )
                            new_cte = exp.With()
                            new_cte.set("expressions", new_cte_expression)
                            new_subquery = subquery.copy()
                            _set_with(new_subquery, new_cte)
                            if new_subquery.sql() in subquery_2_name:
                                subquery_exp = sqlglot.parse_one(
                                    subquery_2_name[new_subquery.sql()]
                                )
                                subquery.parent.replace(subquery_exp)
                            else:
                                subquery_str = f"subquery_{self.subquery_count}"
                                self.subquery_count += 1
                                self.subquery_dict[subquery_str] = new_subquery.sql()
                                subquery_exp = sqlglot.parse_one(subquery_str)
                                subquery.parent.replace(subquery_exp)

                tables_in_subquery = self.extract_items(subquery, exp.Table)
                columns_in_subquery = self.extract_items(subquery, exp.Column)

                is_separable = True
                for table in tables_in_subquery:
                    if table not in tables_in_from:
                        is_separable = False
                for column in columns_in_subquery:
                    if column.this.this not in column_list:
                        is_separable = False
                    if column.table:
                        if self.table_alias[column.table] not in tables_in_from:
                            is_separable = False
                if is_separable:
                    subquery_str = f"subquery_{self.subquery_count}"
                    self.subquery_count += 1
                    self.subquery_dict[subquery_str] = subquery.sql()
                    subquery_exp = sqlglot.parse_one(subquery_str)
                    subquery.parent.replace(subquery_exp)

        return expression

    def add_sample_rate(self, expression):
        new_select_expression_list = []
        for select_expression in expression.args["expressions"]:
            if select_expression.find(exp.Div):
                div_operator = select_expression.find(exp.Div)
                if div_operator.this.find(exp.AggFunc) and div_operator.expression.find(
                    exp.AggFunc
                ):
                    new_select_expression_list.append(select_expression)
                    continue
            if (
                select_expression.find(exp.Sum)
                or select_expression.find(exp.Count)
                or (
                    select_expression.find(exp.Anonymous)
                    and select_expression.find(exp.Anonymous).this.upper()
                    == "COUNT_BIG"
                )
            ):
                agg_expression = select_expression.find(exp.AggFunc)
                if not agg_expression:
                    agg_expression = select_expression.find(exp.Anonymous)
                col = agg_expression.find(exp.Column)
                if col and col.this.this in self.alias:
                    original_agg_expression = self.alias[col.this.this]
                    if original_agg_expression.find(
                        exp.Sum
                    ) or original_agg_expression.find(exp.Count):
                        new_select_expression_list.append(select_expression)
                        continue
                agg_expression_parent = agg_expression.parent
                new_div_expression = exp.Div(
                    this=agg_expression,
                    expression="{sample_rate}",
                )
                if isinstance(agg_expression_parent, exp.Select):
                    new_select_expression_list.append(new_div_expression)
                else:
                    agg_expression_parent.set("this", new_div_expression)
                    new_select_expression_list.append(select_expression)
            else:
                new_select_expression_list.append(select_expression)
        expression.set("expressions", new_select_expression_list)

    def subquery_in_from(self, expression, is_union=False, is_join=False):
        self.subquery_in_where(expression, self.table_cols)
        if self.add_table_sample(expression):
            self.add_sample_rate(expression)

        return expression

    def primary_query_rewriter(
        self, expression, is_union=False, level=0, is_join=False
    ):
        if expression.find(exp.Union):
            is_union = True
        from_node = _get_from(expression)
        if from_node.find(exp.Subquery):
            if from_node.find(exp.Union):
                for select_query in from_node.find_all(
                    exp.Select, bfs=False
                ):
                    is_in_where = False
                    node = select_query
                    while node:
                        if isinstance(node, exp.Where):
                            is_in_where = True
                            break
                        node = node.parent
                    if not is_in_where:
                        self.primary_query_rewriter(select_query, is_union, level + 1)
            else:
                select_query = from_node.find(exp.Select)
                self.primary_query_rewriter(select_query, is_union, level + 1)
            if "joins" in expression.args:
                for join_expression in expression.args["joins"]:
                    if join_expression.find(exp.Select):
                        self.primary_query_rewriter(
                            join_expression.find(exp.Select), is_union, level + 1
                        )
            self.add_sample_rate(expression)

        elif self.cte and _get_from(expression).this.this.this in self.cte:
            from_table_name = _get_from(expression).this.this.this
            cte_expression = self.cte[from_table_name]
            if from_table_name not in self.sampled_cte:
                self.primary_query_rewriter(cte_expression, is_union, level + 1)
                self.sampled_cte.add(from_table_name)

            if not self.single_sample:
                if "joins" in expression.args:
                    for join_expression in expression.args["joins"]:
                        if join_expression.this.this.this in self.cte:
                            cte_expression = self.cte[join_expression.this.this.this]
                            if join_expression.this.this.this not in self.sampled_cte:
                                self.primary_query_rewriter(
                                    cte_expression, is_union, level + 1, True
                                )
                                self.sampled_cte.add(join_expression.this.this.this)

            self.add_sample_rate(expression)
        else:
            self.subquery_in_from(expression, is_union, is_join)

        return expression

    def remove_cte(self, expression):
        if expression.find(exp.With):
            cte_alias_list = []
            new_cte_expression_list = set()
            for table in _get_from(expression).find_all(exp.Table):
                if table.this.this in self.cte:
                    cte_alias_list.append(table.this.this)
                    new_cte_expression_list.add(self.cte[table.this.this].parent)

            if "joins" in expression.args:
                for join_expression in expression.args["joins"]:
                    for table in join_expression.find_all(exp.Table):
                        if table.this.this in self.cte:
                            cte_alias_list.append(table.this.this)
                            new_cte_expression_list.add(
                                self.cte[table.this.this].parent
                            )

            for cte_expression in new_cte_expression_list:
                for cte_table in cte_expression.find_all(exp.Table):
                    if cte_table.this.this in self.cte:
                        cte_alias_list.append(cte_table.this.this)
            new_ctes = []
            expr_with = _get_with(expression)
            if expr_with:
                for old_cte in expr_with.expressions:
                    if old_cte.alias in cte_alias_list:
                        new_ctes.append(old_cte)
            if new_ctes:
                new_with_expression = exp.With(expressions=new_ctes)
                _set_with(expression, new_with_expression)
            else:
                _set_with(expression, None)

    def replace_sample_method(self, sql_query):
        """Map per-table TABLESAMPLE markers back to per-table placeholders.

        Multi-table path: `add_table_sample` placed
        ``TABLESAMPLE SYSTEM (N ROWS)`` for each large table with N as a
        unique marker. We rewrite each to its `{sampling_method_<table>}`
        placeholder so `apply_sampling_plan_template` can fill them with
        the actual per-table rate strings from the chosen plan.

        Legacy path: when no multi-table markers were recorded we fall
        back to the single-table rewrite (a single TABLESAMPLE SYSTEM
        ``(1 ROWS)`` mapped to the largest-table placeholder).
        """
        if self.sampled_tables:
            for table_name, marker_value in self.sampled_tables:
                marker_sql = f"TABLESAMPLE SYSTEM ({marker_value} ROWS)"
                sql_query = sql_query.replace(
                    marker_sql, sampling_placeholder(table_name)
                )
            return sql_query
        placeholder = "{sampling_method}"
        if self.largest_table:
            placeholder = sampling_placeholder(self.largest_table)
        return sql_query.replace("TABLESAMPLE SYSTEM (1 ROWS)", placeholder)

    def modify_having(self, expression):
        for having_expression in expression.find_all(exp.Having):
            if not having_expression.parent.find(exp.TableSample):
                continue
            if having_expression.find(exp.Sum) or having_expression.find(exp.Count):
                agg_expression = having_expression.find(exp.AggFunc)
                col = agg_expression.find(exp.Column)
                if col and col.this.this in self.alias:
                    original_agg_expression = self.alias[col.this.this]
                    if original_agg_expression.find(
                        exp.Sum
                    ) or original_agg_expression.find(exp.Count):
                        continue
                agg_expression_parent = agg_expression.parent
                new_div_expression = exp.Div(
                    this=agg_expression, expression="{sample_rate}"
                )
                agg_expression_parent.set("this", new_div_expression)

    def rewrite(self, original_query):
        include_limit = False
        if self.database == SQLSERVER:
            match = re.search(r"TOP (\d+)", original_query, re.IGNORECASE)
            if match:
                limit_value = int(match.group(1))  # The number x to be retrieved
                include_limit = True
                original_query = re.sub(
                    r"TOP \d+", "", original_query, flags=re.IGNORECASE
                )

        expression = sqlglot.parse_one(original_query)
        self.find_alias(expression)
        self.extract_cte(expression)
        self.find_all_aggregator(expression)

        expression = self.primary_query_rewriter(expression)

        self.remove_cte(expression)
        self.modify_having(expression)
        modified_query = expression.sql()
        new_query = self.replace_sample_method(modified_query)

        if self.database == POSTGRES:
            pattern = r"\b(INTERVAL) '(\d+)' (DAYS)\b"
            new_query = re.sub(pattern, r"\1 '\2 \3'", new_query)

        if include_limit:
            new_query = f"SELECT TOP {limit_value} " + new_query[6:]
        return new_query
