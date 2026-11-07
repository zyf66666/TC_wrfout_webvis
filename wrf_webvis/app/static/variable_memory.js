/* 图型常用变量：只保存样式，绘图选择由当前请求负责。 */
(function (global) {
  const transient = new Set(['time', 'height', 'angle', 'radius']);
  function key(row) { return `${String(row.name || '').trim()}\u0000${row.plot_type || 'contourf'}`; }
  function remember(previous, rows) {
    const result = (previous || []).map(row => ({ ...row }));
    for (const row of rows || []) {
      if (!String(row.name || '').trim()) continue;
      const clean = Object.fromEntries(Object.entries(row).filter(([field]) => !transient.has(field)));
      clean.name = clean.name.trim();
      clean.enabled = false;
      const index = result.findIndex(item => key(item) === key(clean));
      if (index < 0) result.push(clean); else result[index] = clean;
    }
    return result;
  }
  function merge(current, remembered) {
    const result = current.map(row => ({ ...row }));
    const keys = new Set(result.map(key));
    for (const row of remember([], remembered)) {
      if (!keys.has(key(row))) { result.push(row); keys.add(key(row)); }
    }
    return result;
  }
  global.VariableMemory = { remember, merge };
})(globalThis);
