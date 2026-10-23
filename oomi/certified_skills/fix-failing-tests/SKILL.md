---
name: fix-failing-tests
description: Find out why a project's tests are failing, fix the cause rather than the test, and prove it by rerunning them.
status: certified
---

## When to use
The user says tests fail, CI is red, or asks you to make the test suite pass.

## Steps
1. Find how this project runs its tests: look for a README section, `package.json` scripts, `pyproject.toml`,
   `Makefile`, `Cargo.toml`, `go.mod`. Use the project's own command (e.g. `npm test`, `pytest -q`, `cargo test`, `go test ./...`).
2. Run the suite once and save the output. If it's long, rerun only the failing tests
   (`pytest path::test -x -q`, `npm test -- -t "name"`, `go test -run Name ./pkg`).
3. For each failure, read the assertion and the code under test before changing anything. Decide which is wrong:
   the code, the test, or the environment (missing dependency, wrong version, no network, missing env var).
4. Make the smallest fix to the code that's wrong. Change a test only when its expectation is truly outdated,
   and tell the user when you do.
5. Rerun the failing tests, then the whole suite, to make sure nothing else broke.
6. Report: what was failing, the cause, what you changed, and the final pass/fail count.

## Pitfalls
- Don't skip, delete or loosen tests to get to green unless the user asks for that.
- Environment problems (a missing package, a database that isn't running) are the user's call; explain
  what's missing instead of installing things system-wide.
- Flaky tests: run them three times before calling something fixed.
