"""评测集路径的跨环境回落（2026-10-31）。

e2e 报告 JSON 里的 `cases_file` 存的是「生成报告那台机器」的绝对路径。
宿主机上跑出来的报告存的是 `D:\\...\\backend\\rag_eval_cases.json`，容器里
没有 D 盘，照原路径找必然落空 —— `/evaluation/metrics` 因此在容器里恒 500
（连带 `metrics.csv` 也 500，本该是唯一能下载文件的那个接口）。

这里锁三件事：

1. 别的机器上的绝对路径也能解析（回落到仓库内同名文件）；
2. 本机真实路径照样走原路径，行为不因为这次改动而变化；
3. 真的找不到时照旧报错——回落不能变成静默兜底。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import smoke_rag as S


class LoadCasesPathTests(unittest.TestCase):
    def _load(self, path: str):
        return S.load_cases(path)

    def test_windows_absolute_path_from_other_machine(self):
        """核心回归：容器里读到宿主机生成的报告路径。"""
        self.assertTrue(self._load(r"D:\other\machine\rag_eval_cases.json"))

    def test_posix_absolute_path_from_other_machine(self):
        self.assertTrue(self._load("/somewhere/else/rag_eval_cases.json"))

    def test_mixed_separators(self):
        """报告路径里 \ 与 / 混杂也要能取出文件名。"""
        self.assertTrue(self._load(r"D:\other/machine\rag_eval_cases.json"))

    def test_real_path_is_still_preferred(self):
        """本机真实路径存在时不走回落：改动不能影响现有行为。"""
        self.assertTrue(self._load(str(S.CASES_FILE)))

    def test_default_path_works(self):
        self.assertTrue(self._load(""))

    def test_missing_file_still_raises(self):
        """连同名文件都不存在时必须照旧报错，不能静默当作空评测集。"""
        with self.assertRaises(SystemExit):
            self._load(r"D:\other\machine\no_such_cases.json")


if __name__ == "__main__":
    unittest.main()
