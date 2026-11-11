-- Run after migration 006 as the isolated test administrator.
BEGIN;

DO $$
DECLARE
    table_name text;
    privilege_name text;
    allowed boolean;
    writer text := 'gateway_protected_binding_writer';
BEGIN
    -- Require an ordinary role without inherited privileges from another role.
    IF EXISTS (
        SELECT 1 FROM pg_roles
        WHERE rolname = writer
            AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
    ) OR EXISTS (
        SELECT 1 FROM pg_auth_members
        WHERE member = (SELECT oid FROM pg_roles WHERE rolname = writer)
    ) THEN
        RAISE EXCEPTION 'Protected binding writer has excess role authority';
    END IF;

    IF NOT has_schema_privilege(writer, 'gateway_audit', 'USAGE')
        OR NOT has_schema_privilege(writer, 'gateway_approval', 'USAGE')
        OR has_schema_privilege(writer, 'gateway_audit', 'CREATE')
        OR has_schema_privilege(writer, 'gateway_approval', 'CREATE')
    THEN
        RAISE EXCEPTION 'Unexpected protected binding schema privileges';
    END IF;

    -- Compare the effective privileges against the entire allowed table set.
    FOREACH table_name IN ARRAY ARRAY[
        'gateway_audit.policy_decisions', 'gateway_audit.request_bindings',
        'gateway_audit.execution_events', 'gateway_approval.requests',
        'gateway_approval.policy_event_request_links'
    ] LOOP
        FOREACH privilege_name IN ARRAY ARRAY['SELECT', 'INSERT', 'UPDATE', 'REFERENCES'] LOOP
            allowed := (
                privilege_name = 'SELECT' AND table_name = 'gateway_approval.requests'
            ) OR (
                privilege_name = 'INSERT' AND table_name IN (
                    'gateway_audit.policy_decisions', 'gateway_audit.request_bindings',
                    'gateway_approval.policy_event_request_links'
                )
            );
            IF has_any_column_privilege(writer, table_name, privilege_name) <> allowed
                OR has_any_column_privilege(
                    writer, table_name, privilege_name || ' WITH GRANT OPTION'
                )
            THEN
                RAISE EXCEPTION 'Unexpected protected binding column privilege';
            END IF;
        END LOOP;
        IF has_table_privilege(writer, table_name, 'DELETE, TRUNCATE, TRIGGER') THEN
            RAISE EXCEPTION 'Protected binding writer can mutate or manage records';
        END IF;
    END LOOP;
END;
$$;

ROLLBACK;
SELECT 'PROTECTED_BINDING_WRITER_SQL_ACCEPTANCE=PASS';
