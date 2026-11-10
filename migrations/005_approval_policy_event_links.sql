-- Apply after migrations 001 through 004 as a migration administrator.
-- The administrator needs CREATE in gateway_approval, REFERENCES on both
-- parent keys, and permission to transfer ownership to gateway_approval_owner.
-- Pre-provision gateway_approval_link_writer without owner membership.

BEGIN;

-- Create the cross-schema foreign key with migration privileges.
CREATE TABLE gateway_approval.policy_event_request_links (
    policy_event_id text COLLATE "C" PRIMARY KEY, -- Allow one link per policy event.
    request_id text COLLATE "C" NOT NULL, -- Preserve the exact protected request ID.

    CONSTRAINT approval_link_policy_binding FOREIGN KEY (policy_event_id)
        REFERENCES gateway_audit.request_bindings (policy_event_id)
        ON UPDATE NO ACTION ON DELETE NO ACTION NOT DEFERRABLE,
    CONSTRAINT approval_link_protected_request FOREIGN KEY (request_id)
        REFERENCES gateway_approval.requests (request_id)
        ON UPDATE NO ACTION ON DELETE NO ACTION NOT DEFERRABLE
);

-- Leave ownership with the protected schema owner rather than an application role.
ALTER TABLE gateway_approval.policy_event_request_links OWNER TO gateway_approval_owner;
SET LOCAL ROLE gateway_approval_owner;

REVOKE ALL ON SCHEMA gateway_approval FROM gateway_approval_link_writer;
REVOKE ALL ON TABLE gateway_approval.policy_event_request_links FROM PUBLIC,
    gateway_audit_owner, gateway_audit_writer, gateway_audit_reader,
    gateway_approval_request_writer, gateway_approval_request_reader,
    gateway_approval_link_writer;

GRANT USAGE ON SCHEMA gateway_approval TO gateway_approval_link_writer;
GRANT INSERT ON TABLE gateway_approval.policy_event_request_links
    TO gateway_approval_link_writer;
GRANT SELECT ON TABLE gateway_approval.policy_event_request_links
    TO gateway_approval_request_reader;

COMMIT;
