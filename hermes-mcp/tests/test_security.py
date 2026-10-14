from hermes_mcp.errors import ReadOnlyViolation
from hermes_mcp.security import validate_readonly, is_readonly


def ok(sql):
    return is_readonly(sql)[0]


def tests_valid_read_only():
    assert ok("SELECT * FROM orders")
    assert ok("select id, name from customers where country = 'CN'")
    assert ok("SELECT c.name, SUM(o.quantity) FROM orders o JOIN customers c ON c.id=o.customer_id GROUP BY c.name")
    assert ok("WITH t AS (SELECT * FROM orders) SELECT * FROM t")
    assert ok("EXPLAIN SELECT * FROM orders")
    assert ok("SHOW TABLES")
    assert ok("DESCRIBE orders")


def test_rejects_write_keywords():
    for sql in [
        "INSERT INTO orders VALUES (1,1,1,1,'x')",
        "UPDATE customers SET name='x'",
        "DELETE FROM orders WHERE id=1",
        "DROP TABLE orders",
        "ALTER TABLE orders ADD COLUMN x INT",
        "CREATE TABLE t (i INT)",
        "TRUNCATE TABLE orders",
        "GRANT ALL ON orders TO me",
    ]:
        assert not ok(sql), sql


def test_rejects_multiple_statements():
    assert not ok("SELECT * FROM orders; SELECT * FROM customers")
    assert not ok("SELECT 1; DROP TABLE orders")


def test_rejects_dangerous_patterns():
    assert not ok("SELECT * INTO OUTFILE '/tmp/x' FROM orders t")
    assert not ok("SELECT LOAD_FILE('/etc/passwd')")
    assert not ok("SELECT @@version")


def test_rejects_unsafe_first_keyword():
    assert not ok("   ")
    assert not ok("DELETE FROM orders")
    assert not ok("SHOWME")


def test_with_without_select_rejected():
    assert not ok("WITH t AS (SELECT * FROM orders) DELETE... y")
    assert ok("WITH t AS (SELECT * FROM orders) SELECT * FROM t")


def test_returns_normalized_single_statement():
    assert validate_readonly("  select * from orders  ").lower().startswith("select")