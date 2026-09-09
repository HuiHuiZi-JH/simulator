# -*- coding: utf-8 -*-
"""Read-only dashboard and setpoint console for the running simulator.

Serves the single-page UI in web/ plus a small JSON API over the same
devices_data table the Modbus server reads, guarded by the same lock.
Standard library only, in keeping with the project's zero-dependency rule.
"""
import json
import logging
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from config.modbus_registers import REGISTERS

logger = logging.getLogger('message')

WEB_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), 'web')

# Registers the simulation reads back as control inputs. Anything not listed
# here is an output that the next tick would overwrite, so the UI refuses it.
WRITABLE = {
    'PV': {'INV.LimitPower': 4},
    'BESS': {'BS.SysAPSetPoint': 14},
    'EV': {'PUB_CONN.ChargePWSet': 8},
}

# Config values the UI needs to scale its sliders sensibly.
LIMIT_KEYS = {
    'PV': ('ratedPower',),
    'BESS': ('maxChargePower', 'maxDischargePower', 'ratedCapacity'),
    'EV': ('PUB_CONN.RatedPW', 'PUB_CONN.MinChargePW'),
    'Load': ('base_power',),
}


class WebServer:
    def __init__(self, devices_data, data_lock, get_sim_hour, port=8080):
        self.devices_data = devices_data
        self.data_lock = data_lock
        self.get_sim_hour = get_sim_hour
        self.port = port
        self.httpd = None

    def snapshot(self):
        """Every device's named points, read as one atomic set."""
        devices = []
        with self.data_lock:
            for key, dev in self.devices_data.items():
                points = REGISTERS.get(dev['type'], {}).get('points', {})
                devices.append({
                    'key': key,
                    'type': dev['type'],
                    'slave_id': dev['slave_id'],
                    'points': {name: dev['data'].get(off, 0.0)
                               for name, off in points.items()},
                    'writable': sorted(WRITABLE.get(dev['type'], {})),
                    'limits': {k: dev['config'].get(k)
                               for k in LIMIT_KEYS.get(dev['type'], ())
                               if dev['config'].get(k) is not None},
                })
        return {'sim_hour': self.get_sim_hour(),
                'wall_time': time.strftime('%H:%M:%S'),
                'devices': devices}

    def apply_setpoint(self, device_key, point, value):
        """Write a control register, exactly as a Modbus FC16 write would."""
        dev = self.devices_data.get(device_key)
        if dev is None:
            return False, 'No device named %s' % device_key
        offset = WRITABLE.get(dev['type'], {}).get(point)
        if offset is None:
            return False, '%s is not a control register on %s' % (point, device_key)
        try:
            value = float(value)
        except (TypeError, ValueError):
            return False, 'Value must be a number'
        value = max(-21474836.48, min(21474836.48, value))
        with self.data_lock:
            dev['data'][offset] = value
        logger.info('Web setpoint: %s %s = %.2f', device_key, point, value)
        return True, 'Set %s to %.2f' % (point, value)

    def run(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, fmt, *args):
                logger.debug('Web %s - %s', self.address_string(), fmt % args)

            def _send(self, code, body, content_type):
                if isinstance(body, str):
                    body = body.encode('utf-8')
                self.send_response(code)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(body)

            def _json(self, code, payload):
                self._send(code, json.dumps(payload), 'application/json; charset=utf-8')

            def do_GET(self):
                if self.path in ('/', '/index.html'):
                    path = os.path.join(WEB_ROOT, 'index.html')
                    try:
                        with open(path, 'rb') as f:
                            self._send(200, f.read(), 'text/html; charset=utf-8')
                    except OSError as e:
                        logger.error('Cannot read %s: %s', path, e)
                        self._send(500, 'Dashboard file missing: %s' % path,
                                   'text/plain; charset=utf-8')
                elif self.path.startswith('/api/state'):
                    self._json(200, server.snapshot())
                else:
                    self._send(404, 'Not found', 'text/plain; charset=utf-8')

            def do_POST(self):
                if not self.path.startswith('/api/control'):
                    self._send(404, 'Not found', 'text/plain; charset=utf-8')
                    return
                try:
                    length = int(self.headers.get('Content-Length', 0))
                    payload = json.loads(self.rfile.read(length) or b'{}')
                except (ValueError, TypeError) as e:
                    self._json(400, {'ok': False, 'error': 'Malformed request: %s' % e})
                    return
                ok, message = server.apply_setpoint(
                    payload.get('device'), payload.get('point'), payload.get('value'))
                self._json(200 if ok else 400, {'ok': ok, 'message': message})

        logger.info('Starting web dashboard on 0.0.0.0:%d...', self.port)
        try:
            self.httpd = ThreadingHTTPServer(('0.0.0.0', self.port), Handler)
            self.httpd.daemon_threads = True
        except OSError as e:
            logger.error('Failed to bind web port %d: %s', self.port, e)
            raise
        logger.info('Web dashboard listening on http://0.0.0.0:%d', self.port)
        self.httpd.serve_forever()

    def stop(self):
        logger.info('Shutting down web dashboard...')
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
