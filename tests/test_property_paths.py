"""属性路径（^p、a/b、a|b、p?、p*、p+、圆括号分组）查询测试。"""

import json
import unittest

from onto_reason import OntologyEngine, OntologyError, QueryResult


def parse(doc):
    return OntologyEngine().parse(json.dumps(doc))


def chain_doc(with_cycle=True, rules=None):
    """knows 链 a-b-c-d，可选回边 c-a 成环；likes 为另一条边。"""
    triples = [
        {"subject": "a", "predicate": "knows", "object": "b"},
        {"subject": "b", "predicate": "knows", "object": "c"},
        {"subject": "c", "predicate": "knows", "object": "d"},
        {"subject": "a", "predicate": "likes", "object": "x"},
    ]
    if with_cycle:
        triples.append({"subject": "c", "predicate": "knows", "object": "a"})
    doc = {
        "classes": ["Person"],
        "properties": ["knows", "likes", "parent"],
        "individuals": ["a", "b", "c", "d", "x"],
        "triples": triples
        + [
            {"subject": "c", "predicate": "parent", "object": "b"},
            {"subject": "d", "predicate": "parent", "object": "c"},
        ],
        "rules": rules
        or [
            {
                "id": "knows-likes",
                "if": [
                    {"subject": "?x", "predicate": "knows", "object": "?y"}
                ],
                "then": [
                    {"subject": "?x", "predicate": "likes", "object": "?y"}
                ],
            }
        ],
    }
    return parse(doc)


class PropertyPathSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.model = chain_doc()

    def test_one_or_more_transitive_closure(self):
        result = self.model.query("SELECT ?y WHERE { a knows+ ?y }")
        self.assertEqual(
            result.rows,
            (("a",), ("b",), ("c",), ("d",)),
        )

    def test_zero_or_more_includes_self(self):
        result = self.model.query("SELECT ?y WHERE { a knows* ?y }")
        self.assertEqual(
            result.rows,
            (("a",), ("b",), ("c",), ("d",)),
        )

    def test_zero_or_one(self):
        # a knows? 自身零次 + 一层出边 b
        self.assertEqual(
            self.model.query("SELECT ?y WHERE { a knows? ?y }").rows,
            (("a",), ("b",)),
        )
        # d 没有 knows 出边：零次只给出 d 自己
        self.assertEqual(
            self.model.query("SELECT ?y WHERE { d knows? ?y }").rows,
            (("d",),),
        )

    def test_zero_branch_only_uses_actual_nodes(self):
        # x 只作为宾语出现过，仍是零次分支节点；zzz 从未出现，无任何行。
        self.assertEqual(
            self.model.query("SELECT ?a WHERE { x knows? ?a }").rows,
            (("x",),),
        )
        self.assertEqual(
            self.model.query("SELECT * WHERE { zzz knows* ?b }").rows,
            (),
        )

    def test_inverse_path(self):
        # s ^p o 成立当且仅当 o p s：?x ^knows b 即 b knows ?x -> b->c => c
        result = self.model.query("SELECT ?x WHERE { ?x ^knows b }")
        self.assertEqual(result.rows, (("c",),))
        # 逆向传递闭包：从 d 沿反向 knows* 可达 {d,c,b,a}
        self.assertEqual(
            self.model.query("SELECT ?x WHERE { d ^knows* ?x }").rows,
            (("a",), ("b",), ("c",), ("d",)),
        )

    def test_sequence_is_relation_composition(self):
        # a knows/parent：a->b(knows), b parent 无；其它起点见结果
        result = self.model.query("SELECT ?x ?z WHERE { ?x knows/parent ?z }")
        self.assertEqual(
            result.rows,
            # b->c(knows), c parent b => (b,b)
            # c->a/c->d(knows), a 无 parent, d parent c => (c,c)
            (("b", "b"), ("c", "c")),
        )

    def test_alternation_is_union(self):
        self.assertEqual(
            self.model.query("SELECT ?y WHERE { a knows|likes ?y }").rows,
            # likes 含派生：a likes x(显式) + a likes b(派生)；knows: a->b
            (("b",), ("x",)),
        )

    def test_precedence_sequence_over_alternation(self):
        # likes 含派生：a likes b（派生）、a likes x（显式）；
        # knows/knows 两步：a->b->c = c。
        self.assertEqual(
            self.model.query("SELECT ?y WHERE { a (knows/knows)|likes ?y }").rows,
            (("b",), ("c",), ("x",)),
        )
        # a knows b，再 (knows|likes) 一步：b knows c、b likes c（派生）=> c
        self.assertEqual(
            self.model.query("SELECT ?y WHERE { a knows/(knows|likes) ?y }").rows,
            (("c",),),
        )

    def test_grouped_inverse_alternation(self):
        # ?x ^(knows|likes) a 等价于 a (knows|likes) ?x：
        # a knows b；a likes b（派生）、a likes x（显式）=> {b, x}
        self.assertEqual(
            self.model.query("SELECT ?x WHERE { ?x ^(knows|likes) a }").rows,
            (("b",), ("x",)),
        )

    def test_quantifier_binds_before_inverse(self):
        # ^knows* 解释为 ^(knows*)：b ^knows* ?y 等价于 ?y knows* b。
        # 可达 b 的有 b 自身（零次）、a->b、c->a->b；d 无出边不可达。
        self.assertEqual(
            self.model.query("SELECT ?y WHERE { b ^knows* ?y }").rows,
            (("a",), ("b",), ("c",)),
        )

    def test_whitespace_around_operators(self):
        self.assertEqual(
            self.model.query(
                "SELECT ?y WHERE { a ( knows | likes ) ?y }"
            ).rows,
            (("b",), ("x",)),
        )
        self.assertEqual(
            self.model.query("SELECT ?y WHERE { a knows + ?y }").rows,
            (("a",), ("b",), ("c",), ("d",)),
        )

    def test_paths_cover_derived_facts(self):
        # likes 全部含派生；a likes b 由规则推出，沿 likes+ 可到达
        result = self.model.query("SELECT ?x ?y WHERE { ?x likes+ ?y }")
        self.assertIn(("a", "b"), result.rows)
        self.assertIn(("a", "x"), result.rows)

    def test_constant_endpoints_ground_hit_and_miss(self):
        hit = self.model.query("SELECT * WHERE { a knows+ d }")
        self.assertEqual(hit.variables, ())
        self.assertEqual(hit.rows, ((),))
        miss = self.model.query("SELECT * WHERE { d knows+ a }")
        self.assertEqual(miss.variables, ())
        self.assertEqual(miss.rows, ())

    def test_same_variable_both_ends_on_cycle(self):
        result = self.model.query("SELECT ?x WHERE { ?x knows+ ?x }")
        # 环上 a/b/c 可回到自身；d 不能
        self.assertEqual(result.rows, (("a",), ("b",), ("c",)))

    def test_pairs_are_deduplicated(self):
        # 显式 a->b 与任何派生都不产生重复节点对
        result = self.model.query("SELECT ?x ?y WHERE { ?x knows? ?y }")
        rows = result.rows
        self.assertEqual(len(rows), len(set(rows)))

    def test_star_projection_first_appearance_order(self):
        result = self.model.query("SELECT * WHERE { ?x knows/parent ?y }")
        self.assertEqual(result.variables, ("?x", "?y"))

    def test_result_is_query_result_sorted_and_repeatable(self):
        text = "SELECT ?x ?y WHERE { ?x (knows|likes)+ ?y }"
        first = self.model.query(text)
        self.assertIsInstance(first, QueryResult)
        rows = first.rows
        self.assertEqual(rows, tuple(sorted(rows)))
        for _ in range(3):
            self.assertEqual(self.model.query(text), first)


