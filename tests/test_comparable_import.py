from __future__ import annotations

import csv
import io
import unittest

from openpyxl import Workbook

from equity_agent.demo import demo_comparables
from equity_agent.market_method import PEER_FIELDS, parse_comparable_file


class ComparableImportTests(unittest.TestCase):
    def test_filled_template_preserves_price_basis_and_valuation_inputs(self) -> None:
        columns = [(key, label) for key, label in PEER_FIELDS if key != "source_location"]
        peer = demo_comparables()[0]
        headers = [label for _, label in columns]
        row = [peer.get(key) for key, _ in columns]
        workbook = Workbook()
        workbook.active.append(headers)
        workbook.active.append(row)
        excel = io.BytesIO()
        workbook.save(excel)
        csv_text = io.StringIO()
        writer = csv.writer(csv_text)
        writer.writerow(headers)
        writer.writerow(row)
        for name, payload in (
            ("filled-template.xlsx", excel.getvalue()),
            ("filled-template.csv", csv_text.getvalue().encode("utf-8-sig")),
        ):
            with self.subTest(format=name):
                imported = parse_comparable_file(name, payload).iloc[0]
                self.assertEqual(imported["price_basis"], peer["price_basis"])
                self.assertEqual(imported["period"], peer["period"])
                self.assertEqual(imported["source"], peer["source"])
                self.assertEqual(imported["weight_pct"], 25.0)
                self.assertEqual(imported["market_cap"], 18.0)
                self.assertTrue(imported["selected"])


if __name__ == "__main__":
    unittest.main()
