-- DataOpsStudio 测试数据初始化
USE dataops;

-- ─── 部门表 ───────────────────────────────────────────────────────────────────
CREATE TABLE departments (
  id       INT         PRIMARY KEY,
  name     VARCHAR(50) NOT NULL,
  manager  VARCHAR(50)
);

INSERT INTO departments VALUES
(1, '研发部',   '张伟'),
(2, '产品部',   '李娜'),
(3, '运营部',   '王芳'),
(4, '数据部',   '刘洋'),
(5, '财务部',   '陈静');

-- ─── 用户表（当前版本）────────────────────────────────────────────────────────
CREATE TABLE users (
  id         INT            PRIMARY KEY,
  name       VARCHAR(50)    NOT NULL,
  email      VARCHAR(100),
  dept_id    INT,
  salary     DECIMAL(10,2),
  status     VARCHAR(20),
  created_at DATE
);

INSERT INTO users VALUES
(1,  '张三',   'zhangsan@company.com',  1, 15000.00, 'active',   '2023-01-15'),
(2,  '李四',   'lisi@company.com',      1, 16000.00, 'active',   '2023-02-20'),
(3,  '王五',   'wangwu@company.com',    2, 14000.00, 'active',   '2023-03-10'),
(4,  '赵六',   'zhaoliu@company.com',   2, 18000.00, 'active',   '2023-04-05'),
(5,  '钱七',   'qianqi@company.com',    3, 12000.00, 'inactive', '2023-05-01'),
(6,  '孙八',   'sunba@company.com',     3, 13000.00, 'active',   '2023-06-15'),
(7,  '周九',   'zhoujiu@company.com',   4, 20000.00, 'active',   '2023-07-20'),
(8,  '吴十',   'wushi@company.com',     4, 22000.00, 'active',   '2023-08-01'),
(9,  '郑十一', 'zheng11@company.com',   5, 11000.00, 'active',   '2023-09-10'),
(10, '冯十二', 'feng12@company.com',    5, 10500.00, 'active',   '2023-10-05'),
(11, '褚十三', 'chu13@company.com',     1, 17500.00, 'active',   '2024-01-08'),
(12, '卫十四', 'wei14@company.com',     4, 21000.00, 'active',   '2024-02-14');

-- ─── 用户归档表（对比用，含差异）────────────────────────────────────────────
-- 差异：id=2 薪资变更；id=4 状态变更；id=7 薪资变更；id=11 在归档中不存在；id=13 归档独有
CREATE TABLE users_archive (
  id         INT            PRIMARY KEY,
  name       VARCHAR(50)    NOT NULL,
  email      VARCHAR(100),
  dept_id    INT,
  salary     DECIMAL(10,2),
  status     VARCHAR(20),
  created_at DATE
);

INSERT INTO users_archive VALUES
(1,  '张三',   'zhangsan@company.com',  1, 15000.00, 'active',   '2023-01-15'),
(2,  '李四',   'lisi@company.com',      1, 14500.00, 'active',   '2023-02-20'), -- salary: 16000→14500
(3,  '王五',   'wangwu@company.com',    2, 14000.00, 'active',   '2023-03-10'),
(4,  '赵六',   'zhaoliu@company.com',   2, 18000.00, 'inactive', '2023-04-05'), -- status: active→inactive
(5,  '钱七',   'qianqi@company.com',    3, 12000.00, 'inactive', '2023-05-01'),
(6,  '孙八',   'sunba@company.com',     3, 13000.00, 'active',   '2023-06-15'),
(7,  '周九',   'zhoujiu@company.com',   4, 19000.00, 'active',   '2023-07-20'), -- salary: 20000→19000
(8,  '吴十',   'wushi@company.com',     4, 22000.00, 'active',   '2023-08-01'),
(9,  '郑十一', 'zheng11@company.com',   5, 11000.00, 'active',   '2023-09-10'),
(10, '冯十二', 'feng12@company.com',    5, 10500.00, 'active',   '2023-10-05'),
-- id=11 缺失（归档中已删除）
(12, '卫十四', 'wei14@company.com',     4, 21000.00, 'active',   '2024-02-14'),
(13, '蒋十五', 'jiang15@company.com',   2, 15500.00, 'active',   '2023-11-20'); -- 归档独有

