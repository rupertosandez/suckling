"""
Version constant for the bot. Update this when shipping changes.

Versioning follows SemVer (MAJOR.MINOR.PATCH):
  MAJOR — breaking changes (new required env var, removed commands, etc.)
  MINOR — new features that don't break existing ones
  PATCH — bug fixes, copy tweaks, internal refactors

A fourth segment (MAJOR.MINOR.PATCH.BUILD) may be used for backend-only builds
that members would never notice, to avoid burning a patch number on internal
work. VERSION is only ever compared as an opaque string, so a fourth segment is
safe. Keep the matching CHANGELOG.md header in sync with this string.
"""

VERSION = "2.10.0"
