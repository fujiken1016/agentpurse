import puppeteer from 'puppeteer-core';
const b = await puppeteer.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless:'new'});
async function shot(port,out,tick){
  const p = await b.newPage(); await p.setViewport({width:375,height:900,deviceScaleFactor:3});
  await p.goto('http://localhost:'+port+'/'); await new Promise(r=>setTimeout(r,1500));
  if(tick){ await p.evaluate(()=>{const c=document.querySelector('input[type=checkbox]'); c.click();}); await new Promise(r=>setTimeout(r,300)); }
  await p.screenshot({path:out, fullPage:true}); await p.close();
}
for (const i of [0,1,2,3]) await shot(8800+i,`work/dash_d${i}.png`);
await shot(8809,'work/dash_s0.png'); await shot(8809,'work/dash_s1.png',true);
await b.close();
