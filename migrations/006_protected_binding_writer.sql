-- Apply after migrations 001 through 005 as the migration administrator.
-- Pre-provision gateway_protected_binding_writer without other role memberships.
BEGIN;

-- This role can read protected requests and append the three related records.
-- Existing application roles receive no new grants.
REVOKE ALL ON SCHEMA gateway_audit, gateway_approval FROM gateway_protected_binding_writer;
REVOKE ALL ON TABLE gateway_audit.policy_decisions,
    gateway_audit.request_bindings, gateway_audit.execution_events,
    gateway_approval.requests, gateway_approval.policy_event_request_links
    FROM gateway_protected_binding_writer;

GRANT USAGE ON SCHEMA gateway_audit, gateway_approval TO gateway_protected_binding_writer;
GRANT SELECT ON TABLE gateway_approval.requests TO gateway_protected_binding_writer;
GRANT INSERT ON TABLE gateway_audit.policy_decisions,
    gateway_audit.request_bindings, gateway_approval.policy_event_request_links
    TO gateway_protected_binding_writer;

COMMIT;
