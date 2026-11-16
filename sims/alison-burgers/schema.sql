-- Alison Burgers — point-of-sale replica used for Nubi demos.
-- Entirely synthetic. No real people, stores or transactions.

DROP DATABASE IF EXISTS alison_pos;
CREATE DATABASE alison_pos CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE alison_pos;

CREATE TABLE stores (
  store_id      INT PRIMARY KEY,
  store_code    VARCHAR(12)  NOT NULL,
  store_name    VARCHAR(80)  NOT NULL,
  city          VARCHAR(60)  NOT NULL,
  region        VARCHAR(40)  NOT NULL,
  store_format  VARCHAR(20)  NOT NULL,
  opened_on     DATE         NOT NULL,
  seats         INT          NOT NULL,
  UNIQUE KEY uq_stores_code (store_code)
) ENGINE=InnoDB;

CREATE TABLE menu_items (
  item_id       INT PRIMARY KEY,
  item_name     VARCHAR(60)  NOT NULL,
  category      VARCHAR(30)  NOT NULL,
  subcategory   VARCHAR(30)  NOT NULL,
  list_price    DECIMAL(8,2) NOT NULL,
  food_cost     DECIMAL(8,2) NOT NULL,
  is_vegan      TINYINT(1)   NOT NULL DEFAULT 0,
  is_limited    TINYINT(1)   NOT NULL DEFAULT 0,
  launched_on   DATE         NULL
) ENGINE=InnoDB;

CREATE TABLE staff (
  staff_id      INT PRIMARY KEY,
  store_id      INT          NOT NULL,
  full_name     VARCHAR(60)  NOT NULL,
  job_role      VARCHAR(30)  NOT NULL,
  hired_on      DATE         NOT NULL,
  hourly_rate   DECIMAL(8,2) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE shifts (
  shift_id      INT PRIMARY KEY,
  store_id      INT          NOT NULL,
  staff_id      INT          NOT NULL,
  shift_date    DATE         NOT NULL,
  daypart       VARCHAR(16)  NOT NULL,
  hours_worked  DECIMAL(5,2) NOT NULL,
  labour_cost   DECIMAL(9,2) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE orders (
  order_id        BIGINT PRIMARY KEY,
  store_id        INT          NOT NULL,
  ordered_at      DATETIME     NOT NULL,
  order_date      DATE         NOT NULL,
  daypart         VARCHAR(16)  NOT NULL,
  channel         VARCHAR(20)  NOT NULL,
  payment_type    VARCHAR(20)  NOT NULL,
  promo_code      VARCHAR(24)  NULL,
  item_count      INT          NOT NULL,
  gross_amount    DECIMAL(10,2) NOT NULL,
  discount_amount DECIMAL(10,2) NOT NULL,
  net_amount      DECIMAL(10,2) NOT NULL,
  food_cost       DECIMAL(10,2) NOT NULL,
  commission_amount DECIMAL(10,2) NOT NULL,
  prep_seconds    INT          NOT NULL,
  wait_seconds    INT          NOT NULL
) ENGINE=InnoDB;

-- order_items carries store/date/channel alongside the order key. A line-item
-- fact table is normally built this way, and it keeps menu analysis off the
-- 347k-row orders table: without it, grouping 1.3M lines by item costs half a
-- million random primary-key lookups into orders just to reach the date.
CREATE TABLE order_items (
  order_item_id BIGINT PRIMARY KEY,
  order_id      BIGINT       NOT NULL,
  store_id      INT          NOT NULL,
  order_date    DATE         NOT NULL,
  channel       VARCHAR(20)  NOT NULL,
  item_id       INT          NOT NULL,
  quantity      INT          NOT NULL,
  unit_price    DECIMAL(8,2) NOT NULL,
  unit_cost     DECIMAL(8,2) NOT NULL,
  line_discount DECIMAL(8,2) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE reviews (
  review_id     BIGINT PRIMARY KEY,
  order_id      BIGINT       NOT NULL,
  store_id      INT          NOT NULL,
  submitted_on  DATE         NOT NULL,
  score         INT          NOT NULL,
  source        VARCHAR(20)  NOT NULL
) ENGINE=InnoDB;