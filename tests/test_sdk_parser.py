import tempfile
import unittest
from pathlib import Path

from sdk_parser import SDKParser


class SDKParserLineNumberTests(unittest.TestCase):
    def test_chunked_parse_keeps_source_line_numbers_stable(self):
        source = """interface First {}\nexport {};\ndeclare global {\n  namespace IDP {\n    function exit(): void;\n  }\n}\nexport {};\ndeclare global {\n  interface Last {}\n}\n"""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.d.ts"
            path.write_text(source, encoding="utf-8")
            symbols = SDKParser(str(path)).parse()

        self.assertGreaterEqual(len(symbols), 2)
        self.assertTrue(all(1 <= symbol.start_line <= 10 for symbol in symbols))
        self.assertTrue(all(symbol.start_line <= symbol.end_line <= 10 for symbol in symbols))

    def test_exported_function_inherits_its_jsdoc_description(self):
        source = """declare global {
  namespace IDP {
    namespace Design {
      /** 保存方案 */
      export function save(): Promise<void>;
    }
  }
}
"""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.d.ts"
            path.write_text(source, encoding="utf-8")
            symbols = SDKParser(str(path)).parse()

        save_symbol = next(symbol for symbol in symbols if symbol.id == "IDP.Design.save")
        self.assertEqual(save_symbol.description, "保存方案")

    def test_function_references_preserve_source_order(self):
        """references 必须按源码声明顺序保序去重。

        回归背景：曾用 `list(set(refs))` 去重，set 迭代顺序随进程哈希种子变化，
        导致本地与 CI 构建出的 references 顺序不同，序列评测的
        related_types[0] / related_symbols[0] 位置断言随机失败。
        """
        source = """declare global {
  namespace IDP {
    export function upload(z: ZebraType, a: AppleType, z2: ZebraType): Promise<ResultType>;
  }
}
"""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.d.ts"
            path.write_text(source, encoding="utf-8")
            symbols = SDKParser(str(path)).parse()

        upload = next(symbol for symbol in symbols if symbol.id == "IDP.upload")
        self.assertEqual(upload.references, ["ZebraType", "AppleType", "ResultType"])


if __name__ == "__main__":
    unittest.main()
