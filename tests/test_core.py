import copy
import unittest
from unittest.mock import patch

from onion.core import accumulate, reconstruct, recommend


def fixture_model():
    sites = [
        {"id": name, "symbol": name, "label": name, "kind": "region",
         "anchor": name in ("work.begin", "work.end"), "estimated_cost": 1,
         "order": order}
        for order, name in enumerate(("work.begin", "branch.slow", "branch.fast",
                                      "retry.ok", "retry.fail", "work.end"))
    ]
    nodes = [
        {"id": "begin", "sites": ["work.begin"]},
        {"id": "slow", "sites": ["branch.slow"]},
        {"id": "fast", "sites": ["branch.fast"]},
        {"id": "ok", "sites": ["retry.ok"]},
        {"id": "fail", "sites": ["retry.fail"]},
        {"id": "end", "sites": ["work.end"]},
    ]
    return {"schema_version": 1, "build_id": "build-A", "function": "process_job",
            "provenance": {"kind": "test model"}, "nodes": nodes, "sites": sites,
            "edges": [{"source": a, "target": b} for a, b in
                      [("begin", "slow"), ("begin", "fast"), ("slow", "ok"),
                       ("slow", "fail"), ("fast", "ok"), ("fast", "fail"),
                       ("ok", "end"), ("fail", "end")]],
            "region": {"entry": "begin", "exit": "end"}}


def fixture_plan(*extra):
    return {"schema_version": 1, "plan_id": "plan-0", "build_id": "build-A",
            "parent_plan_id": None, "enabled_sites": ["work.begin", "work.end", *extra],
            "anchor_sites": ["work.begin", "work.end"], "rationale": {}, "budget": {}}


def fixture_trace(plan, observations=None, run_id="run-0"):
    observations = observations if observations is not None else ["work.begin", "work.end"]
    events = [{"seq": n, "thread": "worker", "thread_seq": n,
               "timestamp_ns": 100 + n * 10, "site": site, "job": 0}
              for n, site in enumerate(observations)]
    return {"schema_version": 1, "run_id": run_id, "build_id": "build-A",
            "plan_id": plan["plan_id"], "enabled_sites": plan["enabled_sites"][:],
            "clock": "CLOCK_MONOTONIC", "complete": True, "dropped": 0,
            "events": events, "results": [{"job": 0, "result": "diagnostic only"}]}


