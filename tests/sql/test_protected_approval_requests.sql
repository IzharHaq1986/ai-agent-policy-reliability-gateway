-- Run after migration 004 as the isolated test administrator.
-- Roll back the test rows after checking storage and effective privileges.
BEGIN;

DO $$
DECLARE
    role_name text;
    request_table regclass := 'gateway_approval.requests'::regclass;
BEGIN
    -- Keep ownership with the dedicated role and exclude PUBLIC grants.
    IF NOT EXISTS (
        SELECT 1
        FROM pg_class AS c
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE c.oid = request_table
            AND pg_get_userbyid(c.relowner) = 'gateway_approval_owner'
            AND c.relowner = n.nspowner
            AND NOT EXISTS (
                SELECT 1 FROM aclexplode(c.relacl) WHERE grantee = 0
            )
            AND NOT EXISTS (
                SELECT 1 FROM aclexplode(n.nspacl) WHERE grantee = 0
            )
    ) THEN
        RAISE EXCEPTION 'Unexpected protected request ownership or PUBLIC access';
    END IF;

    -- Application roles receive only the capabilities needed for this table.
    FOREACH role_name IN ARRAY ARRAY[
        'gateway_approval_request_writer', 'gateway_approval_request_reader',
        'gateway_audit_owner', 'gateway_audit_writer', 'gateway_audit_reader'
    ] LOOP
        IF has_schema_privilege(role_name, 'gateway_approval', 'CREATE')
            OR has_table_privilege(role_name, request_table, 'DELETE, TRUNCATE, TRIGGER')
            OR has_any_column_privilege(role_name, request_table, 'UPDATE, REFERENCES')
            OR has_any_column_privilege(
                role_name, request_table, 'SELECT WITH GRANT OPTION, INSERT WITH GRANT OPTION'
            )
            OR has_schema_privilege(role_name, 'gateway_approval', 'USAGE') <>
                (role_name IN ('gateway_approval_request_writer', 'gateway_approval_request_reader'))
            OR has_any_column_privilege(role_name, request_table, 'INSERT') <>
                (role_name = 'gateway_approval_request_writer')
            OR has_any_column_privilege(role_name, request_table, 'SELECT') <>
                (role_name = 'gateway_approval_request_reader')
        THEN
            RAISE EXCEPTION 'Unexpected protected request privileges for %', role_name;
        END IF;
    END LOOP;
END;
$$;

SET LOCAL ROLE gateway_approval_request_writer;

DO $$
DECLARE
    saved gateway_approval.requests := ROW(
        '11111111-1111-4111-8111-111111111111', 1, 'test-actor',
        'reliability-reader', 'fixture_store', 'READ_ITEM', 42,
        'policy-core-v1', 'test-adapter-v1'
    );
    invalid record;
    rejected_constraint text;
BEGIN
    -- Insert two distinct request instances with identical captured values.
    INSERT INTO gateway_approval.requests SELECT (saved).*;
    BEGIN
        INSERT INTO gateway_approval.requests SELECT (saved).*;
        RAISE EXCEPTION 'Duplicate request identity was accepted';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;
    saved.request_id := '22222222-2222-4222-8222-222222222222';
    INSERT INTO gateway_approval.requests SELECT (saved).*;

    -- Use a fresh ID so each invalid input must fail its own constraint.
    saved.request_id := '33333333-3333-4333-8333-333333333333';
    FOR invalid IN
        SELECT * FROM (VALUES
            ('{"request_id":"bad-id"}', 'approval_request_id_format'),
            ('{"request_schema_version":2}', 'approval_request_schema_version'),
            ('{"requester_actor_id":" "}', 'approval_request_actor_id'),
            ('{"principal_id":"other"}', 'approval_request_principal'),
            ('{"tool":"other"}', 'approval_request_tool'),
            ('{"operation":"WRITE_ITEM"}', 'approval_request_operation'),
            ('{"item_id":0}', 'approval_request_item'),
            ('{"item_id":1001}', 'approval_request_item'),
            ('{"policy_version":"other"}', 'approval_request_policy_version'),
            ('{"adapter_version":"bad version"}', 'approval_request_adapter_version')
        ) AS cases(changes, constraint_name)
    LOOP
        BEGIN
            INSERT INTO gateway_approval.requests
            SELECT (jsonb_populate_record(saved, invalid.changes::jsonb)).*;
            RAISE EXCEPTION 'Invalid request was accepted for %', invalid.constraint_name;
        EXCEPTION WHEN check_violation THEN
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
    -- Read both IDs back with every captured field intact.
    IF (SELECT count(*) FROM gateway_approval.requests) <> 2
        OR (
            SELECT array_agg(request_id ORDER BY request_id)
            FROM gateway_approval.requests
            WHERE request_schema_version = 1 AND requester_actor_id = 'test-actor'
                AND principal_id = 'reliability-reader' AND tool = 'fixture_store'
                AND operation = 'READ_ITEM' AND item_id = 42
                AND policy_version = 'policy-core-v1' AND adapter_version = 'test-adapter-v1'
        ) IS DISTINCT FROM ARRAY[
            '11111111-1111-4111-8111-111111111111',
            '22222222-2222-4222-8222-222222222222'
        ]
    THEN
        RAISE EXCEPTION 'Protected request readback differed from the saved values';
    END IF;
END;
$$;

ROLLBACK;
SELECT 'PROTECTED_APPROVAL_REQUEST_SQL_ACCEPTANCE=PASS';
