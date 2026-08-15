"""Mutable ``InstanceBuilder``: add/remove/move/rename stops.

Stage 2 responsibility. This is the contract both the CLI (Stage 2) and the
Streamlit UI (Stage 10) call — the UI adds no logic of its own, only widget
plumbing around these methods.
"""
