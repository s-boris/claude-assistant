"""Offline tests for ha-apply round 3: exact-message guard for the new write types,
add-on/dashboard/hidden_by restrictions, body-keyed options flow, rename planners and the rename apply path
(with reference rewrite) against a fake HA. Nothing touches the network."""
import copy, importlib.machinery, importlib.util, json, os, sys, tempfile, unittest
sys.dont_write_bytecode = True
BIN = os.environ.get('HA_APPLY_BIN', os.path.expanduser('~/bin/ha-apply'))
loader = importlib.machinery.SourceFileLoader('ha_apply_r3', BIN)
spec = importlib.util.spec_from_loader('ha_apply_r3', loader)
M = importlib.util.module_from_spec(spec)
loader.exec_module(M)
SLUG = 'john_doe_gmail_com'  # fake account slug


class GuardR3(unittest.TestCase):
    def test_exact_message_only(self):
        msg = {'entity_id': 'media_player.office', 'new_entity_id': 'media_player.living_room_left'}
        with self.assertRaises(M.NotAllowed):
            M.Guard(writes=False).check_ws('config/entity_registry/update', msg)
        g = M.Guard(writes=True)
        with self.assertRaises(M.NotAllowed):
            g.check_ws('config/entity_registry/update', msg)
        g.approve(('config/entity_registry/update', M.canon(msg)))
        g.check_ws('config/entity_registry/update', dict(msg))
        with self.assertRaises(M.NotAllowed):
            g.check_ws('config/entity_registry/update', dict(msg, new_entity_id='media_player.x'))
        with self.assertRaises(M.NotAllowed):
            g.check_ws('config/entity_registry/update', dict(msg, disabled_by='user'))

    def test_restrictions(self):
        g = M.Guard(writes=True)
        bad = [('supervisor/api', {'endpoint': '/addons/core_configurator/stop', 'method': 'post'}),
               ('supervisor/api', {'endpoint': '/addons/core_matter_server/uninstall', 'method': 'post'}),
               ('supervisor/api', {'endpoint': '/addons/core_matter_server/options', 'method': 'post',
                                   'data': {'boot': 'manual', 'options': {}}}),
               ('supervisor/api', {'endpoint': '/addons/core_matter_server/stop', 'method': 'post', 'data': {'x': 1}}),
               ('supervisor/api', {'endpoint': '/host/reboot', 'method': 'post'}),
               ('lovelace/config/save', {'url_path': 'home-mobile', 'config': {}}),
               ('config/entity_registry/update', {'entity_id': 'a.b', 'hidden_by': 'integration'})]
        for t, msg in bad:
            with self.assertRaises(M.NotAllowed, msg=str(msg)):
                g.approve((t, M.canon(msg)))
                g.check_ws(t, msg)
        ok = {'endpoint': '/addons/core_matter_server/options', 'method': 'post', 'data': {'boot': 'manual'}}
        g.approve(M.target_key('supervisor/api', ok))
        g.check_ws('supervisor/api', ok)

    def test_supervisor_reads(self):
        g = M.Guard(writes=False)
        g.check_ws('supervisor/api', {'endpoint': '/addons/core_matter_server/info', 'method': 'get'})
        for ep in ('/addons/core_matter_server/logs', '/backups', '/addons/x/info/../stop'):
            with self.assertRaises(M.NotAllowed):
                g.check_ws('supervisor/api', {'endpoint': ep, 'method': 'get'})

    def test_options_flow_body_keyed(self):
        g = M.Guard(writes=True)
        path = '/api/config/config_entries/options/flow'
        with self.assertRaises(M.NotAllowed):
            g.check_rest('POST', path, {'handler': 'abc'})
        g.approve(('rest', 'POST', path, M.canon({'handler': 'abc'})))
        g.check_rest('POST', path, {'handler': 'abc'})
        with self.assertRaises(M.NotAllowed):
            g.check_rest('POST', path, {'handler': 'other'})
        with self.assertRaises(M.NotAllowed):
            g.check_rest('POST', '/api/config/config_entries/flow', {'handler': 'abc'})

    def test_scene_rest(self):
        M.Guard(writes=False).check_rest('GET', '/api/config/scene/config/1704831840573')
        g = M.Guard(writes=True)
        with self.assertRaises(M.NotAllowed):
            g.check_rest('DELETE', '/api/config/scene/config/1')
        g.approve(('rest', 'DELETE', '/api/config/scene/config/1'))
        g.check_rest('DELETE', '/api/config/scene/config/1')
        with self.assertRaises(M.NotAllowed):
            g.check_rest('DELETE', '/api/config/script/config/1')


