"""Fake Home Assistant (REST only) for ~/bin/ha tests; pattern of ~/opt/ha-audit-tests/fake_ha.py.

Holds mutable states, applies the effect of the service calls ha may send (so the read-back can be
tested), records every request, and carries privacy canaries that must never appear in ha output/logs.
"""
import copy
import datetime as dt
import json
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = 'fake-token-CANARY-0123456789abcdefXYZ'
CANARIES = ['12.345', '65.432', 'Musterstrasse', '1234', 'Exampletown', 'ExampleNet-5G', 'aa:bb:cc:dd:ee:ff',
            'AA:BB:CC:DD:EE:01', '198.51.100.', '203.0.113.77', 'Your package', 'alex@example.com', 'AirPods',
            'CANARYPICTOKEN', 'CANARYAPIKEY', 'CANARYCAMTOKEN', 'Dentist', 'fe80::1:2:3:4', 'alex_gmail_com', TOKEN]
T0 = '2026-10-15T21:00:00.123456+00:00'
PRIV = {'latitude': 12.34567, 'longitude': 65.43219, 'gps_accuracy': 12, 'altitude': 410.5}


def st(eid, state, attrs=None):
    return {'entity_id': eid, 'state': state, 'attributes': attrs or {}, 'last_changed': T0, 'last_updated': T0,
            'context': {'id': 'x'}}


def fixtures():
    return [
        st('person.alex', 'Work', dict(PRIV, friendly_name='Alex', source='device_tracker.phone')),
        st('person.jamie', 'Musterstrasse 12, 1234 Exampletown', dict(PRIV, friendly_name='Jamie')),
        st('device_tracker.phone', 'home', dict(PRIV, ip='198.51.100.23', ssid='ExampleNet-5G',
                                                     friendly_name='Galaxy A55', source_type='gps')),
        st('zone.home', '1', dict(PRIV, radius=100, friendly_name='Home')),
        st('zone.work', '1', dict(PRIV, radius=100, friendly_name='Work')),
        st('sensor.phone_geocoded_location', 'Musterstrasse 12, 1234 Exampletown',
           {'Location': [12.34567, 65.43219], 'Thoroughfare': 'Musterstrasse', 'Postal Code': '1234',
            'friendly_name': 'Galaxy A55 Geocoded location'}),
        st('sensor.phone_wifi_connection', 'ExampleNet-5G', {'friendly_name': 'Galaxy A55 Wi-Fi connection'}),
        st('sensor.phone_wifi_bssid', 'aa:bb:cc:dd:ee:ff', {'friendly_name': 'Galaxy A55 Wi-Fi BSSID'}),
        st('sensor.phone_last_notification', 'Your package from Musterstrasse 12',
           {'android.text': 'Your package', 'friendly_name': 'Galaxy A55 Last notification'}),
        st('sensor.phone_public_ip', '203.0.113.77', {'friendly_name': 'Galaxy A55 Public IP'}),
        st('sensor.phone_battery_level', '54', {'unit_of_measurement': '%', 'friendly_name': 'Galaxy A55 Battery'}),
        st('sensor.car_status', 'parked', {
            'friendly_name': 'Car status', 'battery_level': 0.87654, 'x': 12.34567,
            'details': {'pos': {'lat': 12.34567, 'lon': 65.43219}, 'coords': [12.34567, 65.43219],
                        'items': [{'address': 'Musterstrasse 12'}, {'contact': 'alex@example.com'},
                                  {'router': '198.51.100.1', 'ap_mac': 'AA:BB:CC:DD:EE:01', 'v6': 'fe80::1:2:3:4'}],
                        'wifi_ssid': 'ExampleNet-5G', 'note': 'parked at Musterstrasse 12, 1234 Exampletown'},
            'last_seen': '2026-10-15T20:59:59.987654+00:00'}),
        st('sensor.living_room_temperature', '21.53125', {'unit_of_measurement': '°C', 'device_class': 'temperature',
                                                           'friendly_name': 'Living room temperature'}),
        st('media_player.lg_webos_tv_oled55c7v', 'on', {
            'friendly_name': 'LG webOS TV OLED55C7V', 'source': 'HDMI 1', 'source_list': ['HDMI 1', 'Netflix'],
            'volume_level': 0.12000000476837158, 'is_volume_muted': False,
            'entity_picture': '/api/media_player_proxy/media_player.lg?token=CANARYPICTOKEN&cache=1',
            'media_content_id': 'http://198.51.100.5:8096/Videos/1/stream?api_key=CANARYAPIKEY'}),
        st('switch.tv_plug', 'on', {'friendly_name': 'TV Plug'}),
        st('light.living_room_ceiling', 'on', {'friendly_name': 'Ceiling Light', 'brightness': 128,
                                               'color_temp_kelvin': 2700, 'supported_color_modes': ['color_temp']}),
        st('light.living_room_lamp', 'off', {'friendly_name': 'Floor Lamp'}),
        st('light.kitchen', 'off', {'friendly_name': 'Kitchen Lamp'}),
        st('light.bedroom_dead', 'unavailable', {'friendly_name': 'Bedroom', 'restored': True}),
        st('switch.garage_door_opener', 'off', {'friendly_name': 'Garage door opener'}),
        st('cover.garage', 'closed', {'friendly_name': 'Garage', 'device_class': 'garage', 'current_position': 0}),
        st('cover.living_room_blinds', 'open', {'friendly_name': 'Blinds', 'device_class': 'shade',
                                                'current_position': 100}),
        st('lock.front_door', 'locked', {'friendly_name': 'Front door'}),
        st('alarm_control_panel.home_alarm', 'armed_away', {'friendly_name': 'Alarm'}),
        st('scene.movie_night', T0, {'friendly_name': 'Movie night',
                                     'entity_id': ['light.living_room_ceiling', 'media_player.lg_webos_tv_oled55c7v']}),
        st('scene.bad_scene', T0, {'friendly_name': 'Leave', 'entity_id': ['lock.front_door', 'light.kitchen']}),
        st('script.goodnight', 'off', {'friendly_name': 'Goodnight', 'last_triggered': None}),
        st('script.open_up', 'off', {'friendly_name': 'Open up', 'last_triggered': None}),
        st('script.yaml_only', 'off', {'friendly_name': 'Yaml only', 'last_triggered': None}),
        st('device_tracker.alex_airpods', 'home', {'friendly_name': 'Alex AirPods Pro',
                                                    'source': 'AA:BB:CC:DD:EE:01'}),
        st('sensor.ble_aa_bb_cc_dd_ee_01_rssi', '-70', {'source': 'AA:BB:CC:DD:EE:01'}),
        st('calendar.alex_example_com', 'on', {'friendly_name': 'alex@example.com', 'message': 'Dentist',
                                                'location': 'Musterstrasse 12', 'description': 'Dentist'}),
        st('camera.door', 'idle', {'friendly_name': 'Door cam', 'access_token': 'CANARYCAMTOKEN',
                                   'entity_picture': '/api/camera_proxy/camera.door?token=CANARYCAMTOKEN'}),
        st('button.router_restart', 'unknown', {'friendly_name': 'Router restart'}),
        st('vacuum.robi', 'docked', {'friendly_name': 'Robi'}),
        st('vacuum.alex_gmail_com', 'docked', {'friendly_name': 'alex_gmail_com'}),
        st('input_boolean.guest_mode', 'off', {'friendly_name': 'Guest mode'}),
    ]


