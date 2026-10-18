// Static execution workspaces share selection with their local CFG, while the
// existing runtime fixture keeps its own build/run/job/cursor on return.
const executionStates=new Map();
const relationNames={calls:'呼出し',spawns:'スレッド生成',starts:'開始関数',submits:'投入',dequeues:'取得',invokes:'callback呼出し',binds:'callback候補',registration:'callback登録地点'};
const resolutionNames={resolved:'静的接続',candidate:'静的候補',unresolved:'未解決'};
function executionView(){return state.mode.startsWith('structure:')?(DATA.execution_views||[])[Number(state.mode.split(':')[1])]:null;}
function executionState(){const view=executionView();if(!executionStates.has(view.id))executionStates.set(view.id,{expanded:new Set(),pages:new Map(),entity:view.roots[0]||null,link:null,cfg:null,block:null,cfgLimit:60});return executionStates.get(view.id);}
function executionModel(){const view=executionView(),s=executionState(),model=view.cfgs[s.cfg];if(!model)return {nodes:[],edges:[],function:'未選択'};const nodes=model.nodes.slice(0,s.cfgLimit),ids=new Set(nodes.map(n=>n.id));return {...model,nodes,edges:model.edges.filter(e=>ids.has(e.source)&&ids.has(e.target))};}
function entityLabel(view,id){const entity=view.entities.find(e=>e.id===id);return entity?(entity.label||entity.kind+' '+id.slice(-8)):'未解決の端点';}
function executionLinks(view,id){const entity=view.entities.find(e=>e.id===id);return view.links.filter(l=>l.source===id||(l.target===id&&(entity?.kind==='queue'||l.source===null)));}
function badge(resolution){return el('span',resolutionNames[resolution]||resolution,'evidence-badge '+resolution);}
function selectExecution(id,link){const s=executionState();s.entity=id;s.link=link?.id||null;s.refLimit=16;renderExecution();}
function openExecutionCFG(fid,reference=null){const s=executionState();s.reference=reference;if(s.cfg!==fid){s.cfg=fid;s.block=null;s.cfgLimit=60;}state.selected=s.block;renderExecution();$('graph-title').scrollIntoView?.({block:'start',behavior:'smooth'});}
function renderStructureTree(view,s){
 const container=$('structure-tree');container.replaceChildren();let count=0,limited=false;
 function branch(id,link,path,ancestors){
  if(count>=240){limited=true;return null;}count++;
  const wrap=el('div'),row=el('div',undefined,'structure-row'),label=id?entityLabel(view,id):'未解決の端点';
  const children=id?executionLinks(view,id):[],cycle=ancestors.includes(id),key=path;
  if(children.length&&!cycle){const toggle=el('button',s.expanded.has(key)?'▼':'▶','expand');toggle.setAttribute('aria-label',label+' を展開・折り畳み');toggle.setAttribute('aria-expanded',String(s.expanded.has(key)));toggle.onclick=()=>{s.expanded.has(key)?s.expanded.delete(key):s.expanded.add(key);renderExecution();};row.append(toggle);}
  const pick=el('button',(link?relationNames[link.kind]+' → ':'')+label,'pick');pick.dataset.entity=id||'';pick.dataset.kind=view.entities.find(e=>e.id===id)?.kind||'unresolved';
  if(link)pick.append(badge(link.resolution));else pick.append(badge('resolved'));
  if(s.entity===id&&s.link===(link?.id||null))pick.classList.add('active');pick.onclick=()=>selectExecution(id,link);row.append(pick);wrap.append(row);
  if(cycle)wrap.append(el('p','循環参照：この枝での再展開を停止','tiny muted'));
  else if(s.expanded.has(key)){
   const group=el('div',undefined,'structure-children'),limit=s.pages.get(key)||16;
   if(ancestors.length>=24)group.append(el('p','深さの上限です。選択対象のCFGと根拠は確認できます。','tiny warn'));
   else {children.slice(0,limit).forEach(edge=>{const next=edge.source===id?edge.target:edge.source,child=branch(next,edge,key+'/'+edge.id,[...ancestors,id]);if(child)group.append(child);});
    if(children.length>limit){const more=el('button',`さらに表示（残り ${children.length-limit} 接続）`);more.onclick=()=>{s.pages.set(key,limit+16);renderExecution();};group.append(more);}}
   wrap.append(group);
  }return wrap;
 }
 view.roots.forEach((id,i)=>{const root=branch(id,null,'root'+i,[]);if(root)container.append(root);});
 if(!view.roots.length)container.append(el('p','mainが未解決です。抽出失敗・frontierを確認してください。','notice'));
 if(limited)container.append(el('p','表示上限240要素です。他の枝を折り畳むと続きを開けます。','notice'));
}
function renderExecution(){
 const view=executionView(),s=executionState(),entity=view.entities.find(e=>e.id===s.entity),link=view.links.find(l=>l.id===s.link),model=view.cfgs[s.cfg];
 $('app').className='view-static';$('execution-panel').classList.remove('hidden');
 $('context').textContent=`${view.target} / ソース抽出の静的構造。実行証跡なし。静的な接続先の解決は実行確認を意味しません。`;
 $('stats').replaceChildren(metric('対象',view.target),metric('構造要素',view.entities.length),metric('未解決接続',view.links.filter(l=>l.resolution==='unresolved').length),metric('実行観測','なし'));
 renderStructureTree(view,s);
 $('structure-gaps').textContent=`抽出失敗・不完全性 ${view.incompleteness.length} 件 / 探索frontier ${view.frontiers.length} 件。未展開の枝も静的候補として保持します。`;
 $('structure-selection').replaceChildren(el('p',entity?entityLabel(view,entity.id):'未解決の端点','structure-meta'),badge(link?.resolution||(entity?'resolved':'unresolved')));
 const refs=view.references.filter(r=>r.origin_id===s.entity||r.origin_id===s.link),choices=$('structure-references');choices.replaceChildren();
 if(entity?.kind==='function'&&view.cfgs[entity.id]){const button=el('button','CFGを開く: '+entityLabel(view,entity.id));button.onclick=()=>openExecutionCFG(entity.id);choices.append(button);}
 // Preserve reference IDs and relation context even when two choices lead to
 // the same function (e.g. Queue ends or different callback alternatives).
 refs.slice(0,s.refLimit||16).forEach(ref=>{const button=el('button',`CFGを開く: ${entityLabel(view,ref.function_id)} / ${ref.category}`);button.dataset.reference=ref.id;button.onclick=()=>openExecutionCFG(ref.function_id,ref.id);choices.append(button);});
 if(refs.length>(s.refLimit||16)){const more=el('button',`参照をさらに表示（残り ${refs.length-(s.refLimit||16)} 件）`);more.onclick=()=>{s.refLimit=(s.refLimit||16)+16;renderExecution();};choices.append(more);}
 if(!choices.children.length)choices.append(el('p','対応する関数CFGは未解決です。根拠と未解決理由を保持しています。','tiny warn'));
 const selectedRef=view.references.find(r=>r.id===s.reference&&r.function_id===s.cfg);
 $('structure-evidence').textContent=JSON.stringify({structure_id:view.structure_id,build_id:view.build_id,entity,connection:link,reference:selectedRef,related_references:refs,
  cfg_provenance:model?.provenance,cfg_uncertainties:model?.uncertainties,incompleteness:view.incompleteness,frontiers:view.frontiers},null,2);
 $('graph-title').textContent=(model?.function||'未選択')+' / 静的CFG';
 $('job-summary').replaceChildren(el('p','実行証跡なし：静的候補','warn'));
 $('site-list').replaceChildren();$('recommendation').replaceChildren(el('p','CFG表示にはプローブ不要です。計測計画・実行時間は静的情報から生成しません。','tiny muted'));
 $('selection-context').textContent=`選択: ${entity?entityLabel(view,entity.id):'未解決の端点'} / CFG: ${model?.function||'未選択'}${selectedRef?' / '+selectedRef.category:''}。構造の折り畳み・対象切替後も選択を保持します。`;
 if(DATA.stages.length)$('selection-context').textContent+=` 別buildのFixture時間軸は ${current().trace.run_id} / job ${state.job} / ${state.cursor} events の選択を保持しています。対応を推定して重ねません。`;
 $('return-runtime').classList.toggle('hidden',!DATA.stages.length);
 state.selected=s.block;const block=model?.nodes.find(n=>n.id===s.block);$('details').textContent=block?JSON.stringify(block.source_block,null,2):'ブロックを選択すると元のCFG式・range・未解決successorを表示します。';
 renderGraph();if(model&&model.nodes.length>s.cfgLimit){const more=el('button',`CFGをさらに表示（残り ${model.nodes.length-s.cfgLimit} ブロック）`);more.id='cfg-more';more.onclick=()=>{s.cfgLimit+=60;renderExecution();};$('graph').append(more);}
 if(model)$('selection-context').textContent+=` CFG ${Math.min(model.nodes.length,s.cfgLimit)} / ${model.nodes.length} ブロック表示。非同期接続はCFG辺に含めません。`;
}
(DATA.execution_views||[]).forEach((view,i)=>{const option=el('option',`構造: ${view.target}（静的）`);option.value='structure:'+i;$('mode').append(option);});
if(!DATA.stages.length){$('mode').querySelector('option[value="fixture"]').remove();document.querySelector('h1').textContent='mainから処理構造を開く';document.querySelector('header p').textContent='ソース上の静的接続・候補・未解決をたどり、関数内CFGと根拠を確認する';}
$('collapse-structure').onclick=()=>{executionState().expanded.clear();renderExecution();};
$('return-runtime').onclick=()=>{$('mode').value='fixture';$('mode').dispatchEvent(new Event('change'));};
