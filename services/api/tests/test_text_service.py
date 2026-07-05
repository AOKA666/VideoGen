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
    choose_guozhijiliang_seed,
    clean_shot_visual_terms,
    cover_title_needs_rewrite,
    generate_viral_title,
    generate_shots,
    guozhijiliang_opening_needs_rewrite,
    guozhijiliang_script_stats,
    keywords_from_text,
    parse_title_candidates,
)


class ShotTagGenerationTests(unittest.TestCase):
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
        with patch.dict("os.environ", {"BIGMODEL_API_KEY": "test"}), patch(
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

        with patch.dict("os.environ", {"BIGMODEL_API_KEY": "test"}), patch(
            "services.text_service.urllib.request.urlopen",
            return_value=FakeResponse(),
        ):
            title = generate_viral_title("她捐出1000万，自己却穿15块钱胶鞋。")

        self.assertEqual("她捐出千万", title["line1"])
        self.assertEqual("却穿15块鞋", title["line2"])
        self.assertEqual("反差型", title["style"])

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

    def test_keywords_from_text_does_not_slice_script_fragments(self) -> None:
        tags = keywords_from_text("alpha beta gamma delta epsilon")

        flattened = tags["people"] + tags["scene"] + tags["era"] + tags["keywords"]
        self.assertEqual([], flattened)

    def test_generate_shots_leaves_tags_empty_when_ai_visuals_unavailable(self) -> None:
        script = "alpha beta gamma delta epsilon\nzeta eta theta iota kappa"

        with patch.dict("os.environ", {"BIGMODEL_API_KEY": ""}):
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
