-- Disposable fixture for the isolated PostgreSQL acceptance runner only.
BEGIN;

-- Give the proof its own tables, separate from the application schema.
CREATE SCHEMA approval_consumption_proof;

-- Model one synthetic approver whose authority can change per exact scope.
CREATE TABLE approval_consumption_proof.scope_grants (
    scope text PRIMARY KEY,
    active boolean NOT NULL
);

-- Only a consumed approval has an execution reference.
CREATE TABLE approval_consumption_proof.approvals (
    approval_id uuid PRIMARY KEY,
    state text NOT NULL,
    execution_id uuid UNIQUE,
    authority_scope text REFERENCES approval_consumption_proof.scope_grants (scope),
    CHECK (
        (state IN ('approved', 'revoked') AND execution_id IS NULL)
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

-- Give each proof scenario an independent approval row.
INSERT INTO approval_consumption_proof.approvals (approval_id, state)
VALUES
    ('11111111-1111-4111-8111-111111111111', 'approved'),
    ('22222222-2222-4222-8222-222222222222', 'approved'),
    ('66666666-6666-4666-8666-666666666666', 'approved'),
    ('77777777-7777-4777-8777-777777777777', 'approved'),
    ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'approved');

-- Keep the control and revocation scenarios independent.
INSERT INTO approval_consumption_proof.scope_grants (scope, active)
VALUES ('control-scope', true), ('revocation-scope', true);

-- Bind each approval to the exact grant checked during consumption.
INSERT INTO approval_consumption_proof.approvals (approval_id, state, authority_scope)
VALUES
    ('dddddddd-dddd-4ddd-8ddd-dddddddddddd', 'approved', 'control-scope'),
    ('eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee', 'approved', 'revocation-scope');

-- Isolate committed approvals used by the three reader-boundary fault cases.
INSERT INTO approval_consumption_proof.approvals (approval_id, state)
VALUES
    ('10000000-0000-4000-8000-000000000001', 'approved'), -- Missing reported result.
    ('10000000-0000-4000-8000-000000000002', 'approved'), -- Wrong reported execution.
    ('10000000-0000-4000-8000-000000000003', 'approved'); -- Unavailable verification.

-- Keep the start-only control separate from the post-commit close-error scenario.
INSERT INTO approval_consumption_proof.approvals (approval_id, state)
VALUES
    ('30000000-0000-4000-8000-000000000001', 'approved'), -- Normally acknowledged start.
    ('30000000-0000-4000-8000-000000000002', 'approved'); -- Saved start with close error.

-- Separate pre-commit rollback from confirmation loss after a real start commit.
INSERT INTO approval_consumption_proof.approvals (approval_id, state)
VALUES
    ('30000000-0000-4000-8000-000000000003', 'approved'), -- Start INSERT is rolled back.
    ('30000000-0000-4000-8000-000000000004', 'approved'); -- Start commit stays saved.

COMMIT;
