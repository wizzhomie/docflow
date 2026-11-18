-- A document's whole life in one table.
--
-- Until now an upload wrote a JSON snapshot and a history line, and the database
-- row appeared only when a tool imported the files. Three columns make the row
-- the document: the name it arrived with, where it came from, and when a person
-- confirmed it. The parsed snapshot was already here; with these, the queue, the
-- history and the impact report can all read the same row instead of two stores.

ALTER TABLE documents ADD COLUMN IF NOT EXISTS filename text;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'upload';
ALTER TABLE documents ADD COLUMN IF NOT EXISTS confirmed_at timestamptz;

COMMENT ON COLUMN documents.filename IS
    'The name the file arrived with, shown to the reviewer when the stored path is opaque.';
COMMENT ON COLUMN documents.source IS
    'upload, folder, mailbox or sample: where the document entered the system.';
COMMENT ON COLUMN documents.confirmed_at IS
    'When a person confirmed the document; the review time is received_at → confirmed_at.';

-- One queue per company, and the history in the order it was written.
CREATE INDEX IF NOT EXISTS documents_company_status_idx
    ON documents (company_id, status, received_at DESC);
