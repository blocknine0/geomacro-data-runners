# Geomacro Data Runners

Public, sanitized data-fetch and validation runners for sources that Geomacro has explicitly approved for public redistribution.

This repository is intentionally separated from the private `geomacro-historical-data` source of truth. It must not contain production database credentials, service-role keys, restricted/raw licensed datasets, private research artifacts, or proprietary production data.

## Allowed public source families

Initial allowlist:

- GDELT Events
- World Bank Open Data
- Worldwide Governance Indicators (WGI)
- UCDP datasets only where redistribution terms are satisfied and no private API token is required in this repository
- USGS public-domain mineral data

Sources marked research-only, non-commercial, license-review, or redistribution-blocked in Geomacro's private source registry must stay out of this repository.

## Architecture

1. Public runners fetch and validate approved public-source artifacts on GitHub-hosted runners.
2. Each output includes provenance metadata and SHA-256 checksums.
3. Public outputs are published as rolling release assets, not written directly to Geomacro's production database.
4. The private historical repository remains responsible for guarded production ingestion, private datasets, secrets, and final canonicalization.

## Security boundary

No Supabase service-role key, database URL with credentials, UCDP private token, signing key, wallet key, private dataset, or restricted raw source is permitted here.

The public runner layer is a cost and isolation boundary. It is not the production database authority.

## Status

Phase 1 initializes the public runner framework and GDELT rolling artifact path. Existing private production workflows remain active until the replacement path has been acceptance-tested end to end.

All rights reserved unless a source file states otherwise.
