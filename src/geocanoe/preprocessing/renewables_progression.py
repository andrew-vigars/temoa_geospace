"""Consistent, self-labelled Silver renewable evidence gallery (no conversion)."""
import argparse
import html
import json
from pathlib import Path

import geopandas as gpd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
from matplotlib.patches import Patch
import pandas as pd


def plot_wind_mix_pies(frame, fields, colors, destination):
    """Equal-radius pies in projected metres; no aggregation of model regions."""
    import numpy as np
    from matplotlib.collections import PatchCollection
    from matplotlib.patches import Wedge
    from scipy.spatial import cKDTree

    frame = frame.to_crs(3347)
    points = frame.geometry.representative_point()
    xy = np.column_stack([points.x, points.y])
    shares = frame[fields].to_numpy(dtype=float)
    if not np.isfinite(shares).all() or (shares < 0).any():
        raise ValueError('Wind pie shares must be finite and nonnegative.')
    totals = shares.sum(axis=1)
    if not np.allclose(totals[totals > 0], 1, atol=1e-6):
        raise ValueError('Represented wind shares must sum to one.')
    mixed = (shares > 1e-8).sum(axis=1) > 1
    # Choose a dense mixed-resource neighbourhood for a legible companion view.
    candidates = xy[mixed] if mixed.any() else xy
    tree = cKDTree(candidates)
    counts = tree.query_ball_point(candidates, 200000, return_length=True)
    center = candidates[counts.argmax()]
    bounds = frame.total_bounds
    names = []
    for detail in (False, True):
        fig, ax = plt.subplots(figsize=(14, 11))
        fig.subplots_adjust(left=.025, right=.975, top=.94, bottom=.13)
        try:
            if detail:
                west, south = center - 225000
                east, north = center + 225000
                selected = frame.cx[west:east, south:north]
            else:
                west, south, east, north = bounds
                selected = frame
            selected.plot(ax=ax, facecolor='#f0f1f2', edgecolor='#b5bcc1', linewidth=.25)
            patches, faces = [], []
            visible = (xy[:, 0] >= west) & (xy[:, 0] <= east) & (xy[:, 1] >= south) & (xy[:, 1] <= north)
            for point, values, total in zip(xy[visible], shares[visible], totals[visible]):
                if total == 0:
                    continue
                angle = 90.
                for value, color in zip(values, colors):
                    if value > 0:
                        end = angle + 360 * value / total
                        patches.append(Wedge(point, 9000, angle, end))
                        faces.append(color)
                        angle = end
            ax.add_collection(PatchCollection(patches, facecolors=faces, edgecolors='white', linewidth=.12))
            ax.set(xlim=(west, east), ylim=(south, north), aspect='equal')
            ax.set_axis_off()
            ax.set_title('T1–T4 wind area shares · ' + ('regional detail' if detail else 'all model regions'),
                         loc='left', fontsize=18, fontweight='bold')
            fig.legend(handles=[Patch(color=c, label=f[:2]) for c, f in zip(colors, fields)] +
                      [Patch(facecolor='#f0f1f2', edgecolor='#b5bcc1', label='No pie: no represented wind area')],
                      loc='lower center', bbox_to_anchor=(.5, .04), ncol=5,
                      fontsize=10, title='Fraction of represented wind area', framealpha=.95)
            fig.text(.02, .015, 'EPSG:3347 · One equal-size pie per production region · '
                     'Slice area = T1–T4 fraction; pie size does not represent MW or eligible area.', fontsize=10)
            name = '03_wind_mix_pies' + ('_detail' if detail else '')
            fig.savefig(Path(destination) / f'{name}.png', dpi=220, facecolor='white')
            fig.savefig(Path(destination) / f'{name}.svg', facecolor='white')
            names.append(f'{name}.png')
        finally:
            plt.close(fig)
    return names