AREAS = {'areas': [
    ['living_room', 'Living Room', ['light.living_room_ceiling', 'light.living_room_lamp',
                                    'media_player.lg_webos_tv_oled55c7v', 'switch.tv_plug', 'cover.living_room_blinds',
                                    'sensor.living_room_temperature']],
    ['kitchen', 'Küche', ['light.kitchen']],
    ['bedroom', 'Bedroom', ['light.bedroom_dead']],
    ['garage', 'Garage', ['cover.garage', 'switch.garage_door_opener']]],
    'bt': ['device_tracker.alex_airpods']}
DEVICE_URLS = {'light.living_room_ceiling': 'http://ceiling-light.local/', 'light.kitchen': 'http://198.51.100.9',
               'switch.tv_plug': 'http://tv-plug.local/admin?token=CANARYAPIKEY', 'light.bedroom_dead': None,
               'cover.living_room_blinds': 'https://blinds.lan:8443'}
SCRIPT_CFG = {
    'goodnight': {'alias': 'Goodnight', 'sequence': [{'action': 'light.turn_off', 'target': {'entity_id': 'all'}}]},
    'open_up': {'alias': 'Open up', 'sequence': [{'action': 'lock.unlock', 'target': {'entity_id': 'lock.front_door'}}]},
}


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def apply_effect(states, domain, service, body):
    ids = body.get('entity_id')
    ids = [ids] if isinstance(ids, str) else ids or []
    changed = []
    for eid in ids:
        s = states.get(eid)
        if not s:
            continue
        a = s['attributes']
        old = (s['state'], json.dumps(a, sort_keys=True))
        if service == 'turn_off':
            s['state'] = 'off'
        elif service == 'turn_on':
            if domain == 'scene':
                s['state'] = _now()
            elif domain == 'script':
                a['last_triggered'] = _now()
            else:
                s['state'] = 'on'
                if 'brightness_pct' in body:
                    if body['brightness_pct'] == 0:
                        s['state'] = 'off'
                    else:
                        a['brightness'] = round(body['brightness_pct'] * 255 / 100)
                if 'color_temp_kelvin' in body:
                    a['color_temp_kelvin'] = body['color_temp_kelvin']
                if 'color_name' in body:
                    a['rgb_color'] = [255, 0, 0]
        elif service == 'toggle':
            s['state'] = 'off' if s['state'] == 'on' else 'on'
        elif service == 'media_play':
            s['state'] = 'playing'
        elif service == 'media_pause':
            s['state'] = 'paused'
        elif service == 'media_stop':
            s['state'] = 'idle'
        elif service == 'volume_set':
            a['volume_level'] = body['volume_level']
        elif service == 'volume_mute':
            a['is_volume_muted'] = body['is_volume_muted']
        elif service == 'start':
            s['state'] = 'cleaning'
        elif service == 'open_cover':
            s['state'], a['current_position'] = 'open', 100
        if (s['state'], json.dumps(a, sort_keys=True)) != old or domain in ('scene', 'script'):
            s['last_updated'] = _now()
            changed.append(copy.deepcopy(s))
    return changed


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    def _json(self, code, obj, text=False):
        body = (obj if text else json.dumps(obj)).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'text/plain' if text else 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(n) if n else b''
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            return raw.decode('latin-1')

    def _authed(self):
        return self.headers.get('Authorization') == 'Bearer ' + TOKEN

    def _other(self):
        self.server.log.append(('REST', self.command, self.path, self._body()))
        self._json(405, {'message': 'not allowed in fake'})

    do_DELETE = do_PUT = do_PATCH = _other

    def do_GET(self):
        path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        self.server.log.append(('REST', 'GET', path, None))
        if not self._authed():
            return self._json(401, {'message': 'unauthorized'})
        with self.server.lock:
            if path == '/api/states':
                return self._json(200, list(self.server.states.values()))
            m = re.fullmatch(r'/api/states/(.+)', path)
            if m:
                s = self.server.states.get(m.group(1))
                return self._json(200, s) if s else self._json(404, {'message': 'Entity not found.'})
        m = re.fullmatch(r'/api/config/script/config/(.+)', path)
        if m:
            c = SCRIPT_CFG.get(m.group(1))
            return self._json(200, c) if c else self._json(404, {'message': 'Resource not found'})
        return self._json(404, {'message': 'not found'})

    def do_POST(self):
        path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        body = self._body()
        self.server.log.append(('REST', 'POST', path, body))
        if not self._authed():
            return self._json(401, {'message': 'unauthorized'})
        if path == '/api/template':
            m = re.fullmatch(r"\{\{ \{'url': device_attr\(device_id\('([a-z0-9_.]+)'\), 'configuration_url'\)\} \| tojson \}\}",
                             str((body or {}).get('template', '')))
            if m:
                return self._json(200, json.dumps({'url': DEVICE_URLS.get(m.group(1))}), text=True)
            return self._json(200, json.dumps(AREAS), text=True)
        m = re.fullmatch(r'/api/services/([a-z_]+)/([a-z0-9_]+)', path)
        if m:
            d, s = m.groups()
            if (d, s) in self.server.fail:
                return self._json(400, {'message': f'Invalid data for {d}.{s} at 198.51.100.7'})
            self.server.calls.append((d, s, body))
            with self.server.lock:
                changed = apply_effect(self.server.states, d, s, body) if self.server.effects else []
            return self._json(200, changed)
        return self._json(404, {'message': 'not found'})


class FakeHA:
    def __init__(self, effects=True):
        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.srv.daemon_threads = True
        self.srv.log, self.srv.calls, self.srv.fail = [], [], set()
        self.srv.effects = effects
        self.srv.lock = threading.Lock()
        self.srv.states = {s['entity_id']: s for s in fixtures()}
        self.url = f'http://127.0.0.1:{self.srv.server_address[1]}'
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)

    @property
    def log(self):
        return self.srv.log

    @property
    def calls(self):
        return self.srv.calls

    def posts(self):
        return [x for x in self.srv.log if x[1] == 'POST' and x[2].startswith('/api/services/')]

    def __enter__(self):
        self.t.start()
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()