class ReconstructionTests(unittest.TestCase):
    def setUp(self):
        self.model = fixture_model()
        self.plan = fixture_plan()

    def test_unobserved_branches_remain_ambiguous(self):
        result = reconstruct(self.model, self.plan, fixture_trace(self.plan))
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["jobs"][0]["candidate_count"], 4)
        self.assertEqual(result["jobs"][0]["elapsed_ns"], 10)
        self.assertTrue(result["enumeration"]["complete"])

    def test_selected_site_absence_is_information_only_when_complete(self):
        plan = fixture_plan("branch.slow")
        trace = fixture_trace(plan)
        result = reconstruct(self.model, plan, trace)
        self.assertEqual(result["jobs"][0]["candidate_count"], 2)
        self.assertTrue(all("fast" in p for p in result["jobs"][0]["candidate_paths"]))
        trace["dropped"] = 1
        result = reconstruct(self.model, plan, trace)
        self.assertEqual(result["jobs"][0]["candidate_count"], 4)
        self.assertEqual(result["status"], "incomplete")

    def test_positive_event_survives_drop_but_not_unique_certainty(self):
        plan = fixture_plan("branch.slow", "retry.ok")
        trace = fixture_trace(plan, ["work.begin", "branch.slow", "retry.ok", "work.end"])
        trace["dropped"] = 1
        result = reconstruct(self.model, plan, trace)
        self.assertEqual(result["jobs"][0]["candidate_count"], 1)
        self.assertEqual(result["status"], "incomplete")
        next_plan = recommend(self.model, plan, result)
        self.assertEqual(next_plan["enabled_sites"], plan["enabled_sites"])
        self.assertIn("Incomplete", next_plan["rationale"]["reason"])

    def test_complete_unique_is_model_relative(self):
        plan = fixture_plan("branch.slow", "retry.ok")
        result = reconstruct(self.model, plan,
                             fixture_trace(plan, ["work.begin", "branch.slow", "retry.ok", "work.end"]))
        self.assertEqual(result["status"], "unique")
        self.assertEqual(result["jobs"][0]["candidate_paths"], [["begin", "slow", "ok", "end"]])
        self.assertTrue(all(e["evidence"] == "unique_under_model" for e in result["jobs"][0]["edge_evidence"]))

    def test_results_never_drive_branch_inference(self):
        trace = fixture_trace(self.plan)
        before = reconstruct(self.model, self.plan, trace)
        trace["results"] = [{"job": 0, "branch": "slow", "retry": "fail"}, {"job": 99}]
        self.assertEqual(before, reconstruct(self.model, self.plan, trace))

    def test_wrong_build_and_plan_are_rejected(self):
        for field in ("build_id", "plan_id"):
            trace = fixture_trace(self.plan)
            trace[field] = "wrong"
            with self.assertRaises(ValueError):
                reconstruct(self.model, self.plan, trace)

    def test_mixed_run_event_is_rejected(self):
        trace = fixture_trace(self.plan)
        trace["events"][0]["run_id"] = "other-run"
        with self.assertRaises(ValueError):
            reconstruct(self.model, self.plan, trace)

    def test_manifest_mismatch_disables_negative_evidence(self):
        plan = fixture_plan("branch.slow")
        trace = fixture_trace(plan)
        trace["enabled_sites"] = ["work.begin", "work.end"]
        result = reconstruct(self.model, plan, trace)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["jobs"][0]["candidate_count"], 4)
        self.assertFalse(result["jobs"][0]["absence_usable"])

    def test_cycle_marks_enumeration_incomplete(self):
        self.model["edges"].append({"source": "slow", "target": "slow"})
        result = reconstruct(self.model, self.plan, fixture_trace(self.plan))
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["enumeration"]["complete"])
        self.assertTrue(any("cycle" in r for r in result["enumeration"]["reasons"]))

    def test_enumeration_limits_never_claim_uniqueness(self):
        with patch("onion.core.MAX_PATHS", 1):
            result = reconstruct(self.model, self.plan, fixture_trace(self.plan))
        self.assertEqual(result["jobs"][0]["candidate_count"], 1)
        self.assertEqual(result["status"], "incomplete")

    def test_expansion_budget_bounds_search_even_before_any_exit(self):
        with patch("onion.core.MAX_EXPANSIONS", 2):
            result = reconstruct(self.model, self.plan, fixture_trace(self.plan))
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["enumeration"]["expansions"], 2)
        self.assertIn("graph expansion limit reached", result["enumeration"]["reasons"])

    def test_unknown_successor_incomplete_but_static_unreachable_is_fine(self):
        self.model["uncertainties"] = [{"kind": "unreachable_successor", "affects_completeness": False}]
        self.assertTrue(reconstruct(self.model, self.plan, fixture_trace(self.plan))["enumeration"]["complete"])
        self.model["uncertainties"].append({"kind": "unknown_successor", "affects_completeness": True})
        self.assertFalse(reconstruct(self.model, self.plan, fixture_trace(self.plan))["enumeration"]["complete"])

    def test_marker_order_within_one_block_is_retained(self):
        plan = fixture_plan("branch.slow", "retry.ok")
        self.model["nodes"] = [{"id": "block", "sites": ["work.begin", "retry.ok", "branch.slow", "work.end"]}]
        self.model["edges"] = []
        self.model["region"] = {"entry": "block", "exit": "block"}
        correct = fixture_trace(plan, ["work.begin", "retry.ok", "branch.slow", "work.end"])
        wrong = fixture_trace(plan, ["work.begin", "branch.slow", "retry.ok", "work.end"])
        self.assertEqual(reconstruct(self.model, plan, correct)["status"], "unique")
        self.assertEqual(reconstruct(self.model, plan, wrong)["status"], "inconsistent")

    def test_prefix_can_constrain_but_cannot_be_unique(self):
        plan = fixture_plan("branch.slow", "retry.ok")
        trace = fixture_trace(plan, ["work.begin", "branch.slow", "retry.ok"])
        trace["complete"] = False
        result = reconstruct(self.model, plan, trace)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["jobs"][0]["candidate_count"], 1)
        self.assertIsNone(result["jobs"][0]["elapsed_ns"])

    def test_declared_complete_missing_end_stays_incomplete(self):
        trace = fixture_trace(self.plan, ["work.begin"])
        result = reconstruct(self.model, self.plan, trace)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["jobs"][0]["candidate_count"], 4)

    def test_missing_and_duplicate_thread_sequence_never_infer_absence(self):
        plan = fixture_plan("branch.slow")
        for mutation in (None, 0, 9):
            trace = fixture_trace(plan)
            trace["events"][1]["thread_seq"] = mutation
            result = reconstruct(self.model, plan, trace)
            self.assertEqual(result["status"], "incomplete")
            self.assertEqual(result["jobs"][0]["candidate_count"], 4)

    def test_serialization_seq_does_not_override_thread_order(self):
        plan = fixture_plan("branch.slow", "retry.ok")
        trace = fixture_trace(plan, ["work.begin", "branch.slow", "retry.ok", "work.end"])
        trace["events"] = list(reversed(trace["events"]))
        for seq, event in enumerate(trace["events"]):
            event["seq"] = seq
        self.assertEqual(reconstruct(self.model, plan, trace)["status"], "unique")

    def test_missing_job_evidence_is_not_manufactured_from_results(self):
        trace = fixture_trace(self.plan, [])
        result = reconstruct(self.model, self.plan, trace)
        self.assertEqual(result["jobs"], [])
        self.assertEqual(result["status"], "incomplete")

    def test_queue_transfer_uses_job_and_phase_not_serial_seq(self):
        trace = fixture_trace(self.plan)
        trace["events"] += [
            {"seq": 99, "thread": "main", "thread_seq": 0, "timestamp_ns": 80,
             "site": "queue.enqueue", "job": 0, "queue": "jobs"},
            {"seq": 0, "thread": "consumer", "thread_seq": 0, "timestamp_ns": 90,
             "site": "queue.dequeue", "job": 0, "queue": "jobs"},
        ]
        transfer = reconstruct(self.model, self.plan, trace)["transfers"][0]
        self.assertEqual(transfer["status"], "paired")
        self.assertEqual(transfer["elapsed_ns"], 10)
        self.assertEqual(transfer["run_id"], "run-0")
        self.assertIn("before_publish", transfer["enqueue_events"][0]["phase"])

    def test_repeated_queue_identifier_needs_generation_not_guessed_pair(self):
        trace = fixture_trace(self.plan)
        for n in range(2):
            trace["events"].append({"seq": 2 + n, "thread": "main", "thread_seq": n,
                                    "timestamp_ns": n, "site": "queue.enqueue", "job": 0,
                                    "queue": "jobs"})
        result = reconstruct(self.model, self.plan, trace)
        self.assertEqual(result["transfers"][0]["status"], "ambiguous")


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.model, self.plan = fixture_model(), fixture_plan()

    def test_deterministic_tie_and_incremental_discrimination(self):
        result = reconstruct(self.model, self.plan, fixture_trace(self.plan))
        next_plan = recommend(self.model, self.plan, result, max_new_sites=2)
        self.assertEqual(next_plan["rationale"]["selected_sites"], ["branch.slow", "retry.ok"])
        self.assertEqual(next_plan["enabled_sites"], ["work.begin", "work.end", "branch.slow", "retry.ok"])
        self.assertEqual(next_plan["parent_plan_id"], self.plan["plan_id"])
        self.assertNotEqual(next_plan["plan_id"], self.plan["plan_id"])
        self.assertEqual(next_plan, recommend(self.model, self.plan, result, max_new_sites=2))
        scores = next_plan["rationale"]["candidate_scores"]
        self.assertEqual(scores[0]["pair_gain"], 4)
        self.assertAlmostEqual(scores[0]["weighted_gain"], 4 / 6)
        self.assertEqual(next_plan["rationale"]["rounds"][1]["candidate_scores"][0]["pair_gain"], 2)
        self.assertIn("not measured", next_plan["budget"]["cost_unit"])

    def test_cost_changes_selection(self):
        self.model["sites"][1]["estimated_cost"] = 10
        result = reconstruct(self.model, self.plan, fixture_trace(self.plan))
        self.assertEqual(recommend(self.model, self.plan, result)["rationale"]["selected_sites"], ["branch.fast"])

    def test_no_recommendation_for_incomplete_prefix(self):
        trace = fixture_trace(self.plan, ["work.begin"])
        trace["complete"] = False
        result = reconstruct(self.model, self.plan, trace)
        self.assertEqual(recommend(self.model, self.plan, result)["rationale"]["selected_sites"], [])

    def test_all_prior_sites_retained(self):
        plan = fixture_plan("branch.slow")
        result = reconstruct(self.model, plan, fixture_trace(plan))
        next_plan = recommend(self.model, plan, result)
        self.assertTrue(set(plan["enabled_sites"]) <= set(next_plan["enabled_sites"]))
        self.assertEqual(next_plan["anchor_sites"], plan["anchor_sites"])
        self.assertEqual(next_plan["rationale"]["selected_sites"], ["retry.ok"])


