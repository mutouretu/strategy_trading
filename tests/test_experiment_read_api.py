from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from experiment_system import (
    CodeRevision,
    ParquetMarketStore,
    SQLiteExperimentRepository,
    create_read_server,
    execute_experiment,
    parse_experiment_spec,
    plan_experiment,
)

from experiment_test_support import (
    executable_registry,
    single_experiment_document,
)


class ExperimentReadApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        registry, _ = executable_registry()
        document = single_experiment_document()
        document["experiment_id"] = "read-api-probe"
        document["seeds"] = [42, 43]
        document["controls"]["max_runs"] = 2
        self.plan = plan_experiment(
            parse_experiment_spec(document),
            registry,
            code_revisions={
                "market_simulator": CodeRevision(commit="a" * 40),
            },
        )
        self.database = self.root / "results" / "probe.sqlite3"
        execute_experiment(
            self.plan,
            registry=registry,
            repository=SQLiteExperimentRepository(self.database),
            market_store=ParquetMarketStore(self.root / "market_data"),
        )
        viewer_root = Path(__file__).resolve().parents[1] / "viewer"
        self.server = create_read_server(
            self.root / "results",
            viewer_root=viewer_root,
            port=0,
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temporary.cleanup()

    def _get(self, path: str) -> tuple[int, str, bytes]:
        with urlopen(f"{self.base_url}{path}", timeout=5) as response:
            return (
                response.status,
                response.headers.get_content_type(),
                response.read(),
            )

    def test_catalog_detail_filter_and_dynamic_viewer_routes(self) -> None:
        status, content_type, body = self._get("/api/experiments")
        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/json")
        catalog = json.loads(body)
        self.assertEqual(catalog["total"], 1)
        self.assertEqual(
            catalog["items"][0]["experiment_id"],
            "read-api-probe",
        )

        _, _, body = self._get("/api/experiments/read-api-probe")
        detail = json.loads(body)
        self.assertEqual(detail["planned_run_count"], 2)
        self.assertEqual(detail["status_counts"], {"SUCCEEDED": 2})

        _, _, body = self._get(
            "/api/experiments/read-api-probe/runs"
            "?seed=43&sort=seed&order=desc"
        )
        runs = json.loads(body)
        self.assertEqual(runs["total"], 1)
        self.assertEqual(runs["items"][0]["seed"], 43)
        self.assertIn(
            "result.final_equity",
            runs["items"][0]["summary_scalars"],
        )

        run_id = self.plan.runs[0].run_id
        _, _, body = self._get(
            f"/api/experiments/read-api-probe/runs/{run_id}"
        )
        run_detail = json.loads(body)
        self.assertEqual(run_detail["run_id"], run_id)
        self.assertEqual(run_detail["trace_state"], "STORED")

        before_json = set(self.root.rglob("*.json"))
        _, _, body = self._get(
            f"/api/experiments/read-api-probe/runs/{run_id}/viewer"
        )
        viewer = json.loads(body)
        self.assertEqual(viewer["schema_version"], 2)
        self.assertEqual(len(viewer["market"]), 6)
        self.assertEqual(len(viewer["fills"]), 3)
        self.assertEqual(set(self.root.rglob("*.json")), before_json)

    def test_csv_static_assets_validation_and_read_only_methods(self) -> None:
        _, content_type, body = self._get(
            "/api/experiments/read-api-probe/comparison.csv"
        )
        self.assertEqual(content_type, "text/csv")
        self.assertIn(b"summary:result.final_equity", body)
        self.assertEqual(body.count(b"\n"), 3)

        _, content_type, body = self._get("/experiments.html")
        self.assertEqual(content_type, "text/html")
        self.assertIn(b"Simulation Experiments", body)

        with self.assertRaises(HTTPError) as context:
            self._get(
                "/api/experiments/read-api-probe/runs?limit=0"
            )
        self.assertEqual(context.exception.code, 400)
        error = json.loads(context.exception.read())
        self.assertIn("limit", error["error"]["message"])

        request = Request(
            f"{self.base_url}/api/experiments",
            data=b"{}",
            method="POST",
        )
        with self.assertRaises(HTTPError) as context:
            urlopen(request, timeout=5)
        self.assertEqual(context.exception.code, 405)


if __name__ == "__main__":
    unittest.main()
