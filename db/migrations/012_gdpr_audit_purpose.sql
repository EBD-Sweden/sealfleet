-- 012_gdpr_audit_purpose.sql — GDPR Art. 30 processing metadata on audit events
-- Run: PGPASSWORD=admin psql -h localhost -p 54323 -U admin -d mcpfinder -f db/migrations/012_gdpr_audit_purpose.sql
--
-- Every audit event now records WHY the processing happened (purpose) and the
-- lawful basis under which it happened. The router derives defaults from the
-- action when the caller does not supply them:
--   privacy.*/audit*/retention.*      -> compliance / legal_obligation
--   auth*/token*/policy_*/credential* -> security / legitimate_interest
--   everything else                   -> service_delivery / contract
--
-- Hash-chain compatibility: rows written before this migration have NULL in
-- both columns and verify with the original 9-field hash; new rows include
-- purpose/lawful_basis in the hashed canonical fields (see
-- runtime/router.py::_audit_hash_fields).

BEGIN;

ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS purpose TEXT;
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS lawful_basis TEXT;

COMMENT ON COLUMN audit_events.purpose IS 'GDPR Art. 30 processing purpose (e.g. service_delivery, security, compliance)';
COMMENT ON COLUMN audit_events.lawful_basis IS 'GDPR Art. 6 lawful basis (e.g. contract, legitimate_interest, legal_obligation, consent)';

COMMIT;
