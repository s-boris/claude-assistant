"""Offline tests for ha-apply round 2: config entry delete guard, target selection
(plants / wiz / ibeacon / old-phones) and the apply path against a fake HA. Nothing touches the network."""
import copy, importlib.machinery, importlib.util, json, os, sys, tempfile, unittest
sys.dont_write_bytecode = True
BIN = os.environ.get('HA_APPLY_BIN', os.path.expanduser('~/bin/ha-apply'))
loader = importlib.machinery.SourceFileLoader('ha_apply_r2', BIN)
spec = importlib.util.spec_from_loader('ha_apply_r2', loader)
M = importlib.util.module_from_spec(spec)
loader.exec_module(M)

E = '/api/config/config_entries/entry/'


def hx(n, p='a'):
    return (p * 32 + str(n))[-32:]


class FakeWS:
    def __init__(self, c, guard):
        self.c, self.guard, self.calls = c, guard, []

    def call(self, t, **p):
        self.guard.check_ws(t, p)
        self.calls.append((t, p))
        if t == 'person/list':
            return {'storage': copy.deepcopy(self.c.persons), 'config': []}
        if t == 'person/update':
            pp = next(x for x in self.c.persons if x['id'] == p['person_id'])
            pp['device_trackers'] = list(p['device_trackers'])
            return pp
        if t == 'config/device_registry/remove_config_entry':
            d = self.c.devs[p['device_id']]
            d['config_entries'].remove(p['config_entry_id'])
            if not d['config_entries']:
                del self.c.devs[p['device_id']]
                for k in [k for k, x in self.c.reg.items() if x.get('device_id') == p['device_id']]:
                    del self.c.reg[k]
            return None
        raise AssertionError('unexpected ws call ' + t)


class FakeRest:
    def __init__(self, c, guard):
        self.c, self.guard, self.calls = c, guard, []

    def request(self, method, path, body=None):
        self.guard.check_rest(method, path)
        self.calls.append((method, path))
        eid = path[len(E):]
        del self.c.entries[eid]
        for k in [k for k, d in self.c.devs.items() if d['config_entries'] == [eid]]:
            del self.c.devs[k]
        for k in [k for k, x in self.c.reg.items() if x.get('config_entry_id') == eid]:
            del self.c.reg[k]
        return {'require_restart': False}


class FakeCtx(M.Ctx):
    def __init__(self, guard):
        self.entries, self.devs, self.reg, self.states, self.persons = {}, {}, {}, {}, []
        self.audit = {'int_dead': [], 'int_failing': [], 'dev_dead': [], 'phones': []}
        self.ws, self.rest = FakeWS(self, guard), FakeRest(self, guard)

    def refresh(self):
        pass

    def entry(self, eid, dom, state='loaded', srd=False, title='t'):
        self.entries[eid] = {'entry_id': eid, 'domain': dom, 'state': state, 'supports_remove_device': srd,
                             'title': title, 'source': 'user', 'disabled_by': None}

    def dev(self, did, entries, model=None):
        self.devs[did] = {'id': did, 'config_entries': list(entries), 'model': model, 'name': 'n', 'area_id': None}

    def ent(self, eid, did, ceid, plat, state='unavailable', disabled=None):
        self.reg[eid] = {'entity_id': eid, 'device_id': did, 'config_entry_id': ceid, 'platform': plat,
                         'disabled_by': disabled, 'unique_id': eid}
        if not disabled and state is not None:
            self.states[eid] = state


