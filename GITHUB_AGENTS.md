# GITHUB_AGENTS.md - GitHub Automation Guide for File Fridge

## Overview

This document describes GitHub-based automation agents and bots working with the File Fridge repository. It covers workflows, actions, security scanning, dependency management, and integration with GitHub's automation ecosystem.

**Target Audience:** DevOps engineers, repository maintainers, GitHub automation tools

---

## Quick Reference

**Repository:** EasyCloudDeploy/file-fridge
**Primary Branch:** `main`
**CI/CD Platform:** GitHub Actions
**Container Registry:** Docker Hub (`martinoj2009/file-fridge`)
**Workflow Count:** 1 active workflow

**Key Integrations:**
- Docker Hub (multi-platform builds)
- GitHub Actions (CI/CD)

---

## Active GitHub Workflows

### 1. Container Build and Push

**File:** `.github/workflows/container-byuild-push.yml`
**Purpose:** Build and publish Docker images to Docker Hub

**Triggers:**
- Push to `main` branch
- Git tags matching `v*` pattern
- Manual workflow dispatch

**Platforms Built:**
- `linux/amd64` (x86_64)
- `linux/arm64` (ARM64/Apple Silicon)

**Workflow Steps:**

```yaml
1. Checkout code (actions/checkout@v4)
   ↓
2. Set up Docker Buildx (docker/setup-buildx-action@v3)
   ↓
3. Extract version from VERSION file
   - Reads VERSION file contents
   - Defaults to "latest" if not found
   ↓
4. Determine tags
   - Always: {repo}:latest
   - Git tag: {repo}:{tag_name}
   - VERSION file: {repo}:{version}
   - Git SHA: {repo}:{short_sha}
   ↓
5. Log in to Docker Hub (docker/login-action@v3)
   - Uses DOCKERHUB_USERNAME secret
   - Uses DOCKERHUB_TOKEN secret
   ↓
6. Build and push (docker/build-push-action@v5)
   - Multi-platform build
   - Registry cache for speed
   - Pushes all tags
   ↓
7. Output image digest
```

**Example Tags Generated:**

```
Push to main:
  martinoj2009/file-fridge:latest
  martinoj2009/file-fridge:0.0.22
  martinoj2009/file-fridge:abc1234

Tag v1.0.0:
  martinoj2009/file-fridge:latest
  martinoj2009/file-fridge:v1.0.0
  martinoj2009/file-fridge:1.0.0
  martinoj2009/file-fridge:0.0.22
  martinoj2009/file-fridge:abc1234
```

**Required Secrets:**
- `DOCKERHUB_USERNAME` - Docker Hub username
- `DOCKERHUB_TOKEN` - Docker Hub access token (not password!)

**Permissions Required:**
```yaml
permissions:
  contents: read      # Read repository code
  packages: write     # Write to container registry
```

**Performance Optimizations:**
- BuildKit cache via registry
- Cache mode: max (cache all layers)
- Cache reference: `martinoj2009/file-fridge:buildcache`

**Workflow Duration:**
- ~5-10 minutes for multi-platform build (depends on cache hit rate)

---

## Recommended Additional Workflows

### 1. Pull Request Validation (Recommended)

**Purpose:** Validate code quality on PRs before merge

**Suggested Workflow:** `.github/workflows/pr-validation.yml`

```yaml
name: Pull Request Validation

on:
  pull_request:
    branches: [main]

jobs:
  lint-and-format:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        run: curl -LsSf https://astral.sh/uv/install.sh | sh

      - name: Install dependencies
        run: uv sync

      - name: Check Black formatting
        run: uv run black --check app/

      - name: Check Ruff linting
        run: uv run ruff check app/

  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        run: curl -LsSf https://astral.sh/uv/install.sh | sh

      - name: Install dependencies
        run: uv sync

      - name: Run tests
        run: uv run pytest --cov=app

      - name: Upload coverage
        uses: codecov/codecov-action@v3
        with:
          files: ./coverage.xml

  validate-migrations:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        run: curl -LsSf https://astral.sh/uv/install.sh | sh

      - name: Install dependencies
        run: uv sync

      - name: Check for pending migrations
        run: |
          uv run alembic upgrade head
          uv run alembic check

  docker-build-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Build Docker image
        run: docker build -t file-fridge:test .

      - name: Test Docker image
        run: |
          docker run -d -p 8000:8000 \
            -v $(pwd)/test-data:/app/data \
            file-fridge:test
          sleep 10
          curl -f http://localhost:8000/health || exit 1
```

