-- Run only in the isolated test database after migration 001.
-- Invoke with psql -X -v ON_ERROR_STOP=1 as the test administrator.
-- All test rows are rolled back.

BEGIN;

DO $$
DECLARE
    columns_found text[];
BEGIN
    SELECT array_agg(attname::text ORDER BY attnum)
    INTO columns_found
    FROM pg_attribute
    WHERE attrelid = 'gateway_audit.policy_decisions'::regclass
        AND attnum > 0 AND NOT attisdropped;

    IF columns_found <> ARRAY[
        'schema_version', 'event_type', 'event_id', 'occurred_at',
        'principal_id', 'decision', 'reason_code', 'policy_version',
        'execution_status'
    ] THEN
        RAISE EXCEPTION 'Unexpected audit columns';
    END IF;

    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = 'gateway_audit.policy_decisions'::regclass
            AND attnum > 0 AND NOT attisdropped
            AND (
                NOT attnotnull
                OR atttypid <> CASE WHEN attname = 'schema_version'
                    THEN 'integer'::regtype ELSE 'text'::regtype END
            )
    ) THEN
        RAISE EXCEPTION 'Unexpected column type or nullability';
    END IF;

    IF (
        SELECT pg_get_userbyid(relowner)
        FROM pg_class
        WHERE oid = 'gateway_audit.policy_decisions'::regclass
    ) <> 'gateway_audit_owner' OR (
        SELECT pg_get_userbyid(nspowner)
        FROM pg_namespace WHERE nspname = 'gateway_audit'
    ) <> 'gateway_audit_owner' THEN
        RAISE EXCEPTION 'Unexpected audit ownership';
    END IF;

    IF EXISTS (
        SELECT 1 FROM pg_class AS c,
            LATERAL aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) AS a
        WHERE c.oid = 'gateway_audit.policy_decisions'::regclass
            AND a.grantee = 0
    ) OR EXISTS (
        SELECT 1 FROM pg_namespace AS n,
            LATERAL aclexplode(coalesce(n.nspacl, acldefault('n', n.nspowner))) AS a
        WHERE n.nspname = 'gateway_audit' AND a.grantee = 0
    ) THEN
        RAISE EXCEPTION 'Unexpected PUBLIC audit privilege';
    END IF;
END;
$$;

SET LOCAL ROLE gateway_audit_writer;

DO $$
DECLARE
    baseline jsonb := '{
        "schema_version": 1,
        "event_type": "policy_decision",
        "event_id": "550e8400-e29b-41d4-a716-446655440000",
        "occurred_at": "2026-10-07T10:00:00Z",
        "principal_id": "reliability-reader",
        "decision": "ALLOW",
        "reason_code": "POLICY_ALLOW",
        "policy_version": "policy-core-v1",
        "execution_status": "not_executed"
    }';
    candidate jsonb;
    patch jsonb;
    reason text;
    field text;
    stamp text;
    command text;
    rejected boolean;
    sequence integer := 0;
