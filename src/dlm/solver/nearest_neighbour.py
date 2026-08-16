"""Greedy nearest-neighbour construction heuristic, asymmetry-aware.

Starts at the depot and repeatedly visits whichever unvisited stop is
cheapest to reach *from the current position* — using the matrix's
directed `cost(current, candidate)`, never a symmetrised distance, so a
one-way street that makes B cheap to reach from A but not vice versa is
respected exactly as the road network dictates.
"""

from __future__ import annotations

from dlm.instance.matrix import Matrix
from dlm.instance.schema import Instance
from dlm.solver.base import Solution, build_solution


def _construct_order(instance: Instance, matrix: Matrix) -> list[str]:
    assert instance.depot is not None
    remaining = {s.id: s.node for s in instance.stops}
    current_node = instance.depot.node
    order: list[str] = []

    while remaining:
        next_id = min(remaining, key=lambda sid: matrix.get_cost(current_node, remaining[sid]))
        order.append(next_id)
        current_node = remaining.pop(next_id)

    return order


class NearestNeighbourSolver:
    """`Solver` that builds a route by always stepping to the cheapest
    unvisited stop from wherever it currently is. `N=0` and `N=1` are
    trivial cases handled the same way as any other size — no stops to
    choose between, or one stop with nothing to compare it to."""

    def solve(self, instance: Instance, matrix: Matrix) -> Solution:
        order = _construct_order(instance, matrix)
        return build_solution(instance, matrix, order, meta={"solver": "nearest_neighbour"})