class Pure(unittest.TestCase):
    def test_refs_boundary(self):
        cfg = {'a': 'sensor.x_humidity', 'b': ['sensor.x_humidity_2', "{{ states('sensor.x_humidity') }}"],
               'c': 'sensor.x_humidity.attr'}
        rx = [(M._ref_rx('sensor.x_humidity'), 'sensor.bedroom_humidity')]
        new, n = M._replace_refs(cfg, rx)
        self.assertEqual(n, 3)
        self.assertEqual(new['b'][0], 'sensor.x_humidity_2')
        self.assertIn('sensor.bedroom_humidity', new['b'][1])

    def test_sonos_rebase(self):
        pl = M.SONOS_PLAN
        self.assertEqual(M._rebase('unnamed_room_bass_2', 'bedroom_speaker', pl['Bedroom'][2]), 'bedroom_speaker_bass')
        self.assertEqual(M._rebase('unnamed_room_2', 'bedroom_speaker', pl['Bedroom'][2]), 'bedroom_speaker')
        self.assertEqual(M._rebase('bedroom_tv_autoplay', 'bedroom_speaker', pl['Bedroom'][2]), 'bedroom_speaker_tv_autoplay')
        self.assertIsNone(M._rebase('bedroom_speaker_bass', 'bedroom_speaker', pl['Bedroom'][2]))
        self.assertEqual(M._rebase('unnamed_room', 'living_room_right', pl['Living Room (R)'][2]), 'living_room_right')
        self.assertEqual(M._rebase('living_room_r_tv_autoplay', 'living_room_right', pl['Living Room (R)'][2]),
                         'living_room_right_tv_autoplay')
        self.assertIsNone(M._rebase('living_room_left_x', 'living_room_left', pl['Living Room (L)'][2]))
        self.assertIs(M._rebase('kitchen', 'living_room_left', pl['Living Room (L)'][2]), False)

    def test_zb_new(self):
        self.assertEqual(M._zb_new('ikea_of_sweden_rodret_dimmer_battery_3', 'balcony', 'dimmer'), 'balcony_dimmer_battery')
        self.assertEqual(M._zb_new('sonoff_temperature_sensor_temperature_2', 'living_room', 'climate'), 'living_room_temperature')
        self.assertEqual(M._zb_new('ewelink_snzb_02p_lqi_2', 'hallway', 'climate'), 'hallway_climate_lqi')
        self.assertEqual(M._zb_new('philips_lom006', 'balcony', 'plug'), 'balcony_plug')
        self.assertEqual(M._zb_new('philips_lom006_power_on_behaviour', 'balcony', 'plug'), 'balcony_plug_power_on_behaviour')
        self.assertIsNone(M._zb_new('ikea_of_sweden_rodret_dimmer', 'x', 'dimmer'))


