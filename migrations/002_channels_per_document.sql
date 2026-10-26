-- Channel uniqueness is per document (contract amendment).
--
-- channels.id is the content hash of (kind, normalized_text) and is NOT unique
-- across documents: the same paper parsed by two parsers (pdf-fallback and
-- grobid) yields identical title channels, and repeated reference strings
-- collide between papers.  The primary key therefore becomes
-- (id, document_id); the hash derivation itself is unchanged.
--
-- claims must say which document a claim's channel belongs to, so they carry
-- document_id and reference the composite key.  The old claims FK on
-- channel_id alone depends on the old primary key and is dropped first.
-- Safe because all tables are still empty.

ALTER TABLE claims DROP CONSTRAINT claims_channel_id_fkey;

ALTER TABLE channels DROP CONSTRAINT channels_pkey;
ALTER TABLE channels
    ADD CONSTRAINT channels_id_document_pkey PRIMARY KEY (id, document_id);

ALTER TABLE claims
    ADD COLUMN document_id UUID NOT NULL REFERENCES documents (id) ON DELETE CASCADE;

ALTER TABLE claims
    ADD CONSTRAINT claims_channel_document_fkey
    FOREIGN KEY (channel_id, document_id)
    REFERENCES channels (id, document_id)
    ON DELETE CASCADE;

CREATE INDEX claims_document_idx ON claims (document_id);
