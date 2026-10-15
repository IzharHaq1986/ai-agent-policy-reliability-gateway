-- Run after migrations 001 and 002 in the isolated test database.
-- Invoke as the test administrator with psql -X -v ON_ERROR_STOP=1.
-- All inserted policy and binding rows are rolled back.

BEGIN; -- Keep every test row inside one rollback boundary.

DO $$ -- Inspect the actual table definition.
DECLARE -- Declare values used for schema inspection.
    columns_found text[]; -- Capture columns in their stored order.
    role_name text; -- Check each application role.
    privilege_name text; -- Check forbidden privileges individually.
BEGIN -- Begin schema assertions.
    SELECT array_agg(attname::text ORDER BY attnum) -- Preserve column order.
    INTO columns_found -- Save the discovered columns.
    FROM pg_attribute -- Read PostgreSQL's column catalog.
    WHERE attrelid = 'gateway_audit.request_bindings'::regclass -- Select this table.
        AND attnum > 0 AND NOT attisdropped; -- Ignore system and removed columns.

    IF columns_found IS DISTINCT FROM ARRAY[ -- Require exactly the supported fields.
        'schema_version', 'policy_event_id', 'principal_id', -- Identify format and caller.
        'tool', 'operation', 'item_id' -- Identify the requested activity.
    ] THEN -- Reject unexpected schema changes.
        RAISE EXCEPTION 'Unexpected binding columns'; -- Fail the acceptance run.
    END IF; -- Finish the column check.

    IF EXISTS ( -- Reject nullable columns or incorrect storage types.
        SELECT 1 FROM pg_attribute -- Inspect each stored column.
        WHERE attrelid = 'gateway_audit.request_bindings'::regclass -- Select this table.
            AND attnum > 0 AND NOT attisdropped -- Ignore system columns.
            AND ( -- Require exact nullability and type.
                NOT attnotnull -- Every field must reject NULL.
                OR atttypid <> CASE -- Select the expected PostgreSQL type.
                    WHEN attname IN ('schema_version', 'item_id') -- Numeric fields.
                    THEN 'integer'::regtype ELSE 'text'::regtype END -- Remaining text.
                OR (atttypid = 'text'::regtype -- Text comparisons must use C collation.
                    AND attcollation <> 'pg_catalog."C"'::regcollation)
            ) -- Finish invalid-column conditions.
    ) THEN -- Fail if any field violates the contract.
        RAISE EXCEPTION 'Unexpected binding type or nullability'; -- Report failure.
    END IF; -- Finish column-property checks.

    IF ( -- Require the existing dedicated owner.
        SELECT pg_get_userbyid(relowner) FROM pg_class -- Resolve table ownership.
        WHERE oid = 'gateway_audit.request_bindings'::regclass -- Select the table.
    ) IS DISTINCT FROM 'gateway_audit_owner' THEN -- Reject unexpected ownership.
        RAISE EXCEPTION 'Unexpected binding owner'; -- Fail the run.
    END IF; -- Finish ownership verification.

    IF NOT EXISTS ( -- Require the exact primary-key column.
        SELECT 1 FROM pg_constraint -- Read the actual constraint definition.
        WHERE conrelid = 'gateway_audit.request_bindings'::regclass -- Select the table.
            AND contype = 'p' AND conkey = ARRAY[2]::smallint[] -- Second column is key.
    ) THEN -- Reject a missing or different primary key.
        RAISE EXCEPTION 'Unexpected binding primary key'; -- Fail the run.
    END IF; -- Finish primary-key verification.

    IF NOT EXISTS ( -- Require the intended immediate foreign key.
        SELECT 1 FROM pg_constraint -- Inspect the relationship metadata.
        WHERE conrelid = 'gateway_audit.request_bindings'::regclass -- Source table.
            AND contype = 'f' AND conkey = ARRAY[2]::smallint[] -- Source key column.
            AND confrelid = 'gateway_audit.policy_decisions'::regclass -- Target table.
            AND confkey = ARRAY[3]::smallint[] -- Target event_id column.
            AND NOT condeferrable AND NOT condeferred -- No deferred checking.
            AND confupdtype = 'a' AND confdeltype = 'a' -- NO ACTION in both directions.
            AND convalidated -- Require validation against existing rows.
    ) THEN -- Reject a different relationship.
        RAISE EXCEPTION 'Unexpected binding foreign key'; -- Fail the run.
    END IF; -- Finish foreign-key verification.

    IF EXISTS ( -- Reject any PUBLIC table privilege.
        SELECT 1 FROM pg_class AS c, -- Select table metadata.
            LATERAL aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) AS a
        WHERE c.oid = 'gateway_audit.request_bindings'::regclass -- Select this table.
            AND a.grantee = 0 -- PUBLIC has the special identifier zero.
    ) THEN -- PUBLIC must have no access.
        RAISE EXCEPTION 'Unexpected PUBLIC binding privilege'; -- Fail the run.
    END IF; -- Finish PUBLIC permission checks.

    FOREACH role_name IN ARRAY ARRAY[ -- Inspect both application roles.
        'gateway_audit_writer', 'gateway_audit_reader' -- Owner privileges are separate.
    ] LOOP -- Check each role's effective privileges.
        IF NOT has_table_privilege( -- Both roles must be able to inspect records.
            role_name, 'gateway_audit.request_bindings', 'SELECT'
        ) THEN -- Missing SELECT would break expected lookup behavior.
            RAISE EXCEPTION 'Missing binding read privilege'; -- Fail the run.
        END IF; -- Finish read-access verification.
        FOREACH privilege_name IN ARRAY ARRAY[ -- Check all forbidden table rights.
            'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER' -- No mutation or DDL aid.
        ] LOOP -- Inspect one privilege at a time.
            IF has_table_privilege( -- Include inherited effective privileges.
                role_name, 'gateway_audit.request_bindings', privilege_name
            ) THEN -- Neither application role may have this right.
                RAISE EXCEPTION 'Excess binding privilege'; -- Fail the run.
            END IF; -- Finish this forbidden-privilege check.
        END LOOP; -- Finish forbidden privileges.
        FOREACH privilege_name IN ARRAY ARRAY[ -- Neither role may delegate table rights.
            'SELECT WITH GRANT OPTION', 'INSERT WITH GRANT OPTION',
            'UPDATE WITH GRANT OPTION', 'DELETE WITH GRANT OPTION',
            'TRUNCATE WITH GRANT OPTION', 'REFERENCES WITH GRANT OPTION',
            'TRIGGER WITH GRANT OPTION' -- Check every supported table grant option.
        ] LOOP -- Inspect each delegation right.
            IF has_table_privilege( -- Check effective grant authority.
                role_name, 'gateway_audit.request_bindings', privilege_name
            ) THEN -- Delegation is outside both roles' scope.
                RAISE EXCEPTION 'Unexpected binding grant authority'; -- Fail the run.
            END IF; -- Finish grant-option check.
        END LOOP; -- Finish delegation checks.
    END LOOP; -- Finish application-role inspection.

    IF NOT has_table_privilege( -- Writer needs append access.
        'gateway_audit_writer', 'gateway_audit.request_bindings', 'INSERT'
    ) OR has_table_privilege( -- Reader must not have append access.
        'gateway_audit_reader', 'gateway_audit.request_bindings', 'INSERT'
    ) THEN -- Reject either permission mismatch.
        RAISE EXCEPTION 'Unexpected binding insert privileges'; -- Fail the run.
    END IF; -- Finish INSERT permission checks.
