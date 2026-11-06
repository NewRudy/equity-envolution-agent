from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from equity_agent.demo import demo_comparables, demo_records
from equity_agent.workflow import CHECKS


APP = Path(__file__).resolve().parents[1] / "app.py"


def widget(group, label):
    return next(item for item in group if item.label == label)


class WorkbenchFlowTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"APP_PASSWORD": "", "OPC_PACKAGE_PATH": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app = AppTest.from_file(str(APP), default_timeout=20).run()
        self.assert_no_exception()

    def click(self, label):
        widget(self.app.button, label).click().run()
        self.assert_no_exception()

    def assert_no_exception(self):
        self.assertEqual([str(e.value) for e in self.app.exception], [])

    @property
    def project(self):
        return self.app.session_state["wb_projects"][self.app.session_state["wb_active"]]

    def test_demo_keeps_its_project_when_starting_a_real_one(self):
        self.click("打开合成示例")
        example = self.project
        self.assertEqual(self.app.session_state["wb_page"], "review")
        self.click("进入估值测算")
        self.click("生成合成估值草稿")
        self.assertTrue(example.result["is_demo"])
        self.assertAlmostEqual(example.result["market_method"]["result"]["equity_value"], 17.175)
        self.click("工作台总览")
        self.click("＋ 新建估值项目")
        self.assertEqual(self.project.mode, "real")
        self.assertEqual(self.project.selected(), [])
        self.assertEqual(self.project.records, [])
        self.assertEqual(self.app.session_state["wb_projects"][example.id].result, example.result)

    def test_real_project_requires_business_review_and_calculation_confirmation(self):
        self.click("＋ 新建估值项目")
        widget(self.app.text_input, "标的名称").set_value("测试标的")
        widget(self.app.text_input, "估值基准日").set_value("2026-11-04")
        widget(self.app.text_area, "主营产品或服务").set_value("工业软件")
        widget(self.app.text_input, "标的业务资料来源").set_value("测试案例 / 简介")
        self.click("保存并进入候选池")
        project = self.project
        project.records = demo_records()
        project.save_peer_inputs(demo_comparables(), "可比表.xlsx")
        project.policy["method_reason"] = "经营盈利与净债务口径已核对"
        self.app.run()
        self.click("进入三态复核")
        self.assertEqual(project.selected(), [])  # The file's prefilled adoption is never accepted.
        for i, code in enumerate(project.roster()):
            widget(self.app.selectbox, "当前公司").set_value(code).run()
            widget(self.app.radio, "复核决定").set_value("采纳")
            widget(self.app.text_area, "判断依据").set_value("产品、工厂客户与订阅方式相近")
            widget(self.app.text_input, "复核人").set_value("流程复核人")
            if i == 0:
                self.click("保存复核决定")
                self.assertEqual(project.selected(), [])
                self.assertTrue(any("补充可回查的业务证据" in str(e.value) for e in self.app.error))
            widget(self.app.text_input, "补充证据定位").set_value("业务年报第 18 页")
            self.click("保存复核决定")
        self.assertEqual(len(project.selected()), 4)
        self.click("进入估值测算")
        self.click("生成估值草稿")
        self.assertIsNone(project.result)
        for label in CHECKS.values():
            widget(self.app.checkbox, label).check()
        self.click("生成估值草稿")
        self.assertFalse(project.result["is_demo"])
        self.assertEqual(project.result["market_method"]["reviewer"], "流程复核人")
        self.click("返回估值测算")
        widget(self.app.radio, "本次比率").set_value("P/B").run()
        self.assertIsNone(project.result)
        self.assertTrue(widget(self.app.button, "生成估值草稿").disabled)
