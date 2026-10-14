-- hermes-mcp 联调测试库初始化
-- 建库 + 三张有业务含义的表 + 样例数据 + 只读账号

CREATE DATABASE IF NOT EXISTS hermes_test
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;

USE hermes_test;

DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS customers;

CREATE TABLE customers (
  id         INT PRIMARY KEY,
  name       VARCHAR(50) NOT NULL,
  city       VARCHAR(50),
  level      VARCHAR(10) DEFAULT '普通',       -- 普通 / 银卡 / 金卡
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE products (
  id       INT PRIMARY KEY,
  name     VARCHAR(80) NOT NULL,
  category VARCHAR(30),
  price    DECIMAL(10,2),
  stock    INT DEFAULT 0
);

CREATE TABLE orders (
  id          INT PRIMARY KEY,
  customer_id INT NOT NULL,
  product_id  INT NOT NULL,
  quantity    INT NOT NULL,
  status      VARCHAR(10) DEFAULT '已支付',     -- 待支付 / 已支付 / 已取消
  order_date  DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (customer_id) REFERENCES customers(id),
  FOREIGN KEY (product_id)  REFERENCES products(id)
);

INSERT INTO customers (id,name,city,level) VALUES
  (1,'张三','武汉','金卡'),
  (2,'李四','北京','银卡'),
  (3,'王五','上海','普通'),
  (4,'赵六','武汉','银卡'),
  (5,'孙七','广州','金卡');

INSERT INTO products (id,name,category,price,stock) VALUES
  (101,'电动SUV','汽车',199000.00,30),
  (102,'紧凑轿车','汽车',129000.00,45),
  (103,'车载导航','配件',1800.00,200),
  (104,'行车记录仪','配件',699.00,150),
  (105,'高性能电池包','配件',30000.00,60);

INSERT INTO orders (id,customer_id,product_id,quantity,status,order_date) VALUES
  (1001,1,101,1,'已支付','2026-09-15 10:00:00'),
  (1002,2,103,2,'已支付','2026-09-20 14:30:00'),
  (1003,3,104,1,'待支付','2026-09-22 09:15:00'),
  (1004,4,101,1,'已支付','2026-09-28 16:45:00'),
  (1005,5,105,1,'已支付','2026-10-02 11:20:00'),
  (1006,1,102,1,'已取消','2026-10-05 13:00:00'),
  (1007,2,105,2,'已支付','2026-10-08 15:40:00'),
  (1008,3,103,1,'已支付','2026-10-09 10:05:00'),
  (1009,5,101,1,'待支付','2026-10-09 18:30:00');

-- 只读账号：仅对该库授 SELECT，符合"SELECT-only"规范
CREATE USER IF NOT EXISTS 'hermes_ro'@'%' IDENTIFIED BY '123456';
GRANT SELECT ON hermes_test.* TO 'hermes_ro'@'%';
FLUSH PRIVILEGES;