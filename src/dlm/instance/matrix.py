"""Cached, incrementally-updatable travel-time + path matrix.

Stage 3 responsibility. Adding one stop must cost one new Dijkstra row/column,
not a full ``(N+1)^2`` rebuild. See ``docs/stages/stage-03-matrix.md``.
"""
