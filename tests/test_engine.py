# -*- coding: utf-8 -*-
"""OntologyEngine 校验、推理与只读模型的测试（仅使用标准库 unittest）。"""

import json
import unittest

from onto_reason import OntologyEngine, OntologyError, Triple


def build(**overrides):
    """构造一份最小合法本体，可用关键字覆盖各字段。"""
    ontology = {
        "classes": ["Person"],
        "properties": ["knows", "ancestor"],
        "individuals": ["alice", "bob", "carol"],
        "triples": [],
        "rules": [],
    }
    ontology.update(overrides)
    return json.dumps(ontology, ensure_ascii=False)


class ParseAndExplicitTests(unittest.TestCase):
    def test_minimal_ontology(self):
        model = OntologyEngine().parse(build())
        self.assertEqual(model.classes, ["Person"])
        self.assertEqual(model.properties, ["ancestor", "knows"])
        self.assertEqual(model.individuals, ["alice", "bob", "carol"])
        self.assertEqual(model.triples(), [])

    def test_explicit_triples_sorted_stable(self):
        text = build(triples=[
            ["carol", "knows", "alice"],
            ["alice", "knows", "bob"],
            ["alice", "knows", "alice"],
        ])
        model = OntologyEngine().parse(text)
        self.assertEqual(
            [t.as_tuple() for t in model.explicit_triples()],
            [
                ("alice", "knows", "alice"),
                ("alice", "knows", "bob"),
                ("carol", "knows", "alice"),
            ],
        )
        self.assertTrue(model.is_explicit(("alice", "knows", "bob")))
        self.assertFalse(model.is_inferred(("alice", "knows", "bob")))

    def test_duplicate_explicit_triples_collapsed(self):
        text = build(triples=[["alice", "knows", "bob"], ["alice", "knows", "bob"]])
        model = OntologyEngine().parse(text)
        self.assertEqual(len(model.explicit_triples()), 1)

    def test_utf8_bytes_and_chinese_names(self):
        data = {
            "classes": ["人物"],
            "properties": ["认识"],
            "individuals": ["张三", "李四"],
            "triples": [["张三", "认识", "李四"]],
            "rules": [],
        }
        text = json.dumps(data, ensure_ascii=False).encode("utf-8")
        model = OntologyEngine().parse(text)
        self.assertEqual(model.individuals, ["张三", "李四"])
        self.assertTrue(model.entails(("张三", "认识", "李四")))

    def test_engine_callable_alias(self):
        model = OntologyEngine()(build())
        self.assertEqual(model.triples(), [])


