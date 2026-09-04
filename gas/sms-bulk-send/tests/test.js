const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', 'Code.gs'), 'utf8');

// ---- 最小限の GAS モック ----
class Sheet {
  constructor(name, data) { this.name = name; this.data = data.map(r => r.slice()); }
  _ensure(r, c) {
    while (this.data.length < r) this.data.push([]);
    for (const row of this.data) while (row.length < c) row.push('');
  }
  getLastRow() { let n = 0; this.data.forEach((r, i) => { if (r.some(v => String(v ?? '') !== '')) n = i + 1; }); return n; }
  getLastColumn() { let n = 0; this.data.forEach(r => r.forEach((v, i) => { if (String(v ?? '') !== '') n = Math.max(n, i + 1); })); return n; }
  getDataRange() { return this.getRange(1, 1, Math.max(1, this.getLastRow()), Math.max(1, this.getLastColumn())); }
  getRange(r, c, nr = 1, nc = 1) {
    const sheet = this;
    return {
      getValues() { sheet._ensure(r + nr - 1, c + nc - 1); return sheet.data.slice(r - 1, r - 1 + nr).map(row => row.slice(c - 1, c - 1 + nc)); },
      getDisplayValues() { return this.getValues().map(row => row.map(v => v instanceof Date ? v.toISOString() : String(v ?? ''))); },
      setValues(vals) { sheet._ensure(r + nr - 1, c + nc - 1); vals.forEach((row, i) => row.forEach((v, j) => { sheet.data[r - 1 + i][c - 1 + j] = v; })); return this; },
      setValue(v) { return this.setValues([[v]]); },
      setFontWeight() { return this; }, setBackground() { return this; }, setWrap() { return this; },
      insertCheckboxes() { return this; }, setNumberFormat() { return this; },
    };
  }
  appendRow(row) { this.data.push(row.slice()); }
  setFrozenRows() {} setColumnWidth() {}
}
class SS {
  constructor(sheets) { this.sheets = sheets; }
  getSheetByName(n) { return this.sheets[n] || null; }
  insertSheet(n) { this.sheets[n] = new Sheet(n, []); return this.sheets[n]; }
}
let CURRENT_SS;
let SENT = [];
global.SpreadsheetApp = { getActiveSpreadsheet: () => CURRENT_SS, getUi: () => { throw new Error('no ui'); }, flush: () => {} };
global.Logger = { log: (...a) => {} };
global.Utilities = { sleep: () => {} };
global.LockService = { getDocumentLock: () => ({ tryLock: () => true, releaseLock: () => {} }) };
global.UrlFetchApp = {
  fetch: (url, opt) => { SENT.push(opt.payload); return { getContentText: () => JSON.stringify({ responseCode: opt.payload.phoneNumber === '+819099999999' ? 1 : 0 }) }; }
};

eval(src);

// ---- テストデータ ----
function makeSS(listData) {
  return new SS({
    '送信リスト': new Sheet('送信リスト', listData),
    'SMSテンプレート': new Sheet('SMSテンプレート', [
      ['テンプレート名', 'メモ', '本文'],
      ['セミナー案内', '', '{{名前}}様\nセミナーのご案内です。担当:{{担当}}'],
      ['リマインド', '', '{{姓}}様 明日よろしくお願いします。'],
    ]),
    'システム設定': new Sheet('システム設定', [
      ['項目', '値', '説明'],
      ['トークン', 'TOKEN123', ''],
      ['クライアントID', 'CL1', ''],
      ['SMSコード', 'SMS1', ''],
      ['既定テンプレート', 'セミナー案内', ''],
    ]),
  });
}

let pass = 0, fail = 0;
function eq(actual, expected, label) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a === e) { pass++; console.log('  ok   ' + label); }
  else { fail++; console.log('  FAIL ' + label + '\n       actual  =' + a + '\n       expected=' + e); }
}

console.log('--- normalizePhoneNumber_ ---');
eq(normalizePhoneNumber_('090-1234-5678'), '+819012345678', 'ハイフンあり携帯');
eq(normalizePhoneNumber_('０９０１２３４５６７８'), '+819012345678', '全角');
eq(normalizePhoneNumber_('9012345678'), '+819012345678', '先頭0落ち');
eq(normalizePhoneNumber_('+819012345678'), '+819012345678', '+81形式');
eq(normalizePhoneNumber_('819012345678'), '+819012345678', '81始まり');
eq(normalizePhoneNumber_('0312345678'), '+81312345678', '固定電話');
eq(normalizePhoneNumber_('123'), '', '短すぎ');
eq(normalizePhoneNumber_(''), '', '空');
eq(normalizePhoneNumber_('あいうえお'), '', '文字列');

console.log('--- renderBody_ ---');
eq(renderBody_('{{名前}}様、{{ 姓 }}さん、{{未定義}}!', { '名前': '山田 太郎', '姓': '山田' }), '山田 太郎様、山田さん、!', '差し込みと未定義');
eq(renderBody_('a'.repeat(700), {}).length, 660, '660文字で切る');

