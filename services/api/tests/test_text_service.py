from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from services.text_service import (  # noqa: E402
    GUOZHIJILIANG_PEOPLE,
    GUOZHIJILIANG_STORY_SEEDS,
    MIN_GUOZHIJILIANG_SCRIPT_CHARS,
    MIN_GUOZHIJILIANG_SCRIPT_PARAGRAPHS,
    MAX_REWRITE_LENGTH_RATIO,
    MIN_REWRITE_DIFFERENCE,
    MIN_REWRITE_LENGTH_RATIO,
    RECENT_GUOZHIJILIANG_OPENINGS,
    RECENT_GUOZHIJILIANG_PEOPLE,
    RewriteGenerationError,
    build_guozhijiliang_script_prompt_v2,
    build_rewrite_analysis_prompt,
    build_rewrite_prompt,
    choose_guozhijiliang_seed,
    clean_shot_visual_terms,
    compare_scripts,
    cover_title_rejection_reasons,
    cover_title_needs_rewrite,
    ensure_original_opening,
    ensure_rewrite_book_promotion,
    extract_leading_title,
    fallback_infer_title,
    extract_opening_hook,
    generate_viral_title,
    generate_shots,
    guozhijiliang_opening_needs_rewrite,
    guozhijiliang_script_stats,
    keywords_from_text,
    normalize_auto_title,
    normalize_rewrite_fact_brief,
    parse_title_candidates,
    request_minimax_rewrite_analysis,
    rewrite_script,
    rewrite_script_with_minimax,
)


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

        self.assertIn("draft generation attempt 1", raised.exception.detail)
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

    def test_source_analysis_retries_malformed_model_json(self) -> None:
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
            with self.assertRaises(RuntimeError) as raised:
                request_minimax_rewrite_analysis("source text", "test-key")

        self.assertEqual(2, urlopen.call_count)
        self.assertIn("invalid JSON after 2 analysis attempts", str(raised.exception))
        self.assertIn("JSONDecodeError", str(raised.exception))

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

    def test_rewrite_requires_seventy_percent_reconstruction(self) -> None:
        self.assertEqual(70, MIN_REWRITE_DIFFERENCE)

    def test_rewrite_length_stays_close_to_source(self) -> None:
        self.assertEqual(0.90, MIN_REWRITE_LENGTH_RATIO)
        self.assertEqual(1.10, MAX_REWRITE_LENGTH_RATIO)

        prompt = build_rewrite_prompt("一" * 1000, "纪实故事型", 1, fact_brief={"facts": ["测试事实"]})
        self.assertIn("900 到 1100", prompt)
        self.assertIn("应以接近原文的 1000 字为写作目标", prompt)

    def test_rewrite_comparison_rejects_a_script_that_is_too_short(self) -> None:
        comparison = compare_scripts("甲" * 2000, "乙" * 800)

        self.assertEqual(40, comparison["length_ratio"])
        self.assertEqual(1800, comparison["min_rewritten_length"])
        self.assertEqual(2200, comparison["max_rewritten_length"])
        self.assertFalse(comparison["length_passed"])
        self.assertFalse(comparison["passed"])

    def test_opening_hook_uses_complete_sentences_between_20_and_35_chars(self) -> None:
        source = "他拒绝了所有人的劝告。因为那个箱子里，藏着不能公开的秘密。后面的故事继续。"

        self.assertEqual("他拒绝了所有人的劝告。因为那个箱子里，藏着不能公开的秘密。", extract_opening_hook(source))

    def test_opening_hook_caps_an_overlong_first_sentence_at_a_clause(self) -> None:
        source = "美国海关突然扣下他的行李箱，并且封锁了所有消息，因为他们真正害怕的根本不是箱子里的几张纸。后文。"
        hook = extract_opening_hook(source)

        self.assertGreaterEqual(len(hook), 20)
        self.assertLessEqual(len(hook), 35)
        self.assertTrue(hook.endswith(("，", "。", "！", "？", ",")))

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

    def test_guozhijiliang_seed_pool_covers_book_people(self) -> None:
        seed_people = {person for person, _ in GUOZHIJILIANG_STORY_SEEDS}

        self.assertEqual(40, len(GUOZHIJILIANG_PEOPLE))
        self.assertTrue(set(GUOZHIJILIANG_PEOPLE).issubset(seed_people))

    def test_guozhijiliang_random_seed_avoids_recent_repeats(self) -> None:
        RECENT_GUOZHIJILIANG_PEOPLE.clear()
        selected = [choose_guozhijiliang_seed()[0] for _ in range(13)]
        RECENT_GUOZHIJILIANG_PEOPLE.clear()

        self.assertEqual(len(selected), len(set(selected)))

    def test_guozhijiliang_prompt_discourages_reused_opening_templates(self) -> None:
        RECENT_GUOZHIJILIANG_OPENINGS.clear()
        prompt = build_guozhijiliang_script_prompt_v2("茅以升", "亲手建成钱塘江大桥，又在战火逼近时含泪参与炸桥")
        RECENT_GUOZHIJILIANG_OPENINGS.clear()

        self.assertIn("本篇独特点", prompt)
        self.assertIn("本篇开头策略", prompt)
        self.assertIn("第一句话就是钩子", prompt)
        self.assertIn("不能承担介绍任务", prompt)
        self.assertIn("不能描写环境、天气、时代氛围", prompt)
        self.assertIn("必须先出事，再补背景", prompt)
        self.assertIn("前三秒必须做到“三连击”", prompt)
        self.assertIn("第一句给爆点", prompt)
        self.assertIn("强冲突开头示例", prompt)
        self.assertIn("1000到1300", prompt)
        self.assertIn("20到30", prompt)
        self.assertIn("换成另一个科学家也能用，必须推翻重写", prompt)
        self.assertNotIn("去大街上拉住", prompt)
        self.assertNotIn("随便问十个人", prompt)
        self.assertNotIn("9999", prompt)

    def test_guozhijiliang_script_stats_counts_length_and_paragraphs(self) -> None:
        script = "第一段有内容。\n\n第二段也有内容。"
        stats = guozhijiliang_script_stats(script)

        self.assertEqual(2, stats["paragraphs"])
        self.assertGreater(stats["chars"], 10)
        self.assertEqual(1000, MIN_GUOZHIJILIANG_SCRIPT_CHARS)
        self.assertEqual(20, MIN_GUOZHIJILIANG_SCRIPT_PARAGRAPHS)

    def test_guozhijiliang_opening_rejects_weak_intros(self) -> None:
        self.assertTrue(guozhijiliang_opening_needs_rewrite("在那个年代，很多科学家都很伟大。\n第二段"))
        self.assertTrue(guozhijiliang_opening_needs_rewrite("茅以升是我国著名桥梁专家。\n第二段"))
        self.assertFalse(guozhijiliang_opening_needs_rewrite("他亲手建起的大桥，最后却要亲手炸掉。\n第二段"))

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

    def test_cover_title_rejects_vague_dialogue_and_generic_emotion(self) -> None:
        script = "妻子告诉他自己回不来了，让他别再等。听完以后，他一直低着头。"

        self.assertTrue(cover_title_needs_rewrite("她说回不来别等了", "他不敢抬头", script))
        self.assertTrue(cover_title_needs_rewrite("等了整整四十年", "他流下眼泪", script))

    def test_cover_title_accepts_evidence_backed_core_fact_without_forced_suspense(self) -> None:
        script = "他是地下党员，在台湾潜伏42年，为了等妻子一生没有再娶。"
        evidence = "他是地下党员，在台湾潜伏42年，为了等妻子一生没有再娶"

        reasons = cover_title_rejection_reasons("地下党潜伏42年", "一生没有再娶", script, evidence)

        self.assertEqual([], reasons)

    def test_generate_viral_title_fails_instead_of_using_fallback(self) -> None:
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

        self.assertEqual("", title["line1"])
        self.assertEqual("", title["line2"])
        self.assertIn("error", title)

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

        self.assertEqual("她捐出千万", title["line1"])
        self.assertEqual("却穿15块鞋", title["line2"])
        self.assertEqual("反差型", title["style"])

    def test_generate_viral_title_skips_fake_contrast_candidate(self) -> None:
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

        self.assertEqual("丈夫被关监狱", title["line1"])
        self.assertEqual("她四处奔走营救", title["line2"])

    def test_parse_title_candidates_reads_json_array(self) -> None:
        candidates = parse_title_candidates('[{"first_line":"美国扣下箱子","second_line":"到底怕什么","style":"悬念型"}]')

        self.assertEqual("美国扣下箱子", candidates[0]["first_line"])

    def test_cover_title_prompt_balances_core_fact_and_local_conflict_without_templates(self) -> None:
        import inspect
        from services import text_service

        source = inspect.getsource(text_service.generate_viral_title)
        self.assertIn("局部瞬间并不天然优于全文核心事实", source)
        self.assertIn("可以写有具体身份、数字、任务或结果的事实概括标题", source)
        self.assertIn("一次生成12组不同角度的候选", source)
        self.assertIn("允许输出事实型概括标题", source)
        self.assertIn("evidence_quote", source)
        self.assertNotIn("好的结构示例", source)
        self.assertNotIn("高停留词优先", source)
        self.assertNotIn("第一行：父亲去世那天", source)
        self.assertNotIn("她说回不来别等了", source)

    def test_rewrite_prompt_only_keeps_book_promotion_when_source_has_it(self) -> None:
        plain_prompt = build_rewrite_prompt("他拒绝高薪，回到祖国继续研究。", "纪实故事型", 1)
        book_prompt = build_rewrite_prompt("翻开《国之脊梁》，你才知道他的选择。", "纪实故事型", 1)

        self.assertIn("原文不包含带书或图书推荐内容", plain_prompt)
        self.assertIn("禁止主动添加书名", plain_prompt)
        self.assertIn("原文包含带书或图书推荐内容", book_prompt)

    def test_rewrite_prompt_can_force_the_selected_sales_book(self) -> None:
        prompt = build_rewrite_prompt(
            "原文没有任何带书内容。",
            "纪实故事型",
            1,
            append_book_promotion=True,
            promotion_book_title="《国之脊梁》",
        )

        self.assertIn("用户已主动开启结尾带书", prompt)
        self.assertIn("最后一个自然段必须", prompt)
        self.assertIn("《国之脊梁》", prompt)

    def test_missing_sales_book_is_appended_once_at_the_end(self) -> None:
        script = "这是已经完成的二创故事。"

        promoted = ensure_rewrite_book_promotion(script, True, "《国之脊梁》")
        promoted_again = ensure_rewrite_book_promotion(promoted, True, "国之脊梁")

        self.assertTrue(promoted.endswith("值得和孩子一起慢慢看。"))
        self.assertIn("《国之脊梁》", promoted)
        self.assertEqual(promoted, promoted_again)

    def test_rewrite_analysis_prompt_reads_the_complete_source(self) -> None:
        source = "开头事实。" + ("中间事实" * 300) + "全文末尾的重要结果"

        prompt = build_rewrite_analysis_prompt(source)

        self.assertIn("全文末尾的重要结果", prompt)
        self.assertIn("只做内容理解和事实拆解", prompt)
        self.assertIn("资料卡不是摘要", prompt)
        self.assertIn("后续二创稿仍要写到约", prompt)
        self.assertIn("转发机制：这是核心", prompt)
        self.assertIn("评论、点赞、关注", prompt)
        self.assertIn("section_plan", prompt)
        self.assertIn("总计不超过200字", prompt)
        self.assertIn("同一事件只写一次", prompt)
        self.assertIn("不得增加字段", prompt)
        self.assertNotIn('"detail_density"', prompt)
        self.assertNotIn('"retention_beats"', prompt)

    def test_second_stage_uses_fact_brief_without_source_body(self) -> None:
        source = "固定开头必须保留。原文正文里的独特句子绝不能进入第二步提示词。"
        fact_brief = normalize_rewrite_fact_brief({
            "core_subject": "测试人物",
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
        self.assertIn('"target_rewrite_length": 2000', prompt)
        self.assertIn("事实资料卡不是篇幅上限", prompt)
        self.assertNotIn("原文正文里的独特句子", prompt)
        self.assertNotIn("<raw_script>", prompt)

    def test_short_rewrite_retry_uses_targeted_expansion(self) -> None:
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

        self.assertIn("本轮首要任务：定向扩写", prompt)
        self.assertIn("至少增加约 1000 字", prompt)
        self.assertIn("不要另起炉灶重写成另一篇短稿", prompt)

    def test_eighty_percent_rewrite_returns_with_a_warning_when_other_quality_passes(self) -> None:
        comparison = {
            "passed": False,
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

        self.assertEqual("length_warning", result["rewrite_quality_status"])
        self.assertIn("85%", result["rewrite_warning"])
        self.assertEqual(3, result["rewrite_attempts"])

    def test_rewrite_prompt_requires_body_to_continue_protected_opening(self) -> None:
        source = (
            "他在台湾潜伏四十二年后终于回乡，却发现妻子身边早已儿孙满堂。"
            "正当他准备转身时，妻子追出来说了一番话，把他感动得老泪纵横。"
            "他原本是一个广东读书人，后来去了台湾。"
        )

        prompt = build_rewrite_prompt(source, "纪实故事型", 1, preserve_rule="chars_67")

        self.assertIn("后续正文必须紧接固定开头最后一句继续讲", prompt)
        self.assertIn("不得沿着原文的段落结构逐段改写", prompt)
        self.assertIn("重新选择一条更有吸引力的叙事主线", prompt)
        self.assertIn("原文叙事骨架必须推翻重建", prompt)
        self.assertIn("不能直接写“谁能想到，一个广东读书人……”", prompt)
        self.assertIn("信息揭示顺序、段落顺序、各段功能", prompt)

    def test_rewrite_prompt_avoids_fragmented_paragraphs_and_preserves_fixed_opening(self) -> None:
        prompt = build_rewrite_prompt(
            "这是一段由用户选择并且需要完整保留的固定开头，即使它本身超过五十个中文字符也不能删改或者生硬截断。后续正文继续展开。",
            "纪实故事型",
            1,
            preserve_rule="chars_55",
        )

        self.assertIn("画面和语义完整优先于字数", prompt)
        self.assertIn("每段建议控制在 40 到 60 个中文字符", prompt)
        self.assertIn("少于 25 个字符的段落", prompt)
        self.assertIn("不能只因为达到某个字数就强行拆段", prompt)
        self.assertIn("固定开头必须完整原样保留", prompt)
        self.assertIn("不能从一句话中间生硬截断", prompt)

    def test_rewrite_comparison_includes_the_fixed_opening(self) -> None:
        fixed_opening = "这段固定开头必须一字不改而且仍然参与相似度计算"
        original = f"{fixed_opening}。他在一九八零年来到北京，随后进入实验室工作。"
        rewritten = f"{fixed_opening}。抵达首都以后，他把余下的时间都交给了科研。"

        comparison = compare_scripts(original, rewritten, fixed_opening)

        self.assertTrue(any(fixed_opening in passage for passage in comparison["reused_passages"]))
        self.assertEqual(0, comparison["protected_opening_length"])
        self.assertIn("keyword_overlap", comparison)
        self.assertIn("source_phrase_reuse", comparison)

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

    def test_rewrite_comparison_rejects_identical_text(self) -> None:
        comparison = compare_scripts("完全相同的文案", "完全相同的文案")

        self.assertEqual(100, comparison["continuous_reuse"])
        self.assertEqual(100, comparison["phrase_overlap"])
        self.assertEqual(0, comparison["overall_difference"])
        self.assertFalse(comparison["passed"])

    def test_rewrite_retry_receives_previous_draft_and_reused_passages(self) -> None:
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

        self.assertIn(previous_script, prompt)
        self.assertIn("重点重复片段：仍然照着原文写的重复表达", prompt)
        self.assertIn("固定开头也参与重复率和总体重构度计算", prompt)
        self.assertIn("放弃上一版的段落、句序和叙事路径", prompt)

    def test_keywords_from_text_does_not_slice_script_fragments(self) -> None:
        tags = keywords_from_text("alpha beta gamma delta epsilon")

        flattened = tags["people"] + tags["scene"] + tags["era"] + tags["keywords"]
        self.assertEqual([], flattened)

    def test_generate_shots_leaves_tags_empty_when_ai_visuals_unavailable(self) -> None:
        script = "alpha beta gamma delta epsilon\nzeta eta theta iota kappa"

        with patch.dict("os.environ", {"MINIMAX_API_KEY": ""}):
            shots = generate_shots(script)

        self.assertGreaterEqual(len(shots), 1)
        for shot in shots:
            self.assertEqual([], shot["required_object"])
            self.assertEqual([], shot["required_scene"])
            self.assertEqual([], shot["object_tags"])
            self.assertEqual([], shot["scene_tags"])
            self.assertEqual([], shot["keywords"])
            self.assertEqual([], shot["search_keywords"])

    def test_clean_shot_visual_terms_rejects_sentence_fragments(self) -> None:
        terms = clean_shot_visual_terms(
            [
                "\u7684\u538b\u529b",
                "\u753b\u9762\u9700\u8981",
                "validSubject",
                "subject,with,punctuation",
                "toolongfragmentvalue",
            ],
            max_length=12,
        )

        self.assertEqual(["validSubject"], terms)


if __name__ == "__main__":
    unittest.main()
