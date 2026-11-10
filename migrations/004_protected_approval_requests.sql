-- Apply after migrations 001 through 003 as the migration administrator.
-- Pre-provision gateway_approval_owner, gateway_approval_request_writer,
-- and gateway_approval_request_reader without membership in the owner role.
-- This migration is transactional and intentionally not repeatable.

BEGIN;

CREATE SCHEMA gateway_approval AUTHORIZATION gateway_approval_owner;
SET LOCAL ROLE gateway_approval_owner;

CREATE TABLE gateway_approval.requests (
    request_id text COLLATE "C" PRIMARY KEY, -- Preserve the server-created identity.
    request_schema_version integer NOT NULL, -- Identify the captured descriptor format.
    requester_actor_id text COLLATE "C" NOT NULL, -- Store an opaque canonical actor ID.
    principal_id text COLLATE "C" NOT NULL, -- Record the separately mapped policy principal.
    tool text COLLATE "C" NOT NULL, -- Capture the fixed synthetic adapter.
    operation text COLLATE "C" NOT NULL, -- Capture the exact permitted operation.
    item_id integer NOT NULL, -- Together with tool and operation, delimit the resource scope.
    policy_version text COLLATE "C" NOT NULL, -- Preserve the captured policy version.
    adapter_version text COLLATE "C" NOT NULL, -- Preserve the captured adapter version.

    CONSTRAINT approval_request_id_format CHECK (
        length(request_id) = 36 AND request_id ~
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT approval_request_schema_version CHECK (request_schema_version = 1),
    CONSTRAINT approval_request_actor_id CHECK (
        length(requester_actor_id) BETWEEN 1 AND 256
        AND btrim(requester_actor_id) = requester_actor_id
        AND requester_actor_id !~ '[[:cntrl:]]'
    ),
    CONSTRAINT approval_request_principal CHECK (principal_id = 'reliability-reader'),
    CONSTRAINT approval_request_tool CHECK (tool = 'fixture_store'),
    CONSTRAINT approval_request_operation CHECK (operation = 'READ_ITEM'),
    CONSTRAINT approval_request_item CHECK (item_id BETWEEN 1 AND 1000),
    CONSTRAINT approval_request_policy_version CHECK (policy_version = 'policy-core-v1'),
    CONSTRAINT approval_request_adapter_version CHECK (
        length(adapter_version) BETWEEN 1 AND 64
        AND adapter_version ~ '^[a-zA-Z0-9][a-zA-Z0-9._-]*$'
    )
);

-- Restrict the new schema independently of the existing audit schema.
REVOKE ALL ON SCHEMA gateway_approval FROM PUBLIC,
    gateway_audit_owner, gateway_audit_writer, gateway_audit_reader,
    gateway_approval_request_writer, gateway_approval_request_reader;
REVOKE ALL ON TABLE gateway_approval.requests FROM PUBLIC,
    gateway_audit_owner, gateway_audit_writer, gateway_audit_reader,
    gateway_approval_request_writer, gateway_approval_request_reader;

GRANT USAGE ON SCHEMA gateway_approval
    TO gateway_approval_request_writer, gateway_approval_request_reader;
GRANT INSERT ON TABLE gateway_approval.requests TO gateway_approval_request_writer;
GRANT SELECT ON TABLE gateway_approval.requests TO gateway_approval_request_reader;

COMMIT;
