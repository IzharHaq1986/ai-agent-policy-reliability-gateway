-- Run after migration 005 as the isolated test administrator.
-- Roll back the test links and their parent records together.
BEGIN;

DO $$
DECLARE
    role_name text;
    link_table regclass := 'gateway_approval.policy_event_request_links'::regclass;
BEGIN
    -- Keep ownership with the protected schema owner and exclude PUBLIC grants.
    IF NOT EXISTS (
        SELECT 1 FROM pg_class AS c
        WHERE c.oid = link_table
            AND pg_get_userbyid(c.relowner) = 'gateway_approval_owner'
            AND NOT EXISTS (
                SELECT 1 FROM aclexplode(c.relacl) WHERE grantee = 0
            )
    ) THEN
        RAISE EXCEPTION 'Unexpected policy-event link ownership or PUBLIC access';
    END IF;

    -- The writer may append links and the protected reader may inspect them.
    FOREACH role_name IN ARRAY ARRAY[
        'gateway_approval_link_writer', 'gateway_approval_request_reader',
        'gateway_approval_request_writer',
        'gateway_audit_owner', 'gateway_audit_writer', 'gateway_audit_reader'
    ] LOOP
        IF has_schema_privilege(role_name, 'gateway_approval', 'CREATE')
            OR has_schema_privilege(role_name, 'gateway_approval', 'USAGE') <>
                (role_name IN (
                    'gateway_approval_link_writer', 'gateway_approval_request_reader',
                    'gateway_approval_request_writer'
                ))
            OR has_table_privilege(role_name, link_table, 'DELETE, TRUNCATE, TRIGGER')
            OR has_any_column_privilege(role_name, link_table, 'UPDATE, REFERENCES')
            OR has_any_column_privilege(
                role_name, link_table, 'SELECT WITH GRANT OPTION, INSERT WITH GRANT OPTION'
            )
            OR has_any_column_privilege(role_name, link_table, 'INSERT') <>
                (role_name = 'gateway_approval_link_writer')
            OR has_any_column_privilege(role_name, link_table, 'SELECT') <>
                (role_name = 'gateway_approval_request_reader')
        THEN
            RAISE EXCEPTION 'Unexpected policy-event link privileges for %', role_name;
        END IF;
    END LOOP;

    -- Foreign keys require no parent-table reads by the application writer.
    IF has_any_column_privilege(
        'gateway_approval_link_writer', 'gateway_audit.request_bindings', 'SELECT'
    ) OR has_any_column_privilege(
        'gateway_approval_link_writer', 'gateway_approval.requests', 'SELECT'
    ) THEN
        RAISE EXCEPTION 'Link writer can read a parent table';
    END IF;
END;
$$;

SET LOCAL ROLE gateway_audit_writer;

-- Seed two valid link targets, one for the missing-request case, and one event
-- without a binding to distinguish a policy decision from a saved binding.
INSERT INTO gateway_audit.policy_decisions (
    schema_version, event_type, event_id, occurred_at, principal_id,
    decision, reason_code, policy_version, execution_status
)
SELECT 1, 'policy_decision', event_id, '2026-11-10T00:00:00Z',
    'reliability-reader', 'ALLOW', 'POLICY_ALLOW', 'policy-core-v1', 'not_executed'
FROM (VALUES
    ('11111111-1111-4111-8111-111111111111'),
    ('22222222-2222-4222-8222-222222222222'),
    ('33333333-3333-4333-8333-333333333333'),
    ('44444444-4444-4444-8444-444444444444')
) AS events(event_id);

INSERT INTO gateway_audit.request_bindings (
    schema_version, policy_event_id, principal_id, tool, operation, item_id
)
SELECT 1, event_id, 'reliability-reader', 'fixture_store', 'READ_ITEM', 42
FROM (VALUES
    ('11111111-1111-4111-8111-111111111111'),
    ('22222222-2222-4222-8222-222222222222'),
    ('33333333-3333-4333-8333-333333333333')
) AS events(event_id);

SET LOCAL ROLE gateway_approval_request_writer;

-- Two request IDs deliberately have identical captured values.
INSERT INTO gateway_approval.requests (
    request_id, request_schema_version, requester_actor_id, principal_id,
    tool, operation, item_id, policy_version, adapter_version
)
SELECT request_id, 1, 'test-actor', 'reliability-reader', 'fixture_store',
    'READ_ITEM', 42, 'policy-core-v1', 'test-adapter-v1'
FROM (VALUES
    ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'),
    ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb')
) AS requests(request_id);

SET LOCAL ROLE gateway_approval_link_writer;

-- A new policy evaluation may link to the same protected request.
INSERT INTO gateway_approval.policy_event_request_links (policy_event_id, request_id)
VALUES
    ('11111111-1111-4111-8111-111111111111', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'),
    ('22222222-2222-4222-8222-222222222222', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');

DO $$
DECLARE
    invalid record;
    rejected_constraint text;
BEGIN
    -- Each negative case must fail on the intended key or foreign key.
    FOR invalid IN
        SELECT * FROM (VALUES
            ('11111111-1111-4111-8111-111111111111', 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
                'policy_event_request_links_pkey'),
            ('44444444-4444-4444-8444-444444444444', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
                'approval_link_policy_binding'),
            ('33333333-3333-4333-8333-333333333333', 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
                'approval_link_protected_request')
        ) AS cases(policy_event_id, request_id, constraint_name)
    LOOP
        BEGIN
            INSERT INTO gateway_approval.policy_event_request_links (policy_event_id, request_id)
            VALUES (invalid.policy_event_id, invalid.request_id);
            RAISE EXCEPTION 'Invalid link was accepted for %', invalid.constraint_name;
        EXCEPTION WHEN unique_violation OR foreign_key_violation THEN
            GET STACKED DIAGNOSTICS rejected_constraint = CONSTRAINT_NAME;
            IF rejected_constraint IS DISTINCT FROM invalid.constraint_name THEN
                RAISE EXCEPTION 'Unexpected constraint %', rejected_constraint;
            END IF;
        END;
    END LOOP;
END;
$$;

SET LOCAL ROLE gateway_approval_request_reader;

DO $$
BEGIN
    -- Both saved event IDs must still point to the original request.
    IF (SELECT count(*) FROM gateway_approval.policy_event_request_links) <> 2
        OR (
            SELECT array_agg(policy_event_id ORDER BY policy_event_id)
            FROM gateway_approval.policy_event_request_links
            WHERE request_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
        ) IS DISTINCT FROM ARRAY[
            '11111111-1111-4111-8111-111111111111',
            '22222222-2222-4222-8222-222222222222'
        ]
    THEN
        RAISE EXCEPTION 'Policy-event link readback differed from the saved links';
    END IF;
END;
$$;

ROLLBACK;
SELECT 'APPROVAL_POLICY_EVENT_LINK_SQL_ACCEPTANCE=PASS';
