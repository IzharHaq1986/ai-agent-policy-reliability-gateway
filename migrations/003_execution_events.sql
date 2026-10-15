-- Apply after migrations 001 and 002 using the migration administrator.
-- Required roles: gateway_audit_owner, gateway_audit_writer, gateway_audit_reader.
-- Storage constraints do not prove permission or actual simulator execution.
-- This migration is transactional and intentionally not repeatable.

BEGIN; -- Apply the complete schema change together.

SET LOCAL ROLE gateway_audit_owner; -- Create objects under the dedicated owner.

CREATE TABLE gateway_audit.execution_events ( -- Store claimed simulation activity.
    schema_version integer NOT NULL, -- Identify the supported record format.
    event_type text COLLATE "C" NOT NULL, -- Identify the execution-event category.
    event_id text COLLATE "C" PRIMARY KEY, -- Give each record a unique identity.
    execution_id text COLLATE "C" NOT NULL, -- Group records for one attempt.
    policy_event_id text COLLATE "C" NOT NULL, -- Reference the request binding.
    occurred_at text COLLATE "C" NOT NULL, -- Store the supplied UTC occurrence time.
    principal_id text COLLATE "C" NOT NULL, -- Store the supported caller context.
    tool text COLLATE "C" NOT NULL, -- Store the supported practice tool.
    operation text COLLATE "C" NOT NULL, -- Store the supported read operation.
    status text COLLATE "C" NOT NULL, -- Describe intent or a terminal outcome.
    reason_code text COLLATE "C" NOT NULL, -- Explain that status.

    CONSTRAINT execution_schema_version CHECK (schema_version = 1), -- Support format 1.
    CONSTRAINT execution_event_type CHECK ( -- Restrict the record category.
        event_type = 'simulator_execution' -- Match the Python event contract.
    ), -- Finish the category restriction.
    CONSTRAINT execution_event_id CHECK ( -- Require canonical version-4 identity.
        length(event_id) = 36 -- Reject extra or missing characters.
        AND event_id ~ -- Check case, separators, version, and UUID variant.
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ), -- Finish the record identifier check.
    CONSTRAINT execution_attempt_id CHECK ( -- Validate the attempt identifier too.
        length(execution_id) = 36 -- Require the canonical length.
        AND execution_id ~ -- Require the supported UUID format.
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ), -- Finish attempt identifier validation.
    CONSTRAINT execution_policy_id CHECK ( -- Validate the referenced identifier.
        length(policy_event_id) = 36 -- Require the canonical length.
        AND policy_event_id ~ -- Require the supported UUID format.
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ), -- Finish policy identifier validation.
    CONSTRAINT execution_occurred_at CHECK ( -- Check format and calendar validity.
        CASE -- Parse calendar values only after the text passes format checks.
            WHEN length(occurred_at) = 20 -- Require the exact UTC text length.
                AND occurred_at ~ -- Restrict date and time components.
                    '^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z$'
            THEN -- Check the actual calendar date.
                substring(occurred_at FROM 1 FOR 4)::integer BETWEEN 1 AND 9999
                AND make_date( -- Reject impossible dates such as February 30.
                    substring(occurred_at FROM 1 FOR 4)::integer, -- Read the year.
                    substring(occurred_at FROM 6 FOR 2)::integer, -- Read the month.
                    substring(occurred_at FROM 9 FOR 2)::integer -- Read the day.
                ) IS NOT NULL -- Require a valid constructed date.
            ELSE false -- Reject malformed timestamps.
        END -- Finish safe calendar validation.
    ), -- Finish the timestamp constraint.
    CONSTRAINT execution_principal CHECK ( -- Restrict caller context.
        principal_id = 'reliability-reader' -- Match the supported principal.
    ), -- Finish principal validation.
    CONSTRAINT execution_tool CHECK (tool = 'fixture_store'), -- Restrict the tool.
    CONSTRAINT execution_operation CHECK (operation = 'READ_ITEM'), -- Restrict the action.
    CONSTRAINT execution_status_reason CHECK ( -- Allow only the four defined pairs.
        (status = 'started' AND reason_code = 'SIMULATION_STARTED') -- Intent to invoke.
        OR (status = 'completed' AND reason_code = 'SIMULATION_COMPLETED') -- Valid result.
        OR (status = 'failed' AND reason_code = 'SIMULATOR_ERROR') -- Invocation failure.
        OR (status = 'failed' AND reason_code = 'INVALID_RESULT') -- Invalid returned data.
    ), -- These contents do not prove that activity actually occurred.
    CONSTRAINT execution_request_binding FOREIGN KEY (policy_event_id) -- Require a link.
        REFERENCES gateway_audit.request_bindings (policy_event_id) -- Existing binding.
        ON UPDATE NO ACTION -- Do not cascade identifier changes.
        ON DELETE NO ACTION -- Do not cascade binding deletion.
        NOT DEFERRABLE -- Check the relationship during the statement.
); -- Ordering and cross-record agreement require later receiver enforcement.

CREATE UNIQUE INDEX execution_events_one_start -- Permit at most one start per attempt.
    ON gateway_audit.execution_events (execution_id) -- Group by attempt identifier.
    WHERE status = 'started'; -- Apply uniqueness only to start records.

CREATE UNIQUE INDEX execution_events_one_terminal -- Permit at most one final outcome.
    ON gateway_audit.execution_events (execution_id) -- Group by attempt identifier.
    WHERE status IN ('completed', 'failed'); -- Both terminal states share this limit.

REVOKE ALL ON TABLE gateway_audit.execution_events FROM PUBLIC; -- Remove public access.
REVOKE ALL ON TABLE gateway_audit.execution_events -- Reset direct application grants.
    FROM gateway_audit_writer, gateway_audit_reader; -- Preserve the owner separately.

GRANT INSERT, SELECT ON TABLE gateway_audit.execution_events -- Allow append and lookup.
    TO gateway_audit_writer; -- Grant no update, deletion, or management rights.
GRANT SELECT ON TABLE gateway_audit.execution_events -- Allow inspection only.
    TO gateway_audit_reader; -- Grant no write or management rights.

COMMIT; -- Make the complete table, indexes, and permissions visible together.
