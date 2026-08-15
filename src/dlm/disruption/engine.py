"""Apply a ``Scenario`` to a graph without mutating the base graph.

Stage 5 responsibility. Returns a disrupted graph view plus an audit record
of exactly which edges changed and how, with a ``revert()`` path.
"""
