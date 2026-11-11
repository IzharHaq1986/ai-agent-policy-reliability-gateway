-- Run after migration 007 as the isolated test administrator.
-- Force the same deferred constraints that PostgreSQL checks at commit.
BEGIN;

DO $$
DECLARE
    table_name text;
    role_name text;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = 'gateway_approval_owner'
            AND NOT rolcanlogin AND NOT rolsuper AND NOT rolcreaterole
    ) THEN
        RAISE EXCEPTION 'Unexpected lifecycle owner authority';
    END IF;
    IF (
        SELECT count(*) FROM pg_constraint
        WHERE conname IN ('pending_approval_creation_event', 'approval_creation_event_record')
            AND conrelid IN (
                'gateway_approval.approvals'::regclass,
                'gateway_approval.lifecycle_events'::regclass
            )
            AND contype = 'f' AND condeferrable AND condeferred AND convalidated
            AND confupdtype = 'a' AND confdeltype = 'a'
    ) <> 2 THEN
        RAISE EXCEPTION 'Missing deferred creation-pair constraints';
    END IF;
    FOREACH table_name IN ARRAY ARRAY[
        'gateway_approval.approvals', 'gateway_approval.lifecycle_events'
    ] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_class AS c
            WHERE c.oid = table_name::regclass
                AND pg_get_userbyid(c.relowner) = 'gateway_approval_owner'
                AND NOT EXISTS (
                    SELECT 1 FROM aclexplode(c.relacl) WHERE grantee = 0
                )
        ) THEN
            RAISE EXCEPTION 'Unexpected pending approval ownership or PUBLIC access';
        END IF;
        FOREACH role_name IN ARRAY ARRAY[
            'gateway_audit_owner', 'gateway_audit_writer', 'gateway_audit_reader',
            'gateway_approval_request_writer', 'gateway_approval_request_reader',
            'gateway_approval_link_writer', 'gateway_protected_binding_writer'
        ] LOOP
            IF has_table_privilege(role_name, table_name, 'DELETE, TRUNCATE, TRIGGER')
                OR pg_has_role(role_name, 'gateway_approval_owner', 'MEMBER')
                OR has_any_column_privilege(role_name, table_name, 'INSERT, UPDATE, REFERENCES')
                OR has_any_column_privilege(role_name, table_name, 'SELECT') <>
                    (role_name = 'gateway_approval_request_reader')
                OR has_any_column_privilege(
                    role_name, table_name, 'SELECT WITH GRANT OPTION'
                )
            THEN
                RAISE EXCEPTION 'Unexpected pending approval application privileges';
            END IF;
        END LOOP;
    END LOOP;
END;
$$;

SET LOCAL ROLE gateway_approval_request_writer;
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

-- Only the migration owner may populate lifecycle storage at this stage.
SET LOCAL ROLE gateway_approval_owner;
INSERT INTO gateway_approval.approvals VALUES (
    '11111111-1111-4111-8111-111111111111', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    1, '2026-11-11T12:00:00Z', '2026-11-11T12:05:00Z',
    'pending', 1, '22222222-2222-4222-8222-222222222222'
);
INSERT INTO gateway_approval.lifecycle_events VALUES (
    '22222222-2222-4222-8222-222222222222', '11111111-1111-4111-8111-111111111111',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'pending', 1,
    '2026-11-11T12:00:00Z', 'created'
);
SET CONSTRAINTS ALL IMMEDIATE;
SET CONSTRAINTS ALL DEFERRED;

DO $$
DECLARE
    approval gateway_approval.approvals := ROW(
        '33333333-3333-4333-8333-333333333333', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
        1, '2026-11-11T12:00:00Z', '2026-11-11T12:05:00Z',
        'pending', 1, '44444444-4444-4444-8444-444444444444'
    );
    creation gateway_approval.lifecycle_events := ROW(
        '44444444-4444-4444-8444-444444444444', '33333333-3333-4333-8333-333333333333',
        'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'pending', 1,
        '2026-11-11T12:00:00Z', 'created'
    );
    invalid record;
    rejected_constraint text;
