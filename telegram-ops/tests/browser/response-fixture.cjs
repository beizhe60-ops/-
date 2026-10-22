// Shared isolated browser server; API writes only change fictional memory.
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { fixture } = require('./helpers.cjs');
const root = path.resolve(__dirname, '../..');
const delay = Number(process.env.API_DELAY_MS || 500);
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const state = fixture();
state.chats = Array.from({length:300},(_,i)=>({id:-(1001+i),account_id:1,title:'测试群组 '+i,type:'supergroup'}));
state.accounts[1].dm_copy_options = {format:'plain',image_id:null};
let ai = {configured:true,enabled:false,account_id:1,source_chat_ids:[-1001],api_url:'https://example.test/v1/chat/completions',api_key_configured:true,model:'test-model',trigger:'schedule',target_type:'group',target_id:-1002,schedule_time:'23:00',message_count:50,trigger_count:50,lookback_hours:24};
let feed = {id:1,name:'测试采集',account_id:1,source:'example_channel',source_id:-1001,source_title:'测试来源',target_id:-1002,exclude_terms:'广告',ad_terms:'广告合作',filter_ads:true,auto_publish:true,template_title:'曝光档案',emoji_style:'alert',body_format:'normal',enabled:false,revision:1,counts:{draft:1}};
let customRecords=null, campaigns=[], guards=[];
const calls=[], gates=new Map(), failures=new Map();
function holdNext(route) { let release,markStarted; const promise=new Promise(resolve=>release=resolve); const started=new Promise(resolve=>markStarted=resolve); const gate={promise,release,started,markStarted};gates.set(route,gate);return gate; }
function failNext(route) { failures.set(route,true); }

const server=http.createServer(async(req,res)=>{
 const url=new URL(req.url,'http://localhost');
 if(!url.pathname.startsWith('/api/')){
  const name=url.pathname.startsWith('/static/')?path.basename(url.pathname):'console.html';
  const file=path.join(root,'static',name);
  if(!fs.existsSync(file)){res.writeHead(404).end();return;}
  res.setHeader('Content-Type',name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':'text/html');
  res.end(fs.readFileSync(file));return;
 }
 let raw='';for await(const chunk of req)raw+=chunk;
 const body=raw?JSON.parse(raw):{};
 const event={path:url.pathname,query:url.search,method:req.method,start:Date.now()};calls.push(event);
 const gate=gates.get(url.pathname);gates.delete(url.pathname);gate?.markStarted();
 const failure=failures.get(url.pathname);failures.delete(url.pathname);
 if(failure){await pause(delay);event.end=Date.now();res.writeHead(503,{'Content-Type':'application/json'}).end(JSON.stringify({detail:'模拟请求失败'}));return;}
 let data;
 if(url.pathname==='/api/state') data=structuredClone(state);
 else if(url.pathname==='/api/records')data=customRecords||{total:120,items:Array.from({length:50},(_,i)=>({id:i+1,task_id:1,stage:'relay',username:'example_user',source_title:'测试群',chat_id:-1001,text:'测试消息正文。'.repeat(100),original_text:'咨询',status:'sent',created_at:'2026-10-23T00:00:00'}))};
 else if(url.pathname==='/api/ai/settings'){if(req.method==='PUT')ai={...ai,...body};data=ai;}
 else if(url.pathname==='/api/ai/activity')data={collected:1,next_at:null,runs:[]};
 else if(url.pathname==='/api/ai/preview')data={preview:'这是模拟摘要，未发送消息。',message_count:1};
 else if(url.pathname==='/api/broadcasts')data=campaigns;
 else if(/^\/api\/broadcasts\/\d+\/(pause|cancel|start)$/.test(url.pathname)){const parts=url.pathname.split('/');const t=campaigns.find(t=>t.id===Number(parts[3]));t.status={pause:'paused',cancel:'cancelled',start:'queued'}[parts[4]];data=t;}
 else if(url.pathname==='/api/accounts/1/listener')data={connected:true,update_loop_running:true,events:[{at:'2026-10-23T00:00:00Z',chat_id:-1001,message_id:21,outcome:'received'},{at:'2026-10-23T00:00:00Z',chat_id:-1001,message_id:21,task_id:1,outcome:'命中排除词'},{at:'2026-10-23T00:00:00Z',chat_id:-9999,message_id:22,task_id:2,outcome:'unrelated-event'}]};
 else if(url.pathname==='/api/guards'){if(req.method==='POST')guards.push({id:Date.now(),...body});data=req.method==='GET'?guards:{ok:true};}
 else if(/^\/api\/guards\/\d+$/.test(url.pathname)&&req.method==='DELETE'){guards=guards.filter(g=>g.id!==Number(url.pathname.split('/').pop()));data={ok:true};}
 else if(/^\/api\/records\/\d+\/cancel$/.test(url.pathname)){customRecords.items.find(r=>r.id===Number(url.pathname.split('/')[3])).status='cancelled';data={ok:true};}
 else if(url.pathname==='/api/accounts/2/receive-groups'){Object.assign(state.accounts[1],body);data={chat_ids:body.chat_ids};}
 else if(url.pathname==='/api/channel-feeds')data=[feed];
 else if(url.pathname==='/api/channel-drafts')data={total:0,items:[]};
 else if(url.pathname==='/api/channel-feeds/1'){feed={...feed,...body,enabled:false,revision:feed.revision+1};data=feed;}
 else if(url.pathname==='/api/accounts/2/weight'){state.accounts[1].rotation_weight=body.weight;data={rotation_weight:body.weight};}
 else if(url.pathname==='/api/tasks/1/toggle'){state.tasks[0].enabled=!state.tasks[0].enabled;data=state.tasks[0];}
 else if(url.pathname==='/api/tasks/1'){state.tasks[0]={...state.tasks[0],...body,enabled:false};data=state.tasks[0];}
 else {res.writeHead(404).end(JSON.stringify({detail:'Unexpected fixture route'}));return;}
 if(gate)await gate.promise;
 await pause(delay);event.end=Date.now();
 res.setHeader('Content-Type','application/json');res.end(JSON.stringify(data));
});

module.exports={configure(data){customRecords=data.records;campaigns=data.campaigns||[];guards=data.guards||[];},server,state,calls,holdNext,failNext,delay,get ai(){return ai},get feed(){return feed}};
