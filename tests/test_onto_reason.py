import json
import unittest

from onto_reason import OntologyEngine, OntologyError, QueryResult, Triple


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


def query_model():
    """带显式事实与推理结论的模型，供查询测试使用。"""
    rules = [
        {
            "id": "knows-likes",
            "if": [{"subject": "?x", "predicate": "knows", "object": "?y"}],
            "then": [{"subject": "?x", "predicate": "likes", "object": "?y"}],
        },
        {
            "id": "friend-of-friend",
            "if": [
                {"subject": "?x", "predicate": "knows", "object": "?y"},
                {"subject": "?y", "predicate": "likes", "object": "?z"},
            ],
            "then": [{"subject": "?x", "predicate": "friendOf", "object": "?z"}],
        },
    ]
    triples = make_doc()["triples"] + [
        {"subject": "bob", "predicate": "likes", "object": "carol"}
    ]
    return parse(make_doc(triples=triples, rules=rules))


class QueryTests(unittest.TestCase):
    def test_basic_join_returns_query_result(self):
        model = query_model()
        result = model.query(
            "SELECT ?x ?y WHERE { ?x knows ?y . ?y likes ?z }"
        )
        self.assertIsInstance(result, QueryResult)
        self.assertEqual(result.variables, ("?x", "?y"))
        self.assertEqual(result.rows, (("alice", "bob"),))

    def test_star_projection_first_appearance_order(self):
        model = query_model()
        result = model.query(
            "SELECT * WHERE { ?x knows ?y . ?y likes ?z }"
        )
        self.assertEqual(result.variables, ("?x", "?y", "?z"))
        self.assertEqual(result.rows, (("alice", "bob", "carol"),))

    def test_derived_triples_match_like_explicit(self):
        model = query_model()
        # alice likes bob 由规则 knows-likes 推出，应与显式事实一样命中
        result = model.query("SELECT ?x ?y WHERE { ?x likes ?y }")
        self.assertEqual(
            result.rows,
            (("alice", "bob"), ("bob", "carol")),
        )
        # 但 source_rule 仍只描述推理来源
        self.assertEqual(model.source_rule("alice", "likes", "bob"), "knows-likes")
        self.assertIsNone(model.source_rule("bob", "likes", "carol"))

    def test_constant_subject_and_object(self):
        model = query_model()
        self.assertEqual(
            model.query("SELECT ?y WHERE { alice knows ?y }").rows,
            (("bob",),),
        )
        self.assertEqual(
            model.query("SELECT ?y WHERE { ?y knows carol }").rows,
            (("bob",),),
        )
        self.assertEqual(
            model.query("SELECT * WHERE { alice knows carol }").rows,
            (),
        )

    def test_same_variable_bound_equal_within_pattern(self):
        model = query_model()
        self.assertEqual(
            model.query("SELECT ?x WHERE { ?x knows ?x }").rows, ()
        )

    def test_rows_deduped_and_sorted_by_projection(self):
        model = query_model()
        # 仅投影 ?y，?x 取值不同不产生重复行
        result = model.query("SELECT ?y WHERE { ?x knows ?y }")
        self.assertEqual(result.rows, (("bob",), ("carol",)))

    def test_no_match_returns_empty_rows(self):
        model = query_model()
        result = model.query("SELECT ?x WHERE { ?x knows nobody }")
        self.assertEqual(result.variables, ("?x",))
        self.assertEqual(result.rows, ())

    def test_ground_pattern_star_has_no_variables(self):
        model = query_model()
        hit = model.query("SELECT * WHERE { alice knows bob }")
        self.assertEqual(hit.variables, ())
        self.assertEqual(hit.rows, ((),))
        miss = model.query("SELECT * WHERE { alice knows nobody }")
        self.assertEqual(miss.rows, ())

    def test_trailing_dot_and_mixed_whitespace(self):
        model = query_model()
        result = model.query(
            "SELECT\t?x\nWHERE  {\n ?x knows ?y .\n}"
        )
        self.assertEqual(result.rows, (("alice",), ("bob",)))

    def test_json_string_literal_decoded_and_exact(self):
        doc = make_doc(
            triples=[
                {"subject": "alice", "predicate": "likes", "object": 'a"b  c'}
            ]
        )
        model = parse(doc)
        self.assertEqual(
            model.query(r'SELECT ?s WHERE { ?s likes "a\"b  c" }').rows,
            (("alice",),),
        )
        self.assertEqual(
            model.query(r'SELECT ?s WHERE { ?s likes "a\"b c" }').rows,
            (),
        )

    def test_unicode_variable_and_name(self):
        doc = make_doc(
            triples=[
                {"subject": "张三", "predicate": "knows", "object": "李四"}
            ]
        )
        model = OntologyEngine().parse(json.dumps(doc, ensure_ascii=False))
        result = model.query("SELECT ?人1 WHERE { ?人1 knows ?_y }")
        self.assertEqual(result.variables, ("?人1",))
        self.assertEqual(result.rows, (("张三",),))

    def test_pattern_order_only_affects_star_discovery(self):
        model = query_model()
        explicit_a = model.query(
            "SELECT ?x ?y ?z WHERE { ?x knows ?y . ?y likes ?z }"
        )
        explicit_b = model.query(
            "SELECT ?x ?y ?z WHERE { ?y likes ?z . ?x knows ?y }"
        )
        self.assertEqual(explicit_a.rows, explicit_b.rows)
        star_a = model.query(
            "SELECT * WHERE { ?x knows ?y . ?y likes ?z }"
        )
        star_b = model.query(
            "SELECT * WHERE { ?y likes ?z . ?x knows ?y }"
        )
        self.assertEqual(star_a.variables, ("?x", "?y", "?z"))
        self.assertEqual(star_b.variables, ("?y", "?z", "?x"))
        bindings_a = {tuple(sorted(zip(star_a.variables, r))) for r in star_a.rows}
        bindings_b = {tuple(sorted(zip(star_b.variables, r))) for r in star_b.rows}
        self.assertEqual(bindings_a, bindings_b)

    def test_repeated_execution_identical(self):
        model = query_model()
        text = "SELECT * WHERE { ?x knows ?y . ?y likes ?z }"
        first = model.query(text)
        for _ in range(3):
            again = model.query(text)
            self.assertEqual(again, first)
            self.assertIsNot(again.rows, first.rows)

    def test_rows_are_immutable_tuples(self):
        model = query_model()
        result = model.query("SELECT ?x WHERE { ?x knows ?y }")
        self.assertIsInstance(result.variables, tuple)
        self.assertIsInstance(result.rows, tuple)
        self.assertTrue(all(isinstance(row, tuple) for row in result.rows))
        with self.assertRaises(TypeError):
            result.rows[0] = ("x",)

    def test_query_does_not_mutate_model(self):
        model = query_model()
        before = (model.explicit_triples, model.derived_triples, model.triples)
        model.query("SELECT * WHERE { ?x knows ?y . ?y likes ?z }")
        after = (model.explicit_triples, model.derived_triples, model.triples)
        self.assertEqual(before, after)


