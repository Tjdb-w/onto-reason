import json
import unittest

from onto_reason import (
    InconsistencyError,
    OntologyEngine,
    OntologyError,
    OntologyModel,
    Triple,
)


def base_doc(**overrides):
    doc = {
        "classes": ["Animal", "Cat", "Dog", "Person", "Student"],
        "properties": ["rdfType", "knows", "mother"],
        "individuals": ["alice", "bob", "carol", "tom"],
        "triples": [],
        "rules": [],
    }
    doc.update(overrides)
    return doc


def member(subject, cls, predicate="rdfType"):
    return {"subject": subject, "predicate": predicate, "object": cls}


def parse(doc):
    return OntologyEngine().parse(json.dumps(doc))


def consistency(**overrides):
    section = {
        "classMembershipPredicate": "rdfType",
        "disjointClasses": [],
        "functionalProperties": [],
    }
    section.update(overrides)
    return section


class ConsistencyStructureTests(unittest.TestCase):
    def test_without_consistency_returns_model(self):
        model = parse(base_doc())
        self.assertIsInstance(model, OntologyModel)

    def test_consistency_must_be_object(self):
        with self.assertRaises(OntologyError) as ctx:
            parse(base_doc(consistency=[]))
        self.assertIn("consistency", str(ctx.exception))

    def test_consistency_missing_and_extra_keys(self):
        with self.assertRaises(OntologyError) as ctx:
            parse(base_doc(consistency={"classMembershipPredicate": "rdfType"}))
        msg = str(ctx.exception)
        self.assertIn("disjointClasses", msg)
        self.assertIn("functionalProperties", msg)

        with self.assertRaises(OntologyError) as ctx:
            parse(
                base_doc(
                    consistency={
                        "classMembershipPredicate": "rdfType",
                        "disjointClasses": [],
                        "functionalProperties": [],
                        "extra": 1,
                    }
                )
            )
        self.assertIn("extra", str(ctx.exception))

    def test_membership_predicate_must_be_nonempty_declared_string(self):
        with self.assertRaises(OntologyError) as ctx:
            parse(base_doc(consistency=consistency(classMembershipPredicate="")))
        self.assertIn("classMembershipPredicate", str(ctx.exception))

        with self.assertRaises(OntologyError) as ctx:
            parse(base_doc(consistency=consistency(classMembershipPredicate=7)))
        self.assertIn("classMembershipPredicate", str(ctx.exception))

        with self.assertRaises(OntologyError) as ctx:
            parse(base_doc(consistency=consistency(classMembershipPredicate="nope")))
        self.assertIn("nope", str(ctx.exception))

    def test_constraint_arrays_must_be_lists(self):
        with self.assertRaises(OntologyError) as ctx:
            parse(base_doc(consistency=consistency(disjointClasses={})))
        self.assertIn("disjointClasses", str(ctx.exception))

        with self.assertRaises(OntologyError) as ctx:
            parse(base_doc(consistency=consistency(functionalProperties=1)))
        self.assertIn("functionalProperties", str(ctx.exception))

    def test_disjoint_item_shape_and_location(self):
        with self.assertRaises(OntologyError) as ctx:
            parse(base_doc(consistency=consistency(disjointClasses=[1])))
        self.assertIn("disjointClasses[0]", str(ctx.exception))

        bad = {"id": "d1"}  # 缺少 classes
        with self.assertRaises(OntologyError) as ctx:
            parse(base_doc(consistency=consistency(disjointClasses=[bad])))
        msg = str(ctx.exception)
        self.assertIn("disjointClasses[0]", msg)
        self.assertIn("classes", msg)

        bad = {"id": "d1", "classes": ["Cat", "Dog"], "extra": 1}
        with self.assertRaises(OntologyError):
            parse(base_doc(consistency=consistency(disjointClasses=[bad])))

    def test_disjoint_id_rules(self):
        for bad_id in (True, 1.5, None, ["d"]):
            with self.assertRaises(OntologyError) as ctx:
                parse(
                    base_doc(
                        consistency=consistency(
                            disjointClasses=[{"id": bad_id, "classes": ["Cat", "Dog"]}]
                        )
                    )
                )
            self.assertIn("disjointClasses[0]", str(ctx.exception))

        with self.assertRaises(OntologyError) as ctx:
            parse(
                base_doc(
                    consistency=consistency(
                        disjointClasses=[{"id": "", "classes": ["Cat", "Dog"]}]
                    )
                )
            )
            self.assertIn("id", str(ctx.exception))

    def test_classes_requirements(self):
        def doc_with(classes):
            return base_doc(
                consistency=consistency(
                    disjointClasses=[{"id": "d1", "classes": classes}]
                )
            )

        with self.assertRaises(OntologyError) as ctx:
            parse(doc_with(["Cat"]))
        self.assertIn("至少", str(ctx.exception))

        with self.assertRaises(OntologyError) as ctx:
            parse(doc_with(["Cat", "Cat"]))
        self.assertIn("重复", str(ctx.exception))

        with self.assertRaises(OntologyError) as ctx:
            parse(doc_with(["Cat", ""]))
        self.assertIn("classes[1]", str(ctx.exception))

        with self.assertRaises(OntologyError) as ctx:
            parse(doc_with(["Cat", "Ghost"]))
        self.assertIn("Ghost", str(ctx.exception))
        self.assertIn("未声明", str(ctx.exception))

        with self.assertRaises(OntologyError) as ctx:
            parse(
                base_doc(
                    consistency=consistency(
                        disjointClasses=[{"id": "d1", "classes": "Cat Dog"}]
                    )
                )
            )
        self.assertIn("classes", str(ctx.exception))

    def test_functional_item_shape_and_property(self):
        with self.assertRaises(OntologyError) as ctx:
            parse(base_doc(consistency=consistency(functionalProperties=[{}])))
        msg = str(ctx.exception)
        self.assertIn("functionalProperties[0]", msg)
        self.assertIn("id", msg)
        self.assertIn("property", msg)

        with self.assertRaises(OntologyError) as ctx:
            parse(
                base_doc(
                    consistency=consistency(
                        functionalProperties=[{"id": "f1", "property": ""}]
                    )
                )
            )
        self.assertIn("property", str(ctx.exception))

        with self.assertRaises(OntologyError) as ctx:
            parse(
                base_doc(
                    consistency=consistency(
                        functionalProperties=[{"id": "f1", "property": "ghost"}]
                    )
                )
            )
        self.assertIn("ghost", str(ctx.exception))

    def test_ids_unique_across_both_arrays(self):
        section = consistency(
            disjointClasses=[{"id": "same", "classes": ["Cat", "Dog"]}],
            functionalProperties=[{"id": "same", "property": "knows"}],
        )
        with self.assertRaises(OntologyError) as ctx:
            parse(base_doc(consistency=section))
        self.assertIn("same", str(ctx.exception))
        self.assertIn("重复", str(ctx.exception))

    def test_integer_id_accepted(self):
        section = consistency(
            disjointClasses=[{"id": 7, "classes": ["Cat", "Dog"]}]
        )
        model = parse(base_doc(consistency=section))
        self.assertIsInstance(model, OntologyModel)

    def test_structural_error_is_ontology_error_not_inconsistency(self):
        # 结构错误必须是 OntologyError 而不能误报为语义冲突
        with self.assertRaises(OntologyError):
            parse(base_doc(consistency=consistency(classMembershipPredicate="x")))
        with self.assertRaises(OntologyError):
            parse(base_doc(consistency=consistency(classMembershipPredicate="x")))


