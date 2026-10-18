const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const tokens=Object.fromEntries([...fs.readFileSync(path.join(__dirname,'../styles/tokens.css'),'utf8').matchAll(/--([\w-]+):\s*(#[\da-f]{6})/ig)].map(m=>[m[1],m[2]]));
function luminance(hex){const values=[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);return values[0]*.2126+values[1]*.7152+values[2]*.0722;}
for(const [foreground,background] of [['ink','paper'],['ink','surface'],['secondary','paper'],['secondary','sunken'],['muted','paper'],['primary','primary-tint'],['surface','primary'],['ai-ink','ai-tint'],['danger','surface'],['success','surface']]){const a=luminance(tokens[foreground]),b=luminance(tokens[background]),ratio=(Math.max(a,b)+.05)/(Math.min(a,b)+.05);assert.ok(ratio>=4.5,`${foreground}/${background}: ${ratio}`);console.log(`PASS token contrast ${foreground}/${background} ${ratio.toFixed(2)}:1`);}
