from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from services.generation_service import (  # noqa: E402
    build_image_prompt,
    storyboard_image_size,
)
from services.text_service import (  # noqa: E402
    _build_storyboard_plan_prompt,
    _build_shot_visuals_prompt,
    _materialize_storyboard_narration,
    _parse_storyboard_plan,
    _parse_storyboard_prompt_lines,
    _storyboard_model_config,
    ai_generate_shot_visuals,
    generate_shots,
    normalize_storyboard_model_provider,
    split_script_into_storyboards,
)


class ImagePromptTests(unittest.TestCase):
    def test_storyboard_plan_prompt_matches_direct_conversation_flow(self) -> None:
        prompt = _build_storyboard_plan_prompt("第一段。第二段。")

        self.assertIn("先根据内容、转折和重点把文案划分成6至9个分镜", prompt)
        self.assertIn("文案对应", prompt)
        self.assertIn("结束原句", prompt)
        self.assertIn("画面描述", prompt)
        self.assertIn("图片提示词", prompt)
        self.assertIn("不要返回JSON", prompt)
        self.assertIn("所有分镜画面中都禁止出现任何可读文字", prompt)
        self.assertIn("只表现无字外观", prompt)

    def test_storyboard_plan_parser_reads_natural_sections(self) -> None:
        content = """分镜一：总起
文案对应：
<旁白>第一段。</旁白>
画面描述：选择开场的重要画面。
图片提示词：
<提示词>老者站在庭院中回望来路。</提示词>

分镜2：转折
文案对应：
<旁白>第二段。</旁白>
画面描述：呈现人物面对选择。
图片提示词：
<提示词>文士在岔路前驻足沉思。</提示词>"""

        plan = _parse_storyboard_plan(content)

        self.assertEqual([1, 2], [item["shot_index"] for item in plan])
        self.assertEqual("第一段。", plan[0]["voice_text"])
        self.assertEqual("文士在岔路前驻足沉思。", plan[1]["visual_need"])

    def test_storyboard_end_quotes_split_original_script_without_rewriting(self) -> None:
        script = "第一段原文。\n\n第二段原文。\n第三段原文。"
        plan = [
            {"shot_index": 1, "end_quote": "第一段原文。", "visual_need": "画面一"},
            {"shot_index": 2, "end_quote": "第三段原文。", "visual_need": "画面二"},
        ]

        result = _materialize_storyboard_narration(script, plan)

        self.assertEqual("第一段原文。", result[0]["voice_text"])
        self.assertEqual("第二段原文。\n第三段原文。", result[1]["voice_text"])
        self.assertEqual("".join(script.split()), "".join(
            "".join(item["voice_text"] for item in result).split()
        ))

    def test_storyboard_end_quotes_tolerate_punctuation_changes(self) -> None:
        script = "第一段原文！\n第二段原文。"
        plan = [
            {"shot_index": 1, "end_quote": "第一段原文。", "visual_need": "画面一"},
            {"shot_index": 2, "end_quote": "第二段原文", "visual_need": "画面二"},
        ]

        result = _materialize_storyboard_narration(script, plan)

        self.assertEqual("第一段原文！", result[0]["voice_text"])
        self.assertEqual("第二段原文。", result[1]["voice_text"])

    def test_storyboard_end_quotes_match_nearest_original_sentence(self) -> None:
        script = "他先保存证据，再等待机会。随后把事实交给真正能处理的人。最后事情解决了。"
        plan = [
            {"shot_index": 1, "end_quote": "他保存证据并等待时机。", "visual_need": "画面一"},
            {"shot_index": 2, "end_quote": "把事实交给可以处理问题的人。", "visual_need": "画面二"},
            {"shot_index": 3, "end_quote": "最后事情解决了。", "visual_need": "画面三"},
        ]

        result = _materialize_storyboard_narration(script, plan)

        self.assertEqual("他先保存证据，再等待机会。", result[0]["voice_text"])
        self.assertEqual("随后把事实交给真正能处理的人。", result[1]["voice_text"])
        self.assertEqual("最后事情解决了。", result[2]["voice_text"])

    def test_generate_shots_uses_deepseek_storyboard_boundaries_and_prompts(self) -> None:
        plan = [
            {"shot_index": index, "voice_text": f"第{index}段。", "visual_need": f"第{index}幅具体画面"}
            for index in range(1, 7)
        ]
        with patch("services.text_service.ai_generate_storyboard_plan", return_value=plan), patch(
            "services.text_service.ai_generate_shot_visuals"
        ) as legacy_visuals:
            shots = generate_shots("".join(item["voice_text"] for item in plan))

        legacy_visuals.assert_not_called()
        self.assertEqual(6, len(shots))
        self.assertEqual("第1段。", shots[0]["voice_text"])
        self.assertEqual("第6幅具体画面", shots[5]["visual_need"])

    def test_local_narration_fallback_honors_ai_selected_shot_count(self) -> None:
        script = "".join(f"第{index}段讲述一个转折。" for index in range(1, 20))

        chunks = split_script_into_storyboards(script, target_count=6)

        self.assertEqual(6, len(chunks))
        self.assertEqual(script, "".join(chunks))

    def test_seedream_prompt_uses_unified_song_court_ink_style(self) -> None:
        prompt = build_image_prompt({"visual_need": "大殿中武将双手呈上无字奏章"})

        self.assertTrue(prompt.startswith("9:16竖屏，新国风宋式水墨工笔"))
        self.assertIn("古绢泛黄宣纸底色", prompt)
        self.assertIn("柔和均匀殿内柔光，无强烈明暗对比", prompt)
        self.assertIn("人物为中国古风", prompt)
        self.assertIn("纯手绘国画质感，无厚涂油画笔触，无CG塑料感", prompt)
        self.assertIn("画面全程无任何文字、字幕、水印、logo", prompt)
        self.assertIn("具体画面：大殿中武将双手呈上无字奏章", prompt)
        self.assertNotIn("固定负面提示词", prompt)
        self.assertNotIn("人物外貌", prompt)
        self.assertNotIn("档案老照片", prompt)
        self.assertEqual("1440x2560", storyboard_image_size())

    def test_seedream_prompt_does_not_expose_visual_design_instructions(self) -> None:
        prompt = build_image_prompt({"visual_need": "实验室或者戈壁滩上的科学家"})

        self.assertNotIn("只能呈现一个明确、固定的场景", prompt)
        self.assertNotIn("只选择其中最能表达旁白核心的一种", prompt)
        self.assertNotIn("只保留核心主体、一个关键动作和必要环境", prompt)

    def test_seedream_prompt_omits_stale_person_description_for_people_free_scene(self) -> None:
        prompt = build_image_prompt({
            "visual_need": "空旷戈壁中的试验塔",
            "person_gender": "none",
            "person_description": "中年中国男性，穿深色工作服",
        })

        self.assertNotIn("人物外貌", prompt)
        self.assertNotIn("中年中国男性", prompt)

    def test_visual_design_prompt_only_asks_deepseek_for_storyboard_prompts(self) -> None:
        prompt = _build_shot_visuals_prompt(
            [{"id": "shot-1", "voice_text": "他辗转多地完成研究。"}],
            "他辗转多地完成研究。",
        )

        self.assertIn("为每个分镜生成一条", prompt)
        self.assertIn("能清楚区分每个分镜即可", prompt)
        self.assertIn("不要返回 JSON", prompt)
        self.assertIn("所有画面都禁止出现任何可读文字", prompt)
        self.assertIn("书籍、匾额、信件、奏章、纸张和屏幕只能呈现无字外观", prompt)
        self.assertNotIn("80到180个汉字", prompt)
        self.assertNotIn("每镜只能有一个时刻", prompt)
        self.assertNotIn("不预设三国", prompt)

    def test_shot_prompt_generation_calls_deepseek(self) -> None:
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({
                    "choices": [{
                        "message": {
                            "content": "分镜1：中景平视，武将双手呈上无字奏章",
                        },
                    }],
                }, ensure_ascii=False).encode("utf-8")

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        with patch.dict(os.environ, {
            "DEEPSEEK_API_KEY": "test-key",
            "DEEPSEEK_ENDPOINT": "https://api.deepseek.test/v1",
            "DEEPSEEK_MODEL": "deepseek-test-model",
            "MINIMAX_API_KEY": "",
        }), patch("services.text_service.urllib.request.urlopen", side_effect=fake_urlopen):
            result = ai_generate_shot_visuals(
                [{"shot_index": 1, "voice_text": "武将入殿呈上奏章。"}],
                "武将入殿呈上奏章。",
            )

        self.assertEqual("https://api.deepseek.test/v1/chat/completions", captured["url"])
        self.assertEqual("deepseek-test-model", captured["payload"]["model"])
        self.assertEqual({"type": "disabled"}, captured["payload"]["thinking"])
        self.assertNotIn("response_format", captured["payload"])
        self.assertNotIn("search_keywords", captured["payload"]["messages"][-1]["content"])
        self.assertIn("武将双手呈上", result["1"]["visual_need"])

    def test_storyboard_model_provider_configs(self) -> None:
        with patch.dict(os.environ, {
            "MINIMAX_API_KEY": "mini-key",
            "MINIMAX_ENDPOINT": "https://minimax.test/v1",
            "MINIMAX_MODEL": "MiniMax-test",
            "OPENAI_API_KEY": "openai-key",
            "OPENAI_ENDPOINT": "https://openai.test/v1",
            "OPENAI_MODEL": "gpt-test",
        }):
            self.assertEqual("minimax", normalize_storyboard_model_provider("MiniMax"))
            self.assertEqual(
                ("mini-key", "https://minimax.test/v1", "MiniMax-test", "MiniMax"),
                _storyboard_model_config("minimax"),
            )
            self.assertEqual(
                ("openai-key", "https://openai.test/v1", "gpt-test", "OpenAI"),
                _storyboard_model_config("openai"),
            )

    def test_openai_storyboard_prompt_uses_openai_parameters(self) -> None:
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({
                    "choices": [{"message": {"content": "普通图片提示词"}}],
                }, ensure_ascii=False).encode("utf-8")

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "test-key",
            "OPENAI_ENDPOINT": "https://openai.test/v1",
            "OPENAI_MODEL": "gpt-test",
        }), patch("services.text_service.urllib.request.urlopen", side_effect=fake_urlopen):
            result = ai_generate_shot_visuals(
                [{"shot_index": 3, "voice_text": "人物进入庭院。"}],
                "人物进入庭院。",
                model_provider="openai",
            )

        self.assertEqual("https://openai.test/v1/chat/completions", captured["url"])
        self.assertEqual("gpt-test", captured["payload"]["model"])
        self.assertEqual("medium", captured["payload"]["reasoning_effort"])
        self.assertIn("max_completion_tokens", captured["payload"])
        self.assertNotIn("thinking", captured["payload"])
        self.assertEqual("普通图片提示词", result["3"]["visual_need"])

    def test_line_protocol_accepts_multiple_prompts_without_json(self) -> None:
        parsed = _parse_storyboard_prompt_lines(
            "分镜1：大殿内，老臣展开奏章\n\n2. 宫门外，骑兵冒雨抵达"
        )

        self.assertEqual("大殿内，老臣展开奏章", parsed["1"])
        self.assertEqual("宫门外，骑兵冒雨抵达", parsed["2"])

    def test_single_shot_uses_plain_response_as_prompt(self) -> None:
        parsed = _parse_storyboard_prompt_lines(
            "图片提示词：大殿深处，老臣独自站在烛火前。",
            ["4"],
        )

        self.assertEqual("大殿深处，老臣独自站在烛火前。", parsed["4"])

    def test_single_shot_ignores_model_renumbering(self) -> None:
        parsed = _parse_storyboard_prompt_lines(
            "1. 大殿深处，老臣独自站在烛火前。",
            ["4"],
        )

        self.assertEqual("大殿深处，老臣独自站在烛火前。", parsed["4"])
        self.assertNotIn("1", parsed)

    def test_deepseek_prompt_is_returned_without_content_rejection(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({
                    "choices": [{"message": {
                        "content": "建议呈现某个关键画面",
                    }}],
                }, ensure_ascii=False).encode("utf-8")

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}), patch(
            "services.text_service.urllib.request.urlopen",
            return_value=FakeResponse(),
        ):
            result = ai_generate_shot_visuals(
                [{"shot_index": 1, "voice_text": "武将入殿呈奏。"}],
                "武将入殿呈奏。",
            )

        self.assertEqual("建议呈现某个关键画面", result["1"]["visual_need"])

    def test_missing_prompt_is_requested_again_as_plain_text(self) -> None:
        responses = iter([
            "2. 宫门外，骑兵冒雨抵达",
            "大殿内，老臣展开奏章",
        ])

        class FakeResponse:
            def __init__(self, content):
                self.content = content

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({
                    "choices": [{"message": {"content": self.content}}],
                }, ensure_ascii=False).encode("utf-8")

        def fake_urlopen(_request, timeout=None):
            return FakeResponse(next(responses))

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}), patch(
            "services.text_service.urllib.request.urlopen",
            side_effect=fake_urlopen,
        ):
            result = ai_generate_shot_visuals([
                {"shot_index": 1, "voice_text": "老臣在殿内陈情。"},
                {"shot_index": 2, "voice_text": "骑兵冒雨赶到宫门。"},
            ], "老臣陈情，骑兵随后抵达。")

        self.assertEqual("大殿内，老臣展开奏章", result["1"]["visual_need"])
        self.assertEqual("宫门外，骑兵冒雨抵达", result["2"]["visual_need"])


if __name__ == "__main__":
    unittest.main()
