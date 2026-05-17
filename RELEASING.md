# Releasing

This package publishes from `main` through GitHub Actions.

## Branch Strategy

- `dev` is the integration branch.
- feature work should branch from `dev` and merge back into `dev`.
- `main` is the release branch.
- releases happen only when `dev` is merged into `main`.

Workflow placement:

- [`.github/workflows/pr-quality.yml`](./.github/workflows/pr-quality.yml) should exist on both `dev` and `main`
- [`.github/workflows/pr-security.yml`](./.github/workflows/pr-security.yml) should exist on both `dev` and `main`
- [`.github/workflows/release.yml`](./.github/workflows/release.yml) must exist on `main`
- this release guide should also exist on `main`

## One-Time Setup

Choose one publish method:

- preferred: configure GitHub Actions as a PyPI trusted publisher for this project
- fallback: configure `PYPI_API_TOKEN` as a repository secret

The release workflow uses:

- `GITHUB_TOKEN` to create the GitHub release and tag notes
- `id-token: write` for trusted publishing to PyPI

## Release Flow

1. Create a release PR from `dev` to `main`.
2. In that PR, update `version` in [pyproject.toml](./pyproject.toml).
3. Merge the PR into `main`.
4. Create and push a matching semantic-version tag like `1.0.1`.
5. GitHub Actions will:
   - verify governance files and `CODEOWNERS`
   - run lint, typecheck, unit tests, and package build
   - run Trivy and Gitleaks scans
   - publish the package to PyPI
   - create a GitHub release tagged with that version

The workflow does nothing for ordinary pushes to `main` without a release tag.

## Manual Recovery

The release workflow also supports manual dispatch from the GitHub Actions UI.

Use that when:

- the tagged release failed before publish
- PyPI publish succeeded but the GitHub release was not created
- you need to retry the release logic without creating a new merge

When running it manually, provide the existing semantic-version tag, for example `1.0.1`.

## What To Push Where

Normal development:

- push feature code, docs, and tests to a feature branch based on `dev`
- merge that branch into `dev`

Release infrastructure:

- keep [`.github/workflows/pr-quality.yml`](./.github/workflows/pr-quality.yml) in `dev` and `main`
- keep [`.github/workflows/pr-security.yml`](./.github/workflows/pr-security.yml) in `dev` and `main`
- keep [`.github/workflows/release.yml`](./.github/workflows/release.yml) in `dev` so it is reviewed there, but it must be merged into `main` before automated releases can work
- keep [RELEASING.md](./RELEASING.md) in `dev` and `main`

Actual release:

- bump [pyproject.toml](./pyproject.toml) version on `dev` in the release PR or release commit
- merge `dev` into `main`
- create and push the release tag from `main`
- do not bump version for normal `dev` merges unless that merge is intended to release

## Local Verification

```bash
uv sync --group dev
uv run ruff check openbox tests
uv run pyright openbox
uv run pytest tests/unit
uv build
```
