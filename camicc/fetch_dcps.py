"""Download Adobe DNG Converter and extract its DCP camera profiles.

The official Windows installer (~1.8 GB, from adobe.com) is an Inno Setup
archive, so `innoextract` can unpack it directly — nothing is installed or
executed, no Wine needed. The ~4,400 extracted profiles cover essentially
every camera Adobe supports, including the "Camera Standard/Portrait/..."
replicas of the vendor picture styles.

The profiles are copyrighted by Adobe: they are downloaded at run time for
your own use and must not be committed or redistributed.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request

URL = 'https://www.adobe.com/go/dng_converter_win'
PROFILES = 'commonappdata/Adobe/CameraRaw/CameraProfiles'


def _progress(blocks, block_size, total):
    done = blocks * block_size
    if total > 0:
        pct = min(100, done * 100 // total)
        if pct != _progress.last:
            _progress.last = pct
            print(f'\r  {pct}% of {total // 2**20} MiB', end='', flush=True)


_progress.last = -1


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='camicc-fetch-dcps', description=__doc__.splitlines()[0])
    ap.add_argument('installer', nargs='?', default=None,
                    help='an already-downloaded DNG Converter .exe '
                         '(skips the download)')
    ap.add_argument('-o', '--outdir', default='dcps',
                    help='output directory (default: ./dcps, where the '
                         'other camicc tools look for profiles)')
    a = ap.parse_args(argv)

    if shutil.which('innoextract') is None:
        sys.exit('innoextract not found — install it first (Debian/Ubuntu: '
                 'apt install innoextract; Fedora: dnf install innoextract; '
                 'macOS: brew install innoextract)')

    tmp = tempfile.mkdtemp(prefix='.camicc-fetch.', dir='.')
    try:
        installer = a.installer
        if not installer:
            print('downloading Adobe DNG Converter (about 1.8 GB) from '
                  'adobe.com ...')
            installer = os.path.join(tmp, 'dng.exe')
            urllib.request.urlretrieve(URL, installer, _progress)
            print()
        print('extracting camera profiles (nothing is installed or '
              'executed) ...')
        subprocess.run(['innoextract', '-s', '-I', PROFILES,
                        '-d', os.path.join(tmp, 'x'), installer], check=True)
        src = os.path.join(tmp, 'x', *PROFILES.split('/'))
        if not os.path.isdir(src):
            sys.exit(f'no camera profiles found in {installer} — '
                     'is it really the Adobe DNG Converter installer?')
        os.makedirs(a.outdir, exist_ok=True)
        shutil.copytree(src, a.outdir, dirs_exist_ok=True)
        n = sum(len([f for f in files if f.lower().endswith('.dcp')])
                for _, _, files in os.walk(a.outdir))
        print(f'{n} DCP profiles in {a.outdir}/ '
              '(Camera/<model>/ and Adobe Standard/)')
        print('NOTE: the profiles are copyrighted by Adobe - for your own '
              'use only, do not commit or redistribute them.')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