### 2. Dependency Update Automation (Dependabot)

**Purpose:** Automatically create PRs for dependency updates

**Configuration:** `.github/dependabot.yml`

```yaml
version: 2
updates:
  # Python dependencies
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
      day: "monday"
    open-pull-requests-limit: 5
    reviewers:
      - "maintainer-username"
    labels:
      - "dependencies"
      - "python"
    commit-message:
      prefix: "deps"
      include: "scope"

  # GitHub Actions
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "monthly"
    open-pull-requests-limit: 3
    reviewers:
      - "maintainer-username"
    labels:
      - "dependencies"
      - "github-actions"
    commit-message:
      prefix: "ci"

  # Docker base images
  - package-ecosystem: "docker"
    directory: "/"
    schedule:
      interval: "weekly"
    open-pull-requests-limit: 2
    reviewers:
      - "maintainer-username"
    labels:
      - "dependencies"
      - "docker"
    commit-message:
      prefix: "deps"
```

**Note:** Dependabot uses `pip` for Python dependencies. Since this project uses `uv`, you may need to:
- Convert Dependabot PRs to use `uv add package@version`
- Or disable Python Dependabot and use manual updates

### 3. Security Scanning (CodeQL)

**Purpose:** Automated security vulnerability scanning

**Configuration:** `.github/workflows/codeql.yml`

```yaml
name: CodeQL Security Scan

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  schedule:
    - cron: '0 0 * * 1'  # Weekly on Monday

jobs:
  analyze:
    name: Analyze
    runs-on: ubuntu-latest
    permissions:
      actions: read
      contents: read
      security-events: write

    strategy:
      fail-fast: false
      matrix:
        language: ['python', 'javascript']

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Initialize CodeQL
        uses: github/codeql-action/init@v2
        with:
          languages: ${{ matrix.language }}
          queries: security-and-quality

      - name: Autobuild
        uses: github/codeql-action/autobuild@v2

      - name: Perform CodeQL Analysis
        uses: github/codeql-action/analyze@v2
```

### 4. Release Automation

**Purpose:** Automate release creation and changelog generation

**Configuration:** `.github/workflows/release.yml`

```yaml
name: Create Release

on:
  push:
    tags:
      - 'v*'

jobs:
  create-release:
    runs-on: ubuntu-latest
    permissions:
      contents: write

    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Generate changelog
        id: changelog
        run: |
          # Extract version
          VERSION=${GITHUB_REF#refs/tags/}
          echo "version=$VERSION" >> $GITHUB_OUTPUT

          # Generate changelog from commits
          CHANGELOG=$(git log $(git describe --tags --abbrev=0 HEAD^)..HEAD \
            --pretty=format:"- %s (%h)" \
            --no-merges)
          echo "changelog<<EOF" >> $GITHUB_OUTPUT
          echo "$CHANGELOG" >> $GITHUB_OUTPUT
          echo "EOF" >> $GITHUB_OUTPUT

      - name: Create Release
        uses: actions/create-release@v1
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        with:
          tag_name: ${{ github.ref }}
          release_name: Release ${{ steps.changelog.outputs.version }}
          body: |
            ## Changes in ${{ steps.changelog.outputs.version }}

            ${{ steps.changelog.outputs.changelog }}

            ## Docker Images

            ```bash
            docker pull martinoj2009/file-fridge:${{ steps.changelog.outputs.version }}
            ```

            See [CHANGELOG.md](CHANGELOG.md) for full details.
          draft: false
          prerelease: false
```

### 5. Documentation Deployment

**Purpose:** Deploy documentation to GitHub Pages

**Configuration:** `.github/workflows/docs.yml`

```yaml
name: Deploy Documentation

on:
  push:
    branches: [main]
    paths:
      - 'docs/**'
      - '*.md'
      - 'mkdocs.yml'

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.12'

      - name: Install mkdocs
        run: pip install mkdocs mkdocs-material

      - name: Build documentation
        run: mkdocs build

      - name: Deploy to GitHub Pages
        uses: peaceiris/actions-gh-pages@v3
        with:
          github_token: ${{ secrets.GITHUB_TOKEN }}
          publish_dir: ./site
```

