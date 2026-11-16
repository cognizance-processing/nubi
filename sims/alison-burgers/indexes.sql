-- Applied after the bulk load: building secondary indexes on a populated
-- table is far faster than maintaining them row by row during insert.
USE alison_pos;
ALTER TABLE stores ADD KEY ix_stores_region (region);
ALTER TABLE menu_items ADD KEY ix_menu_category (category);
ALTER TABLE staff ADD KEY ix_staff_store (store_id);
ALTER TABLE shifts ADD KEY ix_shifts_store_date (store_id, shift_date);
ALTER TABLE shifts ADD KEY ix_shifts_date (shift_date);
ALTER TABLE orders ADD KEY ix_orders_date (order_date);
ALTER TABLE orders ADD KEY ix_orders_store_date (store_id, order_date);
ALTER TABLE orders ADD KEY ix_orders_channel (channel);
ALTER TABLE order_items ADD KEY ix_oi_order (order_id);
ALTER TABLE order_items ADD KEY ix_oi_item (item_id);
ALTER TABLE order_items ADD KEY ix_oi_date (order_date);
ALTER TABLE order_items ADD KEY ix_oi_store_date (store_id, order_date);
ALTER TABLE reviews ADD KEY ix_reviews_store_date (store_id, submitted_on);
