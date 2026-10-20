from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from services.text_service import (  # noqa: E402
    extract_leading_title,
    generate_viral_title,
    infer_title,
    parse_title_candidates,
)


class ProjectTitleTests(unittest.TestCase):
    def test_project_title_uses_the_first_eight_characters(self) -> None:
        source = "一二三四五六七八九十十一十二"

        self.assertEqual("一二三四五六七八", extract_leading_title(source))
        self.assertEqual("一二三四五六七八", infer_title(source))

    def test_project_title_stops_at_punctuation_before_ten_characters(self) -> None:
        self.assertEqual("谢汉光", extract_leading_title("谢汉光，潜伏台湾四十二年。"))
        self.assertEqual("开头没有标点符超", extract_leading_title("开头没有标点符超过十个字，后文。"))

    def test_project_title_ignores_leading_punctuation_and_whitespace(self) -> None:
        self.assertEqual("他回来了", extract_leading_title("  \n“他回来了！”后文"))
        self.assertEqual("未命名项目", extract_leading_title("，。！？"))


class TitleGenerationTests(unittest.TestCase):
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
        self.assertIn("evidence_quote", source)


if __name__ == "__main__":
    unittest.main()