class DisjointClassTests(unittest.TestCase):
    def disjoint_doc(self, triples, cid="d1", classes=("Cat", "Dog")):
        return base_doc(
            triples=triples,
            consistency=consistency(
                disjointClasses=[{"id": cid, "classes": list(classes)}]
            ),
        )

    def test_explicit_explicit_conflict_raises_and_no_model(self):
        doc = self.disjoint_doc(
            [member("tom", "Cat"), member("tom", "Dog")]
        )
        with self.assertRaises(InconsistencyError) as ctx:
            parse(doc)
        msg = str(ctx.exception)
        self.assertIn("d1", msg)
        self.assertIn("disjointClasses", msg)
        self.assertIn("tom", msg)
        self.assertIn("Cat", msg)
        self.assertIn("Dog", msg)
        self.assertIn("显式事实", msg)
        self.assertIsInstance(ctx.exception, OntologyError)

    def test_same_class_twice_is_not_conflict(self):
        doc = self.disjoint_doc(
            [member("tom", "Cat"), member("tom", "Cat")]
        )
        self.assertIsInstance(parse(doc), OntologyModel)

    def test_membership_only_among_declared_individual_and_class(self):
        # object 不是已声明类：忽略
        doc = self.disjoint_doc(
            [member("tom", "Cat"), member("tom", "Ghost")]
        )
        self.assertIsInstance(parse(doc), OntologyModel)

        # subject 不是已声明个体：忽略（即使 object 都是类）
        doc = self.disjoint_doc(
            [member("ghost", "Cat"), member("ghost", "Dog")]
        )
        self.assertIsInstance(parse(doc), OntologyModel)

    def test_other_predicate_not_membership(self):
        doc = self.disjoint_doc(
            [
                {"subject": "tom", "predicate": "knows", "object": "Cat"},
                {"subject": "tom", "predicate": "knows", "object": "Dog"},
            ]
        )
        self.assertIsInstance(parse(doc), OntologyModel)

    def test_derived_membership_conflict_with_rule_source(self):
        rule = {
            "id": "infer-dog",
            "if": [{"subject": "?x", "predicate": "knows", "object": "carol"}],
            "then": [member("?x", "Dog")],
        }
        doc = self.disjoint_doc(
            [member("tom", "Cat"), {"subject": "tom", "predicate": "knows", "object": "carol"}],
        )
        doc["rules"] = [rule]
        with self.assertRaises(InconsistencyError) as ctx:
            parse(doc)
        msg = str(ctx.exception)
        self.assertIn("infer-dog", msg)
        self.assertIn("显式事实", msg)

    def test_three_classes_emits_pairwise_conflicts_sorted(self):
        doc = base_doc(
            triples=[
                member("tom", "Cat"),
                member("tom", "Dog"),
                member("tom", "Animal"),
            ],
            consistency=consistency(
                disjointClasses=[
                    {"id": "d1", "classes": ["Animal", "Cat", "Dog"]}
                ]
            ),
        )
        with self.assertRaises(InconsistencyError) as ctx:
            parse(doc)
        msg = str(ctx.exception)
        self.assertIn("3 处", msg)
        # 类对按字典序列出：Animal/Cat, Animal/Dog, Cat/Dog
        self.assertLess(msg.index("'Animal'"), msg.index("'Cat'"))
        pair_cat_dog = msg.index("'Cat' 与 'Dog'")
        self.assertGreater(pair_cat_dog, msg.index("'Animal' 与 'Dog'"))

    def test_no_conflict_when_classes_belong_to_different_constraints(self):
        doc = base_doc(
            triples=[member("tom", "Cat"), member("tom", "Person")],
            consistency=consistency(
                disjointClasses=[
                    {"id": "d1", "classes": ["Cat", "Dog"]},
                    {"id": "d2", "classes": ["Person", "Student"]},
                ]
            ),
        )
        self.assertIsInstance(parse(doc), OntologyModel)


