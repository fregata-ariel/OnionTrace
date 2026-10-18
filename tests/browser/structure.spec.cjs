const {test,expect}=require('@playwright/test');
const path=require('path');
const {pathToFileURL}=require('url');
const output=path.resolve(process.env.ONION_OUTPUT||'out');
const url=name=>pathToFileURL(path.join(output,name)).href;
let errors,requests;
test.beforeEach(async({page,context})=>{
  errors=[];requests=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(/^https?:/.test(r.url()))requests.push(r.url());});
  await context.setOffline(true);
  await page.goto(url('structure-demo.html'));
});
test.afterEach(async()=>{expect(errors).toEqual([]);expect(requests).toEqual([]);});
async function structure(page,target='nr-softmodem'){
  await page.locator('#mode').selectOption({label:`構造: ${target}（静的）`});
  await expect(page.locator('#structure-tree .pick')).toHaveCount(1);
  await page.getByRole('button',{name:'main を展開・折り畳み',exact:true}).click();
  await page.getByRole('button',{name:'initialize を展開・折り畳み',exact:true}).click();
}
test('both main roots progressively expose thread and queue CFGs with static evidence',async({page})=>{
  for(const target of ['nr-softmodem','nr-uesoftmodem']){
    await structure(page,target);
    await page.locator('#structure-tree .pick[data-kind="thread_role"]').first().click();
    await page.locator('#structure-references button').filter({hasText:'worker / thread_start'}).click();
    await expect(page.locator('#graph-title')).toHaveText('worker / 静的CFG');
    await expect(page.locator('#structure-evidence')).toContainText('conditions');
    await expect(page.locator('#structure-evidence')).toContainText('content_digest');
    await expect(page.locator('#structure-evidence')).toContainText('enabled');
    await page.locator('#structure-tree .pick[data-kind="queue"]').first().click();
    await expect(page.locator('#structure-references button')).not.toHaveCount(0);
    await page.locator('#structure-references button').first().click();
    await expect(page.locator('#graph .node')).not.toHaveCount(0);
    await expect(page.locator('#context')).toContainText('実行証跡なし');
    await expect(page.locator('#stats')).toContainText('なし');
    await expect(page.locator('.node.observed,.node.inferred')).toHaveCount(0);
    await expect(page.locator('#job-summary')).toContainText('静的候補');
  }
});
test('callback targets and unresolved endpoints remain inspectable',async({page})=>{
  await structure(page);
  await page.locator('#structure-tree .pick[data-kind="callback"]').filter({hasText:'process'}).first().click();
  await page.locator('#structure-references button').filter({hasText:'process / callback_target'}).click();
  await expect(page.locator('#graph-title')).toHaveText('process / 静的CFG');
  await page.locator('#structure-tree .pick[data-kind="unresolved"]').first().click();
  await expect(page.locator('#structure-selection')).toContainText('未解決');
  await expect(page.locator('#structure-evidence')).toContainText('reason');
  await expect(page.locator('.node.observed,.node.inferred')).toHaveCount(0);
});
test('consumer functions expose their incoming Queue without a producer path',async({page})=>{
  await page.goto(url('structure-only.html'));
  await page.evaluate(()=>{
    const v=DATA.execution_views[0];
    v.entities.push({id:'consumer-only-queue',kind:'queue',label:'consumer-only Queue'});
    v.links.unshift({...v.links[0],id:'consumer-only-dequeue',kind:'dequeues',
      source:'consumer-only-queue',target:v.roots[0],resolution:'candidate'});
  });
  await page.getByRole('button',{name:'main を展開・折り畳み',exact:true}).click();
  const queue=page.locator('#structure-tree .pick[data-entity="consumer-only-queue"]');
  await expect(queue).toContainText('取得 ←');await queue.click();
  await expect(page.locator('#structure-evidence')).toContainText('consumer-only-dequeue');
  await expect(page.locator('.node.observed,.node.inferred')).toHaveCount(0);
});
test('opening a wide CFG brings its entry into the graph viewport',async({page})=>{
  await page.goto(url('structure-only.html'));
  await page.evaluate(()=>{
    const v=DATA.execution_views[0],cfg=v.cfgs[v.roots[0]];
    cfg.nodes=Array.from({length:22},(_,i)=>({id:'wide/'+i,clang_id:'B'+i,entry:i===0,exit:i===21,sites:[],source_block:{elements:[]}}));
    cfg.edges=cfg.nodes.slice(1,21).flatMap(n=>[{source:'wide/0',target:n.id},{source:n.id,target:'wide/21'}]);
  });
  await page.getByRole('button',{name:'CFGを開く: main',exact:true}).click();
  const visible=await page.evaluate(()=>{
    const area=document.getElementById('graph').getBoundingClientRect(),entry=document.querySelector('#graph .node').getBoundingClientRect();
    return entry.left>=area.left-2&&entry.right<=area.right+2;
  });
  expect(visible).toBe(true);
});
test('collapse and target switches retain selected CFG, block, reference and runtime cursor',async({page})=>{
  await page.locator('#stages button').nth(2).click();
  await page.locator('#job').selectOption('1');
  await page.locator('#cursor').evaluate(e=>{e.value=10;e.dispatchEvent(new Event('input'));});
  await page.locator('#graph .node').nth(2).click();
  const fixtureBlock=await page.locator('#graph .node.selected').getAttribute('aria-label');
  await structure(page);
  await page.locator('#structure-tree .pick[data-kind="thread_role"]').first().click();
  await page.locator('#structure-references button').filter({hasText:'worker / thread_start'}).click();
  await page.locator('#graph .node').nth(2).click();
  const block=await page.locator('#graph .node.selected').getAttribute('aria-label');
  await page.locator('#collapse-structure').click();
  await expect(page.locator('#structure-tree .pick')).toHaveCount(1);
  await expect(page.locator('#graph-title')).toHaveText('worker / 静的CFG');
  await expect(page.locator('#graph .node.selected')).toHaveAttribute('aria-label',block);
  await page.locator('#mode').selectOption('structure:1');
  await page.locator('#mode').selectOption('structure:0');
  await expect(page.locator('#graph .node.selected')).toHaveAttribute('aria-label',block);
  await expect(page.locator('#selection-context')).toContainText('thread_start');
  await page.locator('#return-runtime').click();
  await expect(page.locator('#job')).toHaveValue('1');
  await expect(page.locator('#cursor')).toHaveValue('10');
  await expect(page.locator('#graph .node.selected')).toHaveAttribute('aria-label',fixtureBlock);
  await expect(page.locator('.node.inferred')).toHaveCount(0);
});
test('runtime skeleton, CFG and timeline share the selected job and observed site',async({page})=>{
  await page.locator('#stages button').nth(2).click();
  await page.locator('#job').selectOption('1');
  const anchor=page.getByRole('button',{name:'直接観測: work.begin / job 1',exact:true});
  const index=Number(await anchor.getAttribute('data-event'));
  await anchor.click();
  await expect(page.locator('#cursor')).toHaveValue(String(index+1));
  await expect(page.locator('#graph .node.selected')).toContainText('work.begin');
  await expect(page.locator('#timeline .tick.focus')).toHaveCount(1);
  await expect(page.locator('#job')).toHaveValue('1');
  await expect(page.locator('.node.inferred')).toHaveCount(0);
});
test('standalone structure is offline, keyboard accessible and fits a narrow viewport',async({page},testInfo)=>{
  await page.goto(url('structure-only.html'));
  await page.setViewportSize({width:390,height:844});
  const toggle=page.getByRole('button',{name:'main を展開・折り畳み',exact:true});
  await toggle.focus();await page.keyboard.press('Enter');
  await page.getByRole('button',{name:'initialize を展開・折り畳み',exact:true}).click();
  await page.locator('#structure-tree .pick[data-kind="thread_role"]').first().click();
  await page.locator('#structure-references button').filter({hasText:'worker / thread_start'}).click();
  await expect(page.locator('#play')).not.toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(392);
  await expect(page.locator('#graph .node')).not.toHaveCount(0);
  await page.screenshot({path:testInfo.outputPath('structure-mobile.png'),fullPage:true});
  await page.setViewportSize({width:1440,height:1100});
  await page.screenshot({path:testInfo.outputPath('structure-desktop.png'),fullPage:true});
});
for(const selection of ['click','Enter','Space'])test(`large branches and CFGs retain paging and selection with ${selection}`,async({page})=>{
  await page.goto(url('structure-only.html'));
  await page.evaluate(()=>{
    const v=DATA.execution_views[0],root=v.roots[0],sample=v.links[0];
    for(let i=0;i<330;i++){
      const id='large/function/'+i;
      v.entities.push({id,kind:'function',label:'extra '+i});
      v.links.push({...sample,id:'large/link/'+i,source:root,target:id});
    }
    v.links.push({...sample,id:'large/cycle',source:root,target:root});
    const ref=v.references[0];
    for(let i=0;i<80;i++)v.references.push({...ref,id:'large/ref/'+i,origin_id:root,function_id:root,category:'large'});
    const cfg=v.cfgs[root];
    cfg.nodes=Array.from({length:125},(_,i)=>({id:'large/block/'+i,clang_id:'B'+i,entry:i===0,exit:i===124,sites:[],source_block:{elements:[],block_index:i}}));
    cfg.edges=cfg.nodes.slice(1).map((n,i)=>({source:cfg.nodes[i].id,target:n.id}));
  });
  await page.getByRole('button',{name:'main を展開・折り畳み',exact:true}).click();
  expect(await page.locator('#structure-tree .pick').count()).toBeLessThanOrEqual(17);
  const more=page.getByRole('button',{name:/^次の接続/});
  await expect(more).toBeVisible();
  while(await more.count())await more.click();
  await expect(page.locator('#structure-tree')).toContainText('循環参照');
  await expect(page.locator('#structure-references button')).toHaveCount(18);
  await page.getByRole('button',{name:/^参照をさらに表示/}).click();
  await expect(page.locator('#structure-references button')).toHaveCount(34);
  await page.getByRole('button',{name:'CFGを開く: main',exact:true}).click();
  async function selectBlock(index){
    const block=page.locator(`#graph .node[aria-label="B${index}"]`);
    if(selection==='click')await block.click();
    else{await block.focus();await block.press(selection);}
    await expect(page.locator('#graph .node.selected')).toHaveAttribute('aria-label',`B${index}`);
    await expect(page.locator('#details')).toContainText(`"block_index": ${index}`);
  }
  await expect(page.locator('#graph .node')).toHaveCount(60);
  await selectBlock(5);
  await expect(page.locator('#cfg-more')).toHaveCount(1);
  await page.locator('#cfg-more').click();
  await expect(page.locator('#graph .node')).toHaveCount(120);
  await expect(page.locator('#graph .node.selected')).toHaveAttribute('aria-label','B5');
  await expect(page.locator('#details')).toContainText('"block_index": 5');
  await selectBlock(80);
  await expect(page.locator('#cfg-more')).toHaveCount(1);
  await page.locator('#cfg-more').click();
  await expect(page.locator('#graph .node')).toHaveCount(125);
  await expect(page.locator('#graph .node.selected')).toHaveAttribute('aria-label','B80');
  await expect(page.locator('#details')).toContainText('"block_index": 80');
  await expect(page.locator('#cfg-more')).toHaveCount(0);
  await selectBlock(124);
  await expect(page.locator('#cfg-more')).toHaveCount(0);
});
