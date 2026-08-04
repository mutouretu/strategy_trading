from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ViewerRunModelTests(unittest.TestCase):
    @unittest.skipUnless(
        shutil.which("node"),
        "Node.js is not installed",
    )
    def test_v1_and_v2_normalization(self) -> None:
        subprocess.run(
            ["node", "run-model.test.js"],
            cwd=PROJECT_ROOT / "viewer",
            check=True,
        )


if __name__ == "__main__":
    unittest.main()
