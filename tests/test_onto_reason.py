import json
import unittest

from onto_reason import OntologyEngine, OntologyError, Triple


def make_doc(**overrides):
    doc = {
        "classes": ["Person"],
        "properties": ["knows", "likes", "friendOf"],
        "individuals": ["alice", "bob", "carol"],
        "triples": [
            {"subject": "alice", "predicate": "knows", "object": "bob"},
            {"subject": "bob", "predicate": "knows", "object": "carol"},
        ],
        "rules": [],
    }
    doc.update(overrides)
    return doc


def parse(doc):
    return OntologyEngine().parse(json.dumps(doc))


class ParseAndValidateTests(unittest.TestCase):
    def test_json_syntax_error(self):
        with self.assertRaises(OntologyError) as ctx:
            OntologyEngine().parse("{not json")
        self.assertIn("JSON", str(ctx.exception))

    def test_non_utf8_bytes(self):
        with self.assertRaises(OntologyError):
            OntologyEngine().parse(b"\xff\xfe{}")

    def test_utf8_bytes_accepted(self):
        model = OntologyEngine().parse(json.dumps(make_doc()).encode("utf-8"))
        self.assertEqual(len(model.explicit_triples), 2)

    def test_root_must_be_object(self):
        with self.assertRaises(OntologyError):
            OntologyEngine().parse("[1, 2]")

    def test_missing_root_field(self):
        doc = make_doc()
        del doc["rules"]
        with self.assertRaises(OntologyError) as ctx:
            parse(doc)
        self.assertIn("rules", str(ctx.exception))

    def test_root_field_wrong_type(self):
        with self.assertRaises(OntologyError) as ctx:
            parse(make_doc(classes="Person"))
        self.assertIn("classes", str(ctx.exception))

    def test_duplicate_name_within_array(self):
        with self.assertRaises(OntologyError) as ctx:
            parse(make_doc(individuals=["alice", "bob", "alice"]))
        self.assertIn("alice", str(ctx.exception))

    def test_duplicate_name_across_arrays(self):
        with self.assertRaises(OntologyError):
            parse(make_doc(classes=["Person"], individuals=["Person"]))

    def test_explicit_triple_bad_shape(self):
        bad = {"subject": "alice", "predicate": "knows"}
        with self.assertRaises(OntologyError) as ctx:
            parse(make_doc(triples=[bad]))
        self.assertIn("triples[0]", str(ctx.exception))

    def test_explicit_triple_undeclared_property(self):
        bad = {"subject": "alice", "predicate": "hates", "object": "bob"}
        with self.assertRaises(OntologyError) as ctx:
            parse(make_doc(triples=[bad]))
        self.assertIn("hates", str(ctx.exception))
        self.assertIn("triples[0]", str(ctx.exception))

    def test_explicit_triple_rejects_variable(self):
        bad = {"subject": "?x", "predicate": "knows", "object": "bob"}
        with self.assertRaises(OntologyError):
            parse(make_doc(triples=[bad]))

    def test_duplicate_rule_id(self):
        rule = {"id": "r1", "if": [], "then": []}
        with self.assertRaises(OntologyError) as ctx:
            parse(make_doc(rules=[rule, rule]))
        self.assertIn("r1", str(ctx.exception))

    def test_rule_undeclared_property(self):
        rule = {
            "id": "r1",
            "if": [{"subject": "?x", "predicate": "hates", "object": "?y"}],
            "then": [],
        }
        with self.assertRaises(OntologyError) as ctx:
            parse(make_doc(rules=[rule]))
        self.assertIn("r1", str(ctx.exception))
        self.assertIn("hates", str(ctx.exception))

    def test_rule_variable_predicate_rejected(self):
        rule = {
            "id": "r1",
            "if": [{"subject": "?x", "predicate": "?p", "object": "?y"}],
            "then": [],
        }
        with self.assertRaises(OntologyError):
            parse(make_doc(rules=[rule]))

    def test_unbound_then_variable(self):
        rule = {
            "id": "r-unbound",
            "if": [{"subject": "?x", "predicate": "knows", "object": "?y"}],
            "then": [{"subject": "?x", "predicate": "likes", "object": "?z"}],
        }
        with self.assertRaises(OntologyError) as ctx:
            parse(make_doc(rules=[rule]))
        self.assertIn("r-unbound", str(ctx.exception))
        self.assertIn("?z", str(ctx.exception))

    def test_rule_bad_shape(self):
        with self.assertRaises(OntologyError) as ctx:
            parse(make_doc(rules=[{"id": "r1", "if": []}]))
        self.assertIn("then", str(ctx.exception))


