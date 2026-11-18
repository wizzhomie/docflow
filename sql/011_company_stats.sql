-- Per-company counters, kept by the database instead of counted per request.
--
-- The metrics endpoint used to open one short transaction per company, because
-- row-level security only shows a company its own documents. That is fine for two
-- companies and wrong for two thousand: the cost of the answer grows with the
-- number of tenants, and the endpoint is supposed to say how the system is doing,
-- not become the thing that is doing badly.
--
-- The counter is updated by a trigger on `documents`, so it cannot drift from the
-- rows: every insert, every status change and every delete moves the numbers by
-- the same amount in the same transaction. It has no row-level security for the
-- same reason the queue has none — it is platform infrastructure, the API never
-- reads one company through it, and every row names the company it counts.

CREATE TABLE IF NOT EXISTS company_stats (
    company_id  bigint PRIMARY KEY REFERENCES companies(id) ON DELETE CASCADE,
    documents   bigint NOT NULL DEFAULT 0 CHECK (documents >= 0),
    waiting     bigint NOT NULL DEFAULT 0 CHECK (waiting >= 0),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE company_stats IS
    'Per-company document counters, maintained by a trigger; read by /api/metrics.';
COMMENT ON COLUMN company_stats.waiting IS
    'Documents still in status new, which is what the review queue shows.';

CREATE OR REPLACE FUNCTION documents_company_stats() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO company_stats (company_id, documents, waiting)
        VALUES (NEW.company_id, 1, CASE WHEN NEW.status = 'new' THEN 1 ELSE 0 END)
        ON CONFLICT (company_id) DO UPDATE
            SET documents  = company_stats.documents + 1,
                waiting    = company_stats.waiting
                             + CASE WHEN NEW.status = 'new' THEN 1 ELSE 0 END,
                updated_at = now();
        RETURN NEW;
    END IF;

    IF TG_OP = 'DELETE' THEN
        UPDATE company_stats
           SET documents  = greatest(0, documents - 1),
               waiting    = greatest(
                  0, waiting - CASE WHEN OLD.status = 'new' THEN 1 ELSE 0 END
               ),
               updated_at = now()
         WHERE company_id = OLD.company_id;
        RETURN OLD;
    END IF;

    -- UPDATE: a status leaving or entering 'new' moves the queue; a document
    -- moving to another company moves both counters.
    IF NEW.company_id IS DISTINCT FROM OLD.company_id THEN
        UPDATE company_stats
           SET documents  = greatest(0, documents - 1),
               waiting    = greatest(
                  0, waiting - CASE WHEN OLD.status = 'new' THEN 1 ELSE 0 END
               ),
               updated_at = now()
         WHERE company_id = OLD.company_id;
        INSERT INTO company_stats (company_id, documents, waiting)
        VALUES (NEW.company_id, 1, CASE WHEN NEW.status = 'new' THEN 1 ELSE 0 END)
        ON CONFLICT (company_id) DO UPDATE
            SET documents  = company_stats.documents + 1,
                waiting    = company_stats.waiting
                             + CASE WHEN NEW.status = 'new' THEN 1 ELSE 0 END,
                updated_at = now();
    ELSIF NEW.status IS DISTINCT FROM OLD.status THEN
        UPDATE company_stats
           SET waiting    = greatest(
                  0,
                  waiting
                  + CASE WHEN NEW.status = 'new' THEN 1 ELSE 0 END
                  - CASE WHEN OLD.status = 'new' THEN 1 ELSE 0 END
              ),
               updated_at = now()
         WHERE company_id = NEW.company_id;
    END IF;
    RETURN NEW;
END $$;

-- What is already here. A migration has no privileges the application lacks, and
-- row-level security is forced on `documents`, so the count is taken one company
-- at a time inside that company's tenant — exactly as the product does it.
DO $$
DECLARE
    company record;
BEGIN
    FOR company IN SELECT id FROM companies LOOP
        PERFORM set_config('app.company_id', company.id::text, true);
        INSERT INTO company_stats (company_id, documents, waiting)
        SELECT company.id,
               count(*),
               count(*) FILTER (WHERE status = 'new')
          FROM documents
        ON CONFLICT (company_id) DO UPDATE
            SET documents  = EXCLUDED.documents,
                waiting    = EXCLUDED.waiting,
                updated_at = now();
    END LOOP;
END $$;

DROP TRIGGER IF EXISTS documents_company_stats ON documents;
CREATE TRIGGER documents_company_stats
    AFTER INSERT OR UPDATE OF status, company_id OR DELETE ON documents
    FOR EACH ROW EXECUTE FUNCTION documents_company_stats();
