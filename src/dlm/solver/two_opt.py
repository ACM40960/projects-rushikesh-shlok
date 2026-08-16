"""2-opt local search improvement, with correct directed-cost re-evaluation
for the asymmetric Dublin travel-time matrix.

**Why full re-evaluation, not the O(1) symmetric-TSP delta trick.** The
textbook 2-opt move removes two edges `(a,b)` and `(c,d)` from a route and
reconnects them as `(a,c)` and `(b,d)`, reversing the segment between `b`
and `c`. For a *symmetric* TSP this is an O(1) cost update: only the two
removed/added edges matter, because reversing a segment doesn't change any
internal edge's cost (`d(x,y) == d(y,x)`). On Dublin's directed street
network that assumption is false — Stage 3 measured over 95% of point
pairs asymmetric — so reversing a segment changes the cost of *every*
internal edge in that segment (each is now traversed in the opposite,
possibly much more expensive, direction). This module recomputes the
*entire* route's cost for each candidate move (`route_time_s`, O(N) per
call) rather than the O(1) delta, which is the brief's "2-opt with correct
directed cost re-evaluation" option. At `N <= 50` this is trivial: an
O(N^3) full improvement pass is at most ~125,000 cost lookups, well under
a second — see docs/stages/stage-04-baseline.md for measured timings.
"""

from __future__ import annotations

import logging

from dlm.instance.matrix import Matrix
from dlm.instance.schema import Instance
from dlm.solver.base import Solution, build_solution, route_time_s

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITERATIONS = 2000


def two_opt_improve(
    instance: Instance,
    matrix: Matrix,
    order: list[str],
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> tuple[list[str], list[float]]:
    """Improve a visit order by repeated 2-opt segment reversal.

    First-improvement strategy: apply the first reversal found that
    reduces cost, then restart the scan (rather than searching for the
    single best move per pass) — simpler to reason about and to defend
    line-by-line, at the cost of possibly a few more iterations to
    converge. Stops when no single reversal improves the route, or
    `max_iterations` reversal attempts have been evaluated.

    Parameters
    ----------
    order : list[str]
        Stop ids, not including the depot (see `dlm.solver.base.Solution.order`).

    Returns
    -------
    (list[str], list[float])
        The improved order, and the cost trajectory — one entry per
        *accepted* improving move, starting with the initial route's cost.
        Non-increasing by construction: this is what
        "log shows monotone improvement" (brief, Stage 4 acceptance
        criteria) means and what is asserted on in tests.
    """
    current = list(order)
    best_cost = route_time_s(instance, matrix, current)
    trajectory = [best_cost]

    iterations = 0
    improved = True
    while improved and iterations < max_iterations:
        improved = False
        n = len(current)
        # i, j index into `current` (stops only); reversing current[i:j+1]
        # is equivalent to reversing the corresponding segment of the full
        # depot-inclusive circuit.
        for i in range(n - 1):
            for j in range(i + 1, n):
                iterations += 1
                candidate = current[:i] + current[i : j + 1][::-1] + current[j + 1 :]
                candidate_cost = route_time_s(instance, matrix, candidate)
                if candidate_cost < best_cost:
                    current = candidate
                    best_cost = candidate_cost
                    trajectory.append(best_cost)
                    improved = True
                    break
                if iterations >= max_iterations:
                    break
            if improved or iterations >= max_iterations:
                break

    logger.info(
        "2-opt: %d iterations, %d accepted improvements, %.2fs -> %.2fs",
        iterations,
        len(trajectory) - 1,
        trajectory[0],
        trajectory[-1],
    )
    return current, trajectory


class TwoOptSolver:
    """`Solver` that builds a route with nearest-neighbour, then improves
    it with 2-opt. This is the project's primary solver (fixed technical
    decision, ADR-0001)."""

    def __init__(self, max_iterations: int = DEFAULT_MAX_ITERATIONS) -> None:
        self.max_iterations = max_iterations

    def solve(self, instance: Instance, matrix: Matrix) -> Solution:
        from dlm.solver.nearest_neighbour import _construct_order

        initial_order = _construct_order(instance, matrix)
        improved_order, trajectory = two_opt_improve(
            instance, matrix, initial_order, self.max_iterations
        )
        return build_solution(
            instance,
            matrix,
            improved_order,
            meta={
                "solver": "nearest_neighbour_two_opt",
                "two_opt_iterations": len(trajectory) - 1,
                "two_opt_trajectory": trajectory,
                "initial_cost_s": trajectory[0],
                "final_cost_s": trajectory[-1],
            },
        )