class FakeWS:
    def __init__(self, c, guard):
        self.c, self.guard, self.calls = c, guard, []

    def call(self, t, **p):
        self.guard.check_ws(t, p)
        self.calls.append((t, p))
        c = self.c
        if t == 'config/area_registry/list':
            return [{'area_id': a, 'name': n} for a, n in c.areas.items()]
        if t == 'lovelace/dashboards/list':
            return [{'url_path': u, 'mode': 'storage'} for u in c.dash]
        if t == 'lovelace/config':
            if 'url_path' not in p:
                raise M.A.HAError('config_not_found')
            return copy.deepcopy(c.dash[p['url_path']])
        if t == 'lovelace/config/save':
            c.dash[p['url_path']] = p['config']
            return None
        if t == 'config/device_registry/update':
            c.devs[p['device_id']].update({k: v for k, v in p.items() if k != 'device_id'})
            return None
        if t == 'config/entity_registry/update':
            e = c.reg.pop(p['entity_id'])
            if 'hidden_by' in p:
                e['hidden_by'] = p['hidden_by']
            e['entity_id'] = p.get('new_entity_id', p['entity_id'])
            c.reg[e['entity_id']] = e
            st = c.states.pop(p['entity_id'], None)
            if st is not None:
                c.states[e['entity_id']] = st
            return None
        raise AssertionError('unexpected ws call ' + t)


class FakeRest:
    def __init__(self, c, guard):
        self.c, self.guard, self.calls = c, guard, []

    def request(self, method, path, body=None):
        self.guard.check_rest(method, path, body)
        self.calls.append((method, path))
        aid = path.rsplit('/', 1)[1]
        if method == 'GET':
            return copy.deepcopy(self.c.autos[aid])
        self.c.autos[aid] = body
        return {'result': 'ok'}


class FakeCtx(M.Ctx):
    def __init__(self, guard):
        self.entries, self.devs, self.reg, self.states, self.attrs = {}, {}, {}, {}, {}
        self.areas = {'living_room': 'Living Room', 'bedroom': 'Bedroom', 'balcony': 'Balcony', 'hallway': 'Hallway'}
        self.dash, self.autos, self.audit = {}, {}, {}
        self.ws, self.rest = FakeWS(self, guard), FakeRest(self, guard)

    def refresh(self):
        pass

    def ent(self, eid, did, plat, ceid='E1'):
        self.reg[eid] = {'entity_id': eid, 'device_id': did, 'platform': plat, 'config_entry_id': ceid}
        self.states[eid] = 'on'


def world(guard):
    c = FakeCtx(guard)
    c.entries['ECO'] = {'entry_id': 'ECO', 'domain': 'ecovacs'}
    c.devs['dev_eco'] = {'id': 'dev_eco', 'config_entries': ['ECO'], 'name': SLUG, 'name_by_user': None, 'area_id': None}
    for o in ('', '_map', '_battery', '_mop_attached'):
        dom = {'': 'vacuum', '_map': 'image', '_battery': 'sensor', '_mop_attached': 'binary_sensor'}[o]
        c.ent(f'{dom}.{SLUG}{o}', 'dev_eco', 'ecovacs', 'ECO')
    c.entries['SON'] = {'entry_id': 'SON', 'domain': 'sonos'}
    for did, name, ids in (('dl', 'Living Room (L)', ['media_player.office', 'number.office_bass',
                                                      'switch.living_room_l_tv_autoplay']),
                           ('dr', 'Living Room (R)', ['media_player.unnamed_room', 'number.unnamed_room_bass']),
                           ('db', 'Bedroom', ['media_player.unnamed_room_2', 'number.unnamed_room_bass_2',
                                              'switch.bedroom_tv_autoplay'])):
        c.devs[did] = {'id': did, 'config_entries': ['SON'], 'name': name, 'name_by_user': None, 'area_id': None}
        for i in ids:
            c.ent(i, did, 'sonos', 'SON')
    c.ent('media_player.living_room_tv_2', 'dtv', 'cast')
    c.ent('media_player.home_theater_2', 'davr', 'heos')
    c.dash = {'dashboard-overview': {'views': [{'cards': [{'type': 'picture-entity', 'entity': f'image.{SLUG}_map'},
                                                          {'entity': 'media_player.office'}]}]},
              'home-mobile': {'views': [{'cards': [{'entity': f'image.{SLUG}_map'}]}]}}
    c.autos = {'a1': {'actions': [{'target': {'entity_id': 'media_player.unnamed_room_2'}}]}}
    c.states['automation.a1'] = 'on'
    c.attrs['automation.a1'] = {'id': 'a1'}
    return c


