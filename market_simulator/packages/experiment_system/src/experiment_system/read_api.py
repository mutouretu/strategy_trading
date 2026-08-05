"""Local read-only HTTP API and static Viewer host."""

from __future__ import annotations

import json
from functools import partial
from http.server import (
    SimpleHTTPRequestHandler,
    ThreadingHTTPServer,
)
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .comparison import ExperimentCatalog, RunQuery
from .errors import (
    ExperimentAccessError,
    ExperimentRepositoryError,
    ExperimentValidationError,
)
from .exports import (
    comparison_csv_text,
    comparison_table,
    viewer_document,
)
from .market_path_catalog import MarketPathSetCatalog


def default_viewer_root() -> Path:
    return Path(__file__).resolve().parents[4] / "viewer"


def _single(
    query: dict[str, list[str]],
    name: str,
) -> str | None:
    values = query.get(name, [])
    if not values:
        return None
    if len(values) != 1:
        raise ExperimentValidationError(
            f"query parameter {name!r} may be provided only once"
        )
    return values[0]


def _integer(
    query: dict[str, list[str]],
    name: str,
    *,
    default: int | None = None,
) -> int | None:
    value = _single(query, name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ExperimentValidationError(
            f"query parameter {name!r} must be an integer"
        ) from exc


def _run_query(query: dict[str, list[str]]) -> RunQuery:
    statuses = tuple(
        status.upper()
        for raw in query.get("status", [])
        for status in raw.split(",")
        if status
    )
    order = (_single(query, "order") or "asc").lower()
    if order not in {"asc", "desc"}:
        raise ExperimentValidationError(
            "query parameter 'order' must be 'asc' or 'desc'"
        )
    return RunQuery(
        statuses=statuses,
        scenario_id=_single(query, "scenario_id"),
        seed=_integer(query, "seed"),
        retention_class=_single(query, "retention_class"),
        trace_state=_single(query, "trace_state"),
        search=_single(query, "q"),
        sort_by=_single(query, "sort") or "run_order",
        descending=order == "desc",
        offset=_integer(query, "offset", default=0) or 0,
        limit=_integer(query, "limit", default=200),
    )


class ExperimentReadHandler(SimpleHTTPRequestHandler):
    """Serve only GET API operations and static Viewer assets."""

    catalog: ExperimentCatalog

    def __init__(
        self,
        *args,
        catalog: ExperimentCatalog,
        market_path_catalog: MarketPathSetCatalog,
        component_descriptors: tuple[dict[str, object], ...],
        directory: str,
        **kwargs,
    ) -> None:
        self.catalog = catalog
        self.market_path_catalog = market_path_catalog
        self.component_descriptors = component_descriptors
        super().__init__(*args, directory=directory, **kwargs)

    def _send_bytes(
        self,
        status: int,
        body: bytes,
        *,
        content_type: str,
        disposition: str | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if disposition is not None:
            self.send_header("Content-Disposition", disposition)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(
        self,
        status: int,
        document: object,
        *,
        disposition: str | None = None,
    ) -> None:
        self._send_bytes(
            status,
            json.dumps(
                document,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8"),
            content_type="application/json; charset=utf-8",
            disposition=disposition,
        )

    def _send_error_json(self, status: int, message: str) -> None:
        self._send_json(
            status,
            {
                "error": {
                    "status": status,
                    "message": message,
                }
            },
        )

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_response(302)
            self.send_header("Location", "/experiments.html")
            self.end_headers()
            return
        if not parsed.path.startswith("/api/"):
            super().do_GET()
            return
        try:
            self._handle_api(
                tuple(
                    unquote(part)
                    for part in parsed.path.split("/")
                    if part
                ),
                parse_qs(parsed.query, keep_blank_values=True),
            )
        except ExperimentAccessError as exc:
            self._send_error_json(403, str(exc))
        except ExperimentRepositoryError as exc:
            self._send_error_json(404, str(exc))
        except ExperimentValidationError as exc:
            self._send_error_json(400, str(exc))
        except Exception:
            self._send_error_json(
                500,
                "unexpected read API failure",
            )

    def _handle_api(
        self,
        parts: tuple[str, ...],
        query: dict[str, list[str]],
    ) -> None:
        if parts == ("api", "experiments"):
            experiments = self.catalog.experiments()
            self._send_json(
                200,
                {
                    "items": experiments,
                    "total": len(experiments),
                },
            )
            return
        if parts == ("api", "components"):
            self._send_json(
                200,
                {
                    "items": self.component_descriptors,
                    "total": len(self.component_descriptors),
                },
            )
            return
        if parts == ("api", "market-path-sets"):
            path_sets = self.market_path_catalog.path_sets()
            self._send_json(
                200,
                {
                    "items": path_sets,
                    "total": len(path_sets),
                },
            )
            return
        if (
            len(parts) == 5
            and parts[:2] == ("api", "market-path-sets")
            and parts[3] == "paths"
        ):
            self._send_json(
                200,
                self.market_path_catalog.path_document(
                    parts[2],
                    parts[4],
                    interval=_single(query, "interval") or "1w",
                ),
            )
            return
        if len(parts) < 3 or parts[:2] != ("api", "experiments"):
            raise ExperimentRepositoryError("API route not found")
        experiment_id = parts[2]
        reader = self.catalog.reader(experiment_id)
        if len(parts) == 3:
            self._send_json(
                200,
                {
                    **reader.experiment_detail(),
                    "metric_sets": reader.metric_sets(),
                },
            )
            return
        if parts[3] == "metrics" and len(parts) == 4:
            self._send_json(
                200,
                {
                    "metric_sets": reader.metric_sets(),
                    "aggregates": reader.aggregate_metric_evaluations(),
                },
            )
            return
        if parts[3] == "comparison.csv" and len(parts) == 4:
            run_query = _run_query(query)
            run_query = RunQuery(
                statuses=run_query.statuses,
                scenario_id=run_query.scenario_id,
                seed=run_query.seed,
                retention_class=run_query.retention_class,
                trace_state=run_query.trace_state,
                search=run_query.search,
                sort_by=run_query.sort_by,
                descending=run_query.descending,
                offset=0,
                limit=None,
            )
            body = comparison_csv_text(
                comparison_table(reader, query=run_query)
            ).encode("utf-8")
            self._send_bytes(
                200,
                body,
                content_type="text/csv; charset=utf-8",
                disposition=(
                    f'attachment; filename="{experiment_id}-comparison.csv"'
                ),
            )
            return
        if parts[3] != "runs":
            raise ExperimentRepositoryError("API route not found")
        if len(parts) == 4:
            result = reader.query_runs(_run_query(query))
            self._send_json(
                200,
                {
                    "items": result.rows,
                    "total": result.total,
                    "offset": result.offset,
                    "limit": result.limit,
                },
            )
            return
        run_id = parts[4]
        if len(parts) == 5:
            self._send_json(200, reader.run_detail(run_id))
            return
        if len(parts) == 6 and parts[5] == "viewer":
            download = (_single(query, "download") or "0") == "1"
            self._send_json(
                200,
                viewer_document(reader, run_id),
                disposition=(
                    f'attachment; filename="{run_id}-viewer.json"'
                    if download
                    else None
                ),
            )
            return
        raise ExperimentRepositoryError("API route not found")

    def _method_not_allowed(self) -> None:
        self.send_response(405)
        self.send_header("Allow", "GET, OPTIONS")
        self.end_headers()

    do_POST = _method_not_allowed
    do_PUT = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_DELETE = _method_not_allowed

    def do_HEAD(self) -> None:
        if urlparse(self.path).path.startswith("/api/"):
            self._method_not_allowed()
            return
        super().do_HEAD()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Allow", "GET, OPTIONS")
        self.end_headers()
        self.end_headers()


def create_read_server(
    result_root: str | Path,
    *,
    viewer_root: str | Path | None = None,
    market_environment_root: str | Path | None = None,
    component_descriptors: tuple[dict[str, object], ...] = (),
    host: str = "127.0.0.1",
    port: int = 8088,
) -> ThreadingHTTPServer:
    root = Path(viewer_root or default_viewer_root()).resolve()
    if not root.is_dir():
        raise ExperimentValidationError(
            f"Viewer root does not exist: {root}"
        )
    catalog = ExperimentCatalog(result_root)
    market_path_catalog = MarketPathSetCatalog(market_environment_root)
    handler = partial(
        ExperimentReadHandler,
        catalog=catalog,
        market_path_catalog=market_path_catalog,
        component_descriptors=component_descriptors,
        directory=str(root),
    )
    return ThreadingHTTPServer((host, port), handler)


def serve_results(
    result_root: str | Path,
    *,
    viewer_root: str | Path | None = None,
    market_environment_root: str | Path | None = None,
    component_descriptors: tuple[dict[str, object], ...] = (),
    host: str = "127.0.0.1",
    port: int = 8088,
) -> None:
    server = create_read_server(
        result_root,
        viewer_root=viewer_root,
        market_environment_root=market_environment_root,
        component_descriptors=component_descriptors,
        host=host,
        port=port,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


__all__ = [
    "ExperimentReadHandler",
    "create_read_server",
    "default_viewer_root",
    "serve_results",
]
