"""Download and cache the routable Dublin road graph.

Stage 1 responsibility: ``build_graph`` / ``load_graph`` around OSMnx,
cached to ``data/cache/<hash>.graphml`` keyed by (place, network_type,
simplify flags, OSMnx version), reduced to the largest strongly connected
component. See ``docs/stages/stage-01-network.md`` for design and
acceptance criteria.
"""
