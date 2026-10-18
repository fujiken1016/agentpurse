import puppeteer from 'puppeteer-core'; import path from 'node:path';
const b = await puppeteer.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless:'new'});
const p = await b.newPage(); await p.setViewport({width:1920,height:1080,deviceScaleFactor:3});
await p.goto('file://'+path.resolve('../docs/sandbox_evidence.html')); await new Promise(r=>setTimeout(r,800));
await p.screenshot({path:'work/sbx_evidence_hi.png'}); await b.close();
