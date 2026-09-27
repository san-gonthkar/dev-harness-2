# Release Checklist (V11 10.D)

Every item must be checked and initialed before tagging `v1.0.0`. The release
gate (`bash scripts/verify_phase_10.sh`) automates the machine-checkable rows;
the rows marked **manual** require a human.

## 1. Phase reports

- [ ] All 11 phase acceptance reports present and `ACCEPTED` — `python scripts/verify_phase.py --audit-all`
- [ ] Every report is commit-pinned (its `commit` field matches a real commit)
- [ ] P5, P8, P10 carry a **human** `signed_by` (Lane D / All)
- [ ] P0–P4, P6, P7, P9 carry the `reviewer-agent` signature

## 2. Test tiers

- [ ] PR suite green and **< 10 min** on the standard runner — `time make ci`
- [ ] NIGHTLY green: `timing`, `slow`, `e2e` — `make test-nightly` (manual: nightly runner)
- [ ] Mutation gates green on the merged trunk — `python scripts/mutation_gate.py` (manual: WSL2/POSIX)
- [ ] Coverage ratchet green — `python scripts/coverage_gate.py`
- [ ] Error-reachability gate green — `python scripts/coverage_gate.py --errors`

## 3. E2E and graph coverage

- [ ] 10.1 full SDLC E2E green (PAUSE/RESUME, no process group left)
- [ ] 10.2 parallel E2E green (3-wide pool, clean merge, 0 lost writes)
- [ ] 10.3 concurrent soak green (0 `database is locked`, RSS growth < 15%) — manual: nightly
- [ ] 10.4 local-profile run green against live Ollama — manual: nightly
- [ ] Every compiled graph node executed ≥1× — `python scripts/generate_traceability.py --graph-coverage`

## 4. Traceability

- [ ] Every TDD section 1–6 maps to ≥1 task and ≥1 passing test — `python scripts/generate_traceability.py --check`
- [ ] `docs/traceability.md` regenerated and committed

## 5. Operator readiness

- [ ] Cold-machine install: clone, `pip install .`, follow `docs/runbook.md` — **manual**: fresh VM, < 10 min
- [ ] Four-panel TUI + healthy broker + engine reached by someone who did not write the runbook
- [ ] `dev-harness --workspace <ws> --self-check` prints socket, DB, broker and engine health

## 6. Rollback

- [ ] Rollback rehearsal from the release tag to the prior tag — **manual**: < 15 min, no data loss
- [ ] `docs/rollback.md` procedure verified on a scratch clone

## 7. Release

- [ ] `bash scripts/verify_phase_10.sh` exits 0
- [ ] `reports/phase_10_acceptance.json` verdict `ACCEPTED` with a human `signed_by`
- [ ] Tag `v1.0.0` created and pushed
- [ ] Release notes reference the acceptance report and the traceability doc

## Sign-off

| Field | Value |
| :--- | :--- |
| Release version | `v1.0.0` |
| Release commit | _(fill in)_ |
| Operator | _(name)_ |
| Date | _(YYYY-MM-DD)_ |
| Signature | _(initials)_ |
