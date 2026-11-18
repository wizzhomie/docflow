-- A durable queue of background work, for a worker that can be a second process.
--
-- The retry queue it replaces was a JSONL file guarded by a lock in one process:
-- two processes overwrote each other, a second worker could not drain it, and a
-- crash in the middle of a retry meant guessing what had happened. The table
-- below is the same idea with the database as the source of truth: work is
-- claimed with `FOR UPDATE SKIP LOCKED`, so two workers never take the same row,
-- and the attempt that failed is kept instead of being overwritten.
--
-- Why no row-level security here, when every other working table has it: a worker
-- serves every company, so a policy keyed on the current tenant would hide work
-- from the only process able to do it. The queue is platform infrastructure, and
-- the rule that keeps it safe is different — every row carries the company that
-- pays for the work, the API only ever reads it filtered by that company, and
-- nothing in it is exposed to a person. The worker sets the tenant from the row
-- before it touches a tenant table.

CREATE TABLE IF NOT EXISTS jobs (
    id           bigserial PRIMARY KEY,
    company_id   bigint NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    kind         text NOT NULL,
    payload      jsonb NOT NULL DEFAULT '{}'::jsonb,
    status       text NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued', 'running', 'done', 'failed')),
    attempts     integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL DEFAULT 5,
    run_after    timestamptz NOT NULL DEFAULT now(),
    locked_by    text,
    locked_at    timestamptz,
    last_error   text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- The claim query's index: ready work, in the order it became ready.
CREATE INDEX IF NOT EXISTS jobs_ready_idx ON jobs (run_after, id) WHERE status = 'queued';

-- What one company's queue looks like, and what the metrics endpoint counts.
CREATE INDEX IF NOT EXISTS jobs_company_idx ON jobs (company_id, status);

COMMENT ON COLUMN jobs.company_id IS
    'The company the work is done for; the worker sets app.company_id from it.';
