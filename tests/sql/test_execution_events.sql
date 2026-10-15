-- Run only in the isolated database after migrations 001 through 003.
-- Invoke as the test administrator with psql -X -v ON_ERROR_STOP=1.
-- All policy, binding, and execution fixtures are rolled back.

BEGIN; -- Keep all later fixture writes inside a rollback boundary.

DO $$ -- Inspect the actual database structure.
DECLARE -- Hold values used by the checks.
    columns_found text[]; -- Capture column names in storage order.
    role_name text; -- Select each application role.
    privilege_name text; -- Select each forbidden privilege.
    index_name text; -- Select each partial unique index.
BEGIN -- Begin structural assertions.
    SELECT array_agg(attname::text ORDER BY attnum) -- Preserve column order.
    INTO columns_found -- Save the discovered column names.
    FROM pg_attribute -- Inspect PostgreSQL metadata.
    WHERE attrelid = 'gateway_audit.execution_events'::regclass -- Select the table.
        AND attnum > 0 AND NOT attisdropped; -- Exclude system and removed columns.

    IF columns_found IS DISTINCT FROM ARRAY[ -- Require the exact eleven fields.
        'schema_version', 'event_type', 'event_id', 'execution_id',
        'policy_event_id', 'occurred_at', 'principal_id', 'tool',
        'operation', 'status', 'reason_code' -- Match the Python event contract.
    ] THEN -- Reject schema drift.
        RAISE EXCEPTION 'Unexpected execution-event columns'; -- Fail acceptance.
    END IF; -- Finish the column-order check.

    IF EXISTS ( -- Reject unexpected types, nullability, or text collation.
        SELECT 1 FROM pg_attribute -- Inspect each real column.
        WHERE attrelid = 'gateway_audit.execution_events'::regclass -- Select the table.
            AND attnum > 0 AND NOT attisdropped -- Exclude system columns.
            AND ( -- Require every field's expected properties.
                NOT attnotnull -- No field may accept NULL.
                OR atttypid <> CASE WHEN attname = 'schema_version'
                    THEN 'integer'::regtype ELSE 'text'::regtype END -- Exact SQL types.
                OR (atttypid = 'text'::regtype -- Text must use the selected collation.
                    AND attcollation <> 'pg_catalog."C"'::regcollation)
            ) -- Finish invalid-column conditions.
    ) THEN -- Any mismatch violates the schema contract.
        RAISE EXCEPTION 'Unexpected execution-event column properties';
    END IF; -- Finish column-property checks.

    IF ( -- Verify ownership of the new table.
        SELECT pg_get_userbyid(relowner) FROM pg_class -- Resolve the owning role.
        WHERE oid = 'gateway_audit.execution_events'::regclass -- Select the table.
    ) IS DISTINCT FROM 'gateway_audit_owner' THEN -- Require the dedicated owner.
        RAISE EXCEPTION 'Unexpected execution-event owner'; -- Fail acceptance.
    END IF; -- Finish ownership verification.

    IF NOT EXISTS ( -- Require event_id as the table's primary key.
        SELECT 1 FROM pg_constraint -- Read the primary-key metadata.
        WHERE conrelid = 'gateway_audit.execution_events'::regclass -- Select the table.
            AND contype = 'p' AND conkey = ARRAY[3]::smallint[] -- Third column: event_id.
    ) THEN -- Reject a missing or different primary key.
        RAISE EXCEPTION 'Unexpected execution-event primary key';
    END IF; -- Finish the primary-key check.

    IF NOT EXISTS ( -- Require an immediate reference to the request binding.
        SELECT 1 FROM pg_constraint -- Inspect the foreign key.
        WHERE conrelid = 'gateway_audit.execution_events'::regclass -- Source table.
            AND contype = 'f' AND conkey = ARRAY[5]::smallint[] -- policy_event_id.
            AND confrelid = 'gateway_audit.request_bindings'::regclass -- Target table.
            AND confkey = ARRAY[2]::smallint[] -- Target policy_event_id column.
            AND NOT condeferrable AND NOT condeferred -- No deferred enforcement.
            AND confupdtype = 'a' AND confdeltype = 'a' -- NO ACTION in both directions.
            AND convalidated -- Require the relationship to be validated.
    ) THEN -- Reject a different relationship.
        RAISE EXCEPTION 'Unexpected execution-event foreign key';
    END IF; -- Finish the relationship check.

    FOREACH index_name IN ARRAY ARRAY[ -- Inspect both attempt-level indexes.
        'execution_events_one_start', 'execution_events_one_terminal'
    ] LOOP -- Check index identity, key, and readiness.
        IF NOT EXISTS ( -- Require a usable partial unique index on execution_id.
            SELECT 1 FROM pg_index AS i -- Read index properties.
            JOIN pg_class AS c ON c.oid = i.indexrelid -- Read the index name.
            WHERE i.indrelid = 'gateway_audit.execution_events'::regclass
                AND c.relname = index_name -- Match the required index.
                AND i.indisunique AND i.indisvalid AND i.indisready -- Enforce uniqueness.
                AND i.indnkeyatts = 1 AND i.indnatts = 1 -- One key, no included columns.
                AND i.indkey[0] = 4 -- execution_id is the fourth table column.
                AND i.indexprs IS NULL -- Index the column, not an expression.
                AND i.indpred IS NOT NULL -- Require a partial-index predicate.
        ) THEN -- Behavioral tests will check the exact status restrictions too.
            RAISE EXCEPTION 'Unexpected execution-event partial index';
        END IF; -- Finish this index check.
    END LOOP; -- Finish index inspection.

    IF EXISTS ( -- PUBLIC must have no privileges on the new table.
        SELECT 1 FROM pg_class AS c, -- Select the table's ACL.
            LATERAL aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) AS a
        WHERE c.oid = 'gateway_audit.execution_events'::regclass
            AND a.grantee = 0 -- Zero identifies PUBLIC.
    ) THEN -- Reject public access.
        RAISE EXCEPTION 'Unexpected PUBLIC execution-event privilege';
    END IF; -- Finish PUBLIC inspection.

    FOREACH role_name IN ARRAY ARRAY[ -- Inspect both application roles.
        'gateway_audit_writer', 'gateway_audit_reader'
    ] LOOP -- Check effective access, including inherited rights.
        IF NOT has_table_privilege( -- Both roles need inspection access.
            role_name, 'gateway_audit.execution_events', 'SELECT'
        ) THEN -- Missing SELECT violates the contract.
            RAISE EXCEPTION 'Missing execution-event read privilege';
        END IF; -- Finish SELECT verification.

        FOREACH privilege_name IN ARRAY ARRAY[ -- Neither role may mutate saved rows.
            'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'
        ] LOOP -- Check each forbidden table privilege.
            IF has_table_privilege( -- Inspect effective privilege.
                role_name, 'gateway_audit.execution_events', privilege_name
            ) THEN -- Reject excess authority.
                RAISE EXCEPTION 'Excess execution-event privilege';
            END IF; -- Finish the privilege check.
        END LOOP; -- Finish forbidden table privileges.

        FOREACH privilege_name IN ARRAY ARRAY[ -- Neither role may delegate access.
            'SELECT WITH GRANT OPTION', 'INSERT WITH GRANT OPTION',
            'UPDATE WITH GRANT OPTION', 'DELETE WITH GRANT OPTION',
            'TRUNCATE WITH GRANT OPTION', 'REFERENCES WITH GRANT OPTION',
            'TRIGGER WITH GRANT OPTION' -- Cover every table grant option.
        ] LOOP -- Check each delegation privilege.
            IF has_table_privilege( -- Include effective grant authority.
                role_name, 'gateway_audit.execution_events', privilege_name
            ) THEN -- Reject delegation rights.
                RAISE EXCEPTION 'Unexpected execution-event grant authority';
            END IF; -- Finish grant-authority verification.
        END LOOP; -- Finish delegation checks.
    END LOOP; -- Finish role inspection.

    IF NOT has_table_privilege( -- Writer must be able to append.
        'gateway_audit_writer', 'gateway_audit.execution_events', 'INSERT'
    ) OR has_table_privilege( -- Reader must not be able to append.
        'gateway_audit_reader', 'gateway_audit.execution_events', 'INSERT'
    ) THEN -- Reject either access mismatch.
        RAISE EXCEPTION 'Unexpected execution-event insert privileges';
    END IF; -- Finish INSERT verification.
