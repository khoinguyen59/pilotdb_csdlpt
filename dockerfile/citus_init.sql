-- Register worker nodes
SELECT * FROM citus_add_node('worker1', 5432);
SELECT * FROM citus_add_node('worker2', 5432);

-- Distribute large tables (sharded across workers)
SELECT create_distributed_table('orders', 'o_orderkey');
SELECT create_distributed_table('lineitem', 'l_orderkey', colocate_with := 'orders');
SELECT create_distributed_table('customer', 'c_custkey');

-- Create reference tables (fully replicated on all workers for local joins)
SELECT create_reference_table('part');
SELECT create_reference_table('supplier');
SELECT create_reference_table('partsupp');
SELECT create_reference_table('nation');
SELECT create_reference_table('region');