---

## GitHub Actions Best Practices

### 1. Secrets Management

**Current Secrets:**
- `DOCKERHUB_USERNAME` - Public username (could be in env vars)
- `DOCKERHUB_TOKEN` - Access token (correct - not using password)

**Security Best Practices:**
- ✅ Use tokens, not passwords
- ✅ Limit token scope (read/write packages only)
- ✅ Rotate tokens regularly (every 90 days)
- ✅ Use organization secrets for shared credentials
- ❌ Never commit secrets to repository
- ❌ Never echo secrets in logs

**Adding New Secrets:**
```
Repository → Settings → Secrets and variables → Actions → New repository secret
```

### 2. Workflow Security

**Permissions:**
```yaml
# ✅ GOOD - Minimal permissions
permissions:
  contents: read
  packages: write

# ❌ BAD - Excessive permissions
permissions: write-all
```

**Third-Party Actions:**
```yaml
# ✅ GOOD - Pinned to SHA
- uses: actions/checkout@8e5e7e5ab8b370d6c329ec480221332ada57f0ab

# ⚠️  OK - Pinned to major version
- uses: actions/checkout@v4

# ❌ BAD - Unpinned, could change unexpectedly
- uses: actions/checkout@main
```

**Pull Request Workflows:**
```yaml
# ⚠️  DANGEROUS - Don't trigger on pull_request_target
on:
  pull_request_target:  # Has write access to repo!

# ✅ SAFE - Use pull_request for validation
on:
  pull_request:  # Read-only access
```

### 3. Performance Optimization

**Caching:**
```yaml
# Docker layer caching
- name: Build and push
  uses: docker/build-push-action@v5
  with:
    cache-from: type=registry,ref=${{ env.DOCKER_REPO }}:buildcache
    cache-to: type=registry,ref=${{ env.DOCKER_REPO }}:buildcache,mode=max

# Python dependency caching
- name: Cache uv
  uses: actions/cache@v3
  with:
    path: ~/.cache/uv
    key: ${{ runner.os }}-uv-${{ hashFiles('**/pyproject.toml') }}
```

**Matrix Builds:**
```yaml
# Parallel testing across Python versions
strategy:
  matrix:
    python-version: ['3.8', '3.9', '3.10', '3.11', '3.12']
```

**Conditional Steps:**
```yaml
# Skip expensive steps on documentation-only changes
- name: Build Docker
  if: |
    !contains(github.event.head_commit.message, '[docs only]')
```

### 4. Error Handling

**Continue on Error:**
```yaml
# Don't fail entire workflow if one linter fails
- name: Lint with Ruff
  continue-on-error: true
  run: uv run ruff check app/
```

**Fail Fast:**
```yaml
# Stop all jobs if one fails (default)
strategy:
  fail-fast: true

# Continue all jobs even if one fails
strategy:
  fail-fast: false
```

**Timeout Protection:**
```yaml
jobs:
  build:
    timeout-minutes: 30  # Prevent hung workflows
```

---

## GitHub Bots and Integrations

### 1. Dependabot (Recommended)

**Configuration:** `.github/dependabot.yml`

**Features:**
- Automatic dependency updates
- Security vulnerability alerts
- PR creation for outdated packages

**Customization:**
```yaml
# Group related updates
groups:
  fastapi-stack:
    patterns:
      - "fastapi"
      - "uvicorn"
      - "pydantic"
  database-stack:
    patterns:
      - "sqlalchemy"
      - "alembic"
```

**Review Process:**
1. Dependabot creates PR
2. CI runs tests
3. Review changes
4. Merge if tests pass

### 2. CodeQL (Recommended)

**Purpose:** Automated code security analysis

**Features:**
- SQL injection detection
- XSS vulnerability scanning
- Secret detection
- Dependency vulnerability scanning

**Integration:**
```
Repository → Security → Code scanning → Set up CodeQL
```

### 3. Renovate Bot (Alternative to Dependabot)

**Advantages over Dependabot:**
- Better support for monorepos
- More customization options
- Can update non-standard dependency files

**Configuration:** `.github/renovate.json`

```json
{
  "$schema": "https://docs.renovatebot.com/renovate-schema.json",
  "extends": ["config:base"],
  "packageRules": [
    {
      "matchPackagePatterns": ["fastapi", "uvicorn", "pydantic"],
      "groupName": "fastapi-stack",
      "schedule": ["before 3am on Monday"]
    }
  ]
}
```

