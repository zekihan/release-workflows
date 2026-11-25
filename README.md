# Release workflows

Shared Go checks and publishing components for zekihan repositories. The caller
owns its project build prerequisites and release metadata. Ordinary CI defaults
to source checks and a single-target GoReleaser snapshot.

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
composite upstream version strings. Each declarative config states its profile:
`versioning: upstream` requires a positive `revision` and publishes
`<version>-r<revision>`; `versioning: own` forbids `revision` and publishes the
image's own stable `X.Y.Z`. A missing or unknown profile fails the decision, so a
dropped `revision` cannot turn an upstream image into a self-versioned one. An unchanged declaration can receive new
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

The reusable workflows accept a string `runs-on` input that defaults to
`ubuntu-latest`. Pass a self-hosted Linux runner label to run the jobs there.
Provide Git, Bash, Python 3 (standard library only), and the GitHub CLI on the
runner. Image jobs need a Docker daemon accessible to the runner user and set
up Buildx with an action. Registry jobs install skopeo with sudo and apt when
it is missing. Image smoke checks install skopeo with sudo and apt when enabled.
Chart jobs install Helm with a setup action.

On a self-hosted x64 runner, build arm64 images by cross-compiling when the
Dockerfile can package the binaries without executing target-platform code.
`qemu` defaults to `false`, so omit it unless the build needs emulation.
Set `qemu: true` when the build needs to execute arm64 code.
`docker/setup-qemu-action` requires a Docker daemon the runner user can access.
Reserve `native-arm: true` for builds that should use the pinned GitHub-hosted
arm64 runner.

## Calling permissions and secrets

The caller must grant the called jobs the token permissions they request:
`decide.yaml` needs `contents: read`, `actions: read` and `packages: read`;
`image.yaml` needs `contents: read` and `packages: write`; and `release.yaml`
needs `contents: write` and `packages: write`. Pass `DOCKER_USERNAME` and
`DOCKER_PASSWORD` to `image.yaml`. They are optional in the other workflow
interfaces, but are needed when their `image` input is set because those jobs
log in to Docker Hub. The GitHub token is used for GHCR authentication.

`decide.yaml` returns `build`, `version`, and `fingerprint`; pass the latter
values to `image.yaml` when building. `image.yaml` returns `digest`, which must
be passed as `image-digest` to `release.yaml` when releasing an image.
`release.yaml` defaults to building binaries and creating a GitHub release;
set `binaries` or `github-release` to `false` to disable those operations.

## Go checks

`go-checks.yaml` needs only `contents: read`. It checks out the caller's event
SHA, sets up Go from `go.mod`, and runs `make tidy`, govulncheck, golangci-lint,
`make lint`, the tracked-file diff check, `make test`, and the GoReleaser
snapshot build. Define frontend and race tests in the caller's `make test`.

| Input | Behavior |
| --- | --- |
| `build-tags` | Optional comma-separated Go build tags for Go commands and golangci-lint |
| `node-version` | Set up Node when supplied |
| `node-cache-dependency-path` | Enable npm caching with this lockfile path when Node is set up |
| `prepare-command` | Optional Bash command run from the checkout before source checks |
| `lint-version` | Override the golangci-lint action's version selection |
| `goreleaser-version` | Defaults to the GoReleaser action's `~> v2` range |
| `goreleaser-id` | Snapshot build ID, defaults to `all`. Pass an empty string to skip the snapshot |
| `chart-path` | Enable a separate Helm lint job for this chart |
| `container-context` | Enable a separate Buildx job for this local Docker context with push disabled |

Keep triggers, concurrency and any local `workflow_call` interface in the
caller. To preserve an existing required `pr` check, name the reusable call
`go-checks` and keep a local job named `pr` with `needs: go-checks`. Run the
gate even after a failed or skipped dependency and require
`needs.go-checks.result == 'success'` for the gate to pass. GitHub prefixes the
individual reusable jobs with the calling job name.

## Development

Install `requirements-dev.txt` into a virtual environment and install actionlint.
Install skopeo to run the native archive merge tests; those tests are skipped
locally when skopeo is unavailable, but CI requires it.
Install Helm to run the chart lint regression test.
Run `make generate lint test` after changes. Generated YAML is checked for drift.
The tests cover failure suppression, forced/manual recovery, missing mirrors,
immutable conflicts and stale promotion.

Publishing requires explicit operational authorization. Creating or editing this
local repository does not publish workflows, images, charts or releases.

Image callers default to anonymous-pull verification after authenticated digest
verification. Set `public-image: false` for private images; this does not disable
authenticated verification. Make new public image/chart packages accessible before
accepting the first publication. The shared repo itself must also be public.

Use `native-arm: true` for native amd64/arm64 compilation (Proton Mail Bridge).
The publisher verifies both build artifacts and combines their OCI indexes with
embedded code. No emulated tests run. `smoke-command` loads the host architecture
from the prepared archive before upload, for the Postgres acceptance suite.