END; -- Finish structural assertions.
$$; -- Execute the inspection block.

SET LOCAL ROLE gateway_audit_writer; -- Exercise actual writer permissions.

INSERT INTO gateway_audit.policy_decisions ( -- Create a valid fixture parent.
    schema_version, event_type, event_id, occurred_at, principal_id,
    decision, reason_code, policy_version, execution_status
) VALUES ( -- This event remains non-executing.
    1, 'policy_decision', '770e8400-e29b-41d4-a716-446655440000',
    '2026-10-15T12:00:00Z', 'reliability-reader', 'ALLOW',
    'POLICY_ALLOW', 'policy-core-v1', 'not_executed'
); -- Save only inside the enclosing rollback boundary.

INSERT INTO gateway_audit.request_bindings ( -- Provide the required binding.
    schema_version, policy_event_id, principal_id, tool, operation, item_id
) VALUES ( -- Link the synthetic request to the parent decision.
    1, '770e8400-e29b-41d4-a716-446655440000',
    'reliability-reader', 'fixture_store', 'READ_ITEM', 42
); -- Execution records can now reference this fixture.

DO $$ -- Test values and uniqueness using real inserts.
DECLARE -- Hold fixtures and rejection diagnostics.
    baseline jsonb := '{
        "schema_version": 1,
        "event_type": "simulator_execution",
        "event_id": "880e8400-e29b-41d4-a716-000000000000",
        "execution_id": "990e8400-e29b-41d4-a716-000000000000",
        "policy_event_id": "770e8400-e29b-41d4-a716-446655440000",
        "occurred_at": "2026-10-15T12:00:00Z",
        "principal_id": "reliability-reader",
        "tool": "fixture_store",
        "operation": "READ_ITEM",
        "status": "started",
        "reason_code": "SIMULATION_STARTED"
    }'; -- Use an initially uninserted record and attempt identifier.
    candidate jsonb; -- Hold the next insert.
    test_case jsonb; -- Hold an invalid patch and expected constraint.
    patch jsonb; -- Select the fields to change.
    expected_constraint text; -- Identify the rule that should reject the row.
    actual_constraint text; -- Read the constraint that actually rejected it.
    field_name text; -- Iterate record fields.
    stamp text; -- Select valid and invalid timestamps.
    status_value text; -- Select a status for combination testing.
    reason_value text; -- Select a reason for combination testing.
    identifier text; -- Select an invalid UUID.
    sequence integer := 0; -- Give accepted records independent identifiers.
    rejected boolean; -- Track expected failure.