def world(guard):
    c = FakeCtx(guard)
    for i in range(6):  # plants
        eid, did = hx(i, 'p'), hx(i, 'q')
        c.entry(eid, 'xiaomi_ble')
        c.dev(did, [eid], M.PLANT_MODEL)
        for j in range(5):
            c.ent(f'sensor.plant{i}_{j}', did, eid, 'xiaomi_ble')
        c.ent(f'sensor.plant{i}_x', did, eid, 'xiaomi_ble', disabled='integration')
        c.audit['int_dead'].append({'entry_id': eid, 'domain': 'xiaomi_ble'})
    c.entry('WIZOFF0000000000000000000A', 'wiz', state='setup_retry')  # wiz
    c.dev('wizdev_off_00000000000000000000', ['WIZOFF0000000000000000000A'], 'SOCKET')
    c.ent('switch.wiz_off', 'wizdev_off_00000000000000000000', 'WIZOFF0000000000000000000A', 'wiz')
    c.entry('WIZON00000000000000000000B', 'wiz')
    c.dev('wizdev_on_000000000000000000000', ['WIZON00000000000000000000B'], 'SOCKET')
    c.ent('switch.wiz_on', 'wizdev_on_000000000000000000000', 'WIZON00000000000000000000B', 'wiz', state='off')
    c.audit['int_failing'].append({'entry_id': 'WIZOFF0000000000000000000A', 'domain': 'wiz'})
    ib = hx(0, 'i')  # ibeacon: 6 shells + 1 other device
    c.entry(ib, 'ibeacon', srd=True)
    for i in range(7):
        did = hx(i, 'b')
        c.dev(did, [ib])
        for j in range(3):
            c.ent(f'sensor.ib{i}_{j}', did, ib, 'ibeacon', disabled='integration')
        if i < 6:
            c.audit['dev_dead'].append({'id': did, 'integration': 'ibeacon'})
        else:
            c.ent('device_tracker.ib6', did, ib, 'ibeacon')
    for model, title, tracker in (('SM-G781B', 'Galaxy S20 FE Robin', 'device_tracker.galaxy_s20_fe_robin'),
                                  ('SM-S928B', 'Galaxy 24 Ultra Robin', 'device_tracker.galaxy_24_ultra_robin'),
                                  ('SM-KEEP-1', 'Robin Testphone', 'device_tracker.robin_testphone'),
                                  ('M2-KEEP-2', 'M2-KEEP-2', 'device_tracker.m2_keep_2')):
        eid, did = hx(len(c.entries), 'm'), hx(len(c.devs), 'd')
        c.entry(eid, 'mobile_app', title=title)
        c.dev(did, [eid], model)
        c.ent(tracker, did, eid, 'mobile_app', state='unknown')
        c.ent(tracker.replace('device_tracker', 'sensor') + '_battery', did, eid, 'mobile_app', state='95')
        c.audit['phones'].append({'model': model, 'id': did, 'entry_ids': [eid]})
    c.reg['person.robin'] = {'entity_id': 'person.robin', 'unique_id': 'robin', 'platform': 'person'}
    c.persons = [{'id': 'robin', 'name': 'Robin', 'device_trackers': ['device_tracker.robin_testphone']},
                 {'id': 'jamie', 'name': 'Jamie', 'device_trackers': ['device_tracker.m2_keep_2']}]
    return c


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._undo, M.UNDO = M.UNDO, os.path.join(self.tmp.name, 'undo.json')
        self.g = M.Guard(writes=True)
        self.c = world(self.g)

    def tearDown(self):
        M.UNDO = self._undo
        self.tmp.cleanup()

    def undo(self):
        with open(M.UNDO) as f:
            return json.load(f)


class GuardRound2(unittest.TestCase):
    def test_entry_delete_needs_apply_and_plan(self):
        path = E + 'WIZOFF0000000000000000000A'
        with self.assertRaises(M.NotAllowed):
            M.Guard(writes=False).check_rest('DELETE', path)
        g = M.Guard(writes=True)
        with self.assertRaises(M.NotAllowed):
            g.check_rest('DELETE', path)
        g.approve(('rest', 'DELETE', path))
        g.check_rest('DELETE', path)
        with self.assertRaises(M.NotAllowed):
            g.check_rest('DELETE', E + 'WIZON00000000000000000000B')

    def test_other_deletes_refused(self):
        g = M.Guard(writes=True)
        for m, path in (('DELETE', E + 'abc'), ('DELETE', E + 'WIZOFF0000000000000000000A/x'),
                        ('DELETE', E + '../../auth/x0000000000000000000000'), ('POST', E + 'WIZOFF0000000000000000000A'),
                        ('DELETE', '/api/config/automation/config/1'), ('DELETE', '/api/config/config_entries/entry'),
                        ('GET', E + 'WIZOFF0000000000000000000A')):
            g.approve(('rest', m, path))
            with self.assertRaises(M.NotAllowed, msg=f'{m} {path}'):
                g.check_rest(m, path)

    def test_no_new_ws_types(self):
        g = M.Guard(writes=True)
        for t in ('config_entries/remove', 'config_entries/delete', 'config_entries/disable',
                  'config/device_registry/remove', 'mobile_app/delete'):
            with self.assertRaises(M.NotAllowed):
                g.check_ws(t, {})


