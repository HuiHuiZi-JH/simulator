# -*- coding: utf-8 -*-
"""The read-only endpoints the Live diagram's detail panel is built on.

The panel names every register a device publishes, so what it lists has to be
the register map itself rather than a second copy of it that could drift.

Run from the repository root:  python3 -m unittest discover -s test -t .
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.communication import web_server
from config.modbus_registers import REGISTERS


class PointCatalogue(unittest.TestCase):
    def setUp(self):
        self.catalogue = web_server.point_catalogue()

    def test_every_device_type_is_covered(self):
        self.assertEqual(set(self.catalogue), set(REGISTERS))

    def test_every_point_is_listed_once_at_its_own_offset(self):
        for dev_type, entries in self.catalogue.items():
            points = REGISTERS[dev_type]['points']
            self.assertEqual(len(entries), len(points), dev_type)
            for entry in entries:
                self.assertEqual(entry['offset'], points[entry['name']])

    def test_points_come_back_in_register_order(self):
        # The panel lists them top to bottom; a Modbus client reads them in the
        # same order, so the two agree without the page having to sort.
        for dev_type, entries in self.catalogue.items():
            offsets = [e['offset'] for e in entries]
            self.assertEqual(offsets, sorted(offsets), dev_type)

    def test_descriptions_are_the_ones_in_the_register_map(self):
        for dev_type, entries in self.catalogue.items():
            described = REGISTERS[dev_type].get('descriptions', {})
            for entry in entries:
                self.assertEqual(entry['description'], described.get(entry['name'], ''))

    def test_control_registers_are_marked_writable(self):
        # The same three the web and Modbus interfaces accept writes on, and
        # nothing else: an output marked writable would invite a write the next
        # tick silently overwrites.
        marked = set()
        for dev_type, entries in self.catalogue.items():
            for entry in entries:
                if entry['writable']:
                    marked.add((dev_type, entry['name']))
        expected = set()
        for dev_type, points in web_server.WRITABLE.items():
            for name in points:
                expected.add((dev_type, name))
        self.assertEqual(marked, expected)
        self.assertEqual(len(expected), 3)


if __name__ == '__main__':
    unittest.main()
