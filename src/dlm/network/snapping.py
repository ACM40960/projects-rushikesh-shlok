"""Snap a lat/lon point to the nearest routable graph node.

Stage 1 responsibility: this is the safety boundary between free-form user
input (clicks, geocoded addresses, lat/lon) and the graph. Must raise a
clear, typed, human-readable error when a point is farther than a
configured maximum snap distance from any routable node (e.g. a point in
the Irish Sea), rather than silently snapping hundreds of metres away.
"""
