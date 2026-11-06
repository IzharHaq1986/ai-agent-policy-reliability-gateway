-- Disposable fixture for the isolated PostgreSQL acceptance runner only.
BEGIN;

-- Give the proof its own tables, separate from the application schema.
CREATE SCHEMA approval_consumption_proof;

-- An approved record has no execution reference; a consumed record has one.
CREATE TABLE approval_consumption_proof.approvals (
    approval_id uuid PRIMARY KEY,
    state text NOT NULL,
    execution_id uuid UNIQUE,
    CHECK (
        (state = 'approved' AND execution_id IS NULL)
        OR (state = 'consumed' AND execution_id IS NOT NULL)
    )
);

-- Allow one consumption event per approval. The named constraint lets the
-- rollback test verify that the database rejected the intended audit write.
CREATE TABLE approval_consumption_proof.consumption_events (
    approval_id uuid PRIMARY KEY
        REFERENCES approval_consumption_proof.approvals (approval_id),
    execution_id uuid NOT NULL UNIQUE,
    event_type text NOT NULL,
    CONSTRAINT proof_consumption_event_type CHECK (event_type = 'consumed')
);

-- Seed separate approvals for contention and rollback.
INSERT INTO approval_consumption_proof.approvals (approval_id, state)
VALUES
    ('11111111-1111-4111-8111-111111111111', 'approved'),
    ('22222222-2222-4222-8222-222222222222', 'approved');

COMMIT;