class ForwardChainTests(unittest.TestCase):
    def test_symmetric_rule_single_application(self):
        rules = [{
            "id": "sym",
            "if": [["?x", "knows", "?y"]],
            "then": [["?y", "knows", "?x"]],
        }]
        model = OntologyEngine().parse(build(
            triples=[["alice", "knows", "bob"]], rules=rules))
        inferred = [t.as_tuple() for t in model.inferred_triples()]
        self.assertEqual(inferred, [("bob", "knows", "alice")])
        self.assertEqual(model.source_rule(("bob", "knows", "alice")), "sym")
        self.assertEqual(model.origin(("bob", "knows", "alice")), ("derived", "sym"))
        # 显式事实保持 explicit，即使规则也会"推出"它。
        self.assertEqual(model.origin(("alice", "knows", "bob")), ("explicit", None))
        self.assertIsNone(model.source_rule(("alice", "knows", "bob")))
        # 自反应用不会制造重复结论。
        self.assertEqual(len(model.triples()), 2)

    def test_transitive_closure_to_fixpoint(self):
        rules = [{
            "id": "trans",
            "if": [["?x", "ancestor", "?y"], ["?y", "ancestor", "?z"]],
            "then": [["?x", "ancestor", "?z"]],
        }]
        individuals = ["a", "b", "c", "d"]
        model = OntologyEngine().parse(build(
            individuals=individuals,
            triples=[["a", "ancestor", "b"], ["b", "ancestor", "c"],
                     ["c", "ancestor", "d"]],
            rules=rules))
        expected = {
            ("a", "ancestor", "b"), ("b", "ancestor", "c"),
            ("c", "ancestor", "d"),
            ("a", "ancestor", "c"), ("b", "ancestor", "d"),
            ("a", "ancestor", "d"),
        }
        self.assertEqual({t.as_tuple() for t in model.triples()}, expected)
        self.assertEqual(
            model.source_rule(("a", "ancestor", "d")), "trans")
        # 输出为稳定字典序。
        all_triples = [t.as_tuple() for t in model.triples()]
        self.assertEqual(all_triples, sorted(all_triples))

    def test_join_with_same_variable_different_positions(self):
        # ?x 与 ?y 必须不同个体：?x p ?y 且 ?y p ?x（2-循环）。
        rules = [{
            "id": "cycle2",
            "if": [["?x", "knows", "?y"], ["?y", "knows", "?x"]],
            "then": [["?x", "knows", "?x"]],
        }]
        model = OntologyEngine().parse(build(
            triples=[["alice", "knows", "bob"], ["bob", "knows", "alice"]],
            rules=rules))
        # 两种绑定 (alice,bob)/(bob,alice) 都推出 alice-alice 与 bob-bob，
        # 但结论不得重复。
        self.assertEqual(
            sorted(t.as_tuple() for t in model.inferred_triples()),
            [("alice", "knows", "alice"), ("bob", "knows", "bob")])

    def test_constant_in_then_and_multiple_then_patterns(self):
        rules = [{
            "id": "cc",
            "if": [["?x", "knows", "bob"]],
            "then": [["?x", "knows", "carol"], ["carol", "knows", "?x"]],
        }]
        model = OntologyEngine().parse(build(
            triples=[["alice", "knows", "bob"]], rules=rules))
        self.assertEqual(
            sorted(t.as_tuple() for t in model.inferred_triples()),
            [("alice", "knows", "carol"), ("carol", "knows", "alice")])

    def test_empty_if_produces_facts_once(self):
        rules = [{"id": "base", "if": [], "then": [["alice", "knows", "bob"]]}]
        model = OntologyEngine().parse(build(rules=rules))
        inferred = model.inferred_triples()
        self.assertEqual(len(inferred), 1)
        self.assertEqual(model.source_rule(inferred[0]), "base")

    def test_derived_fact_can_trigger_next_round(self):
        rules = [
            {"id": "r1",
             "if": [["?x", "knows", "?y"]],
             "then": [["?y", "knows", "?x"]]},
            {"id": "r2",
             "if": [["?x", "knows", "?y"], ["?y", "knows", "?z"]],
             "then": [["?x", "knows", "?z"]]},
        ]
        model = OntologyEngine().parse(build(
            triples=[["alice", "knows", "bob"], ["bob", "knows", "carol"]],
            rules=rules))
        # r1 产生 bob->alice, carol->bob；r2 再闭合出 alice->carol 等。
        self.assertTrue(model.entails(("alice", "knows", "carol")))
        self.assertTrue(model.entails(("carol", "knows", "alice")))

    def test_source_attribution_is_earliest_rule_and_deterministic(self):
        # 两条规则都能推出同一结论；来源应稳定归属于输入顺序较早的规则。
        rules = [
            {"id": "first", "if": [["?x", "knows", "?y"]],
             "then": [["?x", "knows", "carol"]]},
            {"id": "second", "if": [["?x", "knows", "bob"]],
             "then": [["?x", "knows", "carol"]]},
        ]
        t1 = OntologyEngine().parse(build(
            triples=[["alice", "knows", "bob"]], rules=rules))
        # 反转规则顺序后，归属应随规则输入顺序确定地改变。
        t2 = OntologyEngine().parse(build(
            triples=[["alice", "knows", "bob"]], rules=list(reversed(rules))))
        self.assertEqual(
            t1.source_rule(("alice", "knows", "carol")), "first")
        self.assertEqual(
            t2.source_rule(("alice", "knows", "carol")), "second")
        # 两份模型的事实集合相同，只有来源归属随规则顺序变化。
        self.assertEqual(
            {t.as_tuple() for t in t1.triples()},
            {t.as_tuple() for t in t2.triples()})


class EntailmentAndReadonlyTests(unittest.TestCase):
    def setUp(self):
        rules = [{"id": "sym", "if": [["?x", "knows", "?y"]],
                  "then": [["?y", "knows", "?x"]]}]
        self.text = build(triples=[["alice", "knows", "bob"]], rules=rules)

    def test_entails(self):
        model = OntologyEngine().parse(self.text)
        self.assertTrue(model.entails(Triple("alice", "knows", "bob")))
        self.assertTrue(model.entails(("bob", "knows", "alice")))
        self.assertFalse(model.entails(("alice", "knows", "carol")))

    def test_origin_of_unknown_triple_raises(self):
        model = OntologyEngine().parse(self.text)
        with self.assertRaises(OntologyError):
            model.origin(("alice", "knows", "carol"))
        with self.assertRaises(OntologyError):
            model.source_rule(("nope", "knows", "nope"))

    def test_invalid_entails_argument(self):
        model = OntologyEngine().parse(self.text)
        with self.assertRaises(OntologyError):
            model.entails(("alice", "knows"))
        with self.assertRaises(OntologyError):
            model.entails(["alice", "knows", 1])

    def test_repeated_parsing_and_reads_are_identical(self):
        engine = OntologyEngine()
        m1 = engine.parse(self.text)
        m2 = engine.parse(self.text)
        snapshot = [t.as_tuple() for t in m1.triples()]
        # 大量重复读取与蕴含判断不改变数据。
        for _ in range(20):
            self.assertEqual([t.as_tuple() for t in m1.triples()], snapshot)
            m1.entails(("bob", "knows", "alice"))
            m1.origin(("alice", "knows", "bob"))
        self.assertEqual(
            [t.as_tuple() for t in m2.triples()], snapshot)

    def test_returned_lists_are_independent_copies(self):
        model = OntologyEngine().parse(self.text)
        triples = model.triples()
        triples.clear()
        individuals = model.individuals
        individuals.append("hacker")
        self.assertEqual(len(model.triples()), 2)
        self.assertEqual(model.individuals, ["alice", "bob", "carol"])

    def test_triple_is_immutable_and_sortable(self):
        t = Triple("a", "p", "b")
        with self.assertRaises(AttributeError):
            t.subject = "x"
        self.assertEqual(tuple(t), ("a", "p", "b"))
        self.assertLess(Triple("a", "p", "a"), Triple("a", "p", "b"))
        self.assertEqual({Triple("a", "p", "b"), Triple("a", "p", "b")}.__len__(), 1)


