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


if __name__ == "__main__":
    unittest.main()
