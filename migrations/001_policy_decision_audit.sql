-- Apply with psql -X -v ON_ERROR_STOP=1 using a migration administrator.
-- Required pre-provisioned roles:
-- gateway_audit_owner, gateway_audit_writer, gateway_audit_reader.
-- This migration is transactional and intentionally not repeatable.

BEGIN;

CREATE SCHEMA gateway_audit AUTHORIZATION gateway_audit_owner;
SET LOCAL ROLE gateway_audit_owner;

CREATE TABLE gateway_audit.policy_decisions (
    schema_version integer NOT NULL,
    event_type text COLLATE "C" NOT NULL,
    event_id text COLLATE "C" PRIMARY KEY,
    occurred_at text COLLATE "C" NOT NULL,
    principal_id text COLLATE "C" NOT NULL,
    decision text COLLATE "C" NOT NULL,
    reason_code text COLLATE "C" NOT NULL,
    policy_version text COLLATE "C" NOT NULL,
    execution_status text COLLATE "C" NOT NULL,

    CONSTRAINT audit_schema_version CHECK (schema_version = 1),
    CONSTRAINT audit_event_type CHECK (event_type = 'policy_decision'),
    CONSTRAINT audit_event_id CHECK (
        length(event_id) = 36
        AND event_id ~
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT audit_occurred_at CHECK (
        CASE
            WHEN length(occurred_at) = 20
                AND occurred_at ~
                    '^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z$'
            THEN
                substring(occurred_at FROM 1 FOR 4)::integer BETWEEN 1 AND 9999
                AND make_date(
                    substring(occurred_at FROM 1 FOR 4)::integer,
                    substring(occurred_at FROM 6 FOR 2)::integer,
                    substring(occurred_at FROM 9 FOR 2)::integer
                ) IS NOT NULL
            ELSE false
        END
    ),
    CONSTRAINT audit_principal CHECK (principal_id = 'reliability-reader'),
    CONSTRAINT audit_decision CHECK (decision IN ('ALLOW', 'DENY')),
    CONSTRAINT audit_reason CHECK (
        reason_code IN (
            'UNAUTHENTICATED',
            'INVALID_REQUEST',
            'UNKNOWN_TOOL',
            'UNKNOWN_OPERATION',
            'NOT_AUTHORIZED',
            'POLICY_ALLOW'
        )
    ),
    CONSTRAINT audit_policy_version CHECK (policy_version = 'policy-core-v1'),
    CONSTRAINT audit_execution_status CHECK (execution_status = 'not_executed'),
    CONSTRAINT audit_decision_reason CHECK (
        (decision = 'ALLOW' AND reason_code = 'POLICY_ALLOW')
        OR (decision = 'DENY' AND reason_code <> 'POLICY_ALLOW')
    )
);

REVOKE ALL ON SCHEMA gateway_audit FROM PUBLIC;
REVOKE ALL ON TABLE gateway_audit.policy_decisions FROM PUBLIC;
REVOKE ALL ON SCHEMA gateway_audit
    FROM gateway_audit_writer, gateway_audit_reader;
REVOKE ALL ON TABLE gateway_audit.policy_decisions
    FROM gateway_audit_writer, gateway_audit_reader;

GRANT USAGE ON SCHEMA gateway_audit
    TO gateway_audit_writer, gateway_audit_reader;
GRANT INSERT, SELECT ON TABLE gateway_audit.policy_decisions
    TO gateway_audit_writer;
GRANT SELECT ON TABLE gateway_audit.policy_decisions
    TO gateway_audit_reader;

COMMIT;
