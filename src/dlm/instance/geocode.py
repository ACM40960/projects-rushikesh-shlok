"""Cached address geocoding, with loud failures and ambiguous-result handling.

Stage 2 responsibility. The same address must never hit the network twice;
ambiguous queries return candidates instead of silently picking one.
"""
