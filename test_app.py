import base64
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path
import unittest
from urllib.parse import urlsplit, parse_qs
from app import create_app, BrokerError, Broker

BASE="https://connector.example"
CALLBACK="https://chatgpt.com/connector_platform/oauth_redirect"

class FakeBroker:
    def __init__(self):
        self.calls=[]
        self.fail=False
    def call(self,path,payload=None):
        self.calls.append((path,payload))
        if payload is not None:
            if self.fail: raise BrokerError('Timeout; Status unbekannt')
            return {'id':123,'status':'NEW','ticker':payload['ticker'],'quantity':payload['quantity']}
        if path.endswith('instruments'):
            return [{'ticker':'AAPL_US_EQ','type':'STOCK','name':'Apple','isin':'US0378331005','currencyCode':'USD'}]
        if path.endswith('summary'): return {'cash':{'availableToTrade':100}}
        return []

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.cfg={'PUBLIC_BASE_URL':BASE,'APP_PASSWORD':'p'*40,'OAUTH_CLIENT_SECRET':'c'*40,'SESSION_SECRET':'s'*40,
            'T212_API_KEY':'fake-key','T212_API_SECRET':'fake-secret','OAUTH_REDIRECT_URI':CALLBACK,
            'STATE_DB':self.temp.name+'/state.sqlite','ENABLE_TRADING':'true'}
        self.broker=FakeBroker()
        self.app=create_app(self.cfg,self.broker)
        self.app.testing=True
        self.client=self.app.test_client()
    def tearDown(self): self.temp.cleanup()
    def get(self,path,**kw): return self.client.get(path,base_url=BASE,**kw)
    def post(self,path,**kw):
        headers=kw.pop('headers',{});headers.setdefault('Origin',BASE)
        return self.client.post(path,base_url=BASE,headers=headers,**kw)
    def csrf(self,response): return re.search(r'name="csrf" value="([^"]+)"',response.text).group(1)
    def auth(self,**override):
        self.verifier='v'*64
        q={'client_id':'trading212-chatgpt','redirect_uri':CALLBACK,'response_type':'code',
           'code_challenge_method':'S256','code_challenge':base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).decode().rstrip('='),
           'state':'random-state','scope':'portfolio:read orders:prepare','resource':BASE+'/mcp'}
        q.update(override)
        return self.get('/authorize',query_string=q)
    def code(self):
        r=self.auth()
        r=self.post('/authorize',data={'csrf':self.csrf(r),'password':self.cfg['APP_PASSWORD']})
        self.assertEqual(r.status_code,303)
        self.assertEqual(parse_qs(urlsplit(r.location).query)['state'],['random-state'])
        return parse_qs(urlsplit(r.location).query)['code'][0]
    def exchange(self,code,**override):
        data={'grant_type':'authorization_code','code':code,'code_verifier':self.verifier,
              'redirect_uri':CALLBACK,'resource':BASE+'/mcp','client_id':'trading212-chatgpt','client_secret':self.cfg['OAUTH_CLIENT_SECRET']}
        data.update(override)
        return self.post('/token',data=data)
    def access(self): return self.exchange(self.code()).json['access_token']
    def rpc(self,token,method='tools/list',params=None):
        return self.post('/mcp',headers={'Authorization':'Bearer '+token},json={'jsonrpc':'2.0','id':1,'method':method,'params':params or {}})
    def prepare(self,token,side='buy',quantity=2):
        r=self.rpc(token,'tools/call',{'name':'prepare_market_order','arguments':{'ticker':'AAPL_US_EQ','side':side,'quantity':quantity}})
        self.assertFalse(r.json['result']['isError'])
        return json.loads(r.json['result']['content'][0]['text'])['confirmation_url'].removeprefix(BASE)
    def view_order(self,url):
        r=self.get(url)
        return self.post(url,data={'action':'view','csrf':self.csrf(r),'password':self.cfg['APP_PASSWORD']})
    def writes(self): return [c for c in self.broker.calls if c[1] is not None]

    def test_health_no_secrets(self):
        r=self.get('/health');self.assertEqual(r.status_code,200)
        self.assertNotIn('fake-key',r.text)
    def test_metadata(self):
        self.assertEqual(self.get('/.well-known/oauth-protected-resource/mcp').json['resource'],BASE+'/mcp')
        self.assertEqual(self.get('/.well-known/oauth-authorization-server').json['code_challenge_methods_supported'],['S256'])
    def test_auth_required(self):
        r=self.post('/mcp',json={});self.assertEqual(r.status_code,401)
        self.assertIn('resource_metadata',r.headers['WWW-Authenticate'])
    def test_bad_redirect(self): self.assertEqual(self.auth(redirect_uri='https://evil.example').status_code,400)
    def test_bad_resource(self): self.assertEqual(self.auth(resource='https://evil.example/mcp').status_code,400)
    def test_no_plain_pkce(self): self.assertEqual(self.auth(code_challenge_method='plain').status_code,400)
    def test_bad_scope(self): self.assertEqual(self.auth(scope='orders:execute').status_code,400)
    def test_wrong_password(self):
        r=self.auth();r=self.post('/authorize',data={'csrf':self.csrf(r),'password':'wrong'})
        self.assertEqual(r.status_code,403)
    def test_csrf_required(self): self.assertEqual(self.post('/authorize',data={'password':self.cfg['APP_PASSWORD']}).status_code,403)
    def test_external_origin(self):
        self.assertEqual(self.post('/authorize',headers={'Origin':'https://evil.example'}).status_code,403)
    def test_wrong_verifier(self): self.assertEqual(self.exchange(self.code(),code_verifier='x'*64).status_code,400)
    def test_wrong_client_secret(self): self.assertEqual(self.exchange(self.code(),client_secret='wrong').status_code,401)
    def test_code_one_use(self):
        code=self.code();self.assertEqual(self.exchange(code).status_code,200)
        self.assertEqual(self.exchange(code).status_code,400)
    def test_refresh_rotation(self):
        initial=self.exchange(self.code()).json
        data={'grant_type':'refresh_token','refresh_token':initial['refresh_token'],'client_id':'trading212-chatgpt','client_secret':self.cfg['OAUTH_CLIENT_SECRET']}
        self.assertEqual(self.post('/token',data=data).status_code,200)
        self.assertEqual(self.post('/token',data=data).status_code,400)
    def test_initialization_and_read(self):
        a=self.access()
        self.assertEqual(self.rpc(a,'initialize',{'protocolVersion':'2025-06-18'}).json['result']['protocolVersion'],'2025-06-18')
        r=self.rpc(a,'tools/call',{'name':'account_summary','arguments':{}})
        self.assertFalse(r.json['result']['isError']);self.assertEqual(self.writes(),[])
    def test_no_execute_tool(self):
        names=[t['name'] for t in self.rpc(self.access()).json['result']['tools']]
        self.assertNotIn('execute_order',names)
    def test_prepare_and_get_never_trade(self):
        url=self.prepare(self.access());r=self.get(url)
        self.assertEqual(r.status_code,200);self.assertNotIn('Apple',r.text)
        self.assertEqual(self.writes(),[])
    def test_browser_buy_and_duplicate_click(self):
        url=self.prepare(self.access());r=self.view_order(url)
        self.assertIn('KAUFEN',r.text)
        form={'action':'confirm','csrf':self.csrf(r)}
        self.assertEqual(self.post(url,data=form).status_code,200)
        self.assertEqual(self.post(url,data=form).status_code,403)
        self.assertEqual(len(self.writes()),1);self.assertEqual(self.writes()[0][1]['quantity'],2)
    def test_browser_sell_negative_quantity(self):
        url=self.prepare(self.access(),side='sell');r=self.view_order(url)
        self.post(url,data={'action':'confirm','csrf':self.csrf(r)})
        self.assertEqual(self.writes()[0][1]['quantity'],-2)
    def test_confirm_without_login(self):
        url=self.prepare(self.access());r=self.get(url)
        self.assertEqual(self.post(url,data={'action':'confirm','csrf':self.csrf(r)}).status_code,403)
        self.assertEqual(self.writes(),[])
    def test_unknown_response_no_retry(self):
        url=self.prepare(self.access());r=self.view_order(url);self.broker.fail=True
        self.post(url,data={'action':'confirm','csrf':self.csrf(r)})
        self.assertEqual(len(self.writes()),1)
        ident=url.split('/')[-1]
        with sqlite3.connect(self.cfg['STATE_DB']) as c:
            self.assertEqual(c.execute('SELECT status FROM tickets WHERE id=?',(ident,)).fetchone()[0],'unknown')
    def test_expired_ticket(self):
        url=self.prepare(self.access())
        with sqlite3.connect(self.cfg['STATE_DB']) as c: c.execute('UPDATE tickets SET expires=0')
        r=self.view_order(url);self.assertNotIn('Diese Order jetzt',r.text);self.assertEqual(self.writes(),[])
    def test_invalid_quantities(self):
        a=self.access()
        for quantity in [0,-1,True,'2',float('inf')]:
            r=self.rpc(a,'tools/call',{'name':'prepare_market_order','arguments':{'ticker':'AAPL_US_EQ','side':'buy','quantity':quantity}})
            self.assertTrue(r.json['result']['isError'])
        self.assertEqual(self.writes(),[])
    def test_config_fail_closed(self):
        with self.assertRaises(RuntimeError): create_app(dict(self.cfg,APP_PASSWORD='short'),self.broker)
    def test_api_path_allowlist(self):
        with self.assertRaises(BrokerError): Broker('a','b','live').call('https://evil.example')
    def test_tokens_persist_but_not_plaintext(self):
        a=self.access()
        self.assertNotIn(a.encode(),Path(self.cfg['STATE_DB']).read_bytes())
        c=create_app(self.cfg,self.broker).test_client()
        r=c.post('/mcp',base_url=BASE,headers={'Authorization':'Bearer '+a},json={'jsonrpc':'2.0','id':1,'method':'ping'})
        self.assertEqual(r.status_code,200)
    def test_secret_rotation_invalidates_access(self):
        token=self.access()
        other=create_app(dict(self.cfg,APP_PASSWORD='n'*40),self.broker).test_client()
        r=other.post('/mcp',base_url=BASE,headers={'Authorization':'Bearer '+token},json={'jsonrpc':'2.0','id':1,'method':'ping'})
        self.assertEqual(r.status_code,401)
    def test_account_change_invalidates_ticket(self):
        url=self.prepare(self.access())
        other=create_app(dict(self.cfg,T212_API_KEY='different-account'),self.broker).test_client()
        self.assertEqual(other.get(url,base_url=BASE).status_code,410)
    def test_duplicate_pending_ticker_is_blocked(self):
        token=self.access();self.prepare(token)
        r=self.rpc(token,'tools/call',{'name':'prepare_market_order','arguments':{'ticker':'AAPL_US_EQ','side':'buy','quantity':2}})
        self.assertTrue(r.json['result']['isError']);self.assertEqual(self.writes(),[])
    def test_trading_toggle(self):
        other=create_app(dict(self.cfg,ENABLE_TRADING='false'),self.broker)
        self.client=other.test_client()
        token=self.access()
        r=self.rpc(token,'tools/call',{'name':'prepare_market_order','arguments':{'ticker':'AAPL_US_EQ','side':'buy','quantity':2}})
        self.assertTrue(r.json['result']['isError'])


if __name__=='__main__': unittest.main()
