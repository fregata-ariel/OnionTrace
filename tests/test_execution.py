"""Identity collisions, invalid references and evidence separation at the M1 boundary."""

import copy
from hashlib import sha256
import json
from pathlib import Path
import unittest

from onion.execution import (accumulate_execution, make_entity, make_relation,
                             make_structure, validate_run, validate_structure)


FIXTURE = Path(__file__).resolve().parents[1] / "fixture" / "execution"


def fixture_structure():
    catalog = json.loads((FIXTURE / "catalog.json").read_text())
    entities, functions, locations, builds = [], [], [], []

    def entity(kind, **identity):
        row = make_entity(kind, identity)
        if row not in entities:
            entities.append(row)
        return row["id"]

    program = entity("program", origin=catalog["origin"], revision=catalog["revision"])
    for variant in catalog["variants"]:
        target = entity("target", program_id=program, name=variant["target"])
        commands = []
        for source in variant["sources"]:
            contents = (FIXTURE / source).read_bytes()
            digest = "sha256:" + sha256(contents).hexdigest()
            commands.append({"source": source, "directory": ".", "compiler": "clang-18:test-toolchain",
                             "input_digest": digest,
                             "arguments": ["clang-18", "-std=c11", *variant["defines"], "-c", source]})
        manifest = json.dumps(commands, sort_keys=True, separators=(",", ":")).encode()
        configuration = dict(variant["configuration"], manifest_digest="sha256:" + sha256(manifest).hexdigest())
        build = entity("build", target_id=target, configuration=configuration)
        builds.append(build)
        for command in commands:
            source, digest = command["source"], command["input_digest"]
            compilation = entity("compilation", build_id=build, **command)
            functions.append(entity("function", compilation_id=compilation,
                                    symbol="c:@F@handler", linkage="internal"))
            locations.append(entity("location", compilation_id=compilation, path=source,
                                    content_digest=digest, start_offset=0, end_offset=len((FIXTURE / source).read_bytes())))
    role = entity("thread_role", build_id=builds[0], key="worker@a.c:0")
    queue = entity("queue", build_id=builds[0], key="jobs@a.c:0")
    compilation = next(row for row in entities if row["id"] == functions[0])["identity"]["compilation_id"]
    callback = entity("callback", compilation_id=compilation, key="job.callback@a.c:0")
    evidence = {"location_ids": [locations[0]], "conditions": ["worker mode enabled"],
                "method": {"name": "hand-authored-fixture", "version": "1"}, "reason": None}
    relations = [make_relation(kind, source, target, "resolved", evidence)
                 for kind, source, target in [
                     ("calls", functions[0], functions[1]), ("spawns", functions[0], role),
                     ("starts", role, functions[1]), ("submits", functions[0], queue),
                     ("dequeues", queue, functions[1]), ("invokes", role, callback),
                     ("binds", callback, functions[1])]]
    for resolution, target in (("candidate", functions[1]), ("unresolved", None)):
        relations.append(make_relation("calls", functions[0], target, resolution,
                                       dict(evidence, reason="function pointer target is not proven")))
    return make_structure(entities, relations)


def fixture_run(structure, run_id="run-0", plan_id="plan-0"):
    relation = next(row for row in structure["relations"] if row["kind"] == "calls" and row["resolution"] == "resolved")
    source = next(row for row in structure["entities"] if row["id"] == relation["source"])
    compilation = next(row for row in structure["entities"] if row["id"] == source["identity"]["compilation_id"])
    build = compilation["identity"]["build_id"]
    event_ref = {"thread": "worker-instance-0", "thread_seq": 0}
    run = {"schema_version": 1, "kind": "execution_run", "structure_id": structure["structure_id"],
           "build_id": build, "run_id": run_id, "plan_id": plan_id, "trace_ref": f"{run_id}/trace.json",
           "observations": [], "inferences": [{"relation_id": relation["id"], "basis": "unique_under_model",
                                                 "event_refs": [event_ref], "reason": "test inference under selected model"}]}
    trace = {"schema_version": 1, "run_id": run_id, "plan_id": plan_id, "build_id": build,
             "complete": True, "dropped": 0,
             "events": [{**event_ref, "seq": 100, "site": "work.begin"}]}
    return run, trace


