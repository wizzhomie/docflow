-- Confirmation time arrived after the documents it describes.
--
-- The demo's documents were confirmed through the queue before `confirmed_at`
-- existed, so the review time had nothing to measure from and the impact report
-- read zero minutes saved. The row kept `edited_at`, which the confirm has always
-- written — even when the reviewer changed nothing — so that is the timestamp to
-- backfill from, with `received_at` as the floor for anything older.

UPDATE documents
   SET confirmed_at = coalesce(edited_at, received_at)
 WHERE status IN ('confirmed', 'sent')
   AND confirmed_at IS NULL;
