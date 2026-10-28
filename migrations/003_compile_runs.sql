-- Compile-run ledger (Lane 3).
--
-- One row per `wikimind compile <project>` invocation.  Scalar essentials
-- identify the run; the single JSONB stats column carries the per-run
-- counters (units_seen, claims_drafted, dispositions, judge
-- accepted/rejected, schema_rejects, run_cost_usd and friends).
-- Project-scoped per the schema convention.

CREATE TABLE compile_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'done', 'failed')),
    stats JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX compile_runs_project_idx ON compile_runs (project_id);