class InferenceTests(unittest.TestCase):
    def test_forward_chain_to_fixpoint(self):
        rule = {
            "id": "knows-likes",
            "if": [{"subject": "?x", "predicate": "knows", "object": "?y"}],
            "then": [{"subject": "?x", "predicate": "likes", "object": "?y"}],
        }
        model = parse(make_doc(rules=[rule]))
        derived = model.derived_triples
        self.assertEqual(
            [d.triple for d in derived],
            [Triple("alice", "likes", "bob"), Triple("bob", "likes", "carol")],
        )
        self.assertTrue(all(d.rule_id == "knows-likes" for d in derived))

    def test_multi_hop_chain(self):
        # knows 传递闭包：alice -> bob -> carol 推出 alice -> carol
        rule = {
            "id": "knows-transitive",
            "if": [
                {"subject": "?x", "predicate": "knows", "object": "?y"},
                {"subject": "?y", "predicate": "knows", "object": "?z"},
            ],
            "then": [{"subject": "?x", "predicate": "knows", "object": "?z"}],
        }
        model = parse(make_doc(rules=[rule]))
        self.assertTrue(model.entails("alice", "knows", "carol"))
        self.assertEqual(
            model.source_rule("alice", "knows", "carol"), "knows-transitive"
        )

    def test_join_across_predicates(self):
        rule = {
            "id": "friend-of-friend",
            "if": [
                {"subject": "?x", "predicate": "knows", "object": "?y"},
                {"subject": "?y", "predicate": "likes", "object": "?z"},
            ],
            "then": [{"subject": "?x", "predicate": "friendOf", "object": "?z"}],
        }
        triples = make_doc()["triples"] + [
            {"subject": "bob", "predicate": "likes", "object": "carol"}
        ]
        model = parse(make_doc(triples=triples, rules=[rule]))
        self.assertTrue(model.entails("alice", "friendOf", "carol"))
        self.assertFalse(model.entails("carol", "friendOf", "alice"))

    def test_derived_does_not_duplicate_explicit(self):
        rule = {
            "id": "r1",
            "if": [{"subject": "?x", "predicate": "knows", "object": "?y"}],
            "then": [{"subject": "?x", "predicate": "knows", "object": "?y"}],
        }
        model = parse(make_doc(rules=[rule]))
        self.assertEqual(model.derived_triples, ())
        self.assertEqual(len(model.triples), 2)

    def test_same_conclusion_keeps_first_rule(self):
        rules = [
            {
                "id": "r-first",
                "if": [{"subject": "?x", "predicate": "knows", "object": "?y"}],
                "then": [{"subject": "?x", "predicate": "likes", "object": "?y"}],
            },
            {
                "id": "r-second",
                "if": [{"subject": "?x", "predicate": "knows", "object": "?y"}],
                "then": [{"subject": "?x", "predicate": "likes", "object": "?y"}],
            },
        ]
        model = parse(make_doc(rules=rules))
        self.assertEqual(len(model.derived_triples), 2)
        self.assertTrue(all(d.rule_id == "r-first" for d in model.derived_triples))

    def test_ground_then_with_empty_if(self):
        rule = {
            "id": "r-fact",
            "if": [],
            "then": [{"subject": "carol", "predicate": "likes", "object": "alice"}],
        }
        model = parse(make_doc(rules=[rule]))
        self.assertTrue(model.entails("carol", "likes", "alice"))

    def test_output_sorted_and_marked(self):
        model = parse(make_doc())
        self.assertEqual(
            list(model.explicit_triples),
            [Triple("alice", "knows", "bob"), Triple("bob", "knows", "carol")],
        )
        self.assertEqual(model.source_rule("alice", "knows", "bob"), None)

    def test_entails_false_for_unknown(self):
        model = parse(make_doc())
        self.assertFalse(model.entails("alice", "likes", "bob"))


class DeterminismAndImmutabilityTests(unittest.TestCase):
    def test_repeated_parse_same_result(self):
        doc = make_doc(
            rules=[
                {
                    "id": "r1",
                    "if": [{"subject": "?x", "predicate": "knows", "object": "?y"}],
                    "then": [{"subject": "?x", "predicate": "likes", "object": "?y"}],
                }
            ]
        )
        text = json.dumps(doc)
        results = []
        for _ in range(3):
            model = OntologyEngine().parse(text)
            results.append(
                (model.explicit_triples, model.derived_triples, model.triples)
            )
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])

    def test_reads_do_not_mutate_model(self):
        rule = {
            "id": "r1",
            "if": [{"subject": "?x", "predicate": "knows", "object": "?y"}],
            "then": [{"subject": "?x", "predicate": "likes", "object": "?y"}],
        }
        model = parse(make_doc(rules=[rule]))
        before = (model.explicit_triples, model.derived_triples, model.triples)
        # 重复读取并尝试修改返回值
        model.explicit_triples
        derived = model.derived_triples
        model.entails("alice", "likes", "bob")
        model.source_rule("alice", "likes", "bob")
        self.assertIsNot(model.derived_triples, derived)
        after = (model.explicit_triples, model.derived_triples, model.triples)
        self.assertEqual(before, after)

    def test_self_variable_pattern(self):
        # (?x, knows, ?x) 只匹配主语与宾语相同的事实
        triples = [{"subject": "alice", "predicate": "knows", "object": "alice"}]
        rule = {
            "id": "self-like",
            "if": [{"subject": "?x", "predicate": "knows", "object": "?x"}],
            "then": [{"subject": "?x", "predicate": "likes", "object": "?x"}],
        }
        model = parse(make_doc(triples=triples, rules=[rule]))
        self.assertTrue(model.entails("alice", "likes", "alice"))
        self.assertFalse(model.entails("alice", "likes", "bob"))


if __name__ == "__main__":
    unittest.main()
