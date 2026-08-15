"""Instance data models: ``Stop``, ``Depot``, ``Instance``.

Stage 2 responsibility. ``Instance.n_stops`` must be a property derived from
``len(stops)``, never a stored/cached constant, per the project's dynamic-N
requirement (§1.1 of the brief).
"""
