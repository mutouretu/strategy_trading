from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from experiment_system import (
    ExperimentValidationError,
    collect_code_revisions,
    collect_git_revision,
)


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


@unittest.skipUnless(shutil.which("git"), "Git is not installed")
class GitProvenanceTests(unittest.TestCase):
    def test_clean_dirty_and_tagged_revisions_are_distinguished(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            _git(repository, "init", "-q")
            _git(repository, "config", "user.name", "Experiment Test")
            _git(
                repository,
                "config",
                "user.email",
                "experiment@example.invalid",
            )
            tracked = repository / "tracked.txt"
            tracked.write_text("baseline\n", encoding="utf-8")
            _git(repository, "add", "tracked.txt")
            _git(repository, "commit", "-q", "-m", "baseline")

            clean = collect_git_revision(repository)
            self.assertEqual(clean.commit, _git(repository, "rev-parse", "HEAD"))
            self.assertFalse(clean.dirty)
            self.assertIsNone(clean.dirty_fingerprint)

            tracked.write_text("changed\n", encoding="utf-8")
            dirty = collect_git_revision(repository)
            self.assertTrue(dirty.dirty)
            self.assertIsNotNone(dirty.dirty_fingerprint)

            (repository / "untracked.txt").write_text(
                "new content\n",
                encoding="utf-8",
            )
            with_untracked = collect_git_revision(repository)
            self.assertNotEqual(
                dirty.dirty_fingerprint,
                with_untracked.dirty_fingerprint,
            )

            tracked.write_text("baseline\n", encoding="utf-8")
            (repository / "untracked.txt").unlink()
            _git(repository, "tag", "experiment-v1")
            tagged = collect_git_revision(repository)
            self.assertFalse(tagged.dirty)
            self.assertEqual(tagged.tag, "experiment-v1")

    def test_collects_named_repositories_and_rejects_empty_input(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ExperimentValidationError,
            "at least one",
        ):
            collect_code_revisions({})


if __name__ == "__main__":
    unittest.main()