class PathWithOptionalUnionFilterTests(unittest.TestCase):
    def setUp(self):
        self.model = chain_doc()

    def test_optional_path_left_join_with_none(self):
        # knows 边：a->b、b->c、c->d、c->a；parent 边：c->b、d->c。
        # 沿 ?y parent ?z 做左连接，无 parent 的 ?y 以 None 占位。
        result = self.model.query(
            "SELECT ?x ?z WHERE { ?x knows ?y . OPTIONAL { ?y parent ?z } }"
        )
        self.assertEqual(
            result.rows,
            (
                ("a", None),
                ("b", "b"),
                ("c", None),
                ("c", "c"),
            ),
        )

    def test_union_branch_with_path(self):
        result = self.model.query(
            "SELECT ?y WHERE { { a knows ?y } UNION { a ^knows ?y } }"
        )
        # 左：a knows ?y -> b；右：a ^knows ?y 等价 ?y knows a -> c
        self.assertEqual(result.rows, (("b",), ("c",)))

    def test_filter_after_path_pattern(self):
        result = self.model.query(
            "SELECT ?y WHERE { a knows* ?y . FILTER(!BOUND(?y)) }"
        )
        self.assertEqual(result.rows, ())
        result = self.model.query(
            "SELECT ?y WHERE { a knows* ?y . FILTER(?y != \"b\") }"
        )
        self.assertNotIn(("b",), result.rows)
        self.assertIn(("d",), result.rows)


class PathTerminationTests(unittest.TestCase):
    def test_plus_terminates_on_large_cycle(self):
        n = 300
        triples = [
            {"subject": str(i), "predicate": "knows", "object": str(i + 1)}
            for i in range(n)
        ]
        triples.append(
            {"subject": str(n), "predicate": "knows", "object": "0"}
        )
        doc = {
            "classes": ["Person"],
            "properties": ["knows"],
            "individuals": [str(i) for i in range(n + 1)],
            "triples": triples,
            "rules": [],
        }
        model = parse(doc)
        result = model.query("SELECT ?y WHERE { 0 knows+ ?y }")
        self.assertEqual(len(result.rows), n + 1)


