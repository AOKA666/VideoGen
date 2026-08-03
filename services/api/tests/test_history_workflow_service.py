from __future__ import annotations

import json
import os
import unittest
from unittest.mock import MagicMock, patch

try:
    from fastapi import HTTPException
    from routers.projects import (
        HistoryChatPayload,
        HistoryModelPayload,
        chat_history_workflow,
        finalize_history_workflow,
        run_history_workflow_step,
        select_history_workflow_model,
    )
    ROUTER_TESTS_AVAILABLE = True
except ModuleNotFoundError:
    ROUTER_TESTS_AVAILABLE = False
from services.history_workflow_service import (
    _request_history_ai,
    _step_two_review_notes,
    build_history_chat_messages,
    build_history_step_messages,
    extract_history_fixed_opening,
    generate_history_step,
    load_history_workflow_prompt,
    normalize_history_model_provider,
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
        self.assertIn("【前三秒固定开头】", task)
        self.assertIn("不得增删、改写或调整顺序", task)

    def test_step_two_requires_and_uses_confirmed_strategy(self) -> None:
        with self.assertRaisesRegex(ValueError, "step1"):
            build_history_step_messages(2, "参考文案", {})

        messages = build_history_step_messages(
            2,
            "参考文案",
            {"1": "## 【前三秒固定开头】\n\n> 固定开头原文。\n\n## 【3个开场方案】\n三个方案"},
            {"1": [
                {"role": "user", "content": "采用第二个倒叙开场"},
                {"role": "assistant", "content": "已记录，下一步会按倒叙开场创作。"},
            ]},
        )
        self.assertIn("用户已确认阶段一结果", messages[-1]["content"])
        self.assertIn("采用第二个倒叙开场", messages[-1]["content"])
        self.assertIn("必须优先落实用户", messages[-1]["content"])
        self.assertIn("固定开头之前不得添加标题", messages[-1]["content"])
        self.assertIn("固定开头原文。", messages[-1]["content"])
        self.assertIn("不设目标字数和篇幅上下限", messages[-1]["content"])
        self.assertNotIn("1000-1500字", messages[-1]["content"])
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
            {"1": "【前三秒固定开头】\n固定开头。\n\n【切入视角】\n阶段一策略"},
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
        self.assertIn("不要预写冗长带书话术", step1[-1]["content"])
        self.assertIn("带书只允许一个紧凑自然段", step2[-1]["content"])
        self.assertIn("不超过200个汉字", step2[-1]["content"])
        self.assertIn("最后一个自然段必须专门介绍", step2[-1]["content"])
        self.assertIn("带货篇幅是否合适", step3[-1]["content"])
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

    @patch(
        "services.history_workflow_service._request_history_ai",
        return_value="固定开头。完整结果，推荐阅读《国之脊梁》。",
    )
    def test_generation_uses_larger_budget_for_script_steps(self, request_ai) -> None:
        result = generate_history_step(
            2,
            "参考文案",
            {"1": "【前三秒固定开头】\n固定开头。\n\n【切入视角】\n策略"},
        )

        self.assertEqual("固定开头。完整结果，推荐阅读《国之脊梁》。", result)
        self.assertEqual(12000, request_ai.call_args_list[0].kwargs["max_tokens"])
        self.assertEqual("minimax", request_ai.call_args_list[0].kwargs["provider"])
        self.assertEqual(3, request_ai.call_count)
        optimization_prompt = request_ai.call_args_list[1].args[0][-1]["content"]
        for dimension in (
            "开头钩子", "故事性", "结构", "共鸣感", "完播率", "情绪力度", "带书转化",
            "带货篇幅是否合适", "评论互动",
        ):
            self.assertIn(dimension, optimization_prompt)
        self.assertIn("不超过200个汉字", optimization_prompt)
        self.assertIn("删除重复卖点", optimization_prompt)
        self.assertIn("最后一个自然段必须专门介绍", optimization_prompt)
        self.assertIn("最终只输出局部优化后的完整口播正文", optimization_prompt)

    def test_step_two_review_notes_flag_book_introduction_missing_at_the_end(self) -> None:
        script = "固定开头。中段介绍《国之脊梁》。" + ("后续故事仍在继续。" * 30)

        notes = _step_two_review_notes(script, "固定开头。", "《国之脊梁》")

        self.assertTrue(any("正文没有以介绍《国之脊梁》的带书段落结尾" in note for note in notes))

    def test_fixed_opening_is_extracted_without_markdown_wrappers(self) -> None:
        strategy = (
            "## 【前三秒固定开头】\n\n"
            "> 王阳明，一针见血地指出，人到中年才发现。\n\n"
            "## 【爆款逻辑洞察】\n后续分析"
        )

        self.assertEqual(
            "王阳明，一针见血地指出，人到中年才发现。",
            extract_history_fixed_opening(strategy),
        )

    @patch(
        "services.history_workflow_service._request_history_ai",
        side_effect=[
            "错误的新开头，且没有带书。",
            "固定开头原文。优化后的完整故事。推荐阅读《王阳明心学》，书中能帮助读者理解心学，点击即可了解。",
            '{"winner":"draft_2","reason":"修复了固定开头和带书结尾","required_edits":[]}',
        ],
    )
    def test_step_two_includes_low_scoring_draft_notes_in_the_only_rewrite(self, request_ai) -> None:
        result = generate_history_step(
            2,
            "固定开头原文。参考文案后文。",
            {"1": "【前三秒固定开头】\n> 固定开头原文。\n\n【切入视角】\n策略"},
            promotion_book_title="王阳明心学",
        )

        self.assertTrue(result.startswith("固定开头原文。"))
        self.assertIn("《王阳明心学》", result)
        self.assertIn("优化后的完整故事", result)
        self.assertEqual(3, request_ai.call_count)
        optimization_messages = request_ai.call_args_list[1].args[0]
        self.assertIn("只对确有必要的局部做一版优化候选稿", optimization_messages[-1]["content"])
        self.assertIn("首稿评分时需重点关注", optimization_messages[-1]["content"])
        self.assertIn("正文没有逐字以【前三秒固定开头】起笔", optimization_messages[-1]["content"])
        self.assertIn("正文没有自然植入《王阳明心学》", optimization_messages[-1]["content"])
        self.assertIn("纳入九维检查", optimization_messages[-1]["content"])

    @patch(
        "services.history_workflow_service._request_history_ai",
        side_effect=[
            "第一稿更自然。",
            "第二稿重复解释过多。",
            '{"winner":"draft_1","reason":"首稿更自然","required_edits":[]}',
        ],
    )
    def test_step_two_can_keep_the_first_draft(self, request_ai) -> None:
        result = generate_history_step(
            2,
            "固定开头。参考文案后文。",
            {"1": "【前三秒固定开头】\n固定开头。\n\n【切入视角】\n策略"},
            promotion_book_title="王阳明心学",
        )

        self.assertEqual("第一稿更自然。", result)
        self.assertEqual(3, request_ai.call_count)

    @patch(
        "services.history_workflow_service._request_history_ai",
        side_effect=[
            "固定开头。首稿正文。最后介绍《王阳明心学》。",
            "固定开头。优化稿正文。最后介绍《王阳明心学》。",
            '{"winner":"draft_2","reason":"优化稿更紧凑","required_edits":["压缩第三段"]}',
            "固定开头。优化稿正文已压缩。最后介绍《王阳明心学》。",
        ],
    )
    def test_step_two_only_applies_required_local_edits_to_the_winner(self, request_ai) -> None:
        details = generate_history_step(
            2,
            "固定开头。参考文案后文。",
            {"1": "【前三秒固定开头】\n固定开头。\n\n【切入视角】\n策略"},
            promotion_book_title="王阳明心学",
            return_details=True,
        )

        self.assertEqual("draft_2", details["winner"])
        self.assertEqual(["压缩第三段"], details["required_edits"])
        self.assertTrue(details["local_edit_applied"])
        self.assertIn("优化稿正文已压缩", details["final_output"])
        self.assertEqual(4, request_ai.call_count)
        local_edit_prompt = request_ai.call_args_list[3].args[0][-1]["content"]
        self.assertIn("不得整体重写", local_edit_prompt)
        self.assertIn("压缩第三段", local_edit_prompt)

    @patch("services.history_workflow_service._request_history_ai", return_value="DeepSeek 结果")
    def test_generation_uses_selected_deepseek_provider(self, request_ai) -> None:
        result = generate_history_step(
            1,
            "参考文案",
            model_provider="deepseek",
        )

        self.assertEqual("DeepSeek 结果", result)
        self.assertEqual("deepseek", request_ai.call_args.kwargs["provider"])

    def test_model_provider_rejects_unknown_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "minimax, deepseek, or openai"):
            normalize_history_model_provider("unknown")

    def test_deepseek_request_uses_deepseek_configuration(self) -> None:
        response = MagicMock()
        response.read.return_value = (
            b'{"choices":[{"message":{"content":"DeepSeek response"}}]}'
        )
        response_context = MagicMock()
        response_context.__enter__.return_value = response
        with patch.dict(os.environ, {
            "DEEPSEEK_API_KEY": "test-key",
            "DEEPSEEK_ENDPOINT": "https://api.deepseek.com",
            "DEEPSEEK_MODEL": "deepseek-v4-flash",
        }), patch(
            "services.history_workflow_service.urllib.request.urlopen",
            return_value=response_context,
        ) as urlopen:
            result = _request_history_ai(
                [{"role": "user", "content": "测试"}],
                provider="deepseek",
            )

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual("DeepSeek response", result)
        self.assertEqual("https://api.deepseek.com/chat/completions", request.full_url)
        self.assertEqual("deepseek-v4-flash", payload["model"])
        self.assertEqual({"type": "disabled"}, payload["thinking"])

    def test_openai_request_uses_openai_configuration_and_parameters(self) -> None:
        response = MagicMock()
        response.read.return_value = (
            b'{"choices":[{"message":{"content":"OpenAI response"}}]}'
        )
        response_context = MagicMock()
        response_context.__enter__.return_value = response
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "test-key",
            "OPENAI_ENDPOINT": "https://api.openai.com/v1",
            "OPENAI_MODEL": "gpt-5.6",
        }), patch(
            "services.history_workflow_service.urllib.request.urlopen",
            return_value=response_context,
        ) as urlopen:
            result = _request_history_ai(
                [{"role": "user", "content": "测试"}],
                max_tokens=7000,
                provider="openai",
            )

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual("OpenAI response", result)
        self.assertEqual("https://api.openai.com/v1/chat/completions", request.full_url)
        self.assertEqual("gpt-5.6", payload["model"])
        self.assertEqual(7000, payload["max_completion_tokens"])
        self.assertEqual("medium", payload["reasoning_effort"])
        self.assertNotIn("thinking", payload)
        self.assertNotIn("max_tokens", payload)
        self.assertNotIn("temperature", payload)

    def test_loaded_protocol_uses_the_complete_prompt_file(self) -> None:
        load_history_workflow_prompt.cache_clear()
        protocol = load_history_workflow_prompt()

        self.assertIn("开头保护红线 (Opening Lock)", protocol)
        self.assertIn("原参考文案开头三秒对应的口播文字", protocol)
        self.assertIn("不设目标字数和篇幅上下限", protocol)
        for dimension in (
            "开头钩子", "故事性", "结构", "共鸣感", "完播率", "情绪力度", "带书转化",
            "带货篇幅是否合适", "评论互动",
        ):
            self.assertIn(dimension, protocol)
        self.assertIn("不超过200个汉字", protocol)
        self.assertIn("最后一个自然段必须专门介绍", protocol)
        self.assertIn("不得默认优化稿优于首稿", protocol)
        self.assertIn("不得再次整体重写", protocol)
        self.assertNotIn("1000-1500字", protocol)


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
        self.assertEqual("minimax", result["workflow"]["model_provider"])
        save_db.assert_called_once_with(self.db)

    @patch("routers.projects.save_db")
    @patch("routers.projects.generate_history_step", return_value="重新生成的第二步正文")
    @patch("routers.projects.load_db")
    def test_step_two_can_be_regenerated_without_restarting_step_one(
        self,
        load_db,
        _generate,
        save_db,
    ) -> None:
        self.project["history_workflow"] = {
            "active_step": 3,
            "status": "awaiting_confirmation",
            "outputs": {"1": "保留的第一步策略", "2": "旧正文", "3": "旧定稿"},
            "messages": {
                "1": [{"role": "user", "content": "保留的第一步意见"}],
                "2": [{"role": "user", "content": "旧第二步意见"}],
                "3": [{"role": "user", "content": "旧第三步意见"}],
            },
        }
        self.project["rewritten_script"] = "旧定稿"
        load_db.return_value = self.db

        result = run_history_workflow_step("project-1", 2)

        workflow = self.project["history_workflow"]
        self.assertTrue(result["regenerated"])
        self.assertEqual(2, workflow["active_step"])
        self.assertEqual("保留的第一步策略", workflow["outputs"]["1"])
        self.assertEqual("重新生成的第二步正文", workflow["outputs"]["2"])
        self.assertNotIn("3", workflow["outputs"])
        self.assertEqual("保留的第一步意见", workflow["messages"]["1"][0]["content"])
        self.assertEqual([], workflow["messages"]["2"])
        self.assertNotIn("3", workflow["messages"])
        self.assertEqual("重新生成的第二步正文", self.project["rewritten_script"])
        save_db.assert_called_once_with(self.db)

    @patch("routers.projects.save_db")
    @patch("routers.projects.generate_history_step")
    @patch("routers.projects.load_db")
    def test_step_two_persists_draft_comparison(self, load_db, generate, save_db) -> None:
        comparison = {
            "draft_1": "首稿",
            "draft_2": "优化稿",
            "final_output": "首稿局部修改版",
            "draft_1_metrics": {"character_count": 2},
            "draft_2_metrics": {"character_count": 3},
            "winner": "draft_1",
            "reason": "首稿更自然",
            "required_edits": ["压缩第三段"],
        }
        generate.return_value = comparison
        self.project["history_workflow"] = {
            "active_step": 1,
            "status": "awaiting_confirmation",
            "outputs": {"1": "第一步策略"},
            "messages": {"1": []},
        }
        load_db.return_value = self.db

        result = run_history_workflow_step("project-1", 2)

        self.assertEqual("首稿局部修改版", result["output"])
        self.assertEqual(comparison, result["workflow"]["step2_comparison"])
        self.assertEqual("首稿局部修改版", self.project["rewritten_script"])
        self.assertTrue(generate.call_args.kwargs["return_details"])
        save_db.assert_called_once_with(self.db)

    @patch("routers.projects.save_db")
    @patch("routers.projects.generate_history_step", return_value="重新生成的第三步定稿")
    @patch("routers.projects.load_db")
    def test_completed_step_three_can_be_regenerated_independently(
        self,
        load_db,
        _generate,
        save_db,
    ) -> None:
        self.project["history_workflow"] = {
            "active_step": 3,
            "status": "completed",
            "outputs": {"1": "第一步策略", "2": "第二步正文", "3": "旧定稿"},
            "messages": {"1": [], "2": [], "3": []},
        }
        load_db.return_value = self.db

        result = run_history_workflow_step("project-1", 3)

        workflow = self.project["history_workflow"]
        self.assertTrue(result["regenerated"])
        self.assertEqual("第一步策略", workflow["outputs"]["1"])
        self.assertEqual("第二步正文", workflow["outputs"]["2"])
        self.assertEqual("重新生成的第三步定稿", workflow["outputs"]["3"])
        self.assertEqual("awaiting_confirmation", workflow["status"])
        self.assertEqual("重新生成的第三步定稿", self.project["rewritten_script"])
        save_db.assert_called_once_with(self.db)

    @patch("routers.projects.save_db")
    @patch("routers.projects.generate_history_step", return_value="重新生成的第一步策略")
    @patch("routers.projects.load_db")
    def test_step_one_regeneration_invalidates_all_later_steps(
        self,
        load_db,
        _generate,
        save_db,
    ) -> None:
        self.project["history_workflow"] = {
            "active_step": 3,
            "status": "completed",
            "outputs": {"1": "旧策略", "2": "旧正文", "3": "旧定稿"},
            "messages": {"1": [], "2": [], "3": []},
        }
        self.project["rewritten_script"] = "旧定稿"
        load_db.return_value = self.db

        result = run_history_workflow_step("project-1", 1)

        self.assertTrue(result["regenerated"])
        self.assertEqual({"1": "重新生成的第一步策略"}, self.project["history_workflow"]["outputs"])
        self.assertEqual("", self.project["rewritten_script"])
        self.assertEqual("created", self.project["status"])
        save_db.assert_called_once_with(self.db)

    @patch("routers.projects.save_db")
    @patch("routers.projects.load_db")
    def test_model_selection_is_persisted_without_resetting_workflow(
        self,
        load_db,
        save_db,
    ) -> None:
        self.project["history_workflow"] = {
            "active_step": 1,
            "outputs": {"1": "阶段一结果"},
        }
        load_db.return_value = self.db

        result = select_history_workflow_model(
            "project-1",
            HistoryModelPayload(provider="deepseek"),
        )

        self.assertEqual("deepseek", result["provider"])
        self.assertEqual("deepseek", self.project["history_model_provider"])
        self.assertEqual("阶段一结果", self.project["history_workflow"]["outputs"]["1"])
        self.assertEqual("deepseek", self.project["history_workflow"]["model_provider"])
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
