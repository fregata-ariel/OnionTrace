// Headless DOM behavior checks. This does not verify browser CSS layout.
const {JSDOM,VirtualConsole}=require('jsdom');
const fs=require('fs'),path=require('path'),assert=require('assert');
const html=path.resolve(process.argv[2]||'out/onion-demo.html');
const qa=path.resolve(process.argv[3]||'out/qa-dom');fs.mkdirSync(qa,{recursive:true});
const errors=[],consoleProxy=new VirtualConsole();consoleProxy.on('jsdomError',e=>errors.push(e.message));
const dom=new JSDOM(fs.readFileSync(html,'utf8'),{runScripts:'dangerously',virtualConsole:consoleProxy});
const w=dom.window,d=w.document,$=id=>d.getElementById(id);
function click(e){e.dispatchEvent(new w.MouseEvent('click',{bubbles:true}));}
function change(e){e.dispatchEvent(new w.Event('change',{bubbles:true}));}
function value(){return d.querySelector('#stats .value').textContent;}
function saveGraph(name){
 const g=d.querySelector('svg.graph').cloneNode(true);g.setAttribute('xmlns','http://www.w3.org/2000/svg');
 const style=d.createElementNS('http://www.w3.org/2000/svg','style');
 style.textContent='.node rect{fill:#f4f7f7;stroke:#b5c5cb;stroke-width:1.2}.node.observed rect{fill:#def3f7;stroke:#077d9d;stroke-width:2}.node.inferred rect{fill:#fff4dd;stroke:#c99535}.node.pruned{opacity:.35}.node text{font-size:12px;fill:#172e39;font-family:Arial,sans-serif}.edge{stroke:#a7bac2;fill:none;stroke-width:1.3;stroke-dasharray:5 4}.edge.inferred{stroke:#bd8a28;stroke-width:2}';g.prepend(style);
 const data=g.outerHTML;fs.writeFileSync(path.join(qa,name+'.svg'),data);
 try{const {Resvg}=require('@resvg/resvg-js');fs.writeFileSync(path.join(qa,name+'.png'),new Resvg(data,{background:'white',fitTo:{mode:'width',value:1000}}).render().asPng());}catch(e){if(e.code!=='MODULE_NOT_FOUND')throw e;}
}
(async()=>{
 assert.strictEqual(d.querySelectorAll('.node').length,8);assert.strictEqual(value(),'3');saveGraph('stage-0');
 click($('stages').children[1]);assert.ok(['1','2'].includes(value()));
 click($('stages').children[2]);assert.strictEqual(value(),'1');saveGraph('stage-2');
 click(d.querySelectorAll('.node')[2]);assert.ok($('details').textContent.length>20);
 $('cursor').value=0;$('cursor').dispatchEvent(new w.Event('input',{bubbles:true}));
 assert.strictEqual(d.querySelectorAll('.node.observed').length,0);assert.strictEqual(d.querySelectorAll('.node.inferred').length,0);
 assert.ok($('recommendation').textContent.includes('再生途中'));
 click($('play'));await new Promise(resolve=>setTimeout(resolve,520));click($('play'));assert.ok(Number($('cursor').value)>=1);click($('end'));
 for(let i=1;i<$('mode').options.length;i++){
  $('mode').selectedIndex=i;change($('mode'));assert.strictEqual(d.querySelectorAll('.node.observed').length,0);assert.strictEqual(d.querySelectorAll('.node.inferred').length,0);assert.ok(d.querySelectorAll('.node').length>1);
 }
 if($('mode').options.length>1)saveGraph('oai-static');
 $('mode').selectedIndex=0;change($('mode'));assert.strictEqual(value(),'1');
 assert.strictEqual($('history').children.length,3);assert.deepStrictEqual(errors,[]);
 const result={passed:true,checks:['stage switching','3-to-1 path refinement display','block selection','prefix no future inference','play/pause','history rows','no script errors'],limitation:'DOM behavior and generated SVG only; full browser layout not tested'};
 fs.writeFileSync(path.join(qa,'result.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result,null,2));dom.window.close();
})().catch(e=>{console.error(e);dom.window.close();process.exit(1);});
