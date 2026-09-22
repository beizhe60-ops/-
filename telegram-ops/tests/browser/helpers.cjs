// Local static server and fictional data shared by browser regressions.
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const server = http.createServer((req, res) => {
  const file = req.url.startsWith('/static/') ? path.basename(req.url) : 'console.html';
  if (!['console.html', 'console.js', 'console.css'].includes(file)) { res.writeHead(404).end(); return; }
  res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
  const override = file === 'console.js' && process.env.CONSOLE_JS;
  res.end(fs.readFileSync(override || path.join(root, 'static', file)));
});
function fixture() {
  return { running: false, application: {configured:true,api_id:123},
    stats:{hits:1,pending:0,sent:0,failed:0},chats:[],
    accounts:[{id:1,name:'测试监测',phone:'+12025550101',role:'monitor',status:'active',has_session:true,send_enabled:true,monitor_chat_ids:[-1001]},
      {id:2,name:'测试私信',phone:'+12025550102',role:'sender',status:'active',has_session:true,send_enabled:true,receive_chat_ids:[-1002],dm_template:'测试文案',rotation_weight:3}],
    tasks:Array.from({length:12},(_,i)=>({id:i+1,name:'测试绑定'+i,account_a:1,account_b:null,source_chats:[-1001],relay_chat:-1002,keywords:'咨询 服务',exclude_keywords:'',ignore_users:'',match_mode:'any',template:'',enabled:true})) };
}
const gate = () => { let release; const promise=new Promise(r=>release=r); return {promise,release}; };

module.exports = { server, fixture, gate };
