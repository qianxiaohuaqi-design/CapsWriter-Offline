const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const release = require('../website/release.js');
const root = path.resolve(__dirname, '../website');
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'release-manifest.json'), 'utf8'));
assert.equal(manifest.status, 'candidate');
assert.equal(manifest.artifacts.length, 0);
assert.equal(release.validArtifact(manifest, {}), false);
const version = '1.0.0-rc.1';
const filename = `CapsWriter-Offline-${version}-Windows-x64-Setup.exe`;
const goodManifest = {schema:'capswriter.release.v1', status:'published', windows_validated:true, code_sha:'a'.repeat(40), version};
const good = {kind:'installer', filename, architecture:'x64', public_verified:true, verified_at:'2026-11-22T00:00:00Z', size:1234, sha256:'1'.repeat(64), signature:'unsigned', url:`https://github.com/qianxiaohuaqi-design/CapsWriter-Offline/releases/download/v${version}/${filename}`};
assert.equal(release.validArtifact(goodManifest, good), true);
for (const change of [{public_verified:false}, {sha256:'bad'}, {size:0}, {size:1.3}, {architecture:'arm64'}, {signature:'trusted'}, {url:'https://github.com.evil.invalid/file.exe'}, {url:good.url+'?extra=1'}, {filename:'other.exe'}]) assert.equal(release.validArtifact(goodManifest, {...good, ...change}), false);
assert.equal(release.validArtifact({...goodManifest, windows_validated:false}, good), false);
assert.equal(release.validArtifact({...goodManifest, code_sha:null}, good), false);
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
for (const match of html.matchAll(/(?:src|href)="([^"]+)"/g)) {
  const target = match[1];
  if (target.startsWith('#')) { if (target.length > 1) assert(html.includes(`id="${target.slice(1)}"`)); continue; }
  if (/^https:/.test(target)) continue;
  assert(fs.existsSync(path.join(root, target)), `Missing local asset ${target}`);
}
assert(html.includes('仓库既有截图'));
assert(html.includes('Windows 实机截图将在验收后替换'));
assert.equal((html.match(/aria-disabled="true"/g)||[]).length, 2);
assert(!html.includes('href="javascript:'));
console.log('Website metadata and local-link checks: PASS');
