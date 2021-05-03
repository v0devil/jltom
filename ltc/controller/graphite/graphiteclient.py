import argparse
import hashlib
import hmac
import json
import logging
import sys
import time
import urllib.error
from urllib.parse import quote
from urllib.request import (
    HTTPBasicAuthHandler,
    HTTPPasswordMgrWithDefaultRealm,
    build_opener,
    install_opener,
    urlopen,
)

logger = logging.getLogger(__name__)

parser = argparse.ArgumentParser(description='Graphite metric extraction')
parser.add_argument('-u', '--username', help='Username', default='graphite')
parser.add_argument('-p', '--password', help='Password', required=True)
parser.add_argument('-U', '--url', help='Graphite URL', required=True)
parser.add_argument('-q', '--query', help='Query string', required=True)
parser.add_argument('-f', '--tsfrom', help='Timestamp start')
parser.add_argument('-t', '--tsto', help='Timestamp end')
parser.add_argument(
    '-w', '--warning', type=float,
    help='Warning value threshold', required=True)
parser.add_argument(
    '-c', '--critical', type=float,
    help='Critical value threshold', required=True)

# Copy & pasted from this page:
# https://rosettacode.org/wiki/Brace_expansion#Python
def brace_expansion(s, depth=0):
    out = [""]
    while s:
        c = s[0]
        if depth and (c == ',' or c == '}'):
            return out, s
        if c == '{':
            x = getgroup(s[1:], depth+1)
            if x:
                out, s = [a+b for a in out for b in x[0]], x[1]
                continue
        if c == '\\' and len(s) > 1:
            s, c = s[1:], c + s[1]

        out, s = [a+c for a in out], s[1:]

    return out, s


def getgroup(s, depth):
    out, comma = [], False
    while s:
        g, s = brace_expansion(s, depth)
        if not s:
            break
        out += g

        if s[0] == '}':
            if comma:
                return out, s[1:]
            return ['{' + a + '}' for a in out], s[1:]

        if s[0] == ',':
            comma, s = True, s[1:]

    return None


class GraphiteClient(object):
    def __init__(self, url, username, password):
        self.username = username
        self.password = password
        self.token = self._make_token(username, password)
        self.graphite_url = url
        self.url = url

    def _make_token(self, username, password):
        timestamp = str(int(time.time()))
        h = hmac.new(bytes(password, 'utf-8'), digestmod=hashlib.sha256)
        h.update(timestamp.encode('utf-8'))
        h.update(':'.encode('utf-8'))
        h.update(username.encode('utf-8'))
        return '{}:{}:{}'.format(h.hexdigest(), timestamp, username)

    def query(self, query, ts_start, ts_end, output_format='json'):
        results = []
        for q in brace_expansion(query)[0]:
            # build graphite url
            args = {
                '__auth_token': self.token,
                'target': q,
                'format': output_format,
                'from': str(ts_start),
                'until': str(ts_end),
                'width': 1000,
                'height': 400,
                'lineWidth': 2,
                'areaMode': 'first',
                'fontBold': 'true',
                'fontSize': 11,
                'colorList': '389912',
            }
            url = '{}/render?'.format(self.url)
            for k, v in args.items():
                url += '{}={}&'.format(quote(str(k)), quote(str(v)))
            print(url)
            # Basic auth header
            password_mgr = HTTPPasswordMgrWithDefaultRealm()
            password_mgr.add_password(
                None,
                self.graphite_url,
                self.username,
                self.password,
            )
            auth_handler = HTTPBasicAuthHandler(password_mgr)
            opener = build_opener(auth_handler)
            install_opener(opener)

            retry = 5
            while retry > 0:
                try:
                    url_open = urlopen(url)
                    break
                except urllib.error.HTTPError:
                    time.sleep(3)
                    retry -= 1
            if retry < 1:
                raise urllib.error.HTTPError(
                    'Unable to fetch graphite data for query: {}'.format(q))

            if output_format == 'png':
                result = url_open.read()
                results.append(result)
            else:
                result = json.loads(url_open.read().decode('utf-8'))
                results.extend(result)

        return results


if __name__ == '__main__':
    args = parser.parse_args()

    client = GraphiteClient(args.url, args.username, args.password)
    try:
        value = client.query(args.query, args.tsfrom, args.tsto)
    except Exception as e:
        print('UNKNOWN: Query failed - {}'.format(e))
        sys.exit(3)

    if value >= args.critical:
        print('CRITICAL: {} over {}-{}'.format(value, args.tsfrom, args.tsto))
        sys.exit(2)
    elif value >= args.warning:
        print('WARNING: {} over {}-{}'.format(value, args.tsfrom, args.tsto))
        sys.exit(1)
    else:
        print('OK: {} over {}-{}'.format(value, args.tsfrom, args.tsto))
        sys.exit(0)
