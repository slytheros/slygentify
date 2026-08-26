# Inspection accounting

Slygentify applies the effective resource limits listed in the
[`[scan.limits]` configuration reference](configuration-and-provenance.md#scanlimits).
That table is the canonical source for current defaults. A root `slygentify.toml` can
tighten, raise, or remove each accounting limit without weakening containment or other
safety boundaries.

Before traversal, tracked-path discovery may run Git for at most 10 seconds or the
remaining effective scan deadline, whichever is shorter. Its stdout is bounded by both
the remaining aggregate-byte budget and the remaining logical-memory budget. Stderr is
independently capped at 64 KiB and must be empty for the lookup to be accepted. A time or
stream limit terminates the lookup and produces the stable partial Git-tracking fallback;
output and stderr are never emitted as evidence or diagnostics.

The memory limit is a deterministic logical ledger, not a process RSS limit. It counts:

- the UTF-8 byte length of every relative path retained in the breadth-first queue;
- the UTF-8 byte length of every permitted regular-file path retained in the bounded
  detector catalogue;
- the UTF-8 byte length of retained Gitignore patterns;
- validated Git tracked-path bytes plus tracked directory-prefix bytes (Git stdout also
  counts toward the aggregate-byte budget);
- the raw length of each live or retained file buffer, counted once;
- an additional input-buffer length only when a parser makes and retains a copy; and
- the compact canonical JSON byte length of each accumulated candidate record.

JavaScript and TypeScript manifest, workspace, tool, and workflow inputs and generic
CMake/ESP-IDF and KiCad inputs use the same catalogue, lazy-read, raw-buffer, parsed-copy,
and normalized-candidate accounting rules as Python inspection. Normalized component
relationship records count toward the same deterministic model-record ledger. The elapsed
deadline is shared by Git discovery, traversal, detector work, normalization, relationship
composition, and canonical result preparation; a checkpoint stops new work and records the
existing elapsed-limit skipped scope when it is exhausted.

Released queue entries and transient file buffers are deducted. Interpreter objects,
allocator overhead, regular-expression state, validation internals, and native-library
memory are outside the ledger. Exhausting the ledger stops affected work and produces an
explicit partial result when the process remains able to do so.