BEGIN -- Begin value acceptance checks.
    FOR patch IN SELECT value FROM jsonb_array_elements('[
        {"status":"started","reason_code":"SIMULATION_STARTED"},
        {"status":"completed","reason_code":"SIMULATION_COMPLETED"},
        {"status":"failed","reason_code":"SIMULATOR_ERROR"},
        {"status":"failed","reason_code":"INVALID_RESULT"}
    ]'::jsonb) LOOP -- Accept each supported status/reason pair independently.
        sequence := sequence + 1; -- Avoid accidental uniqueness conflicts.
        candidate := baseline || patch || jsonb_build_object(
            'event_id', '880e8400-e29b-41d4-a716-' || lpad(sequence::text, 12, '0'),
            'execution_id', '990e8400-e29b-41d4-a716-' || lpad(sequence::text, 12, '0')
        ); -- Assign this case its own record and attempt.
        INSERT INTO gateway_audit.execution_events -- Exercise table constraints.
        SELECT * FROM jsonb_populate_record(
            NULL::gateway_audit.execution_events, candidate
        ); -- Terminal-only rows demonstrate that ordering is not enforced here.
    END LOOP; -- Finish supported combinations.

    FOREACH stamp IN ARRAY ARRAY[ -- Check calendar boundaries and leap-year validity.
        '2024-02-29T00:00:00Z', '0001-01-01T00:00:00Z',
        '9999-12-31T23:59:59Z'
    ] LOOP -- Each valid timestamp receives a fresh attempt.
        sequence := sequence + 1; -- Avoid duplicate identifiers.
        candidate := baseline || jsonb_build_object(
            'event_id', '880e8400-e29b-41d4-a716-' || lpad(sequence::text, 12, '0'),
            'execution_id', '990e8400-e29b-41d4-a716-' || lpad(sequence::text, 12, '0'),
            'occurred_at', stamp
        ); -- Keep all other fields valid.
        INSERT INTO gateway_audit.execution_events -- Require successful insertion.
        SELECT * FROM jsonb_populate_record(
            NULL::gateway_audit.execution_events, candidate
        ); -- Accept the calendar-valid record.
    END LOOP; -- Finish valid timestamps.

    FOR test_case IN SELECT value FROM jsonb_array_elements('[
        {"patch":{"schema_version":2},"constraint":"execution_schema_version"},
        {"patch":{"event_type":"other"},"constraint":"execution_event_type"},
        {"patch":{"principal_id":"other-reader"},"constraint":"execution_principal"},
        {"patch":{"tool":"other-store"},"constraint":"execution_tool"},
        {"patch":{"operation":"WRITE_ITEM"},"constraint":"execution_operation"}
    ]'::jsonb) LOOP -- Require each fixed-value check to reject its own bad value.
        rejected := false; -- Reset the expected rejection.
        BEGIN -- Isolate the failed insert.
            INSERT INTO gateway_audit.execution_events
            SELECT * FROM jsonb_populate_record(
                NULL::gateway_audit.execution_events, baseline || (test_case->'patch')
            ); -- Use unused baseline identifiers to avoid masking constraint failures.
        EXCEPTION WHEN check_violation THEN -- Require a CHECK failure.
            GET STACKED DIAGNOSTICS actual_constraint = CONSTRAINT_NAME;
            rejected := actual_constraint = test_case->>'constraint'; -- Check its cause.
        END; -- Undo the attempted row.
        IF NOT rejected THEN -- An unrelated rejection must not count as success.
            RAISE EXCEPTION 'Execution fixed-value constraint failed acceptance';
        END IF; -- Finish this case.
    END LOOP; -- Finish fixed-value checks.

    FOREACH field_name IN ARRAY ARRAY[ -- Test each identifier independently.
        'event_id', 'execution_id', 'policy_event_id'
    ] LOOP -- Require the corresponding format constraint.
        expected_constraint := CASE field_name -- Select the expected CHECK name.
            WHEN 'event_id' THEN 'execution_event_id'
            WHEN 'execution_id' THEN 'execution_attempt_id'
            ELSE 'execution_policy_id' END; -- Match the migration's constraint names.
        FOREACH identifier IN ARRAY ARRAY[ -- Try malformed and noncanonical forms.
            '', 'not-a-uuid', 'AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA',
            '880e8400e29b41d4a716000000000000',
            '{880e8400-e29b-41d4-a716-000000000000}',
            '880e8400-e29b-11d4-a716-000000000000',
            '880e8400-e29b-41d4-0716-000000000000'
        ] LOOP -- Verify format checks rather than relying on absent references.
            rejected := false; -- Reset this candidate.
            BEGIN -- Isolate the invalid insert.
                INSERT INTO gateway_audit.execution_events
                SELECT * FROM jsonb_populate_record(
                    NULL::gateway_audit.execution_events,
                    baseline || jsonb_build_object(field_name, identifier)
                ); -- Change just the selected UUID.
            EXCEPTION WHEN check_violation THEN -- Require its named format CHECK.
                GET STACKED DIAGNOSTICS actual_constraint = CONSTRAINT_NAME;
                rejected := actual_constraint = expected_constraint;
            END; -- Undo the candidate.
            IF NOT rejected THEN -- Wrong constraint or acceptance must fail the suite.
                RAISE EXCEPTION 'Execution UUID constraint failed acceptance';
            END IF; -- Finish the UUID case.
        END LOOP; -- Finish invalid UUID forms.
    END LOOP; -- Finish identifier fields.

    FOREACH stamp IN ARRAY ARRAY[ -- Reject invalid calendars and timestamp formats.
        '2026-02-29T00:00:00Z', '2026-04-31T00:00:00Z',
        '0000-01-01T00:00:00Z', '2026-10-15T24:00:00Z',
        '2026-10-15T12:60:00Z', '2026-10-15T12:00:60Z',
        '2026-10-15T12:00:00+00:00', '2026-10-15T12:00:00.000Z',
        E'2026-10-15T12:00:00Z\n'
    ] LOOP -- Calendar construction may itself raise a data exception.
        rejected := false; -- Reset this timestamp check.
        BEGIN -- Isolate the invalid timestamp.
            INSERT INTO gateway_audit.execution_events
            SELECT * FROM jsonb_populate_record(
                NULL::gateway_audit.execution_events,
                baseline || jsonb_build_object('occurred_at', stamp)
            ); -- Keep all other values valid.
        EXCEPTION WHEN check_violation THEN -- Check the timestamp constraint identity.
            GET STACKED DIAGNOSTICS actual_constraint = CONSTRAINT_NAME;
            rejected := actual_constraint = 'execution_occurred_at';
        WHEN data_exception THEN -- Impossible dates may fail inside make_date.
            rejected := true; -- Reject the invalid calendar value.
        END; -- Undo the candidate.
        IF NOT rejected THEN -- Any accepted invalid timestamp is a failure.
            RAISE EXCEPTION 'Execution timestamp accepted invalid data';
        END IF; -- Finish this timestamp.
    END LOOP; -- Finish invalid timestamps.

    FOREACH status_value IN ARRAY ARRAY[ -- Check the status/reason cross-product.
        'started', 'completed', 'failed', 'unknown'
    ] LOOP -- Include unsupported status text.
        FOREACH reason_value IN ARRAY ARRAY[
            'SIMULATION_STARTED', 'SIMULATION_COMPLETED',
            'SIMULATOR_ERROR', 'INVALID_RESULT', 'UNKNOWN'
        ] LOOP -- Include unsupported reason text.
            IF (status_value = 'started' AND reason_value = 'SIMULATION_STARTED')
                OR (status_value = 'completed' AND reason_value = 'SIMULATION_COMPLETED')
                OR (status_value = 'failed' AND reason_value IN (
                    'SIMULATOR_ERROR', 'INVALID_RESULT'
                )) THEN -- Supported combinations were tested above.
                CONTINUE; -- Only invalid combinations belong in this rejection loop.
            END IF; -- Finish valid-pair filtering.
            rejected := false; -- Reset this pair.
            BEGIN -- Isolate the invalid status/reason insertion.
                INSERT INTO gateway_audit.execution_events
                SELECT * FROM jsonb_populate_record(
                    NULL::gateway_audit.execution_events,
                    baseline || jsonb_build_object(
                        'status', status_value, 'reason_code', reason_value
                    )
                ); -- Use otherwise valid fields.
            EXCEPTION WHEN check_violation THEN -- Require the status/reason CHECK.
                GET STACKED DIAGNOSTICS actual_constraint = CONSTRAINT_NAME;
                rejected := actual_constraint = 'execution_status_reason';
            END; -- Undo the candidate.
            IF NOT rejected THEN -- Reject missing or incorrect enforcement.
                RAISE EXCEPTION 'Execution status/reason constraint failed acceptance';
            END IF; -- Finish this combination.
        END LOOP; -- Finish reason values.
    END LOOP; -- Finish statuses.

    FOR field_name IN SELECT jsonb_object_keys(baseline) LOOP -- Check every NULL field.
        rejected := false; -- Require NOT NULL enforcement.
        BEGIN -- Isolate the attempted row.
            INSERT INTO gateway_audit.execution_events
            SELECT * FROM jsonb_populate_record(
                NULL::gateway_audit.execution_events,
                baseline || jsonb_build_object(field_name, NULL)
            ); -- Change only this field.
        EXCEPTION WHEN not_null_violation THEN -- Require the intended failure category.
            rejected := true; -- Record the expected denial.
        END; -- Undo the attempted insert.
        IF NOT rejected THEN -- NULL must never be accepted.
            RAISE EXCEPTION 'Null execution-event field accepted';
        END IF; -- Finish the field.
    END LOOP; -- Finish NULL checks.

    rejected := false; -- Require referential existence beyond UUID format.
    BEGIN -- Attempt a well-formed but absent binding reference.
        INSERT INTO gateway_audit.execution_events
        SELECT * FROM jsonb_populate_record(
            NULL::gateway_audit.execution_events,
            baseline || '{"policy_event_id":"770e8400-e29b-41d4-a716-999999999999"}'::jsonb
        ); -- The referenced binding does not exist.
    EXCEPTION WHEN foreign_key_violation THEN -- Require the intended foreign key.
        GET STACKED DIAGNOSTICS actual_constraint = CONSTRAINT_NAME;
        rejected := actual_constraint = 'execution_request_binding';
    END; -- Undo the attempted row.
    IF NOT rejected THEN -- Missing references must fail.
        RAISE EXCEPTION 'Missing execution binding reference accepted';
    END IF; -- Finish relationship rejection.

    INSERT INTO gateway_audit.execution_events -- Create the baseline start record.
    SELECT * FROM jsonb_populate_record(
        NULL::gateway_audit.execution_events, baseline
    ); -- The baseline identifiers have not previously been accepted.

    rejected := false; -- A record identifier must remain unique.
    BEGIN -- Retry the same record with a different attempt to isolate the primary key.
        INSERT INTO gateway_audit.execution_events
        SELECT * FROM jsonb_populate_record(
            NULL::gateway_audit.execution_events,
            baseline || '{"execution_id":"990e8400-e29b-41d4-a716-999999999999"}'::jsonb
        ); -- Avoid a start-index collision.
    EXCEPTION WHEN unique_violation THEN -- Require the event primary key.
        GET STACKED DIAGNOSTICS actual_constraint = CONSTRAINT_NAME;
        rejected := actual_constraint = 'execution_events_pkey';
    END; -- Undo the duplicate.
    IF NOT rejected THEN -- Duplicate event identities must fail.
        RAISE EXCEPTION 'Duplicate execution event accepted';
    END IF; -- Finish event-identity verification.

    rejected := false; -- A second start must fail even with a fresh event ID.
    BEGIN -- Reuse the attempt, not the record identifier.
        INSERT INTO gateway_audit.execution_events
        SELECT * FROM jsonb_populate_record(
            NULL::gateway_audit.execution_events,
            baseline || '{"event_id":"880e8400-e29b-41d4-a716-999999999990"}'::jsonb
        ); -- The baseline start already occupies this attempt.
    EXCEPTION WHEN unique_violation THEN -- Require the partial start index.
        GET STACKED DIAGNOSTICS actual_constraint = CONSTRAINT_NAME;
        rejected := actual_constraint = 'execution_events_one_start';
    END; -- Undo the second start.
    IF NOT rejected THEN -- Distinct event IDs must not bypass the start limit.
        RAISE EXCEPTION 'Duplicate execution start accepted';
    END IF; -- Finish start uniqueness.

    FOR patch IN SELECT value FROM jsonb_array_elements('[
        {"status":"completed","reason_code":"SIMULATION_COMPLETED"},
        {"status":"failed","reason_code":"SIMULATOR_ERROR"},
        {"status":"failed","reason_code":"INVALID_RESULT"}
    ]'::jsonb) LOOP -- Check each terminal state as the first saved outcome.
        sequence := sequence + 1; -- Assign a distinct terminal-only attempt.
        candidate := baseline || patch || jsonb_build_object(
            'event_id', '880e8400-e29b-41d4-a716-' || lpad(sequence::text, 12, '0'),
            'execution_id', '990e8400-e29b-41d4-a716-' || lpad(sequence::text, 12, '0')
        ); -- Keep this outcome independent of earlier fixtures.
        INSERT INTO gateway_audit.execution_events
        SELECT * FROM jsonb_populate_record(
            NULL::gateway_audit.execution_events, candidate
        ); -- Accept the first terminal outcome.
        FOREACH reason_value IN ARRAY ARRAY[
            'SIMULATION_COMPLETED', 'SIMULATOR_ERROR', 'INVALID_RESULT'
        ] LOOP -- Reject every possible second terminal outcome.
            rejected := false; -- Reset this competing outcome.
            BEGIN -- Isolate the duplicate-attempt insert.
                INSERT INTO gateway_audit.execution_events
                SELECT * FROM jsonb_populate_record(
                    NULL::gateway_audit.execution_events,
                    candidate || jsonb_build_object(
                        'event_id', '880e8400-e29b-41d4-a716-999999999991',
                        'status', CASE WHEN reason_value = 'SIMULATION_COMPLETED'
                            THEN 'completed' ELSE 'failed' END,
                        'reason_code', reason_value
                    )
                ); -- Use a fresh record ID to isolate terminal-index enforcement.
            EXCEPTION WHEN unique_violation THEN -- Require the terminal partial index.
                GET STACKED DIAGNOSTICS actual_constraint = CONSTRAINT_NAME;
                rejected := actual_constraint = 'execution_events_one_terminal';
            END; -- Undo the competing outcome.
            IF NOT rejected THEN -- Never accept two final outcomes for one attempt.
                RAISE EXCEPTION 'Competing execution terminal outcome accepted';
            END IF; -- Finish this competing pair.
        END LOOP; -- Finish alternate terminal outcomes.
    END LOOP; -- Finish first-terminal variants.

    INSERT INTO gateway_audit.execution_events -- Allow one start and one terminal together.
    SELECT * FROM jsonb_populate_record(
        NULL::gateway_audit.execution_events,
        baseline || '{"event_id":"880e8400-e29b-41d4-a716-999999999992",
            "status":"completed","reason_code":"SIMULATION_COMPLETED"}'::jsonb
    ); -- Reuse the baseline attempt, which currently has only its start.

    IF (SELECT count(*) FROM gateway_audit.execution_events) <> 12 THEN
        RAISE EXCEPTION 'Unexpected execution-event fixture count'; -- Catch stray inserts.
    END IF; -- Verify all intended records and no rejected candidates survived.
