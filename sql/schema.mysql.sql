-- T 系统 MySQL 建表脚本（与 app/models.py 一致；程序启动亦可自动建表）
-- 库与账号请按实际环境调整；连接串示例：
-- mysql+pymysql://tuser:tpassword@127.0.0.1:3306/t_system?charset=utf8mb4

CREATE DATABASE IF NOT EXISTS t_system DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE t_system;

-- 兑换记忆库：一条记忆 = 客户名 + 字段名 + 原值 → 修改值
CREATE TABLE IF NOT EXISTS memory_rule (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  customer_name VARCHAR(255)  NOT NULL,
  field_name    VARCHAR(128)  NOT NULL,
  old_value     TEXT,
  new_value     TEXT,
  rule_type     VARCHAR(16)   NOT NULL DEFAULT 'permanent',  -- permanent 长期 / once 单次
  status        VARCHAR(16)   NOT NULL DEFAULT 'enabled',    -- enabled / disabled
  effective_count INT         NOT NULL DEFAULT 0,            -- once 规则剩余可用次数
  hit_count     INT           NOT NULL DEFAULT 0,
  last_hit_time DATETIME      NULL,
  created_by    VARCHAR(64)   NOT NULL DEFAULT '',
  created_at    DATETIME      NOT NULL,
  updated_by    VARCHAR(64)   NOT NULL DEFAULT '',
  updated_at    DATETIME      NOT NULL,
  source        VARCHAR(32)   NOT NULL DEFAULT '',
  INDEX idx_rule_customer (customer_name),
  INDEX idx_rule_field (field_name),
  INDEX idx_rule_status (status)
) ENGINE=InnoDB;

-- 命中日志：T1 每次字段查询均记录（命中与未命中）
CREATE TABLE IF NOT EXISTS hit_log (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  rule_id      INT NULL,
  order_id     INT NULL,
  form_id      VARCHAR(64) NULL,
  hit          TINYINT(1) NOT NULL DEFAULT 0,
  field_name   VARCHAR(128) NOT NULL,
  value_before TEXT,
  value_after  TEXT,
  hit_time     DATETIME NOT NULL,
  INDEX idx_hit_rule (rule_id),
  INDEX idx_hit_order (order_id)
) ENGINE=InnoDB;

-- 全量操作日志：操作人/时间/类型/订单/字段/前后值/规则
CREATE TABLE IF NOT EXISTS history (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  operator_id   VARCHAR(64)  NOT NULL DEFAULT '',
  operator_name VARCHAR(64)  NOT NULL DEFAULT '',
  op_time       DATETIME     NOT NULL,
  op_type       VARCHAR(32)  NOT NULL,
  order_id      INT NULL,
  form_id       VARCHAR(64) NULL,
  field_name    VARCHAR(128) NULL,
  value_before  TEXT,
  value_after   TEXT,
  rule_id       INT NULL,
  remark        VARCHAR(500) NULL,
  INDEX idx_hist_op (op_type),
  INDEX idx_hist_order (order_id),
  INDEX idx_hist_rule (rule_id)
) ENGINE=InnoDB;

-- T 本地订单
CREATE TABLE IF NOT EXISTS orders (
  id                INT AUTO_INCREMENT PRIMARY KEY,
  zhimou_task_id    VARCHAR(64) NULL,
  customer_name     VARCHAR(255) NOT NULL,
  form_id           VARCHAR(64) NULL,
  version           INT NOT NULL DEFAULT 1,
  status            VARCHAR(24) NOT NULL DEFAULT 'pending_create',
  payload           JSON,
  pending_writeback JSON NULL,
  last_error        TEXT NULL,
  locked_by         VARCHAR(64) NULL,
  locked_by_name    VARCHAR(64) NULL,
  locked_at         DATETIME NULL,
  created_at        DATETIME NOT NULL,
  updated_at        DATETIME NOT NULL,
  INDEX idx_order_customer (customer_name),
  INDEX idx_order_status (status),
  INDEX idx_order_form (form_id)
) ENGINE=InnoDB;

