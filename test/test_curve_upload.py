# -*- coding: utf-8 -*-
"""Curve files uploaded from the dashboard.

The upload is the one path where a file the models depend on arrives from
outside the repository, so it is checked on the terms the models read it: the
same parser, a name that cannot leave config/, and an atomic write that cannot
half-replace a curve that was good.

Run from the repository root:  python3 -m unittest discover -s test -t .
"""
import logging
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.communication import web_server
from src.models.curve import DayCurve

GOOD = "hour,kW\n0.0,100\n6.0,400\n12.0,1000\n18.0,300\n"

# Rejecting a file logs about the rows it could not read; that is the point, but
# it is noise in a test run.
logging.getLogger('state').addHandler(logging.NullHandler())


class CurveUpload(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.original = web_server.CONFIG_DIR
        web_server.CONFIG_DIR = self.dir

    def tearDown(self):
        web_server.CONFIG_DIR = self.original
        shutil.rmtree(self.dir)

    def path(self, name):
        return os.path.join(self.dir, name)

    def test_a_good_curve_is_written_and_described(self):
        ok, res = web_server.save_curve('my_load.csv', GOOD)
        self.assertTrue(ok, res)
        self.assertEqual(res['points'], 4)
        self.assertEqual((res['min'], res['max']), (100.0, 1000.0))
        self.assertFalse(res['replaced'])
        self.assertTrue(os.path.exists(self.path('my_load.csv')))

    def test_what_is_accepted_here_is_what_the_model_reads(self):
        web_server.save_curve('my_load.csv', GOOD)
        curve = DayCurve(self.path('my_load.csv'), 'test')
        self.assertEqual(len(curve), 4)
        self.assertAlmostEqual(curve.at(9.0), 700.0)

    def test_a_curve_with_no_usable_rows_is_refused(self):
        ok, res = web_server.save_curve('junk.csv', 'not,a,curve\nstill,not\n')
        self.assertFalse(ok)
        self.assertFalse(os.path.exists(self.path('junk.csv')))
        self.assertTrue(res['errors'])

    def test_names_that_are_not_curve_files_are_refused(self):
        for name in ('../device.json', 'curve.json', '.hidden.csv', '', None):
            ok, res = web_server.save_curve(name, GOOD)
            self.assertFalse(ok, name)
            self.assertTrue(res['errors'])
        self.assertEqual(os.listdir(self.dir), [])

    def test_a_path_is_reduced_to_its_file_name(self):
        # Whatever the client calls it, the file lands in config/ and nowhere
        # else -- the directory part never survives.
        ok, res = web_server.save_curve('/etc/passwd.csv', GOOD)
        self.assertTrue(ok, res)
        self.assertEqual(res['name'], 'passwd.csv')
        self.assertEqual(os.listdir(self.dir), ['passwd.csv'])

    def test_an_oversized_file_is_refused(self):
        huge = GOOD + '#' + 'x' * web_server.MAX_CURVE_BYTES + '\n'
        ok, res = web_server.save_curve('huge.csv', huge)
        self.assertFalse(ok)
        self.assertFalse(os.path.exists(self.path('huge.csv')))

    def test_an_existing_curve_is_only_replaced_when_asked(self):
        web_server.save_curve('my_load.csv', GOOD)
        ok, res = web_server.save_curve('my_load.csv', "0,5\n12,9\n")
        self.assertFalse(ok)
        self.assertTrue(res['exists'])
        with open(self.path('my_load.csv'), encoding='utf-8') as f:
            self.assertIn('1000', f.read())

        ok, res = web_server.save_curve('my_load.csv', "0,5\n12,9\n", overwrite=True)
        self.assertTrue(ok, res)
        self.assertTrue(res['replaced'])
        self.assertEqual((res['min'], res['max']), (5.0, 9.0))

    def test_a_refused_upload_leaves_no_temporary_file(self):
        web_server.save_curve('my_load.csv', GOOD)
        web_server.save_curve('my_load.csv', 'rubbish\n')
        self.assertEqual(os.listdir(self.dir), ['my_load.csv'])


class CurveListing(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.original = web_server.CONFIG_DIR
        web_server.CONFIG_DIR = self.dir

    def tearDown(self):
        web_server.CONFIG_DIR = self.original
        shutil.rmtree(self.dir)

    def write(self, name, text):
        with open(os.path.join(self.dir, name), 'w', encoding='utf-8') as f:
            f.write(text)

    def test_only_readable_curves_are_offered(self):
        self.write('good.csv', GOOD)
        self.write('empty.csv', '')
        self.write('device.json', '{}')
        listed = web_server.list_curves()
        self.assertEqual([c['name'] for c in listed], ['good.csv'])
        self.assertEqual(listed[0]['points'], 4)

    def test_the_repository_curves_are_all_offered(self):
        web_server.CONFIG_DIR = self.original
        names = [c['name'] for c in web_server.list_curves()]
        for shipped in ('jtc_common_curve.csv', 'load_curve.csv',
                        'pv_curve.csv', 't7_pv_curve.csv'):
            self.assertIn(shipped, names)


if __name__ == '__main__':
    unittest.main()
