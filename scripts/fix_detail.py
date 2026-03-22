"""Apply all stock_detail.html changes atomically."""
import re

path = r'd:\stone\code\stone0090\zq-trade\web\templates\stock_detail.html'
c = open(path, 'r', encoding='utf-8').read()

# 1. CSS
c = c.replace(
    '  .algo-row { display: flex; align-items: center; gap: 8px; padding: 8px 0; border-bottom: 1px solid #f3f4f6; }\n'
    '  .algo-label { width: 36px; flex-shrink: 0; font-weight: 700; color: #6b7280; font-size: 13px; }\n'
    '  .algo-grade { width: 48px; flex-shrink: 0; text-align: center; }\n'
    '  .algo-detail { font-size: 12px; color: #9ca3af; margin-left: 8px; min-width: 0; }\n'
    '  .label-row { display: flex; align-items: center; gap: 8px; padding: 8px 0; border-bottom: 1px solid #f3f4f6; }\n'
    '  .label-name { width: 36px; font-weight: 700; color: #6b7280; font-size: 13px; }',
    '  .dim-row, .algo-row { display: flex; align-items: center; gap: 8px; padding: 10px 0; border-bottom: 1px solid #f3f4f6; min-height: 44px; box-sizing: border-box; }\n'
    '  .dim-label, .algo-label { width: 36px; flex-shrink: 0; font-weight: 700; color: #6b7280; font-size: 13px; }\n'
    '  .dim-grade, .algo-grade { width: 48px; flex-shrink: 0; text-align: center; }\n'
    '  .algo-detail { font-size: 12px; color: #9ca3af; margin-left: 4px; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }\n'
    '  .algo-ref { font-size: 11px; color: #d1d5db; margin-left: 4px; flex-shrink: 0; }\n'
    '  .algo-ref.diff { color: #f59e0b; font-weight: 600; }\n'
    '  .algo-ref.same { color: #10b981; }'
)
print('1. CSS done')

# 2. end-date-area id
c = c.replace(
    '<div class="flex items-center gap-2">\n      <span class="text-gray-500">\u622a\u6b62\u65e5\u671f',
    '<div id="end-date-area" class="flex items-center gap-2">\n      <span class="text-gray-500">\u622a\u6b62\u65e5\u671f'
)
print('2. end-date-area done')

# 3. label-title id + snapshot hint
c = c.replace(
    '<h3 class="font-bold text-gray-700">\u4eba\u5de5\u6807\u6ce8</h3>',
    '<h3 class="font-bold text-gray-700" id="label-title">\u4eba\u5de5\u6807\u6ce8</h3>'
)
c = c.replace(
    '\u540c\u610f\u7b97\u6cd5</button>\n    </div>\n    <form',
    '\u540c\u610f\u7b97\u6cd5</button>\n    </div>\n    <div id="snapshot-hint" class="text-xs text-amber-600 bg-amber-50 rounded px-3 py-2 mb-3" style="display:none">\u4fdd\u5b58\u6807\u6ce8\u5c06\u521b\u5efa\u4eca\u65e5\u5feb\u7167\uff0c\u65b0\u8bb0\u5f55\u51fa\u73b0\u5728\u6807\u6ce8\u5217\u8868\u4e2d\u3002\u54c1\u79cd\u5e93\u539f\u59cb\u6570\u636e\u4e0d\u53d7\u5f71\u54cd\u3002</div>\n    <form'
)
print('3. label-title + snapshot done')

# 4. Form rows: label-row -> dim-row, label-name -> dim-label, add onchange + algo-ref
c = c.replace('class="label-row"', 'class="dim-row"')
c = c.replace('class="label-name"', 'class="dim-label"')

# Add onchange and algo-ref for each dim select
for d in ['dl', 'pt', 'lk']:
    D = d.upper()
    c = c.replace(
        f'<select name="{d}_grade" class="w-20"><option value="">-</option><option>S</option><option>A</option><option>B</option><option>C</option></select>\n        <input type="text" name="{d}_note"',
        f'<select name="{d}_grade" class="w-20" onchange="updateDiffHighlights()"><option value="">-</option><option>S</option><option>A</option><option>B</option><option>C</option></select>\n        <span class="algo-ref" data-dim="{d}"></span>\n        <input type="text" name="{d}_note"'
    )

c = c.replace(
    '<select name="sf_grade" class="w-20"><option value="">-</option><option>1st</option><option>2nd</option><option>3rd</option></select>\n        <input type="text" name="sf_note"',
    '<select name="sf_grade" class="w-20" onchange="updateDiffHighlights()"><option value="">-</option><option>1st</option><option>2nd</option><option>3rd</option></select>\n        <span class="algo-ref" data-dim="sf"></span>\n        <input type="text" name="sf_note"'
)

for d in ['ty', 'dn']:
    c = c.replace(
        f'<select name="{d}_grade" class="w-20"><option value="">-</option><option>S</option><option>A</option><option>B</option><option>C</option><option>\u5f85\u5b9a</option></select>\n        <input type="text" name="{d}_note"',
        f'<select name="{d}_grade" class="w-20" onchange="updateDiffHighlights()"><option value="">-</option><option>S</option><option>A</option><option>B</option><option>C</option><option>\u5f85\u5b9a</option></select>\n        <span class="algo-ref" data-dim="{d}"></span>\n        <input type="text" name="{d}_note"'
    )
print('4. Form rows done')

