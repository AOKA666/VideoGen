from __future__ import annotations

import unittest
from unittest.mock import patch

try:
    from fastapi import HTTPException
    from routers.projects import (
        HistoryChatPayload,
        chat_history_workflow,
        finalize_history_workflow,
        run_history_workflow_step,
    )
    ROUTER_TESTS_AVAILABLE = True
except ModuleNotFoundError:
    ROUTER_TESTS_AVAILABLE = False
from services.history_workflow_service import (
    build_history_chat_messages,
    build_history_step_messages,
    generate_history_step,
    load_history_workflow_prompt,
)


class HistoryWorkflowServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.prompt_patch = patch(
            "services.history_workflow_service.load_history_workflow_prompt",
            return_value="严格执行三步历史创作协议。",
        )
        self.prompt_patch.start()

    def tearDown(self) -> None:
        self.prompt_patch.stop()

    def test_step_one_only_requests_strategy_output(self) -> None:
        messages = build_history_step_messages(1, "参考历史文案")
        task = messages[-1]["content"]

        self.assertIn("执行 /step1", task)
        self.assertIn("不要写正文", task)
        self.assertIn("参考历史文案", task)

    def test_step_two_requires_and_uses_confirmed_strategy(self) -> None:
        with self.assertRaisesRegex(ValueError, "step1"):
            build_history_step_messages(2, "参考文案", {})

        messages = build_history_step_messages(
            2,
            "参考文案",
            {"1": "三个开场方案"},
            {"1": [
                {"role": "user", "content": "采用第二个倒叙开场"},
                {"role": "assistant", "content": "已记录，下一步会按倒叙开场创作。"},
            ]},
        )
        self.assertIn("用户已确认阶段一结果", messages[-1]["content"])
        self.assertIn("采用第二个倒叙开场", messages[-1]["content"])
        self.assertIn("必须优先落实用户", messages[-1]["content"])
        self.assertNotIn("执行 /step3", messages[-1]["content"])

    def test_step_three_uses_step_two_draft(self) -> None:
        messages = build_history_step_messages(
            3,
            "参考文案",
            {"1": "策略", "2": "第二步完整正文"},
        )
        self.assertIn("执行 /step3", messages[-1]["content"])
        self.assertIn("第二步完整正文", messages[-1]["content"])
        self.assertIn("只输出可直接录制的定稿", messages[-1]["content"])

    def test_selected_book_is_applied_to_all_three_steps_and_chat(self) -> None:
        step1 = build_history_step_messages(
            1,
            "参考文案",
            promotion_book_title="历史深处的民国",
        )
        step2 = build_history_step_messages(
            2,
            "参考文案",
            {"1": "阶段一策略"},
            promotion_book_title="历史深处的民国",
        )
        step3 = build_history_step_messages(
            3,
            "参考文案",
            {"2": "阶段二正文"},
            promotion_book_title="历史深处的民国",
        )
        chat = build_history_chat_messages(
            1,
            "参考文案",
            "阶段一结果",
            "选择第二个方案",
            promotion_book_title="历史深处的民国",
        )

        for messages in (step1, step2, step3, chat):
            combined = "\n".join(item["content"] for item in messages)
            self.assertIn("《历史深处的民国》", combined)
        self.assertIn("带书衔接策略", step1[-1]["content"])
        self.assertIn("不得改推其他书", step3[-1]["content"])

    def test_chat_records_stage_answer_without_rewriting_current_output(self) -> None:
        messages = build_history_chat_messages(
            1,
            "参考文案",
            "当前策略",
            "采用第二个开场",
            [{"role": "user", "content": "加强冲突"}],
        )
        self.assertIn("严禁写正文", messages[0]["content"])
        self.assertIn("不要重写、替换或输出当前阶段完整结果", messages[0]["content"])
        self.assertIn("采用第二个开场", messages[-1]["content"])

    @patch("services.history_workflow_service._request_history_ai", return_value="完整结果")
    def test_generation_uses_larger_budget_for_script_steps(self, request_ai) -> None:
        result = generate_history_step(2, "参考文案", {"1": "策略"})

        self.assertEqual("完整结果", result)
        self.assertEqual(12000, request_ai.call_args.kwargs["max_tokens"])

    def test_loaded_protocol_excludes_initialization_and_everything_after_it(self) -> None:
        load_history_workflow_prompt.cache_clear()
        protocol = load_history_workflow_prompt()

        self.assertNotIn("## 🚀 Initialization (初始化)", protocol)
        self.assertNotIn("协议已加载", protocol)


@unittest.skipUnless(ROUTER_TESTS_AVAILABLE, "FastAPI is not installed in this local test environment")
class HistoryWorkflowRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = {
            "id": "project-1",
            "raw_script": "一段历史参考文案",
            "rewritten_script": "",
            "status": "created",
        }
        self.db = {"projects": [self.project]}

    @patch("routers.projects.save_db")
    @patch("routers.projects.generate_history_step", return_value="阶段一结果")
    @patch("routers.projects.load_db")
    def test_step_one_initializes_persistent_workflow(self, load_db, _generate, save_db) -> None:
        load_db.return_value = self.db

        result = run_history_workflow_step("project-1", 1)

        self.assertEqual(1, result["workflow"]["active_step"])
        self.assertEqual("阶段一结果", self.project["history_workflow"]["outputs"]["1"])
        self.assertEqual("", self.project["rewritten_script"])
        save_db.assert_called_once_with(self.db)

    @patch("routers.projects.load_db")
    def test_step_two_cannot_skip_step_one(self, load_db) -> None:
        load_db.return_value = self.db

        with self.assertRaises(HTTPException) as raised:
            run_history_workflow_step("project-1", 2)

        self.assertEqual(409, raised.exception.status_code)

    @patch("routers.projects.save_db")
    @patch("routers.projects.revise_history_step", return_value="已记录，下一步会收紧结尾。")
    @patch("routers.projects.load_db")
    def test_chat_records_answer_without_replacing_current_output(
        self,
        load_db,
        _revise,
        _save_db,
    ) -> None:
        self.project["history_workflow"] = {
            "active_step": 3,
            "status": "completed",
            "outputs": {"2": "正文", "3": "旧定稿"},
            "messages": {"3": []},
        }
        load_db.return_value = self.db

        result = chat_history_workflow("project-1", HistoryChatPayload(message="收紧结尾"))

        self.assertEqual("旧定稿", result["output"])
        self.assertEqual("已记录，下一步会收紧结尾。", result["assistant_message"])
        self.assertEqual("awaiting_confirmation", result["workflow"]["status"])
        self.assertEqual("", self.project["rewritten_script"])
        self.assertEqual("收紧结尾", result["workflow"]["messages"]["3"][0]["content"])

    @patch("routers.projects.save_db")
    @patch("routers.projects.load_db")
    def test_finalize_commits_step_three_as_script(self, load_db, _save_db) -> None:
        self.project["history_workflow"] = {
            "active_step": 3,
            "status": "awaiting_confirmation",
            "outputs": {"3": "最终历史口播稿"},
        }
        load_db.return_value = self.db

        result = finalize_history_workflow("project-1")

        self.assertEqual("completed", result["workflow"]["status"])
        self.assertEqual("最终历史口播稿", self.project["rewritten_script"])


if __name__ == "__main__":
    unittest.main()
