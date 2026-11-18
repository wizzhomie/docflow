-- What the person confirmed, as opposed to what the machine read.
--
-- `parsed` is the parser's opinion, and it must stay that way: a history that
-- silently shows the edited number as if it had been read is a history nobody can
-- audit. Until now the edited values were only written into the four columns the
-- queue form exposes, so an edit to the supplier or the tax id was accepted and
-- then dropped. The confirmed values get their own column, and the history, the
-- impact report and the CRM payload read that one.

ALTER TABLE documents ADD COLUMN IF NOT EXISTS confirmed_fields jsonb;

COMMENT ON COLUMN documents.confirmed_fields IS
    'The values the reviewer approved: the parsed fields with the edits applied.';