class FunctionalPropertyTests(unittest.TestCase):
    def functional_doc(self, triples, cid="f1", property_name="mother"):
        return base_doc(
            triples=triples,
            consistency=consistency(
                functionalProperties=[{"id": cid, "property": property_name}]
            ),
        )

    def test_two_distinct_objects_conflict(self):
        doc = self.functional_doc(
            [
                {"subject": "alice", "predicate": "mother", "object": "carol"},
                {"subject": "alice", "predicate": "mother", "object": "bob"},
            ]
        )
        with self.assertRaises(InconsistencyError) as ctx:
            parse(doc)
        msg = str(ctx.exception)
        self.assertIn("f1", msg)
        self.assertIn("functionalProperties", msg)
        self.assertIn("alice", msg)
        self.assertIn("mother", msg)
        # object 按字典序列出：bob 在 carol 之前
        self.assertLess(msg.index("'bob'"), msg.index("'carol'"))
        self.assertIn("显式事实", msg)

    def test_identical_triple_not_conflict(self):
        t = {"subject": "alice", "predicate": "mother", "object": "carol"}
        doc = self.functional_doc([t, t])
        self.assertIsInstance(parse(doc), OntologyModel)

    def test_different_subjects_not_conflict(self):
        doc = self.functional_doc(
            [
                {"subject": "alice", "predicate": "mother", "object": "carol"},
                {"subject": "bob", "predicate": "mother", "object": "carol"},
            ]
        )
        self.assertIsInstance(parse(doc), OntologyModel)

    def test_derived_object_conflict_names_rule(self):
        rule = {
            "id": "infer-mother",
            "if": [{"subject": "?x", "predicate": "knows", "object": "carol"}],
            "then": [{"subject": "?x", "predicate": "mother", "object": "carol"}],
        }
        doc = self.functional_doc(
            [
                {"subject": "alice", "predicate": "mother", "object": "bob"},
                {"subject": "alice", "predicate": "knows", "object": "carol"},
            ]
        )
        doc["rules"] = [rule]
        with self.assertRaises(InconsistencyError) as ctx:
            parse(doc)
        msg = str(ctx.exception)
        self.assertIn("infer-mother", msg)
        self.assertIn("显式事实", msg)

    def test_other_property_not_affected(self):
        doc = self.functional_doc(
            [
                {"subject": "alice", "predicate": "knows", "object": "bob"},
                {"subject": "alice", "predicate": "knows", "object": "carol"},
            ]
        )
        self.assertIsInstance(parse(doc), OntologyModel)


