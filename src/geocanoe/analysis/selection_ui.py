"""Leaflet controls for exact model-region selection in generated maps."""
from __future__ import annotations

import json

from branca.element import MacroElement, Template


def add_region_selection(model_map, sites, database, *, fingerprint=None):
    """Use model-node polygons rather than visual basemap features as hit targets."""
    polygons = sites[["geometry"]].copy()
    polygons["region"] = sites.index.astype(str)
    if polygons["region"].duplicated().any():
        raise ValueError("Model region IDs must be unique for map selection.")
    if not polygons.geometry.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        raise ValueError("Region selection requires model-node polygons.")
    centre_geometry = polygons.to_crs(3347) if polygons.crs.is_geographic else polygons
    centres = centre_geometry.geometry.centroid.to_crs(4326)
    polygons["centre_lon"] = centres.x
    polygons["centre_lat"] = centres.y
    data = json.loads(polygons.to_crs(4326).to_json())
    # Escape HTML delimiters because this data is embedded in a script element.
    config = json.dumps({
        "geojson": data, "database": str(database.resolve()),
        "fingerprint": fingerprint,
    }).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    element = MacroElement()
    element._template = Template(_TEMPLATE.replace("__CONFIG__", config))
    model_map.add_child(element)


_TEMPLATE = r"""
{% macro script(this, kwargs) %}
(function() {
const map = {{this._parent.get_name()}}, config = __CONFIG__;
const selected = new Set(), byId = new Map();
let active = false, drawing = null, vertices = [], preview = null, anchor = null;
const pane = map.createPane('regionSelection');
pane.style.zIndex = 625; pane.style.pointerEvents = 'none';
const baseStyle = {color:'#586477', weight:0.7, fillColor:'#ffffff', fillOpacity:0.01};
const pickedStyle = {color:'#ff6500', weight:3, fillColor:'#ff9b35', fillOpacity:0.35};
const overlay = L.geoJSON(config.geojson, {
  pane:'regionSelection', renderer:L.svg({pane:'regionSelection'}), style:baseStyle,
  onEachFeature(feature, layer) {
    const id = feature.properties.region; byId.set(id, layer);
    layer.bindTooltip(id);
    layer.on('click', e => {
      if (!active || drawing) return;
      L.DomEvent.stopPropagation(e);
      selected.has(id) ? selected.delete(id) : selected.add(id); refresh();
    });
  }
}).addTo(map);
const control = L.control({position:'topleft'});
control.onAdd = function() {
  const div = L.DomUtil.create('div', 'region-selection');
  div.style.cssText = 'background:white;padding:12px;border:1px solid #aaa;border-radius:5px;width:285px;font:13px Arial;color:#222;box-shadow:0 1px 6px #888';
  div.innerHTML = `<div style="display:flex;align-items:center;justify-content:space-between;gap:12px">
    <b data-role="heading">Region selection</b>
    <button data-role="toggle" aria-expanded="true" aria-label="Minimize region selection">Minimize</button></div>
    <div data-role="body" style="max-height:60vh;overflow:auto;margin-top:6px">
    <div><label><input type="checkbox" data-role="active"> Select regions (click to toggle)</label></div>
    <div><input data-role="search" placeholder="Exact region ID" aria-label="Region ID" style="width:170px"> <button data-role="find">Find</button></div>
    <div><button data-role="rectangle">Rectangle</button> <button data-role="polygon">Polygon</button> <button data-role="finish">Finish</button> <button data-role="cancel">Cancel</button></div>
    <div><label>Area action <select data-role="action"><option value="add">Add</option><option value="remove">Remove</option></select></label></div>
    <small>Area tools use model polygon centres. Click individual cells to refine.</small>
    <div data-role="count"></div><select data-role="regions" size="4" aria-label="Selected regions" style="width:100%"></select>
    <div><button data-role="remove">Remove selected ID</button> <button data-role="clear">Clear all</button></div>
    <div><input data-role="name" placeholder="Selection name" aria-label="Selection name" style="width:170px"> <button data-role="save">Save</button></div>
    <div><select data-role="saved" aria-label="Saved selections" style="width:190px"></select> <button data-role="reload">Reload</button></div>
    <div><label>Load JSON <input type="file" data-role="file" accept=".json" style="width:200px"></label></div>
    <hr><b>Export selected regions to Excel</b>
    <div data-role="source" style="overflow-wrap:anywhere"></div>
    <div><label>Output tables <select multiple size="5" data-role="tables" aria-label="Output tables" style="width:100%"></select></label></div>
    <small>All tables selected initially. Ctrl-click to change. Tables without regional data are skipped.</small>
    <div><label>Connections <select data-role="transport"><option value="touching">Touching selection</option><option value="within">Within selection</option><option value="boundary">Crossing boundary</option></select></label></div>
    <div><label>Workbook <input data-role="workbook" value="output_tables_subset.xlsx" aria-label="Workbook filename" style="width:190px"></label></div>
    <div><button data-role="export">Export workbook + SQL</button></div>
    <small>Uses the current selected regions. Saved in this run's region_selections folder.</small>
    <div data-role="status" role="status" style="margin-top:6px;overflow-wrap:anywhere"></div></div>`;
  L.DomEvent.disableClickPropagation(div); L.DomEvent.disableScrollPropagation(div);
  return div;
};
control.addTo(map);
const root = control.getContainer(), ui = role => root.querySelector('[data-role="'+role+'"]');
const status = text => { ui('status').textContent = text; };
const token = '__REGION_SELECTION_TOKEN__';
const connected = token !== '__' + 'REGION_SELECTION_TOKEN__';
const headers = {'Content-Type':'application/json', 'X-Selection-Token':token};
ui('source').textContent = 'Solved database: '+config.database;
ui('export').disabled = true;
let saved = [];
let collapsed = false, resumeSelection = false;
ui('toggle').onclick = () => {
  collapsed = !collapsed;
  if(collapsed) { resumeSelection=active; cancel(); setActive(false); }
  else { setActive(resumeSelection); }
  ui('body').hidden = collapsed;
  root.style.width = collapsed ? 'auto' : '285px';
  ui('toggle').textContent = collapsed ? 'Open' : 'Minimize';
  ui('toggle').setAttribute('aria-expanded',String(!collapsed));
  ui('toggle').setAttribute('aria-label',collapsed ? 'Open region selection' : 'Minimize region selection');
};
function refresh() {
  ui('heading').textContent = 'Region selection ('+selected.size+')';
  byId.forEach((layer, id) => layer.setStyle(selected.has(id) ? pickedStyle : baseStyle));
  ui('count').textContent = selected.size + ' regions selected';
  ui('regions').replaceChildren(...Array.from(selected).sort().map(id => new Option(id,id)));
}
function setActive(value) {
  active = value; ui('active').checked = value;
  pane.style.pointerEvents = value ? 'auto' : 'none';
  byId.forEach(layer => { if(layer._path) layer._path.style.pointerEvents = value ? 'auto' : 'none'; });
}
function cancel() {
  drawing = null; vertices = []; anchor = null;
  if (preview) { map.removeLayer(preview); preview = null; }
  map.dragging.enable(); map.doubleClickZoom.enable();
}
function begin(mode) {
  cancel(); setActive(true); drawing = mode;
  map.dragging.disable(); map.doubleClickZoom.disable();
  status(mode === 'rectangle' ? 'Drag a rectangle over region centres.' : 'Click polygon corners, then Finish.');
}
function inside(point, ring) {
  let hit = false;
  for (let i=0,j=ring.length-1;i<ring.length;j=i++) {
    const a=ring[i], b=ring[j], x=point.lng, y=point.lat;
    if (((a.lat>y)!==(b.lat>y)) && x<(b.lng-a.lng)*(y-a.lat)/(b.lat-a.lat)+a.lng) hit=!hit;
  }
  return hit;
}
function applyArea(test) {
  const remove = ui('action').value === 'remove';
  byId.forEach((layer,id) => {
    const p=layer.feature.properties;
    if(test(L.latLng(p.centre_lat,p.centre_lon))) remove ? selected.delete(id) : selected.add(id);
  });
  cancel(); refresh(); status('Area selection applied.');
}
map.on('mousedown', e => { if(drawing==='rectangle') anchor=e.latlng; });
map.on('mousemove', e => {
  if(drawing!=='rectangle'||!anchor) return;
  if(preview) map.removeLayer(preview);
  preview=L.rectangle(L.latLngBounds(anchor,e.latlng),{interactive:false,color:'#ff6500'}).addTo(map);
});
map.on('mouseup', e => {
  if(drawing!=='rectangle'||!anchor) return;
  const bounds=L.latLngBounds(anchor,e.latlng); applyArea(p=>bounds.contains(p));
});
map.on('click', e => {
  if(drawing!=='polygon') return;
  vertices.push(e.latlng); if(preview) map.removeLayer(preview);
  preview=L.polygon(vertices,{interactive:false,color:'#ff6500'}).addTo(map);
});
ui('active').onchange = () => { cancel(); setActive(ui('active').checked); };
ui('rectangle').onclick = () => begin('rectangle');
ui('polygon').onclick = () => begin('polygon');
ui('finish').onclick = () => {
  if(drawing!=='polygon'||vertices.length<3) return status('Choose at least three polygon corners.');
  const ring=vertices.slice(); applyArea(p=>inside(p,ring));
};
ui('cancel').onclick = cancel;
ui('find').onclick = () => {
  const id=ui('search').value.trim(), layer=byId.get(id);
  if(!layer) return status('Unknown model region ID.');
  map.fitBounds(layer.getBounds()); setActive(true); selected.add(id); refresh();
};
ui('remove').onclick = () => { selected.delete(ui('regions').value); refresh(); };
ui('clear').onclick = () => { selected.clear(); refresh(); };
function load(value) {
  if(value.version!==1 || value.database!==config.database) throw Error('Selection belongs to a different run/database.');
  if(value.fingerprint !== config.fingerprint) throw Error('Selection schema fingerprint differs.');
  if(!Array.isArray(value.regions)||value.regions.some(id=>!byId.has(id))) throw Error('Selection contains unknown model regions.');
  selected.clear(); value.regions.forEach(id=>selected.add(id));
  ui('name').value=value.name; refresh(); status('Selection loaded.');
}
async function reload() {
  if(!connected) return;
  try {
    const response=await fetch('/selections',{headers});
    if(!response.ok) throw Error('Could not list saved selections.');
    saved=await response.json();
    ui('saved').replaceChildren(new Option('Load saved selection…',''),...saved.map((v,i)=>new Option(v.name,String(i))));
  } catch(error) { status(error.message); }
}
ui('saved').onchange = () => { if(ui('saved').value!=='') { try {load(saved[Number(ui('saved').value)]);} catch(e){status(e.message);} } };
ui('reload').onclick = reload;
ui('file').onchange = async () => {
  try { load(JSON.parse(await ui('file').files[0].text())); } catch(e) { status(e.message); }
};
function currentSelection(defaultName='') {
  const name=ui('name').value.trim()||defaultName;
  if(!/^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/.test(name)) throw Error('Use 1–80 letters, numbers, underscores or hyphens for the selection name.');
  if(!selected.size) throw Error('Select at least one region.');
  return {version:1,name,database:config.database,fingerprint:config.fingerprint,
    created_at:new Date().toISOString(),area_rule:'polygon_centres',regions:Array.from(selected).sort()};
}
ui('save').onclick = async () => {
  let value;
  try { value=currentSelection(); } catch(e) { return status(e.message); }
  const name=value.name;
  if(connected) {
    try {
      const response=await fetch('/selections',{method:'POST',headers,body:JSON.stringify(value)});
      const result=await response.json(); if(!response.ok) throw Error(result.error||'Save failed.');
      await reload(); status('Saved: '+result.path);
    } catch(e) { status(e.message); }
  } else {
    const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));
    const link=document.createElement('a'); link.href=url; link.download=name+'.json'; link.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
    status('Downloaded JSON. To save directly into this run, open the map with: python -m geocanoe.analysis.selections --db "'+config.database+'"');
  }
};
async function exportOptions() {
  if(!connected) return;
  try {
    const response=await fetch('/export-options',{headers});
    if(!response.ok) throw Error('Could not load export options. Restart the map helper.');
    const options=await response.json();
    ui('tables').replaceChildren(...options.tables.map(name=>{
      const option=new Option(name,name); option.selected=true; return option;
    }));
    ui('export').disabled=false;
  } catch(e) { status(e.message); }
}
ui('export').onclick = async () => {
  try {
    const selection=currentSelection(ui('workbook').value.trim().replace(/\.xlsx$/,''));
    const tables=Array.from(ui('tables').selectedOptions,option=>option.value);
    if(!tables.length) throw Error('Choose at least one output table.');
    ui('export').disabled=true; status('Exporting selected regions…');
    const response=await fetch('/exports',{method:'POST',headers,body:JSON.stringify({
      selection,tables,transport:ui('transport').value,filename:ui('workbook').value.trim()
    })});
    const result=await response.json();
    if(!response.ok) throw Error(result.error||'Export failed.');
    status('Exported workbook: '+result.path+(result.sql_path ? ' | SQL: '+result.sql_path+' | SQLite: '+result.sqlite_path : ''));
  } catch(e) { status(e.message); }
  finally { ui('export').disabled=!connected; }
};
exportOptions();
setActive(false); refresh(); reload();
ui('toggle').onclick();
if(!connected) status('Direct folder saving: open this map using python -m geocanoe.analysis.selections --db "'+config.database+'". Save here downloads JSON.');
})();
{% endmacro %}
"""