-- ─── 订单表（当前版本）────────────────────────────────────────────────────────
CREATE TABLE orders (
  id         INT            PRIMARY KEY,
  user_id    INT,
  product    VARCHAR(100),
  amount     DECIMAL(10,2),
  status     VARCHAR(20),
  order_date DATE
);

INSERT INTO orders VALUES
(1,  1, '数据分析平台授权',  5000.00,  'completed',  '2024-01-10'),
(2,  2, '云存储服务包',      1200.00,  'completed',  '2024-01-15'),
(3,  3, '数据清洗工具',      3500.00,  'completed',  '2024-02-01'),
(4,  4, '报表定制服务',      8000.00,  'pending',    '2024-02-20'),
(5,  5, 'ETL工具授权',       2000.00,  'completed',  '2024-03-05'),
(6,  6, '数据库迁移服务',   15000.00,  'completed',  '2024-03-10'),
(7,  7, '大数据平台搭建',   50000.00,  'completed',  '2024-03-15'),
(8,  8, 'BI看板定制',       12000.00,  'pending',    '2024-04-01'),
(9,  1, '年度维保服务',      3000.00,  'completed',  '2024-04-10'),
(10, 2, '数据安全审计',      6000.00,  'completed',  '2024-04-15'),
(11, 3, '实时数据流处理',   18000.00,  'processing', '2024-05-01'),
(12, 4, '数据湖建设咨询',   25000.00,  'completed',  '2024-05-10'),
(13, 6, '性能优化服务',      4500.00,  'completed',  '2024-05-20'),
(14, 7, '数据治理培训',      8000.00,  'completed',  '2024-06-01'),
(15, 8, 'API接口开发',      10000.00,  'completed',  '2024-06-15');

-- ─── 订单对比表（含差异）─────────────────────────────────────────────────────
-- 差异：id=2 金额变更；id=4 状态变更；id=7 金额变更；id=11 金额变更；id=16 独有
CREATE TABLE orders_v2 (
  id         INT            PRIMARY KEY,
  user_id    INT,
  product    VARCHAR(100),
  amount     DECIMAL(10,2),
  status     VARCHAR(20),
  order_date DATE
);

INSERT INTO orders_v2 VALUES
(1,  1, '数据分析平台授权',  5000.00,  'completed',  '2024-01-10'),
(2,  2, '云存储服务包',      1500.00,  'completed',  '2024-01-15'), -- amount: 1200→1500
(3,  3, '数据清洗工具',      3500.00,  'completed',  '2024-02-01'),
(4,  4, '报表定制服务',      8000.00,  'completed',  '2024-02-20'), -- status: pending→completed
(5,  5, 'ETL工具授权',       2000.00,  'completed',  '2024-03-05'),
(6,  6, '数据库迁移服务',   15000.00,  'completed',  '2024-03-10'),
(7,  7, '大数据平台搭建',   48000.00,  'completed',  '2024-03-15'), -- amount: 50000→48000
(8,  8, 'BI看板定制',       12000.00,  'pending',    '2024-04-01'),
(9,  1, '年度维保服务',      3000.00,  'completed',  '2024-04-10'),
(10, 2, '数据安全审计',      6000.00,  'completed',  '2024-04-15'),
(11, 3, '实时数据流处理',   20000.00,  'processing', '2024-05-01'), -- amount: 18000→20000
(12, 4, '数据湖建设咨询',   25000.00,  'completed',  '2024-05-10'),
(13, 6, '性能优化服务',      4500.00,  'completed',  '2024-05-20'),
(14, 7, '数据治理培训',      8000.00,  'completed',  '2024-06-01'),
(15, 8, 'API接口开发',      10000.00,  'completed',  '2024-06-15'),
(16, 9, '数据质量评估',      5500.00,  'completed',  '2024-06-20'); -- v2独有
