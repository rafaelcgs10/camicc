"""Parser for Adobe DNG Camera Profile (.dcp) files.

DCP files are TIFF-IFD containers (magic 0x4352 "CR" instead of 42) holding
the DNG 1.4 color profile tags. Only the tags relevant to color rendering are
extracted; unknown tags are ignored.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field


TAGS = {
    0xC614: 'UniqueCameraModel',
    0xC621: 'ColorMatrix1',
    0xC622: 'ColorMatrix2',
    0xC65A: 'CalibrationIlluminant1',
    0xC65B: 'CalibrationIlluminant2',
    0xC6F8: 'ProfileName',
    0xC6F9: 'ProfileHueSatMapDims',
    0xC6FA: 'ProfileHueSatMapData1',
    0xC6FB: 'ProfileHueSatMapData2',
    0xC6FC: 'ProfileToneCurve',
    0xC6FD: 'ProfileEmbedPolicy',
    0xC6FE: 'ProfileCopyright',
    0xC714: 'ForwardMatrix1',
    0xC715: 'ForwardMatrix2',
    0xC725: 'ProfileLookTableDims',
    0xC726: 'ProfileLookTableData',
    0xC7A3: 'ProfileHueSatMapEncoding',
    0xC7A4: 'ProfileLookTableEncoding',
    0xC7A5: 'BaselineExposureOffset',
    0xC7A6: 'DefaultBlackRender',
}

ILLUMINANTS = {17: 'StdA', 20: 'D55', 21: 'D65', 23: 'D50'}

# TIFF data types: id -> (struct code, size)
TYPES = {
    1: ('B', 1), 2: ('c', 1), 3: ('H', 2), 4: ('I', 4),
    5: ('II', 8), 6: ('b', 1), 7: ('B', 1), 8: ('h', 2),
    9: ('i', 4), 10: ('ii', 8), 11: ('f', 4), 12: ('d', 8),
}


@dataclass
class DCPProfile:
    unique_camera_model: str = ''
    profile_name: str = ''
    copyright: str = ''
    calibration_illuminant_1: int | None = None
    calibration_illuminant_2: int | None = None
    color_matrix_1: list | None = None
    color_matrix_2: list | None = None
    forward_matrix_1: list | None = None
    forward_matrix_2: list | None = None
    hue_sat_map_dims: list | None = None
    hue_sat_map_1: list | None = None          # flat [dh, ds, dv, ...]
    hue_sat_map_2: list | None = None
    hue_sat_map_srgb: bool = False
    look_table_dims: list | None = None
    look_table: list | None = None
    look_table_srgb: bool = False
    tone_curve: list | None = None             # flat [x0, y0, x1, y1, ...]
    baseline_exposure_offset: float = 0.0
    raw_tags: dict = field(default_factory=dict)


def _read_values(data, off, typ, count, unpack, inline):
    code, size = TYPES[typ]
    total = size * count
    if total <= 4:
        raw = inline[:total]
    else:
        ptr = unpack('I', inline)[0]
        raw = data[ptr:ptr + total]
    if typ == 2:  # ASCII
        return raw.split(b'\0')[0].decode('utf-8', errors='replace')
    if typ in (5, 10):  # rationals
        vals = unpack(f'{count * 2}{code[0]}', raw)
        return [vals[i] / vals[i + 1] if vals[i + 1] else 0.0
                for i in range(0, len(vals), 2)]
    vals = unpack(f'{count}{code}', raw)
    return list(vals) if count > 1 else vals[0]


def parse_dcp(path: str) -> DCPProfile:
    data = open(path, 'rb').read()
    if data[:2] == b'II':
        end = '<'
    elif data[:2] == b'MM':
        end = '>'
    else:
        raise ValueError(f'{path}: not a TIFF/DCP file')
    unpack = lambda fmt, b: struct.unpack(end + fmt, b[:struct.calcsize(end + fmt)])
    magic = unpack('H', data[2:4])[0]
    if magic not in (0x4352, 42):
        raise ValueError(f'{path}: bad magic 0x{magic:04x}, expected 0x4352 (DCP)')
    ifd_off = unpack('I', data[4:8])[0]
    n = unpack('H', data[ifd_off:ifd_off + 2])[0]
    tags = {}
    for i in range(n):
        e = ifd_off + 2 + 12 * i
        tag, typ, count = unpack('HHI', data[e:e + 8])
        if tag in TAGS and typ in TYPES:
            tags[TAGS[tag]] = _read_values(data, e, typ, count, unpack, data[e + 8:e + 12])

    p = DCPProfile(raw_tags=tags)
    p.unique_camera_model = tags.get('UniqueCameraModel', '')
    p.profile_name = tags.get('ProfileName', '')
    p.copyright = tags.get('ProfileCopyright', '')
    p.calibration_illuminant_1 = tags.get('CalibrationIlluminant1')
    p.calibration_illuminant_2 = tags.get('CalibrationIlluminant2')
    for k_src, k_dst in [('ColorMatrix1', 'color_matrix_1'), ('ColorMatrix2', 'color_matrix_2'),
                         ('ForwardMatrix1', 'forward_matrix_1'), ('ForwardMatrix2', 'forward_matrix_2')]:
        v = tags.get(k_src)
        if v is not None:
            setattr(p, k_dst, [v[0:3], v[3:6], v[6:9]])
    p.hue_sat_map_dims = tags.get('ProfileHueSatMapDims')
    p.hue_sat_map_1 = tags.get('ProfileHueSatMapData1')
    p.hue_sat_map_2 = tags.get('ProfileHueSatMapData2')
    p.hue_sat_map_srgb = tags.get('ProfileHueSatMapEncoding', 0) == 1
    p.look_table_dims = tags.get('ProfileLookTableDims')
    p.look_table = tags.get('ProfileLookTableData')
    p.look_table_srgb = tags.get('ProfileLookTableEncoding', 0) == 1
    tc = tags.get('ProfileToneCurve')
    if tc is not None:
        p.tone_curve = tc
    blev = tags.get('BaselineExposureOffset', 0.0)
    p.baseline_exposure_offset = float(blev) if not isinstance(blev, list) else float(blev[0])
    return p
