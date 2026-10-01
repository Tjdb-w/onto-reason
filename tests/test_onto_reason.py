import json
import unittest

from onto_reason import (
    InconsistencyError,
    OntologyEngine,
    OntologyError,
    QueryResult,
    Triple,
)


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


class ConsistencyValidationTests(unittest.TestCase):
    """consistency 段结构/取值错误应抛 OntologyError 并定位字段。"""

    def test_consistency_must_be_object(self):
        with self.assertRaises(OntologyError) as ctx:
            parse(make_doc(consistency=[]))
        self.assertIn("consistency", str(ctx.exception))

    def test_consistency_wrong_key_set(self):
        bad = {"classMembershipPredicate": "rdfType", "disjointClasses": []}
        with self.assertRaises(OntologyError) as ctx:
            parse(consistency_doc(consistency=bad))
        self.assertIn("functionalProperties", str(ctx.exception))
        good_base = {
            "classMembershipPredicate": "rdfType",
            "disjointClasses": [],
            "functionalProperties": [],
        }
        extra = dict(good_base, bogus=1)
        with self.assertRaises(OntologyError) as ctx:
            parse(consistency_doc(consistency=extra))
        self.assertIn("bogus", str(ctx.exception))

    def test_membership_predicate_must_be_declared_nonempty_string(self):
        with self.assertRaises(OntologyError) as ctx:
            parse(
                consistency_doc(
                    consistency={
                        "classMembershipPredicate": "",
                        "disjointClasses": [],
                        "functionalProperties": [],
                    }
                )
            )
        self.assertIn("classMembershipPredicate", str(ctx.exception))
        with self.assertRaises(OntologyError) as ctx:
            parse(
                consistency_doc(
                    consistency={
                        "classMembershipPredicate": "notDeclared",
                        "disjointClasses": [],
                        "functionalProperties": [],
                    }
                )
            )
        self.assertIn("notDeclared", str(ctx.exception))

    def test_disjoint_item_bad_shape_and_id(self):
        base = {
            "classMembershipPredicate": "rdfType",
            "disjointClasses": [{"id": "d1"}],
            "functionalProperties": [],
        }
        with self.assertRaises(OntologyError) as ctx:
            parse(consistency_doc(consistency=base))
        self.assertIn("consistency['disjointClasses'][0]", str(ctx.exception))

        base["disjointClasses"] = [{"id": "", "classes": ["Person", "Person"]}]
        with self.assertRaises(OntologyError) as ctx:
            parse(consistency_doc(consistency=base))
        self.assertIn("consistency['disjointClasses'][0]", str(ctx.exception))
        self.assertIn("'id'", str(ctx.exception))

        base["disjointClasses"] = [{"id": True, "classes": ["Person"]}]
        with self.assertRaises(OntologyError) as ctx:
            parse(consistency_doc(consistency=base))
        self.assertIn("id", str(ctx.exception))

    def test_disjoint_classes_requirements(self):
        base = {
            "classMembershipPredicate": "rdfType",
            "functionalProperties": [],
        }
        base["disjointClasses"] = [{"id": 1, "classes": ["Person"]}]
        with self.assertRaises(OntologyError) as ctx:
            parse(consistency_doc(consistency=base))
        self.assertIn("两个类", str(ctx.exception))

        base["disjointClasses"] = [{"id": 1, "classes": ["Person", "Person"]}]
        with self.assertRaises(OntologyError) as ctx:
            parse(consistency_doc(consistency=base))
        self.assertIn("Person", str(ctx.exception))
        self.assertIn("重复", str(ctx.exception))

        base["disjointClasses"] = [{"id": 1, "classes": ["Person", "Ghost"]}]
        with self.assertRaises(OntologyError) as ctx:
            parse(consistency_doc(consistency=base))
        self.assertIn("Ghost", str(ctx.exception))
        self.assertIn("未声明的类", str(ctx.exception))

    def test_functional_item_validation(self):
        base = {
            "classMembershipPredicate": "rdfType",
            "disjointClasses": [],
            "functionalProperties": [{"id": "f1"}],
        }
        with self.assertRaises(OntologyError) as ctx:
            parse(consistency_doc(consistency=base))
        self.assertIn("consistency['functionalProperties'][0]", str(ctx.exception))

        base["functionalProperties"] = [{"id": "f1", "property": ""}]
        with self.assertRaises(OntologyError) as ctx:
            parse(consistency_doc(consistency=base))
        self.assertIn("property", str(ctx.exception))

        base["functionalProperties"] = [{"id": "f1", "property": "nope"}]
        with self.assertRaises(OntologyError) as ctx:
            parse(consistency_doc(consistency=base))
        self.assertIn("nope", str(ctx.exception))

    def test_merged_ids_must_be_unique(self):
        consistency = {
            "classMembershipPredicate": "rdfType",
            "disjointClasses": [
                {"id": 7, "classes": ["Person", "Other"]},
            ],
            "functionalProperties": [
                {"id": 7, "property": "knows"},
            ],
        }
        doc = consistency_doc(
            classes=["Person", "Robot", "Animal", "Other"], consistency=consistency
        )
        with self.assertRaises(OntologyError) as ctx:
            parse(doc)
        self.assertIn("7", str(ctx.exception))
        self.assertIn("重复", str(ctx.exception))

    def test_integer_and_string_ids_distinct(self):
        consistency = {
            "classMembershipPredicate": "rdfType",
            "disjointClasses": [
                {"id": 7, "classes": ["Person", "Other"]},
            ],
            "functionalProperties": [
                {"id": "7", "property": "knows"},
            ],
        }
        doc = consistency_doc(
            classes=["Person", "Robot", "Animal", "Other"], consistency=consistency
        )
        # 7 与 "7" 不重复：无冲突时正常返回模型
        model = parse(doc)
        self.assertIsNotNone(model)

    def test_consistency_arrays_wrong_type(self):
        with self.assertRaises(OntologyError) as ctx:
            parse(
                consistency_doc(
                    consistency={
                        "classMembershipPredicate": "rdfType",
                        "disjointClasses": {},
                        "functionalProperties": [],
                    }
                )
            )
        self.assertIn("disjointClasses", str(ctx.exception))


