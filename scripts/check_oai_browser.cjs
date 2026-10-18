// Verify the actual OAI HTML in Chromium, without executing either softmodem.
const {chromium}=require('@playwright/test');
const fs=require('fs'),path=require('path'),{pathToFileURL}=require('url');
const root=path.resolve(process.argv[2]||'out/oai'),out=path.join(root,'browser');
fs.mkdirSync(out,{recursive:true});
function inspectAndOpen({index,kind}) {
 const view=DATA.execution_views[index],d=document,$=id=>d.getElementById(id);
 const check=(ok,message)=>{if(!ok)throw Error(message);};
 $('mode').value='structure:'+index;$('mode').dispatchEvent(new Event('change',{bubbles:true}));
 check(view.runtime_evidence===false,'static data must not contain runtime evidence');
 const names=r=>view.cfgs[r.function_id]?.function;
 let refs=view.references.filter(r=>kind==='thread'?r.category==='thread_start'&&names(r)===(view.target==='nr-softmodem'?'L1_rx_thread':'UE_thread'):view.entities.some(e=>e.id===r.origin_id&&e.kind==='queue'));
 if(kind!=='thread'){
  const desired=kind==='completion'?'wait_free_rx_tti':view.target==='nr-softmodem'?'rx_func':'actor_thread';
  const category=kind==='completion'?'queue_dequeue':view.target==='nr-softmodem'?'queue_processing_candidate':'processes';
  refs=refs.filter(r=>r.category===category&&names(r)===desired);
  check(refs.length,'audited Queue CFG reference absent: '+desired);
 }
 check(refs.length,'required CFG references absent: '+kind);
 const goals=new Set(refs.map(r=>r.origin_id)),pending=view.roots.map((id,i)=>({id,root:i,path:[]})),seen=new Set();
 let route;
 for(let i=0;i<pending.length;i++){
  const row=pending[i];if(seen.has(row.id))continue;seen.add(row.id);
  if(goals.has(row.id)){route=row;break;}
  for(const edge of executionLinks(view,row.id)){
   const next=edge.source===row.id?edge.target:edge.source;
   if(next&&!seen.has(next))pending.push({id:next,root:row.root,path:[...row.path,{from:row.id,to:next,edge:edge.id}]});
  }
 }
 check(route,'no main-rooted route to '+kind);
 $('collapse-structure').click();
 let selector=`#structure-tree > div:nth-of-type(${route.root+1})`;
 for(const step of route.path){
  let wrap=d.querySelector(selector);check(wrap,'missing route parent');
  const expand=wrap.querySelector(':scope > .structure-row > .expand');
  if(expand?.getAttribute('aria-expanded')==='false')expand.click();
  const edges=executionLinks(view,step.from),index=edges.findIndex(e=>e.id===step.edge);
  let visibleIndex;
  for(let tries=0;;tries++){
   wrap=d.querySelector(selector);const group=wrap.querySelector(':scope > .structure-children');check(group,'missing child group');
   const rows=Array.from(group.querySelectorAll(':scope > div'));
   visibleIndex=rows.findIndex(r=>r.querySelector(':scope > .structure-row > .pick').dataset.relation===step.edge);
   if(visibleIndex>=0)break;
   const first=rows[0]?.querySelector(':scope > .structure-row > .pick').dataset.relation;
   const firstIndex=edges.findIndex(e=>e.id===first);
   const label=index<firstIndex?'前の接続':'次の接続';
   const move=Array.from(group.children).find(e=>e.tagName==='BUTTON'&&e.textContent.startsWith(label));
   check(move&&tries<100,'required route hidden by pagination');move.click();
   check(d.querySelector(selector).querySelector(':scope > .structure-children > div > .structure-row > .pick')?.dataset.relation!==first,'tree budget prevents required route');
  }
  selector+=` > .structure-children > div:nth-of-type(${visibleIndex+1})`;
  check(d.querySelector(selector)?.querySelector(':scope > .structure-row > .pick').dataset.entity===step.to,'wrong route endpoint');
 }
 d.querySelector(selector).querySelector(':scope > .structure-row > .pick').click();
 const ref=refs.find(r=>r.origin_id===route.id);
 for(let tries=0;;tries++){
  const button=Array.from($('structure-references').querySelectorAll('button')).find(e=>e.dataset.reference===ref.id);
  if(button){button.click();break;}
  const more=Array.from($('structure-references').querySelectorAll('button')).find(e=>e.textContent.startsWith('参照をさらに表示'));
  check(more&&tries<100,'required CFG reference hidden');more.click();
 }
 check($('graph-title').textContent.includes(names(ref)),'selected CFG differs from reference');
 check(d.querySelectorAll('.node').length>1,'CFG is empty');
 check(d.querySelectorAll('.node.observed,.node.inferred').length===0,'static CFG was presented as execution');
 check($('context').textContent.includes('実行証跡なし'),'runtime disclaimer absent');
 return {target:view.target,kind,function:names(ref),path_length:route.path.length,nodes:d.querySelectorAll('.node').length,display_scope:view.display_scope&&{displayed:view.display_scope.displayed_functions,extracted:view.display_scope.extracted_functions}};
}
(async()=>{
 let browser;const errors=[],requests=[],result={passed:false,engine:'Chromium',node_version:process.version,results:[],errors,requests,runtime_evidence:false};
 try{
  browser=await chromium.launch({args:['--js-flags=--max-old-space-size=6144']});
  result.browser_version=browser.version();
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(/^https?:/.test(r.url()))requests.push(r.url());});
  await page.route(/^https?:/,route=>route.abort());
  await page.goto(pathToFileURL(path.join(root,'acceptance/structure.html')).href,{waitUntil:'load',timeout:180000});
  const count=await page.evaluate(()=>DATA.execution_views.length);if(count!==2)throw Error('expected both OAI targets');
  for(let index=0;index<count;index++)for(const kind of (index===0?['thread','queue','completion']:['thread','queue'])){
   const row=await page.evaluate(inspectAndOpen,{index,kind});result.results.push(row);
   await page.screenshot({path:path.join(out,row.target+'-'+kind+'.png')});
  }
  if(errors.length||requests.length)throw Error('script error or external network dependency');
  result.passed=true;
 }catch(error){result.failure=String(error.stack||error);process.exitCode=1;}
 finally{
  if(browser)await browser.close();
  fs.writeFileSync(path.join(out,'summary.json'),JSON.stringify(result,null,2)+'\n');
  const runPath=path.join(root,'run.json');
  if(fs.existsSync(runPath)){
   const run=JSON.parse(fs.readFileSync(runPath,'utf8'));
   const row={name:'browser',passed:result.passed,...(!result.passed?{failure:result.failure}:{})};
   const pending=run.stages.findIndex(s=>s.name==='browser');
   if(pending>=0)run.stages[pending]=row;else run.stages.push(row);
   run.browser_verified=result.passed;run.passed=run.semantic_acceptance_verified===true&&result.passed;run.full_extraction_verified=run.passed;
   run.pending_stages=result.passed?[]:['browser'];
   fs.writeFileSync(runPath,JSON.stringify(run,null,2)+'\n');
   fs.writeFileSync(path.join(root,'failures.json'),JSON.stringify(run.stages.filter(s=>!s.passed),null,2)+'\n');
  }
  console.log(JSON.stringify(result,null,2));
 }
})();
