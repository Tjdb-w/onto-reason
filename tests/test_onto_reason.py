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


class QueryTests(unittest.TestCase):
    def _model_with_inference(self):
        rule = {
            "id": "knows-likes",
            "if": [{"subject": "?x", "predicate": "knows", "object": "?y"}],
            "then": [{"subject": "?x", "predicate": "likes", "object": "?y"}],
        }
        return parse(make_doc(rules=[rule]))

    def test_basic_select_two_vars(self):
        model = parse(make_doc())
        result = model.query("SELECT ?x ?y WHERE { ?x knows ?y }")
        self.assertIsInstance(result, QueryResult)
        self.assertEqual(result.variables, ("?x", "?y"))
        self.assertEqual(
            result.rows,
            (("alice", "bob"), ("bob", "carol")),
        )

    def test_rows_are_immutable_tuples(self):
        model = parse(make_doc())
        result = model.query("SELECT ?x ?y WHERE { ?x knows ?y }")
        self.assertIsInstance(result.rows, tuple)
        self.assertIsInstance(result.rows[0], tuple)
        self.assertIsInstance(result.variables, tuple)

    def test_star_projection_first_occurrence_order(self):
        model = self._model_with_inference()
        q = "SELECT * WHERE { ?x knows ?y . ?y likes ?z }"
        result = model.query(q)
        # 变量按模式从左到右首次出现顺序：?x, ?y（模式1）, ?z（模式2）
        self.assertEqual(result.variables, ("?x", "?y", "?z"))
        # alice knows bob; bob likes carol（推理） => 一行
        self.assertEqual(result.rows, (("alice", "bob", "carol"),))

    def test_constant_subject_and_object(self):
        model = parse(make_doc())
        result = model.query("SELECT ?y WHERE { alice knows ?y }")
        self.assertEqual(result.rows, (("bob",),))
        result2 = model.query("SELECT ?x WHERE { ?x knows carol }")
        self.assertEqual(result2.rows, (("bob",),))

    def test_constant_pattern_ground_triple(self):
        model = parse(make_doc())
        hit = model.query("SELECT * WHERE { alice knows bob }")
        # 模式中没有变量且事实存在：一行空绑定。
        self.assertEqual(hit.variables, ())
        self.assertEqual(hit.rows, ((),))
        miss = model.query("SELECT * WHERE { alice knows carol }")
        self.assertEqual(miss.rows, ())

    def test_derived_triples_match_like_explicit(self):
        model = self._model_with_inference()
        result = model.query("SELECT ?x ?y WHERE { ?x likes ?y }")
        self.assertEqual(
            result.rows,
            (("alice", "bob"), ("bob", "carol")),
        )
        # source_rule 仍然只描述推理来源，查询不影响该语义。
        self.assertEqual(
            model.source_rule("alice", "likes", "bob"), "knows-likes"
        )

    def test_join_and_same_fact_reuse(self):
        # ?x knows ?y 且 ?y knows ?z；同一事实可被两个模式复用。
        rule = {
            "id": "trans",
            "if": [
                {"subject": "?x", "predicate": "knows", "object": "?y"},
                {"subject": "?y", "predicate": "knows", "object": "?z"},
            ],
            "then": [{"subject": "?x", "predicate": "friendOf", "object": "?z"}],
        }
        model = parse(make_doc(rules=[rule]))
        result = model.query(
            "SELECT ?x ?z WHERE { ?x knows ?y . ?y knows ?z }"
        )
        self.assertEqual(result.rows, (("alice", "carol"),))

    def test_self_join_variable(self):
        triples = [
            {"subject": "alice", "predicate": "knows", "object": "alice"},
            {"subject": "alice", "predicate": "knows", "object": "bob"},
        ]
        model = parse(make_doc(triples=triples))
        result = model.query("SELECT ?x WHERE { ?x knows ?x }")
        self.assertEqual(result.rows, (("alice",),))

    def test_rows_deduplicated_and_sorted(self):
        triples = [
            {"subject": "bob", "predicate": "knows", "object": "alice"},
            {"subject": "alice", "predicate": "knows", "object": "carol"},
            {"subject": "alice", "predicate": "knows", "object": "bob"},
        ]
        # 仅投影 ?x，多个模式命中产生重复绑定，应去重并按字典序。
        model = parse(make_doc(triples=triples))
        result = model.query("SELECT ?x WHERE { ?x knows ?y }")
        self.assertEqual(result.rows, (("alice",), ("bob",)))

    def test_no_match_returns_empty_rows(self):
        model = parse(make_doc())
        result = model.query("SELECT ?x WHERE { ?x hates ?y }")
        self.assertEqual(result.variables, ("?x",))
        self.assertEqual(result.rows, ())

    def test_quoted_json_string_constant(self):
        triples = [
            {"subject": "alice", "predicate": "knows", "object": "hello world"},
            {"subject": "alice", "predicate": "knows", "object": 'say "hi"'},
        ]
        model = parse(make_doc(triples=triples))
        result = model.query(
            'SELECT ?x WHERE { ?x knows "hello world" }'
        )
        self.assertEqual(result.rows, (("alice",),))
        result2 = model.query(
            r'SELECT ?x WHERE { ?x knows "say \"hi\"" }'
        )
        self.assertEqual(result2.rows, (("alice",),))
        # 解码后按完整字符串精确比较：名称形式的 helloworld 不等于带空格的串。
        self.assertEqual(
            model.query("SELECT * WHERE { alice knows helloworld }").rows,
            (),
        )

    def test_whitespace_mixed_and_trailing_dot(self):
        model = parse(make_doc())
        q = "\n\tSELECT  ?x ?y\tWHERE {\r\n ?x knows ?y .\n ?y knows ?z . }"
        result = model.query(q)
        self.assertEqual(result.variables, ("?x", "?y"))

    def test_repeated_execution_is_stable(self):
        model = self._model_with_inference()
        q = "SELECT * WHERE { ?x knows ?y . ?y likes ?z }"
        first = model.query(q)
        for _ in range(3):
            again = model.query(q)
            self.assertEqual(again, first)
        self.assertEqual(
            (first.variables, first.rows),
            (again.variables, again.rows),
        )

    def test_pattern_order_does_not_change_match_set(self):
        model = self._model_with_inference()
        # 显式投影固定列序，两种模式顺序应得到完全相同的结果。
        a = model.query("SELECT ?x ?y ?z WHERE { ?x knows ?y . ?y likes ?z }")
        b = model.query("SELECT ?x ?y ?z WHERE { ?y likes ?z . ?x knows ?y }")
        self.assertEqual(a.variables, ("?x", "?y", "?z"))
        self.assertEqual(a.rows, b.rows)

    def test_unicode_variable_and_constant(self):
        triples = [
            {"subject": "甲", "predicate": "knows", "object": "乙"},
        ]
        model = parse(make_doc(triples=triples))
        result = model.query("SELECT ?谁1 WHERE { ?谁1 knows ?对象_2 }")
        self.assertEqual(result.variables, ("?谁1",))
        self.assertEqual(result.rows, (("甲",),))

    # ---------- 词法 / 语法错误 ----------

    def assert_query_error(self, model, q, *parts):
        with self.assertRaises(OntologyError) as ctx:
            model.query(q)
        message = str(ctx.exception)
        for part in parts:
            self.assertIn(part, message)

    def test_error_missing_select(self):
        model = parse(make_doc())
        self.assert_query_error(
            model, "?x WHERE { ?x knows ?y }", "SELECT"
        )

    def test_error_lowercase_keyword_rejected(self):
        model = parse(make_doc())
        self.assert_query_error(
            model, "select ?x where { ?x knows ?y }", "SELECT", "大写"
        )

    def test_error_missing_where(self):
        model = parse(make_doc())
        self.assert_query_error(
            model, "SELECT ?x { ?x knows ?y }", "WHERE"
        )

    def test_error_missing_braces(self):
        model = parse(make_doc())
        self.assert_query_error(
            model, "SELECT ?x WHERE ?x knows ?y", "左花括号"
        )
        self.assert_query_error(
            model, "SELECT ?x WHERE { ?x knows ?y", "右花括号"
        )

    def test_error_empty_pattern_body(self):
        model = parse(make_doc())
        self.assert_query_error(model, "SELECT ?x WHERE {  }", "空模式体")
        self.assert_query_error(model, "SELECT * WHERE { }", "空模式体")

    def test_error_duplicate_projection_variable(self):
        model = parse(make_doc())
        self.assert_query_error(
            model, "SELECT ?x ?x WHERE { ?x knows ?y }", "?x", "重复"
        )

    def test_error_projection_variable_not_in_patterns(self):
        model = parse(make_doc())
        self.assert_query_error(
            model, "SELECT ?z WHERE { ?x knows ?y }", "?z", "未在 WHERE"
        )

    def test_error_variable_predicate(self):
        model = parse(make_doc())
        self.assert_query_error(
            model, "SELECT ?x WHERE { ?x ?p ?y }", "谓语", "?p"
        )

    def test_error_unknown_trailing_component(self):
        model = parse(make_doc())
        self.assert_query_error(
            model, "SELECT ?x WHERE { ?x knows ?y } EXTRA",
            "未知语句成分",
        )

    def test_error_pattern_too_few_or_many_terms(self):
        model = parse(make_doc())
        self.assert_query_error(
            model, "SELECT ?x WHERE { ?x knows }", "模式 0", "项数不足"
        )
        self.assert_query_error(
            model, "SELECT ?x WHERE { ?x knows ?y extra }",
            "模式 0",
            "项数过多",
        )
        self.assert_query_error(
            model, "SELECT ?x WHERE { ?x knows ?y . bob knows }",
            "模式 1",
            "项数不足",
        )

    def test_error_pattern_index_reported(self):
        model = parse(make_doc())
        with self.assertRaises(OntologyError) as ctx:
            model.query(
                "SELECT * WHERE { ?x knows ?y . ?x ?bad ?z . }"
            )
        self.assertIn("模式 1", str(ctx.exception))

    def test_error_bad_variable_lexeme(self):
        model = parse(make_doc())
        self.assert_query_error(
            model, "SELECT ? WHERE { ?x knows ?y }", "变量词法不合法"
        )
        self.assert_query_error(
            model, "SELECT ?x WHERE { ? knows ?y }", "变量词法不合法"
        )
        # 带圈数字不属于字母/数字/下划线，'?' 后紧跟它仍是非法变量词法。
        self.assert_query_error(
            model, "SELECT ?① WHERE { ?x knows ?y }", "变量词法不合法"
        )

    def test_error_unterminated_string(self):
        model = parse(make_doc())
        self.assert_query_error(
            model, 'SELECT ?x WHERE { ?x knows "abc }', "右引号"
        )

    def test_error_bad_json_string_escape(self):
        model = parse(make_doc())
        with self.assertRaises(OntologyError):
            model.query(r'SELECT ?x WHERE { ?x knows "a\x" }')

    def test_error_empty_query_and_select_list(self):
        model = parse(make_doc())
        with self.assertRaises(OntologyError):
            model.query("   ")
        with self.assertRaises(OntologyError):
            model.query("SELECT WHERE { ?x knows ?y }")
        with self.assertRaises(OntologyError) as ctx:
            model.query("SELECT")
        self.assertIn("SELECT", str(ctx.exception))
        with self.assertRaises(OntologyError) as ctx:
            model.query("SELECT ?x")
        self.assertIn("WHERE", str(ctx.exception))

    def test_error_star_mixed_with_variables(self):
        model = parse(make_doc())
        self.assert_query_error(
            model, "SELECT * ?x WHERE { ?x knows ?y }", "*", "混用"
        )

    def test_query_does_not_mutate_model(self):
        model = self._model_with_inference()
        before = (model.explicit_triples, model.derived_triples, model.triples)
        model.query("SELECT * WHERE { ?x knows ?y . ?y likes ?z }")
        model.query("SELECT ?x WHERE { ?x hates ?y }")
        after = (model.explicit_triples, model.derived_triples, model.triples)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
