-- Roles gain a read-only one, a second demo company appears, and settings learn
-- the difference between a platform default and a company override.
--
-- The three layers from the design discussion now exist in the schema: a
-- platform default (company_id IS NULL), a company value, and a person with a role.

ALTER TABLE users DROP CONSTRAINT users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (role IN ('owner', 'reviewer', 'viewer'));

-- A second company with its own person and its own approval threshold: it makes
-- the isolation visible in the interface instead of only in a test.
INSERT INTO companies (name, slug) VALUES ('Second Demo Company', 'second');
INSERT INTO users (company_id, email, full_name, role)
VALUES ((SELECT id FROM companies WHERE slug = 'second'), 'viewer@demo.local', 'Second Demo Viewer', 'viewer');

-- Settings: one row per name for the platform, one per company when the company
-- has an opinion. The company value wins over the platform default.
ALTER TABLE settings ADD COLUMN company_id bigint REFERENCES companies(id) ON DELETE CASCADE;
ALTER TABLE settings DROP CONSTRAINT settings_pkey;
CREATE UNIQUE INDEX settings_platform_key ON settings (name) WHERE company_id IS NULL;
CREATE UNIQUE INDEX settings_company_key ON settings (company_id, name) WHERE company_id IS NOT NULL;

INSERT INTO settings (company_id, name, value, value_type, updated_by, note)
VALUES (
    (SELECT id FROM companies WHERE slug = 'second'),
    'routing.approval_threshold',
    '5000',
    'number',
    'seed',
    'Этот клиент согласует всё от 5 000'
);

-- What the application reads: the company value, or the platform default.
CREATE FUNCTION setting_value(p_name text, p_company bigint) RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT value
    FROM settings
    WHERE name = p_name AND (company_id = p_company OR company_id IS NULL)
    ORDER BY company_id NULLS LAST
    LIMIT 1
$$;
