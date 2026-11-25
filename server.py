"""Flying Fish voucher prototype. Python 3.10+, standard library only."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import urlopen, Request
import datetime, hmac, json, os, re, secrets, sqlite3, unicodedata
ROOT=Path(__file__).resolve().parent
DATA=Path(os.environ.get('DATA_DIR',ROOT/'data')); DATA.mkdir(parents=True,exist_ok=True)
LIVE=False  # This package is intentionally test-only.
SOURCE='https://www.bpost.be/site/nl/docs/zipcodes/zipcode_num.html'
ADMIN=os.environ.get('ADMIN_TOKEN','')
TERMS=os.environ.get('PROMO_TERMS',''); PRIVACY=os.environ.get('PRIVACY_TEXT',''); VTERMS=os.environ.get('VOUCHER_TERMS','')
IMAGE=os.environ.get('PRODUCT_IMAGE','')
VAT_URL=os.environ.get('VAT_VERIFY_URL',''); VAT_TOKEN=os.environ.get('VAT_VERIFY_TOKEN','')
class Table(HTMLParser):
 def __init__(self): super().__init__(); self.rows=[]; self.row=None; self.cell=None
 def handle_starttag(self,t,a):
  if t=='tr': self.row=[]
  elif t in ('td','th') and self.row is not None: self.cell=[]
 def handle_data(self,d):
  if self.cell is not None: self.cell.append(d)
 def handle_endtag(self,t):
  if t in ('td','th') and self.cell is not None: self.row.append(' '.join(''.join(self.cell).split())); self.cell=None
  elif t=='tr' and self.row is not None: self.rows.append(self.row); self.row=None

def read_postcodes():
 p=DATA/'postcodes.json'
 if p.exists():
  d=json.loads(p.read_text(encoding='utf-8'))
  if isinstance(d,dict) and len(d)>800 and all(re.fullmatch(r'\d{4}',k) and isinstance(v,list) and v and all(isinstance(n,str) and n.strip() for n in v) for k,v in d.items()): return d
  raise RuntimeError('Onvolledige of ongeldige postcodecache')
 raw=(DATA/'bpost.html').read_bytes() if (DATA/'bpost.html').exists() else urlopen(Request(SOURCE,headers={'User-Agent':'FlyingFishHoreca/1.0'}),timeout=20).read()
 try: html=raw.decode('utf-8')
 except UnicodeDecodeError: html=raw.decode('cp1252')
 t=Table();t.feed(html); d={}
 for row in t.rows:
  if len(row)>=2 and re.fullmatch(r'\d{4}',row[0]) and row[1]:d.setdefault(row[0],set()).add(row[1])
 if len(d)<800:raise RuntimeError('bpost-lijst kon niet volledig worden gelezen')
 d={k:sorted(v,key=str.casefold) for k,v in d.items()}; p.write_text(json.dumps(d,ensure_ascii=False),encoding='utf-8'); return d
HTML=(ROOT/'index.html').read_text(encoding='utf-8')
if LIVE:
 if not all((TERMS,PRIVACY,VTERMS,ADMIN,IMAGE,VAT_TOKEN)) or len(ADMIN)<24 or not VAT_URL.startswith('https://'): raise SystemExit('LIVE vereist goedgekeurde PROMO_TERMS, PRIVACY_TEXT, VOUCHER_TERMS, PRODUCT_IMAGE ADMIN_TOKEN (min. 24 tekens) en een geautoriseerde HTTPS VAT_VERIFY_URL met VAT_VERIFY_TOKEN.')
 POSTCODES=read_postcodes()
else:
 POSTCODES=json.loads(re.search(r'const SAMPLE=(\{.*?\});',HTML,re.S).group(1))
DB=DATA/('vouchers.sqlite3' if LIVE else 'demo-v3.sqlite3')
def db():
 c=sqlite3.connect(DB,timeout=10);c.row_factory=sqlite3.Row;return c
with db() as c:
 c.execute('CREATE TABLE IF NOT EXISTS vouchers(code TEXT PRIMARY KEY, address_key TEXT UNIQUE NOT NULL, vat TEXT UNIQUE NOT NULL, request_id TEXT UNIQUE NOT NULL, business TEXT NOT NULL, address TEXT NOT NULL, postcode TEXT NOT NULL, city TEXT NOT NULL, email TEXT NOT NULL, wholesaler TEXT NOT NULL, issued TEXT NOT NULL, redeemed TEXT)')
 # Do not silently discard earlier live registrations without VAT IDs.
 if not {'vat','request_id'}.issubset({r['name'] for r in c.execute('PRAGMA table_info(vouchers)')}):raise SystemExit('Bestaande database vereist gecontroleerde migratie en btw-verrijking; geen nieuwe vouchers uitgegeven.')
def vat_number(s):
 if not isinstance(s,str):raise ValueError('Vul een Belgisch btw-nummer in.')
 n=re.sub(r'[.\s-]','',re.sub(r'^BE\s*','',s.strip().upper()))
 if not re.fullmatch(r'[01][0-9]{9}',n) or int(n[:8])==0 or int(n[8:])!=97-int(n[:8])%97:raise ValueError('Ongeldig Belgisch btw-nummer: controleer de 10 cijfers en het controlegetal.')
 return n
class VerificationUnavailable(Exception):pass
def verify_vat(v):
 # Contract for an approved AB InBev integration, not a pre-connected public KBO API.
 if not LIVE:return
 try:
  req=Request(VAT_URL,data=json.dumps({'vat':'BE'+v['vat'],'address':v['address'],'postcode':v['postcode'],'city':v['city'],'business':v['business']}).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+VAT_TOKEN},method='POST')
  with urlopen(req,timeout=12) as r: result=json.loads(r.read(65536))
  if not isinstance(result,dict) or result.get('vat')!='BE'+v['vat'] or type(result.get('valid')) is not bool or type(result.get('location_match')) is not bool:raise VerificationUnavailable()
 except Exception:raise VerificationUnavailable()
 if not result['valid'] or not result['location_match']:raise ValueError('Btw-nummer of vestigingsadres kon niet worden bevestigd. Neem contact op met je bierhandelaar.')
def normalized(s):return ''.join(x for x in unicodedata.normalize('NFKD',s).casefold() if x.isalnum() and not unicodedata.combining(x))
def stamp():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def code():return ('FF-' if LIVE else 'FF-DEMO-')+'-'.join(secrets.token_hex(2).upper() for _ in range(5))
def validate(d):
 if not isinstance(d,dict):raise ValueError('Ongeldige aanvraag.')
 fields={'business':120,'address':180,'postcode':4,'city':120,'email':180,'wholesaler':120}; v={}
 for k,n in fields.items():
  s=d.get(k); s=s.strip() if isinstance(s,str) else ''
  if not s or len(s)>n or any(ord(x)<32 for x in s):raise ValueError('Vul alle gegevens correct in.')
  v[k]=s
 v['vat']=vat_number(d.get('vat')); v['request_id']=d.get('requestId','')
 if not isinstance(v['request_id'],str) or not re.fullmatch(r'[A-F0-9]{32}',v['request_id']):raise ValueError('Ongeldige aanvraagidentificatie. Vernieuw de pagina.')
 if v['city'] not in POSTCODES.get(v['postcode'],[]):raise ValueError('Postcode en gemeente komen niet overeen.')
 if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',v['email']):raise ValueError('Ongeldig e-mailadres.')
 if not v['email'].lower().endswith('@example.invalid'):raise ValueError('Gebruik uitsluitend fictieve testgegevens en een e-mailadres dat eindigt op @example.invalid.')
 if not re.search(r'\d',v['address']):raise ValueError('Vermeld een huisnummer in het adres.')
 if d.get('confirmed') is not True:raise ValueError('Bevestig de voorwaarden en meerderjarigheid.')
 return v
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def send(self,status,data,typ='application/json; charset=utf-8'):
  b=data.encode() if isinstance(data,str) else json.dumps(data,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type',typ);self.send_header('Content-Length',str(len(b)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer');self.send_header('X-Frame-Options','DENY');self.end_headers();self.wfile.write(b)
 def err(self,status,msg):self.send(status,{'error':msg})
 def do_GET(self):
  if self.path in ('/','/index.html'):self.send(200,HTML.replace('<meta name="robots" content="noindex">','') if LIVE else HTML,'text/html; charset=utf-8')
  elif self.path=='/api/config':self.send(200,dict(server=True,demo=not LIVE,postcodes=POSTCODES,terms=TERMS or None,privacy=PRIVACY or None,voucherTerms=VTERMS or None,productImage=IMAGE or None))
  elif self.path=='/health':self.send(200,{'ok':True,'demo':not LIVE})
  else:self.err(404,'Niet gevonden.')
 def do_POST(self):
  try:
   if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.err(415,'Gebruik JSON.')
   n=int(self.headers.get('Content-Length','0'))
   if n<=0 or n>8192:return self.err(400,'Ongeldige aanvraag.')
   d=json.loads(self.rfile.read(n));
   if self.path=='/api/claim':
    v=validate(d); key=v['postcode']+'|'+normalized(v['address']); v.update(code=code(),issued=stamp(),demo=not LIVE)
    def previous(c):
     row=c.execute('SELECT * FROM vouchers WHERE request_id=?',(v['request_id'],)).fetchone()
     if row and all(row[k]==v[k] for k in ('vat','business','address','postcode','city','email','wholesaler')):
      return {**{k:row[k] for k in ('vat','business','address','postcode','city','email','wholesaler','code','issued')},'demo':not LIVE}
     return None
    with db() as c:
     old=previous(c)
     if old:return self.send(200,old)
     if c.execute('SELECT 1 FROM vouchers WHERE vat=? OR address_key=?',(v['vat'],key)).fetchone():return self.err(409,'Voor dit btw-nummer of adres bestaat al een voucher. Neem contact op met je bierhandelaar.')
    verify_vat(v)
    try:
     with db() as c:c.execute('INSERT INTO vouchers(code,address_key,vat,request_id,business,address,postcode,city,email,wholesaler,issued) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(v['code'],key,v['vat'],v['request_id'],v['business'],v['address'],v['postcode'],v['city'],v['email'],v['wholesaler'],v['issued']))
    except sqlite3.IntegrityError:
     with db() as c:
      old=previous(c)
      if old:return self.send(200,old)
     return self.err(409,'Voor dit btw-nummer of adres bestaat al een voucher. Neem contact op met je bierhandelaar.')
    v.pop('request_id');return self.send(201,v)
   if self.path in ('/api/admin/verify','/api/admin/redeem'):
    if not ADMIN or not hmac.compare_digest(self.headers.get('Authorization','').encode(),('Bearer '+ADMIN).encode()):return self.err(401,'Geen toegang.')
    token=d.get('code','') if isinstance(d,dict) else ''
    with db() as c:
     c.execute('BEGIN IMMEDIATE');r=c.execute('SELECT code,vat,business,address,postcode,city,wholesaler,issued,redeemed FROM vouchers WHERE code=?',(str(token),)).fetchone()
     if not r:return self.err(404,'Code onbekend.')
     if self.path.endswith('/redeem'):
      if r['redeemed']:return self.err(409,'Voucher reeds ingewisseld.')
      c.execute('UPDATE vouchers SET redeemed=? WHERE code=?',(stamp(),token)); r=c.execute('SELECT code,vat,business,address,postcode,city,wholesaler,issued,redeemed FROM vouchers WHERE code=?',(token,)).fetchone()
     self.send(200,dict(r));return
   self.err(404,'Niet gevonden.')
  except VerificationUnavailable:self.err(503,'De officiële btw-controle is tijdelijk niet bereikbaar. Probeer later opnieuw; er is geen voucher aangemaakt.')
  except ValueError as e:self.err(400,str(e) if not isinstance(e,json.JSONDecodeError) else 'Ongeldige aanvraag.')
  except (TypeError,UnicodeError):self.err(400,'Controleer de ingevulde gegevens.')
  except Exception:self.err(500,'De aanvraag is tijdelijk niet beschikbaar.')
if __name__=='__main__':
 port=int(os.environ.get('PORT','8080')); print(f'Flying Fish {"LIVE" if LIVE else "DEMO"}: http://localhost:{port}',flush=True); ThreadingHTTPServer(('0.0.0.0',port),Handler).serve_forever()
