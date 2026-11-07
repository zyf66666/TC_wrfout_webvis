import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const ctx = vm.createContext({});
const path = new URL('../app/static/variable_memory.js', import.meta.url);
assert.ok((await import('node:fs')).existsSync(path), 'variable memory feature is missing');
vm.runInContext(readFileSync(path, 'utf8'), ctx);
const { remember, merge } = ctx.VariableMemory;
const saved = remember([], [{name:'heating',plot_type:'contourf',style:'RdBu_r',enabled:true,time:'30',height:'1000',scale_factor:'3600'}]);
assert.equal(saved[0].time, undefined);
assert.equal(saved[0].height, undefined);
assert.equal(saved[0].enabled, false);
assert.equal(saved[0].scale_factor, '3600');
const both = remember(saved, [{name:'heating',plot_type:'contour',style:'k'}]);
assert.equal(both.length, 2);
const restored = merge([{name:'heating',plot_type:'contourf',enabled:true,style:'Blues',time:'6'}],both);
assert.equal(restored.length,2);
assert.equal(restored[0].style,'Blues');
assert.equal(restored[0].time,'6');
assert.equal(restored[1].enabled,false);
assert.equal(remember(both,[{name:'heating',plot_type:'contour',style:'r'}])[1].style,'r');
assert.equal(remember([], [{name:'  '}]).length,0);
console.log('variable memory merge and settings checks passed');

// Exercise actual app orchestration without loading the rest of the dashboard.
const app = readFileSync(new URL('../app/static/app.js', import.meta.url), 'utf8');
const state = vm.createContext({
  VariableMemory:ctx.VariableMemory, activeVariableFigType:'0', variableMemory:{},
  variableMemoryStorageKey:'test', pendingVariableMemory:new Map(), variableMemoryTimer:null,
  figTypeSelect:{value:'0'}, variableRowsEl:{rows:[]}, quiverRowsEl:{rows:[]},
  defaultPlotVariables:[{name:'dbz',enabled:true}], defaultQuiverVariables:[],
  serializeRows:container=>container.rows, saveRows:()=>{},
  setTimeout:()=>1, clearTimeout:()=>{},
  variableRequestToRow:row=>({...row}), quiverRequestToRow:row=>({...row}), researchAnalysisDefaults:{},
});
vm.runInContext(`
  Object.defineProperty(variableRowsEl, 'innerHTML', {set(){this.rows=[]}});
  Object.defineProperty(quiverRowsEl, 'innerHTML', {set(){this.rows=[]}});
  function addVariableRow(row){variableRowsEl.rows.push(row)}
  function addQuiverRow(row){quiverRowsEl.rows.push(row)}
`,state);
vm.runInContext(app.slice(app.indexOf('function saveVariablePreferences()'),app.indexOf('function serializeRows')),state);
vm.runInContext(app.slice(app.indexOf('function populateVariableRowsFromRequest'),app.indexOf('function variableRequestToRow')),state);
vm.runInContext(`
  variableRowsEl.rows=[{name:'dbz',plot_type:'contourf',enabled:true,time:'5'}];
  saveVariablePreferences();
  figTypeSelect.value='2'; switchVariableFigType();
  variableRowsEl.rows=[{name:'heating',plot_type:'contourf',enabled:true,scale_factor:'3600'}];
  saveVariablePreferences();
  figTypeSelect.value='0'; switchVariableFigType();
`,state);
assert.equal(state.variableRowsEl.rows[0].name,'dbz');
assert.equal(state.variableRowsEl.rows[0].enabled,false);
assert.equal(state.variableRowsEl.rows[0].time,undefined);
vm.runInContext(`
  figTypeSelect.value='2';
  populateVariableRowsFromRequest({variables:[{name:'vr',enabled:true,height:'1000'}],quiver:[]});
`,state);
assert.equal(state.variableRowsEl.rows[0].name,'vr');
assert.equal(state.variableRowsEl.rows[0].height,'1000');
assert.equal(state.variableRowsEl.rows[1].name,'heating');
assert.equal(state.variableRowsEl.rows[1].enabled,false);
console.log('plot switching and old-image restoration checks passed');