class ConflictOrderingTests(unittest.TestCase):
    def test_conflicts_sorted_by_id_type_subject(self):
        doc = base_doc(
            triples=[
                member("tom", "Cat"),
                member("tom", "Dog"),
                member("alice", "Cat"),
                member("alice", "Dog"),
                {"subject": "bob", "predicate": "mother", "object": "alice"},
                {"subject": "bob", "predicate": "mother", "object": "carol"},
            ],
            consistency=consistency(
                disjointClasses=[{"id": "d9", "classes": ["Cat", "Dog"]}],
                functionalProperties=[{"id": "f1", "property": "mother"}],
            ),
        )
        with self.assertRaises(InconsistencyError) as ctx:
            parse(doc)
        msg = str(ctx.exception)
        # 字典序：'d9' < 'f1'，disjointClasses 整体在前；同约束内 alice < tom
        self.assertLess(msg.index("d9"), msg.index("f1"))
        self.assertLess(msg.index("'alice'"), msg.index("'tom'"))
        self.assertIn("3 处", msg)

    def test_integer_ids_ordered_by_string_representation(self):
        # str(10) = "10" 字典序先于 str(9) = "9"
        doc = base_doc(
            triples=[
                member("tom", "Cat"),
                member("tom", "Dog"),
                {"subject": "bob", "predicate": "mother", "object": "alice"},
                {"subject": "bob", "predicate": "mother", "object": "carol"},
            ],
            consistency=consistency(
                disjointClasses=[{"id": 9, "classes": ["Cat", "Dog"]}],
                functionalProperties=[{"id": 10, "property": "mother"}],
            ),
        )
        with self.assertRaises(InconsistencyError) as ctx:
            parse(doc)
        msg = str(ctx.exception)
        self.assertLess(msg.index("10"), msg.index(" 9"))

    def test_message_stable_across_parses(self):
        doc = base_doc(
            triples=[
                member("tom", "Cat"),
                member("tom", "Dog"),
                {"subject": "bob", "predicate": "mother", "object": "alice"},
                {"subject": "bob", "predicate": "mother", "object": "carol"},
            ],
            consistency=consistency(
                disjointClasses=[{"id": "d1", "classes": ["Cat", "Dog"]}],
                functionalProperties=[{"id": "f1", "property": "mother"}],
            ),
        )
        text = json.dumps(doc)
        messages = []
        for _ in range(3):
            try:
                OntologyEngine().parse(text)
                self.fail("应当抛出 InconsistencyError")
            except InconsistencyError as exc:
                messages.append(str(exc))
        self.assertEqual(len(set(messages)), 1)

    def test_bytes_input_supported(self):
        doc = base_doc(
            triples=[member("tom", "Cat"), member("tom", "Dog")],
            consistency=consistency(
                disjointClasses=[{"id": "d1", "classes": ["Cat", "Dog"]}]
            ),
        )
        with self.assertRaises(InconsistencyError):
            OntologyEngine().parse(json.dumps(doc).encode("utf-8"))


class NoConflictUnchangedTests(unittest.TestCase):
    def test_consistency_present_no_conflict_behaviour_unchanged(self):
        rule = {
            "id": "infer-cat",
            "if": [{"subject": "?x", "predicate": "knows", "object": "carol"}],
            "then": [member("?x", "Cat")],
        }
        doc = base_doc(
            triples=[
                member("tom", "Cat"),
                {"subject": "alice", "predicate": "knows", "object": "carol"},
                {"subject": "alice", "predicate": "mother", "object": "carol"},
            ],
            rules=[rule],
            consistency=consistency(
                disjointClasses=[{"id": "d1", "classes": ["Cat", "Dog"]}],
                functionalProperties=[{"id": "f1", "property": "mother"}],
            ),
        )
        model = parse(doc)
        self.assertTrue(model.entails("alice", "rdfType", "Cat"))
        self.assertEqual(model.source_rule("alice", "rdfType", "Cat"), "infer-cat")
        self.assertIsNone(model.source_rule("alice", "mother", "carol"))
        self.assertEqual(
            model.explicit_triples,
            tuple(sorted(model.explicit_triples)),
        )
        self.assertEqual(
            model.triples,
            tuple(sorted(model.triples)),
        )
        result = model.query("SELECT ?x WHERE { ?x rdfType Cat }")
        self.assertEqual(result.rows, (("alice",), ("tom",),))

    def test_derived_membership_duplicates_explicit_no_new_fact(self):
        # 推理出与显式相同的成员事实不产生重复，也不构成冲突
        rule = {
            "id": "again-cat",
            "if": [{"subject": "?x", "predicate": "knows", "object": "carol"}],
            "then": [member("?x", "Cat")],
        }
        doc = base_doc(
            triples=[
                member("tom", "Cat"),
                {"subject": "tom", "predicate": "knows", "object": "carol"},
            ],
            rules=[rule],
            consistency=consistency(
                disjointClasses=[{"id": "d1", "classes": ["Cat", "Dog"]}]
            ),
        )
        model = parse(doc)
        self.assertEqual(
            [d.triple for d in model.derived_triples if d.triple.predicate == "rdfType"],
            [],
        )


if __name__ == "__main__":
    unittest.main()
