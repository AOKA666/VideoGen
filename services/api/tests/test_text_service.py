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
    RECENT_GUOZHIJILIANG_OPENINGS,
    RECENT_GUOZHIJILIANG_PEOPLE,
    build_guozhijiliang_script_prompt_v2,
    build_rewrite_prompt,
    choose_guozhijiliang_seed,
    clean_shot_visual_terms,
    cover_title_needs_rewrite,
    ensure_original_opening,
    extract_opening_hook,
    generate_viral_title,
    generate_shots,
    guozhijiliang_opening_needs_rewrite,
    guozhijiliang_script_stats,
    keywords_from_text,
    parse_title_candidates,
)


class ShotTagGenerationTests(unittest.TestCase):
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
                                {"first_line": "她捐出千万", "second_line": "却穿15块鞋", "style": "反差型"},
                                {"first_line": "伟大一生", "second_line": "民族脊梁", "style": "亏欠型"},
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
                                {"first_line": "丈夫被关监狱", "second_line": "她没先带娃回国", "style": "悬念型"},
                                {"first_line": "丈夫被关监狱", "second_line": "她四处奔走营救", "style": "冲突型"},
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

    def test_cover_title_prompt_allows_local_conflict_focus(self) -> None:
        import inspect
        from services import text_service

        source = inspect.getsource(text_service.generate_viral_title)
        self.assertIn("不是文案摘要", source)
        self.assertIn("最重要原则", source)
        self.assertIn("一个局部爆点", source)
        self.assertIn("请一次生成12组标题", source)

    def test_rewrite_prompt_only_keeps_book_promotion_when_source_has_it(self) -> None:
        plain_prompt = build_rewrite_prompt("他拒绝高薪，回到祖国继续研究。", "纪实故事型", 1)
        book_prompt = build_rewrite_prompt("翻开《国之脊梁》，你才知道他的选择。", "纪实故事型", 1)

        self.assertIn("原文不包含带书或图书推荐内容", plain_prompt)
        self.assertIn("禁止主动添加书名", plain_prompt)
        self.assertIn("原文包含带书或图书推荐内容", book_prompt)

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
