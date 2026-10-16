const {test,expect}=require('@playwright/test');
const path=require('path');
const {pathToFileURL}=require('url');

const output=path.resolve(process.env.ONION_OUTPUT||'out');
const demo=pathToFileURL(path.join(output,'onion-demo.html')).href;
let errors;

test.beforeEach(async({page,context})=>{
  errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await context.setOffline(true);
  await page.goto(demo);
});

test.afterEach(async()=>expect(errors).toEqual([]));

test('stages refine ambiguity and retain earlier observations',async({page})=>{
  await expect(page.locator('#stats .value').first()).toHaveText('3');
  await expect(page.locator('.node.observed')).toHaveCount(2);
  await page.locator('#stages button').nth(1).click();
  await page.locator('#job').selectOption('0');
  await expect(page.locator('#stats .value').first()).toHaveText('2');
  await page.locator('#job').selectOption('1');
  await expect(page.locator('#stats .value').first()).toHaveText('1');
  await page.locator('#stages button').nth(2).click();
  await expect(page.locator('#stats .value').first()).toHaveText('1');
  await expect(page.locator('#history tr')).toHaveCount(3);
  await page.locator('#stages button').first().click();
  await expect(page.locator('#stats .value').first()).toHaveText('3');
});

test('prefix playback does not infer from future events',async({page})=>{
  await page.locator('#stages button').nth(2).click();
  await page.locator('#cursor').evaluate(element=>{
    element.value=0;element.dispatchEvent(new Event('input',{bubbles:true}));
  });
  await expect(page.locator('.node.observed')).toHaveCount(0);
  await expect(page.locator('.node.inferred')).toHaveCount(0);
  await expect(page.locator('#recommendation')).toContainText('再生途中');
  await page.locator('#play').click();
  await expect.poll(()=>page.locator('#cursor').inputValue().then(Number)).toBeGreaterThanOrEqual(2);
  await page.locator('#play').click();
  await expect(page.locator('#play')).toHaveText('再生');
  await page.locator('#end').click();
  await expect(page.locator('#stats .value').first()).toHaveText('1');
});

test('job and block selection expose the matching evidence',async({page})=>{
  await page.locator('#stages button').nth(2).click();
  await page.locator('#job').selectOption('0');
  await expect(page.locator('#site-list')).toContainText('branch.fast');
  await page.locator('#job').selectOption('1');
  await expect(page.locator('#site-list')).toContainText('retry.ok');
  await expect(page.locator('#site-list')).not.toContainText('branch.fast');
  await page.locator('.node').nth(2).click();
  await expect(page.locator('#details')).toContainText('observe');
});

test('incomplete evidence holds recommendations instead of claiming no probes needed',async({page})=>{
  await page.goto(pathToFileURL(path.join(output,'incomplete-demo.html')).href);
  await page.locator('#stages button').nth(2).click();
  await expect(page.locator('#recommendation')).toContainText('保留');
  await expect(page.locator('#recommendation')).not.toContainText('不要');
  await expect(page.locator('.node.inferred')).toHaveCount(0);
  await expect(page.locator('#job-summary')).toContainText('不完全');
});

test('viewer works offline and screenshots are retained as CI evidence',async({page},testInfo)=>{
  await expect(page.locator('h1')).toContainText('実動作');
  await page.screenshot({path:testInfo.outputPath('stage-0.png'),fullPage:true});
  await page.locator('#stages button').nth(2).click();
  await page.screenshot({path:testInfo.outputPath('stage-2.png'),fullPage:true});
});

test('mobile controls and graph stay within the viewport',async({page},testInfo)=>{
  await page.setViewportSize({width:390,height:844});
  await expect(page.locator('#play')).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(392);
  await page.screenshot({path:testInfo.outputPath('mobile.png'),fullPage:true});
});