console.log('--- 標準ケース：一括送信 ---');
CURRENT_SS = makeSS([
  ['送信対象', 'お名前(姓)', 'お名前(名)', '電話番号', 'テンプレート', '担当'],
  [true, '山田', '太郎', '090-1234-5678', '', '鈴木'],
  [true, '佐藤', '花子', '08011112222', 'リマインド', '田中'],
  [false, '無視', '太郎', '09033334444', '', ''],
  [true, '番号なし', '次郎', '', '', ''],
  [true, '番号不正', '三郎', '123', '', ''],
  [true, 'テンプレ無し', '四郎', '09055556666', '存在しない', ''],
  ['', '', '', '', '', ''],
]);
SENT = [];
sendBulkSms_();
eq(SENT.length, 2, '送信件数');
eq(SENT[0].phoneNumber, '+819012345678', '1件目の宛先');
eq(SENT[0].message, '山田太郎様\nセミナーのご案内です。担当:鈴木', '1件目の本文（既定テンプレ＋任意列差し込み）');
eq(SENT[1].phoneNumber, '+818011112222', '2件目の宛先');
eq(SENT[1].message, '佐藤様 明日よろしくお願いします。', '2件目の本文（行テンプレ指定）');
eq(SENT[0].token, 'TOKEN123', 'トークン');

const list = CURRENT_SS.getSheetByName('送信リスト');
const hdr = list.data[0];
const cS = hdr.indexOf('送信ステータス');
eq(list.data[1][cS], '送信成功', '2行目 成功');
eq(list.data[2][cS], '送信成功', '3行目 成功');
eq(list.data[3][cS], '', '4行目 チェック外し＝未処理');
eq(list.data[4][cS], '送信失敗（電話番号が空）', '5行目');
eq(list.data[5][cS], '送信失敗（番号不正）', '6行目');
eq(list.data[6][cS], '送信失敗（テンプレート「存在しない」が無い）', '7行目');
eq(CURRENT_SS.getSheetByName('SMS送信ログ').getLastRow(), 7, 'ログ行数(見出し+3エラー+2送信+SYSTEM)');

console.log('--- 再実行：成功行はスキップ ---');
SENT = [];
sendBulkSms_();
eq(SENT.length, 0, '成功済みは再送されない（残りはエラー行のみ）');

console.log('--- ヘッダーゆれ：お名前1列 / 電話 / 送信対象列なし ---');
CURRENT_SS = makeSS([
  ['お名前', '電話', 'テンプレート'],
  ['山田 太郎', '090-1111-2222', ''],
  ['佐藤 花子', '090-3333-4444', 'リマインド'],
]);
SENT = [];
sendBulkSms_();
eq(SENT.length, 2, '送信対象列が無ければ全行が対象');
eq(SENT[0].message, '山田 太郎様\nセミナーのご案内です。担当:', 'お名前1列で {{名前}} が入る');
eq(SENT[1].message, '山田 太郎様 明日よろしくお願いします。'.replace('山田 太郎', '佐藤 花子'), '{{姓}} は氏名フル値で代替');

console.log('--- 全角ヘッダー：お名前（姓）／お名前（名）／ＴＥＬ ---');
CURRENT_SS = makeSS([
  ['お名前（姓）', 'お名前（名）', 'ＴＥＬ'],
  ['鈴木', '一郎', '09088887777'],
]);
SENT = [];
sendBulkSms_();
eq(SENT.length, 1, '全角括弧ヘッダーを認識');
eq(SENT[0].message, '鈴木一郎様\nセミナーのご案内です。担当:', '姓＋名を連結');

console.log('--- 旧仕様の設定シート（B4/C4/D4）＋見出し無しテンプレ ---');
CURRENT_SS = new SS({
  '送信リスト': new Sheet('送信リスト', [['お名前', '電話番号'], ['田中 三郎', '09012341234']]),
  'SMSテンプレート': new Sheet('SMSテンプレート', [
    ['ラベル', '説明', '本文'],
    ['旧テンプレ', '', '{{ご担当者名}}さんへ'],
  ]),
  'システム設定': new Sheet('システム設定', [
    ['設定', '', '', ''],
    ['', '', '', ''],
    ['アカウント', 'token', 'clientId', 'smsCode'],
    ['本番', 'OLDTOKEN', 'OLDCL', 'OLDCODE'],
  ]),
});
SENT = [];
sendBulkSms_();
eq(SENT.length, 1, '旧レイアウトの設定を読める');
eq([SENT[0].token, SENT[0].clientId, SENT[0].smsCode], ['OLDTOKEN', 'OLDCL', 'OLDCODE'], 'B4/C4/D4 フォールバック');
eq(SENT[0].message, '田中 三郎さんへ', '1件しかないテンプレを自動採用');

console.log('--- 設定不足なら1件も送らない ---');
CURRENT_SS = makeSS([['お名前', '電話番号'], ['山田 太郎', '09012345678']]);
CURRENT_SS.getSheetByName('システム設定').data[1][1] = '';
SENT = [];
sendBulkSms_();
eq(SENT.length, 0, 'トークン未設定なら送信しない');
eq(CURRENT_SS.getSheetByName('送信リスト').data[1].filter(v => String(v).indexOf('送信') === 0).length, 0, 'ステータスも書かない');

console.log('--- APIエラー時 ---');
CURRENT_SS = makeSS([['お名前', '電話番号'], ['エラー 太郎', '09099999999']]);
SENT = [];
sendBulkSms_();
const l2 = CURRENT_SS.getSheetByName('送信リスト');
eq(l2.data[1][l2.data[0].indexOf('送信ステータス')], '送信失敗（APIエラー）', 'responseCode!=0 は失敗扱い');
SENT = [];
sendBulkSms_();
eq(SENT.length, 1, '失敗行は再実行で再送される');

console.log('--- 電話番号列が無ければエラー ---');
CURRENT_SS = makeSS([['お名前'], ['山田 太郎']]);
let threw = '';
try { sendBulkSms_(); } catch (e) { threw = e.message; }
eq(threw.indexOf('電話番号の列が見つかりません') > -1, true, '明示的なエラーメッセージ');

console.log('');
console.log(`pass=${pass} fail=${fail}`);
process.exit(fail ? 1 : 0);
