"""Download and cache the routable Dublin road graph.

The graph is downloaded once from the public Overpass API, reduced to the
largest strongly connected component so every node can reach every other,
and then cached to disk as a single pickle file keyed by a hash of the
inputs that determine its content (area, network type, simplify flag,
OSMnx version). A second call with the same inputs loads from that cache
instead of re-downloading.

The cache is pickle, not OSMnx's usual ``.graphml``: this graph (Greater
Dublin, tens of thousands of nodes) took >10s to parse from XML on second
load, well past the <5s target, while pickle — a fine choice for a purely
internal, gitignored cache with no interchange requirement — loads the
same graph in well under a second. See docs/stages/stage-01-network.md
for the measured numbers.

The actual HTTP fetch is done with ``curl`` via subprocess rather than
OSMnx's own ``requests``-based transport — see the note on
``_fetch_overpass_xml`` below for why. Everything else (query filter
construction, XML parsing, graph building, simplification) uses OSMnx's own
public API.
"""

from __future__ import annotations

import hashlib
import logging
import pickle
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import networkx as nx
import osmnx as ox
from osmnx._overpass import _get_network_filter  # pure string builder, no I/O

from dlm.config import settings
from dlm.network.travel_time import TravelTimeStats, add_travel_times

logger = logging.getLogger(__name__)

DEFAULT_BBOX = (-6.4200, 53.2800, -6.1000, 53.4700)
"""(left, bottom, right, top) i.e. (west, south, east, north), WGS84 degrees.

Covers Greater Dublin: the city centre, UCD Belfield, Dublin Port, Dublin
Airport, and the Tallaght / Blanchardstown / Dun Laoghaire / Swords
suburbs — resolved in ADR-0003 (Stage 2) once the curated preset list
(``data/presets/dublin_locations.yaml``) made the practical need for these
concrete: several "recognisable Dublin locations" (the airport, outer
suburbs, out-of-town shopping centres) fell outside Stage 1's original,
smaller bbox. Still short of the full M50 catchment. See docs/data.md.
"""

NETWORK_TYPE = "drive"

_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_OVERPASS_QUERY_TIMEOUT_S = 120
_CURL_MAX_TIME_S = 150
_DOWNLOAD_RETRIES = 8
_DOWNLOAD_BACKOFF_S = 3.0
_USER_AGENT = "dlm-dublin-routing/0.1 (UCD ACM40960 coursework)"


@dataclass(frozen=True)
class GraphBuildReport:
    """Summary of a graph build, for `dlm network stats` and the stage docs.

    Attributes
    ----------
    n_nodes, n_edges : int
        Node/edge counts of the final graph (after largest-SCC extraction).
    n_nodes_before_scc, n_edges_before_scc : int
        Counts before extracting the largest strongly connected component,
        so the amount discarded is visible.
    travel_time_stats : TravelTimeStats
        Coverage of real vs. imputed speeds.
    cache_path : Path
        Where the graph is cached on disk.
    from_cache : bool
        True if this build was loaded from disk rather than downloaded.
    build_seconds : float
        Wall-clock time for this call.
    """

    n_nodes: int
    n_edges: int
    n_nodes_before_scc: int
    n_edges_before_scc: int
    travel_time_stats: TravelTimeStats
    cache_path: Path
    from_cache: bool
    build_seconds: float


def _cache_key(bbox: tuple[float, float, float, float], network_type: str, simplify: bool) -> str:
    """Hash the inputs that determine a downloaded graph's content."""
    payload = f"{bbox}|{network_type}|{simplify}|osmnx={ox.__version__}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _cache_path(bbox: tuple[float, float, float, float], network_type: str, simplify: bool) -> Path:
    key = _cache_key(bbox, network_type, simplify)
    return settings.cache_dir / f"dublin_{network_type}_{key}.pkl"


def _build_overpass_query(bbox: tuple[float, float, float, float], network_type: str) -> str:
    """Build the Overpass QL query for a bbox + network type, XML output.

    Reuses OSMnx's own ``_get_network_filter`` (a pure string builder, no
    I/O) so the "drive" filter here is exactly the one ``graph_from_bbox``
    would use, rather than a hand-copied string that could drift from it.
    """
    west, south, east, north = bbox
    way_filter = _get_network_filter(network_type)
    return (
        f"[out:xml][timeout:{_OVERPASS_QUERY_TIMEOUT_S}];"
        f"(way{way_filter}({south},{west},{north},{east});>;);out;"
    )


def _fetch_overpass_xml(query: str, dest_path: Path) -> None:
    """Fetch an Overpass query's result to `dest_path` as raw OSM XML, via curl.

    OSMnx's own ``requests``-based HTTP transport was found to be unreliable
    in this environment: individual requests to the public overpass-api.de
    instance would either reset the connection or, worse, hang indefinitely
    without ever raising an exception or timing out — even with an explicit
    `requests` timeout set — which made retrying impossible. `curl` against
    the exact same endpoint reliably either succeeds in a few seconds or
    fails fast within its own `--max-time` bound, which *can* be retried.
    See docs/data.md and docs/stages/stage-01-network.md for the full story.
    """
    result = subprocess.run(  # noqa: S603 - fixed args, no shell, trusted query we built
        [
            "curl",
            "-sS",
            "--max-time",
            str(_CURL_MAX_TIME_S),
            "-o",
            str(dest_path),
            "-w",
            "%{http_code}",
            "-A",
            _USER_AGENT,
            "-X",
            "POST",
            "--data-urlencode",
            f"data={query}",
            _OVERPASS_URL,
        ],
        capture_output=True,
        text=True,
        timeout=_CURL_MAX_TIME_S + 10,
        check=False,
    )
    http_code = result.stdout.strip()
    if http_code != "200":
        raise ConnectionError(
            f"Overpass request failed: http_code={http_code!r} stderr={result.stderr.strip()!r}"
        )