def consistency_doc(**overrides):
    """带类型声明与成员属性的文档。"""
    doc = make_doc(
        classes=["Person", "Robot", "Animal"],
        properties=["rdfType", "knows", "likes", "friendOf", "age"],
    )
    doc.update(overrides)
    return doc


def consistency_section(disjoint=None, functional=None, predicate="rdfType"):
    return {
        "classMembershipPredicate": predicate,
        "disjointClasses": disjoint or [],
        "functionalProperties": functional or [],
    }


class InconsistencyDetectionTests(unittest.TestCase):
    def test_no_consistency_keeps_model(self):
        model = parse(make_doc())
        self.assertIsInstance(model.triples, tuple)

    def test_consistency_without_conflict_returns_model(self):
        section = consistency_section(
            disjoint=[{"id": "d1", "classes": ["Person", "Robot"]}],
            functional=[{"id": "f1", "property": "age"}],
        )
        doc = consistency_doc(consistency=section)
        model = parse(doc)
        self.assertEqual(len(model.explicit_triples), 2)

    def test_explicit_disjoint_conflict(self):
        triples = [
            {"subject": "alice", "predicate": "rdfType", "object": "Person"},
            {"subject": "alice", "predicate": "rdfType", "object": "Robot"},
        ]
        section = consistency_section(
            disjoint=[{"id": "d1", "classes": ["Robot", "Person"]}]
        )
        with self.assertRaises(InconsistencyError) as ctx:
            parse(consistency_doc(triples=triples, consistency=section))
        message = str(ctx.exception)
        # 两个类名按字典序列出（Person 先于 Robot）
        self.assertIn("Person", message)
        self.assertLess(message.index("Person"), message.index("Robot"))
        self.assertIn("d1", message)
        self.assertIn("alice", message)
        self.assertIn("explicit_triples", message)
        self.assertIsInstance(ctx.exception, OntologyError)

    def test_derived_membership_conflict_marks_source_rule(self):
        triples = [
            {"subject": "alice", "predicate": "rdfType", "object": "Person"},
            {"subject": "alice", "predicate": "knows", "object": "bob"},
        ]
        rules = [
            {
                "id": "r-to-robot",
                "if": [{"subject": "?x", "predicate": "knows", "object": "?y"}],
                "then": [{"subject": "?x", "predicate": "rdfType", "object": "Robot"}],
            }
        ]
        section = consistency_section(
            disjoint=[{"id": 9, "classes": ["Person", "Robot"]}]
        )
        with self.assertRaises(InconsistencyError) as ctx:
            parse(
                consistency_doc(
                    triples=triples, rules=rules, consistency=section
                )
            )
        message = str(ctx.exception)
        self.assertIn("derived_triples", message)
        self.assertIn("r-to-robot", message)
        self.assertIn("explicit_triples", message)

    def test_functional_conflict_duplicate_objects(self):
        triples = [
            {"subject": "alice", "predicate": "age", "object": "30"},
            {"subject": "alice", "predicate": "age", "object": "31"},
        ]
        section = consistency_section(
            functional=[{"id": "f-age", "property": "age"}]
        )
        with self.assertRaises(InconsistencyError) as ctx:
            parse(consistency_doc(triples=triples, consistency=section))
        message = str(ctx.exception)
        self.assertIn("age", message)
        self.assertIn("30", message)
        self.assertIn("31", message)
        # object 按字典序列出
        self.assertLess(message.index("'30'"), message.index("'31'"))

    def test_functional_same_triple_not_conflict(self):
        triples = [
            {"subject": "alice", "predicate": "age", "object": "30"},
            {"subject": "alice", "predicate": "age", "object": "30"},
        ]
        section = consistency_section(
            functional=[{"id": "f-age", "property": "age"}]
        )
        model = parse(consistency_doc(triples=triples, consistency=section))
        self.assertEqual(len(model.explicit_triples), 1)

    def test_functional_conflict_derived_and_explicit(self):
        triples = [
            {"subject": "alice", "predicate": "age", "object": "30"},
            {"subject": "bob", "predicate": "knows", "object": "carol"},
        ]
        rules = [
            {
                "id": "r-age",
                "if": [{"subject": "?x", "predicate": "knows", "object": "?y"}],
                "then": [{"subject": "?x", "predicate": "age", "object": "40"}],
            }
        ]
        # alice 无冲突；bob 只有一个值也无冲突。改为让 bob 显式有一个值：
        triples.append({"subject": "bob", "predicate": "age", "object": "50"})
        section = consistency_section(
            functional=[{"id": "f-age", "property": "age"}]
        )
        with self.assertRaises(InconsistencyError) as ctx:
            parse(
                consistency_doc(
                    triples=triples, rules=rules, consistency=section
                )
            )
        message = str(ctx.exception)
        self.assertIn("bob", message)
        self.assertIn("40", message)
        self.assertIn("50", message)
        self.assertIn("r-age", message)
        # alice 仅有一个取值，不应出现
        self.assertNotIn("个体 'alice'", message)

    def test_membership_requires_declared_individual_and_class(self):
        triples = [
            {"subject": "ghost", "predicate": "rdfType", "object": "Person"},
            {"subject": "ghost", "predicate": "rdfType", "object": "Robot"},
            {"subject": "alice", "predicate": "rdfType", "object": "Thing"},
        ]
        section = consistency_section(
            disjoint=[{"id": "d1", "classes": ["Person", "Robot"]}]
        )
        # ghost 未声明为个体；Thing 未声明为类：均不产生冲突
        model = parse(consistency_doc(triples=triples, consistency=section))
        self.assertEqual(len(model.triples), 3)

    def test_conflicts_sorted_by_id_type_subject(self):
        triples = [
            {"subject": "bob", "predicate": "rdfType", "object": "Person"},
            {"subject": "bob", "predicate": "rdfType", "object": "Robot"},
            {"subject": "alice", "predicate": "rdfType", "object": "Person"},
            {"subject": "alice", "predicate": "rdfType", "object": "Robot"},
            {"subject": "alice", "predicate": "age", "object": "1"},
            {"subject": "alice", "predicate": "age", "object": "2"},
        ]
        section = consistency_section(
            disjoint=[{"id": 2, "classes": ["Person", "Robot"]}],
            functional=[{"id": 1, "property": "age"}],
        )
        with self.assertRaises(InconsistencyError) as ctx:
            parse(consistency_doc(triples=triples, consistency=section))
        lines = [ln for ln in str(ctx.exception).splitlines() if ln.startswith("[")]
        # id 1 (functional) 在 id 2 (disjoint) 之前
        self.assertIn("functionalProperties id=1", lines[0])
        self.assertTrue(
            any("disjointClasses id=2" in ln for ln in lines[1:]),
            lines,
        )
        # 同一 id 内按 subject：alice 先于 bob
        disjoint_lines = [ln for ln in lines if "disjointClasses" in ln]
        self.assertIn("'alice'", disjoint_lines[0])
        self.assertIn("'bob'", disjoint_lines[1])

    def test_all_conflicts_listed(self):
        triples = [
            {"subject": "alice", "predicate": "rdfType", "object": "Person"},
            {"subject": "alice", "predicate": "rdfType", "object": "Robot"},
            {"subject": "alice", "predicate": "rdfType", "object": "Animal"},
        ]
        section = consistency_section(
            disjoint=[{"id": "d1", "classes": ["Animal", "Person", "Robot"]}]
        )
        with self.assertRaises(InconsistencyError) as ctx:
            parse(consistency_doc(triples=triples, consistency=section))
        message = str(ctx.exception)
        # 三个类两两配对，共 3 条
        self.assertEqual(message.count("[disjointClasses id='d1']"), 3)

    def test_message_stable_across_parses_and_key_order(self):
        section = consistency_section(
            disjoint=[{"id": "d1", "classes": ["Person", "Robot"]}],
            functional=[{"id": "f1", "property": "age"}],
        )
        triples = [
            {"subject": "alice", "predicate": "rdfType", "object": "Robot"},
            {"subject": "alice", "predicate": "rdfType", "object": "Person"},
            {"subject": "alice", "predicate": "age", "object": "9"},
            {"subject": "alice", "predicate": "age", "object": "1"},
        ]
        doc = consistency_doc(triples=triples, consistency=section)
        messages = []
        for _ in range(2):
            with self.assertRaises(InconsistencyError) as ctx:
                OntologyEngine().parse(json.dumps(doc))
            messages.append(str(ctx.exception))
        self.assertEqual(messages[0], messages[1])
        # JSON 键顺序不同不影响消息
        shuffled = {
            "functionalProperties": section["functionalProperties"],
            "rules": doc["rules"],
            "triples": list(reversed(doc["triples"])),
            "individuals": doc["individuals"],
            "properties": doc["properties"],
            "classes": doc["classes"],
            "disjointClasses": None,
        }
        shuffled["consistency"] = {
            "functionalProperties": section["functionalProperties"],
            "classMembershipPredicate": "rdfType",
            "disjointClasses": section["disjointClasses"],
        }
        del shuffled["disjointClasses"]
        with self.assertRaises(InconsistencyError) as ctx:
            OntologyEngine().parse(json.dumps(shuffled))
        self.assertEqual(str(ctx.exception), messages[0])

    def test_no_model_returned_on_inconsistency(self):
        section = consistency_section(
            disjoint=[{"id": "d1", "classes": ["Person", "Robot"]}]
        )
        triples = [
            {"subject": "alice", "predicate": "rdfType", "object": "Person"},
            {"subject": "alice", "predicate": "rdfType", "object": "Robot"},
        ]
        outcome = []

        def parse_or_none():
            try:
                return parse(consistency_doc(triples=triples, consistency=section))
            except InconsistencyError:
                return None

        self.assertIsNone(parse_or_none())
        self.assertEqual(outcome, [])

    def test_output_unchanged_when_consistent(self):
        section = consistency_section(
            disjoint=[{"id": "d1", "classes": ["Person", "Robot"]}],
            functional=[{"id": "f1", "property": "likes"}],
        )
        without = parse(consistency_doc())
        with_section = parse(consistency_doc(consistency=section))
        self.assertEqual(without.explicit_triples, with_section.explicit_triples)
        self.assertEqual(without.derived_triples, with_section.derived_triples)
        self.assertEqual(without.triples, with_section.triples)
        query = "SELECT ?x ?y WHERE { ?x knows ?y }"
        self.assertEqual(without.query(query), with_section.query(query))
        self.assertEqual(
            without.source_rule("alice", "knows", "bob"),
            with_section.source_rule("alice", "knows", "bob"),
        )
        self.assertEqual(without.entails("alice", "knows", "bob"),
                         with_section.entails("alice", "knows", "bob"))


if __name__ == "__main__":
    unittest.main()
