-- TPC-H schema for SQL Server.
-- TPC-H specification v3.0.1, §1.4.
-- DROP order respects FK references; ignore "does not exist" errors on first run.
IF OBJECT_ID('dbo.lineitem', 'U') IS NOT NULL DROP TABLE dbo.lineitem;
IF OBJECT_ID('dbo.orders',   'U') IS NOT NULL DROP TABLE dbo.orders;
IF OBJECT_ID('dbo.partsupp', 'U') IS NOT NULL DROP TABLE dbo.partsupp;
IF OBJECT_ID('dbo.customer', 'U') IS NOT NULL DROP TABLE dbo.customer;
IF OBJECT_ID('dbo.supplier', 'U') IS NOT NULL DROP TABLE dbo.supplier;
IF OBJECT_ID('dbo.part',     'U') IS NOT NULL DROP TABLE dbo.part;
IF OBJECT_ID('dbo.nation',   'U') IS NOT NULL DROP TABLE dbo.nation;
IF OBJECT_ID('dbo.region',   'U') IS NOT NULL DROP TABLE dbo.region;

CREATE TABLE dbo.region (
    r_regionkey  INT          NOT NULL PRIMARY KEY,
    r_name       CHAR(25)     NOT NULL,
    r_comment    VARCHAR(152) NULL
);

CREATE TABLE dbo.nation (
    n_nationkey  INT          NOT NULL PRIMARY KEY,
    n_name       CHAR(25)     NOT NULL,
    n_regionkey  INT          NOT NULL REFERENCES dbo.region(r_regionkey),
    n_comment    VARCHAR(152) NULL
);

CREATE TABLE dbo.supplier (
    s_suppkey    INT          NOT NULL PRIMARY KEY,
    s_name       CHAR(25)     NOT NULL,
    s_address    VARCHAR(40)  NOT NULL,
    s_nationkey  INT          NOT NULL REFERENCES dbo.nation(n_nationkey),
    s_phone      CHAR(15)     NOT NULL,
    s_acctbal    DECIMAL(15,2) NOT NULL,
    s_comment    VARCHAR(101) NOT NULL
);

CREATE TABLE dbo.customer (
    c_custkey    INT          NOT NULL PRIMARY KEY,
    c_name       VARCHAR(25)  NOT NULL,
    c_address    VARCHAR(40)  NOT NULL,
    c_nationkey  INT          NOT NULL REFERENCES dbo.nation(n_nationkey),
    c_phone      CHAR(15)     NOT NULL,
    c_acctbal    DECIMAL(15,2) NOT NULL,
    c_mktsegment CHAR(10)     NOT NULL,
    c_comment    VARCHAR(117) NOT NULL
);

CREATE TABLE dbo.part (
    p_partkey     INT          NOT NULL PRIMARY KEY,
    p_name        VARCHAR(55)  NOT NULL,
    p_mfgr        CHAR(25)     NOT NULL,
    p_brand       CHAR(10)     NOT NULL,
    p_type        VARCHAR(25)  NOT NULL,
    p_size        INT          NOT NULL,
    p_container   CHAR(10)     NOT NULL,
    p_retailprice DECIMAL(15,2) NOT NULL,
    p_comment     VARCHAR(23)  NOT NULL
);

CREATE TABLE dbo.partsupp (
    ps_partkey    INT          NOT NULL REFERENCES dbo.part(p_partkey),
    ps_suppkey    INT          NOT NULL REFERENCES dbo.supplier(s_suppkey),
    ps_availqty   INT          NOT NULL,
    ps_supplycost DECIMAL(15,2) NOT NULL,
    ps_comment    VARCHAR(199) NOT NULL,
    PRIMARY KEY (ps_partkey, ps_suppkey)
);

CREATE TABLE dbo.orders (
    o_orderkey      INT          NOT NULL PRIMARY KEY,
    o_custkey       INT          NOT NULL REFERENCES dbo.customer(c_custkey),
    o_orderstatus   CHAR(1)      NOT NULL,
    o_totalprice    DECIMAL(15,2) NOT NULL,
    o_orderdate     DATE         NOT NULL,
    o_orderpriority CHAR(15)     NOT NULL,
    o_clerk         CHAR(15)     NOT NULL,
    o_shippriority  INT          NOT NULL,
    o_comment       VARCHAR(79)  NOT NULL
);

CREATE TABLE dbo.lineitem (
    l_orderkey      INT          NOT NULL REFERENCES dbo.orders(o_orderkey),
    l_partkey       INT          NOT NULL REFERENCES dbo.part(p_partkey),
    l_suppkey       INT          NOT NULL REFERENCES dbo.supplier(s_suppkey),
    l_linenumber    INT          NOT NULL,
    l_quantity      DECIMAL(15,2) NOT NULL,
    l_extendedprice DECIMAL(15,2) NOT NULL,
    l_discount      DECIMAL(15,2) NOT NULL,
    l_tax           DECIMAL(15,2) NOT NULL,
    l_returnflag    CHAR(1)      NOT NULL,
    l_linestatus    CHAR(1)      NOT NULL,
    l_shipdate      DATE         NOT NULL,
    l_commitdate    DATE         NOT NULL,
    l_receiptdate   DATE         NOT NULL,
    l_shipinstruct  CHAR(25)     NOT NULL,
    l_shipmode      CHAR(10)     NOT NULL,
    l_comment       VARCHAR(44)  NOT NULL,
    PRIMARY KEY (l_orderkey, l_linenumber)
);