class Selection(Base):
    def test_plants(self):
        t, s = M.sel_plants(self.c)
        self.assertEqual(len(t), 6)
        self.c.states['sensor.plant0_1'] = '21.5'  # came back
        self.c.devs[hx(1, 'q')]['model'] = 'LYWSD03MMC'
        t, s = M.sel_plants(self.c)
        self.assertEqual(len(t), 4)
        self.assertEqual(len(s), 2)
        self.c.audit['int_dead'].pop()
        with self.assertRaises(SystemExit):
            M.sel_plants(self.c)

    def test_wiz_only_offline(self):
        t, s = M.sel_wiz(self.c)
        self.assertEqual([e['entry_id'] for e in t], ['WIZOFF0000000000000000000A'])
        self.c.states['switch.wiz_off'] = 'on'
        self.assertEqual(M.sel_wiz(self.c)[0], [])
        self.c.entries['WIZON00000000000000000000B']['state'] = 'setup_retry'
        with self.assertRaises(SystemExit):
            M.sel_wiz(self.c)

    def test_wiz_must_match_audit(self):
        self.c.audit['int_failing'] = [{'entry_id': 'WIZON00000000000000000000B', 'domain': 'wiz'}]
        with self.assertRaises(SystemExit):
            M.sel_wiz(self.c)

    def test_ibeacon(self):
        entry, t, s = M.sel_ibeacon(self.c)
        self.assertEqual(len(t), 6)
        self.assertNotIn(hx(6, 'b'), [d['id'] for d in t])
        self.c.reg['sensor.ib2_0']['disabled_by'] = None
        self.assertEqual(len(M.sel_ibeacon(self.c)[1]), 5)
        self.c.entries[hx(0, 'i')]['supports_remove_device'] = False
        with self.assertRaises(SystemExit):
            M.sel_ibeacon(self.c)

    def test_old_phones(self):
        t, s = M.sel_old_phones(self.c)
        self.assertEqual(sorted(e['title'] for e in t), ['Galaxy 24 Ultra Robin', 'Galaxy S20 FE Robin'])
        self.c.persons[0]['device_trackers'].append('device_tracker.galaxy_s20_fe_robin')
        t, s = M.sel_old_phones(self.c)
        self.assertEqual([e['title'] for e in t], ['Galaxy 24 Ultra Robin'])

    def test_old_phones_protected_model_refused(self):
        s20 = next(d for d in self.c.devs.values() if d['model'] == 'SM-G781B')
        s20['model'] = 'SM-KEEP-1'
        with self.assertRaises(SystemExit):
            M.sel_old_phones(self.c)


