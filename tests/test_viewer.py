"""T8 display projection keeps scoped references and static uncertainties."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from onion.integration import _seal
from onion.viewer import project_workspace, export_viewer
from test_model_integration import workspace, reseal


def display_workspace():
    w = workspace()
    fid = w['references'][0]['function_id']
    function = w['extractions']['raw']['functions'][0]
    function['name'] = 'main'
    w['sources']['threads']['graphs'][0]['root_candidates'] = [function['id']]
    # Include raw source detail that looks like HTML to exercise safe export.
    function['blocks'][0]['elements'] = [{'kind': 'StringLiteral', 'text': '</script><img src=x>'}]
    return reseal(w), fid


class ViewerTests(unittest.TestCase):
    def test_scopes_cfg_blocks_preserves_unresolved_and_does_not_create_observations(self):
        w, fid = display_workspace()
        original = copy.deepcopy(w)
        view = project_workspace(w)
        self.assertEqual(view['roots'], [fid])
        self.assertFalse(view['runtime_evidence'])
        self.assertEqual(view['references'], w['references'])
        source_relations = w['structure']['relations']
        self.assertEqual([(r['id'], r['resolution'], r['target']) for r in view['links']],
                         [(r['id'], r['resolution'], r['target']) for r in source_relations])
        self.assertTrue(all(r['locations'] for r in view['links']))
        model = view['cfgs'][fid]
        self.assertTrue(all(n['id'].startswith(fid + '/') for n in model['nodes']))
        self.assertEqual(len(model['edges']), 8)
        self.assertEqual(w, original)

    def test_changed_workspace_rejected_and_missing_main_is_explicit(self):
        w, _ = display_workspace()
        w['target'] = 'different'
        with self.assertRaisesRegex(ValueError, 'changed'):
            project_workspace(w)
        w = workspace()
        self.assertEqual(project_workspace(w)['roots'], [])

    def test_standalone_html_inlines_assets_and_escapes_source(self):
        w, _ = display_workspace()
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / 'viewer.html'
            data = export_viewer([w], destination)
            html = destination.read_text()
            self.assertEqual(data['stages'], [])
            self.assertIn('function renderExecution()', html)
            self.assertNotIn('/*__STRUCTURE_', html)
            self.assertNotIn('</script><img src=x>', html)
            self.assertNotIn('<script src=', html)
            self.assertNotIn('<link ', html)
            with self.assertRaisesRegex(ValueError, 'duplicate workspace'):
                export_viewer([w, w], destination)


if __name__ == '__main__':
    unittest.main()
