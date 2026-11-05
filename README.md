# Release workflows

Shared publishing components for zekihan repositories. The caller owns its native
checks, project build prerequisites and release metadata. Ordinary CI does not
build images or cross-compile release matrices.

`decide.yaml` selects nightly/manual work, validates stable tags and suppresses
automatic retries of failed source revisions. `image.yaml` builds an OCI archive
without publishing credentials and copies that exact archive to both registries.
`release.yaml` prepares release assets, publishes charts, verifies immutable image
versions, promotes `latest` forward and creates the GitHub release last.

Callers must serialize their publishing workflow with one repository-wide
concurrency group and `cancel-in-progress: false`. Both development and stable
publication must use that group. Run the caller's native checks on the same
`github.sha` after the decision job and before invoking either publisher. Pass
`publishing: true` to the native-check workflow to include container smoke checks.

Development publication is main-only, scheduled or manually dispatched. `force`
rebuilds unchanged inputs; it never permits replacing an immutable release or
moving a channel backward. A failed revision is skipped on later scheduled runs.
An explicit dispatch or GitHub rerun permits recovery. Registry failures never
count as evidence that a previous publication succeeded.

Stable versions use `vX.Y.Z` Git tags and unprefixed image tags. Development images
use `edge`. The independently versioned upstream-image profile publishes declared
`<version>-r<revision>` versions and promotes `latest` by source history, preserving
composite upstream version strings. An unchanged declaration can receive new
dependencies on `edge` without changing its existing immutable release.

The two registry updates are not transactional. If one fails, rerun publication
using the retained artifact. Do not overwrite a published version with a rebuilt
archive that has a different digest. Use a new version for changed release content.
Artifacts remain available for seven days; immutable registry content remains the
source of truth afterward.

Pin workflow callers to a full reviewed commit. Helpers under `scripts/` are
embedded in generated YAML, so that pin covers the executable policy rather than
fetching scripts from a branch. The shared repository must be public for public
callers. No secret values belong here.

## Development

Install `requirements-dev.txt` into a virtual environment and install actionlint.
Run `make generate lint test` after changes. Generated YAML is checked for drift.
The tests cover failure suppression, forced/manual recovery, missing mirrors,
immutable conflicts and stale promotion.

Publishing requires explicit operational authorization. Creating or editing this
local repository does not publish workflows, images, charts or releases.