# 5. Add updateDiffHighlights function
udh = '''
// \u5bf9\u6bd4\u7b97\u6cd5\u8bc4\u7ea7 vs \u4eba\u5de5\u6807\u6ce8\uff0c\u9ad8\u4eae\u5dee\u5f02
function updateDiffHighlights() {
  if (!stockData) return;
  var dims = ['dl','pt','lk','sf','ty','dn'];
  var form = document.getElementById('label-form');
  for (var i = 0; i < dims.length; i++) {
    var d = dims[i], ag = stockData[d + '_grade'] || '';
    var sel = form.elements[d + '_grade'], hg = sel ? sel.value : '';
    var ref = document.querySelector('.algo-ref[data-dim="' + d + '"]');
    if (!ref) continue;
    if (!ag) { ref.textContent = ''; ref.className = 'algo-ref'; }
    else if (!hg) { ref.textContent = '(' + ag + ')'; ref.className = 'algo-ref'; }
    else if (hg === ag) { ref.textContent = '='; ref.className = 'algo-ref same'; }
    else { ref.textContent = '(' + ag + ')'; ref.className = 'algo-ref diff'; }
  }
}

'''
c = c.replace('\nfunction copyFromAlgo() {', udh + 'function copyFromAlgo() {')
print('5. updateDiffHighlights func done')

# 6. fillLabel calls updateDiffHighlights
c = c.replace(
    "if (el && label[key]) el.value = label[key];\n  }\n}",
    "if (el && label[key]) el.value = label[key];\n  }\n  updateDiffHighlights();\n}"
)
print('6. fillLabel done')

# 7. loadDetail calls updateDiffHighlights
c = c.replace(
    "if (stockData.label) fillLabel(stockData.label);\n    currentStockTags",
    "if (stockData.label) fillLabel(stockData.label);\n    updateDiffHighlights();\n    currentStockTags"
)
print('7. loadDetail done')

# 8. copyFromAlgo calls updateDiffHighlights
c = c.replace(
    "showToast('\u5df2\u586b\u5165\u7b97\u6cd5\u8bc4\u5206');\n}",
    "showToast('\u5df2\u586b\u5165\u7b97\u6cd5\u8bc4\u5206');\n  updateDiffHighlights();\n}"
)
print('8. copyFromAlgo done')

# 9. Universe mode in loadDetail
c = c.replace(
    "document.getElementById('stock-updated-at').textContent = formatTime(stockData.updated_at);\n  } catch(e)",
    "document.getElementById('stock-updated-at').textContent = formatTime(stockData.updated_at);\n\n    // \u54c1\u79cd\u5e93\u6a21\u5f0f UI \u9002\u914d\n    if (detailFrom === 'universe') {\n      document.getElementById('end-date-area').style.display = 'none';\n      document.getElementById('label-title').textContent = '\u4eba\u5de5\u6807\u6ce8\uff08\u5feb\u7167\u6a21\u5f0f\uff09';\n      document.getElementById('snapshot-hint').style.display = 'block';\n      document.getElementById('btn-save').textContent = '\u4fdd\u5b58\u6807\u6ce8\u5feb\u7167';\n    }\n  } catch(e)"
)
print('9. Universe UI done')

# 10. saveLabel universe mode
c = c.replace(
    "const btn = document.getElementById('btn-save');\n  btn.disabled = true; btn.textContent = '\u4fdd\u5b58\u4e2d...';\n  try {\n    await api('/api/stocks/' + STOCK_ID + '/label', {\n      method: 'PUT', body: JSON.stringify(data)\n    });\n    showToast('\u6807\u6ce8\u5df2\u4fdd\u5b58');\n  } catch(e) { showToast(e.message, 'error'); }\n  finally { btn.disabled = false; btn.textContent = '\u4fdd\u5b58\u6807\u6ce8'; }",
    "const btn = document.getElementById('btn-save');\n  const isUniverse = detailFrom === 'universe';\n  btn.disabled = true; btn.textContent = '\u4fdd\u5b58\u4e2d...';\n  try {\n    const url = '/api/stocks/' + STOCK_ID + '/label' + (isUniverse ? '?from_page=universe' : '');\n    const result = await api(url, {\n      method: 'PUT', body: JSON.stringify(data)\n    });\n    if (isUniverse && result.new_stock_id) {\n      showToast('\u6807\u6ce8\u5feb\u7167\u5df2\u4fdd\u5b58\uff0c\u53ef\u5728\u6807\u6ce8\u5217\u8868\u4e2d\u67e5\u770b');\n    } else {\n      showToast('\u6807\u6ce8\u5df2\u4fdd\u5b58');\n    }\n  } catch(e) { showToast(e.message, 'error'); }\n  finally {\n    btn.disabled = false;\n    btn.textContent = isUniverse ? '\u4fdd\u5b58\u6807\u6ce8\u5feb\u7167' : '\u4fdd\u5b58\u6807\u6ce8';\n  }"
)
print('10. saveLabel done')

# 11. Back link + sticky header
c = c.replace(
    "loadAllTags().then(() => loadDetail());",
    "(function() {\n  var backLink = document.getElementById('back-link');\n  if (detailFrom === 'universe') {\n    backLink.href = '/universe';\n    backLink.innerHTML = '&larr; \u8fd4\u56de\u54c1\u79cd\u5e93';\n  }\n})();\n\nloadAllTags().then(() => loadDetail());"
)

c = c.replace(
    '</script>\n{% endblock %}',
    '\n// \u7f6e\u9876\u5934\u90e8\u9634\u5f71\u6548\u679c\nconst stickyHeader = document.getElementById(\'sticky-header\');\nif (stickyHeader) {\n  const observer = new IntersectionObserver(\n    ([e]) => stickyHeader.classList.toggle(\'stuck\', e.intersectionRatio < 1),\n    { threshold: [1], rootMargin: \'-1px 0px 0px 0px\' }\n  );\n  observer.observe(stickyHeader);\n}\n</script>\n{% endblock %}'
)
print('11. Back link + sticky done')

with open(path, 'w', encoding='utf-8') as f:
    f.write(c)
print('\nAll done!')
