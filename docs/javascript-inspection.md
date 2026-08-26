# JavaScript and TypeScript inspection

Use this reference when you need to determine exactly which JavaScript and TypeScript
manifests, workspaces, tools, runtimes, and workflows Slygentify can inspect statically.

Slygentify reports narrow facts from static repository evidence. It does not install
dependencies, resolve locks, import configuration modules, execute package scripts or
workflows, contact a registry, or select a preferred package manager.

| Area | Supported evidence | Reported claim |
| --- | --- | --- |
| Components and metadata | A valid unique-key UTF-8 `package.json`; approved package fields, dependency groups, scripts, and bin entries | A JavaScript/TypeScript package boundary and the exact declared field, direct dependency, command, or entry point. Private and unnamed packages remain valid boundaries. A component beneath an exact lowercase `test`, `tests`, `example`, `examples`, `docs`, `template`, or `templates` path segment is inferred as auxiliary from its existing manifest evidence. Same-root Python or generic evidence produces one component with a JavaScript facet and a `mixed` summary. |
| Managers | `package-lock.json`, `npm-shrinkwrap.json`, `pnpm-lock.yaml`, `pnpm-workspace.yaml`, `yarn.lock`, `.yarnrc.yml`, `packageManager`, and `devEngines.packageManager` | Each npm, pnpm, Yarn, or Corepack declaration is present. Competing families remain visible without selecting a manager. The npm root-lock pair retains npm's documented shrinkwrap precedence without turning it into a workflow recommendation. |
| Runtimes | `engines.node`, `engines.npm`, component-root `.nvmrc` and `.node-version`, and literal or supported static-matrix `actions/setup-node` values | Each compatibility range or exact selection is independently declared. An exact selection outside a safely comparable range produces a conflict; unsupported comparisons remain unknown. No combined effective range is calculated. |
| Workspaces | `package.json` workspace arrays or `packages` objects and `pnpm-workspace.yaml` package patterns | A workspace declaration and each safe in-root match with a valid `package.json` are present as directed `workspace-member` relationships. Exclusions override inclusions. Invalid, missing, overlapping, dynamic, and out-of-root patterns remain diagnostic rather than being silently resolved. |
| TypeScript and tools | `tsconfig.json`, `tsconfig.*.json`, direct `typescript` dependencies, approved ESLint/Prettier/Jest/Vitest/Playwright package fields, direct dependencies, and configuration locations | The named configuration, direct dependency, or safe TypeScript reference is declared. A project-reference directory may resolve only through its in-root strict unique-key `tsconfig.json`; explicit files remain supported. Unresolved references aggregate by component and safe-resolution cause. JSON-with-comments and configuration modules are never evaluated. |
| Frameworks | Direct `express`, `fastify`, and `vue` declarations | The component directly declares the named framework package. Runtime activation and transitive use remain unclaimed. |
| CI | Literal Gitea/GitHub `run` steps, static `setup-node` values, and GitLab script/run fields, including bounded in-root `include:local` files | The attributable command or runtime selection is declared with an exact locator. Checkout ownership and working directories constrain attribution. External and dynamic includes or expressions remain unknown and are never fetched. |

JSON evidence uses RFC 6901 JSON Pointers and workflow YAML uses JSON-Pointer-style
locators. Commands are shown in the complete default report, the interactive explorer,
and JSON. Credential-shaped literals in
package scripts and CI commands are withheld cautiously; unsafe bin targets are also
withheld. Diagnostics ask for review without claiming that text is a confirmed secret.
Variable and expression references remain static source text.

Malformed, unreadable, protected, or budget-exhausted supported evidence makes the result
partial. Conflicting declarations remain a complete result when Slygentify can preserve
all of them without guessing. Source-backed diagnostics retain the evidence location and
give a corrective or review action.

Public 1.0 intentionally excludes Bun, Deno, Bower, Meteor, Rush, Nx, Turborepo, Lerna,
dynamic package generation, JavaScript or TypeScript configuration execution, lifecycle
hooks, lock resolution, source-import inference, external CI includes, and installed or
transitive dependency inspection. Recognized unsupported tooling remains unknown.
