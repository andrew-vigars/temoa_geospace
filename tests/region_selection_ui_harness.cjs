// Exercise generated selection JavaScript without a browser or CDN access.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ui = new Map(), events = {}, layers = [], saved = [];
function field(name) {
  if (!ui.has(name)) ui.set(name, {value:'', checked:false, style:{},
    setAttribute(name,value){this[name]=value;},
    replaceChildren(...values) { this.options=values; this.value=values[0]?.value||''; }});
  return ui.get(name);
}
const root = {style:{}, querySelector(selector) {return field(selector.match(/"([^"]+)"/)[1]);}};
const testMap = {
  createPane() { return {style:{}}; },
  on(event, handler) {events[event]=handler;},
  removeLayer() {}, fitBounds() {},
  dragging:{enable(){testMap.draggable=true;},disable(){testMap.draggable=false;}},
  doubleClickZoom:{enable(){},disable(){}},
};
const bounds = (a,b) => ({contains(p) {
  return p.lat>=Math.min(a.lat,b.lat)&&p.lat<=Math.max(a.lat,b.lat)
    &&p.lng>=Math.min(a.lng,b.lng)&&p.lng<=Math.max(a.lng,b.lng);
}});
const L = {
  svg(){return {};},
  geoJSON(data, options) {
    for(const feature of data.features) {
      const layer={feature,_path:{style:{}},setStyle(style){this.style=style;},
        bindTooltip(){},on(event,handler){this[event]=handler;},getBounds(){return {};}};
      layers.push(layer); options.onEachFeature(feature,layer);
    }
    return {addTo(){}};
  },
  control() {return {addTo(){this.onAdd();},getContainer(){return root;}};},
  DomUtil:{create(){return root;}},
  DomEvent:{disableClickPropagation(){},disableScrollPropagation(){},stopPropagation(){}},
  latLng(lat,lng){return {lat,lng};},latLngBounds:bounds,
  rectangle(){return {addTo(){return this;}};},
  polygon(){return {addTo(){return this;}};},
};
const context={L,testMap,Set,Map,Option:function(text,value){this.text=text;this.value=value;},
  setTimeout,console,
  async fetch(url, options) {
    if(url==='/export-options') return {ok:true,async json(){return {tables:['OutputCost']};}};
    if(url==='/exports') {
      const request=JSON.parse(options.body);
      assert.equal(request.filename,'subset.xlsx');
      assert.equal(request.selection.name,'subset');
      assert.deepEqual(request.selection.regions,['R1','R2']);
      assert.deepEqual(request.tables,['OutputCost']);
      return {ok:true,async json(){return {path:'RUN/region_selections/test/excel_outputs/subset.xlsx'};}};
    }
    if(options.method==='POST') {
      saved.push(JSON.parse(options.body));
      return {ok:true,async json(){return {path:'RUN/region_selections/test.json'};}};
    }
    return {ok:true,async json(){return saved;}};
  },
};
let source=fs.readFileSync(process.argv[2],'utf8');
source=source.replace(/const map = \w+, config =/, 'const map = testMap, config =');
source=source.replaceAll('__REGION_SELECTION_TOKEN__','real-token');
vm.runInNewContext(source,context);
(async()=>{
  assert.equal(layers.length,2);
  assert.equal(layers[0]._path.style.pointerEvents,'none');
  assert.equal(field('body').hidden,true);
  field('toggle').onclick();
  field('active').checked=true; field('active').onchange();
  layers[0].click({}); assert.match(field('count').textContent,/1 regions/);
  field('name').value='retained';
  field('toggle').onclick();
  assert.equal(field('body').hidden,true);
  assert.equal(layers[0]._path.style.pointerEvents,'none');
  assert.equal(field('heading').textContent,'Region selection (1)');
  field('toggle').onclick();
  assert.equal(field('body').hidden,false);
  assert.equal(field('name').value,'retained');
  assert.equal(layers[0]._path.style.pointerEvents,'auto');
  layers[0].click({}); assert.match(field('count').textContent,/0 regions/);
  field('search').value='R2'; field('find').onclick();
  assert.match(field('count').textContent,/1 regions/);
  field('clear').onclick(); field('action').value='add'; field('rectangle').onclick();
  assert.equal(testMap.draggable,false);
  events.mousedown({latlng:{lat:0,lng:0}});
  events.mouseup({latlng:{lat:0.0009,lng:0.0009}});
  assert.equal(testMap.draggable,true);
  assert.equal(field('regions').options[0].value,'R1');
  field('polygon').onclick();
  for(const [lat,lng] of [[0,0.001],[0.001,0.001],[0.001,0.002],[0,0.002]]) events.click({latlng:{lat,lng}});
  field('finish').onclick(); assert.match(field('count').textContent,/2 regions/);
  field('name').value='test'; await field('save').onclick();
  assert.deepEqual(saved[0].regions,['R1','R2']);
  assert.match(field('status').textContent,/Saved:/);
  field('tables').selectedOptions=[{value:'OutputCost'}];
  field('transport').value='touching'; field('workbook').value='subset.xlsx';
  field('name').value='';
  await field('export').onclick();
  assert.match(field('status').textContent,/Exported workbook:/);
  assert.equal(field('export').disabled,false);
  field('clear').onclick(); field('saved').value='0'; field('saved').onchange();
  assert.match(field('count').textContent,/2 regions/);
  field('action').value='remove'; field('rectangle').onclick();
  events.mousedown({latlng:{lat:0,lng:0}});
  events.mouseup({latlng:{lat:0.0009,lng:0.0009}});
  assert.equal(field('regions').options[0].value,'R2');
  field('polygon').onclick(); field('cancel').onclick(); assert.equal(testMap.draggable,true);
  console.log('Selection UI interactions passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