END; -- Finish structural assertions.
$$; -- Execute the inspection block.

SET LOCAL ROLE gateway_audit_writer; -- Exercise real writer permissions.

DO $$ -- Check stored values and referential behavior.
DECLARE -- Define reusable valid data and test variables.
    policy jsonb := '{
        "schema_version": 1,
        "event_type": "policy_decision",
        "event_id": "660e8400-e29b-41d4-a716-446655440000",
        "occurred_at": "2026-10-15T12:00:00Z",
        "principal_id": "reliability-reader",
        "decision": "ALLOW",
        "reason_code": "POLICY_ALLOW",
        "policy_version": "policy-core-v1",
        "execution_status": "not_executed"
    }'; -- Finish the parent-event fixture.
    baseline jsonb := '{
        "schema_version": 1,
        "policy_event_id": "660e8400-e29b-41d4-a716-446655440000",
        "principal_id": "reliability-reader",
        "tool": "fixture_store",
        "operation": "READ_ITEM",
        "item_id": 42
    }'; -- Finish the binding fixture.
    patch jsonb; -- Hold one invalid-field change.
    candidate jsonb; -- Hold a candidate binding.
    field_name text; -- Select each nullable test field.
    identifier text; -- Keep each valid fixture pair distinct.
    item integer; -- Select boundary and representative items.
    sequence integer := 0; -- Generate distinct synthetic identifier suffixes.
    rejected boolean; -- Remember whether an expected rejection occurred.