class PhonesFromProfile(Base):
    """round 1 phones + old-phones read the owner's person, phone and protected models from the profile
    (run.sh points ASSISTANT_PROFILE at the synthetic fixture: ha_user robin, ha_phone testphone)."""

    def test_phone_trackers_from_profile(self):
        drop, keep = M._phone_trackers()
        self.assertEqual(drop, ('device_tracker.galaxy_s20_fe_robin', 'device_tracker.galaxy_24_ultra_robin'))
        self.assertEqual(keep, 'device_tracker.robin_testphone')
        self.assertEqual(M._phones_cfg()[2], frozenset({'SM-KEEP-1', 'M2-KEEP-2'}))

    def test_phones_apply_unlinks_old_trackers_only(self):
        self.c.persons[0]['device_trackers'] += ['device_tracker.galaxy_s20_fe_robin', 'device_tracker.galaxy_24_ultra_robin']
        M.g_phones(self.c, self.g, apply=True)
        self.assertEqual(self.c.persons[0]['device_trackers'], ['device_tracker.robin_testphone'])
        self.assertEqual(self.c.persons[1]['device_trackers'], ['device_tracker.m2_keep_2'])
        u = self.undo()['groups']['phones']
        self.assertTrue(u['verify']['ok'])
        self.assertEqual(u['before']['person'], 'person.robin')

    def test_phones_refuses_another_person(self):
        self.c.persons[0]['name'] = 'Jamie'
        with self.assertRaises(SystemExit):
            M.g_phones(self.c, self.g, apply=True)
        self.assertEqual([x for x in self.c.ws.calls if x[0] == 'person/update'], [])

    def test_phones_refuses_when_keep_tracker_would_go(self):
        self.c.persons[0]['device_trackers'] = ['device_tracker.galaxy_s20_fe_robin']
        with self.assertRaises(SystemExit):
            M.g_phones(self.c, self.g, apply=True)

    def test_unreadable_profile_refuses(self):
        bad = os.path.join(self.tmp.name, 'broken.json')
        with open(bad, 'w') as f:
            f.write('{not json')
        old = os.environ.get('ASSISTANT_PROFILE')
        os.environ['ASSISTANT_PROFILE'] = bad
        try:
            ld = importlib.machinery.SourceFileLoader('ha_apply_r2_noprofile', BIN)
            sp = importlib.util.spec_from_loader('ha_apply_r2_noprofile', ld)
            M2 = importlib.util.module_from_spec(sp)
            ld.exec_module(M2)
        finally:
            if old is None:
                os.environ.pop('ASSISTANT_PROFILE', None)
            else:
                os.environ['ASSISTANT_PROFILE'] = old
        self.assertIsNone(M2._PHONES)
        g = M2.Guard(writes=True)
        c = world(g)
        with self.assertRaises(SystemExit):
            M2.g_phones(c, g, apply=True)
        with self.assertRaises(SystemExit):
            M2.sel_old_phones(c)


class Apply(Base):
    def test_wiz_apply_deletes_only_offline(self):
        M.g_wiz(self.c, self.g, apply=True)
        self.assertEqual(self.c.rest.calls, [('DELETE', E + 'WIZOFF0000000000000000000A')])
        self.assertIn('WIZON00000000000000000000B', self.c.entries)
        r = self.undo()['round2']['groups']['wiz']
        self.assertTrue(r['verify']['ok'])
        self.assertIsNone(r['before']['entries'][0]['title'])

    def test_dry_run_writes_nothing(self):
        g = M.Guard(writes=False)
        c = world(g)
        for fn in (M.g_plants, M.g_wiz, M.g_ibeacon, M.g_old_phones):
            fn(c, g, apply=False)
        self.assertEqual(c.rest.calls, [])
        self.assertEqual([x for x in c.ws.calls if x[0] != 'person/list'], [])
        self.assertFalse(os.path.exists(M.UNDO))

    def test_plants_and_phones_apply(self):
        M.g_plants(self.c, self.g, apply=True)
        M.g_old_phones(self.c, self.g, apply=True)
        self.assertEqual(len(self.c.rest.calls), 8)
        self.assertEqual(sum(1 for e in self.c.entries.values() if e['domain'] == 'xiaomi_ble'), 0)
        left = sorted(d['model'] for d in self.c.devs.values() if d.get('model', '') and d['model'].startswith(('SM', 'M2')))
        self.assertEqual(left, ['M2-KEEP-2', 'SM-KEEP-1'])
        u = self.undo()['round2']['groups']
        self.assertTrue(u['plants']['verify']['ok'] and u['old-phones']['verify']['ok'])
        self.assertEqual(u['plants']['verify']['entities_gone'], 36)

    def test_ibeacon_apply_keeps_entry_and_other(self):
        M.g_ibeacon(self.c, self.g, apply=True)
        rm = [x for x in self.c.ws.calls if x[0] == 'config/device_registry/remove_config_entry']
        self.assertEqual(len(rm), 6)
        self.assertIn(hx(6, 'b'), self.c.devs)
        self.assertIn(hx(0, 'i'), self.c.entries)
        self.assertTrue(self.undo()['round2']['groups']['ibeacon']['verify']['ok'])

    def test_unapproved_target_blocked_mid_apply(self):
        with self.assertRaises(M.NotAllowed):
            self.c.rest.request('DELETE', E + 'WIZON00000000000000000000B')
        with self.assertRaises(M.NotAllowed):
            self.c.ws.call('config/device_registry/remove_config_entry', device_id=hx(6, 'b'), config_entry_id=hx(0, 'i'))


if __name__ == '__main__':
    unittest.main()
