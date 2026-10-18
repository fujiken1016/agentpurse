import puppeteer from 'puppeteer-core'; import fs from 'node:fs'; import path from 'node:path';
const D = JSON.parse(fs.readFileSync('data.json')); const FPS = 15;
const mode = process.argv[2];  // 'probe' or 'all'
const b = await puppeteer.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless:'new', args:['--allow-file-access-from-files']});
const url = 'file://' + path.resolve('stage.html');
async function mk(){const p = await b.newPage(); await p.setViewport({width:1920,height:1080,deviceScaleFactor:1}); await p.goto(url); await p.evaluate(d=>LOAD(d), D); return p;}
if (mode === 'probe') {
  fs.mkdirSync('probe',{recursive:true}); const p = await mk();
  const pts = JSON.parse(process.argv[3]);   // [[sceneIdx, t],...]
  for (const [i,t] of pts) { await p.evaluate((i,t)=>R(i,t), i, t); await new Promise(r=>setTimeout(r,60)); await p.screenshot({path:`probe/s${i}_${t}.png`}); }
} else {
  fs.mkdirSync('frames',{recursive:true}); const N=1; const W=+process.argv[3], NW=+process.argv[4]; const jobs=[];
  D.scenes.forEach((s,i)=>{ const n=Math.ceil(s.dur*FPS); for(let k=0;k<n;k++) jobs.push([i,k]); });
  // global frame index
  let g=0; const idx=jobs.map(j=>g++);
  const pages=await Promise.all(Array.from({length:N},mk)); let next=0;
  await Promise.all(pages.map(async p=>{ while(true){ const j=next++; if(j>=jobs.length)break; if(j%NW!==W) continue; if(fs.existsSync(`frames/f${String(j).padStart(5,'0')}.jpg`)) continue; const [i,k]=jobs[j];
    await p.evaluate((i,t)=>R(i,t), i, k/FPS); await p.screenshot({path:`frames/f${String(j).padStart(5,'0')}.jpg`, type:'jpeg', quality:93}); } }));
  console.log('frames', jobs.length);
}
await b.close();
