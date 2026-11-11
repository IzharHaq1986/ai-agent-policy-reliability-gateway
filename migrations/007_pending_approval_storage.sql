-- Apply after migrations 001 through 006 as the migration administrator.
-- The existing protected schema owner creates both sides of the creation pair.
-- Runtime creation still requires protected operations and trusted time.
BEGIN;
SET LOCAL ROLE gateway_approval_owner;

-- Share the same canonical representation across the record and its event.
-- This checks calendar values and formatting, not the source's clock health.
CREATE DOMAIN gateway_approval.utc_second AS text COLLATE "C"
CHECK (
    VALUE IS NULL OR CASE
        WHEN length(VALUE) = 20 AND VALUE ~
            '^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z$'
        THEN substring(VALUE FROM 1 FOR 4)::integer BETWEEN 1 AND 9999
            AND make_date(
                substring(VALUE FROM 1 FOR 4)::integer,
                substring(VALUE FROM 6 FOR 2)::integer,
                substring(VALUE FROM 9 FOR 2)::integer
            ) IS NOT NULL
        ELSE false
    END
);

CREATE TABLE gateway_approval.approvals (
    approval_id text COLLATE "C" PRIMARY KEY, -- Identify this exact lifecycle.
    request_id text COLLATE "C" NOT NULL
        REFERENCES gateway_approval.requests (request_id), -- Bind the saved request.
    approval_contract_version integer NOT NULL CHECK (approval_contract_version = 1),
    created_at gateway_approval.utc_second NOT NULL,
    expires_at gateway_approval.utc_second NOT NULL,
    state text COLLATE "C" NOT NULL CHECK (state = 'pending'),
    state_version integer NOT NULL CHECK (state_version = 1),
    last_lifecycle_event_id text COLLATE "C" NOT NULL,
    CONSTRAINT pending_approval_id CHECK (
        length(approval_id) = 36 AND approval_id ~
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT pending_approval_time_order CHECK (created_at < expires_at),
    -- Supply the exact parent key required by a matching creation event.
    CONSTRAINT pending_approval_creation_key UNIQUE (
        approval_id, request_id, state, state_version, created_at, last_lifecycle_event_id
    )
);

CREATE TABLE gateway_approval.lifecycle_events (
    event_id text COLLATE "C" PRIMARY KEY, -- Identify the immutable creation evidence.
    approval_id text COLLATE "C" NOT NULL,
    request_id text COLLATE "C" NOT NULL,
    state text COLLATE "C" NOT NULL CHECK (state = 'pending'),
    state_version integer NOT NULL CHECK (state_version = 1),
    occurred_at gateway_approval.utc_second NOT NULL,
    event_type text COLLATE "C" NOT NULL CHECK (event_type = 'created'),
    CONSTRAINT approval_creation_event_id CHECK (
        length(event_id) = 36 AND event_id ~
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT approval_event_revision UNIQUE (approval_id, state_version),
    CONSTRAINT approval_event_creation_key UNIQUE (
        event_id, approval_id, request_id, state, state_version, occurred_at
    )
);

-- Check both directions at commit so neither row can be saved on its own.
ALTER TABLE gateway_approval.approvals
    ADD CONSTRAINT pending_approval_creation_event FOREIGN KEY (
        last_lifecycle_event_id, approval_id, request_id, state, state_version, created_at
    ) REFERENCES gateway_approval.lifecycle_events (
        event_id, approval_id, request_id, state, state_version, occurred_at
    ) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE gateway_approval.lifecycle_events
    ADD CONSTRAINT approval_creation_event_record FOREIGN KEY (
        approval_id, request_id, state, state_version, occurred_at, event_id
    ) REFERENCES gateway_approval.approvals (
        approval_id, request_id, state, state_version, created_at, last_lifecycle_event_id
    ) DEFERRABLE INITIALLY DEFERRED;

-- Ordinary application roles have no creation or transition privileges.
REVOKE ALL ON DOMAIN gateway_approval.utc_second FROM PUBLIC;
REVOKE ALL ON TABLE gateway_approval.approvals, gateway_approval.lifecycle_events
    FROM PUBLIC, gateway_audit_owner, gateway_audit_writer, gateway_audit_reader,
    gateway_approval_request_writer, gateway_approval_request_reader,
    gateway_approval_link_writer, gateway_protected_binding_writer;
GRANT SELECT ON TABLE gateway_approval.approvals, gateway_approval.lifecycle_events
    TO gateway_approval_request_reader;

COMMIT;
