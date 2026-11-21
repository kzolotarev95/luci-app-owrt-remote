param(
    [string]$Vps = 'root@132.243.19.232',
    [ValidatePattern('^[a-zA-Z0-9_-]{1,80}$')][string]$RouterId = 'node-10'
)
$ErrorActionPreference = 'Stop'
$Probe = @'
python3 - <<'PY'
import hashlib,http.client,json,pathlib,re,sqlite3,subprocess
router_id='__ROUTER_ID__'
with sqlite3.connect('file:/var/lib/owrt-remote/hub.db?mode=ro',uri=True) as conn:
    conn.row_factory=sqlite3.Row
    row=conn.execute('select * from routers where id=?',(router_id,)).fetchone()
    if row is None: raise SystemExit('Router not found')
    status=json.loads(row['status_json'] or '{}')
    print('ROUTER',row['id'])
    print('PORTS',json.dumps({k:row[k] for k in ('entry_port','ssh_entry_port','tachyon_entry_port')}))
    print('REPORTED_TACHYON',json.dumps(status.get('tachyon',{})))
config_path=pathlib.Path('/etc/xray/owrt-remote.json')
data=json.loads(config_path.read_text())
for entry in data.get('inbounds',[]):
    if entry.get('tag') in ('entry-'+router_id,'tachyon-'+router_id):
        print('INBOUND',json.dumps(entry))
for rule in data.get('routing',{}).get('rules',[]):
    if any(t in ('entry-'+router_id,'tachyon-'+router_id) for t in rule.get('inboundTag',[])):
        print('RULE',json.dumps(rule))
for name in ('entry_port','tachyon_entry_port'):
    port=int(row[name] or 0)
    if not port: continue
    c=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
    try:
        c.request('GET','/')
        r=c.getresponse(); body=r.read(32768).decode('utf-8','replace')
        title=re.search(r'<title[^>]*>(.*?)</title>',body,re.S|re.I)
        print('HTTP_PROBE',name,'status=',r.status,'location=',r.getheader('Location'),'title=',title.group(1)[:150] if title else None)
    except Exception as exc: print('HTTP_PROBE',name,type(exc).__name__,str(exc))
    finally:c.close()
for command in (['/usr/local/bin/xray','version'],['systemctl','show','owrt-remote-xray','-p','ExecStart','-p','MainPID']):
    r=subprocess.run(command,capture_output=True,text=True)
    print(r.stdout[:1600])
print('TACHYON_VPS_READ_ONLY_DONE')
PY
'@
$Probe = $Probe.Replace('__ROUTER_ID__', $RouterId).Replace([string][char]13, [string]::Empty)
$Encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Probe + [char]10))
& ssh -T -o ConnectTimeout=10 $Vps "printf %s $Encoded | base64 -d | sh -s"
if ($LASTEXITCODE -ne 0) { throw 'Read-only VPS diagnostics failed.' }
