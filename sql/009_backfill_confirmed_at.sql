-- Confirmation time arrived after the documents it describes.
--
-- The demo's documents were confirmed through the queue before `confirmed_at`
-- existed, so the review time had nothing to measure from. The row kept
-- `edited_at`, which the confirm has always written — even when the reviewer
-- changed nothing — so that is the timestamp to backfill from, with `received_at`
-- as the floor for anything older.
--
-- The loop is not decoration: row-level security is forced on this table, and a
-- migration has no privileges that the application lacks. The work is done one
-- company at a time, inside that company's tenant, exactly as the product does it.

DO $$
DECLARE
    company record;
BEGIN
    FOR company IN SELECT id FROM companies LOOP
        PERFORM set_config('app.company_id', company.id::text, true);
        UPDATE documents
           SET confirmed_at = coalesce(edited_at, received_at)
         WHERE status IN ('confirmed', 'sent')
           AND confirmed_at IS NULL;
    END LOOP;
END $$;
