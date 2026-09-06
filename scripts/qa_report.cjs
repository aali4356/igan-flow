// Browser-only acceptance tooling. The pipeline/report do not depend on Node or Playwright.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { pathToFileURL } = require('url');
const cache = process.env.IGAN_CACHE || path.join(process.env.HOME, '.cache/igan-nextflow');
const { chromium } = require(path.join(cache, 'tools/browser-qa/node_modules/playwright'));
const root = path.resolve(__dirname, '..');
const report = path.resolve(process.argv[2] || path.join(root, 'results/report.html'));
const evidence = path.resolve(process.argv[3] || path.join(root, 'results/ci/browser'));
fs.mkdirSync(evidence, {recursive: true});
(async () => {
  const browser = await chromium.launch({...(process.env.IGAN_BROWSER_PATH ? {executablePath: process.env.IGAN_BROWSER_PATH} : {}), headless: true,
    args: ['--disable-background-networking', '--disable-component-update', '--no-first-run']});
  const outputs = [];
  for (const [name, width, height, colorScheme] of [
    ['desktop-light',1440,1100,'light'], ['desktop-dark',1440,1100,'dark'],
    ['mobile-light',390,844,'light'], ['mobile-dark',390,844,'dark']]) {
    const context = await browser.newContext({viewport:{width,height},colorScheme,offline:true});
    const page = await context.newPage();
    const errors=[]; const requests=[];
    page.on('pageerror', e => errors.push(String(e)));
    page.on('request', r => {if (/^https?:/.test(r.url())) requests.push(r.url());});
    await page.goto(pathToFileURL(report).href,{waitUntil:'load'});
    const audit = await page.evaluate(() => ({
      title:document.title,
      images:[...document.images].map(i=>({complete:i.complete,width:i.naturalWidth,height:i.naturalHeight,alt:i.alt})),
      headingCount:document.querySelectorAll('h2').length,
      documentWidth:document.documentElement.scrollWidth,viewport:window.innerWidth,
      text:document.body.innerText.slice(0,1000),
      links:[...document.querySelectorAll('a[href]')].map(a=>a.getAttribute('href'))
    }));
    if(errors.length || requests.length || audit.images.some(i=>!i.complete || !i.width || !i.alt) || audit.headingCount<6 || audit.documentWidth>width+1)
      throw Error(JSON.stringify({name,errors,requests,audit}));
    await page.screenshot({path:path.join(evidence,name+'.png'),fullPage:true});
    await page.screenshot({path:path.join(evidence,name+'-top.png')});
    outputs.push({name,viewport:{width,height},colorScheme,offline:true,status:'PASS',errors,externalRequests:requests,...audit});
    await context.close();
  }
  fs.writeFileSync(path.join(evidence,'qa.json'),JSON.stringify({status:'PASS',browserVersion:browser.version(),report,
    reportSha256:crypto.createHash('sha256').update(fs.readFileSync(report)).digest('hex'),cases:outputs},null,2)+'\n');
  await browser.close();
  console.log('PASS: offline browser QA at desktop/mobile widths in light/dark appearance');
})().catch(e=>{console.error(e);process.exit(1);});
