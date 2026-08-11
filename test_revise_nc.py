"""revise_nc.py 的单元测试（标准库 unittest，无需第三方依赖）。"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revise_nc as rn


def run_convert(content):
    """把 content 当作 NC 文件处理，返回 (输出文本, 统计)。"""
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "in.nc")
        dst = os.path.join(tmp, "out.nc")
        with open(src, "w", encoding="utf-8") as f:
            f.write(content)
        stats = rn.process_gcode(src, dst)
        with open(dst, "r", encoding="utf-8") as f:
            text = f.read()
        return text, stats


class RuleTest(unittest.TestCase):
    def test_m_code_replacements(self):
        out, stats = run_convert("G0 M106\nM88 Z5.\nM135\n")
        self.assertEqual(out, "G0 M06\nM07 Z5.\nM109\n")
        self.assertEqual(stats["m106"], 1)
        self.assertEqual(stats["m88"], 1)
        self.assertEqual(stats["m135"], 1)

    def test_delete_rules(self):
        out, stats = run_convert(
            "IF[ABS[#1] GT 10] THEN GOTO 100\n"
            "G65P8040\nG52 #521\nG52 Z0\n")
        self.assertEqual(out, "")
        self.assertEqual(stats["del_if_abs"], 1)
        self.assertEqual(stats["del_g65"], 1)
        self.assertEqual(stats["del_g52_var"], 1)
        self.assertEqual(stats["del_g52_z0"], 1)

    def test_g52_not_misdeleted(self):
        content = "G52 Z0.5\nG52 Z01\nG52 #5200\n"
        out, stats = run_convert(content)
        self.assertEqual(out, content)
        self.assertEqual(stats["del_g52_var"], 0)
        self.assertEqual(stats["del_g52_z0"], 0)

    def test_g101_with_dot(self):
        out, stats = run_convert(
            "G101A54.B45.\n(注释)\nG0X100.\nG54\nG0Z5.\n")
        # 原语义：G54~G59 行保留（“保留 G52”），替换行插入在其前
        self.assertEqual(out,
                         "(注释)\nG0X100.\nG90G0G54B0\nG54\nG0Z5.\n")
        self.assertEqual(stats["g101"], 1)

    def test_g101_without_dot(self):
        out, stats = run_convert("G101A54B45.\nG55\n")
        self.assertEqual(out, "G90G0G55B0\nG55\n")
        self.assertEqual(stats["g101"], 1)

    def test_g101_g54_followed_by_axis_letter(self):
        # 回归：G54 后紧跟轴字母（无空格）也必须能识别
        out, stats = run_convert(
            "G101A54.B0.\nG0G90G54X-315.007Y322.\n")
        self.assertEqual(out, "G90G0G54B0\nG0G90G54X-315.007Y322.\n")
        self.assertEqual(stats["g101"], 1)

    def test_g101_not_found(self):
        content = "G101A54.B45.\nG0X10.\nG0Z1.\n"
        out, stats = run_convert(content)
        self.assertEqual(out, content)
        self.assertEqual(stats["g101"], 0)

    def test_g101_g540_not_g54(self):
        content = "G101A54.B45.\nG540\nG0Z1.\n"
        out, stats = run_convert(content)
        self.assertEqual(out, content)
        self.assertEqual(stats["g101"], 0)

    def test_g104_merge_basic(self):
        out, stats = run_convert("G104H12\nG43Z5.H#505\n")
        self.assertEqual(out, "G43Z5.H12\n")
        self.assertEqual(stats["g104"], 1)

    def test_g104_merge_negative_with_blank_and_comment(self):
        out, stats = run_convert("G104H5\n\n(注释)\nG43Z-1.5H#505\n")
        self.assertEqual(out, "G43Z-1.5H5\n")
        self.assertEqual(stats["g104"], 1)

    def test_g104_no_match(self):
        content = "G104H5\nG43Z-1.5H3\n"
        out, stats = run_convert(content)
        self.assertEqual(out, content)
        self.assertEqual(stats["g104"], 0)

    def test_encoding_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "gbk.nc")
            with open(src, "wb") as f:
                f.write("中文注释".encode("gbk"))
            with self.assertRaises(ValueError):
                rn.process_gcode(src, os.path.join(tmp, "out.nc"))


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        self.src = os.path.join(self.dir, "a.nc")
        with open(self.src, "w", encoding="utf-8") as f:
            f.write("G0 M106\nG101A54.\nG54\n")

    def test_cli_ok(self):
        self.assertEqual(rn.run_cli([self.src]), 0)
        out = os.path.join(self.dir, "a_processed.nc")
        self.assertTrue(os.path.isfile(out))
        with open(out, encoding="utf-8") as f:
            self.assertIn("G0 M06", f.read())

    def test_cli_skip_existing_and_force(self):
        out = os.path.join(self.dir, "a_processed.nc")
        with open(out, "w", encoding="utf-8") as f:
            f.write("旧内容")
        self.assertEqual(rn.run_cli([self.src]), 0)
        with open(out, encoding="utf-8") as f:
            self.assertEqual(f.read(), "旧内容")
        self.assertEqual(rn.run_cli([self.src], force=True), 0)
        with open(out, encoding="utf-8") as f:
            self.assertNotEqual(f.read(), "旧内容")

    def test_cli_output_dir(self):
        out_dir = os.path.join(self.dir, "out")
        self.assertEqual(rn.run_cli([self.src], output_dir=out_dir), 0)
        self.assertTrue(os.path.isfile(os.path.join(out_dir, "a_processed.nc")))

    def test_cli_missing_file(self):
        self.assertEqual(rn.run_cli([os.path.join(self.dir, "nope.nc")]), 1)

    def test_entry_uses_cli_with_args(self):
        with mock.patch.object(rn, "run_gui") as run_gui:
            with self.assertRaises(SystemExit):
                rn.entry([os.path.join(self.dir, "nope.nc")])
            run_gui.assert_not_called()

    def test_entry_uses_gui_without_args(self):
        with mock.patch.object(rn, "run_gui") as run_gui:
            rn.entry([])
            run_gui.assert_called_once()


if __name__ == "__main__":
    unittest.main()
