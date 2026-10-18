const assert = require('node:assert/strict');
const { harness } = require('./browser_helpers.cjs');
const fs = require('node:fs'), path = require('node:path');

(async () => harness(8779, async ({ page, idle, errors }) => {
  await page.locator('#demoStart').click();
  await page.locator('#consentNo').click();
  await page.waitForFunction(() => window.__pouch3d);
  await idle();
  const result = await page.evaluate(async () => {
    // Independent tetrahedron fixture, including an embedded PNG texture.
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 2;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#ec5028'; ctx.fillRect(0, 0, 2, 2);
    const png = Uint8Array.from(atob(canvas.toDataURL().split(',')[1]), c => c.charCodeAt(0));
    const vertices = new Float32Array([-1,-1,-1, 1,-1,-1, 0,1,-1, 0,0,1]);
    const indices = new Uint16Array([0,2,1, 0,1,3, 1,2,3, 2,0,3]);
    const uv = new Float32Array([0,0, 1,0, .5,1, .5,.5]);
    const pngOffset = vertices.byteLength + indices.byteLength + uv.byteLength;
    const binary = new Uint8Array(Math.ceil((pngOffset + png.byteLength) / 4) * 4);
    binary.set(new Uint8Array(vertices.buffer), 0);
    binary.set(new Uint8Array(indices.buffer), vertices.byteLength);
    binary.set(new Uint8Array(uv.buffer), vertices.byteLength + indices.byteLength);
    binary.set(png, pngOffset);
    const manifest = {
      asset: { version: '2.0' }, scene: 0, scenes: [{ nodes: [0] }], nodes: [{ mesh: 0 }],
      meshes: [{ primitives: [{ attributes: { POSITION: 0, TEXCOORD_0: 2 }, indices: 1, material: 0 }] }],
      materials: [{ doubleSided: true, pbrMetallicRoughness: { baseColorTexture: { index: 0 }, metallicFactor: 0, roughnessFactor: 1 } }],
      textures: [{ source: 0 }], images: [{ bufferView: 3, mimeType: 'image/png' }],
      buffers: [{ byteLength: binary.byteLength }],
      bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: vertices.byteLength },
        { buffer: 0, byteOffset: vertices.byteLength, byteLength: indices.byteLength },
        { buffer: 0, byteOffset: vertices.byteLength + indices.byteLength, byteLength: uv.byteLength },
        { buffer: 0, byteOffset: pngOffset, byteLength: png.byteLength }],
      accessors: [{ bufferView: 0, componentType: 5126, count: 4, type: 'VEC3', min: [-1,-1,-1], max: [1,1,1] },
        { bufferView: 1, componentType: 5123, count: 12, type: 'SCALAR' },
        { bufferView: 2, componentType: 5126, count: 4, type: 'VEC2' }],
    };
    function glb(json) {
      const encoded = new TextEncoder().encode(JSON.stringify(json));
      const length = Math.ceil(encoded.byteLength / 4) * 4;
      const bytes = new Uint8Array(28 + length + binary.byteLength), view = new DataView(bytes.buffer);
      view.setUint32(0, 0x46546c67, true); view.setUint32(4, 2, true); view.setUint32(8, bytes.byteLength, true);
      view.setUint32(12, length, true); view.setUint32(16, 0x4e4f534a, true);
      bytes.fill(32, 20, 20 + length); bytes.set(encoded, 20);
      view.setUint32(20 + length, binary.byteLength, true); view.setUint32(24 + length, 0x004e4942, true);
      bytes.set(binary, 28 + length); return bytes.buffer;
    }
    const viewer = window.__pouch3d;
    let malformed = false, external = false;
    try { await viewer.loadModel(new ArrayBuffer(32)); } catch { malformed = true; }
    try { await viewer.loadModel(glb({ ...manifest, buffers: [{ byteLength: binary.byteLength, uri: 'https://example.com/private.bin' }] })); } catch { external = true; }
    await viewer.loadModel(glb(manifest));
    viewer.dimensions(true);
    const state = viewer.getState();
    const hidden = document.querySelector('.dimension-label').hidden;
    const painted = document.querySelector('#pouchScene canvas').toDataURL();
    const copy = document.createElement('canvas'), source = document.querySelector('#pouchScene canvas');
    copy.width = source.width; copy.height = source.height;
    const pixels = copy.getContext('2d'); pixels.drawImage(source, 0, 0);
    const rgba = pixels.getImageData(0, 0, copy.width, copy.height).data;
    let redPixels = 0;
    for (let i = 0; i < rgba.length; i += 4)
      if (rgba[i + 3] > 0 && rgba[i] > rgba[i + 2] * 1.5) redPixels++;
    const pending = viewer.loadModel(glb(manifest));
    viewer.dispose();
    let stale = false; try { await pending; } catch (error) { stale = error.name === 'AbortError'; }
    return { malformed, external, state, hidden, painted, redPixels, stale };
  });
  assert.equal(result.malformed, true);
  assert.equal(result.external, true);
  assert.equal(result.state.source, 'image_to_3d');
  assert.equal(result.state.preset, 'threeQuarter');
  assert.equal(result.state.dimensionsShown, false);
  assert.equal(result.hidden, true);
  assert.ok(result.redPixels > 100, 'loaded texture replaces the blue pattern preview');
  const output = path.resolve(__dirname, '../../artifacts/image-to-3d');
  fs.mkdirSync(output, { recursive: true });
  fs.writeFileSync(path.join(output, 'viewer-fixture.png'), Buffer.from(result.painted.split(',')[1], 'base64'));
  assert.equal(result.stale, true, 'disposed viewer must reject late model loading');
  assert.deepEqual(errors, []);
  console.log('PASS textured GLB display, malformed/external-resource rejection, no false dimensions, late-load disposal');
}))().catch(error => { console.error(error); process.exitCode = 1; });
