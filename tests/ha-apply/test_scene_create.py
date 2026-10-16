"""Offline tests for ha-apply scene-create: body-keyed scene POST, reload-only scene service (never turn_on),
dry run writes nothing, apply writes the 4 scenes with their old config ids and reads back the expected
entity ids, and a second run is a no-op. Fake HA; nothing touches the network."""
import copy, importlib.machinery, importlib.util, json, os, sys, tempfile, unittest
sys.dont_write_bytecode = True
BIN = os.environ.get('HA_APPLY_BIN', os.path.expanduser('~/bin/ha-apply'))
loader = importlib.machinery.SourceFileLoader('ha_apply_sc', BIN)
spec = importlib.util.spec_from_loader('ha_apply_sc', loader)
M = importlib.util.module_from_spec(spec)
loader.exec_module(M)
EXPECT = {'1704748847044': 'scene.movie', '1704831840573': 'scene.dim_light',
          '1704749350279': 'scene.ambient', '1704749075545': 'scene.full_light'}
LIGHTS = ('light.living_room_hanging_lamps', 'light.wled_4', 'light.wled', 'light.bedroom_lamp')


class FakeHA:
    """scene config store + registry; a reload turns stored configs into scene entities"""
    def __init__(self, guard, taken=()):
        self.guard, self.configs, self.reg, self.states, self.attrs = guard, {}, {}, {}, {}
        self.calls, self.audit, self.entries, self.devs = [], {}, {}, {}
        for l in LIGHTS:
            self.reg[l] = {'entity_id': l, 'platform': 'x'}
            self.states[l] = 'unavailable' if l == 'light.wled' else 'on'
        for eid in taken:
            self.reg[eid] = {'entity_id': eid, 'platform': 'homeassistant', 'unique_id': 'other'}
            self.states[eid] = 'unknown'
        self.ws, self.rest = self, self

    def refresh(self):
        pass

    def call(self, t, **p):
        self.guard.check_ws(t, p)
        self.calls.append(('ws', t, p))
        if t == 'call_service' and (p['domain'], p['service']) == ('scene', 'reload'):
            for sid, cfg in self.configs.items():
                eid = M._scene_eid(cfg['name'])
                if eid in self.reg and self.reg[eid].get('unique_id') != sid:
                    eid += '_2'
                self.reg[eid] = {'entity_id': eid, 'platform': 'homeassistant', 'unique_id': sid}
                self.states[eid] = 'unknown'
                self.attrs[eid] = {'id': sid, 'entity_id': list(cfg['entities'])}
            return None
        raise AssertionError('unexpected ws call ' + t)

    def request(self, method, path, body=None):
        self.guard.check_rest(method, path, body)
        self.calls.append(('rest', method, path))
        sid = path.rsplit('/', 1)[1]
        if method == 'GET':
            if sid not in self.configs:
                raise M.A.HAError(f'HTTP 404 on GET {path}')
            return copy.deepcopy(self.configs[sid])
        if method == 'POST':
            self.configs[sid] = dict(json.loads(json.dumps(body)), id=sid)
            return {'result': 'ok'}
        raise AssertionError(method)


class Guard(unittest.TestCase):
    def test_scene_post_body_keyed(self):
        g = M.Guard(writes=True)
        path, body = '/api/config/scene/config/1704748847044', {'name': 'Movie', 'entities': {}}
        with self.assertRaises(M.NotAllowed):
            g.check_rest('POST', path, body)
        g.approve(('rest', 'POST', path, M.canon(body)))
        g.check_rest('POST', path, body)
        with self.assertRaises(M.NotAllowed):
            g.check_rest('POST', path, {'name': 'Other', 'entities': {}})

    def test_only_reload_never_turn_on(self):
        g = M.Guard(writes=True)
        msg = {'domain': 'scene', 'service': 'reload', 'service_data': {}}
        g.approve(M.target_key('call_service', msg))
        g.check_ws('call_service', msg)
        for svc in ('turn_on', 'apply', 'create'):
            with self.assertRaises(M.NotAllowed):
                g.check_ws('call_service', {'domain': 'scene', 'service': svc,
                                            'service_data': {'entity_id': 'scene.movie'}})
        with self.assertRaises(M.NotAllowed):
            M.Guard(writes=False).check_ws('call_service', msg)


class Apply(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        M.UNDO = os.path.join(self.tmp.name, 'undo.json')
        M.time.sleep = lambda s: None

    def tearDown(self):
        self.tmp.cleanup()

    def test_dry_run_writes_nothing(self):
        c = FakeHA(M.Guard(writes=False))
        M.g_scene_create(c, c.guard, False)
        self.assertEqual(c.configs, {})
        self.assertFalse(any(x[1] == 'POST' or x[1] == 'call_service' for x in c.calls))
        self.assertFalse(os.path.exists(M.UNDO))

    def test_apply_creates_four_and_reads_back(self):
        c = FakeHA(M.Guard(writes=True))
        M.g_scene_create(c, c.guard, True)
        self.assertEqual(sorted(c.configs), sorted(EXPECT))
        for sid, eid in EXPECT.items():
            self.assertEqual(M._scene_found(c, sid), [eid])
        self.assertEqual(c.configs['1704749350279']['name'], 'Ambient')
        self.assertNotIn('light.bedroom_pillar_lamp', c.configs['1704749075545']['entities'])
        self.assertEqual(c.configs['1704748847044']['entities']['light.wled_4'], {'state': 'off'})
        services = [x[2] for x in c.calls if x[1] == 'call_service']
        self.assertEqual(services, [{'domain': 'scene', 'service': 'reload', 'service_data': {}}])
        rec = json.load(open(M.UNDO))['scene-rebuild']
        self.assertTrue(rec['groups']['scene-create']['verify']['ok'])
        self.assertIn('DELETE', rec['about'])
        # idempotent: a second run writes nothing
        n = len(c.calls)
        M.g_scene_create(c, c.guard, True)
        self.assertFalse(any(x[1] in ('POST', 'call_service') for x in c.calls[n:]))

    def test_collision_refused(self):
        c = FakeHA(M.Guard(writes=True), taken=('scene.movie',))
        with self.assertRaises(SystemExit):
            M.g_scene_create(c, c.guard, True)
        self.assertEqual(c.configs, {})


if __name__ == '__main__':
    unittest.main()
