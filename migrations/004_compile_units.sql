-- Compile-unit short-circuit markers (Lane 3).
--
-- One row per (document, channel, compile identity) that has been fully
-- processed.  This is the unit-level content-hash short-circuit: a re-run
-- of an unchanged source issues zero LLM calls for marked units, and a
-- changed COMPILE_PROMPT_VERSION invalidates every marker at once.  Rows
-- are written in the same transaction as the unit's committed claims.

CREATE TABLE compile_units (
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    channel_id TEXT NOT NULL,
    compile_version TEXT NOT NULL,
    run_id UUID REFERENCES compile_runs(id) ON DELETE SET NULL,
    compiled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (document_id, channel_id, compile_version)
);

CREATE INDEX compile_units_project_idx ON compile_units (project_id);
CREATE INDEX compile_units_run_idx ON compile_units (run_id);
