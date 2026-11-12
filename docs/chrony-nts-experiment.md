# Isolated chrony/NTS experiment preparation

This opt-in preparation builds an experiment image and checks it without making
live time queries. It does not change the host clock or its time service.
Approval eligibility stays blocked and measurement age stays unknown.

## Prepare on the validation server

From the checked-out repository, with Docker access and the existing validation
Python environment, run:

```sh
PYTHONDONTWRITEBYTECODE=1 "$HOME/gateway-approval-validation-venv/bin/python" \
  tests/support/prepare_chrony_nts_experiment.py --prepare
```

The build requires registry and Ubuntu package-repository access. It has a
600-second command timeout. An unavailable pinned package fails preparation;
there is no version fallback. No host packages are installed.

The image uses the pinned Ubuntu 24.04 linux/amd64 manifest in its Dockerfile and
chrony package `4.5-1ubuntu4.2`. The report records the resulting immutable image
ID, complete installed-package manifest, configuration hash, and binary version
with NTS support. Dependencies are recorded after resolution rather than claimed
to be reproducible from the base digest alone.

The check container has networking disabled. It parses the configuration without
starting the time daemon, checks its user and effective privileges, and exits.
It has a read-only root filesystem, no Linux capabilities, no privilege
escalation, one CPU, 256 MiB memory, 64 processes, and two 16 MiB temporary
filesystems. The runner checks the configured container protections and the
actual process privileges. Waiting for this static check is bounded to 20 seconds.

A successful run prints a JSON report followed by
`CHRONY_NTS_PREPARATION=PASS`. Failure prints
`CHRONY_NTS_PREPARATION=FAILED` and exits unsuccessfully. The runner removes its
owned check container and temporary image-ID file on completion. Cleanup failure
prevents success. The built image remains available for the separately scoped
experiment; no persistent runtime volume is created.

Record the actual report and image ID in the local ignored `project_state.md`
before any live launch. Preparation alone does not demonstrate certificate
validation, authenticated samples, source accuracy, or measurement freshness.

## Boundaries for a later live experiment

The dedicated configuration names only `time.cloudflare.com`, requires NTS and
authenticated source selection, and preserves ordinary certificate validation.
The image's daemon entrypoint includes `-x` to disable clock adjustment. The
future launch must retain the container protections, use private container
networking without published ports, and enforce the recorded 120-second live
collection limit. This preparation command does not implement that launch.

A later evidence collection must distinguish key-establishment age from the age
of a received good sample, correlate the selected source and sample, and account
for collection and delivery delays. Authentication alone cannot establish
freshness or accuracy. Gateway uncertainty and freshness limits remain undecided.
Missing evidence must leave eligibility blocked.

## Primary references

- [chronyd 4.5 options, including -x and -p](https://chrony-project.org/doc/4.5/chronyd.html)
- [chrony 4.5 configuration and NTS](https://chrony-project.org/doc/4.5/chrony.conf.html)
- [chronyc 4.5 evidence fields](https://chrony-project.org/doc/4.5/chronyc.html)
- [Ubuntu Noble chrony package](https://packages.ubuntu.com/noble-updates/chrony)
- [Cloudflare NTS endpoint](https://developers.cloudflare.com/time-services/nts/)
- [NTS protocol and security considerations](https://www.rfc-editor.org/rfc/rfc8915.html)

The [live procedure review specification](chrony-nts-live-procedure.md) defines
the next stage's requirements. It is not an executable launch command; the
bounded live controller still needs implementation and validation.
