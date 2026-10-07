const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');

const root = path.resolve(__dirname, '..');
const platforms = [
  ['PWA', '/index.html'],
  ['Android assets', '/android/app/src/main/assets/index.html'],
];
const cases = [
  { label: 'zero', input: '0', stored: 0, qty: '1', cost: '29,000', sales: '58,000', balance: '잔액 71,000' },
  { label: 'empty', input: '', stored: '', qty: '1', cost: '29,000', sales: '58,000', balance: '잔액 71,000' },
  { label: 'negative', input: '-3', stored: -3, qty: '1', cost: '29,000', sales: '58,000', balance: '잔액 71,000' },
  { label: 'one', input: '1', stored: 1, qty: '1', cost: '29,000', sales: '58,000', balance: '잔액 71,000' },
  { label: 'multiple', input: '4', stored: 4, qty: '4', cost: '74,000', sales: '148,000', balance: '잔액 26,000' },
  { label: 'two digits / over budget', input: '12', stored: 12, qty: '12', cost: '194,000', sales: '388,000', balance: '초과 94,000' },
];
function fixture(qty = 3) {
  const products = [
    { id: 'first', name: '가디건', status: 'buy', qty, krwCost: '15,000', sellPrice: '30,000',
      image: 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7',
      color: '차콜', size: 'FREE', source: '동대문', shop: '기존 가게', reason: '기존 특징', custom: { preserve: true } },
    { id: 'second', name: '팬츠', status: 'buy', qty: 2, krwCost: '7,000', sellPrice: '14,000' },
    { id: 'held', name: '보류 상품', status: 'hold', qty: 50, krwCost: '99,000', sellPrice: '198,000' },
    { id: 'excluded', name: '제외 상품', status: 'no', qty: 50, krwCost: '99,000', sellPrice: '198,000' },
  ];
  return {
    foxProducts: JSON.stringify(products), foxBudget: '100000',
    'foxMemo:first': '기존 고객 메모 그대로', 'foxMemo:second': '두 번째 메모',
    foxPick: '{"week0":"hold"}', foxVerifiedSeed20260925: '1', unrelated: 'preserve',
  };
}

async function openApp(t, url, initial) {
  const html = readFileSync(path.join(root, url), 'utf8');
  const source = readFileSync(path.join(root, path.dirname(url), 'app.js'), 'utf8');
  const errors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => errors.push(error.message));
  // Load the actual platform HTML and complete app script. External resources
  // (photos, styles, generated Android version asset) are not needed for quantity behavior.
  const dom = new JSDOM(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, ''), {
    url: 'https://fox.test' + url, runScripts: 'dangerously', virtualConsole,
  });
  t.after(() => { dom.window.close(); assert.deepEqual(errors, [], 'no app runtime errors'); });
  const win = dom.window;
  Object.entries(initial).forEach(([key, value]) => win.localStorage.setItem(key, value));
  const script = win.document.createElement('script');
  script.textContent = source;
  win.document.body.appendChild(script);
  await openMine(win);
  return win;
}

async function openMine(win) {
  win.document.querySelector('nav button[data-tab="mine"]').click();
  // Existing navigation schedules several renders; wait for the final V9 summary.
  const deadline = Date.now() + 2000;
  while (!win.document.querySelector('.summary')) {
    if (Date.now() > deadline) throw new Error('My sourcing view did not render');
    await new Promise(resolve => setTimeout(resolve, 5));
  }
}

function storage(win) {
  return Object.fromEntries(Object.entries(win.localStorage));
}

function change(win, selector, value) {
  const input = win.document.querySelector(selector);
  input.value = value;
  input.dispatchEvent(new win.Event('change', { bubbles: true }));
}

function assertView(win, c) {
  const doc = win.document;
  const quantities = doc.querySelectorAll('.qty input');
  assert.equal(quantities[0].value, c.qty, 'visible quantity');
  assert.equal(quantities[1].value, '2', 'other product quantity');
  assert.equal(doc.querySelector('#budgetSummary b').textContent, `예상 사입 합계 ${c.cost}원`);
  assert.equal(doc.querySelector('#budgetSummary span').textContent, `예산 100,000원 · ${c.balance}원`);
  assert.equal(doc.querySelector('.summary p').textContent, `예상 원가 ${c.cost}원 · 예상 판매 ${c.sales}원`);
  assert.deepEqual(Array.from(doc.querySelectorAll('.summary span'), el => el.textContent), ['사입 2', '보류 1', '제외 1']);
  assert.equal(doc.querySelector('#budgetInput').value, '100000');
  assert.equal(doc.querySelector('.product textarea').value, '기존 고객 메모 그대로');
}

for (const [platform, url] of platforms) {
  for (const c of cases) {
    // Catches divergent persisted quantity, stale summary, and stale input after change.
    test(`${platform}: edit ${c.label}, totals, persistence and reload agree`, async t => {
      const initial = fixture();
      const page = await openApp(t, url, initial);
      assert.deepEqual(await storage(page), initial, 'opening the app must not rewrite existing data');
      change(page, '.qty input', c.input); // Run the real inline change handler.
      await assertView(page, c);
      const expected = { ...initial };
      const products = JSON.parse(initial.foxProducts);
      products[0].qty = Number(c.qty);
      expected.foxProducts = JSON.stringify(products);
      assert.deepEqual(await storage(page), expected, 'only the edited product quantity changes');
      const reloaded = await openApp(t, url, storage(page));
      assertView(reloaded, c);
      assert.deepEqual(storage(reloaded), expected, 'reload must preserve all data');
    });
  }

  const legacy = [
    ...cases,
    { ...cases[0], label: 'string zero', stored: '0' },
    { ...cases[0], label: 'missing quantity', stored: undefined },
    { ...cases[0], label: 'null quantity', stored: null },
    { ...cases[0], label: 'whitespace', stored: '   ' },
  ];
  for (const c of legacy) {
    // Catches bad legacy reads while prohibiting an eager storage migration.
    test(`${platform}: legacy ${c.label} renders consistently without rewriting storage`, async t => {
      const initial = fixture(c.stored);
      if (c.stored === undefined) {
        const products = JSON.parse(initial.foxProducts);
        delete products[0].qty;
        initial.foxProducts = JSON.stringify(products);
      }
      const page = await openApp(t, url, initial);
      await assertView(page, c);
      assert.deepEqual(await storage(page), initial);
      const reloaded = await openApp(t, url, storage(page));
      assertView(reloaded, c);
      assert.deepEqual(storage(reloaded), initial);
    });
  }
}

for (const c of cases) {
  test(`Android add product: ${c.label} uses the same minimum quantity`, async t => {
    const initial = fixture();
    const page = await openApp(t, platforms[1][1], initial);
    page.alert = () => {}; // jsdom has no native alert UI.
    const doc = page.document;
    doc.querySelector('#newName').value = '새 테스트 상품';
    doc.querySelector('#newQty').value = c.input;
    doc.querySelector('#saveAdd').click();
    const saved = await storage(page);
    const products = JSON.parse(saved.foxProducts);
    assert.equal(products[0].qty, Number(c.qty));
    assert.equal(products[0].status, 'hold');
    assert.deepEqual(products.slice(1), JSON.parse(initial.foxProducts));
    delete saved.foxProducts;
    const { foxProducts, ...otherKeys } = initial;
    assert.deepEqual(saved, otherKeys);
  });
}