END; -- Finish data and uniqueness tests.
$$; -- Execute the behavior block.

RESET ROLE; -- Restore administrator context before switching application roles.

DO $$ -- Exercise permissions through actual role operations.
DECLARE -- Hold the selected role, command, and expected rejection.
    role_name text; -- Select writer or reader.
    command text; -- Select a prohibited operation.
    rejected boolean; -- Record the expected permission failure.
BEGIN -- Begin application-role checks.
    FOREACH role_name IN ARRAY ARRAY[ -- Exercise both application roles.
        'gateway_audit_writer', 'gateway_audit_reader'
    ] LOOP -- Check actual behavior under each role.
        EXECUTE format('SET LOCAL ROLE %I', role_name); -- Switch safely by identifier.
        IF (SELECT count(*) FROM gateway_audit.execution_events) <> 12 THEN
            RAISE EXCEPTION 'Role cannot read execution events'; -- Verify SELECT.
        END IF; -- Finish read-access verification.

        FOREACH command IN ARRAY ARRAY[ -- Neither role may rewrite saved history.
            'UPDATE gateway_audit.execution_events SET status = status',
            'DELETE FROM gateway_audit.execution_events',
            'TRUNCATE gateway_audit.execution_events',
            'ALTER TABLE gateway_audit.execution_events ADD COLUMN forbidden text',
            'CREATE TABLE gateway_audit.forbidden_execution (id integer)'
        ] LOOP -- Attempt each forbidden operation.
            rejected := false; -- Require permission denial for this command.
            BEGIN -- Isolate the expected failure.
                EXECUTE command; -- Run under the selected application role.
            EXCEPTION WHEN insufficient_privilege THEN -- Reject for authority reasons.
                rejected := true; -- Record the intended failure.
            END; -- Undo the failed command.
            IF NOT rejected THEN -- A successful command violates the contract.
                RAISE EXCEPTION 'Role exceeded execution-event authority';
            END IF; -- Finish this operation.
        END LOOP; -- Finish shared forbidden operations.

        IF role_name = 'gateway_audit_reader' THEN -- Reader must also reject appends.
            rejected := false; -- Require permission denial, not duplicate-key failure.
            BEGIN -- Isolate the attempted reader insertion.
                INSERT INTO gateway_audit.execution_events -- Attempt an actual write.
                SELECT * FROM gateway_audit.execution_events LIMIT 1;
            EXCEPTION WHEN insufficient_privilege THEN -- Require INSERT denial.
                rejected := true; -- Record the expected rejection.
            END; -- Finish the attempted write.
            IF NOT rejected THEN -- Reader must never receive append authority.
                RAISE EXCEPTION 'Reader can insert execution events';
            END IF; -- Finish reader-specific verification.
        END IF; -- Finish role-specific tests.
        EXECUTE 'RESET ROLE'; -- Restore administrator context before the next role.
    END LOOP; -- Finish both application roles.
END; -- Finish permission behavior tests.
$$; -- Execute the permission block.

ROLLBACK; -- Remove every fixture from all three tables.

SELECT 'EXECUTION_EVENT_SQL_ACCEPTANCE=PASS' AS result; -- Report completed acceptance.
