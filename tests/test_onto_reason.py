import json
import unittest

from onto_reason import (
    InconsistencyError,
    OntologyEngine,
    OntologyError,
    QueryResult,
    Triple,
    ValidationReport,
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


def optional_model():
    """带可选关联数据的模型：dave 没有 likes；只有 carol 有 email。"""
    doc = {
        "classes": ["Person"],
        "properties": ["knows", "likes", "email", "label"],
        "individuals": ["alice", "bob", "carol", "dave"],
        "triples": [
            {"subject": "alice", "predicate": "knows", "object": "bob"},
            {"subject": "bob", "predicate": "knows", "object": "carol"},
            {"subject": "carol", "predicate": "knows", "object": "dave"},
            {"subject": "bob", "predicate": "likes", "object": "tennis"},
            {"subject": "bob", "predicate": "likes", "object": "soccer"},
            {"subject": "carol", "predicate": "likes", "object": "music"},
            {"subject": "carol", "predicate": "email", "object": "c@example.com"},
        ],
        "rules": [],
    }
    return parse(doc)


class OptionalFilterTests(unittest.TestCase):
    def setUp(self):
        self.model = optional_model()

    def test_optional_extends_each_matching_binding(self):
        # bob 有两个 likes，左连接应扩展出两行；carol 一个；dave 无匹配保留 None
        result = self.model.query(
            "SELECT * WHERE { ?x knows ?y . OPTIONAL { ?y likes ?z } }"
        )
        self.assertEqual(result.variables, ("?x", "?y", "?z"))
        self.assertEqual(
            result.rows,
            (
                ("alice", "bob", "soccer"),
                ("alice", "bob", "tennis"),
                ("bob", "carol", "music"),
                ("carol", "dave", None),
            ),
        )

    def test_optional_no_match_keeps_binding_once_with_none(self):
        result = self.model.query(
            "SELECT ?x ?e WHERE { ?x knows ?y . OPTIONAL { ?x email ?e } }"
        )
        self.assertEqual(
            result.rows,
            (("alice", None), ("bob", None), ("carol", "c@example.com")),
        )

    def test_optional_multiple_patterns_are_all_or_nothing(self):
        # label 三元组不存在，块内整组无任何匹配，两个块内变量均未绑定
        result = self.model.query(
            "SELECT * WHERE { ?x knows ?y ."
            " OPTIONAL { ?y likes ?z . ?z label ?l } }"
        )
        self.assertEqual(result.variables, ("?x", "?y", "?z", "?l"))
        self.assertEqual(
            result.rows,
            (
                ("alice", "bob", None, None),
                ("bob", "carol", None, None),
                ("carol", "dave", None, None),
            ),
        )

    def test_optional_blocks_processed_in_order(self):
        result = self.model.query(
            "SELECT * WHERE { ?x knows ?y ."
            " OPTIONAL { ?y likes ?z } ."
            " OPTIONAL { ?y email ?e } }"
        )
        self.assertEqual(result.variables, ("?x", "?y", "?z", "?e"))
        self.assertEqual(
            result.rows,
            (
                ("alice", "bob", "soccer", None),
                ("alice", "bob", "tennis", None),
                ("bob", "carol", "music", "c@example.com"),
                ("carol", "dave", None, None),
            ),
        )

    def test_chained_optional_depends_on_previous_optional_variable(self):
        # 第二个 OPTIONAL 引用第一个块的 ?z；?z 未绑定时整块无匹配
        result = self.model.query(
            "SELECT * WHERE { ?x knows ?y ."
            " OPTIONAL { ?y likes ?z } ."
            " OPTIONAL { ?z label ?l } }"
        )
        self.assertEqual(
            result.rows,
            (
                ("alice", "bob", "soccer", None),
                ("alice", "bob", "tennis", None),
                ("bob", "carol", "music", None),
                ("carol", "dave", None, None),
            ),
        )

    def test_optional_with_derived_triples(self):
        model = query_model()
        # bob likes carol 是显式事实；carol 没有任何 likes
        result = model.query(
            "SELECT * WHERE { ?x knows ?y . OPTIONAL { ?y likes ?z } }"
        )
        self.assertEqual(
            result.rows,
            (("alice", "bob", "carol"), ("bob", "carol", None)),
        )

    def test_optional_inner_join_filters_on_outer_binding(self):
        # 块内 ?y 已由外层绑定，只匹配以该 ?y 为主语的 email
        result = self.model.query(
            "SELECT ?y ?e WHERE { ?x knows ?y . OPTIONAL { ?y email ?e } }"
        )
        self.assertEqual(
            result.rows,
            (("bob", None), ("carol", "c@example.com"), ("dave", None)),
        )

    def test_optional_variable_can_be_explicit_projection(self):
        result = self.model.query(
            "SELECT ?z WHERE { ?x knows ?y . OPTIONAL { ?y likes ?z } }"
        )
        self.assertEqual(result.variables, ("?z",))
        # None 占位行经投影去重后只剩一个 None 行
        self.assertEqual(result.rows, ((None,), ("music",), ("soccer",), ("tennis",)))

    def test_none_sorts_before_strings(self):
        result = self.model.query(
            "SELECT ?z WHERE { ?x knows ?y . OPTIONAL { ?y likes ?z } }"
        )
        self.assertIsNone(result.rows[0][0])

    def test_star_variable_order_includes_optional_first_appearance(self):
        result = self.model.query(
            "SELECT * WHERE { OPTIONAL { ?a label ?b } ?x knows ?y ."
            " OPTIONAL { ?y likes ?z } }"
        )
        self.assertEqual(result.variables, ("?a", "?b", "?x", "?y", "?z"))

    def test_filter_bound_and_not_bound(self):
        bound = self.model.query(
            "SELECT ?y ?z WHERE { ?x knows ?y ."
            " OPTIONAL { ?y likes ?z } . FILTER(BOUND(?z)) }"
        )
        self.assertEqual(
            bound.rows,
            (("bob", "soccer"), ("bob", "tennis"), ("carol", "music")),
        )
        not_bound = self.model.query(
            "SELECT ?y ?z WHERE { ?x knows ?y ."
            " OPTIONAL { ?y likes ?z } . FILTER(!BOUND(?z)) }"
        )
        self.assertEqual(not_bound.rows, (("dave", None),))

    def test_filter_variable_equality_and_inequality(self):
        equal = self.model.query(
            "SELECT ?x ?y WHERE { ?x knows ?y . FILTER(?x = ?y) }"
        )
        self.assertEqual(equal.rows, ())
        not_equal = self.model.query(
            "SELECT ?x ?y WHERE { ?x knows ?y . FILTER(?x != ?y) }"
        )
        self.assertEqual(
            not_equal.rows,
            (("alice", "bob"), ("bob", "carol"), ("carol", "dave")),
        )

    def test_filter_string_literal_comparisons(self):
        prefix = "SELECT ?y WHERE { ?x knows ?y . OPTIONAL { ?y likes ?z } "
        self.assertEqual(
            self.model.query(prefix + ". FILTER(?z = \"tennis\") }").rows,
            (("bob",),),
        )
        self.assertEqual(
            self.model.query(prefix + ". FILTER(?z != \"tennis\") }").rows,
            (("bob",), ("carol",)),
        )
        self.assertEqual(
            self.model.query(
                "SELECT ?x WHERE { ?x knows ?y . FILTER(\"a\" = \"a\") }"
            ).rows,
            (("alice",), ("bob",), ("carol",)),
        )
        self.assertEqual(
            self.model.query(
                "SELECT ?x WHERE { ?x knows ?y . FILTER(\"a\" != \"a\") }"
            ).rows,
            (),
        )
        self.assertEqual(
            self.model.query(
                "SELECT ?y WHERE { ?x knows ?y ."
                " OPTIONAL { ?y likes ?z } . FILTER(\"tennis\" = ?z) }"
            ).rows,
            (("bob",),),
        )

    def test_filter_unbound_comparison_is_false_for_eq_and_ne(self):
        # ?z 在 dave 行未绑定：= 与 != 都为假，该行被过滤
        for op in ("=", "!="):
            result = self.model.query(
                f"SELECT ?y WHERE {{ ?x knows ?y ."
                f" OPTIONAL {{ ?y likes ?z }} . FILTER(?z {op} \"tennis\") }}"
            )
            self.assertNotIn(("dave",), result.rows)

    def test_multiple_filters_combined_with_logical_and(self):
        result = self.model.query(
            "SELECT ?z WHERE { ?x knows ?y . OPTIONAL { ?y likes ?z } ."
            " FILTER(BOUND(?z)) . FILTER(?z != \"tennis\") }"
        )
        # bob 的另一个取值 soccer 与 carol 的 music 保留
        self.assertEqual(result.rows, (("music",), ("soccer",)))

    def test_filter_before_optional(self):
        result = self.model.query(
            "SELECT ?y WHERE { ?x knows ?y . FILTER(?y = \"carol\") ."
            " OPTIONAL { ?y likes ?z } }"
        )
        self.assertEqual(result.rows, (("carol",),))

    def test_filter_clause_boundaries_do_not_require_dots(self):
        result = self.model.query(
            "SELECT ?y WHERE { ?x knows ?y . OPTIONAL { ?y likes ?z }"
            " FILTER(BOUND(?z)) }"
        )
        # 按 ?y 投影去重：dave 因 ?z 未绑定被过滤
        self.assertEqual(result.rows, (("bob",), ("carol",)))

    def test_optional_filter_result_is_deduped_sorted_and_immutable(self):
        result = self.model.query(
            "SELECT ?z WHERE { ?x knows ?y . OPTIONAL { ?y likes ?z } }"
        )
        self.assertIsInstance(result.rows, tuple)
        self.assertTrue(all(isinstance(row, tuple) for row in result.rows))
        self.assertEqual(result.rows, tuple(sorted(result.rows, key=lambda r: (0, "") if r[0] is None else (1, r[0]))))
        with self.assertRaises(TypeError):
            result.rows[0] = ("x",)

    def test_query_with_optional_filter_is_repeatable(self):
        text = (
            "SELECT * WHERE { ?x knows ?y ."
            " OPTIONAL { ?y likes ?z } . FILTER(BOUND(?x)) }"
        )
        first = self.model.query(text)
        for _ in range(3):
            self.assertEqual(self.model.query(text), first)

    def test_optional_filter_query_does_not_mutate_model(self):
        before = (
            self.model.explicit_triples,
            self.model.derived_triples,
            self.model.triples,
        )
        self.model.query(
            "SELECT * WHERE { ?x knows ?y . OPTIONAL { ?y likes ?z } ."
            " FILTER(!BOUND(?z)) }"
        )
        after = (
            self.model.explicit_triples,
            self.model.derived_triples,
            self.model.triples,
        )
        self.assertEqual(before, after)

    def test_optional_filter_does_not_change_other_model_behaviors(self):
        self.assertTrue(self.model.entails("bob", "likes", "tennis"))
        self.assertIsNone(self.model.source_rule("bob", "likes", "tennis"))
        self.assertEqual(
            self.model.query("SELECT ?x ?y WHERE { ?x knows ?y }").rows,
            (("alice", "bob"), ("bob", "carol"), ("carol", "dave")),
        )


class OptionalFilterSyntaxErrorTests(unittest.TestCase):
    def setUp(self):
        self.model = optional_model()

    def assertQueryError(self, text):
        with self.assertRaises(OntologyError) as ctx:
            self.model.query(text)
        message = str(ctx.exception)
        self.assertTrue(
            "字符位置" in message or "模式" in message,
            f"错误消息缺少字符位置或模式序号: {message}",
        )
        return message

    def test_nested_optional_rejected(self):
        self.assertQueryError(
            "SELECT * WHERE { ?x knows ?y ."
            " OPTIONAL { OPTIONAL { ?y likes ?z } } }"
        )

    def test_filter_inside_optional_rejected(self):
        self.assertQueryError(
            "SELECT * WHERE { ?x knows ?y ."
            " OPTIONAL { ?y likes ?z . FILTER(BOUND(?z)) } }"
        )

    def test_empty_optional_block_rejected(self):
        msg = self.assertQueryError(
            "SELECT * WHERE { ?x knows ?y . OPTIONAL { } }"
        )
        self.assertIn("OPTIONAL", msg)

    def test_optional_missing_brace(self):
        self.assertQueryError(
            "SELECT * WHERE { ?x knows ?y . OPTIONAL ?y likes ?z }"
        )
        self.assertQueryError(
            "SELECT * WHERE { ?x knows ?y . OPTIONAL { ?y likes ?z }"
        )

    def test_optional_trailing_dot_is_accepted(self):
        # 块内尾点号与 WHERE 体内尾点号行为一致
        result = self.model.query(
            "SELECT * WHERE { ?x knows ?y . OPTIONAL { ?y likes ?z . } }"
        )
        self.assertEqual(
            result.rows,
            (
                ("alice", "bob", "soccer"),
                ("alice", "bob", "tennis"),
                ("bob", "carol", "music"),
                ("carol", "dave", None),
            ),
        )

    def test_filter_missing_or_empty_parens(self):
        self.assertQueryError("SELECT * WHERE { ?x knows ?y . FILTER }")
        self.assertQueryError("SELECT * WHERE { ?x knows ?y . FILTER( }")
        self.assertQueryError("SELECT * WHERE { ?x knows ?y . FILTER() }")
        self.assertQueryError("SELECT * WHERE { ?x knows ?y . FILTER(?x = ?y")
        self.assertQueryError("SELECT * WHERE { ?x knows ?y . FILTER(BOUND(?x) }")

    def test_unknown_filter_expressions(self):
        self.assertQueryError(
            "SELECT * WHERE { ?x knows ?y . FILTER(?x > ?y) }"
        )
        self.assertQueryError(
            "SELECT * WHERE { ?x knows ?y . FILTER(?x = bob) }"
        )
        self.assertQueryError(
            "SELECT * WHERE { ?x knows ?y . FILTER(!?x) }"
        )
        self.assertQueryError(
            "SELECT * WHERE { ?x knows ?y . FILTER(?x) }"
        )
        self.assertQueryError(
            "SELECT * WHERE { ?x knows ?y . FILTER(BOUND) }"
        )
        self.assertQueryError(
            "SELECT * WHERE { ?x knows ?y . FILTER(BOUND(?x ?y)) }"
        )
        self.assertQueryError(
            "SELECT * WHERE { ?x knows ?y . FILTER(BOUND(?x) = 1) }"
        )
        self.assertQueryError(
            "SELECT * WHERE { ?x knows ?y . FILTER(!BOUND()) }"
        )

    def test_filter_only_body_is_empty_group(self):
        self.assertQueryError("SELECT * WHERE { FILTER(BOUND(?x)) }")

    def test_optional_keyword_as_constant_still_works(self):
        # 未跟 '{' 的 OPTIONAL 与未跟 '(' 的 FILTER 仍是普通常量名
        doc = make_doc(
            properties=["FILTER", "OPTIONAL"],
            triples=[
                {"subject": "alice", "predicate": "FILTER", "object": "v1"},
                {"subject": "alice", "predicate": "OPTIONAL", "object": "v2"},
            ],
        )
        model = parse(doc)
        self.assertEqual(
            model.query("SELECT ?v WHERE { ?s FILTER ?v }").rows, (("v1",),)
        )
        self.assertEqual(
            model.query("SELECT ?v WHERE { ?s OPTIONAL ?v }").rows, (("v2",),)
        )


def union_model():
    """并集查询模型：knows 链 + 部分 likes；只有 carol 有 email。"""
    doc = {
        "classes": ["Person"],
        "properties": ["knows", "likes", "email"],
        "individuals": ["alice", "bob", "carol", "dave"],
        "triples": [
            {"subject": "alice", "predicate": "knows", "object": "bob"},
            {"subject": "bob", "predicate": "knows", "object": "carol"},
            {"subject": "carol", "predicate": "knows", "object": "dave"},
            {"subject": "bob", "predicate": "likes", "object": "tennis"},
            {"subject": "bob", "predicate": "likes", "object": "soccer"},
            {"subject": "carol", "predicate": "likes", "object": "music"},
            {"subject": "carol", "predicate": "email", "object": "c@example.com"},
        ],
        "rules": [],
    }
    return parse(doc)


class UnionTests(unittest.TestCase):
    def setUp(self):
        self.model = union_model()

    def test_basic_union_merges_branch_results(self):
        result = self.model.query(
            "SELECT ?s ?o WHERE { { ?s knows ?o } UNION { ?s likes ?o } }"
        )
        self.assertEqual(result.variables, ("?s", "?o"))
        self.assertEqual(
            result.rows,
            (
                ("alice", "bob"),
                ("bob", "carol"),
                ("bob", "soccer"),
                ("bob", "tennis"),
                ("carol", "dave"),
                ("carol", "music"),
            ),
        )

    def test_spec_example_with_filter_after_union(self):
        # FILTER 在所有分支合并完成后执行，可引用任一分支的变量
        result = self.model.query(
            "SELECT ?s ?o WHERE { { ?s knows ?o } UNION { ?s likes ?o }"
            " FILTER (BOUND(?o)) }"
        )
        self.assertEqual(
            result.rows,
            (
                ("alice", "bob"),
                ("bob", "carol"),
                ("bob", "soccer"),
                ("bob", "tennis"),
                ("carol", "dave"),
                ("carol", "music"),
            ),
        )

    def test_chained_union_associates_left_to_right(self):
        result = self.model.query(
            "SELECT ?s ?o WHERE { { ?s knows ?o } UNION { ?s likes ?o }"
            " UNION { ?s email ?o } }"
        )
        self.assertEqual(
            result.rows,
            (
                ("alice", "bob"),
                ("bob", "carol"),
                ("bob", "soccer"),
                ("bob", "tennis"),
                ("carol", "c@example.com"),
                ("carol", "dave"),
                ("carol", "music"),
            ),
        )

    def test_branch_with_multiple_patterns_and_dots(self):
        result = self.model.query(
            "SELECT ?s ?o WHERE { { ?s knows ?x . ?x likes ?o . }"
            " UNION { ?s likes ?o } }"
        )
        self.assertEqual(
            result.rows,
            (
                ("alice", "soccer"),
                ("alice", "tennis"),
                ("bob", "music"),
                ("bob", "soccer"),
                ("bob", "tennis"),
                ("carol", "music"),
            ),
        )

    def test_branch_without_match_falls_back_to_other_branch(self):
        result = self.model.query(
            "SELECT ?s ?o WHERE { { ?s email ?o } UNION { ?s likes ?o } }"
        )
        self.assertEqual(
            result.rows,
            (
                ("bob", "soccer"),
                ("bob", "tennis"),
                ("carol", "c@example.com"),
                ("carol", "music"),
            ),
        )

    def test_all_branches_without_match_returns_empty_rows(self):
        result = self.model.query(
            "SELECT ?s WHERE { { ?s knows ?s } UNION { ?s likes ?s } }"
        )
        self.assertEqual(result.variables, ("?s",))
        self.assertEqual(result.rows, ())

    def test_variable_bound_in_only_one_branch_stays_none_elsewhere(self):
        result = self.model.query(
            "SELECT ?s ?o ?e WHERE { { ?s knows ?o } UNION { ?s email ?e } }"
        )
        self.assertEqual(
            result.rows,
            (
                ("alice", "bob", None),
                ("bob", "carol", None),
                ("carol", None, "c@example.com"),
                ("carol", "dave", None),
            ),
        )

    def test_shared_variable_must_agree_with_outer_binding(self):
        # 前置 BGP 把 ?x 绑定为 carol 的知道者，分支解须与之一致才能合并
        result = self.model.query(
            "SELECT ?x ?o WHERE { ?x knows carol ."
            " { ?x likes ?o } UNION { ?x knows ?o } }"
        )
        self.assertEqual(
            result.rows,
            (("bob", "carol"), ("bob", "soccer"), ("bob", "tennis")),
        )

    def test_star_projection_covers_union_branches_in_order(self):
        result = self.model.query(
            "SELECT * WHERE { { ?s knows ?o } UNION { ?o likes ?s } }"
        )
        self.assertEqual(result.variables, ("?s", "?o"))
        self.assertEqual(
            result.rows,
            (
                ("alice", "bob"),
                ("bob", "carol"),
                ("carol", "dave"),
                ("music", "carol"),
                ("soccer", "bob"),
                ("tennis", "bob"),
            ),
        )

    def test_union_combines_with_optional_and_filter(self):
        # carol 有 email，OPTIONAL 绑定 ?e 后被 FILTER(!BOUND(?e)) 过滤
        result = self.model.query(
            "SELECT * WHERE { { ?s knows ?o } UNION { ?s likes ?o } ."
            " OPTIONAL { ?s email ?e } FILTER(!BOUND(?e)) }"
        )
        self.assertEqual(result.variables, ("?s", "?o", "?e"))
        self.assertEqual(
            result.rows,
            (
                ("alice", "bob", None),
                ("bob", "carol", None),
                ("bob", "soccer", None),
                ("bob", "tennis", None),
            ),
        )

    def test_union_with_derived_triples(self):
        doc = make_doc(
            rules=[
                {
                    "id": "r1",
                    "if": [{"subject": "?x", "predicate": "knows", "object": "?y"}],
                    "then": [{"subject": "?x", "predicate": "likes", "object": "?y"}],
                }
            ]
        )
        model = parse(doc)
        # likes 全部由规则推出，与显式 knows 取并集
        result = model.query(
            "SELECT ?s ?o WHERE { { ?s knows ?o } UNION { ?s likes ?o } }"
        )
        self.assertEqual(
            result.rows,
            (("alice", "bob"), ("bob", "carol")),
        )

    def test_union_result_is_deduped_sorted_and_immutable(self):
        result = self.model.query(
            "SELECT ?o WHERE { { ?s knows ?o } UNION { ?s likes ?o } }"
        )
        self.assertEqual(
            result.rows,
            (("bob",), ("carol",), ("dave",), ("music",), ("soccer",), ("tennis",)),
        )
        self.assertIsInstance(result.rows, tuple)
        self.assertIsInstance(result.rows[0], tuple)

    def test_union_query_is_repeatable(self):
        text = (
            "SELECT ?s ?o WHERE { { ?s knows ?o } UNION { ?s likes ?o } }"
        )
        self.assertEqual(self.model.query(text), self.model.query(text))

    def test_union_query_does_not_mutate_model(self):
        before = (
            self.model.explicit_triples,
            self.model.derived_triples,
            self.model.triples,
        )
        self.model.query(
            "SELECT ?s ?o WHERE { { ?s knows ?o } UNION { ?s likes ?o } }"
        )
        self.assertEqual(before[0], self.model.explicit_triples)
        self.assertEqual(before[1], self.model.derived_triples)
        self.assertEqual(before[2], self.model.triples)


class UnionSyntaxErrorTests(unittest.TestCase):
    def setUp(self):
        self.model = union_model()

    def assertQueryError(self, text, *fragments):
        with self.assertRaises(OntologyError) as ctx:
            self.model.query(text)
        message = str(ctx.exception)
        self.assertTrue(
            "字符位置" in message or "分支" in message or "模式" in message,
            f"错误消息缺少字符位置或分支序号: {message}",
        )
        for fragment in fragments:
            self.assertIn(fragment, message)
        return message

    def test_adjacent_groups_without_union_rejected(self):
        self.assertQueryError(
            "SELECT ?s ?o WHERE { { ?s knows ?o } { ?s likes ?o } }",
            "UNION",
        )
        self.assertQueryError(
            "SELECT ?s ?o WHERE { { ?s knows ?o } }",
            "UNION",
        )

    def test_union_missing_brace_group(self):
        self.assertQueryError(
            "SELECT ?s ?o WHERE { { ?s knows ?o } UNION ?s likes ?o }",
            "'{'",
        )
        self.assertQueryError(
            "SELECT ?s ?o WHERE { { ?s knows ?o } UNION",
            "'{'",
        )
        self.assertQueryError(
            "SELECT ?s ?o WHERE { UNION { ?s likes ?o } }",
            "UNION",
        )

    def test_empty_branch_rejected(self):
        msg = self.assertQueryError(
            "SELECT ?s ?o WHERE { { } UNION { ?s likes ?o } }",
            "分支 1",
            "空",
        )
        self.assertIn("三元组模式", msg)
        self.assertQueryError(
            "SELECT ?s ?o WHERE { { ?s knows ?o } UNION { } }",
            "分支 2",
            "空",
        )

    def test_nested_union_rejected(self):
        self.assertQueryError(
            "SELECT ?s ?o WHERE {"
            " { ?s knows ?o . UNION { ?s likes ?o } } UNION { ?s likes ?o } }",
            "分支 1",
            "UNION",
        )
        self.assertQueryError(
            "SELECT ?s ?o WHERE {"
            " { { ?s knows ?o } UNION { ?s likes ?o } } UNION { ?s likes ?o } }",
            "分支 1",
        )

    def test_optional_or_filter_inside_branch_rejected(self):
        self.assertQueryError(
            "SELECT ?s ?o WHERE {"
            " { OPTIONAL { ?s knows ?o } } UNION { ?s likes ?o } }",
            "分支 1",
            "OPTIONAL",
        )
        self.assertQueryError(
            "SELECT ?s ?o WHERE {"
            " { ?s knows ?o . FILTER(BOUND(?o)) } UNION { ?s likes ?o } }",
            "分支 1",
            "FILTER",
        )

    def test_branch_missing_closing_brace(self):
        self.assertQueryError(
            "SELECT ?s ?o WHERE { { ?s knows ?o . UNION { ?s likes ?o } }"
        )

    def test_projection_variable_must_appear_in_some_branch(self):
        self.assertQueryError(
            "SELECT ?s ?z WHERE { { ?s knows ?o } UNION { ?s likes ?o } }",
            "?z",
        )

    def test_branch_triple_validation_matches_baseline(self):
        self.assertQueryError(
            "SELECT ?s ?o WHERE { { ?s ?p ?o } UNION { ?s likes ?o } }",
            "谓语",
        )
        self.assertQueryError(
            "SELECT ?s ?o WHERE { { ?s knows } UNION { ?s likes ?o } }",
            "项数不足",
        )
        self.assertQueryError(
            "SELECT ?s ?o WHERE { { ?s knows ?o likes ?x } UNION { ?s likes ?o } }",
            "项数过多",
        )
        self.assertQueryError(
            "SELECT ?s ?o WHERE { { . } UNION { ?s likes ?o } }",
            "'.'",
        )

    def test_lowercase_union_is_not_a_keyword(self):
        self.assertQueryError(
            "SELECT ?s ?o WHERE { { ?s knows ?o } union { ?s likes ?o } }"
        )

    def test_union_group_inside_optional_rejected(self):
        self.assertQueryError(
            "SELECT ?s ?o WHERE {"
            " OPTIONAL { { ?s knows ?o } UNION { ?s likes ?o } } }"
        )

    def test_union_keyword_as_constant_still_works(self):
        # 模式中间（非子句边界）的 UNION 仍是普通常量名
        doc = make_doc(
            properties=["knows"],
            triples=[{"subject": "alice", "predicate": "knows", "object": "UNION"}],
        )
        model = parse(doc)
        self.assertEqual(
            model.query("SELECT ?s WHERE { ?s knows UNION }").rows,
            (("alice",),),
        )


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


class DiagnoseApiTests(unittest.TestCase):
    """OntologyEngine.diagnose 与 ValidationReport 的结构化诊断行为。"""

    def diagnose(self, doc):
        return OntologyEngine().diagnose(json.dumps(doc))

    def test_consistent_report_holds_inferred_model(self):
        rule = {
            "id": "knows-likes",
            "if": [{"subject": "?x", "predicate": "knows", "object": "?y"}],
            "then": [{"subject": "?x", "predicate": "likes", "object": "?y"}],
        }
        doc = make_doc(rules=[rule])
        report = self.diagnose(doc)
        self.assertIsInstance(report, ValidationReport)
        self.assertTrue(report.is_consistent)
        self.assertIsInstance(report.model, type(parse(doc)))
        self.assertEqual(report.model.explicit_triples, parse(doc).explicit_triples)
        self.assertEqual(report.model.derived_triples, parse(doc).derived_triples)
        self.assertEqual(report.model.triples, parse(doc).triples)
        self.assertIsInstance(report.diagnostics, tuple)
        self.assertEqual(report.diagnostics, ())
        self.assertEqual(len(report), 0)
        self.assertEqual(list(report), [])
        # 报告内模型的查询语义与 parse 结果一致
        self.assertEqual(
            report.model.query("SELECT ?x ?y WHERE { ?x knows ?y }").rows,
            parse(doc).query("SELECT ?x ?y WHERE { ?x knows ?y }").rows,
        )

    def test_consistency_section_without_conflict_still_consistent(self):
        section = consistency_section(
            disjoint=[{"id": "d1", "classes": ["Person", "Robot"]}],
            functional=[{"id": "f1", "property": "age"}],
        )
        report = self.diagnose(consistency_doc(consistency=section))
        self.assertTrue(report.is_consistent)
        self.assertIsNotNone(report.model)
        self.assertEqual(report.diagnostics, ())

    def test_inconsistent_report_shape(self):
        triples = [
            {"subject": "alice", "predicate": "rdfType", "object": "Person"},
            {"subject": "alice", "predicate": "rdfType", "object": "Robot"},
        ]
        section = consistency_section(
            disjoint=[{"id": "d1", "classes": ["Robot", "Person"]}]
        )
        report = self.diagnose(consistency_doc(triples=triples, consistency=section))
        self.assertFalse(report.is_consistent)
        self.assertIsNone(report.model)
        self.assertEqual(len(report), 1)
        self.assertEqual(len(report.diagnostics), 1)
        diagnostic = report.diagnostics[0]
        self.assertEqual(
            set(diagnostic),
            {"kind", "constraintId", "subject", "evidence", "message"},
        )
        self.assertEqual(diagnostic["kind"], "disjointClassMembership")
        self.assertEqual(diagnostic["constraintId"], "d1")
        self.assertEqual(diagnostic["subject"], "alice")
        evidence = diagnostic["evidence"]
        self.assertIsInstance(evidence, tuple)
        self.assertEqual(len(evidence), 2)
        for item in evidence:
            self.assertEqual(set(item), {"class", "source"})
        # 类名按字典序：Person 在 Robot 之前
        self.assertEqual([item["class"] for item in evidence], ["Person", "Robot"])
        for item in evidence:
            self.assertEqual(item["source"], {"kind": "explicit"})

    def test_disjoint_derived_source_carries_rule_id(self):
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
        report = self.diagnose(
            consistency_doc(triples=triples, rules=rules, consistency=section)
        )
        (diagnostic,) = report.diagnostics
        # constraintId 保留原始整数
        self.assertEqual(diagnostic["constraintId"], 9)
        self.assertIsInstance(diagnostic["constraintId"], int)
        by_class = {item["class"]: item["source"] for item in diagnostic["evidence"]}
        self.assertEqual(by_class["Person"], {"kind": "explicit"})
        self.assertEqual(
            by_class["Robot"], {"kind": "derived", "ruleId": "r-to-robot"}
        )

    def test_functional_diagnostic_shape_and_sorting(self):
        triples = [
            {"subject": "alice", "predicate": "age", "object": "30"},
            {"subject": "alice", "predicate": "age", "object": "31"},
        ]
        section = consistency_section(
            functional=[{"id": "f-age", "property": "age"}]
        )
        report = self.diagnose(consistency_doc(triples=triples, consistency=section))
        (diagnostic,) = report.diagnostics
        self.assertEqual(
            set(diagnostic),
            {"kind", "constraintId", "subject", "evidence", "message"},
        )
        self.assertEqual(diagnostic["kind"], "functionalPropertyValue")
        self.assertEqual(diagnostic["constraintId"], "f-age")
        self.assertEqual(diagnostic["subject"], "alice")
        evidence = diagnostic["evidence"]
        self.assertEqual(len(evidence), 2)
        for item in evidence:
            self.assertEqual(set(item), {"object", "source"})
        # object 按字典序
        self.assertEqual([item["object"] for item in evidence], ["30", "31"])

    def test_functional_derived_source_keeps_integer_rule_id(self):
        triples = [
            {"subject": "bob", "predicate": "knows", "object": "carol"},
            {"subject": "bob", "predicate": "age", "object": "50"},
        ]
        rules = [
            {
                "id": 42,
                "if": [{"subject": "?x", "predicate": "knows", "object": "?y"}],
                "then": [{"subject": "?x", "predicate": "age", "object": "40"}],
            }
        ]
        section = consistency_section(
            functional=[{"id": "f-age", "property": "age"}]
        )
        report = self.diagnose(
            consistency_doc(triples=triples, rules=rules, consistency=section)
        )
        (diagnostic,) = report.diagnostics
        by_object = {item["object"]: item["source"] for item in diagnostic["evidence"]}
        self.assertEqual(by_object["40"], {"kind": "derived", "ruleId": 42})
        self.assertIsInstance(by_object["40"]["ruleId"], int)
        self.assertEqual(by_object["50"], {"kind": "explicit"})

    def test_all_pairwise_conflicts_listed(self):
        triples = [
            {"subject": "alice", "predicate": "rdfType", "object": "Person"},
            {"subject": "alice", "predicate": "rdfType", "object": "Robot"},
            {"subject": "alice", "predicate": "rdfType", "object": "Animal"},
            {"subject": "alice", "predicate": "age", "object": "1"},
            {"subject": "alice", "predicate": "age", "object": "2"},
            {"subject": "alice", "predicate": "age", "object": "3"},
        ]
        section = consistency_section(
            disjoint=[{"id": "d1", "classes": ["Animal", "Person", "Robot"]}],
            functional=[{"id": "f1", "property": "age"}],
        )
        report = self.diagnose(consistency_doc(triples=triples, consistency=section))
        kinds = [d["kind"] for d in report.diagnostics]
        self.assertEqual(kinds.count("disjointClassMembership"), 3)
        self.assertEqual(kinds.count("functionalPropertyValue"), 3)
        # 每条互斥诊断的类对唯一且按字典序
        class_pairs = [
            tuple(item["class"] for item in d["evidence"])
            for d in report.diagnostics
            if d["kind"] == "disjointClassMembership"
        ]
        self.assertEqual(
            sorted(class_pairs),
            [("Animal", "Person"), ("Animal", "Robot"), ("Person", "Robot")],
        )
        object_pairs = [
            tuple(item["object"] for item in d["evidence"])
            for d in report.diagnostics
            if d["kind"] == "functionalPropertyValue"
        ]
        self.assertEqual(
            sorted(object_pairs), [("1", "2"), ("1", "3"), ("2", "3")]
        )

    def test_messages_and_order_match_parse_error_lines(self):
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
        doc = consistency_doc(triples=triples, consistency=section)
        text = json.dumps(doc)
        report = OntologyEngine().diagnose(text)
        with self.assertRaises(InconsistencyError) as ctx:
            OntologyEngine().parse(text)
        lines = str(ctx.exception).splitlines()
        self.assertEqual(lines[0], f"本体一致性诊断发现 {len(report.diagnostics)} 处冲突：")
        # 每条诊断 message 与 parse 冲突行（正文）逐字一致，顺序也一致
        self.assertEqual(
            [d["message"] for d in report.diagnostics], lines[1:]
        )

    def test_repeated_diagnose_gives_identical_report(self):
        triples = [
            {"subject": "alice", "predicate": "rdfType", "object": "Robot"},
            {"subject": "alice", "predicate": "rdfType", "object": "Person"},
            {"subject": "alice", "predicate": "age", "object": "9"},
            {"subject": "alice", "predicate": "age", "object": "1"},
        ]
        section = consistency_section(
            disjoint=[{"id": "d1", "classes": ["Person", "Robot"]}],
            functional=[{"id": "f1", "property": "age"}],
        )
        doc = consistency_doc(triples=triples, consistency=section)
        text = json.dumps(doc)
        first = OntologyEngine().diagnose(text)
        for _ in range(3):
            again = OntologyEngine().diagnose(text)
            self.assertEqual(again, first)
            self.assertEqual(again.diagnostics, first.diagnostics)
        # JSON 键顺序与 triples 顺序变化不影响报告
        shuffled = {
            "functionalProperties": None,
            "rules": doc["rules"],
            "triples": list(reversed(doc["triples"])),
            "individuals": doc["individuals"],
            "properties": doc["properties"],
            "classes": doc["classes"],
        }
        del shuffled["functionalProperties"]
        shuffled["consistency"] = {
            "functionalProperties": section["functionalProperties"],
            "classMembershipPredicate": "rdfType",
            "disjointClasses": section["disjointClasses"],
        }
        self.assertEqual(
            OntologyEngine().diagnose(json.dumps(shuffled)).diagnostics,
            first.diagnostics,
        )

    def test_diagnostics_copies_are_isolated(self):
        triples = [
            {"subject": "alice", "predicate": "rdfType", "object": "Person"},
            {"subject": "alice", "predicate": "rdfType", "object": "Robot"},
        ]
        section = consistency_section(
            disjoint=[{"id": "d1", "classes": ["Person", "Robot"]}]
        )
        report = self.diagnose(consistency_doc(triples=triples, consistency=section))
        snapshot = report.diagnostics
        mutated = report.diagnostics
        mutated[0]["subject"] = "hacker"
        mutated[0]["evidence"][0]["class"] = "Hack"
        mutated[0]["evidence"][0]["source"]["kind"] = "made-up"
        self.assertEqual(report.diagnostics, snapshot)
        # 元组本身不可变
        with self.assertRaises(TypeError):
            report.diagnostics[0] = {}

    def test_diagnose_accepts_bytes_and_reuses_parse_errors(self):
        doc = make_doc()
        # UTF-8 bytes 与 str 结果一致
        self.assertEqual(
            OntologyEngine().diagnose(json.dumps(doc).encode("utf-8")),
            OntologyEngine().diagnose(json.dumps(doc)),
        )

        def parse_error(text):
            with self.assertRaises(OntologyError) as ctx:
                OntologyEngine().parse(text)
            return str(ctx.exception)

        def diagnose_error(text):
            with self.assertRaises(OntologyError) as ctx:
                OntologyEngine().diagnose(text)
            return str(ctx.exception)

        # 非 UTF-8、非法 JSON、错误类型与结构非法：diagnose 与 parse 抛出相同消息
        self.assertEqual(diagnose_error(b"\xff\xfe{}"), parse_error(b"\xff\xfe{}"))
        self.assertEqual(diagnose_error("{not json"), parse_error("{not json"))
        self.assertEqual(diagnose_error(123), parse_error(123))
        bad_shape = make_doc(triples=[{"subject": "alice", "predicate": "knows"}])
        self.assertEqual(
            diagnose_error(json.dumps(bad_shape)),
            parse_error(json.dumps(bad_shape)),
        )
        bad_consistency = consistency_doc(
            consistency={"classMembershipPredicate": "rdfType", "disjointClasses": []}
        )
        self.assertEqual(
            diagnose_error(json.dumps(bad_consistency)),
            parse_error(json.dumps(bad_consistency)),
        )

    def test_diagnose_does_not_change_model_outputs(self):
        rule = {
            "id": "knows-likes",
            "if": [{"subject": "?x", "predicate": "knows", "object": "?y"}],
            "then": [{"subject": "?x", "predicate": "likes", "object": "?y"}],
        }
        doc = make_doc(rules=[rule])
        text = json.dumps(doc)
        parsed = OntologyEngine().parse(text)
        reported = OntologyEngine().diagnose(text).model
        self.assertEqual(parsed.explicit_triples, reported.explicit_triples)
        self.assertEqual(parsed.derived_triples, reported.derived_triples)
        self.assertEqual(parsed.triples, reported.triples)
        query = "SELECT ?x ?y WHERE { ?x knows ?y . OPTIONAL { ?x likes ?y } }"
        self.assertEqual(parsed.query(query), reported.query(query))
        self.assertEqual(
            parsed.source_rule("alice", "likes", "bob"),
            reported.source_rule("alice", "likes", "bob"),
        )
        self.assertEqual(
            parsed.entails("alice", "likes", "bob"),
            reported.entails("alice", "likes", "bob"),
        )

    def test_engine_call_still_aliases_parse(self):
        doc = make_doc()
        self.assertEqual(
            OntologyEngine()(json.dumps(doc)).triples, parse(doc).triples
        )


def path_model():
    """属性路径模型：edge 链 a→b→c→d→b 成环；a --other--> x；b --label--> B。

    'iso' 已声明为个体但从不出现在任何三元组中，用于验证 p?/p* 的零次分支
    只覆盖实际出现过的节点。规则把每条 edge 推导为 invOnly，并由 other
    推导出一条 edge（a→x），使推理事实也参与路径求值。
    """
    doc = {
        "classes": ["Node"],
        "properties": ["edge", "other", "label", "invOnly"],
        "individuals": ["a", "b", "c", "d", "x", "iso"],
        "triples": [
            {"subject": "a", "predicate": "edge", "object": "b"},
            {"subject": "b", "predicate": "edge", "object": "c"},
            {"subject": "c", "predicate": "edge", "object": "d"},
            {"subject": "d", "predicate": "edge", "object": "b"},
            {"subject": "a", "predicate": "other", "object": "x"},
            {"subject": "b", "predicate": "label", "object": "B"},
        ],
        "rules": [
            {
                "id": "copy-edge",
                "if": [{"subject": "?s", "predicate": "edge", "object": "?o"}],
                "then": [
                    {"subject": "?s", "predicate": "invOnly", "object": "?o"}
                ],
            },
            {
                "id": "other-to-edge",
                "if": [{"subject": "?s", "predicate": "other", "object": "?o"}],
                "then": [{"subject": "?s", "predicate": "edge", "object": "?o"}],
            },
        ],
    }
    return parse(doc)


class PropertyPathTests(unittest.TestCase):
    def setUp(self):
        self.model = path_model()

    def assertRows(self, text, rows):
        result = self.model.query(text)
        self.assertEqual(result.rows, tuple(rows))
        return result

    def test_plain_name_is_one_edge_path(self):
        self.assertRows(
            "SELECT ?o WHERE { a edge ?o }",
            [("b",), ("x",)],  # a→b 显式，a→x 由 other-to-edge 推出
        )

    def test_inverse_path_constant_endpoints(self):
        # ?s ^edge b：沿 edge 指向 b 的节点只有 c；指向 a 的有 b 与 x
        self.assertRows("SELECT ?s WHERE { ?s ^edge b }", [("c",)])
        self.assertRows("SELECT ?s WHERE { ?s ^edge a }", [("b",), ("x",)])

    def test_inverse_path_with_two_variables(self):
        self.assertRows(
            "SELECT ?s ?o WHERE { ?s ^edge ?o }",
            [
                ("b", "a"),
                ("b", "d"),
                ("c", "b"),
                ("d", "c"),
                ("x", "a"),
            ],
        )

    def test_sequence_composition(self):
        self.assertRows("SELECT ?o WHERE { a edge/edge ?o }", [("c",)])
        self.assertRows("SELECT ?o WHERE { a edge/edge/edge ?o }", [("d",)])

    def test_alternation_is_union(self):
        self.assertRows(
            "SELECT ?o WHERE { a edge|other ?o }", [("b",), ("x",)]
        )

    def test_grouping_parentheses(self):
        # (edge|other)/label：a 经 edge 到 b、b 经 label 到 B
        self.assertRows("SELECT ?o WHERE { a (edge|other)/label ?o }", [("B",)])
        self.assertRows(
            "SELECT ?o WHERE { b (edge/edge)|label ?o }", [("B",), ("d",)]
        )

    def test_zero_or_one_includes_identity(self):
        result = self.assertRows(
            "SELECT ?s ?o WHERE { ?s edge? ?o }",
            [
                ("B", "B"),
                ("a", "a"),
                ("a", "b"),
                ("a", "x"),
                ("b", "b"),
                ("b", "c"),
                ("c", "c"),
                ("c", "d"),
                ("d", "b"),
                ("d", "d"),
                ("x", "x"),
            ],
        )
        # 零次分支只连接实际出现过的节点：iso 从未出现在三元组中
        subjects = {row[0] for row in result.rows}
        self.assertNotIn("iso", subjects)

    def test_zero_or_more_reflexive_transitive_closure(self):
        # a 经环可到 b,c,d，再由派生边到 x，并含自身 a；不到 B、不到 iso
        self.assertRows(
            "SELECT ?o WHERE { a edge* ?o }",
            [("a",), ("b",), ("c",), ("d",), ("x",)],
        )

    def test_one_or_more_excludes_zero_step(self):
        # a 无法经正数步回到自身（a 不在环上），故结果不含 a
        self.assertRows(
            "SELECT ?o WHERE { a edge+ ?o }",
            [("b",), ("c",), ("d",), ("x",)],
        )

    def test_positive_closure_reaches_self_on_cycle(self):
        # 环 b→c→d→b 上的节点经正数步回到自身；a、x、B 不在环上
        self.assertRows(
            "SELECT ?x WHERE { ?x edge+ ?x }", [("b",), ("c",), ("d",)]
        )

    def test_star_same_variable_binds_appearing_nodes_only(self):
        result = self.model.query("SELECT ?x WHERE { ?x edge* ?x }")
        self.assertEqual(
            result.rows,
            (("B",), ("a",), ("b",), ("c",), ("d",), ("x",)),
        )

    def test_star_closure_terminates_on_cycle(self):
        # 重复执行且结果有限即说明环状数据上闭包终止
        text = "SELECT ?s ?o WHERE { ?s edge* ?o }"
        first = self.model.query(text)
        self.assertEqual(first, self.model.query(text))
        self.assertEqual(len(first.rows), len(set(first.rows)))

    def test_inverse_uses_derived_facts(self):
        # invOnly 全部为推理结论，逆向路径同样能命中
        self.assertRows("SELECT ?s WHERE { ?s ^invOnly a }", [("b",), ("x",)])

    def test_path_with_constant_subject_and_object(self):
        self.assertRows("SELECT * WHERE { a edge/edge c }", [()])
        self.assertEqual(len(self.model.query("SELECT * WHERE { a edge/edge x }")), 0)

    def test_path_in_optional_left_join(self):
        text = "SELECT ?x ?y WHERE { ?x edge b . OPTIONAL { ?x edge+ ?y } }"
        result = self.model.query(text)
        # ?x = a（a→b）与 d（d→b）；二者闭包沿环展开
        self.assertEqual(result.variables, ("?x", "?y"))
        self.assertEqual(
            result.rows,
            (
                ("a", "b"),
                ("a", "c"),
                ("a", "d"),
                ("a", "x"),
                ("d", "b"),
                ("d", "c"),
                ("d", "d"),
            ),
        )
        self.assertEqual(result, self.model.query(text))

    def test_path_optional_no_match_keeps_none(self):
        # x 是 a other x 的宾语，自身没有 edge 出边；经 ^other 取到 x 后
        # OPTIONAL 的 edge+ 无匹配，?y 保持未绑定
        result = self.model.query(
            "SELECT ?x ?y WHERE { ?x ^other a . OPTIONAL { ?x edge+ ?y } }"
        )
        self.assertEqual(result.rows, (("x", None),))

    def test_path_in_union_branches(self):
        result = self.model.query(
            "SELECT ?o WHERE { { a edge+ ?o } UNION { a other ?o } }"
        )
        self.assertEqual(result.rows, (("b",), ("c",), ("d",), ("x",)))

    def test_filter_keeps_semantics_with_path_bound_variable(self):
        result = self.model.query(
            'SELECT ?o WHERE { a edge* ?o . FILTER(?o = "c") }'
        )
        self.assertEqual(result.rows, (("c",),))
        result2 = self.model.query(
            "SELECT ?o WHERE { a edge* ?o . FILTER(!BOUND(?o)) }"
        )
        self.assertEqual(result2.rows, ())

    def test_star_projection_order_with_paths(self):
        result = self.model.query(
            "SELECT * WHERE { ?s edge/edge ?m . ?m ^edge ?t }"
        )
        self.assertEqual(result.variables, ("?s", "?m", "?t"))

    def test_empty_result_keeps_projection_columns(self):
        # label 只指向 B，而 B 没有任何 edge 出边，复合无匹配
        result = self.model.query("SELECT ?x ?y WHERE { ?x label/edge ?y }")
        self.assertEqual(result.variables, ("?x", "?y"))
        self.assertEqual(result.rows, ())

    def test_rows_deduped_sorted_none_first(self):
        result = self.model.query(
            "SELECT ?b ?l WHERE { ?a edge ?b . OPTIONAL { ?b label ?l } }"
        )
        # 先按投影首列 ?b 排序；只有 b 有 label B，c/d/x 的 ?l 为 None
        self.assertEqual(
            result.rows,
            (("b", "B"), ("c", None), ("d", None), ("x", None)),
        )

    def test_path_query_repeatable_and_does_not_mutate(self):
        text = "SELECT ?s ?o WHERE { ?s (edge|other)+ ?o }"
        before = (
            self.model.explicit_triples,
            self.model.derived_triples,
            self.model.triples,
        )
        first = self.model.query(text)
        for _ in range(3):
            self.assertEqual(self.model.query(text), first)
        self.assertEqual(before, (
            self.model.explicit_triples,
            self.model.derived_triples,
            self.model.triples,
        ))

    def test_whitespace_around_operators(self):
        self.assertEqual(
            self.model.query("SELECT ?o WHERE { a edge / edge ?o }").rows,
            self.model.query("SELECT ?o WHERE { a edge/edge ?o }").rows,
        )
        self.assertEqual(
            self.model.query("SELECT ?o WHERE { a ( edge | other ) ?o }").rows,
            self.model.query("SELECT ?o WHERE { a edge|other ?o }").rows,
        )
        self.assertEqual(
            self.model.query("SELECT ?o WHERE { a ^ edge ?o }").rows,
            self.model.query("SELECT ?o WHERE { a ^edge ?o }").rows,
        )


class PropertyPathSyntaxErrorTests(unittest.TestCase):
    def setUp(self):
        self.model = path_model()

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
        return message

    def test_non_str_input_rejected(self):
        with self.assertRaises(OntologyError) as ctx:
            self.model.query(123)
        self.assertIn("str", str(ctx.exception))

    def test_variable_predicate_still_rejected(self):
        self.assertQueryError(
            "SELECT * WHERE { ?x ?p ?y }", "谓语", "模式 0"
        )

    def test_undeclared_property_in_path(self):
        self.assertQueryError(
            "SELECT ?y WHERE { a nope ?y }", "nope", "未声明", "模式 0"
        )
        self.assertQueryError(
            "SELECT ?y WHERE { a edge/nope ?y }", "nope", "未声明"
        )
        self.assertQueryError(
            "SELECT ?y WHERE { a (edge|nope) ?y }", "nope", "未声明"
        )
        self.assertQueryError(
            "SELECT ?y WHERE { a ^nope ?y }", "nope", "未声明"
        )

    def test_missing_operand_after_operator(self):
        self.assertQueryError("SELECT ?y WHERE { a edge/ ?y }", "/")
        self.assertQueryError("SELECT ?y WHERE { a edge| ?y }", "|")
        self.assertQueryError("SELECT ?y WHERE { a ^ ?y }", "^")
        self.assertQueryError("SELECT ?y WHERE { a edge?/ ?y }", "/")

    def test_missing_operand_before_operator(self):
        self.assertQueryError("SELECT ?y WHERE { a /edge ?y }", "/")
        self.assertQueryError("SELECT ?y WHERE { a |edge ?y }", "|")

    def test_quantifier_without_atom(self):
        self.assertQueryError("SELECT ?y WHERE { a * ?y }", "*")
        self.assertQueryError("SELECT ?y WHERE { a + ?y }", "+")

    def test_unbalanced_parentheses(self):
        self.assertQueryError(
            "SELECT ?y WHERE { a (edge/edge ?y }", "圆括号", "模式 0"
        )
        self.assertQueryError(
            "SELECT ?y WHERE { a edge) ?y }", "项数过多"
        )

    def test_empty_parenthesized_group(self):
        self.assertQueryError("SELECT ?y WHERE { a () ?y }", "圆括号")

    def test_stacked_quantifiers_rejected(self):
        self.assertQueryError("SELECT ?y WHERE { a edge** ?y }", "量词")
        self.assertQueryError("SELECT ?y WHERE { a edge?+ ?y }", "量词")
        self.assertQueryError("SELECT ?y WHERE { a edge++ ?y }", "量词")

    def test_incomplete_path_at_end(self):
        self.assertQueryError("SELECT ?y WHERE { a edge/", "/")
        self.assertQueryError("SELECT ?y WHERE { a edge|", "|")

    def test_path_inside_filter_is_not_expression(self):
        # FILTER 不把路径当作比较操作数
        self.assertQueryError(
            "SELECT ?x WHERE { ?x edge b . FILTER(edge/edge = b) }",
            "FILTER",
        )


class PropertyPathBackwardCompatTests(unittest.TestCase):
    def test_subject_object_names_with_path_chars_stay_single(self):
        doc = {
            "classes": ["N"],
            "properties": ["p"],
            "individuals": ["x/y", "u+v", "w|z", "a^b"],
            "triples": [
                {"subject": "x/y", "predicate": "p", "object": "u+v"},
                {"subject": "w|z", "predicate": "p", "object": "a^b"},
            ],
            "rules": [],
        }
        model = parse(doc)
        self.assertEqual(
            model.query("SELECT ?s ?o WHERE { ?s p ?o }").rows,
            (("w|z", "a^b"), ("x/y", "u+v")),
        )

    def test_predicate_name_containing_path_chars_matches_as_constant(self):
        doc = {
            "classes": ["N"],
            "properties": ["a/b", "x(y)"],
            "individuals": ["s"],
            "triples": [
                {"subject": "s", "predicate": "a/b", "object": "o1"},
                {"subject": "s", "predicate": "x(y)", "object": "o2"},
            ],
            "rules": [],
        }
        model = parse(doc)
        self.assertEqual(
            model.query("SELECT ?o WHERE { s a/b ?o }").rows, (("o1",),)
        )
        self.assertEqual(
            model.query("SELECT ?o WHERE { s x(y) ?o }").rows, (("o2",),)
        )

    def test_glued_question_variable_object_is_legacy_lexing(self):
        doc = make_doc()
        model = parse(doc)
        # p?y 旧词法：谓语 p（常量）+ 宾语变量 ?y，不是路径量词
        self.assertEqual(
            model.query("SELECT ?y WHERE { alice knows?y }").rows,
            model.query("SELECT ?y WHERE { alice knows ?y }").rows,
        )


class ExplainTests(unittest.TestCase):
    """OntologyModel.explain 的规则证明行为。"""

    def test_explicit_fact_has_single_explicit_proof(self):
        model = parse(make_doc())
        (proof,) = model.explain("alice", "knows", "bob")
        self.assertEqual(proof.kind, "explicit")
        self.assertEqual(proof.triple, Triple("alice", "knows", "bob"))
        self.assertIsInstance(proof.triple, Triple)
        self.assertIsNone(proof.ruleId)
        self.assertEqual(proof.premises, ())

    def test_unentailed_triple_returns_empty_tuple(self):
        model = parse(make_doc())
        self.assertEqual(model.explain("alice", "likes", "bob"), ())
        self.assertEqual(model.explain("nobody", "knows", "bob"), ())

    def test_non_string_arguments_raise_with_position(self):
        model = parse(make_doc())
        for args, position in (
            ((1, "knows", "bob"), "1"),
            (("alice", 2, "bob"), "2"),
            (("alice", "knows", None), "3"),
        ):
            with self.assertRaises(OntologyError) as ctx:
                model.explain(*args)
            self.assertIn(position, str(ctx.exception))

    def test_rule_proof_traces_to_explicit_leaves_in_if_order(self):
        rule = {
            "id": "fof",
            "if": [
                {"subject": "?x", "predicate": "knows", "object": "?y"},
                {"subject": "?y", "predicate": "knows", "object": "?z"},
            ],
            "then": [{"subject": "?x", "predicate": "friendOf", "object": "?z"}],
        }
        model = parse(make_doc(rules=[rule]))
        (proof,) = model.explain("alice", "friendOf", "carol")
        self.assertEqual(proof.kind, "rule")
        self.assertEqual(proof.ruleId, "fof")
        self.assertEqual(
            [premise.triple for premise in proof.premises],
            [Triple("alice", "knows", "bob"), Triple("bob", "knows", "carol")],
        )
        self.assertTrue(all(p.kind == "explicit" for p in proof.premises))

    def test_multiple_rules_all_shortest_proofs_sorted(self):
        rules = [
            {
                "id": name,
                "if": [{"subject": "?x", "predicate": "knows", "object": "?y"}],
                "then": [{"subject": "?x", "predicate": "likes", "object": "?y"}],
            }
            for name in ("r2", "r1")
        ]
        model = parse(make_doc(rules=rules))
        proofs = model.explain("alice", "likes", "bob")
        self.assertEqual([p.ruleId for p in proofs], ["r1", "r2"])
        # 重复调用返回内容相同的元组
        self.assertEqual(model.explain("alice", "likes", "bob"), proofs)

    def test_rule_id_keeps_original_type_and_string_sort_order(self):
        rules = [
            {
                "id": rule_id,
                "if": [{"subject": "?x", "predicate": "knows", "object": "?y"}],
                "then": [{"subject": "?x", "predicate": "likes", "object": "?y"}],
            }
            for rule_id in ("2", 10)
        ]
        model = parse(make_doc(rules=rules))
        proofs = model.explain("alice", "likes", "bob")
        self.assertEqual([p.ruleId for p in proofs], [10, "2"])
        self.assertIsInstance(proofs[0].ruleId, int)

    def test_explicit_wins_over_derivation(self):
        rule = {
            "id": "r1",
            "if": [{"subject": "?x", "predicate": "knows", "object": "?y"}],
            "then": [{"subject": "?x", "predicate": "knows", "object": "?y"}],
        }
        model = parse(make_doc(rules=[rule]))
        (proof,) = model.explain("alice", "knows", "bob")
        self.assertEqual(proof.kind, "explicit")

    def test_cyclic_rules_terminate_without_cyclic_proofs(self):
        rules = [
            {
                "id": "p2q",
                "if": [{"subject": "?x", "predicate": "knows", "object": "?y"}],
                "then": [{"subject": "?x", "predicate": "likes", "object": "?y"}],
            },
            {
                "id": "q2p",
                "if": [{"subject": "?x", "predicate": "likes", "object": "?y"}],
                "then": [{"subject": "?x", "predicate": "knows", "object": "?y"}],
            },
        ]
        model = parse(make_doc(rules=rules))
        (proof,) = model.explain("alice", "likes", "bob")
        self.assertEqual(proof.ruleId, "p2q")
        self.assertEqual([p.kind for p in proof.premises], ["explicit"])

    def test_multiple_then_patterns_all_kept(self):
        rule = {
            "id": "r",
            "if": [{"subject": "?x", "predicate": "knows", "object": "?y"}],
            "then": [
                {"subject": "?x", "predicate": "likes", "object": "?y"},
                {"subject": "?x", "predicate": "friendOf", "object": "?y"},
            ],
        }
        model = parse(make_doc(rules=[rule]))
        self.assertEqual(len(model.explain("bob", "friendOf", "carol")), 1)
        self.assertEqual(len(model.explain("bob", "likes", "carol")), 1)

    def test_only_minimal_leaf_proofs_returned(self):
        doc = {
            "classes": [],
            "properties": ["p", "q"],
            "individuals": [],
            "triples": [
                {"subject": "a", "predicate": "p", "object": "b"},
                {"subject": "b", "predicate": "p", "object": "c"},
            ],
            "rules": [
                {
                    "id": "short",
                    "if": [{"subject": "?x", "predicate": "p", "object": "?y"}],
                    "then": [{"subject": "?x", "predicate": "q", "object": "?y"}],
                },
                {
                    "id": "long",
                    "if": [
                        {"subject": "?x", "predicate": "p", "object": "?y"},
                        {"subject": "?y", "predicate": "q", "object": "?z"},
                    ],
                    "then": [{"subject": "?x", "predicate": "q", "object": "?z"}],
                },
            ],
        }
        model = parse(doc)
        # a-q-c 只能经 long 推出（2 个显式叶子）；b-q-c 走 short（1 叶）
        (proof,) = model.explain("a", "q", "c")
        self.assertEqual(proof.ruleId, "long")
        (direct,) = model.explain("b", "q", "c")
        self.assertEqual(direct.ruleId, "short")
        self.assertEqual([p.kind for p in direct.premises], ["explicit"])

    def test_transitive_closure_yields_all_shortest_proofs(self):
        doc = {
            "classes": [],
            "properties": ["k"],
            "individuals": [],
            "triples": [
                {"subject": "a", "predicate": "k", "object": "b"},
                {"subject": "b", "predicate": "k", "object": "c"},
                {"subject": "c", "predicate": "k", "object": "d"},
            ],
            "rules": [
                {
                    "id": "t",
                    "if": [
                        {"subject": "?x", "predicate": "k", "object": "?y"},
                        {"subject": "?y", "predicate": "k", "object": "?z"},
                    ],
                    "then": [{"subject": "?x", "predicate": "k", "object": "?z"}],
                }
            ],
        }
        model = parse(doc)
        proofs = model.explain("a", "k", "d")
        self.assertEqual(len(proofs), 2)
        for proof in proofs:
            leaves = []

            def walk(node):
                if node.kind == "explicit":
                    leaves.append(node.triple)
                for premise in node.premises:
                    walk(premise)

            walk(proof)
            self.assertEqual(len(leaves), 3)
        self.assertEqual(model.explain("a", "k", "d"), proofs)

    def test_duplicate_then_patterns_deduped(self):
        doc = {
            "classes": [],
            "properties": ["p", "q"],
            "individuals": [],
            "triples": [{"subject": "a", "predicate": "p", "object": "b"}],
            "rules": [
                {
                    "id": "r",
                    "if": [{"subject": "?x", "predicate": "p", "object": "?y"}],
                    "then": [
                        {"subject": "?x", "predicate": "q", "object": "?y"},
                        {"subject": "?x", "predicate": "q", "object": "?y"},
                    ],
                }
            ],
        }
        model = parse(doc)
        self.assertEqual(len(model.explain("a", "q", "b")), 1)

    def test_empty_if_ground_then_proof_has_no_premises(self):
        rule = {
            "id": "fact",
            "if": [],
            "then": [{"subject": "carol", "predicate": "likes", "object": "alice"}],
        }
        model = parse(make_doc(rules=[rule]))
        (proof,) = model.explain("carol", "likes", "alice")
        self.assertEqual(proof.kind, "rule")
        self.assertEqual(proof.premises, ())

    def test_proof_is_read_only_snapshot(self):
        rule = {
            "id": "r1",
            "if": [{"subject": "?x", "predicate": "knows", "object": "?y"}],
            "then": [{"subject": "?x", "predicate": "likes", "object": "?y"}],
        }
        model = parse(make_doc(rules=[rule]))
        (proof,) = model.explain("alice", "likes", "bob")
        self.assertIsInstance(proof.premises, tuple)
        for attr, value in (
            ("kind", "x"),
            ("triple", None),
            ("ruleId", "y"),
            ("premises", ()),
        ):
            with self.assertRaises(AttributeError):
                setattr(proof, attr, value)
        # explain 不改变模型的其它读取结果
        before = (model.explicit_triples, model.derived_triples, model.triples)
        model.explain("alice", "likes", "bob")
        self.assertEqual(
            before, (model.explicit_triples, model.derived_triples, model.triples)
        )


def explain_doc(**overrides):
    """带成员谓语与类型声明的文档，供 explain_diagnostic 测试使用。"""
    return consistency_doc(
        classes=["Person", "Robot", "Animal"],
        properties=["rdfType", "knows", "likes", "age"],
        **overrides,
    )


def explain_section(disjoint=None, functional=None):
    return consistency_section(
        disjoint=disjoint, functional=functional, predicate="rdfType"
    )


def diagnose_json(doc):
    return OntologyEngine().diagnose(json.dumps(doc, ensure_ascii=False))


def iter_proof_nodes(proof):
    yield proof
    for premise in proof.premises:
        yield from iter_proof_nodes(premise)


class ExplainDiagnosticTests(unittest.TestCase):
    """ValidationReport.explain_diagnostic 的冲突事实追溯行为。"""

    def derived_disjoint_report(self):
        triples = [
            {"subject": "alice", "predicate": "rdfType", "object": "Person"},
            {"subject": "alice", "predicate": "knows", "object": "bob"},
        ]
        rules = [
            {
                "id": "r-to-robot",
                "if": [{"subject": "?x", "predicate": "knows", "object": "?y"}],
                "then": [
                    {"subject": "?x", "predicate": "rdfType", "object": "Robot"}
                ],
            }
        ]
        section = explain_section(
            disjoint=[{"id": 9, "classes": ["Person", "Robot"]}]
        )
        return diagnose_json(
            explain_doc(triples=triples, rules=rules, consistency=section)
        )

    def test_explicit_disjoint_facts_each_have_single_explicit_proof(self):
        triples = [
            {"subject": "alice", "predicate": "rdfType", "object": "Person"},
            {"subject": "alice", "predicate": "rdfType", "object": "Robot"},
        ]
        section = explain_section(
            disjoint=[{"id": "d1", "classes": ["Robot", "Person"]}]
        )
        report = diagnose_json(explain_doc(triples=triples, consistency=section))
        result = report.explain_diagnostic(0)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        # 顺序与 evidence 两项一致：Person 先、Robot 后
        classes = [item["class"] for item in report.diagnostics[0]["evidence"]]
        self.assertEqual(classes, ["Person", "Robot"])
        for proofs, class_name in zip(result, ("Person", "Robot")):
            self.assertIsInstance(proofs, tuple)
            self.assertEqual(len(proofs), 1)
            proof = proofs[0]
            self.assertEqual(proof.kind, "explicit")
            self.assertIsNone(proof.ruleId)
            self.assertEqual(proof.premises, ())
            self.assertEqual(
                proof.triple, Triple("alice", "rdfType", class_name)
            )
            self.assertIsInstance(proof.triple, Triple)

    def test_derived_fact_uses_full_rule_proof_in_evidence_order(self):
        report = self.derived_disjoint_report()
        first_proofs, second_proofs = report.explain_diagnostic(0)
        # 第一项：Person 为显式事实
        self.assertEqual(len(first_proofs), 1)
        explicit_proof = first_proofs[0]
        self.assertEqual(explicit_proof.kind, "explicit")
        self.assertEqual(
            explicit_proof.triple, Triple("alice", "rdfType", "Person")
        )
        # 第二项：Robot 为规则 r-to-robot 的推理结论
        self.assertEqual(len(second_proofs), 1)
        rule_proof = second_proofs[0]
        self.assertEqual(rule_proof.kind, "rule")
        self.assertEqual(rule_proof.ruleId, "r-to-robot")
        self.assertEqual(
            rule_proof.triple, Triple("alice", "rdfType", "Robot")
        )
        self.assertEqual(
            [premise.triple for premise in rule_proof.premises],
            [Triple("alice", "knows", "bob")],
        )
        self.assertTrue(all(p.kind == "explicit" for p in rule_proof.premises))

    def test_functional_evidence_triples_use_constraint_property(self):
        triples = [
            {"subject": "bob", "predicate": "knows", "object": "carol"},
            {"subject": "bob", "predicate": "age", "object": "50"},
        ]
        rules = [
            {
                "id": 42,
                "if": [{"subject": "?x", "predicate": "knows", "object": "?y"}],
                "then": [{"subject": "?x", "predicate": "age", "object": "40"}],
            }
        ]
        section = explain_section(
            functional=[{"id": "f-age", "property": "age"}]
        )
        report = diagnose_json(
            explain_doc(triples=triples, rules=rules, consistency=section)
        )
        objects = [item["object"] for item in report.diagnostics[0]["evidence"]]
        self.assertEqual(objects, ["40", "50"])
        derived_proofs, explicit_proofs = report.explain_diagnostic(0)
        derived = derived_proofs[0]
        self.assertEqual(derived.kind, "rule")
        self.assertEqual(derived.ruleId, 42)
        self.assertIsInstance(derived.ruleId, int)
        self.assertEqual(derived.triple, Triple("bob", "age", "40"))
        self.assertEqual(
            derived.premises[0].triple, Triple("bob", "knows", "carol")
        )
        explicit = explicit_proofs[0]
        self.assertEqual(explicit.kind, "explicit")
        self.assertIsNone(explicit.ruleId)
        self.assertEqual(explicit.triple, Triple("bob", "age", "50"))

    def test_multi_level_premises_follow_if_pattern_order_to_leaves(self):
        triples = [
            {"subject": "alice", "predicate": "rdfType", "object": "Person"},
            {"subject": "alice", "predicate": "knows", "object": "bob"},
            {"subject": "bob", "predicate": "knows", "object": "carol"},
        ]
        rules = [
            {
                "id": "fof-robot",
                "if": [
                    {"subject": "?x", "predicate": "knows", "object": "?y"},
                    {"subject": "?y", "predicate": "knows", "object": "?z"},
                ],
                "then": [
                    {"subject": "?x", "predicate": "rdfType", "object": "Robot"}
                ],
            }
        ]
        section = explain_section(
            disjoint=[{"id": "d1", "classes": ["Person", "Robot"]}]
        )
        report = diagnose_json(
            explain_doc(triples=triples, rules=rules, consistency=section)
        )
        _, robot_proofs = report.explain_diagnostic(0)
        (proof,) = robot_proofs
        self.assertEqual(proof.ruleId, "fof-robot")
        # premises 严格按规则 if 模式顺序，且均为显式叶子
        self.assertEqual(
            [premise.triple for premise in proof.premises],
            [Triple("alice", "knows", "bob"), Triple("bob", "knows", "carol")],
        )
        self.assertTrue(all(p.kind == "explicit" for p in proof.premises))

    def test_chained_rules_premises_point_to_derivation_step(self):
        triples = [
            {"subject": "bob", "predicate": "knows", "object": "carol"},
            {"subject": "bob", "predicate": "age", "object": "50"},
        ]
        rules = [
            {
                "id": "k2l",
                "if": [{"subject": "?x", "predicate": "knows", "object": "?y"}],
                "then": [
                    {"subject": "?x", "predicate": "likes", "object": "?y"}
                ],
            },
            {
                "id": "l2age",
                "if": [{"subject": "?x", "predicate": "likes", "object": "?y"}],
                "then": [{"subject": "?x", "predicate": "age", "object": "40"}],
            },
        ]
        section = explain_section(
            functional=[{"id": "f-age", "property": "age"}]
        )
        report = diagnose_json(
            explain_doc(triples=triples, rules=rules, consistency=section)
        )
        derived_proofs, _ = report.explain_diagnostic(0)
        (proof,) = derived_proofs
        self.assertEqual(proof.ruleId, "l2age")
        self.assertEqual(len(proof.premises), 1)
        inner = proof.premises[0]
        self.assertEqual(inner.kind, "rule")
        self.assertEqual(inner.ruleId, "k2l")
        self.assertEqual(inner.triple, Triple("bob", "likes", "carol"))
        (leaf,) = inner.premises
        self.assertEqual(leaf.kind, "explicit")
        self.assertEqual(leaf.triple, Triple("bob", "knows", "carol"))

    def test_all_shortest_proofs_returned_with_stable_rule_order(self):
        triples = [
            {"subject": "alice", "predicate": "rdfType", "object": "Person"},
            {"subject": "alice", "predicate": "knows", "object": "bob"},
        ]
        rules = [
            {
                "id": name,
                "if": [{"subject": "?x", "predicate": "knows", "object": "?y"}],
                "then": [
                    {"subject": "?x", "predicate": "rdfType", "object": "Robot"}
                ],
            }
            for name in ("r2", "r1")
        ]
        section = explain_section(
            disjoint=[{"id": "d1", "classes": ["Person", "Robot"]}]
        )
        report = diagnose_json(
            explain_doc(triples=triples, rules=rules, consistency=section)
        )
        _, robot_proofs = report.explain_diagnostic(0)
        self.assertEqual([p.ruleId for p in robot_proofs], ["r1", "r2"])

    def test_ground_rule_with_empty_if_has_no_premises(self):
        triples = [
            {"subject": "alice", "predicate": "rdfType", "object": "Person"},
        ]
        rules = [
            {
                "id": "ground-robot",
                "if": [],
                "then": [
                    {"subject": "alice", "predicate": "rdfType", "object": "Robot"}
                ],
            }
        ]
        section = explain_section(
            disjoint=[{"id": "d1", "classes": ["Person", "Robot"]}]
        )
        report = diagnose_json(
            explain_doc(triples=triples, rules=rules, consistency=section)
        )
        _, robot_proofs = report.explain_diagnostic(0)
        (proof,) = robot_proofs
        self.assertEqual(proof.kind, "rule")
        self.assertEqual(proof.ruleId, "ground-robot")
        self.assertEqual(proof.premises, ())

    def test_index_selects_diagnostic_evidence_pair(self):
        triples = [
            {"subject": "alice", "predicate": "rdfType", "object": "Person"},
            {"subject": "alice", "predicate": "rdfType", "object": "Robot"},
            {"subject": "alice", "predicate": "rdfType", "object": "Animal"},
            {"subject": "alice", "predicate": "age", "object": "1"},
            {"subject": "alice", "predicate": "age", "object": "2"},
        ]
        section = explain_section(
            disjoint=[{"id": "d1", "classes": ["Animal", "Person", "Robot"]}],
            functional=[{"id": "f1", "property": "age"}],
        )
        report = diagnose_json(explain_doc(triples=triples, consistency=section))
        diagnostics = report.diagnostics
        self.assertEqual(len(diagnostics), 4)
        for index, diagnostic in enumerate(diagnostics):
            result = report.explain_diagnostic(index)
            evidence = diagnostic["evidence"]
            if diagnostic["kind"] == "disjointClassMembership":
                expected = [
                    Triple("alice", "rdfType", item["class"]) for item in evidence
                ]
            else:
                expected = [
                    Triple("alice", "age", item["object"]) for item in evidence
                ]
            self.assertEqual(
                [proofs[0].triple for proofs in result], expected
            )

    def test_matches_model_explain_on_equivalent_consistent_doc(self):
        # 去掉互斥约束后同一套事实/规则可正常 parse，追溯结果应与
        # OntologyModel.explain 对相应三元组给出的证明完全一致。
        report = self.derived_disjoint_report()
        triples = [
            {"subject": "alice", "predicate": "rdfType", "object": "Person"},
            {"subject": "alice", "predicate": "knows", "object": "bob"},
        ]
        rules = [
            {
                "id": "r-to-robot",
                "if": [{"subject": "?x", "predicate": "knows", "object": "?y"}],
                "then": [
                    {"subject": "?x", "predicate": "rdfType", "object": "Robot"}
                ],
            }
        ]
        model = parse(explain_doc(triples=triples, rules=rules))
        first_proofs, second_proofs = report.explain_diagnostic(0)
        self.assertEqual(
            first_proofs, model.explain("alice", "rdfType", "Person")
        )
        self.assertEqual(
            second_proofs, model.explain("alice", "rdfType", "Robot")
        )

    def test_cyclic_rules_terminate_without_cyclic_proofs(self):
        triples = [
            {"subject": "alice", "predicate": "rdfType", "object": "Person"},
            {"subject": "alice", "predicate": "knows", "object": "bob"},
        ]
        rules = [
            {
                "id": "p2q",
                "if": [{"subject": "?x", "predicate": "knows", "object": "?y"}],
                "then": [
                    {"subject": "?x", "predicate": "rdfType", "object": "Robot"}
                ],
            },
            {
                "id": "q2p",
                "if": [
                    {"subject": "?x", "predicate": "rdfType", "object": "Robot"}
                ],
                "then": [
                    {"subject": "?x", "predicate": "knows", "object": "bob"}
                ],
            },
        ]
        section = explain_section(
            disjoint=[{"id": "d1", "classes": ["Person", "Robot"]}]
        )
        report = diagnose_json(
            explain_doc(triples=triples, rules=rules, consistency=section)
        )
        _, robot_proofs = report.explain_diagnostic(0)
        self.assertEqual([p.ruleId for p in robot_proofs], ["p2q"])
        proof = robot_proofs[0]
        root = proof.triple
        # 证明树中每个三元组节点只出现一次：不存在依赖目标自身的循环证明
        for node in iter_proof_nodes(proof):
            self.assertEqual(
                sum(n.triple == node.triple for n in iter_proof_nodes(proof)),
                1,
            )
        self.assertTrue(all(p.kind == "explicit" for p in proof.premises))
        # 重复调用同样有限结束
        report.explain_diagnostic(0)

    def test_repeated_calls_return_equal_distinct_snapshots(self):
        report = self.derived_disjoint_report()
        first = report.explain_diagnostic(0)
        for _ in range(3):
            again = report.explain_diagnostic(0)
            self.assertEqual(again, first)
            self.assertIsNot(again, first)
            self.assertIsNot(again[0], first[0])
            self.assertIsNot(again[1], first[1])

    def test_returned_proofs_are_read_only_and_isolated(self):
        report = self.derived_disjoint_report()
        result = report.explain_diagnostic(0)
        self.assertIsInstance(result, tuple)
        for proofs in result:
            self.assertIsInstance(proofs, tuple)
            for proof in proofs:
                self.assertIsInstance(proof.premises, tuple)
                for attr, value in (
                    ("kind", "x"),
                    ("triple", None),
                    ("ruleId", "y"),
                    ("premises", ()),
                ):
                    with self.assertRaises(AttributeError):
                        setattr(proof, attr, value)
        snapshot = report.explain_diagnostic(0)
        # 调用方持有旧结果后再次调用，内容不受任何影响
        self.assertEqual(report.explain_diagnostic(0), snapshot)
        # 报告的其余读取结果也不变
        diagnostics = report.diagnostics
        self.assertEqual(report.diagnostics, diagnostics)
        self.assertIsNone(report.model)

    def test_non_integer_bool_negative_and_out_of_range_raise(self):
        report = self.derived_disjoint_report()
        self.assertEqual(len(report.diagnostics), 1)
        for bad in (1, 2, -1):
            with self.assertRaises(OntologyError) as ctx:
                report.explain_diagnostic(bad)
            self.assertIn(str(bad), str(ctx.exception))
        for bad in (1.0, 1.5, "0", None, [0], object()):
            with self.assertRaises(OntologyError) as ctx:
                report.explain_diagnostic(bad)
            self.assertIn("整数", str(ctx.exception))
        # bool 不被当作整数 1
        with self.assertRaises(OntologyError) as ctx:
            report.explain_diagnostic(True)
        self.assertIn("整数", str(ctx.exception))
        self.assertIn("True", str(ctx.exception))
        with self.assertRaises(OntologyError) as ctx:
            report.explain_diagnostic(False)
        self.assertIn("整数", str(ctx.exception))

    def test_empty_diagnostics_always_raises_with_empty_note(self):
        section = explain_section(
            disjoint=[{"id": "d1", "classes": ["Person", "Robot"]}],
            functional=[{"id": "f1", "property": "age"}],
        )
        report = diagnose_json(explain_doc(consistency=section))
        self.assertTrue(report.is_consistent)
        self.assertEqual(report.diagnostics, ())
        for index in (0, -1, 2):
            with self.assertRaises(OntologyError) as ctx:
                report.explain_diagnostic(index)
            message = str(ctx.exception)
            self.assertIn("diagnostics 为空", message)
            self.assertIn(str(index), message)
        # 非整数下标同样报错（类型校验优先）
        with self.assertRaises(OntologyError) as ctx:
            report.explain_diagnostic("0")
        self.assertIn("整数", str(ctx.exception))

    def test_explain_diagnostic_does_not_change_report(self):
        report = self.derived_disjoint_report()
        before = (report.is_consistent, report.model, report.diagnostics)
        report.explain_diagnostic(0)
        report.explain_diagnostic(0)
        after = (report.is_consistent, report.model, report.diagnostics)
        self.assertEqual(before, after)


class AskTests(unittest.TestCase):
    def setUp(self):
        self.model = query_model()

    def test_plain_match_true_and_false(self):
        self.assertIs(self.model.ask("ASK WHERE { alice knows bob }"), True)
        self.assertIs(self.model.ask("ASK WHERE { alice knows carol }"), False)

    def test_join_with_shared_variables(self):
        self.assertTrue(
            self.model.ask("ASK WHERE { ?x knows ?y . ?y likes ?z }")
        )
        self.assertFalse(self.model.ask("ASK WHERE { ?x knows ?x }"))

    def test_inferred_triples_match(self):
        # alice likes bob 由规则 knows-likes 推出，alice friendOf carol 由
        # friend-of-friend 推出，都与显式事实一样命中
        self.assertTrue(self.model.ask("ASK WHERE { alice likes bob }"))
        self.assertTrue(self.model.ask("ASK WHERE { alice friendOf carol }"))
        self.assertFalse(self.model.ask("ASK WHERE { carol likes alice }"))

    def test_ground_query_returns_bool_without_projection(self):
        result = self.model.ask("ASK WHERE { ?x knows ?y }")
        self.assertIs(result, True)

    def test_optional_no_match_keeps_solution(self):
        # carol 没有 knows 出边，OPTIONAL 无匹配时原解保留
        self.assertTrue(
            self.model.ask(
                "ASK WHERE { ?x likes carol . OPTIONAL { ?x knows ?y } }"
            )
        )
        # OPTIONAL 可满足时同样为真
        self.assertTrue(
            self.model.ask(
                "ASK WHERE { ?x knows bob . OPTIONAL { ?x likes ?y } }"
            )
        )

    def test_optional_unbound_variable_with_not_bound_filter(self):
        # bob knows carol 但 bob 没有 friendOf 出边：OPTIONAL 未匹配，
        # ?z 未绑定，!BOUND(?z) 为真
        self.assertTrue(
            self.model.ask(
                "ASK WHERE { ?x knows ?y . OPTIONAL { ?x friendOf ?z }"
                " . FILTER(!BOUND(?z)) }"
            )
        )
        # alice friendOf carol 可推出，BOUND(?z) 在合并解上成立
        self.assertTrue(
            self.model.ask(
                "ASK WHERE { ?x knows ?y . OPTIONAL { ?x friendOf ?z }"
                " . FILTER(BOUND(?z)) }"
            )
        )

    def test_filter_string_constant_and_variable_comparison(self):
        self.assertTrue(
            self.model.ask('ASK WHERE { ?x knows ?y . FILTER(?y = "bob") }')
        )
        self.assertFalse(
            self.model.ask('ASK WHERE { ?x knows ?y . FILTER(?y = "nobody") }')
        )
        self.assertTrue(
            self.model.ask(
                "ASK WHERE { ?x knows ?y . ?y knows ?z . FILTER(?x != ?z) }"
            )
        )
        self.assertFalse(
            self.model.ask("ASK WHERE { ?x knows ?y . FILTER(?x = ?y) }")
        )

    def test_filter_comparison_with_unbound_variable_is_false(self):
        # bob knows carol 但 bob 无 friendOf 出边：?z 未绑定，等值比较为假
        self.assertFalse(
            self.model.ask(
                'ASK WHERE { ?x knows carol . OPTIONAL { ?x friendOf ?z }'
                ' . FILTER(?z = "carol") }'
            )
        )

    def test_union_merges_branches(self):
        self.assertTrue(
            self.model.ask(
                "ASK WHERE { { ?x likes carol } UNION { ?x knows carol } }"
            )
        )
        self.assertFalse(
            self.model.ask(
                "ASK WHERE { { alice knows carol } UNION { carol knows alice } }"
            )
        )

    def test_filter_after_union_runs_on_merged_solutions(self):
        self.assertTrue(
            self.model.ask(
                'ASK WHERE { { ?x knows ?y } UNION { ?x likes ?y }'
                ' . FILTER(?y = "carol") }'
            )
        )
        self.assertFalse(
            self.model.ask(
                'ASK WHERE { { ?x knows ?y } UNION { ?x likes ?y }'
                ' . FILTER(?y = "nobody") }'
            )
        )

    def test_property_paths(self):
        model = path_model()
        self.assertTrue(model.ask("ASK WHERE { a edge/edge c }"))
        self.assertFalse(model.ask("ASK WHERE { a edge/edge d }"))
        self.assertTrue(model.ask("ASK WHERE { c ^edge b }"))
        self.assertTrue(model.ask("ASK WHERE { a edge|other x }"))
        self.assertTrue(model.ask("ASK WHERE { a edge? a }"))
        # iso 不出现在任何三元组中，p? 的零次分支不覆盖它
        self.assertFalse(model.ask("ASK WHERE { iso edge? b }"))
        self.assertTrue(model.ask("ASK WHERE { a edge+ d }"))
        self.assertTrue(model.ask("ASK WHERE { a edge* a }"))

    def test_cyclic_path_terminates(self):
        model = path_model()
        # b→c→d→b 成环，传递闭包有限结束
        self.assertTrue(model.ask("ASK WHERE { b edge+ b }"))
        self.assertTrue(model.ask("ASK WHERE { d edge+ c }"))
        self.assertFalse(model.ask("ASK WHERE { x edge+ a }"))

    def test_inferred_edges_participate_in_paths(self):
        model = path_model()
        # a edge x 由规则 other-to-edge 推出，invOnly 边全部由规则推出
        self.assertTrue(model.ask("ASK WHERE { a edge x }"))
        self.assertTrue(model.ask("ASK WHERE { c ^invOnly b }"))

    def test_repeated_calls_consistent_and_do_not_mutate_model(self):
        model = query_model()
        before = (model.explicit_triples, model.derived_triples, model.triples)
        text = (
            "ASK WHERE { ?x knows ?y . OPTIONAL { ?x friendOf ?z }"
            " . FILTER(!BOUND(?z)) }"
        )
        first = model.ask(text)
        for _ in range(3):
            self.assertIs(model.ask(text), first)
        after = (model.explicit_triples, model.derived_triples, model.triples)
        self.assertEqual(before, after)


class AskSyntaxErrorTests(unittest.TestCase):
    def setUp(self):
        self.model = query_model()

    def assertAskError(self, text, *fragments):
        with self.assertRaises(OntologyError) as ctx:
            self.model.ask(text)
        message = str(ctx.exception)
        for fragment in fragments:
            self.assertIn(fragment, message)

    def test_non_string_input_reports_received_type(self):
        self.assertAskError(123, "str", "int")
        self.assertAskError(None, "NoneType")
        self.assertAskError(["ASK WHERE { ?x knows ?y }"], "list")

    def test_empty_query(self):
        self.assertAskError("", "ASK")
        self.assertAskError("   \n\t", "ASK")

    def test_select_form_rejected(self):
        self.assertAskError("SELECT ?x WHERE { ?x knows ?y }", "ASK", "SELECT")

    def test_projection_variable_rejected(self):
        self.assertAskError("ASK ?x WHERE { ?x knows ?y }", "WHERE", "?x")

    def test_star_rejected(self):
        self.assertAskError("ASK * WHERE { ?x knows ?y }", "WHERE")

    def test_trailing_content_rejected(self):
        self.assertAskError("ASK WHERE { ?x knows ?y } ?z", "未知语句成分")

    def test_lowercase_keywords_rejected(self):
        self.assertAskError("ask where { ?x knows ?y }", "ASK")

    def test_missing_where(self):
        self.assertAskError("ASK { ?x knows ?y }", "WHERE")

    def test_missing_braces(self):
        self.assertAskError("ASK WHERE ?x knows ?y }", "'{'")
        self.assertAskError("ASK WHERE { ?x knows ?y", "'}'")

    def test_empty_where_body(self):
        self.assertAskError("ASK WHERE { }", "为空")

    def test_unclosed_string_lexical_error(self):
        self.assertAskError('ASK WHERE { ?x knows "bob }', "字符位置")

    def test_undeclared_property_rejected(self):
        self.assertAskError("ASK WHERE { ?x unknown ?y }", "未声明", "unknown")

    def test_undeclared_property_in_path_rejected(self):
        self.assertAskError("ASK WHERE { ?x knows/unknown+ ?y }", "未声明")

    def test_unsupported_form_rejected(self):
        self.assertAskError("ASK WHERE { ?x knows ?y . OPTIONAL { } }", "为空")
        self.assertAskError("ASK WHERE { ?x knows** ?y }", "量词")

    def test_legal_query_without_match_is_false_not_error(self):
        self.assertIs(self.model.ask("ASK WHERE { ?x knows nobody }"), False)


if __name__ == "__main__":
    unittest.main()
