const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const frontend = path.resolve(__dirname, '..');
  const source = fs.readFileSync(path.join(frontend, 'src/config/colors.js'), 'utf8');
  const { fabricColors } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
  fabricColors.setPalette(JSON.parse(fs.readFileSync(path.join(frontend, 'src/config/colors.json'), 'utf8')));
  assert.equal(fabricColors.displayName('hitam'), 'black');
  assert.equal(fabricColors.displayName('biru denim'), 'denim blue');
  assert.equal(fabricColors.displayName('blue'), 'denim blue');
  assert.equal(fabricColors.resolve('biru denim').name, 'biru denim');
  assert.equal(fabricColors.resolve('hitam').family, 'black');
  assert.equal(fabricColors.displayName('Ocean Mist'), 'Ocean Mist');
  assert.equal(fabricColors.displayName('Ocean Mist (#53AEC1)'), 'Ocean Mist');
  assert.equal(fabricColors.resolve('Ocean Mist (#53AEC1)').hex, '#53AEC1');
  assert.equal(fabricColors.displayName('biru denim (#123456)'), 'denim blue');
  assert.equal(fabricColors.resolve('biru denim (#123456)').hex, '#123456');
  assert.equal(fabricColors.displayName(null), 'no preference');
  assert.equal(fabricColors.sameFamily('hitam', 'black'), true);
  assert.match(fs.readFileSync(path.join(frontend, 'index.html'), 'utf8'), /<html lang="en">/);
  console.log('PASS English interface language, palette labels, custom names and unchanged color identity');
})().catch(error => { console.error(error); process.exitCode = 1; });