BEGIN
    -- Either half alone must fail the deferred check and roll back its insert.
    BEGIN
        INSERT INTO gateway_approval.approvals SELECT (approval).*;
        SET CONSTRAINTS ALL IMMEDIATE;
        RAISE EXCEPTION 'Approval without creation evidence was accepted';
    EXCEPTION WHEN foreign_key_violation THEN
        GET STACKED DIAGNOSTICS rejected_constraint = CONSTRAINT_NAME;
        IF rejected_constraint <> 'pending_approval_creation_event' THEN
            RAISE EXCEPTION 'Unexpected approval-only constraint %', rejected_constraint;
        END IF;
    END;
    BEGIN
        INSERT INTO gateway_approval.lifecycle_events SELECT (creation).*;
        SET CONSTRAINTS ALL IMMEDIATE;
        RAISE EXCEPTION 'Creation evidence without approval was accepted';
    EXCEPTION WHEN foreign_key_violation THEN
        GET STACKED DIAGNOSTICS rejected_constraint = CONSTRAINT_NAME;
        IF rejected_constraint <> 'approval_creation_event_record' THEN
            RAISE EXCEPTION 'Unexpected event-only constraint %', rejected_constraint;
        END IF;
    END;

    -- The event must match the exact request, identity, and creation time.
    FOR invalid IN
        SELECT * FROM (VALUES
            ('{"request_id":"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"}'),
            ('{"approval_id":"55555555-5555-4555-8555-555555555555"}'),
            ('{"event_id":"66666666-6666-4666-8666-666666666666"}'),
            ('{"occurred_at":"2026-11-11T12:00:01Z"}')
        ) AS cases(changes)
    LOOP
        BEGIN
            INSERT INTO gateway_approval.approvals SELECT (approval).*;
            INSERT INTO gateway_approval.lifecycle_events
            SELECT (jsonb_populate_record(creation, invalid.changes::jsonb)).*;
            SET CONSTRAINTS ALL IMMEDIATE;
            RAISE EXCEPTION 'Mismatched creation evidence was accepted';
        EXCEPTION WHEN foreign_key_violation THEN
            NULL;
        END;
    END LOOP;

    -- Invalid IDs, states, revisions, versions, and time values fail immediately.
    FOR invalid IN
        SELECT * FROM (VALUES
            ('{"approval_id":"bad-id"}'),
            ('{"approval_contract_version":2}'),
            ('{"state":"approved"}'),
            ('{"state_version":2}'),
            ('{"created_at":"2026-11-11T12:05:00Z"}'),
            ('{"expires_at":"2026-11-11T11:59:59Z"}'),
            ('{"created_at":"2026-02-30T12:00:00Z"}'),
            ('{"created_at":"0000-01-01T00:00:00Z"}'),
            ('{"created_at":"2026-11-11T12:00:00+00:00"}'),
            ('{"expires_at":"2026-11-11T12:05:00.0Z"}'),
            ('{"created_at":null}'),
            ('{"request_id":"cccccccc-cccc-4ccc-8ccc-cccccccccccc"}')
        ) AS cases(changes)
    LOOP
        BEGIN
            INSERT INTO gateway_approval.approvals
            SELECT (jsonb_populate_record(approval, invalid.changes::jsonb)).*;
            RAISE EXCEPTION 'Invalid pending approval was accepted';
        EXCEPTION WHEN check_violation OR datetime_field_overflow
            OR not_null_violation OR foreign_key_violation THEN
            NULL;
        END;
    END LOOP;

    -- Event fields also reject attempts to represent a decision or later revision.
    FOR invalid IN
        SELECT * FROM (VALUES
            ('{"event_id":"bad-id"}'),
            ('{"state":"approved"}'),
            ('{"state_version":2}'),
            ('{"event_type":"approved"}'),
            ('{"occurred_at":"2026-11-11T12:00:00.0Z"}')
        ) AS cases(changes)
    LOOP
        BEGIN
            INSERT INTO gateway_approval.lifecycle_events
            SELECT (jsonb_populate_record(creation, invalid.changes::jsonb)).*;
            RAISE EXCEPTION 'Invalid creation event was accepted';
        EXCEPTION WHEN check_violation THEN
            NULL;
        END;
    END LOOP;

    -- An existing approval identity and an existing event identity cannot be reused.
    BEGIN
        INSERT INTO gateway_approval.approvals
        SELECT * FROM gateway_approval.approvals;
        RAISE EXCEPTION 'Duplicate approval ID was accepted';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;
    BEGIN
        INSERT INTO gateway_approval.lifecycle_events
        SELECT * FROM gateway_approval.lifecycle_events;
        RAISE EXCEPTION 'Duplicate creation event ID was accepted';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;
    BEGIN
        INSERT INTO gateway_approval.lifecycle_events
        SELECT '77777777-7777-4777-8777-777777777777', approval_id, request_id,
            state, state_version, occurred_at, event_type
        FROM gateway_approval.lifecycle_events;
        RAISE EXCEPTION 'A second event for the same revision was accepted';
    EXCEPTION WHEN unique_violation THEN
        GET STACKED DIAGNOSTICS rejected_constraint = CONSTRAINT_NAME;
        IF rejected_constraint <> 'approval_event_revision' THEN
            RAISE EXCEPTION 'Unexpected event revision constraint %', rejected_constraint;
        END IF;
    END;
END;
$$;

SET LOCAL ROLE gateway_approval_request_reader;
DO $$
BEGIN
    -- Only the complete original pair may remain after the rejected attempts.
    IF (SELECT count(*) FROM gateway_approval.approvals) <> 1
        OR (SELECT count(*) FROM gateway_approval.lifecycle_events) <> 1
        OR NOT EXISTS (
            SELECT 1 FROM gateway_approval.approvals AS a
            JOIN gateway_approval.lifecycle_events AS e
                ON e.event_id = a.last_lifecycle_event_id
            WHERE a.approval_id = '11111111-1111-4111-8111-111111111111'
                AND a.request_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
                AND a.approval_contract_version = 1
                AND a.state = 'pending' AND a.state_version = 1
                AND a.created_at = '2026-11-11T12:00:00Z'
                AND a.expires_at = '2026-11-11T12:05:00Z'
                AND e.event_id = '22222222-2222-4222-8222-222222222222'
                AND e.approval_id = a.approval_id AND e.request_id = a.request_id
                AND e.state = a.state AND e.state_version = a.state_version
                AND e.occurred_at = a.created_at AND e.event_type = 'created'
        )
    THEN
        RAISE EXCEPTION 'Pending approval readback differed from the saved pair';
    END IF;
END;
$$;

ROLLBACK;
SELECT 'PENDING_APPROVAL_STORAGE_SQL_ACCEPTANCE=PASS';
