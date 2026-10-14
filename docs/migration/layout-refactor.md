# Folder layout refactor

Purpose: make the extracted OpenShell Manager independent of the OMA-era
`infra/dev-sandbox/` and `tools/tests/` paths, without altering behavior.

| Previous location | Current location |
| --- | --- |
| `infra/dev-sandbox/*.py` | `src/openshell_manager/*.py` |
| `infra/dev-sandbox/*.sh` | `scripts/*.sh` |
| `infra/dev-sandbox/phase2.json` | `config/defaults/phase2.json` |
| `infra/dev-sandbox/policies/*` | `config/policies/*` |
| `infra/dev-sandbox/policy.yaml` | `config/policies/validation.yaml` |
| `infra/dev-sandbox/gateway.vm.example.toml` | `config/gateway.vm.example.toml` |
| `infra/dev-sandbox/Dockerfile*` | `docker/Dockerfile*` |
| `infra/dev-sandbox/pilot/*` | `tests/fixtures/pilot/*` |
| `infra/dev-sandbox/{source-manifest,edit-scope}.txt` | `tests/fixtures/legacy/*` |
| `tools/tests/*` | `tests/*` |

Host secrets, Gateway config and runtime state stay outside this Git repository.
The project profile validation, immutable manager revision binding and authorization
guards must retain their previous behavior. Historical PR1/PR2 evidence is left
under `docs/migration/`. Existing host commands have moved to `./scripts/`.

Verify with the offline pytest suite, shell syntax checks, and manual review
of path-sensitive operations before using the migrated commands on a live VM.
This layout-only change makes **no live-acceptance claim**.