### 4. Stale Bot

**Purpose:** Close inactive issues/PRs

**Configuration:** `.github/workflows/stale.yml`

```yaml
name: Close Stale Issues

on:
  schedule:
    - cron: '0 0 * * *'

jobs:
  stale:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/stale@v8
        with:
          repo-token: ${{ secrets.GITHUB_TOKEN }}
          stale-issue-message: 'This issue has been inactive for 60 days and will be closed in 7 days if no further activity occurs.'
          stale-pr-message: 'This PR has been inactive for 30 days and will be closed in 7 days if no further activity occurs.'
          days-before-stale: 60
          days-before-close: 7
          stale-issue-label: 'stale'
          stale-pr-label: 'stale'
```

### 5. Semantic Release

**Purpose:** Automated version bumping and changelog generation

**Configuration:** `.releaserc.json`

```json
{
  "branches": ["main"],
  "plugins": [
    "@semantic-release/commit-analyzer",
    "@semantic-release/release-notes-generator",
    "@semantic-release/changelog",
    "@semantic-release/github",
    [
      "@semantic-release/exec",
      {
        "prepareCmd": "echo ${nextRelease.version} > VERSION"
      }
    ],
    [
      "@semantic-release/git",
      {
        "assets": ["VERSION", "CHANGELOG.md"],
        "message": "chore(release): ${nextRelease.version} [skip ci]"
      }
    ]
  ]
}
```

---

## Workflow Troubleshooting

### Common Issues

**1. Docker Build Fails - Layer Cache Miss**

```
Error: failed to solve: failed to compute cache key
```

**Solution:**
```yaml
# Add fallback cache sources
cache-from: |
  type=registry,ref=${{ env.DOCKER_REPO }}:buildcache
  type=registry,ref=${{ env.DOCKER_REPO }}:latest
```

**2. Docker Hub Authentication Fails**

```
Error: denied: requested access to the resource is denied
```

**Solutions:**
- Verify DOCKERHUB_TOKEN is valid (not expired)
- Check token has write permissions
- Ensure username matches token owner
- Try regenerating token

**3. Workflow Doesn't Trigger**

**Checklist:**
- Is workflow file in `.github/workflows/`?
- Is YAML syntax valid? (use yamllint)
- Does trigger match event? (push vs pull_request)
- Is branch correct? (`main` vs `master`)
- Is workflow enabled? (Actions tab)

**4. Secrets Not Available**

```
Error: secret DOCKERHUB_TOKEN not found
```

**Solutions:**
- Check secret name matches exactly (case-sensitive)
- Verify secret is set at correct level (repo vs org)
- For fork PRs, secrets aren't available (security)

**5. Version File Not Found**

```
Warning: VERSION file not found, using 'latest'
```

**Solution:**
- Ensure VERSION file exists in repository root
- Check file is committed and pushed
- Verify file has no extension (.txt, etc.)

### Debug Mode

**Enable Debug Logging:**

```
Repository → Settings → Secrets → Add:
  ACTIONS_STEP_DEBUG = true
  ACTIONS_RUNNER_DEBUG = true
```

**Workflow Debug Output:**
```yaml
- name: Debug information
  run: |
    echo "Event: ${{ github.event_name }}"
    echo "Ref: ${{ github.ref }}"
    echo "SHA: ${{ github.sha }}"
    env
```

---

## Monitoring and Notifications

### 1. Workflow Status Badges

**README Badge:**
```markdown
![CI/CD](https://github.com/EasyCloudDeploy/file-fridge/workflows/File%20Fridge%20Build%20and%20Push/badge.svg)
```

### 2. Email Notifications

**GitHub Settings:**
```
User Settings → Notifications → Actions
  ✅ Notify on workflow failures
  ✅ Notify on workflow runs you triggered
  ⬜ Notify on all workflow runs (noisy)
```

### 3. Slack Integration

**Workflow Notifications:**
```yaml
- name: Slack Notification
  if: failure()
  uses: 8398a7/action-slack@v3
  with:
    status: ${{ job.status }}
    text: 'Build failed on ${{ github.ref }}'
    webhook_url: ${{ secrets.SLACK_WEBHOOK }}
```

### 4. Status Checks