def _download_with_retries(
    bbox: tuple[float, float, float, float], network_type: str, simplify: bool
) -> nx.MultiDiGraph:
    """Download the raw graph via Overpass, retrying on the public
    instance's occasional connection resets under load."""
    query = _build_overpass_query(bbox, network_type)
    last_error: Exception | None = None
    for attempt in range(1, _DOWNLOAD_RETRIES + 1):
        with tempfile.NamedTemporaryFile(suffix=".osm", delete=False) as f:
            xml_path = Path(f.name)
        try:
            _fetch_overpass_xml(query, xml_path)
            return ox.graph_from_xml(xml_path, bidirectional=False, simplify=simplify)
        except Exception as exc:  # noqa: BLE001 - retry on any transient network failure
            last_error = exc
            logger.warning(
                "graph download attempt %d/%d failed (%s: %s); retrying in %.0fs",
                attempt,
                _DOWNLOAD_RETRIES,
                type(exc).__name__,
                exc,
                _DOWNLOAD_BACKOFF_S,
            )
            if attempt < _DOWNLOAD_RETRIES:
                time.sleep(_DOWNLOAD_BACKOFF_S)
        finally:
            xml_path.unlink(missing_ok=True)
    assert last_error is not None
    raise last_error


def build_graph(
    bbox: tuple[float, float, float, float] = DEFAULT_BBOX,
    network_type: str = NETWORK_TYPE,
    simplify: bool = True,
    force_rebuild: bool = False,
) -> tuple[nx.MultiDiGraph, GraphBuildReport]:
    """Load the Dublin routable graph from cache, or download and cache it.

    Parameters
    ----------
    bbox : tuple[float, float, float, float]
        (left, bottom, right, top) in WGS84 degrees. Defaults to
        :data:`DEFAULT_BBOX`.
    network_type : str
        OSMnx network type; ``"drive"`` gives a directed, one-way-respecting
        driveable network.
    simplify : bool
        Simplify graph topology (merge interstitial nodes on straight road
        segments) — standard OSMnx behaviour, keeps the graph a reasonable
        size without changing route geometry.
    force_rebuild : bool
        If True, ignore any cached graph and re-download.

    Returns
    -------
    (nx.MultiDiGraph, GraphBuildReport)
        The graph (directed, largest strongly-connected component, with
        `travel_time`/`speed_kph`/`speed_source` on every edge) and a build
        report.
    """
    settings.ensure_dirs()

    cache_path = _cache_path(bbox, network_type, simplify)
    t0 = time.time()

    if cache_path.exists() and not force_rebuild:
        with cache_path.open("rb") as f:
            G = pickle.load(f)  # noqa: S301 - our own cache, never untrusted input
        stats = TravelTimeStats(
            n_edges=G.number_of_edges(),
            n_real_maxspeed=sum(
                1 for *_e, d in G.edges(keys=True, data=True) if d["speed_source"] == "osm_maxspeed"
            ),
            n_imputed=sum(
                1 for *_e, d in G.edges(keys=True, data=True) if d["speed_source"] == "imputed"
            ),
        )
        report = GraphBuildReport(
            n_nodes=G.number_of_nodes(),
            n_edges=G.number_of_edges(),
            n_nodes_before_scc=G.number_of_nodes(),
            n_edges_before_scc=G.number_of_edges(),
            travel_time_stats=stats,
            cache_path=cache_path,
            from_cache=True,
            build_seconds=time.time() - t0,
        )
        logger.info("loaded graph from cache in %.2fs: %s", report.build_seconds, cache_path)
        return G, report

    G_raw = _download_with_retries(bbox, network_type, simplify)
    n_nodes_before, n_edges_before = G_raw.number_of_nodes(), G_raw.number_of_edges()

    G = ox.truncate.largest_component(G_raw, strongly=True)
    G, stats = add_travel_times(G)

    with cache_path.open("wb") as f:
        pickle.dump(G, f, protocol=pickle.HIGHEST_PROTOCOL)

    report = GraphBuildReport(
        n_nodes=G.number_of_nodes(),
        n_edges=G.number_of_edges(),
        n_nodes_before_scc=n_nodes_before,
        n_edges_before_scc=n_edges_before,
        travel_time_stats=stats,
        cache_path=cache_path,
        from_cache=False,
        build_seconds=time.time() - t0,
    )
    logger.info(
        "downloaded and built graph in %.1fs: %d nodes, %d edges "
        "(%.1f%% real maxspeed); cached to %s",
        report.build_seconds,
        report.n_nodes,
        report.n_edges,
        stats.pct_real,
        cache_path,
    )
    return G, report
