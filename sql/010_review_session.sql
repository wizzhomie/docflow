-- When a person actually opened the document, as opposed to when it arrived.
--
-- Review time measured upload → confirmation includes the waiting: the demo's
-- imported documents were confirmed days after they were imported, so the report
-- read "no minutes saved" for every one of them. The honest measurement is the
-- session — from opening the document to confirming it — and a document that was
-- never opened simply has no measured review time instead of an invented one.

ALTER TABLE documents ADD COLUMN IF NOT EXISTS viewed_at timestamptz;

COMMENT ON COLUMN documents.viewed_at IS
    'First time a person opened the document; review time is measured from here to confirmation.';

UPDATE documents
   SET viewed_at = NULL
 WHERE viewed_at IS NOT NULL;