class IdentityTests(unittest.TestCase):
    def test_fixture_same_name_targets_and_compile_conditions_do_not_collide(self):
        structure = fixture_structure()
        functions = [row for row in structure["entities"] if row["kind"] == "function"]
        self.assertEqual(len(functions), 4)
        self.assertEqual(len({row["identity"]["symbol"] for row in functions}), 1)
        self.assertEqual(len({row["id"] for row in functions}), 4)
        self.assertEqual({row["resolution"] for row in validate_structure(structure)}, {"candidate", "unresolved"})
        self.assertEqual(structure, json.loads(json.dumps(structure)))
        validate_structure(json.loads(json.dumps(structure)))

    def test_labels_and_dictionary_order_are_not_entity_identity(self):
        first = make_entity("program", {"origin": "test", "revision": "v1"}, label="first")
        second = make_entity("program", {"revision": "v1", "origin": "test"}, label="second")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["id"], "program:df850fbab174e649ccaed738aa9dbec357e37bc365032d217730655348466c8d")

    def test_compiler_arguments_inputs_and_working_directory_affect_identity(self):
        compilation = next(row for row in fixture_structure()["entities"] if row["kind"] == "compilation")
        for field, value in (("compiler", "clang-18:other"), ("directory", "build"),
                             ("arguments", ["clang-18", "-DOTHER", "-c", "a.c"]),
                             ("input_digest", "sha256:" + "0" * 64)):
            with self.subTest(field=field):
                changed = dict(compilation["identity"], **{field: value})
                self.assertNotEqual(make_entity("compilation", changed)["id"], compilation["id"])
        forward = dict(compilation["identity"], arguments=["clang", "-DA=1", "-UA"])
        reverse = dict(forward, arguments=["clang", "-UA", "-DA=1"])
        self.assertNotEqual(make_entity("compilation", forward)["id"], make_entity("compilation", reverse)["id"])

    def test_revision_and_source_offsets_affect_identity(self):
        self.assertNotEqual(make_entity("program", {"origin": "test", "revision": "v1"})["id"],
                            make_entity("program", {"origin": "test", "revision": "v2"})["id"])
        location = next(row for row in fixture_structure()["entities"] if row["kind"] == "location")
        changed = dict(location["identity"], start_offset=1)
        self.assertNotEqual(location["id"], make_entity("location", changed)["id"])

    def test_invalid_json_and_paths_are_rejected(self):
        build = next(row for row in fixture_structure()["entities"] if row["kind"] == "build")
        for value in (float("nan"), float("inf"), {1: "non-string key"}, ("tuple",)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                make_entity("build", dict(build["identity"], configuration={"bad": value}))
        compilation = next(row for row in fixture_structure()["entities"] if row["kind"] == "compilation")
        for path in ("/absolute/a.c", "../a.c", "a/../a.c", "./a.c", "a//b.c", "a\\b.c", "a\x00.c", "."):
            with self.subTest(path=path), self.assertRaises(ValueError):
                make_entity("compilation", dict(compilation["identity"], source=path))


class StructureTests(unittest.TestCase):
    def setUp(self):
        self.structure = fixture_structure()
        self.entities = self.structure["entities"]
        self.relation = next(row for row in self.structure["relations"] if row["kind"] == "calls" and row["resolution"] == "resolved")

    def relation_with(self, **changes):
        values = {key: value for key, value in self.relation.items() if key != "id"}
        return make_relation(**dict(values, **changes))

    def test_unknown_target_is_diagnostic_but_dangling_target_is_invalid(self):
        diagnostics = validate_structure(self.structure)
        self.assertEqual(len(diagnostics), 2)
        self.assertTrue(all(row["reason"] for row in diagnostics))
        with self.assertRaisesRegex(ValueError, "unknown reference"):
            make_structure(self.entities, [self.relation_with(target="function:missing")])

    def test_relation_types_are_enforced(self):
        queue = next(row["id"] for row in self.entities if row["kind"] == "queue")
        with self.assertRaisesRegex(ValueError, "wrong entity type"):
            make_structure(self.entities, [self.relation_with(target=queue)])
        with self.assertRaisesRegex(ValueError, "unsupported"):
            self.relation_with(kind="mystery")

    def test_static_observed_flags_and_runtime_fields_are_rejected(self):
        for key, value in (("observed", True), ("run_id", "run-0"), ("basis", "direct_observation")):
            changed = copy.deepcopy(self.structure)
            changed["relations"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_structure(changed)

    def test_cross_build_connections_and_evidence_are_rejected(self):
        source = next(row for row in self.entities if row["id"] == self.relation["source"])
        other = next(row["id"] for row in self.entities if row["kind"] == "function" and
                     row["identity"]["compilation_id"] != source["identity"]["compilation_id"] and
                     row["id"] != self.relation["target"])
        with self.assertRaisesRegex(ValueError, "crosses builds"):
            make_structure(self.entities, [self.relation_with(target=other)])
        other_compilation = next(row["identity"]["compilation_id"] for row in self.entities if row["id"] == other)
        location = next(row["id"] for row in self.entities if row["kind"] == "location" and row["identity"]["compilation_id"] == other_compilation)
        evidence = dict(self.relation["evidence"], location_ids=[location])
        with self.assertRaisesRegex(ValueError, "evidence crosses builds"):
            make_structure(self.entities, [self.relation_with(evidence=evidence)])

    def test_resolution_requires_reason_and_explicit_null_for_unknown_target(self):
        for changes in ({"resolution": "unresolved"}, {"resolution": "candidate"}, {"target": None}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.relation_with(**changes)

    def test_missing_source_evidence_or_method_is_rejected(self):
        for field, value in (("location_ids", []), ("location_ids", ["location:missing"]),
                             ("method", {"name": "test"}), ("conditions", "unknown")):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                relation = self.relation_with(evidence=dict(self.relation["evidence"], **{field: value}))
                make_structure(self.entities, [relation])

    def test_dangling_entity_parent_and_duplicate_identity_are_rejected(self):
        bad = make_entity("target", {"program_id": "program:missing", "name": "target"})
        with self.assertRaisesRegex(ValueError, "unknown reference"):
            make_structure([*self.entities, bad], [])
        with self.assertRaisesRegex(ValueError, "duplicate ID"):
            make_structure([*self.entities, self.entities[0]], [])

    def test_content_ids_detect_stale_or_tampered_artifacts(self):
        for field in ("structure", "entity", "relation"):
            changed = copy.deepcopy(self.structure)
            if field == "structure":
                changed["structure_id"] = "structure:stale"
            elif field == "entity":
                changed["entities"][0]["identity"]["key"] = "extra"
            else:
                changed["relations"][0]["evidence"]["conditions"].append("changed")
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_structure(changed)

    def test_structure_order_is_not_identity_and_schema_bool_is_not_version(self):
        rebuilt = make_structure(list(reversed(self.entities)), list(reversed(self.structure["relations"])))
        self.assertEqual(rebuilt, self.structure)
        self.structure["schema_version"] = True
        with self.assertRaises(ValueError):
            validate_structure(self.structure)


class RunEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.structure = fixture_structure()
        self.run, self.trace = fixture_run(self.structure)

    def test_model_uniqueness_stays_inference_and_static_alone_adds_no_evidence(self):
        before = copy.deepcopy((self.structure, self.run, self.trace))
        result = accumulate_execution(self.structure, [(self.run, self.trace)])
        self.assertEqual(result["relations"][0]["support"][0]["basis"], "unique_under_model")
        self.assertEqual(accumulate_execution(self.structure, [])["relations"], [])
        self.assertEqual(before, (self.structure, self.run, self.trace))

    def test_direct_observation_requires_explicit_raw_edge_event(self):
        relation = self.run["inferences"][0]["relation_id"]
        self.run["inferences"] = []
        self.run["observations"] = [{"relation_id": relation, "event_ref": {"thread": "worker-instance-0", "thread_seq": 0}}]
        with self.assertRaisesRegex(ValueError, "matching raw relation event"):
            validate_run(self.structure, self.run, self.trace)
        self.trace["events"][0]["relation_id"] = relation
        validate_run(self.structure, self.run, self.trace)
        result = accumulate_execution(self.structure, [(self.run, self.trace)])
        self.assertEqual(result["relations"][0]["support"][0]["basis"], "direct_observation")

    def test_observed_candidate_does_not_resolve_static_uncertainty(self):
        relation = next(row for row in self.structure["relations"] if row["resolution"] == "candidate")
        before = copy.deepcopy(self.structure)
        self.run["inferences"] = []
        self.run["observations"] = [{"relation_id": relation["id"], "event_ref": {"thread": "worker-instance-0", "thread_seq": 0}}]
        self.trace["events"][0]["relation_id"] = relation["id"]
        self.trace["complete"], self.trace["dropped"] = False, 1
        accumulate_execution(self.structure, [(self.run, self.trace)])
        self.assertEqual(self.structure, before)
        self.assertEqual(relation["resolution"], "candidate")

    def test_unknown_endpoint_or_inference_cannot_become_direct_observation(self):
        relation = next(row for row in self.structure["relations"] if row["resolution"] == "unresolved")
        run = copy.deepcopy(self.run)
        run["inferences"][0]["basis"] = "direct_observation"
        with self.assertRaises(ValueError):
            validate_run(self.structure, run, self.trace)
        self.run["inferences"] = []
        self.run["observations"] = [{"relation_id": relation["id"], "event_ref": {"thread": "worker-instance-0", "thread_seq": 0}}]
        self.trace["events"][0]["relation_id"] = relation["id"]
        with self.assertRaisesRegex(ValueError, "unknown target"):
            validate_run(self.structure, self.run, self.trace)

    def test_multiple_runs_retain_plan_and_event_provenance_without_timeline(self):
        other, trace = fixture_run(self.structure, "run-1", "plan-1")
        other["inferences"][0]["basis"] = "candidate_only"
        result = accumulate_execution(self.structure, [(self.run, self.trace), (other, trace)])
        self.assertFalse(result["merges_timelines"])
        self.assertNotIn("events", result)
        support = result["relations"][0]["support"]
        self.assertEqual([(row["run_id"], row["plan_id"], row["basis"]) for row in support],
                         [("run-0", "plan-0", "unique_under_model"), ("run-1", "plan-1", "candidate_only")])
        self.assertEqual(other["inferences"][0]["basis"], "candidate_only")

    def test_duplicate_runs_and_cross_build_accumulation_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate run_id"):
            accumulate_execution(self.structure, [(self.run, self.trace), (self.run, self.trace)])
        other, trace = fixture_run(self.structure, "run-1")
        build = next(row["id"] for row in self.structure["entities"] if row["kind"] == "build" and row["id"] != other["build_id"])
        other["build_id"] = trace["build_id"] = build
        other["inferences"] = []
        validate_run(self.structure, other, trace)
        with self.assertRaisesRegex(ValueError, "cross-build"):
            accumulate_execution(self.structure, [(self.run, self.trace), (other, trace)])

    def test_wrong_structure_build_plan_run_and_mixed_event_are_rejected(self):
        for field in ("structure_id", "build_id", "plan_id", "run_id"):
            changed = copy.deepcopy(self.run)
            changed[field] = "wrong"
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_run(self.structure, changed, self.trace)
        for field in ("build_id", "plan_id", "run_id"):
            trace = copy.deepcopy(self.trace)
            trace["events"][0][field] = "other"
            with self.subTest(event_field=field), self.assertRaisesRegex(ValueError, "raw event"):
                validate_run(self.structure, self.run, trace)

    def test_unresolved_and_candidate_connections_cannot_earn_model_uniqueness(self):
        for resolution in ("candidate", "unresolved"):
            run = copy.deepcopy(self.run)
            relation = next(row for row in self.structure["relations"] if row["resolution"] == resolution)
            run["inferences"][0]["relation_id"] = relation["id"]
            with self.subTest(resolution=resolution), self.assertRaisesRegex(ValueError, "model uniqueness"):
                validate_run(self.structure, run, self.trace)
            run["inferences"][0]["basis"] = "candidate_only"
            validate_run(self.structure, run, self.trace)

    def test_missing_events_duplicates_unknown_relations_and_wrong_build_refs_are_rejected(self):
        for mutation in ("missing_event", "duplicate_event", "unknown_relation", "wrong_build"):
            run, trace = copy.deepcopy(self.run), copy.deepcopy(self.trace)
            if mutation == "missing_event":
                run["inferences"][0]["event_refs"][0]["thread_seq"] = 1
            elif mutation == "duplicate_event":
                trace["events"].append(trace["events"][0])
            elif mutation == "unknown_relation":
                run["inferences"][0]["relation_id"] = "relation:missing"
            else:
                build = next(row["id"] for row in self.structure["entities"] if row["kind"] == "build" and row["id"] != run["build_id"])
                run["build_id"] = trace["build_id"] = build
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_run(self.structure, run, trace)

    def test_incomplete_trace_cannot_earn_uniqueness_but_candidates_survive(self):
        for changes in ({"complete": False}, {"dropped": 1}, {"dropped": False}):
            trace = dict(self.trace, **changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_run(self.structure, self.run, trace)
            run = copy.deepcopy(self.run)
            run["inferences"][0]["basis"] = "candidate_only"
            validate_run(self.structure, run, trace)


if __name__ == "__main__":
    unittest.main()
