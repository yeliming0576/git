# -*- coding: utf-8 -*-
"""renumber_nc.py 的单元测试与 5101/5102 集成测试。"""

import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import renumber_nc as rn
import revise_nc


def write_file(path, content):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    return path


def run_on(paths, **kwargs):
    """对一组文件运行完整后处理，返回 (summary, 输出文本列表)。"""
    summary = rn.run(paths, **kwargs)
    texts = []
    for p in paths:
        with open(p, 'r', encoding='utf-8') as f:
            texts.append(f.read())
    return summary, texts


class PatternTest(unittest.TestCase):
    def test_diameter_extraction(self):
        cases = [
            ('D170-CU-TANG-DAO', 170.0),
            ('D155.63-CU-TANG-DAO', 155.63),
            ('D6.135-CBN-MI-FENG-CAO-XI-DAO', 6.135),
            ('D120*12-SAN-MIAN-REN-XI-DAO', 120.0),
            ('D20.7-ZHENG-GUA-DAO-GK D11.5-DJ03-0179', 20.7),
            ('45 DU-DAO-JIAO-DAO-DJ10-0007-LIANG SHANG MIAN D8', 8.0),
            ('TAN-TOU', 0.0),
            ('G1/2-SI-GONG', 0.0),
            ('M22X1.5-SI-GONG', 0.0),
            ('D80-YU-MI-XI-DAO-DP30', 80.0),
        ]
        for name, expected in cases:
            self.assertEqual(rn.extract_diameter(name), expected, name)

    def test_probe_name(self):
        self.assertTrue(rn.is_probe('TAN-TOU'))
        self.assertTrue(rn.is_probe('TAN TOU'))
        self.assertFalse(rn.is_probe('D80-YU-MI-XI-DAO'))


class ProbeDeletionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_used_probe_at_start(self):
        src = write_file(os.path.join(self.tmp.name, 'a_processed'), (
            'T29(TAN-TOU)\n'
            'M1\n'
            'M6\n'
            'T33(D80-XI-DAO)\n'
            '(TAN-TOU)\n'
            'G0G90G54X-280.Y-57.\n'
            'G104H29\n'
            'G43Z475.H#505\n'
            'G65P9810Z245.F2500\n'
            'M1\n'
            'M6\n'
            'T50(D50-XI-DAO)\n'
            '(D80-XI-DAO)\n'
            'G104H33\n'
            'G43Z475.H33\n'
            'M1\n'
            'M6\n'
            '(D50-XI-DAO)\n'
            'G104H50\n'
        ))
        _, texts = run_on([src])
        out = texts[0]
        self.assertNotIn('TAN-TOU', out)
        self.assertNotIn('G65P9810', out)
        self.assertNotIn('G104H29', out)
        self.assertIn('T33(D80-XI-DAO)', out)
        self.assertIn('G104H33', out)
        self.assertIn('G43Z475.H33', out)
        self.assertIn('G104H50', out)

    def test_unused_probe_staging(self):
        src = write_file(os.path.join(self.tmp.name, 'a_processed'), (
            'T1(D10-XI-DAO)\n'
            'M1\n'
            'M6\n'
            'T89(TAN-TOU)\n'
            '(D10-XI-DAO)\n'
            'G104H1\n'
            'G43Z10.H1\n'
            'M1\n'
            'M60\n'
            'M30\n'
            'M1\n'
            '%\n'
        ))
        _, texts = run_on([src])
        out = texts[0]
        self.assertNotIn('TAN-TOU', out)
        self.assertIn('T1(D10-XI-DAO)', out)
        self.assertIn('G104H1', out)
        self.assertIn('M30', out)

    def test_post_m30_probe_section(self):
        src = write_file(os.path.join(self.tmp.name, 'a_processed'), (
            'M1\n'
            'M30\n'
            'N4321(WEI-YAN-ZHENG)\n'
            'T89(TAN-TOU)\n'
            'M1\n'
            'M6\n'
            '(M30-HOU-MIAN)\n'
            '(TAN-TOU)\n'
            'G0G90G56X0Y-258.\n'
            'G104H89\n'
            'G43Z100.H#505\n'
            'G65P9810Z-28.F2500.\n'
            'M1\n'
            'M9\n'
            'G90G53G0Z0M5\n'
            'G91G30Y0Z0M19\n'
            'M1\n'
            'M1\n'
            '%\n'
        ))
        _, texts = run_on([src])
        out = texts[0]
        self.assertNotIn('N4321', out)
        self.assertNotIn('TAN-TOU', out)
        self.assertNotIn('G65P9810', out)
        self.assertIn('M30', out)
        self.assertTrue(out.rstrip().endswith('%'))

    def test_no_remove_probe_keeps_probe(self):
        src = write_file(os.path.join(self.tmp.name, 'a_processed'), (
            'T29(TAN-TOU)\n'
            'M1\n'
            'M6\n'
            'T33(D80-XI-DAO)\n'
            '(TAN-TOU)\n'
            'G104H29\n'
            'G43Z475.H#505\n'
            'M1\n'
            'M6\n'
            '(D80-XI-DAO)\n'
            'G104H33\n'
        ))
        _, texts = run_on([src], remove_probe=False)
        self.assertIn('T29(TAN-TOU)', texts[0])
        self.assertIn('G104H29', texts[0])
        self.assertIn('G104H33', texts[0])


class RewriteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_h_var_d_rewrite(self):
        src = write_file(os.path.join(self.tmp.name, 'a_processed'), (
            'T9(D18-U-ZUAN-DP20)\n'
            'M1\n'
            'M6\n'
            'T12(D50-XI-DAO)\n'
            '(D18-U-ZUAN-DP20)\n'
            '#13009=15.966/2.\n'
            'IF[ABS[#13009-8.]GT0.3]THEN#3000=1.\n'
            'G0G90G54X-302.509Y37.\n'
            'G104H9\n'
            'G43Z315.H#505\n'
            'G41X-310.402Y39.89D9F200\n'
            'G65P524X-235.007Y32.M[#13009]R9.25Q-90.D9.\n'
            'M1\n'
            'M6\n'
            '(D50-XI-DAO)\n'
            'G104H12\n'
            'G43Z315.H12\n'
            'G41X10.Y10.D12\n'
        ))
        summary, texts = run_on([src], max_tools=8)
        out = texts[0]
        self.assertIn('T1(D18-U-ZUAN-DP20)', out)
        self.assertIn('#13001=15.966/2.', out)
        self.assertIn('IF[ABS[#13001-8.]', out)
        self.assertIn('G104H1\n', out)
        self.assertIn('G41X-310.402Y39.89D1F200', out)
        self.assertIn('G65P524X-235.007Y32.M[#13001]R9.25Q-90.D1.', out)
        self.assertIn('T2(D50-XI-DAO)', out)
        self.assertIn('G104H2\n', out)
        self.assertIn('G41X10.Y10.D2', out)
        self.assertEqual(summary['results'][0]['changed'], True)

    def test_t0_fallback_and_m0(self):
        src = write_file(os.path.join(self.tmp.name, 'a_processed'), (
            'T5(D200-CU-TANG-DAO)\n'
            'M1\n'
            'M6\n'
            'T1(D10-XI-DAO)\n'
            '(D200-CU-TANG-DAO)\n'
            'G104H5\n'
            'G43Z10.H#505\n'
            'M1\n'
            'M6\n'
            'T2(D20-XI-DAO)\n'
            '(D10-XI-DAO)\n'
            'G104H1\n'
            'M1\n'
            'M6\n'
            'T3(D30-XI-DAO)\n'
            '(D20-XI-DAO)\n'
            'G104H2\n'
            'M1\n'
            'M6\n'
            'T4(D40-XI-DAO)\n'
            '(D30-XI-DAO)\n'
            'G104H3\n'
            'M1\n'
            'M6\n'
            '(D40-XI-DAO)\n'
            'G104H4\n'
        ))
        summary, texts = run_on([src], max_tools=5)
        out = texts[0]
        self.assertEqual(out.count('M0(shou_gong_huan_dao)'), 6)
        self.assertIn('T0(D200-CU-TANG-DAO)', out)
        self.assertIn('G104H0\n', out)
        self.assertNotIn('G104H5\n', out)
        self.assertIn('G104H1\n', out)
        # T0 刀具报告
        self.assertIn('T0 手动换刀', summary['report_text'])

    def test_blank_lines_removed(self):
        src = write_file(os.path.join(self.tmp.name, 'a_processed'), (
            'T1(D10-XI-DAO)\n'
            '\n'
            '   \n'
            '\t\n'
            'M1\n'
            'M6\n'
            'T2(D20-XI-DAO)\n'
            '(D10-XI-DAO)\n'
            'G104H1  \n'
            'M1\n'
            'M6\n'
            '(D20-XI-DAO)\n'
            'G104H2\n'
            '%\n'
        ))
        summary, texts = run_on([src])
        out = texts[0]
        for line in out.splitlines():
            self.assertNotEqual(line.strip(), '')
        self.assertIn('G104H1  ', out)
        self.assertEqual(summary['results'][0]['blank_removed'], 3)


class AssignmentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_shared_tool_same_slot(self):
        a = write_file(os.path.join(self.tmp.name, 'a_processed'), (
            'T7(D80-XI-DAO)\n'
            'M1\n'
            'M6\n'
            'T9(D50-XI-DAO)\n'
            '(D80-XI-DAO)\n'
            'G104H7\n'
            'M1\n'
            'M6\n'
            '(D50-XI-DAO)\n'
            'G104H9\n'
        ))
        b = write_file(os.path.join(self.tmp.name, 'b_processed'), (
            'T7(D80-XI-DAO)\n'
            'M1\n'
            'M6\n'
            'T9(D50-XI-DAO)\n'
            '(D80-XI-DAO)\n'
            'G104H7\n'
            'M1\n'
            'M6\n'
            '(D50-XI-DAO)\n'
            'G104H9\n'
        ))
        _, texts = run_on([a, b])
        ta = re.search(r'^T(\d+)\(D80-XI-DAO\)', texts[0], re.M).group(1)
        tb = re.search(r'^T(\d+)\(D80-XI-DAO\)', texts[1], re.M).group(1)
        self.assertEqual(ta, tb)

    def test_small_and_large_separation(self):
        a = write_file(os.path.join(self.tmp.name, 'a_processed'), (
            'T1(D10-XI-DAO)\n'
            'M1\n'
            'M6\n'
            'T2(D20-XI-DAO)\n'
            '(D10-XI-DAO)\n'
            'G104H1\n'
            'M1\n'
            'M6\n'
            'T3(D30-XI-DAO)\n'
            '(D20-XI-DAO)\n'
            'G104H2\n'
            'M1\n'
            'M6\n'
            '(D30-XI-DAO)\n'
            'G104H3\n'
        ))
        b = write_file(os.path.join(self.tmp.name, 'b_processed'), (
            'T4(D160-MIAN-XI-DAO-F45)\n'
            'M1\n'
            'M6\n'
            'T2(D170-CU-TANG-DAO)\n'
            '(D160-MIAN-XI-DAO-F45)\n'
            'G104H4\n'
            'M1\n'
            'M6\n'
            '(D170-CU-TANG-DAO)\n'
            'G104H2\n'
        ))
        summary, texts = run_on([a, b], max_tools=10)
        # 大刀具（D160/D170）的邻位必须没有其它刀具
        occupied = set()
        large_slots = {}
        for text in texts:
            for m in re.finditer(r'^T(\d+)\((D1[67][0-9.]+-[^)]*)\)',
                                 text, re.M):
                large_slots[m.group(2)] = int(m.group(1))
            occupied.update(int(m.group(1)) for m in
                            re.finditer(r'^T(\d+)\(', text, re.M))
        self.assertEqual(len(large_slots), 2)
        for name, slot in large_slots.items():
            self.assertNotIn(slot - 1, occupied)
            self.assertNotIn(slot + 1, occupied)
        self.assertTrue(all(1 <= t <= 10 for t in occupied))


class RepeatStageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _three_tools(self):
        return write_file(os.path.join(self.tmp.name, 'a_processed'), (
            'T1(D10-XI-DAO)\n'
            'M1\n'
            'M6\n'
            'T2(D20-XI-DAO)\n'
            '(D10-XI-DAO)\n'
            'G104H1\n'
            'M1\n'
            'M6\n'
            'T3(D30-XI-DAO)\n'
            '(D20-XI-DAO)\n'
            'G104H2\n'
            'M1\n'
            'M6\n'
            '(D30-XI-DAO)\n'
            'G104H3\n'
        ))

    def test_repeat_stage_inserted(self):
        src = self._three_tools()
        summary, texts = run_on([src])
        out = texts[0]
        # 首次换刀不重复，其余每把刀出现两次（备刀 + 重复备刀）
        self.assertEqual(out.count('T1(D10-XI-DAO)'), 1)
        self.assertEqual(out.count('T2(D20-XI-DAO)'), 2)
        self.assertEqual(out.count('T3(D30-XI-DAO)'), 2)
        # 每个 M06（除首次外）紧贴上一行是备刀行
        lines = out.splitlines()
        m06_idx = [i for i, l in enumerate(lines) if re.search(r'\bM0?6\b', l)]
        self.assertGreaterEqual(len(m06_idx), 3)
        for idx in m06_idx[1:]:
            self.assertRegex(lines[idx - 1].strip(), r'^T\d+\(',
                             f'M06 前不是备刀行: {lines[idx - 1]!r}')
        self.assertIn('插入 2 行', summary['report_text'])

    def test_repeat_stage_disabled(self):
        src = self._three_tools()
        _, texts = run_on([src], repeat_stage=False)
        out = texts[0]
        self.assertEqual(out.count('T1(D10-XI-DAO)'), 1)
        self.assertEqual(out.count('T2(D20-XI-DAO)'), 1)
        self.assertEqual(out.count('T3(D30-XI-DAO)'), 1)

    def test_repeat_stage_skips_t0(self):
        src = write_file(os.path.join(self.tmp.name, 'a_processed'), (
            'T5(D200-CU-TANG-DAO)\n'
            'M1\n'
            'M6\n'
            'T1(D10-XI-DAO)\n'
            '(D200-CU-TANG-DAO)\n'
            'G104H5\n'
            'M1\n'
            'M6\n'
            'T2(D20-XI-DAO)\n'
            '(D10-XI-DAO)\n'
            'G104H1\n'
            'M1\n'
            'M6\n'
            'T3(D30-XI-DAO)\n'
            '(D20-XI-DAO)\n'
            'G104H2\n'
            'M1\n'
            'M6\n'
            'T4(D40-XI-DAO)\n'
            '(D30-XI-DAO)\n'
            'G104H3\n'
            'M1\n'
            'M6\n'
            '(D40-XI-DAO)\n'
            'G104H4\n'
        ))
        summary, texts = run_on([src], max_tools=5)
        out = texts[0]
        # T0 手动换刀不重复；其余正常刀具重复
        self.assertEqual(out.count('T0(D200-CU-TANG-DAO)'), 1)
        self.assertEqual(out.count('T1(D10-XI-DAO)'), 2)
        self.assertEqual(out.count('T2(D20-XI-DAO)'), 2)
        self.assertEqual(out.count('M0(shou_gong_huan_dao)'), 6)

    def test_repeat_stage_keeps_suffix_comment(self):
        src = write_file(os.path.join(self.tmp.name, 'a_processed'), (
            'T1(D10-XI-DAO)\n'
            'M1\n'
            'M6\n'
            'T9(D16-HE-JIN-LI-XI-DAO-DJ05-0142)(1111)\n'
            '(D10-XI-DAO)\n'
            'G104H1\n'
            'M1\n'
            'M6\n'
            'T3(D30-XI-DAO)\n'
            '(D16-HE-JIN-LI-XI-DAO-DJ05-0142)\n'
            'G104H9\n'
            'M1\n'
            'M6\n'
            '(D30-XI-DAO)\n'
            'G104H3\n'
        ))
        _, texts = run_on([src])
        out = texts[0]
        self.assertEqual(out.count('T9(D16-HE-JIN-LI-XI-DAO-DJ05-0142)(1111)'), 2)