class PathSyntaxErrorTests(unittest.TestCase):
    def setUp(self):
        self.model = chain_doc()

    def assertPathError(self, text, *fragments):
        with self.assertRaises(OntologyError) as ctx:
            self.model.query(text)
        message = str(ctx.exception)
        self.assertTrue(
            "字符位置" in message or "模式" in message or "分支" in message,
            f"错误消息缺少定位信息: {message}",
        )
        for fragment in fragments:
            self.assertIn(fragment, message)

    def test_non_str_input(self):
        with self.assertRaises(OntologyError) as ctx:
            self.model.query(123)
        self.assertIn("str", str(ctx.exception))

    def test_operator_missing_operand(self):
        self.assertPathError("SELECT * WHERE { ?x knows/ ?y }")
        self.assertPathError("SELECT * WHERE { ?x knows| ?y }")
        self.assertPathError("SELECT * WHERE { ?x /knows ?y }", "前缺少操作数")
        self.assertPathError("SELECT * WHERE { ?x knows/ . ?x likes ?y }")
        self.assertPathError("SELECT * WHERE { ?x knows/", "缺少操作数")
        self.assertPathError("SELECT * WHERE { ?x ^ ?y }", "^")
        self.assertPathError("SELECT * WHERE { ?x ^", "^")

    def test_unbalanced_parentheses(self):
        self.assertPathError(
            "SELECT * WHERE { ?x (knows|likes ?y }", "右圆括号"
        )
        self.assertPathError(
            "SELECT * WHERE { ?x knows) ?y }", "括号不配对"
        )
        self.assertPathError("SELECT * WHERE { ?x () ?y }", "不能为空")

    def test_variable_predicate_rejected(self):
        self.assertPathError(
            "SELECT * WHERE { ?x ?p ?y }", "谓语", "?p", "模式 0"
        )
        self.assertPathError(
            "SELECT * WHERE { ?x knows/ ?y }"
        )

    def test_undeclared_property_rejected(self):
        self.assertPathError(
            "SELECT * WHERE { ?x ghost ?y }", "ghost", "未声明"
        )
        self.assertPathError(
            "SELECT * WHERE { ?x knows/ghost ?y }", "ghost", "未声明"
        )
        self.assertPathError(
            "SELECT ?s ?o WHERE { { ?s ghost ?o } UNION { ?s likes ?o } }",
            "ghost",
        )

    def test_unsupported_path_notation(self):
        self.assertPathError(
            "SELECT * WHERE { ?x knows?* ?y }", "量词"
        )
        self.assertPathError(
            "SELECT * WHERE { ?x knows++ ?y }", "量词"
        )
        self.assertPathError(
            "SELECT * WHERE { ?x knows^likes ?y }", "^"
        )
        self.assertPathError(
            "SELECT * WHERE { ?x knows( ?y }", "/"
        )
        self.assertPathError(
            'SELECT * WHERE { ?x ^"knows" ?y }', "字符串常量"
        )
        self.assertPathError(
            'SELECT * WHERE { ?x knows/"x" ?y }', "字符串常量"
        )

    def test_lexically_incomplete(self):
        self.assertPathError("SELECT * WHERE { ?x knows+ ?y")
        self.assertPathError("SELECT * WHERE { ?x (knows ?y }")

    def test_path_errors_inside_optional_and_union(self):
        self.assertPathError(
            "SELECT * WHERE { OPTIONAL { ?x knows/ ?y } }"
        )
        self.assertPathError(
            "SELECT ?s ?o WHERE { { ?s knows/ ?o } UNION { ?s likes ?o } }"
        )


class PathBackwardCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.model = chain_doc()

    def test_plain_predicates_unchanged(self):
        self.assertEqual(
            self.model.query("SELECT ?x ?y WHERE { ?x knows ?y }").rows,
            tuple(sorted({
                (s, o)
                for s, o in [
                    ("a", "b"), ("b", "c"), ("c", "d"), ("c", "a"),
                ]
            })),
        )

    def test_special_characters_still_allowed_in_object_constants(self):
        doc = {
            "classes": ["Person"],
            "properties": ["knows"],
            "individuals": ["a"],
            "triples": [
                {"subject": "a", "predicate": "knows", "object": "x|y"},
                {"subject": "a", "predicate": "knows", "object": "(z)"},
            ],
            "rules": [],
        }
        model = parse(doc)
        self.assertEqual(
            model.query("SELECT ?o WHERE { a knows ?o }").rows,
            (("(z)",), ("x|y",)),
        )
        self.assertEqual(
            model.query("SELECT ?s WHERE { ?s knows (z) }").rows,
            (("a",),),
        )


if __name__ == "__main__":
    unittest.main()
