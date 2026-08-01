"""Read-only Git provenance capture for participating repositories."""

from __future__ import annotations

import hashlib
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

from .errors import ExperimentValidationError
from .models import CodeRevision


def _git(
    repository: Path,
    arguments: list[str],
    *,
    text: bool,
) -> str | bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            capture_output=True,
            text=text,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ExperimentValidationError(
            f"cannot inspect Git repository {repository}: {exc}"
        ) from exc
    return completed.stdout


def _dirty_fingerprint(repository: Path) -> tuple[bool, str | None]:
    tracked_diff = _git(
        repository,
        ["diff", "--binary", "HEAD"],
        text=False,
    )
    assert isinstance(tracked_diff, bytes)
    untracked_output = _git(
        repository,
        ["ls-files", "--others", "--exclude-standard", "-z"],
        text=False,
    )
    assert isinstance(untracked_output, bytes)
    untracked_paths = [
        item.decode("utf-8", errors="surrogateescape")
        for item in untracked_output.split(b"\0")
        if item
    ]
    if not tracked_diff and not untracked_paths:
        return False, None

    digest = hashlib.sha256()
    digest.update(b"tracked-diff\0")
    digest.update(tracked_diff)
    for relative_name in sorted(untracked_paths):
        path = repository / relative_name
        digest.update(b"untracked\0")
        digest.update(
            relative_name.encode("utf-8", errors="surrogateescape")
        )
        digest.update(b"\0")
        if path.is_symlink():
            digest.update(b"symlink\0")
            digest.update(os.readlink(path).encode("utf-8"))
        else:
            digest.update(path.read_bytes())
    return True, digest.hexdigest()


def collect_git_revision(path: str | Path) -> CodeRevision:
    repository = Path(path).resolve()
    commit_output = _git(repository, ["rev-parse", "HEAD"], text=True)
    assert isinstance(commit_output, str)
    commit = commit_output.strip()
    dirty, dirty_fingerprint = _dirty_fingerprint(repository)
    tag_output = _git(
        repository,
        ["tag", "--points-at", commit],
        text=True,
    )
    assert isinstance(tag_output, str)
    tags = sorted(tag for tag in tag_output.splitlines() if tag)
    return CodeRevision(
        commit=commit,
        dirty=dirty,
        dirty_fingerprint=dirty_fingerprint,
        tag=tags[0] if tags else None,
    )


def collect_code_revisions(
    repositories: Mapping[str, str | Path],
) -> dict[str, CodeRevision]:
    if not repositories:
        raise ExperimentValidationError(
            "at least one participating repository is required"
        )
    return {
        name: collect_git_revision(path)
        for name, path in repositories.items()
    }
