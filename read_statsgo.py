from __future__ import print_function
import glob
import json
import os

import numpy as np
import pandas as pd
import xarray as xr
import yaml

SOIL_URL = 'https://ldas.gsfc.nasa.gov/nldas/NLDASsoils.php'

SOIL_META_FILE = os.path.abspath('soil_meta_data.yml')

with open(SOIL_META_FILE) as f:
    SOIL_META = yaml.safe_load(f.read())

SOIL_FILES = ('COS_RAWL',
              'HYD_RAWL',
              'HYD_CLAP',
              'HYD_COSB',
              'SOILTEXT',
              'STEX_TAB',
              'TXDM1',
              'PCNTS',)

BIN_FILES = ('NLDAS_Mosaic_soilparms.bin',
             'NLDAS_STATSGOpredomsoil.bin',
             'NLDAS_Noah_soilparms.bin',)
SOIL_DIR = os.environ.get('SOIL_DATA', os.path.abspath('nldas_soil_inputs'))
if not os.path.exists(SOIL_DIR):
    os.mkdir(SOIL_DIR)
BIN_FILES = tuple(os.path.join(SOIL_DIR, 'bin', f)
                  for f in BIN_FILES)
parts = SOIL_DIR, 'asc', 'soils', '*{}*'
COS_HYD_FILES = {f: glob.glob(os.path.join(*parts).format(f))
                 for f in SOIL_FILES}

NO_DATA = -9.99

def dataframe_to_rasters(df,
                         col_attrs=None,
                         drop_cols=None, keep_cols=None,
                         attrs=None,
                         new_dim=None,
                         new_dim_values=None):
    arrs = {}
    i, j, x, y = df.i, df.j, df.x, df.y
    i_pts, j_pts = np.max(i), np.max(j)
    coords = dict(y=np.unique(y), x=np.unique(x))
    coords[new_dim] = new_dim_values
    dims = ('y', 'x', 'layer',)
    for col in df.columns:
        if col in ('i', 'j', 'x', 'y',):
            continue
        if not (drop_cols is None or col not in drop_cols):
            continue
        if not (keep_cols is None or col in keep_cols):
            continue
        arr = df[col].astype(np.float64)
        attrs = dict(meta=col_attrs[col])
        arr = arr.values.reshape(i_pts, j_pts, len(new_dim_values))
        arrs[col] = xr.DataArray(arr, coords=coords, dims=dims, attrs=attrs)
    return arrs


def read_ascii_grid(filenames, y, x, name, dsets=None):
    dsets = dsets or {}
    template = np.empty((y.size, x.size, len(filenames)))
    coords = dict(y=y, x=x, layer=list(range(1, 1 + len(filenames))))
    dims = ('y', 'x', 'layer')
    attrs = dict(filenames=filenames)
    for idx, f in enumerate(filenames):
        template[:, :, idx] = np.loadtxt(f)
    dsets[name] = xr.DataArray(template, coords=coords,
                               dims=dims, attrs=attrs)
    return dsets


def read_one_ascii(f, names=None):
    df = pd.read_csv(f, sep='\s+', names=names, skiprows=0)
    return df


def _get_layer_num(fname):
    ext = os.path.basename(fname).split('.')
    if ext[-1].isdigit():
        return int(ext[-1])
    return int(x[ext].split('_')[-1])


def read_ascii_groups(groups=None):
    dsets = {}
    to_concat_names = set()
    for name in (groups or sorted(COS_HYD_FILES)):
        fs = COS_HYD_FILES[name]
        if name.startswith(('COS_', 'HYD_',)):
            names = SOIL_META['COS_HYD']
        elif name.startswith(('TXDM', 'STEX', 'pcnts')):
            names = SOIL_META['SOIL_LAYERS']
            if name.startswith(('TXDM', 'pcnts')):
                read_ascii_grid(fs, *grid, name=name, dsets=dsets)
                continue
        col_headers = [x[0] for x in names]
        exts = [_get_layer_num(x) for x in fs]
        fs = sorted(fs)
        for idx, f in enumerate(fs, 1):
            df = read_one_ascii(f, col_headers)
            arrs = dataframe_to_rasters(df,
                                        col_attrs=dict(names),
                                        drop_cols=['i', 'j'],
                                        new_dim='layer',
                                        new_dim_values=[idx])
            for column, v in arrs.items():
                column = '{}_{}'.format(name, column)
                dsets[(column, idx)] = v
                to_concat_names.add(column)
                if name.startswith('COS'):
                    grid = v.y, v.x
    for name in to_concat_names:
        ks = [k for k in sorted(dsets) if k[0] == name]
        arr = xr.concat(tuple(dsets[k] for k in ks), dim='layer')
        dsets[name] = arr
        for k in ks:
            dsets.pop(k)
    for v in dsets.values():
        v.values[v.values == NO_DATA] = np.NaN
    return xr.Dataset(dsets)


def download_data(session=None):
    if session is None:
        from nldas_soil_moisture_ml import SESSION as session
    print('Read:', SOIL_URL)
    base_url, basename = os.path.split(SOIL_URL)
    fname = os.path.join(SOIL_DIR, basename.replace('.php', '.html'))
    if not os.path.exists(fname):
        response = session.get(SOIL_URL).content.decode().split()
        paths = [_ for _ in response if '.' in _
                 and 'href' in _.lower() and
                 (any(sf.lower() in _.lower() for sf in SOIL_FILES)
                  or '.bin' in _)]
        paths = [_.split('"')[1] for _ in paths]
        with open(fname, 'w') as f:
            f.write(json.dumps(paths))
    else:
        paths = json.load(open(fname))
    paths2 = []
    for path in paths:
        url = os.path.join(base_url, path)
        fname = os.path.join(SOIL_DIR, path.replace('../nldas', SOIL_DIR))
        paths2.append(fname)
        if not os.path.exists(fname):
            if not os.path.exists(os.path.dirname(fname)):
                os.makedirs(os.path.dirname(fname))
            print('Downloading:', url, 'to:', fname)
            content = session.get(url).content
            with open(fname, 'wb') as f:
                f.write(content)
    return paths2


if __name__ == '__main__':
    download_data()
    X = read_ascii_groups()
