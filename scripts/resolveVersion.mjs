/**
 * resolveVersion.mjs — compute the build's version string.
 *
 * Resolution order (first hit wins):
 *   1. NUBI_VERSION env var — an explicit stamp from release tooling. This is
 *      how CI / the cloud deploy inject a real version when the build context
 *      has no git metadata (e.g. inside Docker, where .git is dockerignored).
 *   2. Git: if HEAD is EXACTLY a version tag (vX.Y.Z) → the clean release
 *      "X.Y.Z"; otherwise a dev build → "X.Y.Z-dev.<shortsha>" (with a
 *      ".dirty" suffix when the working tree has uncommitted changes), where
 *      X.Y.Z is the base version from package.json.
 *   3. Fallback: the bare package.json version (no git available at all).
 *
 * The point: branch/dev builds get a UNIQUE, semver-valid version instead of a
 * static placeholder, so any consumer's version check (e.g. an embedder pinning
 * a nubi-embed version) stays meaningful and never needs a "*" wildcard.
 */

import { execSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

/**
 * @param {string} [repoRoot]  Repo root to read package.json / run git in.
 * @returns {string}  A semver-valid version string.
 */
export function resolveVersion(repoRoot = process.cwd()) {
  // 1. Explicit stamp from release tooling — always wins.
  const override = (process.env.NUBI_VERSION || '').trim()
  if (override) return override.replace(/^v/, '')

  const base = JSON.parse(
    readFileSync(resolve(repoRoot, 'package.json'), 'utf-8'),
  ).version

  const git = (cmd) => {
    try {
      return execSync(cmd, {
        cwd: repoRoot,
        stdio: ['ignore', 'pipe', 'ignore'],
      })
        .toString()
        .trim()
    } catch {
      return ''
    }
  }

  // 2a. HEAD exactly on a version tag → clean release version.
  const exactTag = git('git describe --tags --exact-match HEAD')
  if (exactTag) return exactTag.replace(/^v/, '')

  // 2b. Dev build → X.Y.Z-dev.<shortsha>(.dirty)
  const sha = git('git rev-parse --short=12 HEAD')
  if (!sha) return base // 3. no git metadata → bare base version
  const dirty = git('git status --porcelain') ? '.dirty' : ''
  return `${base}-dev.${sha}${dirty}`
}
