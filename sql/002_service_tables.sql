-- Service tables: what the system remembers about itself between runs.
--
-- settings — a set of named values that a person may change in the interface
-- without a new release. Stored as one row per setting, with an explicit type
-- column: that is what lets the database reject "тридцать" in a numeric
-- setting even when the write comes from psql or from an old build of the app.
-- The narrow bounds (interval between 10 and 600 seconds) stay in the code:
-- there is no universal range that fits every number.

CREATE TABLE settings (
    name       text        PRIMARY KEY,
    value      text        NOT NULL,
    value_type text        NOT NULL DEFAULT 'text',
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by text,
    note       text        NOT NULL DEFAULT '',
    CONSTRAINT settings_name_check CHECK (name ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$'),
    CONSTRAINT settings_type_check CHECK (value_type IN ('number', 'text', 'bool')),
    CONSTRAINT settings_number_check CHECK (
        value_type <> 'number' OR value ~ '^[0-9]+(\.[0-9]+)?$'
    ),
    CONSTRAINT settings_bool_check CHECK (
        value_type <> 'bool' OR value IN ('true', 'false')
    ),
    CONSTRAINT settings_text_check CHECK (
        value_type <> 'text' OR length(btrim(value)) > 0
    )
);

COMMENT ON TABLE settings IS 'Named values a user may change without a release.';
COMMENT ON COLUMN settings.value IS 'Always stored as text; value_type says how to read it.';
COMMENT ON COLUMN settings.note IS 'Human hint shown next to the field in the interface.';

-- Values carried over from the environment variables the app reads today.
-- The IMAP password is deliberately absent: secrets stay in the environment.
INSERT INTO settings (name, value, value_type, updated_by, note) VALUES
    ('mail.interval',        '30',     'number', 'seed', 'Как часто проверять почту, в секундах'),
    ('mail.folder',          'INBOX',  'text',   'seed', 'Папка IMAP, которую читает приём'),
    ('report.manual_minutes','3',      'number', 'seed', 'Сколько минут занимает ручная обработка одного документа'),
    ('crm.connector',        'uspacy', 'text',   'seed', 'Какой CRM включён: uspacy или bitrix24'),
    ('routing.approval_threshold', '100000', 'number', 'seed', 'Сумма в валюте документа, начиная с которой подтверждение идёт на согласование')
ON CONFLICT (name) DO NOTHING;
