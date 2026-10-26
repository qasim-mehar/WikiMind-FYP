-- WikiMind schema v1.
--
-- Every table except projects and schema_migrations carries project_id NOT NULL.
-- Enum-like columns use CHECK constraints consistently.
-- Raw source text is stored only in channels.raw_text.

CREATE TABLE projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued'
        CHECK (state IN ('queued', 'running', 'done', 'failed', 'crashed')),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    heartbeat_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX jobs_project_state_idx ON jobs (project_id, state);

CREATE TABLE documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    sha256 TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    parser TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT documents_memo_key UNIQUE (
        project_id, sha256, parser, prompt_version, model, schema_version
    )
);

CREATE INDEX documents_project_idx ON documents (project_id);

CREATE TABLE channels (
    id TEXT PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    raw_text TEXT NOT NULL
);

CREATE INDEX channels_document_idx ON channels (document_id);
CREATE INDEX channels_project_idx ON channels (project_id);

CREATE TABLE nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    slug TEXT NOT NULL,
    node_type TEXT NOT NULL
        CHECK (node_type IN ('citation', 'entity', 'concept', 'synthetic', 'memory')),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'stale', 'superseded', 'archived', 'retracted')),
    current_version_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT nodes_project_slug_key UNIQUE (project_id, slug)
);

CREATE INDEX nodes_project_status_idx ON nodes (project_id, status);
CREATE INDEX nodes_project_type_idx ON nodes (project_id, node_type);

CREATE TABLE node_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    node_id UUID NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    version INTEGER NOT NULL CHECK (version > 0),
    rendered_text TEXT NOT NULL,
    fts TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', rendered_text)) STORED,
    created_by_job UUID REFERENCES jobs(id) ON DELETE SET NULL,
    CONSTRAINT node_versions_node_version_key UNIQUE (node_id, version)
);

CREATE INDEX node_versions_node_idx ON node_versions (node_id);
CREATE INDEX node_versions_fts_gin ON node_versions USING gin (fts);
CREATE INDEX node_versions_project_idx ON node_versions (project_id);

ALTER TABLE nodes
    ADD CONSTRAINT nodes_current_version_id_fkey
    FOREIGN KEY (current_version_id) REFERENCES node_versions(id) ON DELETE SET NULL;

CREATE TABLE node_redirects (
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    from_slug TEXT NOT NULL,
    node_id UUID NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    PRIMARY KEY (project_id, from_slug)
);

CREATE INDEX node_redirects_node_idx ON node_redirects (node_id);

CREATE TABLE claims (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    citation_node_id UUID NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    anchor_quote TEXT NOT NULL,
    anchor_span JSONB NOT NULL,
    text TEXT NOT NULL,
    created_by_job UUID REFERENCES jobs(id) ON DELETE SET NULL
);

CREATE INDEX claims_project_idx ON claims (project_id);
CREATE INDEX claims_citation_node_idx ON claims (citation_node_id);
CREATE INDEX claims_channel_idx ON claims (channel_id);

CREATE TABLE ledger_blocks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    node_id UUID NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    claim_id UUID REFERENCES claims(id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position >= 0),
    block_type TEXT NOT NULL CHECK (block_type IN ('claim', 'derived')),
    CONSTRAINT ledger_blocks_node_claim_key UNIQUE (node_id, claim_id),
    CONSTRAINT ledger_blocks_type_claim_check CHECK (
        (block_type = 'claim' AND claim_id IS NOT NULL)
        OR (block_type = 'derived' AND claim_id IS NULL)
    )
);

CREATE INDEX ledger_blocks_node_position_idx ON ledger_blocks (node_id, position);
CREATE INDEX ledger_blocks_claim_idx ON ledger_blocks (claim_id);
CREATE INDEX ledger_blocks_project_idx ON ledger_blocks (project_id);

CREATE TABLE edges (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    src_node UUID NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    dst_node UUID NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    edge_type TEXT NOT NULL CHECK (
        edge_type IN (
            'relates_to', 'part_of', 'supports', 'contradicts', 'supersedes',
            'cites', 'authored_by', 'uses', 'measured_by'
        )
    ),
    created_by_job UUID REFERENCES jobs(id) ON DELETE SET NULL,
    CONSTRAINT edges_project_src_dst_type_key UNIQUE (
        project_id, src_node, dst_node, edge_type
    )
);

CREATE INDEX edges_src_idx ON edges (src_node);
CREATE INDEX edges_dst_idx ON edges (dst_node);
CREATE INDEX edges_project_idx ON edges (project_id);

CREATE TABLE edge_sources (
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    edge_id UUID NOT NULL REFERENCES edges(id) ON DELETE CASCADE,
    claim_id UUID NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    PRIMARY KEY (edge_id, claim_id)
);

CREATE INDEX edge_sources_claim_idx ON edge_sources (claim_id);
CREATE INDEX edge_sources_project_idx ON edge_sources (project_id);

CREATE TABLE node_sources (
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    node_id UUID NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    PRIMARY KEY (node_id, document_id)
);

CREATE INDEX node_sources_document_idx ON node_sources (document_id);
CREATE INDEX node_sources_project_idx ON node_sources (project_id);

CREATE TABLE embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    node_version_id UUID NOT NULL REFERENCES node_versions(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    vec vector(384) NOT NULL,
    CONSTRAINT embeddings_node_version_model_key UNIQUE (node_version_id, model)
);

CREATE INDEX embeddings_project_idx ON embeddings (project_id);
CREATE INDEX embeddings_vec_hnsw ON embeddings USING hnsw (vec vector_cosine_ops);

CREATE TABLE cost_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    job_id UUID REFERENCES jobs(id) ON DELETE SET NULL,
    call_site TEXT NOT NULL,
    model TEXT NOT NULL,
    in_tokens BIGINT NOT NULL DEFAULT 0 CHECK (in_tokens >= 0),
    out_tokens BIGINT NOT NULL DEFAULT 0 CHECK (out_tokens >= 0),
    unit_price NUMERIC(20, 10) NOT NULL DEFAULT 0 CHECK (unit_price >= 0),
    shadow_cost_usd NUMERIC(20, 10) NOT NULL DEFAULT 0 CHECK (shadow_cost_usd >= 0),
    meta JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX cost_events_project_idx ON cost_events (project_id);
CREATE INDEX cost_events_job_idx ON cost_events (job_id);

CREATE TABLE events (
    id BIGSERIAL PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX events_project_created_idx ON events (project_id, created_at);
