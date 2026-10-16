-- Einmalig im Supabase SQL Editor ausführen, VOR dem Deployment.
-- Bestehende Daten und Zuordnungen bleiben erhalten.
ALTER TABLE public.odoo_products ADD COLUMN IF NOT EXISTS brand_id BIGINT;
ALTER TABLE public.odoo_products ADD COLUMN IF NOT EXISTS brand_name TEXT;
