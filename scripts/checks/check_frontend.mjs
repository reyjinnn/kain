import assert from 'node:assert/strict';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const frontend = resolve(root, 'frontend');
const modules = readdirSync(resolve(frontend, 'src'), { recursive: true })
  .filter(name => name.endsWith('.js')).sort().map(name => resolve(frontend, 'src', name));
const tests = readdirSync(resolve(frontend, 'test'))
  .filter(name => name.endsWith('.cjs')).sort().map(name => resolve(frontend, 'test', name));
assert(modules.length > 0, 'No frontend modules found.');
for (const file of [...modules, ...tests]) {
  const result = spawnSync(process.execPath, ['--check', file], { cwd: root, stdio: 'inherit' });
  if (result.status !== 0) process.exit(result.status || 1);
}
for (const file of modules) {
  for (const [, target] of readFileSync(file, 'utf8').matchAll(/(?:\bfrom\s*|\bimport\s*\(\s*)['"]([^'"]+)['"]/g)) {
    if (target.startsWith('.')) assert(existsSync(resolve(dirname(file), target)), `${file}: missing import ${target}`);
  }
}
const styles = readdirSync(resolve(frontend, 'styles')).filter(name => name.endsWith('.css'))
  .map(name => resolve(frontend, 'styles', name));
for (const file of [resolve(frontend, 'index.html'), ...modules, ...styles]) {
  for (const [, target] of readFileSync(file, 'utf8').matchAll(/["'](\/static\/[^"'?#]+)(?:[?#][^"']*)?["']/g)) {
    assert(existsSync(resolve(frontend, target.slice('/static/'.length))), `${file}: missing asset ${target}`);
  }
}
console.log(`Syntax and paths passed for ${modules.length} frontend modules, ${tests.length} test/support files and ${styles.length} stylesheets.`);
