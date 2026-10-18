"""CPU reconstruction of the supported pouch from its illustration.

This fits a pouch prior to image evidence, rather than pretending a single view
reveals the unseen back or physical depth. Pattern/export geometry is untouched.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import struct
import time
import threading

from PIL import Image, ImageOps

VERSION = 'local-pouch-image-v1'
MAX_BYTES = 12 * 1024 * 1024
_LOCK = threading.Lock()


def available():
    try:
        import cv2
        import numpy
        return True
    except ImportError:
        return False


def reconstruct(content: bytes, back_hex: str):
    import cv2
    import numpy as np

    started = time.monotonic()
    if not content or len(content) > MAX_BYTES:
        raise ValueError('IMAGE_SIZE')
    with Image.open(io.BytesIO(content)) as source:
        if source.width * source.height > 20_000_000 or min(source.size) < 64:
            raise ValueError('IMAGE_SIZE')
        image = ImageOps.exif_transpose(source).convert('RGB')
        image.thumbnail((1024, 1024))
        rgb = np.asarray(image)
    height, width = rgb.shape[:2]
    small = cv2.resize(rgb, (int(width * min(1, 384 / max(width, height))), int(height * min(1, 384 / max(width, height)))))
    sh, sw = small.shape[:2]
    lab = cv2.cvtColor(small, cv2.COLOR_RGB2LAB).astype(np.float32)
    edge = max(2, min(sh, sw) // 24)
    corners = np.concatenate([lab[:edge, :edge].reshape(-1, 3), lab[:edge, -edge:].reshape(-1, 3), lab[-edge:, :edge].reshape(-1, 3), lab[-edge:, -edge:].reshape(-1, 3)])
    background = np.median(corners, axis=0)
    difference = np.linalg.norm(lab - background, axis=2)
    mask = np.where(difference > 18, cv2.GC_PR_FGD, cv2.GC_PR_BGD).astype(np.uint8)
    mask[difference > 65] = cv2.GC_FGD
    mask[:2] = mask[-2:] = cv2.GC_BGD
    mask[:, :2] = mask[:, -2:] = cv2.GC_BGD
    if np.count_nonzero(mask == cv2.GC_FGD) < sh * sw * .02:
        # Light cloth needs a softer foreground seed, with no background forced
        # into the object when the image contains no separable pouch.
        mask[(difference > 30) & (difference < 120)] = cv2.GC_PR_FGD
    if np.count_nonzero(mask == cv2.GC_PR_FGD) + np.count_nonzero(mask == cv2.GC_FGD) < sh * sw * .05:
        raise ValueError('POUCH_NOT_FOUND')
    # GrabCut has process-global RNG state: serialize only this small CPU step.
    with _LOCK:
        cv2.setRNGSeed(7)
        try:
            cv2.grabCut(small, mask, None, np.zeros((1, 65)), np.zeros((1, 65)), 2, cv2.GC_INIT_WITH_MASK)
        except cv2.error as exc:
            raise ValueError('POUCH_NOT_FOUND') from exc
    foreground = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    contours, _ = cv2.findContours(foreground, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise ValueError('POUCH_NOT_FOUND')
    contour = max(contours, key=cv2.contourArea)
    x, y, w, h = cv2.boundingRect(contour)
    # A studio-floor shadow can join the pouch mask and reach the frame edge.
    # For colored fabric, use chromatic evidence to separate that neutral
    # shadow. A genuinely cropped colored object still fails the edge check.
    if x <= 2 or y <= 2 or x + w >= sw - 2 or y + h >= sh - 2:
        chroma = np.linalg.norm(lab[:, :, 1:] - background[1:], axis=2)
        colored = np.where((chroma > 14) & (foreground > 0), 255, 0).astype(np.uint8)
        colored = cv2.morphologyEx(colored, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        color_contours, _ = cv2.findContours(colored, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if color_contours:
            body = max(color_contours, key=cv2.contourArea)
            if cv2.contourArea(body) > .08 * sh * sw:
                contour = body
                x, y, w, h = cv2.boundingRect(contour)
    area = cv2.contourArea(contour)
    if not .08 * sh * sw < area < .92 * sh * sw:
        raise ValueError('POUCH_NOT_FOUND')
    if x <= 2 or y <= 2 or x + w >= sw - 2 or y + h >= sh - 2:
        raise ValueError('POUCH_CROPPED')
    hull = cv2.convexHull(contour)
    quad = None
    for epsilon in np.linspace(.01, .075, 28):
        approximation = cv2.approxPolyDP(hull, epsilon * cv2.arcLength(hull, True), True)
        if len(approximation) == 4:
            quad = approximation[:, 0, :].astype(np.float32)
            break
    if quad is None:
        raise ValueError('POUCH_SHAPE_UNCLEAR')
    sums, differences = quad.sum(axis=1), quad[:, 1] - quad[:, 0]
    quad = np.array([quad[np.argmin(sums)], quad[np.argmin(differences)], quad[np.argmax(sums)], quad[np.argmax(differences)]], np.float32)
    if len({tuple(point) for point in quad}) != 4 or abs(cv2.contourArea(quad)) < area * .65:
        raise ValueError('POUCH_SHAPE_UNCLEAR')
    quad[:, 0] *= width / sw
    quad[:, 1] *= height / sh
    edge_width = (np.linalg.norm(quad[1] - quad[0]) + np.linalg.norm(quad[2] - quad[3])) / 2
    edge_height = (np.linalg.norm(quad[3] - quad[0]) + np.linalg.norm(quad[2] - quad[1])) / 2
    aspect = float(np.clip(edge_height / edge_width, .5, 1.8))
    size = 512
    transform = cv2.getPerspectiveTransform(quad, np.array([[0, 0], [size-1, 0], [size-1, size-1], [0, size-1]], np.float32))
    flat = cv2.warpPerspective(rgb, transform, (size, size), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    # Estimate visible opening from the dark interior-to-front transition.
    luminance = cv2.cvtColor(flat, cv2.COLOR_RGB2GRAY).astype(np.float32)
    profile = cv2.GaussianBlur(luminance[:, size//3:2*size//3].mean(axis=1).reshape(-1, 1), (1, 9), 0).ravel()
    limit = size // 5
    opening_row = int(np.argmax(np.diff(profile[:limit]))) + 1
    opening_evidence = float(profile[min(limit-1, opening_row+4)] - profile[max(0, opening_row-4)])
    opening_fraction = opening_row / size if opening_evidence > 8 else .06
    opening_fraction = float(np.clip(opening_fraction, .025, .18))
    depth = float(np.clip(opening_fraction * aspect * 2.5, .10, .32))
    # Remove the visible interior from the front texture. Every texel still
    # comes from the actual illustration, including hem, stitches, and weave.
    u, v = np.meshgrid(np.linspace(0, 1, size), np.linspace(0, 1, size))
    start = opening_fraction * np.sin(np.pi * u)
    front_texture = cv2.remap(flat, (u * (size-1)).astype(np.float32), ((start + (1-start)*v) * (size-1)).astype(np.float32), cv2.INTER_LINEAR)
    # An unseen back cannot be recovered from one image. Reuse cloth variation
    # from a small visible patch, tinted with the assigned back material.
    patch = front_texture[size//3:2*size//3, size//3:2*size//3]
    variation = cv2.cvtColor(patch, cv2.COLOR_RGB2GRAY).astype(np.float32)
    variation = np.clip(variation / max(float(np.median(variation)), 1), .75, 1.2)
    variation = cv2.resize(variation, (size, size), interpolation=cv2.INTER_LINEAR)
    color = np.array([int(back_hex[i:i+2], 16) for i in (1, 3, 5)], np.float32)
    back_texture = np.clip(variation[:, :, None] * color, 0, 255).astype(np.uint8)
    model = _make_glb(front_texture, back_texture, aspect, depth, np)
    return model, {
        'method': VERSION, 'source': 'local_image_reconstruction',
        'image_sha256': hashlib.sha256(content).hexdigest(),
        'back_estimated': True, 'depth_estimated': True,
        'physical_dimensions': False,
        'source_quad_px': [[round(float(a), 2) for a in point] for point in quad],
        'opening_fraction': round(opening_fraction, 4),
        'vertex_count': 4 * 49 * 33,
        'latency_s': round(time.monotonic() - started, 3),
    }


def _make_glb(front_texture, back_texture, aspect, depth, np):
    binary = bytearray()
    views, accessors, primitives, images = [], [], [], []

    def view(raw):
        while len(binary) % 4:
            binary.append(0)
        index = len(views)
        views.append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': len(raw)})
        binary.extend(raw)
        return index

    def accessor(values, kind, component=5126):
        array = np.asarray(values, dtype='<f4' if component == 5126 else '<u2')
        entry = {'bufferView': view(array.tobytes()), 'componentType': component, 'count': len(array), 'type': kind}
        if kind == 'VEC3':
            entry.update(min=array.min(axis=0).tolist(), max=array.max(axis=0).tolist())
        accessors.append(entry)
        return len(accessors) - 1

    for texture in (front_texture, back_texture):
        output = io.BytesIO()
        Image.fromarray(texture).save(output, format='PNG')
        images.append({'bufferView': view(output.getvalue()), 'mimeType': 'image/png'})
    nx, ny = 48, 32
    for side, material, inside in ((1, 0, False), (-1, 1, False), (1, 0, True), (-1, 1, True)):
        positions, texcoords, shades, indices = [], [], [], []
        for j in range(ny + 1):
            up = j / ny
            for i in range(nx + 1):
                across = i / nx
                bow = max(0, math.sin(math.pi * across)) ** .7
                z = side * (.002 + depth / 2 * bow * math.sin(up * math.pi / 2))
                if inside:
                    z -= side * .0015
                positions.append([across - .5, (up-.5)*aspect - .008*bow*up, z])
                texcoords.append([across if side == 1 else 1-across, 1-up])
                shade = .18 + .7*up**2 if inside else 1.
                shades.append([shade, shade, shade])
        for j in range(ny):
            for i in range(nx):
                a = j*(nx+1)+i
                triangles = [a, a+1, a+nx+2, a, a+nx+2, a+nx+1]
                if (side == -1) != inside:
                    triangles = [triangles[k+t] for k in (0, 3) for t in (0, 2, 1)]
                indices.extend(triangles)
        vertices = np.asarray(positions, np.float32)
        normals = np.zeros_like(vertices)
        faces = np.asarray(indices).reshape(-1, 3)
        for face in faces:
            normal = np.cross(vertices[face[1]]-vertices[face[0]], vertices[face[2]]-vertices[face[0]])
            normals[face] += normal
        normals /= np.maximum(np.linalg.norm(normals, axis=1)[:, None], 1e-9)
        primitives.append({'attributes': {'POSITION': accessor(positions, 'VEC3'), 'NORMAL': accessor(normals, 'VEC3'), 'TEXCOORD_0': accessor(texcoords, 'VEC2'), 'COLOR_0': accessor(shades, 'VEC3')}, 'indices': accessor(indices, 'SCALAR', 5123), 'material': material})
    manifest = {
        'asset': {'version': '2.0', 'generator': 'SISA '+VERSION},
        'scene': 0, 'scenes': [{'nodes': [0]}], 'nodes': [{'mesh': 0}],
        'meshes': [{'primitives': primitives}], 'buffers': [{'byteLength': len(binary)}],
        'bufferViews': views, 'accessors': accessors, 'images': images,
        'textures': [{'source': 0}, {'source': 1}],
        'materials': [{'pbrMetallicRoughness': {'baseColorTexture': {'index': index}, 'metallicFactor': 0, 'roughnessFactor': .92}} for index in (0, 1)],
        'extras': {'source': 'image_reconstruction', 'unseen_back_estimated': True, 'depth_estimated': True, 'physical_dimensions': False},
    }
    encoded = json.dumps(manifest, separators=(',', ':'), allow_nan=False).encode()
    encoded += b' ' * (-len(encoded) % 4)
    binary.extend(b'\0' * (-len(binary) % 4))
    total = 12 + 8 + len(encoded) + 8 + len(binary)
    return struct.pack('<III', 0x46546c67, 2, total) + struct.pack('<II', len(encoded), 0x4e4f534a) + encoded + struct.pack('<II', len(binary), 0x004e4942) + binary
