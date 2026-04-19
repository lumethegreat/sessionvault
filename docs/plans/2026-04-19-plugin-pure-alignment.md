# SessionVault Plugin-Pure Alignment Plan

> **Goal:** Align the `sessionvault` repo with the decision that SessionVault must remain a pure Hermes memory plugin, with no local patching of Hermes core files such as `gateway/run.py`.

## Decisions locked in
- `gateway/run.py` must stay upstream / restorable via `git restore`.
- SessionVault must live entirely as plugin code under `plugins/memory/sessionvault`.
- Features that required a core patch are removed rather than kept as optional dead/legacy paths.
- Repo scripts and docs must stop recommending or checking a gateway patch.

## Files to change

### Plugin source
- `plugin/vault_db.py`
- `plugin/__init__.py`
- `plugin/README.md`

### Repo docs
- `README.md`
- `INSTALL.md`

### Repo scripts
- `scripts/install.sh`
- `scripts/sessionvault-doctor.sh`
- remove `scripts/sessionvault-gateway-patch.sh`
- remove `references/hermes-gateway-run-sessionvault-events.patch`

### Tests
- `tests/test_profile_aware_scripts.py`

## Planned edits
1. Remove `record_gateway_event(...)` from plugin source.
2. Update plugin wording from “lifecycle events” to provider-recorded/runtime-recorded events.
3. Remove all install/doctor/docs references to `--with-gateway-patch` and the gateway patch helper.
4. Delete the patch helper script and the stored `.patch` reference file.
5. Update tests so they validate plugin-only install/doctor behaviour.
6. Run targeted tests and a final grep audit for stale gateway-patch references.

## Verification
- `rg -n "gateway patch|with-gateway-patch|sessionvault-gateway-patch|hermes-gateway-run-sessionvault-events.patch" /Users/mestre/projects/sessionvault`
  should return no active workflow references.
- `python -m pytest -q tests/test_profile_aware_scripts.py -o 'addopts='`
- runtime plugin already verified separately via `hermes sessionvault status` / `doctor`.
