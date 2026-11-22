/* Only reviewed release artifacts can enable a public download link. */
(function (root) {
  'use strict';
  const REPO = 'qianxiaohuaqi-design/CapsWriter-Offline';
  function validArtifact(manifest, item) {
    if (!manifest || manifest.schema !== 'capswriter.release.v1' || manifest.status !== 'published' || manifest.windows_validated !== true || !/^[0-9a-f]{40}$/.test(manifest.code_sha || '')) return false;
    if (!/^\d+\.\d+\.\d+(?:-[\da-zA-Z.-]+)?$/.test(manifest.version || '')) return false;
    if (!item || item.public_verified !== true || !item.verified_at || item.architecture !== 'x64' || !Number.isSafeInteger(item.size) || item.size <= 0 || !/^[0-9a-f]{64}$/.test(item.sha256 || '') || !['unsigned', 'signed_verified'].includes(item.signature)) return false;
    const suffix = {installer:'Setup.exe', portable:'Portable.zip'}[item.kind];
    if (!suffix) return false;
    const name = `CapsWriter-Offline-${manifest.version}-Windows-x64-${suffix}`;
    if (item.filename !== name) return false;
    try {
      const url = new URL(item.url);
      return url.protocol === 'https:' && url.host === 'github.com' && !url.username && !url.password && !url.search && !url.hash && decodeURIComponent(url.pathname) === `/${REPO}/releases/download/v${manifest.version}/${name}`;
    } catch (_) { return false; }
  }
  function sizeLabel(bytes) { return `${(bytes / 1024 / 1024).toFixed(1)} MiB`; }
  root.CapsWriterRelease = {validArtifact, sizeLabel};
  if (typeof module !== 'undefined' && module.exports) module.exports = root.CapsWriterRelease;
  if (typeof document === 'undefined') return;
  fetch('release-manifest.json', {cache:'no-store', credentials:'omit'}).then(response => {
    if (!response.ok) throw new Error('Unavailable manifest');
    return response.json();
  }).then(manifest => {
    if (!manifest || !Array.isArray(manifest.artifacts)) throw new Error('Invalid manifest');
    let enabled = 0;
    for (const kind of ['installer', 'portable']) {
      const matches = manifest.artifacts.filter(item => item && item.kind === kind);
      if (matches.length !== 1 || !validArtifact(manifest, matches[0])) continue;
      const item = matches[0];
      const anchor = document.getElementById(`download-${kind}`);
      anchor.href = item.url;
      anchor.removeAttribute('aria-disabled');
      anchor.removeAttribute('tabindex');
      anchor.classList.remove('unavailable');
      anchor.textContent = `下载 ${kind === 'installer' ? 'EXE 安装版' : 'Portable ZIP'} · ${sizeLabel(item.size)}`;
      document.getElementById(`${kind}-detail`).textContent = `${item.filename}\nSHA-256：${item.sha256}\n签名：${item.signature === 'signed_verified' ? '已验证数字签名' : '未签名'} · 核验时间：${item.verified_at}`;
      enabled++;
    }
    if (enabled) {
      document.getElementById('release-version').textContent = `${manifest.version} · ${manifest.channel === 'stable' ? '稳定版' : '预览版'}`;
      document.getElementById('release-status').textContent = '以下文件已完成对应 Windows 验收与公开下载核验。请核对版本、SHA-256 和签名状态；升级前安全结束当前任务。';
    }
  }).catch(() => {
    document.getElementById('release-status').textContent = '当前无法取得完整下载核验信息，下载入口保持关闭。请到 GitHub 发行页查看最新记录。';
  });
})(typeof globalThis !== 'undefined' ? globalThis : this);