-- 用户（mock 认证；SSO 模式下仅存映射）
CREATE TABLE IF NOT EXISTS users (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  username      VARCHAR(64) NOT NULL UNIQUE,
  display_name  VARCHAR(64) NOT NULL DEFAULT '',
  password_hash VARCHAR(128) NOT NULL DEFAULT '',
  role          VARCHAR(16) NOT NULL DEFAULT 'sales',
  token         VARCHAR(128) NULL UNIQUE,
  enabled       TINYINT(1) NOT NULL DEFAULT 1
) ENGINE=InnoDB;

-- 预订单模板字段（按客户配置展示，'*' 为默认）
CREATE TABLE IF NOT EXISTS template_fields (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  customer_name VARCHAR(255) NOT NULL DEFAULT '*',
  field_name    VARCHAR(128) NOT NULL,
  label         VARCHAR(128) NOT NULL DEFAULT '',
  display_order INT NOT NULL DEFAULT 100,
  visible       TINYINT(1) NOT NULL DEFAULT 1,
  editable      TINYINT(1) NOT NULL DEFAULT 1,
  INDEX idx_tpl_customer (customer_name)
) ENGINE=InnoDB;

-- mock ePortal 预订单（演示用；真实环境为外部系统）
-- fields 为 schema 化结构：{字段: {value, type, editable, required, options, group}}
CREATE TABLE IF NOT EXISTS eportal_orders (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  form_id       VARCHAR(64) NOT NULL UNIQUE,
  customer_name VARCHAR(255) NOT NULL,
  fields        JSON,
  items         JSON,                                -- 产品行（含稳定 line_id）
  item_schema   JSON,                                -- {列: {editable, label}}
  attachments   JSON,                                -- 附件元数据 {id, name, removable}
  auto_modified JSON,
  version       INT NOT NULL DEFAULT 1,
  status        VARCHAR(16) NOT NULL DEFAULT 'draft',
  created_at    DATETIME NOT NULL,
  updated_at    DATETIME NOT NULL
) ENGINE=InnoDB;

-- 纠正案例：保存成功后按错误说明创建，后台交给内部 Agent 归纳学习
CREATE TABLE IF NOT EXISTS correction_case (
  id             INT AUTO_INCREMENT PRIMARY KEY,
  order_ref      VARCHAR(64) NULL,                   -- ePortal 订单 ID
  version        INT NULL,
  customer_name  VARCHAR(255) NOT NULL DEFAULT '',
  field_name     VARCHAR(128) NOT NULL,
  original_value TEXT NULL,                          -- 智眸原始值
  memory_value   TEXT NULL,                          -- T 自动替换值（如有）
  final_value    TEXT NULL,                          -- 人工最终值
  description    TEXT NULL,                          -- 错误说明原文
  operator_id    VARCHAR(64) NULL,                   -- ePortal 操作人
  operator_name  VARCHAR(64) NULL,
  state          VARCHAR(16) NOT NULL DEFAULT 'pending', -- pending/processed/failed/conflict
  agent_summary  TEXT NULL,
  agent_result   JSON NULL,
  error          TEXT NULL,
  rule_id        INT NULL,
  created_at     DATETIME NOT NULL,
  processed_at   DATETIME NULL,
  INDEX idx_case_order (order_ref),
  INDEX idx_case_state (state),
  INDEX idx_case_rule (rule_id)
) ENGINE=InnoDB;

-- mock ePortal 写入日志：幂等键 = 表单ID + 版本号
CREATE TABLE IF NOT EXISTS eportal_write_log (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  form_id    VARCHAR(64) NOT NULL,
  version    INT NOT NULL,
  payload    JSON NULL,
  created_at DATETIME NOT NULL,
  UNIQUE KEY uq_form_version (form_id, version)
) ENGINE=InnoDB;
