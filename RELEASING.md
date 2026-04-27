# Releasing wlens

Releases are automated. Pushing a `v*` git tag triggers `.github/workflows/release.yml`, which runs the test matrix, builds the wheel + sdist with `uv build`, and publishes to PyPI via Trusted Publishing (no API token).

## One-time PyPI setup

Before the first release, register a Trusted Publisher on PyPI so the workflow can authenticate via OIDC.

1. Create an account at https://pypi.org if you don't have one.
2. Go to **Account → Publishing → Add a new pending publisher** and enter:
   - **PyPI project name:** `wlens`
   - **Owner:** `WhimsicalCode`
   - **Repository name:** `wlens`
   - **Workflow name:** `release.yml`
   - **Environment name:** `pypi`
3. In this GitHub repo, go to **Settings → Environments → New environment** and create one named `pypi`. (Optionally add required reviewers for an extra approval step before publish.)

After the first successful release the "pending" publisher becomes a regular publisher tied to the project.

## Cutting a release

1. Bump `version` in `pyproject.toml` (e.g. `0.1.0` → `0.1.1`).
2. Commit on `main`:
   ```
   git commit -am "chore: release v0.1.1"
   git push origin main
   ```
3. Wait for CI to go green on `main`.
4. Tag and push:
   ```
   git tag v0.1.1
   git push origin v0.1.1
   ```
5. Watch the **Release** workflow under the Actions tab. The publish job will appear as `pypi` in the environments list.

The workflow refuses to publish if the tag (`v0.1.1`) doesn't match the version in `pyproject.toml` (`0.1.1`), so a stale tag can't accidentally upload the wrong version.

PyPI version numbers can never be reused — even after yanking. If the publish fails for any reason, bump to the next patch version and tag again rather than re-tagging.

## Pre-release / dry run

For a dry run on TestPyPI, follow the same Trusted Publisher steps at https://test.pypi.org with a separate `pypi-test` environment, then duplicate the publish job in a workflow triggered manually with `workflow_dispatch` and pass `repository-url: https://test.pypi.org/legacy/` to the publish action.

Pre-release tags like `v0.1.0rc1` work with the existing workflow — PyPI accepts them and `pip` / `uv` only install them when explicitly requested.