class QuerySyntaxErrorTests(unittest.TestCase):
    def setUp(self):
        self.model = query_model()

    def assertQueryError(self, text, *fragments):
        with self.assertRaises(OntologyError) as ctx:
            self.model.query(text)
        message = str(ctx.exception)
        self.assertTrue(
            "字符位置" in message or "模式" in message,
            f"错误消息缺少字符位置或模式序号: {message}",
        )
        for fragment in fragments:
            self.assertIn(fragment, message)

    def test_lowercase_keywords_rejected(self):
        self.assertQueryError("select ?x WHERE { ?x knows ?y }")
        self.assertQueryError("SELECT ?x where { ?x knows ?y }")

    def test_missing_keywords_and_braces(self):
        self.assertQueryError("?x WHERE { ?x knows ?y }")
        self.assertQueryError("SELECT WHERE { ?x knows ?y }")
        self.assertQueryError("SELECT ?x { ?x knows ?y }")
        self.assertQueryError("SELECT ?x WHERE  ?x knows ?y }")
        self.assertQueryError("SELECT ?x WHERE { ?x knows ?y ")
        self.assertQueryError("SELECT ?x WHERE { ?x knows ?y } }")
        self.assertQueryError("")
        self.assertQueryError("SELECT")

    def test_duplicate_or_unknown_projection_variable(self):
        self.assertQueryError(
            "SELECT ?x ?x WHERE { ?x knows ?y }", "?x", "重复"
        )
        self.assertQueryError(
            "SELECT ?z WHERE { ?x knows ?y }", "?z", "未"
        )

    def test_empty_pattern_body(self):
        self.assertQueryError("SELECT * WHERE { }", "空")
        self.assertQueryError("SELECT * WHERE {  }", "空")

    def test_variable_predicate_rejected(self):
        self.assertQueryError("SELECT * WHERE { ?x ?p ?y }", "谓语", "模式 0")

    def test_unknown_statement_component(self):
        self.assertQueryError("SELECT ?x WHERE { ?x knows ?y } EXTRA")
        self.assertQueryError("SELECT * * WHERE { ?x knows ?y }")
        self.assertQueryError("SELECT . ?x WHERE { ?x knows ?y }")

    def test_pattern_arity_too_few_or_many(self):
        self.assertQueryError("SELECT * WHERE { ?x knows }", "模式 0", "项数不足")
        self.assertQueryError(
            "SELECT * WHERE { ?x knows ?y likes ?z }", "项数过多"
        )
        self.assertQueryError(
            "SELECT * WHERE { ?x knows ?y . knows ?y }", "模式 1", "项数不足"
        )
        self.assertQueryError(
            "SELECT * WHERE { . ?x knows ?y }", "'.'"
        )
        self.assertQueryError(
            "SELECT * WHERE { ?x knows ?y . . ?y knows ?z }", "'.'"
        )

    def test_lexical_errors(self):
        self.assertQueryError("SELECT ? WHERE { ?x knows ?y }")
        self.assertQueryError('SELECT * WHERE { ?x likes "unclosed }')
        self.assertQueryError(r'SELECT * WHERE { ?x likes "bad\q" }')

    def test_non_str_input(self):
        with self.assertRaises(OntologyError) as ctx:
            self.model.query(123)
        self.assertIn("str", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
