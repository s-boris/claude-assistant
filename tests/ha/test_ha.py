"""Tests for ~/bin/ha against a fake HA on 127.0.0.1 (no other network).

Run: ~/opt/ha-tests/run.sh      HA_BIN=/path/to/ha tests another copy (default: ./stage/ha if present,
                                 else ~/bin/ha); its lib/ha_privacy.py is the one next to it.
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import stat
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fake_ha  # noqa: E402

BIN = os.environ.get('HA_BIN') or (os.path.join(HERE, 'stage', 'ha') if os.path.exists(os.path.join(HERE, 'stage', 'ha'))
                                   else os.path.expanduser('~/bin/ha'))
sys.dont_write_bytecode = True


def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


H = load(BIN, 'ha_under_test')
P = H.P


class Base(unittest.TestCase):
    effects = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tok = os.path.join(self.tmp.name, 'tok')
        with open(tok, 'w') as fh:
            fh.write(fake_ha.TOKEN + '\n')
        self.fake = fake_ha.FakeHA(effects=self.effects).__enter__()
        self.state_dir = os.path.join(self.tmp.name, 'state')
        self.env = {'HA_URL': self.fake.url, 'HA_TOKEN_FILE': tok, 'HA_STATE_DIR': self.state_dir}
        self._old = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)

    def tearDown(self):
        self.fake.__exit__()
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def ha(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = H.main(list(argv))
            except SystemExit as e:
                rc = e.code
        return rc, out.getvalue(), err.getvalue()

    def logged(self):
        p = os.path.join(self.state_dir, 'actions.log')
        if not os.path.exists(p):
            return []
        with open(p) as fh:
            return [json.loads(x) for x in fh if x.strip()]

    def assertNoCanary(self, text):
        for c in fake_ha.CANARIES:
            self.assertNotIn(c, text, f'canary {c!r} leaked')

    def assertNothingSent(self):
        self.assertEqual(self.fake.posts(), [])
        self.assertEqual(self.fake.calls, [])


# ----------------------------------------------------------------------------- privacy
class PrivacyTests(Base):
    def test_no_canary_in_any_read(self):
        chunks = []
        for argv in (['list'], ['list', '--json'], ['areas'], ['find', 'galaxy'], ['find', 'alex'],
                     ['find', 'musterstrasse'], ['state', 'galaxy'], ['state', 'tv', '--json'],
                     ['list', '--area', 'Living Room']):
            rc, o, e = self.ha(*argv)
            chunks += [o, e]
        for eid in self.fake.srv.states:
            for extra in ([], ['--json']):
                rc, o, e = self.ha('state', eid, *extra)
                self.assertEqual(rc, 0, (eid, e))
                chunks += [o, e]
        self.assertNoCanary('\n'.join(chunks))

    def test_geocoded_sensor_hidden(self):
        rc, o, e = self.ha('state', 'sensor.phone_geocoded_location', '--json')
        g = json.loads(o)
        self.assertEqual(g['state'], '<hidden>')
        self.assertEqual(g['attributes'], {})
        self.assertNoCanary(o)

    def test_trackers_show_zone_or_home_only(self):
        want = {'device_tracker.phone': 'home', 'person.alex': 'Work', 'person.jamie': 'not_home',
                'zone.home': '<hidden>'}
        for eid, st in want.items():
            g = json.loads(self.ha('state', eid, '--json')[1])
            self.assertEqual(g['state'], st, eid)
            self.assertEqual(g['attributes'], {}, eid)

    def test_wifi_ip_notification_sensors_hidden(self):
        for eid in ('sensor.phone_wifi_connection', 'sensor.phone_wifi_bssid',
                    'sensor.phone_last_notification', 'sensor.phone_public_ip'):
            g = json.loads(self.ha('state', eid, '--json')[1])
            self.assertEqual(g['state'], '<hidden>', eid)
        g = json.loads(self.ha('state', 'sensor.phone_battery_level', '--json')[1])
        self.assertEqual(g['state'], '54')

    def test_nested_attributes(self):
        rc, o, e = self.ha('state', 'sensor.car_status', '--json')
        self.assertNoCanary(o)
        a = json.loads(o)['attributes']
        keys = []

        def walk(x):
            if isinstance(x, dict):
                for k, v in x.items():
                    keys.append(k)
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
        walk(a)
        for bad in ('lat', 'lon', 'address'):
            self.assertNotIn(bad, keys)
        d = a['details']
        self.assertEqual(d['pos'], {})
        self.assertEqual(d['coords'], ['<coord>', '<coord>'])
        self.assertEqual(d['wifi_ssid'], '<ssid>')
        self.assertEqual(d['items'][1]['contact'], '<email>')
        self.assertEqual(d['items'][2]['router'], '<ip>')
        self.assertEqual(d['items'][2]['ap_mac'], '<mac>')
        self.assertEqual(d['items'][2]['v6'], '<ip>')
        self.assertIn('<address>', d['note'])
        self.assertEqual(a['x'], '<coord>')
        self.assertEqual(a['battery_level'], 0.877)
        self.assertEqual(a['last_seen'], '2026-10-15T20:59:59+00:00')

    def test_gate_value_unit(self):
        v = P.gate_value({'a': [{'b': {'Latitude': 1.0, 'GPS': [1, 2], 'street': 'x', 'ok': 'y',
                                       'BSSID': 'zz', 'Access_Token': 't', 'url': 'http://h/x?apikey=SECRET1&a=1'}}]})
        self.assertEqual(v, {'a': [{'b': {'ok': 'y', 'BSSID': '<mac>', 'url': 'http://h/x?apikey=<token>&a=1'}}]})

    def test_media_player_token_and_picture(self):
        rc, o, e = self.ha('state', 'media_player.lg_webos_tv_oled55c7v')
        self.assertNoCanary(o)
        self.assertNotIn('entity_picture', o)
        self.assertIn('source: HDMI 1', o)
        self.assertIn('volume_level: 0.12', o)
        rc, o, e = self.ha('list', 'media_player')
        self.assertIn('vol 12%', o)

    def test_bluetooth_names_masked(self):
        rc, o, e = self.ha('state', 'device_tracker.alex_airpods', '--json')
        g = json.loads(o)
        self.assertTrue(g['entity_id'].startswith('device_tracker.[bt:'))
        self.assertEqual(g['name'], 'Bluetooth device')
        rc, o, e = self.ha('find', 'airpods')
        self.assertNotIn('AirPods', o + e)

    def test_calendar_and_camera_content_hidden(self):
        for eid in ('calendar.alex_example_com', 'camera.door'):
            rc, o, e = self.ha('state', eid, '--json')
            self.assertEqual(json.loads(o)['attributes'], {}, eid)
            self.assertNoCanary(o)

    def test_measured_value_rounded_not_coord(self):
        rc, o, e = self.ha('state', 'sensor.living_room_temperature')
        self.assertIn('state: 21.53', o)

    def test_text_gate_masks_token(self):
        H._SECRETS[:] = ['SECRETTOKEN123456789012']
        self.assertEqual(P.text_gate('x SECRETTOKEN123456789012 y', H._SECRETS), 'x <token> y')

    def test_regexes_equal_ha_audit(self):
        audit = os.path.expanduser('~/bin/ha-audit')
        if not os.path.exists(audit):
            self.skipTest('no ha-audit')
        A = load(audit, 'ha_audit_for_drift')
        for n in ('EMAIL_RE', 'EMAIL_SLUG_RE', 'MAC_RE', 'MAC12_RE', 'IP4_RE', 'IP6_RE', 'IP6C_RE', 'COORD_RE',
                  'STREET_RE', 'PLZ_RE', 'SENSITIVE_OBJ_RE'):
            self.assertEqual(getattr(A, n).pattern, getattr(P, n).pattern, n)
            self.assertEqual(getattr(A, n).flags, getattr(P, n).flags, n)


# ----------------------------------------------------------------------------- resolution
class ResolutionTests(Base):
    def test_ambiguous_name_refused_ids_only(self):
        rc, o, e = self.ha('off', 'lamp')
        self.assertEqual(rc, 2)
        self.assertIn('ambiguous', e)
        self.assertIn('light.kitchen', e)
        self.assertIn('light.living_room_lamp', e)
        self.assertNotIn('Kitchen Lamp', e)
        self.assertNotIn('Floor Lamp', e)
        self.assertNothingSent()

    def test_tv_ambiguous_for_off(self):
        rc, o, e = self.ha('off', 'tv')
        self.assertEqual(rc, 2)
        self.assertIn('media_player.lg_webos_tv_oled55c7v', e)
        self.assertIn('switch.tv_plug', e)
        self.assertNothingSent()

    def test_exact_name_resolves(self):
        rc, o, e = self.ha('on', 'Floor Lamp')
        self.assertEqual(rc, 0, e)
        self.assertEqual(self.fake.calls[0][:2], ('light', 'turn_on'))
        self.assertEqual(self.fake.calls[0][2]['entity_id'], 'light.living_room_lamp')

    def test_words_resolve_when_unique(self):
        rc, o, e = self.ha('off', 'ceiling')
        self.assertEqual(rc, 0, e)
        self.assertEqual(self.fake.calls[0][2]['entity_id'], 'light.living_room_ceiling')

    def test_ambiguous_exact_names_refused(self):
        self.fake.srv.states['light.kitchen']['attributes']['friendly_name'] = 'Floor Lamp'
        rc, o, e = self.ha('on', 'Floor Lamp')
        self.assertEqual(rc, 2)
        self.assertNothingSent()

    def test_unquoted_multiword_names(self):
        rc, o, e = self.ha('on', 'Floor', 'Lamp')
        self.assertEqual(rc, 0, e)
        self.assertEqual(self.fake.calls[-1][2]['entity_id'], 'light.living_room_lamp')
        rc, o, e = self.ha('media', 'LG', 'webOS', 'TV', 'volume', '30')
        self.assertEqual(rc, 0, e)
        self.assertEqual(self.fake.calls[-1][2], {'volume_level': 0.3, 'entity_id': 'media_player.lg_webos_tv_oled55c7v'})
        rc, o, e = self.ha('set', 'Floor', 'Lamp', '--brightness', '40')
        self.assertEqual(rc, 0, e)
        rc, o, e = self.ha('scene', 'run', 'Movie', 'night')
        self.assertEqual(rc, 0, e)
        rc, o, e = self.ha('state', 'Ceiling', 'Light')
        self.assertIn('state: on', o)
        rc, o, e = self.ha('off', 'light.kitchen', 'light.living_room_ceiling')
        self.assertEqual(rc, 0, e)
        self.assertEqual(self.fake.calls[-1][2]['entity_id'], ['light.kitchen', 'light.living_room_ceiling'])
        rc, o, e = self.ha('media', 'LG', 'TV', 'dance')
        self.assertEqual(rc, 2)

    def test_unknown_refused(self):
        rc, o, e = self.ha('off', 'spaceship')
        self.assertEqual(rc, 2)
        rc, o, e = self.ha('off', 'light.nope')
        self.assertEqual(rc, 2)
        self.assertNothingSent()

    def test_state_by_name_lists_matches(self):
        rc, o, e = self.ha('state', 'tv')
        self.assertEqual(rc, 0)
        self.assertIn('media_player.lg_webos_tv_oled55c7v', o)
        self.assertIn('switch.tv_plug', o)

    def test_find_and_area_list(self):
        rc, o, e = self.ha('find', 'living room light')
        self.assertIn('light.living_room_ceiling', o)
        rc, o, e = self.ha('list', 'light', '--area', 'living room')
        self.assertEqual(sorted(x.split()[0] for x in o.splitlines()),
                         ['light.living_room_ceiling', 'light.living_room_lamp'])
        rc, o, e = self.ha('list', '--area', 'kuche')
        self.assertIn('light.kitchen', o)
        rc, o, e = self.ha('list', '--area', 'nowhere')
        self.assertEqual(rc, 2)
        rc, o, e = self.ha('areas')
        self.assertIn('Living Room (living_room)', o)


# ----------------------------------------------------------------------------- guard
class GuardTests(Base):
    REFUSED = [
        ['call', 'homeassistant.restart', 'light.kitchen'],
        ['call', 'lock.unlock', 'lock.front_door'],
        ['call', 'alarm_control_panel.alarm_disarm', 'alarm_control_panel.home_alarm'],
        ['off', 'lock.front_door'], ['on', 'alarm_control_panel.home_alarm'],
        ['call', 'scene.apply', 'scene.movie_night', '{"entities": {"lock.front_door": "unlocked"}}'],
        ['call', 'scene.create', 'scene.movie_night'], ['call', 'light.reload', 'light.kitchen'],
        ['call', 'script.reload'], ['call', 'remote.learn_command', 'remote.x'],
        ['call', 'light.turn_on', 'light.kitchen', '{"entity_id": "lock.front_door"}'],
        ['call', 'light.turn_on', 'light.kitchen', '{"area_id": "all"}'],
        ['call', 'light.turn_on', 'light.kitchen', '{"target": {"entity_id": "all"}}'],
        ['call', 'light.turn_on', 'switch.tv_plug'],
        ['call', 'light.turn_on'], ['call', 'notify.notify', 'light.kitchen', '{"message": "hi"}'],
        ['call', 'shell_command.x', 'light.kitchen'], ['call', 'hassio.host_reboot', 'light.kitchen'],
        ['call', 'button.press', 'light.kitchen'],
        ['on', 'button.router_restart'], ['on', 'sensor.car_status'], ['off', 'cover.living_room_blinds'],
        ['off', 'cover.garage'], ['toggle', 'cover.garage'], ['on', 'switch.garage_door_opener'],
        ['call', 'cover.open_cover', 'cover.garage'],
        ['on', 'scene.bad_scene'], ['scene', 'run', 'bad_scene'], ['script', 'run', 'open_up'],
        ['call', 'script.open_up'], ['call', 'script.turn_on', 'script.open_up'],
        ['set', 'switch.tv_plug', '--brightness', '20'], ['set', 'light.kitchen', '--brightness', '200'],
        ['media', 'light.kitchen', 'play'], ['on', '--area', 'Living Room', '--domain', 'sensor'],
    ]

    def test_refused_without_sending(self):
        for argv in self.REFUSED:
            rc, o, e = self.ha(*argv)
            self.assertEqual(rc, 2, (argv, o, e))
            self.assertIn('refused', e, argv)
        self.assertNothingSent()
        self.assertEqual(self.logged(), [])

    def test_check_request_allowlist(self):
        bad = [('DELETE', '/api/states/light.kitchen', None), ('PUT', '/api/states', None),
               ('POST', '/api/states/light.kitchen', {'state': 'on'}), ('GET', '/api/config', None),
               ('GET', '/api/websocket', None), ('GET', '/api/', None), ('GET', '/api/states?x=1', None),
               ('POST', '/api/template', {'template': '{{ states.device_tracker | list }}'}),
               ('POST', '/api/config/script/config/x', {}), ('POST', '/api/events/x', {}),
               ('POST', '/api/services/homeassistant/restart', {'entity_id': 'light.x'}),
               ('POST', '/api/services/lock/unlock', {'entity_id': 'lock.x'}),
               ('POST', '/api/services/light/turn_on', {'entity_id': 'lock.x'}),
               ('POST', '/api/services/light/turn_on', {'entity_id': 'light.x', 'device_id': 'abc'}),
               ('POST', '/api/services/light/turn_on', {}),
               ('POST', '/api/services/light/turn_on', {'entity_id': 'all'}),
               ('POST', '/api/services/script/reload', {'entity_id': 'script.x'}),
               ('GET', '/api/config/automation/config/1', None)]
        for m, p, b in bad:
            with self.assertRaises(H.Refused, msg=(m, p, b)):
                H.check_request(m, p, b)
        H.check_request('GET', '/api/states')
        H.check_request('GET', '/api/states/light.kitchen')
        H.check_request('POST', '/api/template', {'template': H.AREA_TEMPLATE})
        H.check_request('POST', '/api/services/light/turn_off', {'entity_id': ['light.a', 'light.b']})

    def test_never_websocket_or_other_methods(self):
        self.ha('list')
        self.ha('off', 'light.living_room_ceiling')
        self.ha('state', 'tv')
        for kind, method, path, body in self.fake.log:
            self.assertNotEqual(path, '/api/websocket')
            self.assertIn(method, ('GET', 'POST'))
            if method == 'POST' and path == '/api/template':
                self.assertEqual(body, {'template': H.AREA_TEMPLATE})

    def test_device_url_template_guard(self):
        H.check_request('POST', '/api/template', {'template': H.DEVICE_URL_TEMPLATE % 'light.kitchen'})
        for t in [H.DEVICE_URL_TEMPLATE % "light.kitchen') }}{{ states.person | list }}{{ ('x",
                  H.DEVICE_URL_TEMPLATE % 'Light.Kitchen', H.DEVICE_URL_TEMPLATE % 'kitchen',
                  H.DEVICE_URL_TEMPLATE.replace('configuration_url', 'name') % 'light.kitchen']:
            with self.assertRaises(H.Refused, msg=t):
                H.check_request('POST', '/api/template', {'template': t})
        with self.assertRaises(H.Refused):
            H.check_request('POST', '/api/template', {'template': H.DEVICE_URL_TEMPLATE % 'light.kitchen', 'x': 1})

    def test_lan_networks(self):
        # exactly the RFC 1918 blocks: 10/8, 172.16/12, 192.168/16
        self.assertEqual([(n.network_address.packed[:2], n.prefixlen) for n in H.LAN_NETS],
                         [(bytes([10, 0]), 8), (bytes([172, 16]), 12), (bytes([192, 168]), 16)])

    def test_lan_url(self):
        # one host in each RFC 1918 block, derived from H.LAN_NETS (no private-range literal in the public tree)
        ten, mid, home = (str(n.network_address + k) for n, k in zip(H.LAN_NETS, (5, 300, 23)))
        ok = {'http://' + home: 'http://' + home, 'http://%s/' % home: 'http://' + home,
              'http://%s:8080' % ten: 'http://%s:8080' % ten, 'https://wled-shelf.local/': 'https://wled-shelf.local',
              'http://wled-tv': 'http://wled-tv', 'http://' + mid: 'http://' + mid}
        for u, want in ok.items():
            self.assertEqual(H.lan_url(u), want, u)
        for u in ['http://198.51.100.9', 'http://8.8.8.8', 'http://example.com', 'http://172.32.0.1',
                  'http://%s/admin' % home, 'http://u:p@' + home, 'ftp://' + home, 'http://%s?x=1' % home,
                  'http://%s:99999' % home, 'http://999.1.1.1', None, 42, '']:
            self.assertIsNone(H.lan_url(u), u)

    def test_device_url_command(self):
        rc, o, e = self.ha('device-url', 'Ceiling', 'Light')
        self.assertEqual((rc, o.strip()), (0, 'http://ceiling-light.local'), e)
        rc, o, e = self.ha('device-url', 'light.living_room_ceiling', '--json')
        self.assertEqual(json.loads(o), {'entity_id': 'light.living_room_ceiling', 'url': 'http://ceiling-light.local'})
        rc, o, e = self.ha('device-url', 'cover.living_room_blinds')
        self.assertEqual((rc, o.strip()), (0, 'https://blinds.lan:8443'))
        for target, why in [('light.kitchen', 'not a LAN'), ('switch.tv_plug', 'not a LAN'),
                            ('light.bedroom_dead', 'no configuration_url'), ('light.living_room_lamp', 'no configuration_url'),
                            ('light.nope', 'no such entity')]:
            rc, o, e = self.ha('device-url', target)
            self.assertEqual(rc, 2, target)
            self.assertEqual(o, '', target)
            self.assertIn(why, e, target)
            self.assertNoCanary(o + e)
        self.assertNothingSent()
        self.assertEqual(self.logged(), [])

    def test_owner_ok_allows_garage_and_is_logged(self):
        rc, o, e = self.ha('call', 'cover.open_cover', 'cover.garage', '--owner-ok', 'yes open the garage')
        self.assertEqual(rc, 0, e)
        lg = self.logged()
        self.assertEqual(lg[-1]['owner_ok'], 'yes open the garage')
        self.assertEqual(lg[-1]['service'], 'cover.open_cover')

    def test_yaml_script_runs_with_note(self):
        rc, o, e = self.ha('script', 'run', 'yaml_only', '--wait', '1')
        self.assertIn('lock/garage check skipped', e)
        self.assertEqual(self.fake.calls[0][:2], ('script', 'turn_on'))

    def test_bad_token_no_leak(self):
        with open(os.environ['HA_TOKEN_FILE'], 'w') as fh:
            fh.write('wrong-token-0123456789-abcdef\n')
        rc, o, e = self.ha('list')
        self.assertEqual(rc, 1)
        self.assertNotIn('wrong-token', o + e)


# ----------------------------------------------------------------------------- writes
class WriteTests(Base):
    def test_off_logged_and_read_back(self):
        rc, o, e = self.ha('off', 'light.living_room_ceiling')
        self.assertEqual(rc, 0, e)
        self.assertIn('light.living_room_ceiling: on 50% 2700K -> off ok', o)
        lg = self.logged()
        self.assertEqual(len(lg), 1)
        self.assertEqual(lg[0]['service'], 'light.turn_off')
        self.assertEqual(lg[0]['entity_ids'], ['light.living_room_ceiling'])
        self.assertEqual(lg[0]['result'], 'ok')
        self.assertTrue(re.match(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', lg[0]['ts']))
        self.assertEqual(lg[0]['old'], {'light.living_room_ceiling': 'on 50% 2700K'})
        self.assertEqual(lg[0]['new'], {'light.living_room_ceiling': 'off'})
        mode = stat.S_IMODE(os.stat(os.path.join(self.state_dir, 'actions.log')).st_mode)
        self.assertEqual(mode, 0o600)
        with open(os.path.join(self.state_dir, 'actions.log')) as fh:
            self.assertNotIn(fake_ha.TOKEN, fh.read())

    def test_already_off(self):
        rc, o, e = self.ha('off', 'light.kitchen')
        self.assertEqual(rc, 0)
        self.assertIn('off -> off ok (already)', o)

    def test_set_light(self):
        rc, o, e = self.ha('set', 'Floor Lamp', '--brightness', '30', '--color-temp', '3000')
        self.assertEqual(rc, 0, e)
        self.assertEqual(self.fake.calls[0][2], {'brightness_pct': 30, 'color_temp_kelvin': 3000,
                                                 'entity_id': 'light.living_room_lamp'})
        self.assertIn('off -> on 30% 3000K ok', o)

    def test_media(self):
        rc, o, e = self.ha('media', 'media_player.lg_webos_tv_oled55c7v', 'volume', '25')
        self.assertEqual(rc, 0, e)
        self.assertIn('vol 12%', o)
        self.assertIn('-> on vol 25% src HDMI 1 ok', o)
        rc, o, e = self.ha('media', 'LG webOS TV OLED55C7V', 'pause')
        self.assertEqual(rc, 0, e)
        self.assertIn('-> paused', o)
        self.assertNoCanary(o + e)
        rc, o, e = self.ha('call', 'media_player.select_source', 'media_player.lg_webos_tv_oled55c7v',
                           '{"source": "Netflix"}')
        self.assertEqual(rc, 0, e)
        self.assertEqual(self.fake.calls[-1][2], {'source': 'Netflix', 'entity_id': 'media_player.lg_webos_tv_oled55c7v'})

    def test_area_off_one_call(self):
        self.fake.srv.states['light.living_room_lamp']['state'] = 'on'
        rc, o, e = self.ha('off', '--area', 'Living Room')
        self.assertEqual(rc, 0, e)
        self.assertEqual(len(self.fake.calls), 1)
        self.assertEqual(self.fake.calls[0][2]['entity_id'], ['light.living_room_ceiling', 'light.living_room_lamp'])
        self.assertEqual(self.logged()[0]['entity_ids'], ['light.living_room_ceiling', 'light.living_room_lamp'])

    def test_scene_and_script(self):
        rc, o, e = self.ha('scene', 'run', 'movie_night')
        self.assertEqual(rc, 0, e)
        self.assertEqual(self.fake.calls[-1][:2], ('scene', 'turn_on'))
        self.assertNoCanary(o)
        self.assertNotIn('<coord>', o)
        rc, o, e = self.ha('script', 'run', 'Goodnight')
        self.assertEqual(rc, 0, e)
        self.assertEqual(self.fake.calls[-1][:2], ('script', 'turn_on'))
        rc, o, e = self.ha('call', 'script.goodnight', '{"x": 1}')
        self.assertEqual(rc, 0, e)
        self.assertEqual(self.fake.calls[-1], ('script', 'turn_on', {'variables': {'x': 1},
                                                                     'entity_id': 'script.goodnight'}))

    def test_dry_run_sends_nothing(self):
        rc, o, e = self.ha('off', 'light.living_room_ceiling', '-n')
        self.assertEqual(rc, 0)
        self.assertIn('dry run: would call light.turn_off', o)
        self.assertNothingSent()
        self.assertEqual(self.logged(), [])

    def test_ha_error_logged(self):
        self.fake.srv.fail.add(('media_player', 'select_source'))
        rc, o, e = self.ha('call', 'media_player.select_source', 'media_player.lg_webos_tv_oled55c7v',
                           '{"source": "Nope"}')
        self.assertEqual(rc, 1)
        self.assertIn('HTTP 400', e)
        self.assertNoCanary(e)
        self.assertTrue(self.logged()[0]['result'].startswith('error'))


class MaskedIdTests(Base):
    def test_email_masked_id_usable(self):
        rc, o, e = self.ha('list', 'vacuum')
        self.assertIn('vacuum.<email>', o)
        rc, o, e = self.ha('state', 'vacuum.<email>')
        self.assertEqual(rc, 0, e)
        self.assertIn('state: docked', o)
        rc, o, e = self.ha('call', 'vacuum.start', 'vacuum.<email>')
        self.assertEqual(rc, 0, e)
        self.assertEqual(self.fake.calls[-1], ('vacuum', 'start', {'entity_id': 'vacuum.alex_gmail_com'}))
        self.assertIn('vacuum.<email>: docked -> cleaning', o)
        self.assertNoCanary(o + e + json.dumps(self.logged()))

    def test_unavailable_target_refused(self):
        rc, o, e = self.ha('on', 'light.bedroom_dead')
        self.assertEqual(rc, 2)
        self.assertIn('unavailable', e)
        self.assertNothingSent()


class UnconfirmedTests(Base):
    effects = False

    def test_not_confirmed_exit_3(self):
        rc, o, e = self.ha('off', 'light.living_room_ceiling', '--wait', '0.5')
        self.assertEqual(rc, 3)
        self.assertIn('not confirmed after 0.5s', o)
        self.assertEqual(self.logged()[0]['result'], 'unconfirmed')
        self.assertEqual(len(self.fake.calls), 1)


if __name__ == '__main__':
    unittest.main()
