"""Edge speed imputation and travel-time computation.

Stage 1 responsibility: fill missing OSM ``maxspeed`` with a per-``highway``-type
default speed table (config-driven, not hard-coded), then compute
``travel_time = length_m / speed_m_s`` in seconds on every edge.
"""
