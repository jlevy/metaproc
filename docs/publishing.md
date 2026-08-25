# Publishing

Metaproc versions come from Git tags.
Publishing is performed by the `publish.yml` workflow with PyPI trusted publishing; no
long-lived PyPI token belongs in the repository.

## First-Time PyPI Setup

Create a pending trusted publisher for:

- PyPI project: `metaproc`
- GitHub owner: `jlevy`
- GitHub repository: `metaproc`
- workflow: `publish.yml`
- environment: `pypi`

The repository must be public before the first trusted-publisher release when required
by the selected PyPI configuration.
Confirm the complete public-hygiene and distribution gates before changing visibility.

The first public release is `v0.2.0`. No `v0.1.0` tag or PyPI distribution was
published; `0.1.0` appeared only in pre-release documentation and must not be published
retroactively.

## Release Checklist

1. Update local `main` and confirm the worktree is clean.

2. Run the complete gate:

   ```shell
   make verify
   ```

3. Confirm CI is green for the exact commit to be tagged.

4. Review changes since the previous release and choose a semantic version.

5. Write release notes following `tbd guidelines release-notes-guidelines`. Describe the
   aggregate user-visible delta, compatibility notes, and shipped Agent Skill or
   process-content changes.
   Past notes live in [releases/](releases/); the most recent are
   [v0.3.0.md](releases/v0.3.0.md).
   End with a concrete compare link.

6. Create a GitHub release with a `vX.Y.Z` tag:

   ```shell
   gh release create vX.Y.Z --target main --title vX.Y.Z --notes-file <notes>
   ```

   The workflow checks out that exact tag, rejects non-semantic tags, and verifies that
   the derived package version matches before publishing.

7. Watch the `Publish to PyPI` workflow through completion.

8. Verify the published metadata and files on PyPI, including the AGPL license, Python
   classifiers, source and issue links, and release notes.

9. Run isolated smoke tests against the released version:

   ```shell
   uvx metaproc@X.Y.Z --help
   uvx metaproc@X.Y.Z --version
   uvx metaproc@X.Y.Z skill metaproc
   uvx metaproc@X.Y.Z env --template
   ```

## Failure Handling

PyPI releases are immutable.
If publication succeeds with a defective artifact, fix the defect and publish a new
patch version; do not delete and reuse the version.

If the workflow fails before publication, fix the workflow or trusted-publisher
configuration on a branch, rerun `make verify`, and create the release only from the
validated commit.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
