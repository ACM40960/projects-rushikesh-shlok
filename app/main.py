"""Streamlit front end — thin client only.

Every action here calls into `app.state` (which itself only calls
`dlm.*`, the same functions `dlm.cli` calls — see that module's
docstring). No routing, solving, disruption, or metric logic lives in
this file (§2.1 of the project brief, `docs/architecture.md`'s
architectural law): this file is widget layout and `st.session_state`
plumbing only. See `docs/stages/stage-10-ui.md`.

Run with `make app` (`streamlit run app/main.py`).
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st
from streamlit_folium import st_folium

sys.path.insert(0, str(Path(__file__).resolve().parent))
import state  # noqa: E402 - after sys.path fixup

st.set_page_config(page_title="Disruption-Aware Last-Mile Routing", layout="wide")
state.init_session_state()

st.title("Disruption-Aware Last-Mile Routing on Dublin's Road Network")
st.caption(
    "A thin client over the `dlm` CLI pipeline — every number here is computed by the "
    "same `dlm.*` functions `dlm plan`/`dlm compare` call. See `docs/cli.md` for the "
    "command-line equivalent of everything on this page."
)

with st.spinner("Loading Dublin road network (cached after first run)..."):
    graph, graph_report = state.get_graph()
st.sidebar.success(
    f"Graph: {graph_report.n_nodes} nodes, {graph_report.n_edges} edges "
    f"({'cache hit' if graph_report.from_cache else 'built fresh'})"
)

# --- Sidebar: choose or create an instance -------------------------------

st.sidebar.header("1. Instance")
existing = state.list_instance_names()
choice = st.sidebar.selectbox(
    "Load an existing instance", ["(none)", *existing], index=0, key="instance_choice"
)
if choice != "(none)":
    st.session_state["instance_name"] = choice

with st.sidebar.expander("Create a new instance"):
    new_name = st.text_input("Name", key="new_instance_name")
    depot_kind = st.selectbox("Depot from", ["preset", "address", "latlon"], key="new_depot_kind")
    if depot_kind == "preset":
        depot_value = st.selectbox("Preset", state.list_preset_names(), key="new_depot_preset")
    else:
        depot_value = st.text_input(
            "Address" if depot_kind == "address" else "lat,lon", key="new_depot_value"
        )
    fleet_size = st.number_input(
        "Fleet size (K)", min_value=1, value=1, step=1, key="new_fleet_size"
    )
    vehicle_capacity = None
    if fleet_size > 1:
        vehicle_capacity = st.number_input(
            "Vehicle capacity (per vehicle)",
            min_value=0.1,
            value=10.0,
            key="new_vehicle_capacity",
        )
    if st.button("Create instance", key="create_instance_btn"):
        if not new_name or not depot_value:
            st.error("Name and depot are both required.")
        else:
            try:
                result = state.create_instance(
                    new_name,
                    depot_kind,
                    depot_value,
                    fleet_size=int(fleet_size),
                    vehicle_capacity=vehicle_capacity,
                )
                st.success(result.message)
                st.session_state["instance_name"] = new_name
                st.rerun()
            except (FileExistsError, ValueError) as exc:
                st.error(str(exc))
            except Exception as exc:  # noqa: BLE001 - report any pipeline error, not a crash
                st.error(f"Could not create instance: {exc}")

instance_name = st.session_state.get("instance_name")

if not instance_name:
    st.info("Choose or create an instance in the sidebar to get started.")
    st.stop()

instance = state.load_instance(instance_name)
st.header(f"Instance: {instance_name}")

# --- Add stops -------------------------------------------------------------

col_add, col_table = st.columns([1, 2])

with col_add:
    st.subheader("Add a stop")
    add_kind = st.selectbox("From", ["preset", "address", "latlon", "random"], key="add_kind")
    if add_kind == "preset":
        add_value = st.selectbox("Preset", state.list_preset_names(), key="add_preset")
    elif add_kind == "random":
        add_value = st.number_input(
            "How many random stops?", min_value=1, value=5, key="add_random_n"
        )
    else:
        add_value = st.text_input(
            "Address" if add_kind == "address" else "lat,lon", key="add_value"
        )
    demand = 0.0
    if instance.fleet_size > 1:
        demand = st.number_input("Demand", min_value=0.0, value=1.0, key="add_demand")

    if st.button("Add stop", key="add_stop_btn"):
        try:
            if add_kind == "random":
                results = state.add_random_stops(instance_name, int(add_value))
                for r in results:
                    st.success(r.message)
            else:
                result = state.add_stop(instance_name, add_kind, str(add_value), demand=demand)
                st.success(result.message)
            st.rerun()
        except Exception as exc:  # noqa: BLE001 - report any pipeline error, not a crash
            st.error(f"Could not add stop: {exc}")

    st.subheader("Remove a stop")
    stop_ids = [s.id for s in instance.stops]
    if stop_ids:
        remove_id = st.selectbox("Stop", stop_ids, key="remove_stop_id")
        if st.button("Remove stop", key="remove_stop_btn"):
            result = state.remove_stop(instance_name, remove_id)
            st.success(result.message)
            st.rerun()
    else:
        st.caption("No stops yet.")

with col_table:
    st.subheader("Depot + stops")
    rows = state.instance_rows(instance)
    if rows:
        st.dataframe(rows, width="stretch", hide_index=True)
    else:
        st.caption("No depot set yet — create the instance with a depot in the sidebar.")

# --- Instance map, with click-to-add-a-stop ---------------------------------

st.subheader("Map")
if instance.depot is not None:
    from dlm.viz.folium_map import render_instance_map

    m = render_instance_map(instance)
    click = st_folium(m, height=450, use_container_width=True, returned_objects=["last_clicked"])
    last_clicked = click.get("last_clicked") if click else None
    if last_clicked and last_clicked != st.session_state.get("_last_map_click"):
        st.session_state["_last_map_click"] = last_clicked
        st.session_state["_pending_click_latlon"] = (last_clicked["lat"], last_clicked["lng"])

    pending = st.session_state.get("_pending_click_latlon")
    if pending:
        lat, lon = pending
        st.info(f"Clicked ({lat:.5f}, {lon:.5f}) — add it as a stop?")
        c1, c2 = st.columns(2)
        if c1.button("Add as stop here", key="confirm_map_click_add"):
            try:
                result = state.add_stop(instance_name, "map_click", f"{lat},{lon}")
                st.success(result.message)
            except Exception as exc:  # noqa: BLE001 - report any pipeline error, not a crash
                st.error(f"Could not add stop: {exc}")
            st.session_state["_pending_click_latlon"] = None
            st.rerun()
        if c2.button("Discard", key="discard_map_click"):
            st.session_state["_pending_click_latlon"] = None
            st.rerun()
else:
    st.caption("Set a depot to see the map.")

# --- Plan: T1 ---------------------------------------------------------------

st.header("Plan (T1)")
solver_name = st.session_state["solver_name"]
if instance.fleet_size == 1:
    solver_name = st.selectbox("Solver", list(state.SOLVERS), key="solver_name")

if st.button("Run plan", key="run_plan_btn", disabled=instance.depot is None or not instance.stops):
    try:
        plan_outcome = state.run_plan(instance_name, solver_name=solver_name)
        st.session_state["plan_outcome"] = plan_outcome
    except Exception as exc:  # noqa: BLE001 - report any pipeline error, not a crash
        st.error(f"Could not plan: {exc}")

plan_outcome = st.session_state.get("plan_outcome")
if plan_outcome is not None and plan_outcome.instance.name == instance_name:
    t1 = plan_outcome.t1
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Solver", plan_outcome.solver_name)
    c2.metric("T1 (total)", f"{t1.total_time_s:.1f}s")
    c3.metric("Drive time", f"{t1.drive_time_s:.1f}s")
    c4.metric("Service time", f"{t1.service_time_s:.1f}s")

    if plan_outcome.solution is not None:
        from dlm.viz.folium_map import render_route_map

        st.caption(" -> ".join(plan_outcome.solution.order))
        st_folium(
            render_route_map(plan_outcome.instance, plan_outcome.solution, plan_outcome.graph),
            height=450,
            use_container_width=True,
            key="route_map",
        )
    else:
        from dlm.viz.folium_map import render_fleet_route_map

        st.caption(f"{len(plan_outcome.fleet.routes)} / {instance.fleet_size} vehicles used")
        for i, s in enumerate(plan_outcome.fleet.routes, start=1):
            st.caption(f"vehicle {i}: {' -> '.join(s.order)} ({s.total_time_s:.1f}s)")
        if plan_outcome.fleet.unassigned:
            st.warning(f"Unassigned: {', '.join(plan_outcome.fleet.unassigned)}")
        st_folium(
            render_fleet_route_map(plan_outcome.instance, plan_outcome.fleet, plan_outcome.graph),
            height=450,
            use_container_width=True,
            key="fleet_route_map",
        )

# --- Disruption: T2 / T3 / Saving % -----------------------------------------

st.header("Disruption comparison (T2 / T3 / Saving %)")
if instance.fleet_size > 1:
    st.info(
        "Disruption comparison only supports single-vehicle instances "
        "(no fleet-aware T2/T3 yet — see docs/limitations.md)."
    )
else:
    scenario_name = st.selectbox("Scenario", state.list_scenario_names(), key="scenario_name")
    at_time = st.number_input(
        "Scenario time (seconds from start)",
        min_value=0.0,
        value=0.0,
        step=300.0,
        help="Only disruptions active at this time are applied.",
    )
    scenario_mode = st.radio(
        "Disruption mode",
        ["Saved scenario", "Adjust selected scenario"],
        horizontal=True,
    )
    scenario_override = None
    if scenario_mode == "Adjust selected scenario":
        if state.scenario_has_slow_zone(scenario_name):
            speed_factor = st.slider(
                "Remaining traffic speed",
                min_value=0.1,
                max_value=0.9,
                value=0.5,
                step=0.1,
                help="0.5 means vehicles travel at half the original speed.",
            )
            scenario_override = state.scenario_with_speed_factor(scenario_name, speed_factor)
        else:
            st.caption("This scenario has no slow zone, so its intensity is not adjustable.")
    if st.button(
        "Compare", key="run_compare_btn", disabled=instance.depot is None or not instance.stops
    ):
        try:
            compare_outcome = state.run_compare(
                instance_name,
                scenario_name,
                solver_name=solver_name,
                at_time=float(at_time),
                scenario_override=scenario_override,
            )
            st.session_state["compare_outcome"] = compare_outcome
        except Exception as exc:  # noqa: BLE001 - report any pipeline error, not a crash
            st.error(f"Could not compare: {exc}")

    compare_outcome = st.session_state.get("compare_outcome")
    if compare_outcome is not None and compare_outcome.instance.name == instance_name:
        co = compare_outcome
        c1, c2, c3 = st.columns(3)
        c1.metric("T1 (normal)", f"{co.t1.total_time_s:.1f}s")
        c2.metric(
            "T2 (reactive)",
            f"{co.t2_reactive.total_time_s:.1f}s" if co.t2_reactive.feasible else "INFEASIBLE",
        )
        c3.metric(
            "T3 (re-optimised)",
            f"{co.t3.total_time_s:.1f}s" if co.t3.feasible else "INFEASIBLE",
        )
        c4, c5, c6 = st.columns(3)
        c4.metric(
            "T2 (omniscient)",
            f"{co.t2_omniscient.total_time_s:.1f}s" if co.t2_omniscient.feasible else "INFEASIBLE",
        )
        c5.metric(
            "T3 full-knowledge heuristic",
            f"{co.t3_oracle.total_time_s:.1f}s" if co.t3_oracle.feasible else "INFEASIBLE",
        )
        c6.metric("Saving %", f"{co.saving_pct:.1f}%" if co.saving_pct is not None else "n/a")

        st.caption(
            f"edges closed/slowed: {co.disruption_result.n_edges_closed}/"
            f"{co.disruption_result.n_edges_slowed}"
        )

        from dlm.viz.folium_map import render_disruption_map, render_route_map

        map_col1, map_col2 = st.columns(2)
        with map_col1:
            st.caption("T1 plan")
            st_folium(
                render_route_map(co.instance, co.solution, co.graph),
                height=400,
                use_container_width=True,
                key="compare_route_map",
            )
        with map_col2:
            st.caption("Disruption")
            st_folium(
                render_disruption_map(co.disruption_result),
                height=400,
                use_container_width=True,
                key="compare_disruption_map",
            )