BEGIN
    FOREACH reason IN ARRAY ARRAY[
        'POLICY_ALLOW', 'UNAUTHENTICATED', 'INVALID_REQUEST',
        'UNKNOWN_TOOL', 'UNKNOWN_OPERATION', 'NOT_AUTHORIZED'
    ] LOOP
        sequence := sequence + 1;
        candidate := baseline || jsonb_build_object(
            'event_id', '550e8400-e29b-41d4-a716-' || lpad(sequence::text, 12, '0'),
            'reason_code', reason,
            'decision', CASE WHEN reason = 'POLICY_ALLOW' THEN 'ALLOW' ELSE 'DENY' END
        );
        INSERT INTO gateway_audit.policy_decisions
        SELECT * FROM jsonb_populate_record(
            NULL::gateway_audit.policy_decisions, candidate
        );
    END LOOP;

    FOREACH stamp IN ARRAY ARRAY[
        '2024-02-29T00:00:00Z',
        '0001-01-01T00:00:00Z',
        '9999-12-31T23:59:59Z'
    ] LOOP
        sequence := sequence + 1;
        candidate := baseline || jsonb_build_object(
            'event_id', '550e8400-e29b-41d4-a716-' || lpad(sequence::text, 12, '0'),
            'occurred_at', stamp
        );
        INSERT INTO gateway_audit.policy_decisions
        SELECT * FROM jsonb_populate_record(
            NULL::gateway_audit.policy_decisions, candidate
        );
    END LOOP;

    FOR patch IN SELECT value FROM jsonb_array_elements('[
        {"schema_version": 2},
        {"schema_version": true},
        {"event_type": "other_event"},
        {"event_id": "not-a-uuid"},
        {"event_id": "550E8400-e29b-41d4-a716-446655440000"},
        {"event_id": "550e8400-e29b-11d4-a716-446655440000"},
        {"event_id": "550e8400-e29b-41d4-0716-446655440000"},
        {"occurred_at": "2026-02-29T00:00:00Z"},
        {"occurred_at": "2026-04-31T00:00:00Z"},
        {"occurred_at": "0000-01-01T00:00:00Z"},
        {"occurred_at": "2026-10-07T24:00:00Z"},
        {"occurred_at": "2026-10-07T10:00:60Z"},
        {"occurred_at": "2026-10-07T10:00:00+00:00"},
        {"occurred_at": "2026-10-07T10:00:00Z\n"},
        {"principal_id": "other-reader"},
        {"decision": "UNKNOWN"},
        {"decision": "DENY"},
        {"reason_code": "UNKNOWN_REASON"},
        {"reason_code": "UNKNOWN_TOOL"},
        {"policy_version": "policy-core-v2"},
        {"execution_status": "executed"}
    ]'::jsonb) LOOP
        rejected := false;
        BEGIN
            INSERT INTO gateway_audit.policy_decisions
            SELECT * FROM jsonb_populate_record(
                NULL::gateway_audit.policy_decisions, baseline || patch
            );
        EXCEPTION WHEN integrity_constraint_violation OR data_exception THEN
            rejected := true;
        END;
        IF NOT rejected THEN
            RAISE EXCEPTION 'Invalid audit event accepted';
        END IF;
    END LOOP;

    FOR field IN SELECT jsonb_object_keys(baseline) LOOP
        rejected := false;
        BEGIN
            INSERT INTO gateway_audit.policy_decisions
            SELECT * FROM jsonb_populate_record(
                NULL::gateway_audit.policy_decisions,
                baseline || jsonb_build_object(field, NULL)
            );
        EXCEPTION WHEN not_null_violation THEN
            rejected := true;
        END;
        IF NOT rejected THEN
            RAISE EXCEPTION 'Null audit field accepted';
        END IF;
    END LOOP;

    rejected := false;
    BEGIN
        INSERT INTO gateway_audit.policy_decisions
        SELECT * FROM gateway_audit.policy_decisions LIMIT 1;
    EXCEPTION WHEN unique_violation THEN
        rejected := true;
    END;
    IF NOT rejected THEN
        RAISE EXCEPTION 'Duplicate audit identity accepted';
    END IF;

    FOREACH command IN ARRAY ARRAY[
        'UPDATE gateway_audit.policy_decisions SET decision = decision',
        'DELETE FROM gateway_audit.policy_decisions',
        'TRUNCATE gateway_audit.policy_decisions',
        'ALTER TABLE gateway_audit.policy_decisions ADD COLUMN forbidden text',
        'CREATE TABLE gateway_audit.forbidden (id integer)'
    ] LOOP
        rejected := false;
        BEGIN
            EXECUTE command;
        EXCEPTION WHEN insufficient_privilege THEN
            rejected := true;
        END;
        IF NOT rejected THEN
            RAISE EXCEPTION 'Writer exceeded its authority';
        END IF;
    END LOOP;

    IF (SELECT count(*) FROM gateway_audit.policy_decisions) <> 9 THEN
        RAISE EXCEPTION 'Unexpected accepted row count';
    END IF;
END;
$$;

SET LOCAL ROLE gateway_audit_reader;

DO $$
DECLARE
    command text;
    rejected boolean;
BEGIN
    IF (SELECT count(*) FROM gateway_audit.policy_decisions) <> 9 THEN
        RAISE EXCEPTION 'Reader cannot inspect expected events';
    END IF;

    FOREACH command IN ARRAY ARRAY[
        'INSERT INTO gateway_audit.policy_decisions SELECT * FROM gateway_audit.policy_decisions LIMIT 1',
        'UPDATE gateway_audit.policy_decisions SET decision = decision',
        'DELETE FROM gateway_audit.policy_decisions',
        'TRUNCATE gateway_audit.policy_decisions',
        'ALTER TABLE gateway_audit.policy_decisions ADD COLUMN forbidden text',
        'CREATE TABLE gateway_audit.forbidden (id integer)'
    ] LOOP
        rejected := false;
        BEGIN
            EXECUTE command;
        EXCEPTION WHEN insufficient_privilege THEN
            rejected := true;
        END;
        IF NOT rejected THEN
            RAISE EXCEPTION 'Reader exceeded its authority';
        END IF;
    END LOOP;
END;
$$;

ROLLBACK;

SELECT 'AUDIT_SCHEMA_SQL_ACCEPTANCE=PASS' AS result;
