# Security and data-boundary policy

This repository is public by design. Treat every committed file, workflow log, release asset, pull request, issue, and Git history entry as publicly readable.

## Never place here

- Supabase service-role keys or database credentials
- UCDP private API tokens
- signing keys, wallet keys, private keys, seed phrases, or API secrets
- private Geomacro datasets, customer data, or internal-only research artifacts
- source material whose redistribution is blocked, under license review, or non-commercial only
- production database dumps or private provenance bundles

## Allowed outputs

Only artifacts derived from sources explicitly marked as redistribution-allowed in `config/public_sources.json` may be published. Every published artifact must include provenance metadata and a SHA-256 checksum.

## Production boundary

Public runners do not write directly to Geomacro's production database. Production ingestion and private-source processing remain in the private historical repository behind its existing safeguards.

If a credential or restricted artifact is ever committed, rotate/revoke the credential first, remove the material from current history, and treat prior public exposure as permanent for incident-response purposes.
