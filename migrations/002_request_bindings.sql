-- Apply after migration 001 using the migration administrator.
-- Required roles: gateway_audit_owner, gateway_audit_writer, gateway_audit_reader.
-- This migration adds storage only; it grants no execution permission.
-- The transaction is intentionally not repeatable.

BEGIN; -- Apply all changes together or roll them back on failure.

SET LOCAL ROLE gateway_audit_owner; -- Create objects under the existing owner.

CREATE TABLE gateway_audit.request_bindings ( -- Store one request per policy event.
    schema_version integer NOT NULL, -- Identify the supported binding format.
    policy_event_id text COLLATE "C" PRIMARY KEY, -- Require a unique, non-null link.
    principal_id text COLLATE "C" NOT NULL, -- Store the supported caller name.
    tool text COLLATE "C" NOT NULL, -- Store the supported practice tool.
    operation text COLLATE "C" NOT NULL, -- Store the supported read operation.
    item_id integer NOT NULL, -- Store the requested synthetic item.

    CONSTRAINT binding_schema_version CHECK (schema_version = 1), -- Accept format 1.
    CONSTRAINT binding_principal CHECK ( -- Limit the stored caller context.
        principal_id = 'reliability-reader' -- Match the existing policy principal.
    ), -- Finish the principal restriction.
    CONSTRAINT binding_tool CHECK (tool = 'fixture_store'), -- Restrict the tool.
    CONSTRAINT binding_operation CHECK (operation = 'READ_ITEM'), -- Restrict the action.
    CONSTRAINT binding_item_id CHECK (item_id BETWEEN 1 AND 1000), -- Bound the item.
    CONSTRAINT binding_policy_event FOREIGN KEY (policy_event_id) -- Require a policy row.
        REFERENCES gateway_audit.policy_decisions (event_id) -- Link to its unique ID.
        ON UPDATE NO ACTION -- Never cascade identifier changes.
        ON DELETE NO ACTION -- Never cascade policy-record deletion.
        NOT DEFERRABLE -- Check the relationship during the statement.
); -- An existing policy row does not by itself establish an ALLOW decision.

REVOKE ALL ON TABLE gateway_audit.request_bindings FROM PUBLIC; -- Remove public access.
REVOKE ALL ON TABLE gateway_audit.request_bindings -- Clear inherited default grants.
    FROM gateway_audit_writer, gateway_audit_reader; -- Reset these direct role grants.

GRANT INSERT, SELECT ON TABLE gateway_audit.request_bindings -- Allow append and lookup.
    TO gateway_audit_writer; -- Grant no update, deletion, or table-management rights.
GRANT SELECT ON TABLE gateway_audit.request_bindings -- Allow inspection only.
    TO gateway_audit_reader; -- Grant no write or table-management rights.

COMMIT; -- Make the complete schema change visible together.
