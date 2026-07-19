from __future__ import annotations

import sys
import unittest
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from services.generation_service import build_image_prompt  # noqa: E402
from services.text_service import _build_shot_visuals_prompt  # noqa: E402


class ImagePromptTests(unittest.TestCase):
    def test_seedream_prompt_uses_archival_old_photo_tone(self) -> None:
        prompt = build_image_prompt({"visual_need": "昏暗实验室里的科研人员"})

        self.assertTrue(prompt.startswith(
            "真实历史纪实影像风格，档案老照片质感，旧色调；"
            "画面需求：昏暗实验室里的科研人员。"
        ))
        self.assertNotIn("档案照片质感，克制色彩，电影级构图", prompt)

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

    def test_visual_design_prompt_rejects_alternative_and_overloaded_scenes(self) -> None:
        prompt = _build_shot_visuals_prompt(
            [{"id": "shot-1", "voice_text": "他辗转多地完成研究。"}],
            "他辗转多地完成研究。",
        )

        self.assertIn("每个镜头只能写一个明确、固定的场景", prompt)
        self.assertIn("不得写“A或者B”", prompt)
        self.assertIn("不要试图把旁白中的所有人物、物品、事件和象征元素都塞进同一画面", prompt)


if __name__ == "__main__":
    unittest.main()
