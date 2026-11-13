# Bounded diagnostic evidence serialization

`tests/support/nts_evidence_bundle.py` is an internal building block for a future
NTS experiment controller. It returns a complete bytes value or raises a fixed
`EvidenceError`. It does not write a file, print output, launch a command, query a
time source, or change an existing runner. Persistence and live integration are
separate work.

## Version 1 input

Trusted controller code supplies an ordinary builtin dictionary with exactly
three fields:

| Field | Input |
| --- | --- |
| `collection_id` | Canonical lowercase UUID string created by trusted code |
| `metadata` | Bounded dictionary of plain JSON values; descriptive claims only |
| `observations` | Ordered list of at most 128 query observations |

Each observation requires exactly these fields:

| Field | Input |
| --- | --- |
| `query_ref` | Lowercase reference starting with a letter; remaining characters are letters, digits, or underscores; maximum 64 ASCII characters |
| `stdout`, `stderr` | Immutable bytes, combined size at most 32 KiB |
| `returncode` | Integer from -255 through 255, or null when unavailable |
| `reason_code` | Null or uppercase reference with digits/underscores, maximum 64 ASCII characters |
| `started_monotonic`, `ended_monotonic` | Finite nonnegative numbers, with end at least start |

These are recorded observations, not validated proof of execution or time health.
A zero return code does not authenticate a time source. Supplied monotonic values
are not independently verified here. Failed-query capture and interpretation
remain the caller's responsibility; this helper does not recover output already
discarded by command capture.

## Input and encoding limits

Only exact builtin types are supported. Custom objects, mapping subclasses,
custom encoder callbacks, unsupported fields, lone Unicode surrogates, non-finite
numbers, integers outside signed 64-bit range, and cyclic metadata are rejected.
Metadata has at most 128 members per container, depth at most eight edges from
its root, keys of 1–64 UTF-8 bytes, and string values of at most 32 KiB UTF-8.
A shared pre-encoding budget bounds visited variable-input nodes to 4,096 and
variable text plus raw stream bytes to 1 MiB. Repeated non-cyclic values consume
the budget again because their encoded copies also count. These are experiment
report limits, not gateway freshness or uncertainty thresholds.

The output has fixed `schema_version: 1`, `measurement_age: unknown`,
`approval_eligibility: blocked`, and `stream_encoding: base64`. Raw streams become
`stdout_base64` and `stderr_base64`, preserving every byte without lossy text
decoding. Observation order stays intact. Metadata and observation claims do not
override the fixed envelope. Reserved eligibility, age, and schema keys are
rejected in metadata, including nested metadata. A claim appearing inside a raw
stream stays opaque Base64 data.

JSON encoding sorts dictionary keys, uses compact separators, escapes non-ASCII
characters, rejects non-finite values, and adds one newline. The complete UTF-8
bytes value must be at most 1,048,576 bytes. This includes metadata, field names,
Base64 expansion, escaping, and the newline. A caller can choose a smaller cap
but cannot increase it. Input is bounded before encoding; encoding then checks
each bounded chunk before adding it to the output buffer. It does not first
construct an unrestricted full JSON string.

The serializer never truncates, drops records, or returns a successful prefix to
make a report fit. On overflow the caller receives `OUTPUT_LIMIT`, not a partial
report. The caller must retain its owned original observations for investigation
and report failure or insufficient evidence; it must not silently retry with
missing records. The helper never mutates the input and is intended for private
controller-owned data, not concurrently modified shared structures.

## Failure codes and validation

Fixed codes are `INVALID_BYTE_LIMIT`, `INVALID_REPORT`, `INVALID_TEXT`,
`INPUT_LIMIT`, `INVALID_NUMBER`, `INVALID_METADATA`,
`RESERVED_OR_INVALID_METADATA_KEY`, `INVALID_OBSERVATION`,
`INVALID_COLLECTION_ID`, `INVALID_OBSERVATIONS`, and `OUTPUT_LIMIT`.
Errors do not include supplied values or captured streams.

Focused tests verify all byte values, escaping, Base64 expansion, metadata and
newline overhead, exact final size boundaries including 1 MiB, aggregate overflow,
deterministic output, malformed structures, cycles, reserved-field overrides,
and failures without output or file side effects. The existing watchdog proof
and required checks remain in place.

Serialization is neither durable storage nor authenticated provenance. It does
not establish source accuracy, freshness, uncertainty, successful cleanup, or
approval authority. The live controller still needs orchestration, health and
identity checks, source/sample correlation, verified resource cleanup, and safe
artifact persistence. Live NTS collection and runtime approval remain blocked.

The separate [private evidence publisher](nts-evidence-publication.md) uses this
serializer to save reports through a trusted directory descriptor. It does not
change this serializer's pure in-memory interface or enable live collection.