BEGIN -- Begin writer behavior checks.
    FOREACH item IN ARRAY ARRAY[1, 42, 1000] LOOP -- Test supported item boundaries.
        sequence := sequence + 1; -- Advance the fixture identifier.
        identifier := '660e8400-e29b-41d4-a716-' || lpad(sequence::text, 12, '0');
        INSERT INTO gateway_audit.policy_decisions -- Create the referenced parent first.
        SELECT * FROM jsonb_populate_record( -- Convert fixture values into table columns.
            NULL::gateway_audit.policy_decisions,
            policy || jsonb_build_object('event_id', identifier)
        ); -- Save a valid parent event.
        INSERT INTO gateway_audit.request_bindings -- Append a matching binding.
        SELECT * FROM jsonb_populate_record( -- Match the parent and selected item.
            NULL::gateway_audit.request_bindings,
            baseline || jsonb_build_object('policy_event_id', identifier, 'item_id', item)
        ); -- Save a valid child row.
    END LOOP; -- Finish valid binding cases.

    INSERT INTO gateway_audit.policy_decisions -- Create a parent for rejection cases.
    SELECT * FROM jsonb_populate_record(NULL::gateway_audit.policy_decisions, policy);

    FOR patch IN SELECT value FROM jsonb_array_elements('[
        {"schema_version": 2},
        {"schema_version": true},
        {"principal_id": "other-reader"},
        {"tool": "other-store"},
        {"operation": "WRITE_ITEM"},
        {"item_id": 0},
        {"item_id": 1001},
        {"item_id": true},
        {"policy_event_id": "not-a-uuid"},
        {"policy_event_id": "660e8400-e29b-41d4-a716-999999999999"}
    ]'::jsonb) LOOP -- Exercise invalid values and absent references.
        rejected := false; -- Require each candidate to fail independently.
        BEGIN -- Isolate the failed insert in a subtransaction.
            INSERT INTO gateway_audit.request_bindings -- Attempt the invalid candidate.
            SELECT * FROM jsonb_populate_record( -- Patch only the selected fields.
                NULL::gateway_audit.request_bindings, baseline || patch
            ); -- Valid-looking identifiers must still reference an existing event.
        EXCEPTION WHEN integrity_constraint_violation OR data_exception THEN
            rejected := true; -- Accept only an expected data or constraint failure.
        END; -- Roll back the failed candidate automatically.
        IF NOT rejected THEN -- Unexpected acceptance must fail the suite.
            RAISE EXCEPTION 'Invalid binding accepted'; -- Report the broken rule.
        END IF; -- Finish this candidate.
    END LOOP; -- Finish invalid-value tests.

    FOR field_name IN SELECT jsonb_object_keys(baseline) LOOP -- Check every NULL field.
        rejected := false; -- Require a NOT NULL failure.
        BEGIN -- Isolate this failed insert.
            INSERT INTO gateway_audit.request_bindings -- Attempt a NULL field.
            SELECT * FROM jsonb_populate_record( -- Replace one field with NULL.
                NULL::gateway_audit.request_bindings,
                baseline || jsonb_build_object(field_name, NULL)
            ); -- Other fields remain valid.
        EXCEPTION WHEN not_null_violation THEN -- Require the specific null constraint.
            rejected := true; -- Record the expected rejection.
        END; -- Finish the attempted insert.
        IF NOT rejected THEN -- Reject a schema that accepts NULL.
            RAISE EXCEPTION 'Null binding field accepted'; -- Fail the run.
        END IF; -- Finish this field.
    END LOOP; -- Finish NULL checks.

    rejected := false; -- Require duplicate identity rejection.
    BEGIN -- Isolate the duplicate insertion.
        INSERT INTO gateway_audit.request_bindings -- Copy an existing binding.
        SELECT * FROM gateway_audit.request_bindings LIMIT 1; -- Reuse its primary key.
    EXCEPTION WHEN unique_violation THEN -- Require the primary-key failure.
        rejected := true; -- Record the expected rejection.
    END; -- Finish the duplicate attempt.
    IF NOT rejected THEN -- Duplicate identities must never be accepted.
        RAISE EXCEPTION 'Duplicate binding accepted'; -- Fail the run.
    END IF; -- Finish duplicate verification.

    IF (SELECT count(*) FROM gateway_audit.request_bindings) <> 3 THEN
        RAISE EXCEPTION 'Unexpected binding row count'; -- Catch unintended inserts.
    END IF; -- Finish row-count verification.
