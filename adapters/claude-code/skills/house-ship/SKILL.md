---
name: house-ship
description: Release for build contests - deploy the demo, prepare fallback, pitch script, slides, submission README, and a clean public export.
disable-model-invocation: true
---
Follow **Release** in `$HOUSE_DIR/playbooks/build.md` and `$HOUSE_DIR/protocols/export.md`.

1. Deploy if `spec.deploy` is true; test from another device. Record the URL in `PROGRESS.md`.
2. Fallback: demo video plan + offline/seeded mode.
3. Dispatch `pitch` for script, slides, README, demo plan (in `spec.languages.public`).
4. Final `/house-judge` round on the release candidate.
5. Export: `$HOUSE_ROOT/bin/house export <contest> <dest>`; show the human what will be published. Pushing the
   export to a public remote is the human's call unless `spec` says otherwise.
