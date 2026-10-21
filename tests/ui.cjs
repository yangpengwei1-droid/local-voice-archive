// Actual page script + real isolated backend. Synthetic native event payloads, no audio/GUI.
const fs=require('fs'),vm=require('vm'),assert=require('assert'),crypto=require('crypto');
const script=fs.readFileSync('web/r3.html','utf8').split('<script>')[1].split('</script>')[0];
const nodes=new Map(),calls=[],sent=[];let pending=0,injectOutside=false,injectFailure=false;
function el(){return {value:'',textContent:'',checked:false,disabled:false,hidden:false,children:[],style:{},append(x){this.children.push(x)},replaceChildren(...x){this.children=x},click(){return this.onclick?.()}}}
const $=id=>{if(!nodes.has(id))nodes.set(id,el());return nodes.get(id)};
const ctx={localStorage:{getItem:()=>null,setItem:()=>{}},console,AbortController,setTimeout,clearTimeout,crypto:{randomUUID:()=>crypto.randomUUID()},document:{getElementById:$,createElement:el},confirm:()=>{throw Error('Unexpected discard prompt')},window:{webkit:{messageHandlers:{voice:{postMessage:x=>sent.push(x)}}}},fetch:async(url,opt)=>{pending++;try{const body=opt?.body?JSON.parse(opt.body):{};if(url==='/api/search'&&injectFailure){injectFailure=false;throw Error('injected transport failure')}const res=await fetch(process.env.LOCAL_VOICE_ARCHIVE_TEST_BASE+url,opt);let d=await res.json();if(url==='/api/search'&&injectOutside){d.cross_scope_count=1;d.cross_scope_candidates=[{row:9999,project:'其他项目',title:'不应展示的酒店',supplier:'别的公司'}];injectOutside=false}calls.push({url,body,reply:d});return {ok:res.ok,headers:res.headers,json:async()=>d}}finally{pending--}}};
vm.createContext(ctx);vm.runInContext(script,ctx);const run=s=>vm.runInContext(s,ctx);
const pause=()=>new Promise(r=>setTimeout(r,10));async function settle(){for(let i=0;i<1000;i++){await pause();if(!pending&&!run('busy')){await pause();if(!pending&&!run('busy'))return}}throw Error('client timeout')}
let eventCount=0;async function native(text,extra={}){eventCount++;await ctx.window.voiceEvent({type:'final',epoch:run('epoch'),source:'live_microphone',text,recognition_final:false,finish_reason:'empty_final_after_partial',...extra});await settle()}
const searches=()=>calls.filter(c=>c.url==='/api/search');const ids=()=>[...run('baseScope||[]')];
(async()=>{await settle();const start=Date.now();
async function query(q){$('query').value=q;await $('search').onclick();await settle();return searches().at(-1).reply}
async function back(){await $('searchBack').onclick();await settle()}
await query('查找第五届星桥创意节');assert.equal(run('baseScope.length'),5);
await query('安保服务');assert.equal(run('baseScope.length'),4);const four=ids();
await query('完全错误火星');assert.equal(run('baseScope'),null);await back();assert.deepEqual(ids(),four);assert.equal($('query').value,'安保服务');
await query('酒店驻地');assert.deepEqual(Array.from(ids()),[3]);await native('回退上一句',{recognition_final:true});assert.deepEqual(ids(),four);
await run('select(3)');await settle();$('G').value='已询价';run("dirty.add('G')");const n=run('searchHistory.length'),epochBefore=run('epoch'),before=calls.length;await back();assert.equal(calls.length,before);assert.equal(run('epoch'),epochBefore);assert.equal(run('selected'),3);assert(run("dirty.has('G')"));assert.equal(run('searchHistory.length'),n);
$('query').value=' ';await $('search').onclick();await settle();assert.equal(calls.length,before);assert.equal(run('selected'),3);run('dirty.clear()');
await query('酒店驻地');await back();assert.equal(run('selected'),3);assert.equal(run('current.record.row'),3);
await query('查找错词供应');const related=searches().at(-1).reply;assert.equal(related.count,0);assert(related.related_results.some(r=>r.row===7&&r.matched_fragments.includes('供应')&&r.unmatched_fragments.includes('错词')));assert.equal(run('selected'),null);
await query('完全错误火星');await back();assert(ids().includes(7));
await query('查找第五届星桥创意节');await query('安保服务');const raceScope=ids(),oldFetch=ctx.fetch;let release,intercepted=false;const gate=new Promise(r=>release=r);ctx.fetch=async(url,opt)=>{if(url==='/api/search'&&!intercepted){intercepted=true;await gate}return oldFetch(url,opt)};
$('query').value='完全错误火星';const pendingSearch=$('search').onclick();await pause();await $('searchBack').onclick();assert.match($('message').textContent,/正在处理/);release();await pendingSearch;await settle();ctx.fetch=oldFetch;const stale=run('epoch');await back();assert.deepEqual(ids(),raceScope);await ctx.window.voiceEvent({type:'final',source:'live_microphone',text:'完全错误火星',recognition_final:true,epoch:stale});await settle();assert.deepEqual(ids(),raceScope);
await $('next').onclick();await settle();assert.equal(run('searchHistory.length'),0);assert.equal(run('baseScope'),null);
const writes=calls.filter(c=>['/api/fields','/api/register','/api/confirm_voice_preview','/api/undo'].includes(c.url));assert.equal(writes.length,0);console.log(JSON.stringify({evidence:'fictional workbook; actual page script in simulated DOM; no GUI or audio',searches:searches().length,elapsed_ms:Date.now()-start,business_writes:writes.length}));
})().catch(e=>{console.error(e);process.exitCode=1});