class ValidationErrorTests(unittest.TestCase):
    def assertError(self, text, *needles):
        with self.assertRaises(OntologyError) as ctx:
            OntologyEngine().parse(text)
        message = str(ctx.exception)
        for needle in needles:
            self.assertIn(needle, message)

    def test_json_syntax_error(self):
        self.assertError("{not json", "JSON")

    def test_root_must_be_object(self):
        self.assertError("[]", "根对象")
        self.assertError("\"hi\"", "根对象")

    def test_missing_root_field(self):
        self.assertError(json.dumps({"classes": [], "properties": [],
                                     "individuals": [], "triples": []}),
                         "rules")

    def test_root_field_wrong_type(self):
        self.assertError(json.dumps({"classes": {}, "properties": [],
                                     "individuals": [], "triples": [],
                                     "rules": []}),
                         "classes", "数组")

    def test_input_type_and_utf8(self):
        with self.assertRaises(OntologyError):
            OntologyEngine().parse(123)
        with self.assertRaises(OntologyError):
            OntologyEngine().parse(b"\xff\xfe\x00bad")

    def test_declaration_name_errors(self):
        self.assertError(build(classes=["Person", "Person"]), "名称重复", "Person")
        self.assertError(build(properties=["Person"]), "名称重复")
        self.assertError(build(individuals=["?x"]), "?x")
        self.assertError(build(classes=[1]), "classes[0]")
        self.assertError(build(classes=[""]), "空字符串")

    def test_explicit_triple_shape_errors(self):
        self.assertError(build(triples=[["alice", "knows"]]), "triples[0]", "3")
        self.assertError(build(triples=["alice knows bob"]), "triples[0]")
        self.assertError(build(triples=[["alice", "knows", 1]]), "triples[0]", "宾语")
        self.assertError(build(triples=[["alice", "nope", "bob"]]),
                         "triples[0]", "property")
        self.assertError(build(triples=[["x", "knows", "bob"]]),
                         "triples[0]", "individual")
        self.assertError(build(triples=[["alice", "knows", "x"]]),
                         "triples[0]", "individual")
        self.assertError(build(triples=[["?x", "knows", "bob"]]), "triples[0]", "变量")

    def test_rule_structure_errors(self):
        self.assertError(build(rules=[{"id": "r", "if": [], "then": []}]),
                         "r", "then")
        self.assertError(build(rules=[{"if": [], "then": []}]), "id")
        self.assertError(build(rules=[{"id": "r", "then": []}]), "if")
        self.assertError(build(rules=[{"id": "r", "if": [], "then": "x"}]),
                         "r", "then")
        self.assertError(build(rules=["nope"]), "rules[0]")

    def test_duplicate_rule_id(self):
        rule = {"id": "dup", "if": [], "then": [["alice", "knows", "bob"]]}
        self.assertError(build(rules=[rule, dict(rule)]), "规则 id 重复", "dup")

    def test_rule_undeclared_property(self):
        self.assertError(build(rules=[{
            "id": "r", "if": [["?x", "ghost", "?y"]],
            "then": [["?y", "knows", "?x"]]}]), "r", "ghost")

    def test_predicate_cannot_be_variable(self):
        self.assertError(build(rules=[{
            "id": "r", "if": [["?x", "?p", "?y"]],
            "then": [["?y", "knows", "?x"]]}]), "r", "谓语")

    def test_unbound_then_variable(self):
        self.assertError(build(rules=[{
            "id": "r9",
            "if": [["?x", "knows", "bob"]],
            "then": [["?x", "knows", "?z"]]}]),
            "r9", "?z", "未在 if 中绑定")

    def test_unbound_when_if_empty(self):
        self.assertError(build(rules=[{
            "id": "q", "if": [], "then": [["?x", "knows", "bob"]]}]),
            "q", "?x")

    def test_then_constant_must_be_individual(self):
        self.assertError(build(rules=[{
            "id": "r", "if": [["?x", "knows", "?y"]],
            "then": [["ghost", "knows", "?x"]]}]), "r", "ghost")

    def test_bare_variable_mark(self):
        self.assertError(build(rules=[{
            "id": "r", "if": [["?", "knows", "?y"]],
            "then": [["?y", "knows", "?y"]]}]), "r", "变量缺少名称")


if __name__ == "__main__":
    unittest.main()
