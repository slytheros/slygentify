# Support

Slygentify is pre-alpha software maintained on a best-effort basis. There is no
guaranteed response or remediation service-level agreement.

## Before requesting help

1. Read the [installation guide](docs/installation.md), [task guides](docs/index.md),
   and [known safety boundaries](docs/safety.md).
2. Run `slygentify --help` and the relevant command's `--help` output.
3. Search existing GitHub issues after the public repository is available.

For a reproducible bug, use the GitHub bug template and include the Slygentify version,
Python version, operating system, command, exit status, and a minimal repository shape.
Remove credentials, private paths, source contents, and other sensitive information.
Feature requests should describe the user outcome and expected effects rather than an
implementation alone.

Security vulnerabilities do not belong in public issues. Follow [SECURITY.md](SECURITY.md).

## Supported versions

Before 1.0, only the current development version receives best-effort maintenance. After
1.0, only the latest public 1.x release is supported unless the maintainer explicitly
opens a maintenance branch. The [current installation status](docs/installation.md#current-availability)
records the required Python versions and pre-release platform boundary. Other environments
may work but are not promised before a verified public support matrix is published.