def build_progression(output, destination):
    """Render recorded evidence, preserving the run's actual spatial mixtures."""
    output, destination = Path(output), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    record = json.loads((output / 'manifest.json').read_text())
    regions = gpd.read_file(output / 'annual_capacity_factors.gpkg').to_crs(3347)
    extent = regions.total_bounds
    cards = []

    def render(frame, column, name, title, label, cmap='viridis', maximum=1):
        fig, ax = plt.subplots(figsize=(10, 8), layout='constrained')
        try:
            frame.plot(ax=ax, column=column, cmap=cmap, vmin=0, vmax=maximum,
                       linewidth=0, missing_kwds={'color': '#dddddd'})
            ax.set(xlim=(extent[0], extent[2]), ylim=(extent[1], extent[3]))
            ax.set_axis_off()
            ax.set_title(title, loc='left', fontsize=15, fontweight='bold')
            # Inset colour bar stays inside the exported map, including in isolation.
            bar = ax.inset_axes([.08, .08, .36, .022])
            fig.colorbar(plt.cm.ScalarMappable(norm=plt.Normalize(0, maximum), cmap=cmap),
                         cax=bar, orientation='horizontal', label=label)
            fig.text(.04, .02, f"ERA5 {record['weather_year']} | EPSG:3347 | {output.name}",
                     fontsize=8, color='#555555')
            fig.savefig(destination / name, dpi=150, facecolor='white')
        finally:
            plt.close(fig)
        cards.append((title, name, label))

    era = destination / 'era5'
    for path in sorted(era.glob('*_era5.png')):
        cards.append((f'01 · Raw ERA5 · {path.stem[:7]}', f'era5/{path.name}',
                      'Before Atlite: wind speed and surface radiation; no land masks.'))

    land = output / 'land_exclusion/eligibility.gpkg'
    if land.exists():
        eligibility = gpd.read_file(land).to_crs(3347)
        for resource, cmap in [('wind', 'Greens'), ('solar', 'YlOrBr')]:
            render(eligibility, f'{resource}_eligible_fraction', f'02_{resource}_land.png',
                   f'02 · {resource.title()} land eligibility', 'Eligible area fraction (0–1)', cmap)

    mix_path = output / 'wind_resource/regional_mix.csv'
    if mix_path.exists():
        mix = pd.read_csv(mix_path)
        fields = [f'{t}_fraction' for t in ('T1', 'T2', 'T3', 'T4') if f'{t}_fraction' in mix]
        frame = regions[['region', 'geometry']].merge(mix, on='region', validate='one_to_one')
        colors = ['#4477AA', '#228833', '#CCBB44', '#AA3377']
        for name in plot_wind_mix_pies(frame, fields, colors, destination):
            cards.append(('03 · T1–T4 regional area shares', name,
                          'Equal-size pies show the actual mixture per region. SVG companion available for zooming.'))
        for field, color in zip(fields, colors):
            from matplotlib.colors import LinearSegmentedColormap
            render(frame, field, f'03_{field}.png', f'03 · Wind {field[:2]} spatial share',
                   'Fraction of represented wind area (0–1)',
                   LinearSegmentedColormap.from_list(field, ['#fafafa', color]))
        # Dominant type is a diagnostic; actual CFs retain all spatial shares.
        frame['dominant'] = frame[fields].to_numpy().argmax(axis=1).astype(float)
        frame.loc[frame[fields].sum(axis=1) == 0, 'dominant'] = float('nan')
        fig, ax = plt.subplots(figsize=(10, 8), layout='constrained')
        try:
            frame.plot(ax=ax, column='dominant', cmap=ListedColormap(colors[:len(fields)]),
                       vmin=-.5, vmax=len(fields)-.5, linewidth=0,
                       missing_kwds={'color': '#dddddd'})
            ax.set_axis_off()
            ax.set_title('03 · Combined T1–T4 wind representation', loc='left', fontweight='bold')
            ax.legend(handles=[Patch(color=c, label=f[:2]) for c, f in zip(colors, fields)] +
                      [Patch(color='#dddddd', label='No represented wind area')],
                      loc='lower left', bbox_to_anchor=(0, .06), fontsize=9,
                      title='Largest area share (ties: first type)', title_fontsize=9)
            fig.text(.04, .02, 'Dominant type only; final wind CF retains the spatial T1–T4 mixture.', fontsize=9)
            fig.savefig(destination / '03_combined_wind_types.png', dpi=150, facecolor='white')
        finally:
            plt.close(fig)
        cards.append(('03 · Combined T1–T4', '03_combined_wind_types.png',
                      'Dominant turbine type; individual share maps show mixed regions.'))

    for resource, cmap in [('wind', 'Blues'), ('solar', 'YlOrBr')]:
        render(regions, f'{resource}_cf', f'04_{resource}_cf.png',
               f'04 · Final annual {resource} capacity factor', 'Capacity factor (0–1)', cmap)
    intro = ('Raw ERA5 → eligible land → Atlite turbine / tracking-PV conversion → '
             'area-weighted regional capacity factors. Wind uses the recorded turbine shares. '
             'Land eligibility is an area fraction, not a CF or an inferred MW limit.')
    body = ''.join(f'<article><h2>{html.escape(title)}</h2><a href="{name.replace(".png", ".svg") if "pies" in name else name}"><img src="{name}" '
                   f'alt="{html.escape(title)}"></a><p>{html.escape(caption)}</p></article>'
                   for title, name, caption in cards)
    if not list(era.glob('*_era5.png')):
        body = '<p>Raw ERA5 previews have not been generated for this run. Use --raw-era5 first.</p>' + body
    (destination / 'index.html').write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Silver renewable evidence</title>'
        '<style>body{font:16px system-ui;margin:32px;background:#f3f5f6;color:#182b36}'
        'main{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:24px}'
        'article{background:white;padding:20px;border-radius:12px}img{width:100%}h2{font-size:20px}</style>'
        f'<h1>Silver · Renewable evidence progression</h1><p>{intro}</p>'
        f'<p>Run: {html.escape(str(output))} · Weather year: {record["weather_year"]}</p><main>{body}</main></html>',
        encoding='utf-8')
    return destination / 'index.html'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print(build_progression(args.output, args.destination))