**Branch Protection:**
```
Repository → Settings → Branches → main → Add rule
  ✅ Require status checks to pass
    - Lint and format
    - Tests
    - Docker build
  ✅ Require branches to be up to date
```

---

## Cost Optimization

### GitHub Actions Usage

**Free Tier (Public Repos):**
- Unlimited minutes for public repositories
- 2,000 minutes/month for private repositories

**Usage Tracking:**
```
Repository → Insights → Actions
  - View minutes used
  - See workflow run history
  - Identify expensive workflows
```

**Cost Reduction Tips:**

1. **Use Self-Hosted Runners:**
   ```yaml
   runs-on: self-hosted  # No minutes charged
   ```

2. **Cache Aggressively:**
   ```yaml
   - uses: actions/cache@v3
   ```

3. **Skip Redundant Builds:**
   ```yaml
   on:
     push:
       paths-ignore:
         - '**.md'
         - 'docs/**'
   ```

4. **Optimize Docker Builds:**
   ```yaml
   # Multi-stage builds reduce layer size
   # BuildKit cache reduces rebuild time
   ```

---

## Security Considerations

### 1. Workflow Security

**Code Injection Prevention:**
```yaml
# ❌ DANGEROUS - User input in run command
- name: Print PR title
  run: echo "${{ github.event.pull_request.title }}"

# ✅ SAFE - Use environment variable
- name: Print PR title
  env:
    PR_TITLE: ${{ github.event.pull_request.title }}
  run: echo "$PR_TITLE"
```

**Script Injection:**
```yaml
# ✅ Use $GITHUB_ENV instead of set-output
- name: Set variable
  run: echo "VERSION=1.0.0" >> $GITHUB_ENV
```

### 2. Dependency Security

**Verified Actions:**
```yaml
# ✅ Use verified creators
- uses: actions/checkout@v4        # GitHub official
- uses: docker/build-push-action@v5  # Docker official

# ⚠️  Verify third-party actions
- uses: unknown-user/action@v1     # Review before use
```

**SBOM Generation:**
```yaml
- name: Generate SBOM
  uses: anchore/sbom-action@v0
  with:
    image: martinoj2009/file-fridge:latest
    format: cyclonedx-json
```

### 3. Secret Scanning

**Enable Secret Scanning:**
```
Repository → Settings → Security → Secret scanning
  ✅ Enable secret scanning
  ✅ Enable push protection
```

**Leaked Secret Response:**
1. Revoke compromised credential immediately
2. Generate new secret
3. Update in GitHub Secrets
4. Audit access logs for unauthorized use

---

## Maintenance Schedule

### Weekly
- Review failed workflow runs
- Check Dependabot PRs
- Monitor security alerts

### Monthly
- Review workflow efficiency
- Update pinned action versions
- Audit secrets and rotate if needed
- Review cache hit rates

### Quarterly
- Review and update workflow logic
- Optimize expensive workflows
- Update documentation
- Review security scan results

### Annually
- Full security audit
- Review automation strategy
- Update CI/CD best practices
- Evaluate new GitHub features

---

## Migration Guide

### From Other CI/CD Platforms

**Jenkins → GitHub Actions:**
```
Jenkinsfile stages → workflow jobs
Jenkins agents → runs-on
Jenkins credentials → GitHub secrets
Jenkins pipeline → YAML workflow
```

**GitLab CI → GitHub Actions:**
```
.gitlab-ci.yml → .github/workflows/*.yml
stages → jobs
before_script → jobs.<job>.steps
variables → env
```

**Travis CI → GitHub Actions:**
```
.travis.yml → .github/workflows/*.yml
script → jobs.<job>.steps.run
matrix → strategy.matrix
```

---

## Version History

**Current Version:** 1.0
**Last Updated:** 2026-01-17
**Active Workflows:** 1 (Container Build and Push)

**Changelog:**
- 2026-01-17: Initial documentation
- Active: Container build and push to Docker Hub
- Recommended: PR validation, Dependabot, CodeQL

---

## See Also

- `CLAUDE.md` - Codebase documentation for AI assistants
- `AGENTS.md` - AI agent integration guide
- `README.md` - User documentation
- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [Docker Hub](https://hub.docker.com/r/martinoj2009/file-fridge)

---

**Maintained By:** File Fridge Development Team
**Repository:** https://github.com/EasyCloudDeploy/file-fridge
