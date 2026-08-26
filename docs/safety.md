# Safety boundaries

Slygentify treats repository content as untrusted input. Default inspection is bounded,
contained within the selected Git root, local, read-only, and network-free.

## What inspection does not do

Scan, map, and doctor do not:

- import the target project or execute commands they discover;
- install target-project dependencies or resolve its dependency graph;
- follow descendant symbolic links or traverse nested repositories;
- read supported sensitive paths by default;
- upload repository content, use ambient credentials, or contact hosted services; or
- write repository files.

Init uses the same inspection boundary. Applying a reviewed plan writes only root
`AGENTS.md` and `.slygentify/state.json`, preserves unrelated work, and reports any
bounded partial write.

## Fixed Git lookup

Automatic inspection may invoke one fixed `git ls-files --cached --full-name -z` lookup
to retain tracked manifests hidden by checked-out ignore rules. Missing, rejected, or
failed automatic Git produces useful partial results rather than authorizing another
command.

`--git-executable PATH` is a distinct explicit authorization. The selected file is
trusted, unsandboxed code and may have arbitrary filesystem, process, credential, or
network effects. Inspect a nonstandard or repository-contained executable before using
it. This option authorizes only the fixed tracked-path lookup, not discovered commands.

## Resource and content boundaries

Built-in and configured limits bound depth, entries, bytes, elapsed time, open files,
and deterministic logical memory accounting. Raising or removing a resource limit never
weakens containment, file-type, sensitive-path, link, nested-repository, command, or
network protections. See [inspection accounting](inspection-accounting.md).

Errors and partial results should say what was unavailable, whether anything changed,
and the next safe action. Report suspected security problems through the repository's
private process, never a public issue.