class Integration5101Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        proj = os.path.dirname(os.path.abspath(__file__))
        self.src1 = os.path.join(self.tmp.name, '5101')
        self.src2 = os.path.join(self.tmp.name, '5102')
        shutil.copyfile(os.path.join(proj, '5101'), self.src1)
        shutil.copyfile(os.path.join(proj, '5102'), self.src2)
        self.out1 = os.path.join(self.tmp.name, '5101_processed')
        self.out2 = os.path.join(self.tmp.name, '5102_processed')
        revise_nc.process_gcode(self.src1, self.out1)
        revise_nc.process_gcode(self.src2, self.out2)
        self.summary, self.texts = run_on([self.out1, self.out2])

    def test_probe_and_blanks_removed(self):
        for text in self.texts:
            self.assertNotIn('TAN TOU', text)
            self.assertNotIn('TAN-TOU', text)
            for line in text.splitlines():
                self.assertNotEqual(line.strip(), '')

    def test_all_blocks_h_match_and_t_within_60(self):
        for path in (self.out1, self.out2):
            pf = rn.parse_file(path, remove_probe=False)
            self.assertTrue(pf['blocks'])
            for b in pf['blocks']:
                self.assertTrue(all(h == b.old_t for h in b.h_refs), b.name)
                self.assertTrue(1 <= b.old_t <= 60, b.old_t)

    def test_minimal_renumber_expected(self):
        text1, text2 = self.texts
        # 5101 的 T1(D24-YE-ZI) 移入 27
        self.assertIn('T27(D24-YE-ZI-DAO-DJ01-0098)', text1)
        self.assertNotIn('T1(D24-YE-ZI-DAO-DJ01-0098)', text1)
        # 5102 的 T3(D16-HE-JIN-ZUAN-DP40) 移入 29
        self.assertIn('T29(D16-HE-JIN-ZUAN-DP40)', text2)
        self.assertNotIn('T3(D16-HE-JIN-ZUAN-DP40)', text2)
        # 大刀具保持 2/4/6，邻位 1/3/5/7 无刀具
        self.assertIn('T2(D170-CU-TANG-DAO)', text2)
        self.assertIn('T4(D160-MIAN-XI-DAO-F45)', text2)
        self.assertIn('T6(D175.46-CU-TANG-DAO)', text2)
        occupied = set()
        for text in self.texts:
            occupied.update(int(m.group(1)) for m in
                            re.finditer(r'^T(\d+)\(', text, re.M))
        for nb in (1, 3, 5, 7):
            self.assertNotIn(nb, occupied)

    def test_report_file(self):
        self.assertTrue(os.path.isfile(self.summary['report_path']))
        with open(self.summary['report_path'], encoding='utf-8') as f:
            content = f.read()
        self.assertIn('刀具汇总', content)
        self.assertIn('规则校验', content)
        self.assertIn('重复备刀', content)

    def test_repeat_stage_before_each_m06(self):
        for text in self.texts:
            lines = text.splitlines()
            m06_idx = [i for i, l in enumerate(lines)
                       if re.search(r'\bM0?6\b', l)]
            self.assertGreaterEqual(len(m06_idx), 2)
            for idx in m06_idx[1:]:
                self.assertRegex(lines[idx - 1].strip(), r'^T\d+\(',
                                 f'M06 前不是备刀行: {lines[idx - 1]!r}')

    def test_cli_auto_renumber(self):
        with tempfile.TemporaryDirectory() as tmp2:
            src1 = os.path.join(tmp2, '5101')
            src2 = os.path.join(tmp2, '5102')
            shutil.copyfile(self.src1, src1)
            shutil.copyfile(self.src2, src2)
            code = revise_nc.run_cli([src1, src2], output_dir=tmp2, force=True)
            self.assertEqual(code, 0)
            self.assertTrue(os.path.isfile(os.path.join(tmp2, '5101_processed')))
            self.assertTrue(os.path.isfile(os.path.join(tmp2, 'tool_summary.txt')))
            with open(os.path.join(tmp2, '5101_processed'), encoding='utf-8') as f:
                self.assertNotIn('TAN TOU', f.read())


if __name__ == '__main__':
    unittest.main()