END; -- Finish writer data tests.
$$; -- Execute the writer block.

RESET ROLE; -- Return to the test administrator for controlled role switching.

DO $$ -- Exercise forbidden operations under both application roles.
DECLARE -- Hold the role, command, and rejection state.
    role_name text; -- Select the role under test.
    command text; -- Select a forbidden operation.
    rejected boolean; -- Record expected permission failures.
BEGIN -- Begin permission behavior tests.
    FOREACH role_name IN ARRAY ARRAY[ -- Test writer and reader separately.
        'gateway_audit_writer', 'gateway_audit_reader'
    ] LOOP -- Apply actual role permissions to each operation.
        EXECUTE format('SET LOCAL ROLE %I', role_name); -- Switch safely by identifier.
        IF (SELECT count(*) FROM gateway_audit.request_bindings) <> 3 THEN
            RAISE EXCEPTION 'Role cannot read bindings'; -- Verify actual SELECT access.
        END IF; -- Finish read verification.
        FOREACH command IN ARRAY ARRAY[ -- Neither role may rewrite or manage the table.
            'UPDATE gateway_audit.request_bindings SET item_id = item_id',
            'DELETE FROM gateway_audit.request_bindings',
            'TRUNCATE gateway_audit.request_bindings',
            'ALTER TABLE gateway_audit.request_bindings ADD COLUMN forbidden text',
            'CREATE TABLE gateway_audit.forbidden_binding (id integer)'
        ] LOOP -- Test each prohibited command.
            rejected := false; -- Require an actual permission failure.
            BEGIN -- Isolate the expected rejection.
                EXECUTE command; -- Attempt the operation as the selected role.
            EXCEPTION WHEN insufficient_privilege THEN -- Accept only permission denial.
                rejected := true; -- Record the expected failure.
            END; -- Finish the attempted operation.
            IF NOT rejected THEN -- Any successful forbidden command is a failure.
                RAISE EXCEPTION 'Role exceeded binding authority'; -- Fail the run.
            END IF; -- Finish the authority check.
        END LOOP; -- Finish common forbidden operations.
        IF role_name = 'gateway_audit_reader' THEN -- Reader must also reject INSERT.
            rejected := false; -- Require permission denial rather than a duplicate error.
            BEGIN -- Isolate the attempted write.
                INSERT INTO gateway_audit.request_bindings -- Attempt a real reader write.
                SELECT * FROM gateway_audit.request_bindings LIMIT 1;
            EXCEPTION WHEN insufficient_privilege THEN -- Require the permission failure.
                rejected := true; -- Record the expected denial.
            END; -- Finish reader insertion.
            IF NOT rejected THEN -- Reader append access violates the contract.
                RAISE EXCEPTION 'Reader can insert bindings'; -- Fail the run.
            END IF; -- Finish reader-specific verification.
        END IF; -- Finish role-specific checks.
        EXECUTE 'RESET ROLE'; -- Restore administrator context before the next role.
    END LOOP; -- Finish both application roles.
END; -- Finish permission tests.
$$; -- Execute the permission block.

ROLLBACK; -- Remove every policy and binding fixture inserted by these tests.

SELECT 'REQUEST_BINDING_SQL_ACCEPTANCE=PASS' AS result; -- Report completed acceptance.