class Apply(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        M.UNDO = os.path.join(self.tmp.name, 'undo.json')
        M.time.sleep = lambda s: None

    def tearDown(self):
        self.tmp.cleanup()

    def test_vacuum_plan_has_no_slug(self):
        c = world(M.Guard(writes=False))
        du, rn, sk = M.sel_vacuum(c)
        self.assertEqual(du, [('dev_eco', {'name_by_user': M.ECO_NAME})])
        self.assertEqual(sorted(n for _, n in rn), ['binary_sensor.deebot_mop_attached', 'image.deebot_map',
                                                    'sensor.deebot_battery', 'vacuum.deebot'])
        self.assertFalse(any('gmail' in n for _, n in rn))

    def test_vacuum_apply_rewrites_refs_not_generated_dash(self):
        g = M.Guard(writes=True)
        c = world(g)
        M.g_vacuum(c, g, True)
        self.assertIn('vacuum.deebot', c.reg)
        self.assertEqual(c.devs['dev_eco']['name_by_user'], M.ECO_NAME)
        self.assertEqual(c.dash['dashboard-overview']['views'][0]['cards'][0]['entity'], 'image.deebot_map')
        self.assertEqual(c.dash['home-mobile']['views'][0]['cards'][0]['entity'], f'image.{SLUG}_map')
        undo = open(M.UNDO).read()
        self.assertNotIn('gmail', undo)
        self.assertTrue(json.loads(undo)['round3']['groups']['vacuum']['verify']['ok'])
        # idempotent: second run plans nothing
        du, rn, _ = M.sel_vacuum(c)
        self.assertEqual((du, rn), ([], []))

    def test_sonos_apply(self):
        g = M.Guard(writes=True)
        c = world(g)
        M.g_sonos(c, g, True)
        for i in ('media_player.living_room_left', 'number.living_room_left_bass', 'switch.living_room_left_tv_autoplay',
                  'media_player.living_room_right', 'media_player.bedroom_speaker', 'number.bedroom_speaker_bass',
                  'switch.bedroom_speaker_tv_autoplay'):
            self.assertIn(i, c.reg)
        self.assertEqual([c.devs[d]['area_id'] for d in ('dl', 'dr', 'db')], ['living_room', 'living_room', 'bedroom'])
        self.assertEqual(c.autos['a1']['actions'][0]['target']['entity_id'], 'media_player.bedroom_speaker')
        self.assertEqual(c.dash['dashboard-overview']['views'][0]['cards'][1]['entity'], 'media_player.living_room_left')

    def test_dupes_hidden(self):
        g = M.Guard(writes=True)
        c = world(g)
        M.g_dupes(c, g, True)
        self.assertEqual(c.reg['media_player.living_room_tv_cast']['hidden_by'], 'user')
        self.assertEqual(c.reg['media_player.home_theater_heos']['hidden_by'], 'user')

    def test_dry_run_writes_nothing(self):
        g = M.Guard(writes=False)
        c = world(g)
        before = copy.deepcopy((c.reg, c.devs, c.dash, c.autos))
        for fn in (M.g_vacuum, M.g_sonos, M.g_dupes):
            fn(c, g, False)
        self.assertEqual(before, (c.reg, c.devs, c.dash, c.autos))
        self.assertFalse(os.path.exists(M.UNDO))

    def test_collision_refused(self):
        g = M.Guard(writes=True)
        c = world(g)
        c.ent('media_player.living_room_left', 'other', 'x')
        with self.assertRaises(SystemExit):
            M.g_sonos(c, g, True)
        self.assertIn('media_player.office', c.reg)


if __name__ == '__main__':
    unittest.main()