class AccumulationTests(unittest.TestCase):
    def test_runs_remain_separate_and_edges_preserve_evidence(self):
        model, plan = fixture_model(), fixture_plan("branch.slow", "retry.ok")
        slow = reconstruct(model, plan, fixture_trace(plan, ["work.begin", "branch.slow", "retry.ok", "work.end"], "slow-run"))
        fast = reconstruct(model, plan, fixture_trace(plan, ["work.begin", "work.end"], "fast-run"))
        merged = accumulate([slow, fast])
        self.assertFalse(merged["merges_timelines"])
        self.assertEqual(len(merged["runs"]), 2)
        slow_edge = next(e for e in merged["edges"] if e["source"] == "begin" and e["target"] == "slow")
        self.assertEqual({s["run_id"] for s in slow_edge["support"]}, {"slow-run"})
        self.assertNotIn("events", merged)
        with self.assertRaises(ValueError):
            accumulate([slow, slow])

    def test_cross_build_requires_mapping(self):
        model, plan = fixture_model(), fixture_plan()
        result = reconstruct(model, plan, fixture_trace(plan))
        other = copy.deepcopy(result)
        other["run_id"], other["build_id"] = "different", "build-B"
        with self.assertRaises(ValueError):
            accumulate([result, other])

    def test_ambiguous_edges_are_not_called_observed(self):
        model, plan = fixture_model(), fixture_plan()
        result = reconstruct(model, plan, fixture_trace(plan))
        merged = accumulate([result])
        self.assertTrue(all(s["evidence"] == "candidate_only" for e in merged["edges"] for s in e["support"]))


if __name__ == "__main__":
    unittest.main()
