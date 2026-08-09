from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from services.text_service import (  # noqa: E402
    MIN_REWRITE_DIFFERENCE,
    REWRITE_COMPRESSION_WARNING_RATIO,
    RewriteGenerationError,
    apply_rewrite_fact_coverage_quality,
    build_rewrite_analysis_prompt,
    build_rewrite_prompt,
    compare_scripts,
    cover_title_rejection_reasons,
    cover_title_needs_rewrite,
    ensure_original_opening,
    ensure_rewrite_book_promotion,
    extract_leading_title,
    fallback_publish_assistant,
    fallback_infer_title,
    extract_opening_hook,
    fallback_rewrite_fact_brief,
    generate_publish_assistant,
    generate_viral_title,
    generate_shots,
    keywords_from_text,
    normalize_auto_title,
    normalize_rewrite_fact_brief,
    normalize_rewrite_fact_coverage,
    parse_rewrite_analysis_json,
    parse_title_candidates,
    request_minimax_rewrite_fact_coverage,
    request_minimax_rewrite_analysis,
    rewrite_script,
    rewrite_script_with_minimax,
    split_script_into_storyboards,
)


class PublishAssistantTests(unittest.TestCase):
    def test_generated_short_title_is_not_hard_truncated(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({
                    "choices": [{
                        "message": {
                            "content": json.dumps({
                                "short_title": "他隐姓埋名二十年只为守住中国的秘密",
                                "description": "他把名字藏进岁月，把选择留给国家。那些无人知晓的坚持，最终变成了守护无数人的力量，也让今天的我们重新看见沉默背后的重量。",
                                "tags": ["无名英雄", "家国情怀", "人物故事", "致敬先辈"],
                            }, ensure_ascii=False)
                        }
                    }]
                }, ensure_ascii=False).encode("utf-8")

        with patch.dict("os.environ", {"MINIMAX_API_KEY": "test"}), patch(
            "services.text_service.urllib.request.urlopen",
            return_value=FakeResponse(),
        ):
            result = generate_publish_assistant("他隐姓埋名二十年，只为守住中国的秘密。")

        self.assertEqual("他隐姓埋名二十年只为守住中国的秘密", result["short_title"])
        self.assertTrue(result["description"].endswith("#无名英雄 #家国情怀 #人物故事 #致敬先辈"))
        self.assertEqual(4, len(re.findall(r"#[^#\s]+", result["description"])))

    def test_fallback_short_title_keeps_the_complete_first_sentence(self) -> None:
        result = fallback_publish_assistant("他隐姓埋名二十年，只为守住中国的秘密。后来人们才知道他的名字。")

        self.assertEqual("他隐姓埋名二十年只为守住中国的秘密", result["short_title"])
        self.assertEqual(4, len(re.findall(r"#[^#\s]+", result["description"])))


class ShotTagGenerationTests(unittest.TestCase):
    def test_ai_rewrite_failure_does_not_fall_back_to_source_copy(self) -> None:
        source = "This source must never be returned as an AI rewrite fallback."
        with patch.dict("os.environ", {"MINIMAX_API_KEY": "test"}), patch(
            "services.text_service.rewrite_script_with_minimax",
            side_effect=ValueError("invalid analysis JSON"),
        ), patch(
            "services.text_service.fallback_rewrite_script",
        ) as fallback:
            with self.assertRaises(RewriteGenerationError) as raised:
                rewrite_script(source)

        fallback.assert_not_called()
        self.assertIn("rewrite pipeline", raised.exception.detail)
        self.assertIn("invalid analysis JSON", raised.exception.detail)

    def test_two_stage_rewrite_reports_analysis_failure_stage(self) -> None:
        with patch(
            "services.text_service.request_minimax_rewrite_analysis",
            side_effect=ValueError("truncated JSON"),
        ):
            with self.assertRaises(RewriteGenerationError) as raised:
                rewrite_script_with_minimax("source text", "documentary", "test-key")

        self.assertIn("source analysis", raised.exception.detail)
        self.assertIn("truncated JSON", raised.exception.detail)

    def test_two_stage_rewrite_reports_draft_attempt_failure_stage(self) -> None:
        fact_brief = {"facts": ["fact"], "material_cards": [{"detail": "detail"}]}
        with patch(
            "services.text_service.request_minimax_rewrite_analysis",
            return_value=fact_brief,
        ), patch(
            "services.text_service.request_minimax_rewrite",
            side_effect=TimeoutError("request timed out"),
        ):
            with self.assertRaises(RewriteGenerationError) as raised:
                rewrite_script_with_minimax("source text", "documentary", "test-key")

        self.assertIn("draft generation candidates", raised.exception.detail)
        self.assertIn("候选稿 1", raised.exception.detail)
        self.assertIn("request timed out", raised.exception.detail)

    def test_source_analysis_retries_read_timeout_and_reports_attempts(self) -> None:
        with patch(
            "services.text_service.urllib.request.urlopen",
            side_effect=TimeoutError("The read operation timed out"),
        ) as urlopen:
            with self.assertRaises(RuntimeError) as raised:
                request_minimax_rewrite_analysis("source text", "test-key")

        self.assertEqual(2, urlopen.call_count)
        self.assertIn("timed out after 2 requests", str(raised.exception))
        self.assertIn("120s timeout each", str(raised.exception))

    def test_source_analysis_repairs_missing_json_comma(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({
                    "choices": [{
                        "message": {
                            "content": '{"timeline": [], "facts": ["fact"] "material_cards": []}'
                        }
                    }]
                }).encode("utf-8")

        with patch(
            "services.text_service.urllib.request.urlopen",
            return_value=FakeResponse(),
        ) as urlopen:
            brief = request_minimax_rewrite_analysis("source text", "test-key")

        self.assertEqual(2, urlopen.call_count)
        self.assertEqual(["fact"], brief["facts"])
        self.assertIn("事实底稿仍不完整", brief["analysis_warning"])

    def test_source_analysis_uses_fallback_cards_after_unparseable_json(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({
                    "choices": [{"message": {"content": "not json at all"}}]
                }).encode("utf-8")

        with patch(
            "services.text_service.urllib.request.urlopen",
            return_value=FakeResponse(),
        ) as urlopen:
            brief = request_minimax_rewrite_analysis(
                "曹操，率军北上。此后，他继续推进部署。",
                "test-key",
            )

        self.assertEqual(2, urlopen.call_count)
        self.assertTrue(brief["material_cards"])
        self.assertEqual(["曹操"], brief["protagonists"])
        self.assertFalse(brief["timeline_verified"])
        self.assertNotIn("曹操，率军北上。", brief["material_cards"][0]["fact"])
        self.assertIn("系统已按原文段落生成保底资料卡继续二创", brief["analysis_warning"])

    def test_fallback_cards_do_not_mark_every_background_chunk_as_must(self) -> None:
        source = (
            "固定开头。"
            + "这是普通背景资料，没有发生关键转折。" * 30
            + "后来他决定回国，并完成了关键任务。"
            + "此后还有一些普通履历和背景介绍。" * 30
            + "最终项目成功。"
        )

        brief = fallback_rewrite_fact_brief(source, ValueError("bad json"), "固定开头。")
        priorities = [card["priority"] for card in brief["material_cards"]]

        self.assertIn("must", priorities)
        self.assertIn("support", priorities)

    def test_source_analysis_retries_an_underdense_fact_brief(self) -> None:
        class FakeResponse:
            def __init__(self, content: dict):
                self.content = content

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({
                    "choices": [{"message": {"content": json.dumps(self.content, ensure_ascii=False)}}]
                }).encode("utf-8")

        sparse = {"material_cards": ["只有一条事实"], "section_plan": []}
        dense = {
            "core_subject": "测试人物｜科学家",
            "protagonists": ["测试人物"],
            "protagonist_relationship": "本篇主人公，一位科学家",
            "material_cards": [
                "01｜早年｜测试人物｜发生第一件事",
                "02｜青年｜测试人物｜完成第二件事",
                "03｜中年｜测试人物｜应对第三件事",
                "04｜晚年｜测试人物｜得到最终结果",
            ],
            "section_plan": [
                {"task": "交代人物", "cards": [1]},
                {"task": "推进冲突", "cards": [2]},
                {"task": "写出应对", "cards": [3]},
                {"task": "完成收束", "cards": [4]},
            ],
        }

        with patch(
            "services.text_service.urllib.request.urlopen",
            side_effect=[FakeResponse(sparse), FakeResponse(dense)],
        ) as urlopen:
            brief = request_minimax_rewrite_analysis("这是需要完整拆解的原文。", "test-key")

        self.assertEqual(2, urlopen.call_count)
        self.assertTrue(brief["fact_coverage_passed"])
        self.assertEqual("测试人物｜科学家", brief["core_subject"])
        self.assertEqual(["测试人物"], brief["protagonists"])

    def test_fact_brief_requires_explicit_protagonist_names(self) -> None:
        brief = normalize_rewrite_fact_brief({
            "core_subject": "宋氏三姐妹",
            "material_cards": ["01｜早年｜三姐妹出生"],
            "section_plan": [{"task": "一"}, {"task": "二"}, {"task": "三"}, {"task": "四"}],
        })

        self.assertFalse(brief["protagonist_identity_passed"])
        self.assertFalse(brief["fact_coverage_passed"])

    def test_single_event_source_does_not_need_cards_based_on_character_count(self) -> None:
        brief = normalize_rewrite_fact_brief({
            "core_subject": "测试人物",
            "protagonists": ["测试人物"],
            "material_cards": [{
                "id": 1,
                "priority": "must",
                "time": "某年",
                "person": "测试人物",
                "fact": "全文只讲这一件独立事件",
            }],
            "section_plan": [{"task": "完整讲述这一事件", "cards": [1]}],
        }, raw_length=3000)

        self.assertEqual(4, brief["minimum_fact_items"])
        self.assertEqual(0, brief["minimum_material_length"])
        self.assertEqual(1, brief["minimum_section_count"])
        self.assertTrue(brief["fact_coverage_passed"])

    def test_material_card_priority_is_initialized_without_section_plan_priority(self) -> None:
        brief = normalize_rewrite_fact_brief({
            "protagonists": ["测试人物"],
            "material_cards": [{
                "id": 1,
                "time": "某年",
                "person": "测试人物",
                "fact": "完成关键任务",
            }],
            "section_plan": [{"task": "完整讲述", "cards": [1]}],
        })

        self.assertEqual("must", brief["material_cards"][0]["priority"])
        self.assertEqual("support", brief["material_cards"][0]["expansion_level"])
        self.assertNotIn("priority", brief["section_plan"][0])

    def test_many_structured_cards_cannot_all_default_to_must(self) -> None:
        cards = [
            {"id": index, "priority": "must", "fact": f"事实{index}"}
            for index in range(1, 7)
        ]
        brief = normalize_rewrite_fact_brief({
            "protagonists": ["测试人物"],
            "material_cards": cards,
            "section_plan": [
                {"task": "一", "cards": [1, 2]},
                {"task": "二", "cards": [3]},
                {"task": "三", "cards": [4]},
                {"task": "四", "cards": [5, 6]},
            ],
        }, raw_length=1200)

        self.assertFalse(brief["priority_balance_passed"])
        self.assertTrue(brief["fact_coverage_passed"])

    def test_material_cards_cannot_all_expand_as_focus_scenes(self) -> None:
        brief = normalize_rewrite_fact_brief({
            "protagonists": ["测试人物"],
            "material_cards": [
                {"id": index, "priority": "mergeable", "expansion_level": "focus", "fact": f"事实{index}"}
                for index in range(1, 5)
            ],
            "section_plan": [{"task": "起点", "cards": [1, 2]}, {"task": "结果", "cards": [3, 4]}],
            "narrative_angles": [
                {"strategy": "关键选择", "focus_cards": [2], "guidance": "突出选择"},
                {"strategy": "实际代价", "focus_cards": [3], "guidance": "突出代价"},
                {"strategy": "行动现场", "focus_cards": [1], "guidance": "突出行动"},
            ],
        }, raw_length=500)

        self.assertFalse(brief["expansion_balance_passed"])
        self.assertTrue(brief["fact_coverage_passed"])
        self.assertEqual("关键选择", brief["narrative_angles"][0]["strategy"])

    def test_section_plan_counts_story_stages_instead_of_fact_cards(self) -> None:
        brief = normalize_rewrite_fact_brief({
            "protagonists": ["测试人物"],
            "material_cards": [
                {"id": index, "priority": "mergeable", "fact": f"事实{index}"}
                for index in range(1, 5)
            ],
            "section_plan": [
                {"task": "起点", "cards": [1, 2]},
                {"task": "发展与结果", "cards": [3, 4]},
            ],
        }, raw_length=500)

        self.assertEqual(2, brief["minimum_section_count"])
        self.assertTrue(brief["section_plan_passed"])

    def test_legacy_emotional_fields_are_compacted_during_normalization(self) -> None:
        brief = normalize_rewrite_fact_brief({
            "protagonists": ["测试人物"],
            "viral_analysis": {"hook": "旧钩子结论", "completion": "推进事件"},
            "material_cards": [{
                "id": 1,
                "priority": "must",
                "fact": "关键事实",
                "emotional_stakes": "付出代价",
                "relationship_change": "",
                "emotional_beat": "心疼",
            }],
            "section_plan": [{"task": "完整讲述", "cards": [1], "emotional_beat": "敬佩"}],
        })

        self.assertEqual("旧钩子结论", brief["viral_analysis"]["opening_continuation"])
        self.assertNotIn("hook", brief["viral_analysis"])
        self.assertNotIn("emotional_beat", brief["material_cards"][0])
        self.assertNotIn("relationship_change", brief["material_cards"][0])
        self.assertNotIn("emotional_beat", brief["section_plan"][0])
        self.assertEqual({"card": 1, "beat": "敬佩"}, brief["emotional_arc"][0])

    def test_analysis_json_repairs_missing_comma_before_next_key(self) -> None:
        parsed = parse_rewrite_analysis_json(
            '{"core_subject":"测试人物" "material_cards":[{"id":1,"fact":"关键事实"}]}'
        )

        self.assertEqual("测试人物", parsed["core_subject"])
        self.assertEqual(1, parsed["material_cards"][0]["id"])

    def test_analysis_json_repairs_unescaped_quotes_inside_string(self) -> None:
        parsed = parse_rewrite_analysis_json(
            '{"material_cards":[{"id":1,"details":"他说"马上回家"并立即出发"}]}'
        )

        self.assertEqual('他说"马上回家"并立即出发', parsed["material_cards"][0]["details"])

    def test_analysis_json_repairs_missing_comma_between_array_items(self) -> None:
        parsed = parse_rewrite_analysis_json('{"verified_quotes":["第一句" "第二句"]}')

        self.assertEqual(["第一句", "第二句"], parsed["verified_quotes"])

    def test_book_promotion_details_and_verified_quotes_survive_normalization(self) -> None:
        brief = normalize_rewrite_fact_brief({
            "protagonists": ["测试人物"],
            "material_cards": [{"id": 1, "priority": "must", "fact": "关键事实"}],
            "section_plan": [{"task": "完整讲述", "cards": [1]}],
            "verified_quotes": ["我一定要回去。"],
            "emotional_arc": ["好奇", "心疼", "敬佩"],
            "book_promotion": {
                "present": True,
                "original_intent": "让更多英雄被看见",
                "selling_points": ["补充课本外的人物经历"],
                "target_readers": ["家长和孩子"],
                "transition_angle": "从人物选择扩展到一群人",
            },
        })

        self.assertEqual(["我一定要回去。"], brief["verified_quotes"])
        self.assertEqual(["好奇", "心疼", "敬佩"], brief["emotional_arc"])
        self.assertEqual("让更多英雄被看见", brief["book_promotion"]["original_intent"])
        self.assertEqual(["家长和孩子"], brief["book_promotion"]["target_readers"])

    def test_fact_coverage_rejects_partial_and_unreviewed_cards(self) -> None:
        fact_brief = {
            "material_cards": ["第一件重要事实", "第二件重要事实", "第三件重要事实"]
        }
        audit = {
            "covered_cards": [1],
            "partial_cards": [{"card": 2, "missing": "缺少人物采取的动作"}],
            "missing_cards": [],
            "coverage_passed": True,
        }

        coverage = normalize_rewrite_fact_coverage(audit, fact_brief)

        self.assertFalse(coverage["fact_coverage_passed"])
        self.assertEqual([2, 3], [item["card"] for item in coverage["missing_fact_cards"]])
        self.assertEqual("缺少人物采取的动作", coverage["missing_fact_cards"][0]["missing"])

    def test_fact_audit_rejects_out_of_order_timeline(self) -> None:
        fact_brief = {
            "material_cards": ["01｜早年｜第一件事", "02｜中年｜第二件事", "03｜晚年｜最终结果"]
        }
        coverage = normalize_rewrite_fact_coverage({
            "covered_cards": [1, 2, 3],
            "partial_cards": [],
            "missing_cards": [],
            "timeline_order_passed": False,
            "out_of_order_cards": [{
                "card": 3,
                "appears_before": 2,
                "reason": "最终结果提前到中年事件之前",
            }],
        }, fact_brief)
        result = apply_rewrite_fact_coverage_quality({
            "rewrite_comparison": {
                "non_length_quality_passed": True,
                "length_passed": True,
                "length_ratio": 100,
            }
        }, coverage)

        self.assertTrue(coverage["fact_coverage_passed"])
        self.assertFalse(coverage["timeline_order_passed"])
        self.assertFalse(result["rewrite_comparison"]["passed"])

    def test_emotional_focus_is_a_hard_requirement_when_a_card_has_stakes(self) -> None:
        fact_brief = {
            "material_cards": [{
                "id": 1,
                "priority": "must",
                "fact": "人物执行任务",
                "emotional_stakes": "因此错过与家人最后一次见面",
                "relationship_change": "家人长期误解他的选择",
                "emotional_beat": "心疼",
            }]
        }
        coverage = normalize_rewrite_fact_coverage({
            "covered_cards": [1],
            "partial_cards": [],
            "missing_cards": [],
            "timeline_order_passed": True,
            "out_of_order_cards": [],
            "emotional_quality_passed": False,
            "emotional_issues": [{"card": 1, "reason": "只写完成任务，遗漏家庭代价和误解"}],
        }, fact_brief)
        result = apply_rewrite_fact_coverage_quality({
            "rewrite_comparison": {
                "non_length_quality_passed": True,
                "length_passed": True,
                "length_ratio": 100,
            }
        }, coverage)

        self.assertFalse(coverage["emotional_quality_passed"])
        self.assertFalse(result["rewrite_comparison"]["passed"])

    def test_support_cards_may_be_omitted_when_they_do_not_serve_the_core(self) -> None:
        fact_brief = {
            "material_cards": [
                {"id": 1, "priority": "must", "fact": "主人公完成核心任务"},
                {"id": 2, "priority": "support", "fact": "一段辅助履历"},
                {"id": 3, "priority": "discardable", "fact": "一段重复履历"},
            ],
        }

        coverage = normalize_rewrite_fact_coverage({
            "covered_cards": [1],
            "partial_cards": [],
            "missing_cards": [
                {"card": 2, "missing": "成稿省略了辅助履历"},
                {"card": 3, "missing": "成稿省略了重复履历"},
            ],
            "timeline_order_passed": True,
            "out_of_order_cards": [],
        }, fact_brief)

        self.assertTrue(coverage["fact_coverage_passed"])
        self.assertEqual(1, coverage["expected_fact_cards"])
        self.assertEqual([], coverage["missing_fact_cards"])

    def test_unsupported_new_facts_fail_reverse_grounding(self) -> None:
        coverage = normalize_rewrite_fact_coverage({
            "covered_cards": [1],
            "partial_cards": [],
            "missing_cards": [],
            "factual_grounding_passed": False,
            "unsupported_claims": [{"claim": "虚构了一段获奖经历", "reason": "资料卡无依据"}],
            "timeline_order_passed": True,
            "out_of_order_cards": [],
        }, {"material_cards": [{"id": 1, "priority": "must", "fact": "完成核心任务"}]})
        result = apply_rewrite_fact_coverage_quality({
            "rewrite_comparison": {
                "non_length_quality_passed": True,
                "length_passed": True,
                "length_ratio": 100,
            },
        }, coverage)

        self.assertFalse(coverage["factual_grounding_passed"])
        self.assertEqual("虚构了一段获奖经历", coverage["unsupported_claims"][0]["claim"])
        self.assertFalse(result["rewrite_comparison"]["passed"])

    def test_fact_audit_receives_exact_protected_opening(self) -> None:
        captured_payload = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({
                    "choices": [{"message": {"content": json.dumps({
                        "covered_cards": [1],
                        "partial_cards": [],
                        "missing_cards": [],
                        "timeline_order_passed": True,
                        "out_of_order_cards": [],
                    })}}]
                }).encode("utf-8")

        def fake_urlopen(request, **_kwargs):
            captured_payload.update(json.loads(request.data.decode("utf-8")))
            return FakeResponse()

        with patch("services.text_service.urllib.request.urlopen", side_effect=fake_urlopen):
            coverage = request_minimax_rewrite_fact_coverage(
                {"material_cards": [{"id": 1, "priority": "must", "fact": "早年事实"}]},
                "用户选择的两句固定开头。第二句也要保留。正文从早年开始。",
                "test-key",
                "用户选择的两句固定开头。第二句也要保留。",
            )

        audit_prompt = captured_payload["messages"][1]["content"]
        self.assertIn(
            "<protected_opening>用户选择的两句固定开头。第二句也要保留。</protected_opening>",
            audit_prompt,
        )
        self.assertIn("检查完整成稿，包括 protected_opening", audit_prompt)
        self.assertIn("逐张审核 must 卡", audit_prompt)
        self.assertIn("反向检查成稿中的每个事实性陈述", audit_prompt)
        self.assertTrue(coverage["timeline_order_passed"])

    def test_length_ratio_does_not_reject_a_fact_complete_draft(self) -> None:
        self.assertEqual(0, REWRITE_COMPRESSION_WARNING_RATIO)
        result = {
            "rewrite_comparison": {
                "non_length_quality_passed": True,
                "length_ratio": 50,
            }
        }

        checked = apply_rewrite_fact_coverage_quality(result, {
            "fact_coverage_passed": True,
            "covered_fact_cards": [1, 2],
            "expected_fact_cards": 2,
            "missing_fact_cards": [],
        })

        self.assertFalse(checked["rewrite_comparison"]["compression_warning"])
        self.assertTrue(checked["rewrite_comparison"]["length_passed"])
        self.assertTrue(checked["rewrite_comparison"]["passed"])

    def test_project_title_uses_the_first_eight_characters(self) -> None:
        source = "一二三四五六七八九十十一十二"

        self.assertEqual("一二三四五六七八", extract_leading_title(source))
        self.assertEqual("一二三四五六七八", fallback_infer_title(source))
        self.assertEqual("一二三四五六七八", normalize_auto_title("忽略这个标题", source))

    def test_project_title_stops_at_punctuation_before_ten_characters(self) -> None:
        self.assertEqual("谢汉光", extract_leading_title("谢汉光，潜伏台湾四十二年。"))
        self.assertEqual("开头没有标点符超", extract_leading_title("开头没有标点符超过十个字，后文。"))

    def test_project_title_ignores_leading_punctuation_and_whitespace(self) -> None:
        self.assertEqual("他回来了", extract_leading_title("  \n“他回来了！”后文"))
        self.assertEqual("未命名项目", extract_leading_title("，。！？"))

    def test_rewrite_requires_seventy_five_percent_reconstruction(self) -> None:
        self.assertEqual(75, MIN_REWRITE_DIFFERENCE)

    def test_rewrite_prompt_has_no_length_target_or_limit(self) -> None:
        prompt = build_rewrite_prompt("一" * 1000, "纪实故事型", 1, fact_brief={"facts": ["测试事实"]})
        self.assertIn("不设目标字数和篇幅上下限", prompt)
        self.assertIn("不要检查字数", prompt)
        self.assertNotIn("全文不设目标字数", prompt)
        self.assertNotIn("900 到 1100", prompt)

    def test_rewrite_comparison_keeps_length_as_diagnostic_only(self) -> None:
        comparison = compare_scripts("甲" * 2000, "乙" * 800)

        self.assertEqual(40, comparison["length_ratio"])
        self.assertEqual(0, comparison["min_rewritten_length"])
        self.assertEqual(0, comparison["max_rewritten_length"])
        self.assertTrue(comparison["length_passed"])
        self.assertTrue(comparison["passed"])

        boundary = compare_scripts("甲" * 2000, "乙" * 1100)
        self.assertEqual(55, boundary["length_ratio"])
        self.assertTrue(boundary["length_passed"])
        self.assertTrue(boundary["passed"])

    def test_opening_hook_keeps_exactly_the_first_sentence(self) -> None:
        source = "他拒绝了所有人的劝告。因为那个箱子里，藏着不能公开的秘密。后面的故事继续。"

        self.assertEqual("他拒绝了所有人的劝告。", extract_opening_hook(source))

    def test_opening_hook_does_not_truncate_an_overlong_first_sentence(self) -> None:
        source = "美国海关突然扣下他的行李箱，并且封锁了所有消息，因为他们真正害怕的根本不是箱子里的几张纸。后文。"
        hook = extract_opening_hook(source)

        self.assertEqual("美国海关突然扣下他的行李箱，并且封锁了所有消息，因为他们真正害怕的根本不是箱子里的几张纸。", hook)

    def test_opening_hook_supports_user_selected_ranges(self) -> None:
        source = "第一句话很短。第二句话用于测试用户选择的固定开头范围。\n\n这是第二段。"

        self.assertEqual(source[:20], extract_opening_hook(source, "chars_20"))
        self.assertEqual(source[:27], extract_opening_hook(source, "chars_27"))
        self.assertEqual("第一句话很短。", extract_opening_hook(source, "first_sentence"))
        self.assertEqual("第一句话很短。第二句话用于测试用户选择的固定开头范围。", extract_opening_hook(source, "first_paragraph"))

    def test_original_opening_is_forced_back_after_rewrite(self) -> None:
        source = "父亲去世那天，他明明活着，却不能回家奔丧。后面的原文内容。"
        rewritten = "父亲离世时他没有回去。这里是完全改写后的正文。"

        result = ensure_original_opening(source, rewritten)

        self.assertTrue(result.startswith(extract_opening_hook(source)))

    def test_multi_paragraph_fixed_opening_is_not_prepended_twice(self) -> None:
        opening = "第一段固定开头。\n\n第二段继续制造悬念。\n\n第三段揭示核心人物。"
        source = f"{opening}\n\n后面的原始正文。"
        rewritten = "第一段固定开头。\n第二段继续制造悬念。\n第三段揭示核心人物。\n全新正文从这里开始。"

        result = ensure_original_opening(source, rewritten, f"chars_{len(opening)}")

        self.assertEqual(1, result.count("第一段固定开头。"))
        self.assertEqual(1, result.count("第二段继续制造悬念。"))
        self.assertEqual(1, result.count("第三段揭示核心人物。"))

    def test_cover_title_rejects_stiff_titles(self) -> None:
        self.assertTrue(cover_title_needs_rewrite("伟大一生", "民族脊梁"))
        self.assertTrue(cover_title_needs_rewrite("中国科学家", "值得铭记"))
        self.assertTrue(cover_title_needs_rewrite("女儿病危那天", "他死盯绝密图纸"))
        self.assertTrue(cover_title_needs_rewrite("飞机坠毁前", "他护住公文包"))
        self.assertFalse(cover_title_needs_rewrite("父亲去世那天", "他不能回家"))
        self.assertFalse(cover_title_needs_rewrite("美国扣下箱子", "到底怕什么"))
        self.assertFalse(cover_title_needs_rewrite("她捐出千万", "却穿15块鞋"))
        self.assertFalse(cover_title_needs_rewrite("普通老太太", "顶住中国芯片"))
        self.assertTrue(cover_title_needs_rewrite("隐姓埋名二十年", "铸就雷达千里眼"))
        self.assertTrue(cover_title_needs_rewrite("扎根荒原六十年", "为国奉献一生"))
        self.assertFalse(cover_title_needs_rewrite("庆功名单翻遍", "为什么没有他"))
        self.assertTrue(cover_title_needs_rewrite("丈夫被关监狱", "她没先带娃回国"))
        self.assertTrue(cover_title_needs_rewrite("丈夫被关监狱", "她没有先回国"))

    def test_cover_title_rejects_invented_sequence_modifiers(self) -> None:
        script = "丈夫被关进美国监狱，她带着孩子四处奔走营救。"

        self.assertTrue(cover_title_needs_rewrite("丈夫被关监狱", "她先带孩子回国", script))
        self.assertFalse(cover_title_needs_rewrite("丈夫被关监狱", "她四处奔走营救", script))
        self.assertFalse(cover_title_needs_rewrite("普通老先生", "护住公文包", "老人护住了公文包。"))

    def test_cover_title_allows_generic_emotion_as_an_angle(self) -> None:
        script = "妻子告诉他自己回不来了，让他别再等。听完以后，他一直低着头。"

        self.assertTrue(cover_title_needs_rewrite("她说回不来别等了", "他不敢抬头", script))
        reasons = cover_title_rejection_reasons(
            "等了整整四十年",
            "他流下眼泪",
            "他等了整整四十年，终于见面时流下眼泪。",
            "他等了整整四十年，终于见面时流下眼泪",
        )
        self.assertNotIn("包含空洞总结或泛情绪反应", reasons)

    def test_cover_title_accepts_evidence_backed_core_fact_without_forced_suspense(self) -> None:
        script = "他是地下党员，在台湾潜伏42年，为了等妻子一生没有再娶。"
        evidence = "他是地下党员，在台湾潜伏42年，为了等妻子一生没有再娶"

        reasons = cover_title_rejection_reasons("地下党潜伏42年", "一生没有再娶", script, evidence)

        self.assertEqual([], reasons)

    def test_generate_viral_title_accepts_legacy_single_object(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({
                    "choices": [{
                        "message": {
                            "content": json.dumps({
                                "line1": "女儿病危那天",
                                "line2": "死盯绝密图纸",
                            }, ensure_ascii=False)
                        }
                    }]
                }, ensure_ascii=False).encode("utf-8")

        script = "美国海关扣下他的行李，硬说箱子里藏着机密。女儿病危那天，他仍然没有离开岗位。"
        with patch.dict("os.environ", {"MINIMAX_API_KEY": "test"}), patch(
            "services.text_service.urllib.request.urlopen",
            return_value=FakeResponse(),
        ):
            title = generate_viral_title(script)

        self.assertEqual(1, len(title["candidates"]))
        self.assertEqual("女儿病危那天", title["candidates"][0]["line1"])

    def test_generate_viral_title_accepts_json_array_candidates(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({
                    "choices": [{
                        "message": {
                            "content": json.dumps([
                                {"first_line": "她捐出千万", "second_line": "却穿15块鞋", "style": "反差型", "evidence_quote": "她捐出1000万，自己却穿15块钱胶鞋"},
                                {"first_line": "伟大一生", "second_line": "民族脊梁", "style": "亏欠型", "evidence_quote": "她捐出1000万，自己却穿15块钱胶鞋"},
                            ], ensure_ascii=False)
                        }
                    }]
                }, ensure_ascii=False).encode("utf-8")

        with patch.dict("os.environ", {"MINIMAX_API_KEY": "test"}), patch(
            "services.text_service.urllib.request.urlopen",
            return_value=FakeResponse(),
        ):
            title = generate_viral_title("她捐出1000万，自己却穿15块钱胶鞋。")

        self.assertEqual(2, len(title["candidates"]))
        self.assertEqual("她捐出千万", title["candidates"][0]["line1"])
        self.assertEqual("却穿15块鞋", title["candidates"][0]["line2"])
        self.assertEqual("伟大一生", title["candidates"][1]["line1"])
        self.assertNotIn("score", title["candidates"][0])

    def test_generate_viral_title_returns_all_candidates_without_word_bank_warnings(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({
                    "choices": [{
                        "message": {
                            "content": json.dumps([
                                {"first_line": "丈夫被关监狱", "second_line": "她没先带娃回国", "style": "悬念型", "evidence_quote": "丈夫被关进美国监狱，她带着孩子四处奔走营救"},
                                {"first_line": "丈夫被关监狱", "second_line": "她四处奔走营救", "style": "冲突型", "evidence_quote": "丈夫被关进美国监狱，她带着孩子四处奔走营救"},
                            ], ensure_ascii=False)
                        }
                    }]
                }, ensure_ascii=False).encode("utf-8")

        script = "丈夫被关进美国监狱，她带着孩子四处奔走营救。"
        with patch.dict("os.environ", {"MINIMAX_API_KEY": "test"}), patch(
            "services.text_service.urllib.request.urlopen",
            return_value=FakeResponse(),
        ):
            title = generate_viral_title(script)

        self.assertEqual(2, len(title["candidates"]))
        self.assertEqual("她没先带娃回国", title["candidates"][0]["line2"])
        self.assertNotIn("warnings", title["candidates"][0])
        self.assertEqual("她四处奔走营救", title["candidates"][1]["line2"])

    def test_generate_viral_title_keeps_complete_long_lines(self) -> None:
        long_line1 = "他在最困难的时候仍然选择回到祖国继续研究"
        long_line2 = "这项选择后来改变了整个工程的命运"

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({
                    "choices": [{
                        "message": {
                            "content": json.dumps([{
                                "first_line": long_line1,
                                "second_line": long_line2,
                                "style": "事实型",
                                "evidence_quote": "他在最困难的时候仍然选择回到祖国继续研究，这项选择后来改变了整个工程的命运",
                            }], ensure_ascii=False)
                        }
                    }]
                }, ensure_ascii=False).encode("utf-8")

        with patch.dict("os.environ", {"MINIMAX_API_KEY": "test"}), patch(
            "services.text_service.urllib.request.urlopen",
            return_value=FakeResponse(),
        ):
            result = generate_viral_title(
                "他在最困难的时候仍然选择回到祖国继续研究，这项选择后来改变了整个工程的命运。"
            )

        self.assertEqual(long_line1, result["candidates"][0]["line1"])
        self.assertEqual(long_line2, result["candidates"][0]["line2"])

    def test_parse_title_candidates_reads_json_array(self) -> None:
        candidates = parse_title_candidates('[{"first_line":"美国扣下箱子","second_line":"到底怕什么","style":"悬念型"}]')

        self.assertEqual("美国扣下箱子", candidates[0]["first_line"])

    def test_parse_title_candidates_accepts_fenced_wrapped_object(self) -> None:
        content = """Here are the candidates:
```json
{"candidates":[{"first_line":"她捐出1000万","second_line":"自己只穿15元胶鞋","style":"反差型"}]}
```"""

        candidates = parse_title_candidates(content)

        self.assertEqual(1, len(candidates))
        self.assertEqual("她捐出1000万", candidates[0]["first_line"])

    def test_parse_title_candidates_accepts_single_candidate_object(self) -> None:
        candidates = parse_title_candidates(
            '{"first_line":"守了整整30年","second_line":"他从未离开岗位","style":"事实型"}'
        )

        self.assertEqual("他从未离开岗位", candidates[0]["second_line"])

    def test_generate_viral_title_retries_after_malformed_json(self) -> None:
        class FakeResponse:
            def __init__(self, content: str):
                self.content = content

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({
                    "choices": [{"message": {"content": self.content}}]
                }, ensure_ascii=False).encode("utf-8")

        valid = json.dumps([{
            "first_line": "她捐出1000万",
            "second_line": "自己只穿15元胶鞋",
            "style": "反差型",
            "evidence_quote": "她捐出1000万，自己却穿15元胶鞋",
        }], ensure_ascii=False)
        with patch.dict("os.environ", {"MINIMAX_API_KEY": "test"}), patch(
            "services.text_service.urllib.request.urlopen",
            side_effect=[FakeResponse("这次没有按格式输出"), FakeResponse(valid)],
        ) as mocked_urlopen:
            result = generate_viral_title("她捐出1000万，自己却穿15元胶鞋。")

        self.assertEqual(2, mocked_urlopen.call_count)
        self.assertEqual("她捐出1000万", result["candidates"][0]["line1"])

    def test_cover_title_prompt_is_click_driven_without_word_bank_constraints(self) -> None:
        import inspect
        from services import text_service

        source = inspect.getsource(text_service.generate_viral_title)
        self.assertIn("提高点击率和停留率", source)
        self.assertIn("不使用任何标题词库、禁词表、优先词表或固定模板", source)
        self.assertIn("任何词、语气和句式都可以使用", source)
        self.assertIn("一次生成12组不同角度、不同句式的候选", source)
        self.assertIn("不设字数上限", source)
        self.assertNotIn("cover_title_score(", source)
        self.assertNotIn("cover_title_rejection_reasons(", source)
        self.assertNotIn("style 只能是", source)
        self.assertIn("evidence_quote", source)

    def test_rewrite_prompt_only_keeps_book_promotion_when_source_has_it(self) -> None:
        plain_prompt = build_rewrite_prompt("他拒绝高薪，回到祖国继续研究。", "纪实故事型", 1)
        book_prompt = build_rewrite_prompt("翻开《国之脊梁》，你才知道他的选择。", "纪实故事型", 1)

        self.assertIn("原文不含带书内容", plain_prompt)
        self.assertIn("禁止主动添加书名", plain_prompt)
        self.assertIn("原文含带书内容", book_prompt)

    def test_rewrite_prompt_can_force_the_selected_sales_book(self) -> None:
        prompt = build_rewrite_prompt(
            "原文没有任何带书内容。",
            "纪实故事型",
            1,
            append_book_promotion=True,
            promotion_book_title="《国之脊梁》",
        )

        self.assertIn("用户已开启结尾带书", prompt)
        self.assertIn("选中的商品是《国之脊梁》", prompt)
        self.assertIn("控制在250到350字", prompt)
        self.assertIn("《国之脊梁》", prompt)

    def test_existing_book_promotion_uses_original_conversion_details(self) -> None:
        prompt = build_rewrite_prompt(
            "翻开这本书，你会理解他的选择。",
            "纪实故事型",
            1,
            fact_brief={
                "book_promotion": {
                    "present": True,
                    "original_intent": "让被忽略的名字重新被看见",
                    "selling_points": ["课本之外的真实人生"],
                    "target_readers": ["家长和孩子"],
                    "transition_angle": "从一个人物扩展到一群建设者",
                }
            },
        )

        self.assertIn("让被忽略的名字重新被看见", prompt)
        self.assertIn("课本之外的真实人生", prompt)
        self.assertIn("家长和孩子", prompt)
        self.assertIn("从一个人物扩展到一群建设者", prompt)

    def test_missing_sales_book_is_appended_once_at_the_end(self) -> None:
        script = "这是已经完成的二创故事。"

        promoted = ensure_rewrite_book_promotion(script, True, "《国之脊梁》")
        promoted_again = ensure_rewrite_book_promotion(promoted, True, "国之脊梁")

        self.assertTrue(promoted.endswith("尤其适合家长和孩子一起读。"))
        self.assertIn("《国之脊梁》", promoted)
        self.assertEqual(promoted, promoted_again)

    def test_rewrite_analysis_prompt_reads_the_complete_source(self) -> None:
        source = "开头事实。" + ("中间事实" * 300) + "全文末尾的重要结果"

        prompt = build_rewrite_analysis_prompt(source)

        self.assertIn("全文末尾的重要结果", prompt)
        self.assertIn("整理一份中性事实底稿", prompt)
        self.assertIn("资料卡不是摘要", prompt)
        self.assertIn('"priority": "must"', prompt)
        self.assertIn('"priority": "support"', prompt)
        self.assertIn('"priority": "discardable"', prompt)
        self.assertIn("不要为了凑数量拆卡", prompt)
        self.assertIn("按真实时间排序", prompt)
        self.assertIn("只记录原文明示的事实", prompt)
        self.assertIn("<protected_opening>开头事实。</protected_opening>", prompt)
        self.assertNotIn('"emotional_arc"', prompt)
        self.assertNotIn('"section_plan"', prompt)
        self.assertNotIn('"viral_analysis"', prompt)

    def test_rewrite_analysis_receives_user_selected_opening_range(self) -> None:
        source = "用户选择的较长固定开头。第二段正文从早年开始。后续继续。"

        prompt = build_rewrite_analysis_prompt(source, preserve_rule="chars_15")

        self.assertIn(
            f"<protected_opening>{source[:15]}</protected_opening>",
            prompt,
        )
        self.assertIn("<raw_script>", prompt)

    def test_second_stage_uses_fact_brief_without_source_body(self) -> None:
        source = "固定开头必须保留。原文正文里的独特句子只能由系统侧核验。"
        fact_brief = normalize_rewrite_fact_brief({
            "core_subject": "测试人物",
            "protagonists": ["测试人物"],
            "protagonist_relationship": "本篇主人公",
            "core_conflict": "测试冲突",
            "timeline": [{"time": "某年", "event": "发生关键事件"}],
            "facts": ["人物完成任务"],
            "book_promotion": {"present": False, "facts": []},
        }, raw_length=2000)

        prompt = build_rewrite_prompt(
            source,
            "纪实故事型",
            1,
            preserve_rule="first_sentence",
            fact_brief=fact_brief,
        )

        self.assertIn("固定开头必须保留。", prompt)
        self.assertIn("人物完成任务", prompt)
        self.assertNotIn("原文正文里的独特句子只能由系统侧核验", prompt)
        self.assertIn("请只依据事实资料卡，独立创作", prompt)
        self.assertNotIn("<raw_script>", prompt)
        self.assertIn("事件的实际先后和因果不得写反", prompt)
        self.assertIn("support 卡只在服务核心命题时保留", prompt)
        self.assertIn("discardable 卡允许删除", prompt)

    def test_rewrite_prompt_uses_project_creative_guidelines_and_keeps_dynamic_opening(self) -> None:
        source = "用户框选的固定开头。后面的原文需要进入第二阶段核对。"

        prompt = build_rewrite_prompt(
            source,
            "纪实故事型",
            1,
            preserve_rule="first_sentence",
            fact_brief={"facts": ["人物完成关键任务"]},
        )

        self.assertIn("事实资料卡是唯一写作依据", prompt)
        self.assertIn("用户框选的固定开头。", prompt)
        self.assertIn("固定开头必须一字不改", prompt)
        self.assertIn("真实爆点与完播", prompt)
        self.assertIn("固定开头与紧接的前两段共同完成留人", prompt)
        self.assertIn("至少两个阶段性问题", prompt)
        self.assertIn("专业贡献必须翻译成普通人能理解的实际变化", prompt)
        self.assertIn("具体年份使用阿拉伯数字", prompt)
        self.assertIn("年龄、数量、金额、比例、序号和自然量词不作格式限制", prompt)
        self.assertIn("时间回到", prompt)
        self.assertIn("时间拨回到", prompt)
        self.assertNotIn("【在这里粘贴原文】", prompt)

    def test_rewrite_prompt_stays_compact(self) -> None:
        prompt = build_rewrite_prompt(
            "固定开头。后续原文内容。",
            "纪实故事型",
            1,
            fact_brief={"material_cards": [{"id": 1, "fact": "人物完成关键任务"}]},
        )

        self.assertLess(len(prompt), 5000)
        self.assertLessEqual(prompt.count("固定开头"), 13)
        self.assertNotIn("正文总体重构度应达到", prompt)

    def test_fallback_timeline_does_not_treat_card_ids_as_chronology(self) -> None:
        prompt = build_rewrite_prompt(
            "后期结果固定开头。随后原文先讲晚年再讲早年。",
            "纪实故事型",
            1,
            fact_brief={
                "timeline_verified": False,
                "material_cards": [
                    {"id": 1, "priority": "must", "time": "晚年", "fact": "晚年事实"},
                    {"id": 2, "priority": "must", "time": "早年", "fact": "早年事实"},
                ],
            },
        )

        self.assertIn("卡片 id 仅代表原文出现顺序", prompt)
        self.assertIn("恢复真实发生顺序", prompt)
        self.assertIn("允许预告有依据的结果", prompt)

    def test_short_rewrite_does_not_receive_a_length_correction(self) -> None:
        previous = {
            "rewritten_script": "上一版短稿" * 100,
            "rewrite_comparison": {
                "overall_difference": 80,
                "text1_length": 2000,
                "text2_length": 1000,
                "length_passed": False,
                "min_rewritten_length": 1800,
                "max_rewritten_length": 2200,
                "continuous_reuse": 5,
                "source_phrase_reuse": 5,
                "sentence_imitation": 5,
            },
        }

        prompt = build_rewrite_prompt("原" * 2000, "纪实故事型", 2, previous, fact_brief={"facts": ["关键事实"]})

        self.assertNotIn("本轮必须补足篇幅", prompt)
        self.assertNotIn("低于最低", prompt)
        self.assertIn("不设目标字数和篇幅上下限", prompt)
        self.assertNotIn("上一版短稿", prompt)

    def test_eighty_percent_rewrite_passes_when_other_quality_passes(self) -> None:
        comparison = {
            "passed": True,
            "non_length_quality_passed": True,
            "length_ratio": 85,
            "overall_difference": 80,
            "continuous_reuse": 5,
            "source_phrase_reuse": 5,
            "sentence_imitation": 5,
            "text1_length": 1000,
            "text2_length": 850,
            "min_rewritten_length": 900,
            "max_rewritten_length": 1100,
        }

        def fake_rewrite(*_args, **_kwargs):
            return {"rewritten_script": "二创正文", "rewrite_comparison": dict(comparison)}

        with patch(
            "services.text_service.request_minimax_rewrite_analysis",
            return_value={"facts": ["事实"]},
        ), patch("services.text_service.request_minimax_rewrite", side_effect=fake_rewrite):
            result = rewrite_script_with_minimax("原文" * 500, "纪实故事型", "test-key")

        self.assertNotIn("rewrite_quality_status", result)
        self.assertNotIn("rewrite_warning", result)
        self.assertEqual(2, result["rewrite_attempts"])
        self.assertEqual(2, result["rewrite_candidates_generated"])

    def test_two_strategy_candidates_compete_and_best_difference_wins(self) -> None:
        prompts = []
        differences = iter([72, 91])

        def fake_rewrite(prompt, *_args, **_kwargs):
            prompts.append(prompt)
            difference = next(differences)
            return {"rewritten_script": f"差异得分为{difference}的候选稿", "rewrite_comparison": {
                "passed": True, "non_length_quality_passed": True, "length_passed": True,
                "outline_structure_passed": True, "narrative_difference": difference,
                "overall_difference": difference, "continuous_reuse": 2, "sentence_imitation": 2,
            }}

        with patch("services.text_service.request_minimax_rewrite_analysis", return_value={"facts": ["事实"]}), patch(
            "services.text_service.request_minimax_rewrite", side_effect=fake_rewrite
        ) as rewrite:
            result = rewrite_script_with_minimax("原文事实。后续事实。", "纪实故事型", "test-key")

        self.assertEqual(2, rewrite.call_count)
        self.assertEqual("差异得分为91的候选稿", result["rewritten_script"])
        self.assertEqual("选择代价", result["rewrite_narrative_strategy"]["strategy"])
        self.assertIn("本稿叙事策略：冲突悬念", prompts[0])
        self.assertIn("本稿叙事策略：选择代价", prompts[1])
        self.assertEqual(2, len({
            prompt.split("本稿表达指纹：", 1)[1].split("。", 1)[0]
            for prompt in prompts
        }))

    def test_attraction_score_selects_better_candidate_after_quality_gates(self) -> None:
        responses = [
            {
                "rewritten_script": "差异更高但吸引力较弱的候选稿",
                "rewrite_comparison": {
                    "passed": True,
                    "non_length_quality_passed": True,
                    "length_passed": True,
                    "narrative_difference": 96,
                    "overall_difference": 96,
                    "continuous_reuse": 1,
                    "source_phrase_reuse": 1,
                },
            },
            {
                "rewritten_script": "有真实冲突和递进悬念的候选稿",
                "rewrite_comparison": {
                    "passed": True,
                    "non_length_quality_passed": True,
                    "length_passed": True,
                    "narrative_difference": 86,
                    "overall_difference": 86,
                    "continuous_reuse": 2,
                    "source_phrase_reuse": 2,
                },
            },
        ]
        audits = [
            {
                "fact_coverage_passed": True,
                "timeline_order_passed": True,
                "emotional_quality_passed": True,
                "attraction_score": 58,
            },
            {
                "fact_coverage_passed": True,
                "timeline_order_passed": True,
                "emotional_quality_passed": True,
                "attraction_score": 88,
            },
        ]

        with patch(
            "services.text_service.request_minimax_rewrite_analysis",
            return_value={"facts": ["事实"]},
        ), patch(
            "services.text_service.request_minimax_rewrite",
            side_effect=responses,
        ), patch(
            "services.text_service.request_minimax_rewrite_fact_coverage",
            side_effect=audits,
        ):
            result = rewrite_script_with_minimax("原文事实。后续事实。", "纪实故事型", "test-key")

        self.assertEqual("有真实冲突和递进悬念的候选稿", result["rewritten_script"])
        self.assertEqual(88, result["rewrite_comparison"]["attraction_score"])

    def test_low_attraction_feedback_is_sent_to_the_next_candidate(self) -> None:
        prompts = []
        responses = [
            {
                "rewritten_script": "第一篇较平的候选稿",
                "rewrite_comparison": {
                    "passed": True,
                    "non_length_quality_passed": True,
                    "overall_difference": 85,
                    "continuous_reuse": 2,
                    "source_phrase_reuse": 2,
                },
            },
            {
                "rewritten_script": "第二篇有递进的候选稿",
                "rewrite_comparison": {
                    "passed": True,
                    "non_length_quality_passed": True,
                    "overall_difference": 85,
                    "continuous_reuse": 2,
                    "source_phrase_reuse": 2,
                },
            },
        ]
        audits = [
            {
                "fact_coverage_passed": True,
                "factual_grounding_passed": True,
                "timeline_order_passed": True,
                "emotional_quality_passed": True,
                "attraction_score": 52,
                "attraction_issues": ["前段没有具体冲突", "答案释放过早"],
            },
            {
                "fact_coverage_passed": True,
                "factual_grounding_passed": True,
                "timeline_order_passed": True,
                "emotional_quality_passed": True,
                "attraction_score": 82,
                "attraction_issues": [],
            },
        ]

        def fake_rewrite(prompt, *_args, **_kwargs):
            prompts.append(prompt)
            return responses[len(prompts) - 1]

        with patch(
            "services.text_service.request_minimax_rewrite_analysis",
            return_value={"facts": ["事实"]},
        ), patch(
            "services.text_service.request_minimax_rewrite",
            side_effect=fake_rewrite,
        ), patch(
            "services.text_service.request_minimax_rewrite_fact_coverage",
            side_effect=audits,
        ):
            result = rewrite_script_with_minimax("原文事实。后续事实。", "纪实故事型", "test-key")

        self.assertIn("本轮必须提升吸引力", prompts[1])
        self.assertIn("前段没有具体冲突", prompts[1])
        self.assertEqual("第二篇有递进的候选稿", result["rewritten_script"])

    def test_length_ratio_does_not_outweigh_rewrite_difference(self) -> None:
        responses = [
            {
                "rewritten_script": "差异更高但过度压缩的稿件",
                "rewrite_comparison": {
                    "passed": True, "non_length_quality_passed": True, "length_passed": True,
                    "length_ratio": 60, "narrative_difference": 95, "overall_difference": 95,
                    "continuous_reuse": 1, "source_phrase_reuse": 1,
                },
            },
            {
                "rewritten_script": "篇幅达到目标区间的完整稿件",
                "rewrite_comparison": {
                    "passed": True, "non_length_quality_passed": True, "length_passed": True,
                    "length_ratio": 68, "narrative_difference": 86, "overall_difference": 86,
                    "continuous_reuse": 2, "source_phrase_reuse": 2,
                },
            },
        ]

        with patch(
            "services.text_service.request_minimax_rewrite_analysis",
            return_value={"facts": ["事实"]},
        ), patch(
            "services.text_service.request_minimax_rewrite",
            side_effect=responses,
        ):
            result = rewrite_script_with_minimax("原文事实。后续事实。", "纪实故事型", "test-key")

        self.assertEqual("差异更高但过度压缩的稿件", result["rewritten_script"])

    def test_candidate_strategies_force_different_focus_and_paragraph_architecture(self) -> None:
        fact_brief = {
            "material_cards": [
                {"id": 1, "expansion_level": "focus", "fact": "事实一"},
                {"id": 2, "expansion_level": "support", "fact": "事实二"},
                {"id": 3, "expansion_level": "focus", "fact": "事实三"},
                {"id": 4, "expansion_level": "support", "fact": "事实四"},
                {"id": 5, "expansion_level": "focus", "fact": "事实五"},
            ],
        }

        prompts = [
            build_rewrite_prompt("固定开头。后续内容。", "纪实故事型", attempt, fact_brief=fact_brief)
            for attempt in range(1, 3)
        ]

        self.assertIn("优先展开情绪与主线素材卡：[1, 2]", prompts[0])
        self.assertIn("优先展开情绪与主线素材卡：[4, 5]", prompts[1])
        for prompt in prompts:
            self.assertIn("段落数量、段落长短和信息落点与原文形成一一对应", prompt)

    def test_emotion_focus_cards_drive_candidate_focus_without_dropping_other_facts(self) -> None:
        fact_brief = {
            "material_cards": [
                {"id": 1, "priority": "must", "emotion_focus": False, "fact": "普通事实"},
                {
                    "id": 2,
                    "priority": "must",
                    "emotion_focus": True,
                    "fact": "关键选择",
                    "emotional_stakes": "人物因此承担了长期代价",
                },
                {"id": 3, "priority": "mergeable", "emotion_focus": False, "fact": "背景履历"},
            ],
        }

        prompt = build_rewrite_prompt(
            "固定开头。后续内容。", "纪实故事型", 1, fact_brief=fact_brief
        )

        self.assertIn("优先展开情绪与主线素材卡：[2]", prompt)
        self.assertIn("普通事实", prompt)
        self.assertIn("背景履历", prompt)
        self.assertIn("人物因此承担了长期代价", prompt)
        self.assertIn("不设目标字数和篇幅上下限", prompt)

    def test_both_candidates_receive_every_fact_card(self) -> None:
        fact_brief = {
            "material_cards": [
                {"id": 1, "priority": "must", "fact": "核心选择事实"},
                {"id": 2, "priority": "mergeable", "fact": "普通早年履历"},
                {"id": 3, "priority": "mergeable", "fact": "重复成就介绍"},
            ],
            "verified_quotes": [],
        }

        first_prompt = build_rewrite_prompt(
            "固定开头。原文正文。", "纪实故事型", 1, fact_brief=fact_brief
        )
        second_prompt = build_rewrite_prompt(
            "固定开头。原文正文。", "纪实故事型", 2, fact_brief=fact_brief
        )

        self.assertIn("核心选择事实", first_prompt)
        self.assertIn("普通早年履历", first_prompt)
        self.assertIn("核心选择事实", second_prompt)
        self.assertIn("普通早年履历", second_prompt)
        self.assertIn("重复成就介绍", second_prompt)
        self.assertIn("verified_quotes 之外的对话、演讲和遗言只能转成间接叙述", second_prompt)

    def test_length_does_not_trigger_a_compression_failure(self) -> None:
        base = {
            "non_length_quality_passed": True,
            "overall_difference": 85,
            "continuous_reuse": 3,
            "source_phrase_reuse": 3,
            "sentence_imitation": 3,
        }
        responses = [
            {
                "rewritten_script": "第一版篇幅不足。",
                "rewrite_comparison": {
                    **base,
                    "passed": False,
                    "length_ratio": 60,
                    "length_passed": False,
                },
            },
            {
                "rewritten_script": "第二版已经补足资料卡中的事实、动作和关键画面。",
                "rewrite_comparison": {
                    **base,
                    "passed": True,
                    "length_ratio": 80,
                    "length_passed": True,
                },
            },
        ]

        with patch(
            "services.text_service.request_minimax_rewrite_analysis",
            return_value={"facts": ["事实"]},
        ), patch(
            "services.text_service.request_minimax_rewrite",
            side_effect=responses,
        ) as rewrite:
            result = rewrite_script_with_minimax("原文事实。后续事实。", "纪实故事型", "test-key")

        self.assertEqual(2, rewrite.call_count)
        self.assertEqual(2, result["rewrite_attempts"])
        self.assertEqual(2, result["rewrite_candidates_generated"])
        self.assertTrue(result["rewrite_comparison"]["passed"])
        self.assertNotIn("rewrite_compression_warning", result)

    def test_missing_fact_card_triggers_rewrite_retry(self) -> None:
        comparison = {
            "passed": True,
            "non_length_quality_passed": True,
            "length_ratio": 80,
            "length_passed": True,
            "overall_difference": 90,
            "continuous_reuse": 2,
            "source_phrase_reuse": 2,
            "sentence_imitation": 2,
        }
        fact_brief = {
            "material_cards": ["01｜早年｜完成第一件事", "02｜晚年｜承担关键代价"],
            "protagonists": ["测试人物"],
        }
        coverage_results = [
            {
                "fact_coverage_passed": False,
                "covered_fact_cards": [1],
                "expected_fact_cards": 2,
                "missing_fact_cards": [{
                    "card": 2,
                    "fact": "02｜晚年｜承担关键代价",
                    "missing": "关键代价没有写入",
                }],
            },
            {
                "fact_coverage_passed": True,
                "covered_fact_cards": [1, 2],
                "expected_fact_cards": 2,
                "missing_fact_cards": [],
            },
            {
                "fact_coverage_passed": True,
                "covered_fact_cards": [1, 2],
                "expected_fact_cards": 2,
                "missing_fact_cards": [],
            },
        ]

        with patch(
            "services.text_service.request_minimax_rewrite_analysis",
            return_value=fact_brief,
        ), patch(
            "services.text_service.request_minimax_rewrite",
            return_value={
                "rewritten_script": "测试人物先完成第一件事。后来承担了关键代价。",
                "rewrite_comparison": dict(comparison),
            },
        ) as rewrite, patch(
            "services.text_service.request_minimax_rewrite_fact_coverage",
            side_effect=coverage_results,
        ) as audit:
            result = rewrite_script_with_minimax("测试人物的原文。后续内容。", "纪实故事型", "test-key")

        self.assertEqual(2, rewrite.call_count)
        self.assertEqual(2, audit.call_count)
        self.assertEqual(2, result["rewrite_attempts"])
        self.assertTrue(result["rewrite_comparison"]["fact_coverage_passed"])
        self.assertNotIn("rewrite_compression_warning", result)

    def test_fact_audit_outage_does_not_reject_an_otherwise_valid_draft(self) -> None:
        comparison = {
            "passed": True,
            "non_length_quality_passed": True,
            "length_ratio": 80,
            "length_passed": True,
            "overall_difference": 90,
            "continuous_reuse": 2,
            "source_phrase_reuse": 2,
        }
        fact_brief = {
            "protagonists": ["测试人物"],
            "material_cards": [{"id": 1, "priority": "must", "fact": "完成核心任务"}],
        }

        with patch(
            "services.text_service.request_minimax_rewrite_analysis",
            return_value=fact_brief,
        ), patch(
            "services.text_service.request_minimax_rewrite",
            return_value={"rewritten_script": "测试人物完成了核心任务。", "rewrite_comparison": dict(comparison)},
        ), patch(
            "services.text_service.request_minimax_rewrite_fact_coverage",
            side_effect=TimeoutError("audit timeout"),
        ):
            result = rewrite_script_with_minimax("测试人物的原文。", "纪实故事型", "test-key")

        self.assertTrue(result["rewrite_comparison"]["passed"])
        self.assertEqual("unavailable", result["rewrite_comparison"]["audit_status"])
        self.assertIn("事实审稿暂不可用", result["rewrite_audit_warning"])

    def test_best_rewrite_is_returned_when_multiple_quality_checks_miss(self) -> None:
        comparison = {
            "passed": False,
            "non_length_quality_passed": False,
            "length_passed": False,
            "length_ratio": 87,
            "overall_difference": 85,
            "continuous_reuse": 14,
            "source_phrase_reuse": 10,
            "sentence_imitation": 22,
            "text1_length": 2051,
            "text2_length": 1782,
            "min_rewritten_length": 1845,
            "max_rewritten_length": 2256,
        }

        with patch(
            "services.text_service.request_minimax_rewrite_analysis",
            return_value={"facts": ["事实"]},
        ), patch(
            "services.text_service.request_minimax_rewrite",
            return_value={
                "rewritten_script": "这是三次生成中最好的完整二创稿。",
                "rewrite_comparison": comparison,
            },
        ):
            result = rewrite_script_with_minimax("原文" * 1025, "纪实故事型", "test-key")

        self.assertEqual("这是三次生成中最好的完整二创稿。", result["rewritten_script"])
        self.assertEqual("quality_warning", result["rewrite_quality_status"])
        self.assertEqual("", result["rewrite_error"])
        self.assertNotIn("字数为原文的 87%", result["rewrite_warning"])
        self.assertIn("独立表达不足", result["rewrite_warning"])
        self.assertIn("存在连续复用", result["rewrite_warning"])
        self.assertNotIn("连续复用率 14%", result["rewrite_warning"])
        self.assertEqual(2, result["rewrite_attempts"])

    def test_previous_draft_is_returned_when_a_later_retry_request_fails(self) -> None:
        comparison = {
            "passed": False,
            "non_length_quality_passed": False,
            "length_passed": False,
            "length_ratio": 87,
            "overall_difference": 85,
            "continuous_reuse": 14,
            "source_phrase_reuse": 10,
            "sentence_imitation": 22,
        }
        responses = [
            {"rewritten_script": "第一次生成的完整稿件", "rewrite_comparison": comparison},
            TimeoutError("retry timeout"),
        ]

        with patch(
            "services.text_service.request_minimax_rewrite_analysis",
            return_value={"facts": ["事实"]},
        ), patch(
            "services.text_service.request_minimax_rewrite",
            side_effect=responses,
        ):
            result = rewrite_script_with_minimax("原文" * 100, "纪实故事型", "test-key")

        self.assertEqual("第一次生成的完整稿件", result["rewritten_script"])
        self.assertEqual("quality_warning", result["rewrite_quality_status"])
        self.assertIn("候选生成异常", result["rewrite_warning"])
        self.assertIn("候选稿 2", result["rewrite_warning"])
        self.assertEqual(2, result["rewrite_attempts"])
        self.assertEqual(1, result["rewrite_candidates_generated"])

    def test_rewrite_prompt_requires_body_to_continue_protected_opening(self) -> None:
        source = (
            "他在台湾潜伏四十二年后终于回乡，却发现妻子身边早已儿孙满堂。"
            "正当他准备转身时，妻子追出来说了一番话，把他感动得老泪纵横。"
            "他原本是一个广东读书人，后来去了台湾。"
        )

        prompt = build_rewrite_prompt(source, "纪实故事型", 1, preserve_rule="chars_67")

        self.assertIn("后续正文必须自然承接固定开头", prompt)
        self.assertIn("不沿着原文逐句换词", prompt)
        self.assertIn("事件实际发生的先后关系不得写反", prompt)
        self.assertIn("信息揭晓顺序可以重组", prompt)
        self.assertIn("不让段落数量、段落长短和信息位置与原文一一对应", prompt)

    def test_rewrite_retry_requires_timeline_correction(self) -> None:
        previous = {
            "rewrite_comparison": {
                "overall_difference": 80,
                "continuous_reuse": 2,
                "source_phrase_reuse": 2,
                "sentence_imitation": 2,
                "length_passed": True,
                "timeline_order_passed": False,
                "timeline_order_issues": [{"reason": "素材卡4提前到素材卡2之前"}],
            }
        }

        prompt = build_rewrite_prompt(
            "固定开头。后续原文。",
            "纪实故事型",
            2,
            previous,
            fact_brief={"material_cards": ["早年", "中年", "晚年", "结果"]},
        )

        self.assertIn("本轮必须修正时间线", prompt)
        self.assertIn("素材卡4提前到素材卡2之前", prompt)
        self.assertIn("可以先预告资料卡已有的真实结果", prompt)
        self.assertIn("不得把后发生的行动写成先发生", prompt)

    def test_rewrite_prompt_does_not_turn_emotional_arc_into_a_hard_plan(self) -> None:
        prompt = build_rewrite_prompt(
            "固定开头。后续原文。",
            "纪实故事型",
            1,
            fact_brief={
                "emotional_arc": [
                    {"card": 1, "beat": "心疼"},
                    {"card": 2, "beat": "敬佩"},
                ],
                "material_cards": [{
                    "id": 1,
                    "priority": "must",
                    "fact": "人物执行任务",
                    "emotional_stakes": "错过与家人最后一次见面",
                    "relationship_change": "家人长期误解",
                }],
            },
        )

        self.assertNotIn("emotional_arc 用卡片编号标出关键情感节点", prompt)
        self.assertIn("不虚构心理、眼泪或台词", prompt)

    def test_rewrite_retry_targets_grounded_emotional_content(self) -> None:
        previous = {
            "rewrite_comparison": {
                "overall_difference": 80,
                "continuous_reuse": 2,
                "source_phrase_reuse": 2,
                "sentence_imitation": 2,
                "length_passed": True,
                "emotional_quality_passed": False,
                "emotional_issues": [{"reason": "关键牺牲只写了结果，没有人物和家人的反应"}],
            }
        }

        prompt = build_rewrite_prompt(
            "固定开头。后续原文。",
            "纪实故事型",
            2,
            previous,
            fact_brief={"emotional_arc": ["心疼", "敬佩"], "material_cards": ["关键事实"]},
        )

        self.assertIn("本轮必须写透情绪重点", prompt)
        self.assertIn("关键牺牲只写了结果", prompt)
        self.assertIn("不得添加哭泣、心理活动、台词", prompt)

    def test_rewrite_prompt_avoids_fragmented_paragraphs_and_preserves_fixed_opening(self) -> None:
        prompt = build_rewrite_prompt(
            "这是一段由用户选择并且需要完整保留的固定开头，即使它本身超过五十个中文字符也不能删改或者生硬截断。后续正文继续展开。",
            "纪实故事型",
            1,
            preserve_rule="chars_55",
        )

        self.assertIn("一个完整画面一个自然段", prompt)
        self.assertIn("固定开头必须一字不改并单独成段", prompt)
        self.assertIn("不设目标字数和篇幅上下限", prompt)

    def test_rewrite_comparison_excludes_the_fixed_opening(self) -> None:
        fixed_opening = "这段固定开头必须一字不改但不参与相似度计算"
        original = f"{fixed_opening}。他在一九八零年来到北京，随后进入实验室工作。"
        rewritten = f"{fixed_opening}。抵达首都以后，他把余下的时间都交给了科研。"

        comparison = compare_scripts(original, rewritten, fixed_opening)

        self.assertFalse(any(fixed_opening in passage for passage in comparison["reused_passages"]))
        self.assertEqual(len(fixed_opening), comparison["protected_opening_length"])
        self.assertIn("keyword_overlap", comparison)
        self.assertIn("source_phrase_reuse", comparison)

    def test_rewrite_comparison_rejects_chinese_years_and_stiff_time_transitions(self) -> None:
        comparison = compare_scripts(
            "他在实验室工作多年，后来完成任务。",
            "时间拨回到一九八零年，他三十岁，带着壹佰元进入实验室。",
        )

        self.assertFalse(comparison["rewrite_format_passed"])
        self.assertFalse(comparison["passed"])
        self.assertIn("时间拨回到一九八零年", comparison["rewrite_format_issues"])
        self.assertIn("一九八零", comparison["rewrite_format_issues"])
        self.assertNotIn("三十", comparison["rewrite_format_issues"])
        self.assertNotIn("壹佰", comparison["rewrite_format_issues"])

        direct_return = compare_scripts(
            "他后来进入实验室。",
            "时间回到1980年，他进入实验室。",
        )
        self.assertFalse(direct_return["rewrite_format_passed"])
        self.assertIn("时间回到1980年", direct_return["rewrite_format_issues"])

        natural_numbers = compare_scripts(
            "原文事实。",
            "1980年，他三十岁，带着壹佰元和一封信进入实验室。",
        )
        self.assertTrue(natural_numbers["rewrite_format_passed"])

    def test_rewrite_format_rules_do_not_modify_protected_opening_or_quote(self) -> None:
        opening = "时间拨回到一九八零年。"
        quote = "我等了三十年。"
        comparison = compare_scripts(
            f"{opening}{quote}原文继续。",
            f"{opening}{quote}1981年，他直接进入新的工作阶段。",
            protected_opening=opening,
            protected_passages=[quote],
        )

        self.assertTrue(comparison["rewrite_format_passed"])
        self.assertEqual([], comparison["rewrite_format_issues"])

    def test_rewrite_comparison_excludes_verified_direct_quotes(self) -> None:
        quote = "我一定要回到祖国。"
        source = f"他说：{quote}随后整理行李准备出发。"
        rewritten = f"临行前，他只留下一句话：{quote}第二天，他踏上了归途。"

        comparison = compare_scripts(
            source,
            rewritten,
            protected_passages=[quote],
        )

        self.assertEqual(1, comparison["protected_passage_count"])
        self.assertFalse(any(quote in passage for passage in comparison["reused_passages"]))

    def test_rewrite_comparison_cannot_be_diluted_by_appending_new_text(self) -> None:
        opening = "固定开头保持不变。"
        body = "他拒绝了高薪回到祖国。此后他进入实验室，连续多年解决关键难题。最后项目终于成功。"
        padded_copy = f"{opening}{body}\n\n这里再新增两段完全不同的话，用来人为拉开长度。又补充一些无关表达。"

        comparison = compare_scripts(f"{opening}{body}", padded_copy, opening)

        self.assertEqual(100, comparison["source_phrase_reuse"])
        self.assertFalse(comparison["passed"])

    def test_rewrite_comparison_rejects_sentence_by_sentence_imitation(self) -> None:
        source = "他放弃国外高薪回到祖国。回国以后，他立即进入实验室攻关。多年之后，关键项目终于成功。"
        imitation = "他舍弃海外优厚待遇选择归国。回来之后，他马上走进实验室解决难题。经过许多年，核心工程最终取得成功。"

        comparison = compare_scripts(source, imitation)

        self.assertGreater(comparison["sentence_imitation"], 35)
        self.assertFalse(comparison["passed"])

    def test_rewrite_comparison_detects_matching_rhythm_and_detail_distribution(self) -> None:
        source = (
            "他离开家乡走进学校。老师把一封信交给了他。\n\n"
            "后来他进入工厂工作。几年里他解决了许多难题。\n\n"
            "最终那台设备顺利启动。"
        )
        rewritten = (
            "她告别亲友来到医院。院长将1份名单递给她。\n\n"
            "此后她留在病房值守。几年间她帮助了很多病人。\n\n"
            "最终这项救治圆满结束。"
        )
        comparison = compare_scripts(source, rewritten)
        self.assertIsNotNone(comparison["structure_similarity"])
        self.assertIsNotNone(comparison["detail_distribution_similarity"])
        self.assertFalse(comparison["structure_similarity_passed"])
        self.assertFalse(comparison["detail_distribution_passed"])
        self.assertTrue(comparison["passed"])

    def test_rewrite_comparison_rejects_identical_text(self) -> None:
        comparison = compare_scripts("完全相同的文案", "完全相同的文案")

        self.assertEqual(100, comparison["continuous_reuse"])
        self.assertEqual(100, comparison["phrase_overlap"])
        self.assertEqual(0, comparison["overall_difference"])
        self.assertFalse(comparison["passed"])

    def test_rewrite_rejects_outline_style_ai_transitions(self) -> None:
        source = "甲" * 160
        rewritten = (
            "先说他的童年遭遇，再说他后来完成的事业，最后再说这段经历带来的意义。"
            + "乙" * 130
        )

        comparison = compare_scripts(source, rewritten)

        self.assertTrue(comparison["length_passed"])
        self.assertFalse(comparison["outline_structure_passed"])
        self.assertTrue(comparison["outline_structure_fragments"])
        self.assertTrue(comparison["passed"])

    def test_fixed_opening_is_excluded_from_outline_style_check(self) -> None:
        opening = "先说他的过去，再说他的选择，最后再说他的结局。"
        source = opening + ("甲" * 120)
        rewritten = opening + ("乙" * 120)

        comparison = compare_scripts(source, rewritten, opening)

        self.assertTrue(comparison["outline_structure_passed"])

    def test_rewrite_retry_omits_previous_draft_but_keeps_diagnostics(self) -> None:
        previous_script = "固定开头保持不变。这里是一段仍然照着原文写的重复表达。"
        previous = {
            "rewritten_script": previous_script,
            "rewrite_comparison": {
                "overall_difference": 25,
                "continuous_reuse": 70,
                "phrase_overlap": 62,
                "reused_passages": ["仍然照着原文写的重复表达"],
            },
        }

        prompt = build_rewrite_prompt("固定开头保持不变。后续原文内容。", "纪实故事型", 2, previous)

        self.assertNotIn(previous_script, prompt)
        self.assertNotIn("<previous_rewrite>", prompt)
        self.assertIn("重点重复片段：仍然照着原文写的重复表达", prompt)
        self.assertIn("固定开头不参与重复率和总体重构度计算", prompt)
        self.assertIn("结构问题摘要：存在连续复用原文表达", prompt)
        self.assertIn("不提供也不得猜测上一版全文", prompt)

    def test_keywords_from_text_does_not_slice_script_fragments(self) -> None:
        tags = keywords_from_text("alpha beta gamma delta epsilon")

        flattened = tags["people"] + tags["scene"] + tags["era"] + tags["keywords"]
        self.assertEqual([], flattened)

    def test_generate_shots_has_no_local_visual_fallback(self) -> None:
        script = "alpha beta gamma delta epsilon\nzeta eta theta iota kappa"

        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": ""}):
            with self.assertRaisesRegex(RuntimeError, "DEEPSEEK_API_KEY"):
                generate_shots(script)

    def test_storyboard_split_is_limited_to_six_to_nine_ordered_shots(self) -> None:
        script = "".join(f"第{index}段讲述朝堂中的一次关键转折。" for index in range(1, 18))

        chunks = split_script_into_storyboards(script)

        self.assertGreaterEqual(len(chunks), 6)
        self.assertLessEqual(len(chunks), 9)
        self.assertEqual(script, "".join(chunks))

if __name__ == "__main__":
    unittest.main()
