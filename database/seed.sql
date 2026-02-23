CREATE EXTENSION IF NOT EXISTS pgcrypto;

INSERT INTO users (email, password_hash, full_name)
VALUES ('imran@cognizance.vision', crypt('happy123', gen_salt('bf')), 'Imran')
ON CONFLICT (email) DO NOTHING;
